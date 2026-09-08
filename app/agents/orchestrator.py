"""
Orchestrator — Central state machine for DEVAA.
Runs all 8 agents in sequence:
  Intake → RepoAnalysis → [Developer ↔ Validator loop] → BranchPR → Comment

Persists a WorkflowStep for each agent execution.
Enforces loop limit via AGENT_MAX_LOOP_ITERATIONS.
"""
import logging
from flask import current_app
from app import db
from app.models.workflow import Workflow, WorkflowStep
from app.models.story import Story
from app.models.devaa_models import SuccessMetric

from app.agents.intake_validation_agent import IntakeValidationAgent
from app.agents.repo_analysis_agent import RepoAnalysisAgent
from app.agents.developer_agent import DeveloperAgent
from app.agents.validator_agent import ValidatorAgent
from app.agents.branch_pr_agent import BranchPRAgent
from app.agents.comment_agent import CommentAgent
from app.agents.rework_handler import ReworkHandler

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
            # Fallback: treat workflow itself as the story context
            logger.warning(f"No story linked to workflow {workflow_id}. Running in standalone mode.")

        config = self.config
        results = {}
        story_id = story.id if story else None

        self._update_workflow_status(workflow, 'Running', 'Intake')
        logger.info(f"[Orchestrator] Starting workflow {workflow_id}")

        # ═══════════════════════════════════════════════════════════
        # STEP 1 — INTAKE VALIDATION
        # ═══════════════════════════════════════════════════════════
        step1 = self._make_step(workflow_id, 'Intake', 'Validate story fields and completeness.')
        intake_agent = IntakeValidationAgent(db=db, config=config)

        context_story = story or {'title': workflow.title, 'description': workflow.requirements_doc,
                                   'acceptance_criteria': '', 'source_branch': '', 'repository_details': []}

        intake_result = intake_agent.run({'story': context_story}, workflow_id=workflow_id, step_record=step1)
        results['intake'] = intake_result.output

        if not intake_result.success:
            # Post validation failure comment
            step_c = self._make_step(workflow_id, 'Comment', 'Post validation failure comment.')
            comment_agent = CommentAgent(db=db, config=config)
            comment_agent.run({
                'story': context_story,
                'comment_type': 'validation_failure',
                'new_status': 'INVALID',
                'workflow_id': workflow_id,
                'extra': {'details': intake_result.error or 'Fields missing.'}
            }, workflow_id=workflow_id, step_record=step_c)

            self._update_workflow_status(workflow, 'Failed', 'Intake')
            self._record_metric(workflow_id, story_id, 'eligibility_accuracy', 0.0, 'Intake validation failed')
            return {"success": False, "stage": "intake", "error": intake_result.error, "results": results}

        self._record_metric(workflow_id, story_id, 'eligibility_accuracy', 1.0, 'Intake validation passed')

        # Post "ready for dev" comment
        step_c1 = self._make_step(workflow_id, 'Comment', 'Post ready-for-dev comment.')
        CommentAgent(db=db, config=config).run({
            'story': context_story, 'comment_type': 'ready_for_dev',
            'new_status': 'IN-PROGRESS', 'workflow_id': workflow_id, 'extra': {}
        }, workflow_id=workflow_id, step_record=step_c1)

        # ═══════════════════════════════════════════════════════════
        # STEP 2 — REPOSITORY ANALYSIS
        # ═══════════════════════════════════════════════════════════
        self._update_workflow_status(workflow, 'Running', 'RepoAnalysis')
        step2 = self._make_step(workflow_id, 'RepoAnalysis', 'Analyze repositories and build implementation map.')
        repo_result = RepoAnalysisAgent(db=db, config=config).run(
            {'story': context_story}, workflow_id=workflow_id, step_record=step2
        )
        results['repo_analysis'] = repo_result.output

        if not repo_result.success:
            self._update_workflow_status(workflow, 'Failed', 'RepoAnalysis')
            return {"success": False, "stage": "repo_analysis", "error": repo_result.error, "results": results}

        implementation_map = repo_result.output
        qa_feedback = None  # Will be set if QA rejects

        # ═══════════════════════════════════════════════════════════
        # STEP 3+4 — DEVELOPER ↔ VALIDATOR SELF-CORRECTION LOOP
        # ═══════════════════════════════════════════════════════════
        developer_output = None
        validation_passed = False
        loop_count = 0

        while loop_count < self.max_loops:
            loop_count += 1
            self._update_workflow_status(workflow, 'Running', 'Developer')
            workflow.loop_iteration = loop_count
            db.session.commit()

            # Developer
            step_dev = self._make_step(workflow_id, 'Developer',
                                        f'Generate implementation (iteration {loop_count}).', loop_count)
            dev_result = DeveloperAgent(db=db, config=config).run({
                'story': context_story,
                'implementation_map': implementation_map,
                'qa_feedback': qa_feedback,
                'loop_iteration': loop_count
            }, workflow_id=workflow_id, step_record=step_dev)

            if not dev_result.success:
                self._update_workflow_status(workflow, 'Failed', 'Developer')
                return {"success": False, "stage": "developer", "error": dev_result.error, "results": results}

            developer_output = dev_result.output
            results[f'developer_loop_{loop_count}'] = developer_output

            # Validator
            self._update_workflow_status(workflow, 'Running', 'Validator')
            step_val = self._make_step(workflow_id, 'Validator',
                                        f'Validate implementation (iteration {loop_count}).', loop_count)
            val_result = ValidatorAgent(db=db, config=config).run({
                'story': context_story,
                'developer_output': developer_output,
                'loop_iteration': loop_count
            }, workflow_id=workflow_id, step_record=step_val)

            results[f'validator_loop_{loop_count}'] = val_result.output

            if val_result.success:
                validation_passed = True
                self._record_metric(workflow_id, story_id, 'acceptance_criteria_coverage', 1.0,
                                     f'Passed on iteration {loop_count}')
                self._record_metric(workflow_id, story_id, 'loop_iterations', float(loop_count))
                break
            else:
                # Prepare feedback for next dev iteration
                qa_feedback = val_result.output.get('feedback_for_developer', '')
                logger.info(f"[Orchestrator] Validator rejected (loop {loop_count}). Feedback: {qa_feedback[:100]}")

        if not validation_passed:
            logger.warning(f"[Orchestrator] Max loop iterations ({self.max_loops}) reached without validation pass.")
            self._record_metric(workflow_id, story_id, 'acceptance_criteria_coverage', 0.0,
                                 f'Failed after {self.max_loops} iterations')
            self._update_workflow_status(workflow, 'Failed', 'Validator')
            return {
                "success": False,
                "stage": "validator",
                "error": f"Max loop iterations ({self.max_loops}) reached. Human review required.",
                "results": results
            }

        # ═══════════════════════════════════════════════════════════
        # STEP 5 — BRANCH & PR
        # ═══════════════════════════════════════════════════════════
        self._update_workflow_status(workflow, 'Running', 'BranchPR')
        step5 = self._make_step(workflow_id, 'BranchPR', 'Create feature branch and pull request.')
        pr_result = BranchPRAgent(db=db, config=config).run({
            'story': context_story,
            'workflow_id': workflow_id,
            'developer_output': developer_output,
            'triggered_by_user_id': triggered_by_user_id
        }, workflow_id=workflow_id, step_record=step5)

        results['branch_pr'] = pr_result.output

        if not pr_result.success:
            self._update_workflow_status(workflow, 'Failed', 'BranchPR')
            return {"success": False, "stage": "branch_pr", "error": pr_result.error, "results": results}

        # ═══════════════════════════════════════════════════════════
        # STEP 6 — COMMENT (PR ready → QA-TESTING)
        # ═══════════════════════════════════════════════════════════
        self._update_workflow_status(workflow, 'Running', 'Comment')
        step6 = self._make_step(workflow_id, 'Comment', 'Post PR-ready comment, move to QA-TESTING.')
        pr_out = pr_result.output
        changed_files_text = "\n".join([f"- `{f}`" for f in (pr_out.get('changed_files') or [])])
        CommentAgent(db=db, config=config).run({
            'story': context_story,
            'comment_type': 'pr_ready',
            'new_status': 'QA-TESTING',
            'workflow_id': workflow_id,
            'extra': {
                'pr_url': pr_out.get('pr_url', '#'),
                'branch_name': pr_out.get('branch_name', ''),
                'changed_files': changed_files_text or 'No files listed',
                'pr_summary': developer_output.get('summary', '')
            }
        }, workflow_id=workflow_id, step_record=step6)

        # ── Final workflow state ──────────────────────────────────
        self._update_workflow_status(workflow, 'Awaiting QA', 'Comment')
        results['branch_pr'] = pr_out

        logger.info(f"[Orchestrator] Workflow {workflow_id} complete. Awaiting QA.")
        return {
            "success": True,
            "workflow_id": workflow_id,
            "status": "Awaiting QA",
            "pr_url": pr_out.get('pr_url'),
            "branch_name": pr_out.get('branch_name'),
            "loop_iterations": loop_count,
            "results": results
        }
