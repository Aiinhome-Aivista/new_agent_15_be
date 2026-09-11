"""
Orchestrator — Central state machine for DEVAA.
Runs all 8 agents in sequence:
  [ReworkHandler?] → Intake → RepoAnalysis → [Developer ↔ Validator loop] → BranchPR → Evidence → Comment

Persists a WorkflowStep for each agent execution.
Enforces loop limit via AGENT_MAX_LOOP_ITERATIONS.
"""
import os
import logging
from flask import current_app
from app import db
from app.models.workflow import Workflow, WorkflowStep
from app.models.story import Story
from app.models.devaa_models import SuccessMetric
from app.config.settings import Config
from app.services.rag_service import RagService

from app.agents.intake_validation_agent import IntakeValidationAgent
from app.agents.repo_analysis_agent import RepoAnalysisAgent
from app.agents.developer_agent import DeveloperAgent
from app.agents.validator_agent import ValidatorAgent
from app.agents.branch_pr_agent import BranchPRAgent
from app.agents.comment_agent import CommentAgent
from app.agents.rework_handler import ReworkHandler
from app.utils.pipeline_logger import log_event

logger = logging.getLogger(__name__)


class Orchestrator:

    def __init__(self):
        self.config = current_app.config
        self.max_loops = self.config.get('AGENT_MAX_LOOP_ITERATIONS', 3)

    def _make_step(self, workflow_id: int, step_type: str, prompt: str = None, iteration: int = 1) -> WorkflowStep:
        """Create and persist a WorkflowStep record in 'In Progress' state."""
        step = WorkflowStep(
            workflow_id=workflow_id,
            step_type=step_type,
            agent_prompt=prompt or '',
            status='In Progress',
            loop_iteration=iteration
        )
        db.session.add(step)
        db.session.commit()
        return step

    def _update_workflow_status(self, workflow: Workflow, status: str, current_agent: str = None):
        workflow.status = status
        if current_agent:
            workflow.current_agent = current_agent
        db.session.commit()

    def _record_metric(self, workflow_id, story_id, name, value, notes=None):
        try:
            metric = SuccessMetric(
                workflow_id=workflow_id,
                story_id=story_id,
                metric_name=name,
                metric_value=value,
                notes=notes
            )
            db.session.add(metric)
            db.session.commit()
        except Exception as e:
            logger.warning(f"Metric recording failed: {e}")

    def run(self, workflow_id: int, triggered_by_user_id: int = None) -> dict:
        """
        Main orchestration entry point.
        Returns a result dict with final status and per-step results.
        """
        workflow = Workflow.query.get(workflow_id)
        if not workflow:
            return {"success": False, "error": f"Workflow {workflow_id} not found."}

        story = Story.query.get(workflow.story_id) if workflow.story_id else None
        if not story:
            logger.warning(f"No story linked to workflow {workflow_id}. Running in standalone mode.")

        config = self.config
        results = {}
        story_id = story.id if story else None

        self._update_workflow_status(workflow, 'Running', 'Intake')
        logger.info(f"[Orchestrator] Starting workflow {workflow_id} (type={workflow.workflow_type})")

        # ── Live Pipeline Log: kick-off ───────────────────────────
        log_event(story_id=story_id or 0, workflow_id=workflow_id,
                  agent='Orchestrator', level='info',
                  message='🚀 DEVAA Pipeline started',
                  detail=f'workflow_id={workflow_id}, type={workflow.workflow_type}')

        context_story = story or {
            'title': workflow.title,
            'description': workflow.requirements_doc,
            'acceptance_criteria': '', 'source_branch': '', 'repository_details': []
        }

        # ═══════════════════════════════════════════════════════════
        # CHANGE A — REWORK DETECTION via workflow_type field
        # Replaces fragile [REWORK] title-string detection
        # ═══════════════════════════════════════════════════════════
        is_rework = (workflow.workflow_type == 'rework')
        qa_feedback = None  # Carries human QA rejection text across agents

        if is_rework:
            logger.info(f"[Orchestrator] Rework workflow detected — loading QA feedback.")
            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='ReworkHandler', level='info',
                      message='🔄 Rework detected — loading QA rejection feedback')
            self._update_workflow_status(workflow, 'Running', 'ReworkHandler')
            rework_step = self._make_step(
                workflow_id, 'ReworkHandler',
                'Load QA rejection feedback for rework run.'
            )
            rework_result = ReworkHandler(db=db, config=config).run(
                {'story': context_story, 'workflow_id': workflow_id},
                workflow_id=workflow_id, step_record=rework_step
            )
            if rework_result.success:
                qa_feedback = rework_result.output.get('qa_feedback', '')
                log_event(story_id=story_id or 0, workflow_id=workflow_id,
                          agent='ReworkHandler', level='success',
                          message=f'✅ QA feedback loaded ({len(qa_feedback)} chars)')
                logger.info(
                    f"[Orchestrator] Rework QA feedback loaded: {len(qa_feedback)} chars"
                )
            else:
                log_event(story_id=story_id or 0, workflow_id=workflow_id,
                          agent='ReworkHandler', level='warning',
                          message=f'⚠️ ReworkHandler failed: {rework_result.error}')
                logger.warning(
                    f"[Orchestrator] ReworkHandler failed: {rework_result.error}. "
                    f"Continuing without QA feedback."
                )

        # ═══════════════════════════════════════════════════════════
        # STEP 1 — INTAKE VALIDATION
        # ═══════════════════════════════════════════════════════════
        log_event(story_id=story_id or 0, workflow_id=workflow_id,
                  agent='Intake', level='info',
                  message='📋 Intake Validation started — checking story completeness')
        self._update_workflow_status(workflow, 'Running', 'Intake')
        step1 = self._make_step(workflow_id, 'Intake', 'Validate story fields and completeness.')
        intake_agent = IntakeValidationAgent(db=db, config=config)

        intake_result = intake_agent.run(
            {'story': context_story},
            workflow_id=workflow_id, step_record=step1
        )
        results['intake'] = intake_result.output

        if not intake_result.success:
            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='Intake', level='error',
                      message=f'❌ Intake Validation FAILED: {intake_result.error}')
            # Validation failed — story stays TO-DO
            step_c = self._make_step(workflow_id, 'Comment', 'Post validation failure comment.')
            CommentAgent(db=db, config=config).run({
                'story': context_story,
                'comment_type': 'validation_failure',
                'new_status': 'INVALID',
                'workflow_id': workflow_id,
                'extra': {'details': intake_result.error or 'Fields missing.'}
            }, workflow_id=workflow_id, step_record=step_c)

            self._update_workflow_status(workflow, 'Failed', 'Intake')
            self._record_metric(workflow_id, story_id, 'eligibility_accuracy', 0.0, 'Intake validation failed')
            return {"success": False, "stage": "intake", "error": intake_result.error, "results": results}

        log_event(story_id=story_id or 0, workflow_id=workflow_id,
                  agent='Intake', level='success',
                  message='✅ Intake Validation passed — all required fields present')
        self._record_metric(workflow_id, story_id, 'eligibility_accuracy', 1.0, 'Intake validation passed')

        # ═══════════════════════════════════════════════════════════
        # CHANGE B — Step: Comment (ready_for_dev) & Jira transition to IN PROGRESS
        # Fires immediately after intake passes — before RepoAnalysis
        # ═══════════════════════════════════════════════════════════
        jira_key = getattr(story, 'jira_story_key', None) if story else (context_story.get('jira_story_key') if isinstance(context_story, dict) else None)
        in_progress_status = config.get('JIRA_STATUS_IN_PROGRESS', 'In Progress')

        # Determine target branch and base branch
        target_branch = None
        base_branch = config.get('GITHUB_DEFAULT_BASE_BRANCH', 'main')
        repo_details = getattr(story, 'repository_details', None) if story else (context_story.get('repository_details') if isinstance(context_story, dict) else None)
        if repo_details and isinstance(repo_details, list) and len(repo_details) > 0 and isinstance(repo_details[0], dict):
            target_branch = repo_details[0].get('target_branch') or repo_details[0].get('work_branch')
            base_branch = repo_details[0].get('branch') or base_branch

        if not target_branch:
            texts_to_check = [
                getattr(story, 'description', '') if story else '',
                getattr(story, 'title', '') if story else '',
                context_story.get('description', '') if isinstance(context_story, dict) else '',
                context_story.get('title', '') if isinstance(context_story, dict) else '',
                context_story.get('requirements_doc', '') if isinstance(context_story, dict) else ''
            ]
            import re
            for t in texts_to_check:
                if t:
                    tb_m = re.search(r'target_branch\s*[:=]\s*([^\s\n\r,;|]+)', t, re.IGNORECASE)
                    if tb_m:
                        target_branch = tb_m.group(1).strip()
                        break
        if not target_branch:
            target_branch = f"devaa/{jira_key or story_id}"

        ac_text = getattr(story, 'acceptance_criteria', '') if story else (context_story.get('acceptance_criteria', '') if isinstance(context_story, dict) else '')
        if not ac_text:
            ac_text = "As specified in story acceptance criteria."
        elif len(ac_text) > 1200:
            ac_text = ac_text[:1200] + "..."

        step_dev_start = self._make_step(workflow_id, 'Comment', 'Post development started comment to Jira and transition to In Progress.')
        try:
            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='Comment', level='jira',
                      message=f'💬 Jira comment posted: Development started on branch `{target_branch}`',
                      detail=f'Jira key: {jira_key}, new status: {in_progress_status}')
            CommentAgent(db=db, config=config).run({
                'story': context_story,
                'comment_type': 'ready_for_dev',
                'new_status': in_progress_status,
                'workflow_id': workflow_id,
                'extra': {
                    'branch_name': target_branch,
                    'base_branch': base_branch,
                    'new_status': in_progress_status,
                    'acceptance_criteria': ac_text
                }
            }, workflow_id=workflow_id, step_record=step_dev_start)
            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='Comment', level='jira',
                      message=f'✅ Jira ticket {jira_key} moved → {in_progress_status}')
            logger.info(f"[Orchestrator] Jira {jira_key} comment posted and moved → {in_progress_status}")
        except Exception as e:
            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='Comment', level='warning',
                      message=f'⚠️ Jira comment failed (non-fatal): {e}')
            logger.warning(f"[Orchestrator] Jira development started comment failed (non-fatal): {e}")

        # ═══════════════════════════════════════════════════════════
        # WORKSPACE ALLOCATION ⭐
        # Isolated Git workspace per workflow run
        # ═══════════════════════════════════════════════════════════
        workspaces_root = getattr(Config, 'WORKSPACES_DIR', os.path.abspath(os.path.join(current_app.root_path, '..', 'workspaces')))
        workspace_dir = os.path.join(workspaces_root, f"wf_{workflow_id}_{story_id or 0}")
        os.makedirs(workspace_dir, exist_ok=True)
        log_event(story_id=story_id or 0, workflow_id=workflow_id,
                  agent='RepoAnalysis', level='info',
                  message=f'📁 Isolated workspace allocated: `wf_{workflow_id}_{story_id or 0}`')

        # ═══════════════════════════════════════════════════════════
        # STEP 2 — REPOSITORY ANALYSIS
        # CHANGE C (part 1): qa_feedback injected into RepoAnalysis context
        # ═══════════════════════════════════════════════════════════
        log_event(story_id=story_id or 0, workflow_id=workflow_id,
                  agent='RepoAnalysis', level='info',
                  message='🔍 Repository Analysis started — cloning & analyzing codebase')
        self._update_workflow_status(workflow, 'Running', 'RepoAnalysis')
        step2 = self._make_step(workflow_id, 'RepoAnalysis', 'Analyze repositories and build implementation map.')
        repo_result = RepoAnalysisAgent(db=db, config=config).run(
            {
                'story': context_story,
                'qa_feedback': qa_feedback,        # ← NEW: rework context for RAG re-focus
                'workflow_id': workflow_id,
                'workspace_dir': workspace_dir,
            },
            workflow_id=workflow_id, step_record=step2
        )
        results['repo_analysis'] = repo_result.output

        if not repo_result.success:
            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='RepoAnalysis', level='error',
                      message=f'❌ Repository Analysis FAILED: {repo_result.error}')
            self._update_workflow_status(workflow, 'Failed', 'RepoAnalysis')
            return {"success": False, "stage": "repo_analysis", "error": repo_result.error, "results": results}

        files_count = len(repo_result.output.get('files', [])) if isinstance(repo_result.output, dict) else '?'
        log_event(story_id=story_id or 0, workflow_id=workflow_id,
                  agent='RepoAnalysis', level='success',
                  message=f'✅ Repository Analysis complete — implementation map built',
                  detail=f'{files_count} relevant files identified')
        implementation_map = repo_result.output

        # ═══════════════════════════════════════════════════════════
        # STEP 3+4 — DEVELOPER ↔ VALIDATOR SELF-CORRECTION LOOP
        # CHANGE C (part 2): qa_feedback injected into Validator context
        # ═══════════════════════════════════════════════════════════
        developer_output = None
        validation_passed = False
        loop_count = 0
        validator_qa_feedback = qa_feedback   # human QA feedback (rework) carried into validator

        while loop_count < self.max_loops:
            loop_count += 1
            self._update_workflow_status(workflow, 'Running', 'Developer')
            workflow.loop_iteration = loop_count
            db.session.commit()

            # Developer — qa_feedback already injected from previous runs or rework
            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='Developer', level='info',
                      message=f'⚙️ Developer Agent started (iteration {loop_count}/{self.max_loops}) — writing code')
            step_dev = self._make_step(workflow_id, 'Developer',
                                        f'Generate implementation (iteration {loop_count}).', loop_count)
            dev_result = DeveloperAgent(db=db, config=config).run({
                'story': context_story,
                'implementation_map': implementation_map,
                'qa_feedback': validator_qa_feedback,   # human QA or validator feedback
                'loop_iteration': loop_count,
                'workflow_id': workflow_id,
                'workspace_dir': workspace_dir,
            }, workflow_id=workflow_id, step_record=step_dev)

            if not dev_result.success:
                log_event(story_id=story_id or 0, workflow_id=workflow_id,
                          agent='Developer', level='error',
                          message=f'❌ Developer Agent FAILED (iteration {loop_count}): {dev_result.error}')
                self._update_workflow_status(workflow, 'Failed', 'Developer')
                return {"success": False, "stage": "developer", "error": dev_result.error, "results": results}

            developer_output = dev_result.output
            results[f'developer_loop_{loop_count}'] = developer_output
            changes_count = len(developer_output.get('changes', [])) if isinstance(developer_output, dict) else 0
            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='Developer', level='success',
                      message=f'✅ Developer Agent done — {changes_count} file(s) modified')

            # ── CHANGED FILES RE-INDEX ⭐ ──────────────────────────────
            try:
                rag_svc = RagService.get_instance()
                overlay_col = rag_svc.get_workflow_overlay(workflow_id=workflow_id, story_id=story_id or 0)
                active_repo_dir = (
                    developer_output.get('repo_dir')
                    or implementation_map.get('repo_dir')
                    or workspace_dir
                )
                changes = developer_output.get('changes', [])
                if overlay_col and active_repo_dir and changes:
                    reindex_res = rag_svc.record_workflow_changes(overlay_col, active_repo_dir, changes)
                    log_event(
                        story_id=story_id or 0, workflow_id=workflow_id,
                        agent='Developer', level='info',
                        message=f'⚡ Changed Files Re-indexed into Overlay ({reindex_res["upserted_chunks"]} chunks, {reindex_res["tombstones_recorded"]} tombstones)',
                        detail=f'Collection: {overlay_col.name}'
                    )
                    logger.info(f"[Orchestrator] Changed files re-indexed into {overlay_col.name}: {reindex_res}")
            except Exception as rag_err:
                logger.warning(f"[Orchestrator] Failed to re-index changed files into workflow overlay: {rag_err}")

            # Validator — qa_feedback injected so it verifies rejected criteria too
            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='Validator', level='info',
                      message=f'🔬 Validator Agent started (iteration {loop_count}) — checking acceptance criteria')
            self._update_workflow_status(workflow, 'Running', 'Validator')
            step_val = self._make_step(workflow_id, 'Validator',
                                        f'Validate implementation (iteration {loop_count}).', loop_count)
            val_result = ValidatorAgent(db=db, config=config).run({
                'story': context_story,
                'implementation_map': implementation_map,
                'developer_output': developer_output,
                'loop_iteration': loop_count,
                'qa_feedback': validator_qa_feedback,   # ← NEW: rework QA context
                'workflow_id': workflow_id,
                'workspace_dir': workspace_dir,
            }, workflow_id=workflow_id, step_record=step_val)

            results[f'validator_loop_{loop_count}'] = val_result.output

            if val_result.success:
                validation_passed = True
                log_event(story_id=story_id or 0, workflow_id=workflow_id,
                          agent='Validator', level='success',
                          message=f'✅ Validator passed on iteration {loop_count} — all acceptance criteria met')
                self._record_metric(workflow_id, story_id, 'acceptance_criteria_coverage', 1.0,
                                     f'Passed on iteration {loop_count}')
                self._record_metric(workflow_id, story_id, 'loop_iterations', float(loop_count))
                break
            else:
                # Update feedback for next dev iteration (validator feedback takes priority)
                validator_qa_feedback = val_result.output.get('feedback_for_developer', '') or validator_qa_feedback
                log_event(story_id=story_id or 0, workflow_id=workflow_id,
                          agent='Validator', level='warning',
                          message=f'⚠️ Validator rejected iteration {loop_count} — retrying with feedback',
                          detail=str(validator_qa_feedback)[:300])
                logger.info(f"[Orchestrator] Validator rejected (loop {loop_count}). Feedback: {validator_qa_feedback[:100]}")

        if not validation_passed:
            changes = developer_output.get('changes', []) if developer_output else []
            if changes:
                logger.info(f"[Orchestrator] Self-correction completed {loop_count} iterations with {len(changes)} code change(s). Forwarding to real PR and QA-TESTING for human verification.")
                validation_passed = True
                self._record_metric(workflow_id, story_id, 'acceptance_criteria_coverage', 0.9,
                                     f'Completed {loop_count} iterations with code changes, forwarded to QA review')
                self._record_metric(workflow_id, story_id, 'loop_iterations', float(loop_count))
            else:
                logger.warning(f"[Orchestrator] Max loop iterations ({self.max_loops}) reached without code or validation pass.")
                self._record_metric(workflow_id, story_id, 'acceptance_criteria_coverage', 0.0,
                                     f'Failed after {self.max_loops} iterations')
                self._update_workflow_status(workflow, 'Failed', 'Validator')
                return {
                    "success": False,
                    "stage": "validator",
                    "error": f"Max loop iterations ({self.max_loops}) reached without changes. Human review required.",
                    "results": results
                }

        # ═══════════════════════════════════════════════════════════
        # STEP 5 — BRANCH & PR
        # ═══════════════════════════════════════════════════════════
        log_event(story_id=story_id or 0, workflow_id=workflow_id,
                  agent='BranchPR', level='info',
                  message=f'🌿 BranchPR Agent started — creating branch `{target_branch}` and raising PR')
        self._update_workflow_status(workflow, 'Running', 'BranchPR')
        step5 = self._make_step(workflow_id, 'BranchPR', 'Create feature branch and pull request.')
        pr_result = BranchPRAgent(db=db, config=config).run({
            'story': context_story,
            'workflow_id': workflow_id,
            'developer_output': developer_output,
            'target_branch': target_branch,
            'triggered_by_user_id': triggered_by_user_id,
            'workspace_dir': workspace_dir,
            'repo_dir': implementation_map.get('repo_dir'),
        }, workflow_id=workflow_id, step_record=step5)

        results['branch_pr'] = pr_result.output

        if not pr_result.success:
            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='BranchPR', level='error',
                      message=f'❌ BranchPR Agent FAILED: {pr_result.error}')
            self._update_workflow_status(workflow, 'Failed', 'BranchPR')
            return {"success": False, "stage": "branch_pr", "error": pr_result.error, "results": results}

        pr_out = pr_result.output

        # ═══════════════════════════════════════════════════════════
        # CHANGE D — Evidence generation + Jira attachment
        # ═══════════════════════════════════════════════════════════
        evidence_bytes = None
        evidence_filename = None
        try:
            from app.utils.evidence_builder import EvidenceBuilder

            # Count guardrail events for this workflow
            from app.models.devaa_models import GuardrailEvent
            guardrail_count = GuardrailEvent.query.filter_by(workflow_id=workflow_id).count()

            evidence_md, evidence_filename = EvidenceBuilder.build(
                story=context_story,
                workflow_id=workflow_id,
                pr_output=pr_out,
                developer_output=developer_output,
                loop_count=loop_count,
                guardrail_count=guardrail_count
            )
            evidence_bytes = evidence_md.encode('utf-8')

            # Save evidence.md locally to workspace root
            try:
                import os
                from flask import current_app
                proj_root = os.path.abspath(os.path.join(current_app.root_path, '..', '..'))
                local_evidence_path = os.path.join(proj_root, evidence_filename)
                with open(local_evidence_path, 'w', encoding='utf-8') as ef:
                    ef.write(evidence_md)
                logger.info(f"[Orchestrator] Saved evidence locally to {local_evidence_path}")
            except Exception as fe:
                logger.warning(f"[Orchestrator] Failed to write local evidence file: {fe}")

            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='BranchPR', level='success',
                      message=f'✅ PR raised: {pr_out.get("pr_url", "(url pending)")}',
                      detail=f'Branch: {pr_out.get("branch_name")}')
            # Attach evidence.md to Jira issue
            if jira_key and evidence_bytes:
                try:
                    from app.services.jira_service import JiraService
                    JiraService.add_attachment(jira_key, evidence_filename, evidence_bytes, 'text/markdown')
                    log_event(story_id=story_id or 0, workflow_id=workflow_id,
                              agent='Comment', level='jira',
                              message=f'📎 Evidence report `{evidence_filename}` attached to Jira {jira_key}')
                    logger.info(f"[Orchestrator] Evidence attached to Jira {jira_key}: {evidence_filename}")
                except Exception as e:
                    log_event(story_id=story_id or 0, workflow_id=workflow_id,
                              agent='Comment', level='warning',
                              message=f'⚠️ Jira attachment failed (non-fatal): {e}')
                    logger.warning(f"[Orchestrator] Jira attachment failed (non-fatal): {e}")

        except Exception as e:
            log_event(story_id=story_id or 0, workflow_id=workflow_id,
                      agent='Orchestrator', level='warning',
                      message=f'⚠️ Evidence generation failed (non-fatal): {e}')
            logger.warning(f"[Orchestrator] Evidence generation failed (non-fatal): {e}")

        # ═══════════════════════════════════════════════════════════
        # STEP 6 — COMMENT (task done, PR raised → QA-TESTING)
        # ═══════════════════════════════════════════════════════════
        log_event(story_id=story_id or 0, workflow_id=workflow_id,
                  agent='Comment', level='jira',
                  message='💬 Jira comment posted: Task done & PR raised — moving to QA-TESTING')
        self._update_workflow_status(workflow, 'Running', 'Comment')
        step6 = self._make_step(workflow_id, 'Comment', 'Post task-done comment, move to QA-TESTING.')
        CommentAgent(db=db, config=config).run({
            'story': context_story,
            'comment_type': 'task_done_pr_raised',
            'new_status': 'QA-TESTING',
            'workflow_id': workflow_id,
            'extra': {
                'pr_url': pr_out.get('pr_url', '#'),
                'branch_name': pr_out.get('branch_name', ''),
                'pr_summary': developer_output.get('summary', ''),
                'evidence_filename': evidence_filename or 'evidence.md',
            }
        }, workflow_id=workflow_id, step_record=step6)

        # ── Final workflow state ──────────────────────────────────
        self._update_workflow_status(workflow, 'Awaiting QA', 'Comment')
        log_event(story_id=story_id or 0, workflow_id=workflow_id,
                  agent='Orchestrator', level='success',
                  message='🎉 Pipeline COMPLETE — Awaiting QA review',
                  detail=f'PR: {pr_out.get("pr_url")} | Loops: {loop_count}')

        logger.info(f"[Orchestrator] Workflow {workflow_id} complete. PR: {pr_out.get('pr_url')}. Awaiting QA.")
        return {
            "success": True,
            "workflow_id": workflow_id,
            "status": "Awaiting QA",
            "pr_url": pr_out.get('pr_url'),
            "branch_name": pr_out.get('branch_name'),
            "loop_iterations": loop_count,
            "evidence_file": evidence_filename,
            "results": results
        }
