import logging
import re
from flask import current_app
from app import db
from app.models.story import Story
from app.models.user import User
from app.services.task_providers.factory import TaskProviderFactory

logger = logging.getLogger(__name__)

class SyncService:

    @staticmethod
    def delete_story_and_relations(story_id: int) -> bool:
        """
        Cleanly removes a story and all related DB entities (logs, metrics, workflows, etc.).
        """
        try:
            story = Story.query.get(story_id)
            if not story:
                return False

            from app.models.devaa_models import PipelineLog, AuditLog, GuardrailEvent, SuccessMetric, QAReview, PullRequest
            from app.models.workflow import Workflow, WorkflowStep

            wf_ids = [w.id for w in Workflow.query.filter_by(story_id=story_id).all()]
            if wf_ids:
                WorkflowStep.query.filter(WorkflowStep.workflow_id.in_(wf_ids)).delete(synchronize_session=False)

            PipelineLog.query.filter_by(story_id=story_id).delete(synchronize_session=False)
            AuditLog.query.filter_by(story_id=story_id).delete(synchronize_session=False)
            GuardrailEvent.query.filter_by(story_id=story_id).delete(synchronize_session=False)
            SuccessMetric.query.filter_by(story_id=story_id).delete(synchronize_session=False)
            QAReview.query.filter_by(story_id=story_id).delete(synchronize_session=False)
            PullRequest.query.filter_by(story_id=story_id).delete(synchronize_session=False)
            Workflow.query.filter_by(story_id=story_id).delete(synchronize_session=False)

            db.session.delete(story)
            db.session.commit()
            logger.info(f"Successfully deleted story ID {story_id} and relations from database.")
            return True
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Error deleting story {story_id}: {e}")
            return False

    @staticmethod
    def sync_assigned_tasks(user_id: int, project_key: str = None) -> dict:
        """
        Syncs external tasks from the configured project/provider into the local DEVAA database.
        Returns a dict with a summary of the sync operation.
        """
        user = User.query.get(user_id)
        if not user or not user.email:
            return {"error": "User not found or has no email address configured."}

        provider = TaskProviderFactory.get_provider()
        if not provider:
            return {"message": "No active external task provider configured. Skipping sync."}

        provider_name = current_app.config.get('ACTIVE_TASK_PROVIDER', 'manual').lower()
        min_priority = current_app.config.get('MIN_SYNC_PRIORITY', 'all')
        proj_key = (project_key or current_app.config.get('JIRA_PROJECT_KEY') or '').strip().upper()
        
        try:
            # Fetch tasks from external provider (Jira, etc.)
            external_tasks = provider.fetch_tasks(
                assignee_email=user.email, 
                min_priority=min_priority, 
                status="all", 
                project_key=proj_key
            )
            
            created_count = 0
            updated_count = 0
            deleted_count = 0
            skipped_count = 0

            default_base_branch = current_app.config.get('GITHUB_DEFAULT_BASE_BRANCH', 'main')
            default_repo_url = current_app.config.get('GITHUB_BASE_URL', '')

            seen_keys = set()

            for task in external_tasks:
                raw_key = (task.external_id or '').strip()
                if not raw_key or raw_key.lower() in seen_keys:
                    skipped_count += 1
                    continue
                seen_keys.add(raw_key.lower())

                # Check if it already exists locally (case-insensitive)
                existing = Story.query.filter(
                    (db.func.lower(Story.external_task_id) == raw_key.lower()) |
                    (db.func.lower(Story.jira_story_key) == raw_key.lower())
                ).first()

                # Extract acceptance criteria if present in description
                task_ac = (task.acceptance_criteria or "").strip()
                task_title = (task.title or "").strip()
                raw_desc = (task.description or "").strip()
                task_desc = raw_desc
                task_prio = task.priority or "Medium"

                if raw_desc:
                    ac_header = re.search(r'(?:^|\n)\s*(?:h\d+\.\s*)?(?:Acceptance Criteria|Acceptance Criterion|ACs?)\s*[:\n\-]+', raw_desc, re.IGNORECASE)
                    if ac_header:
                        clean_desc = raw_desc[:ac_header.start()].strip()
                        extracted_ac = raw_desc[ac_header.end():].strip()
                        if not task_ac:
                            task_ac = extracted_ac
                        if clean_desc:
                            task_desc = clean_desc
                    else:
                        ac_direct = re.search(r'(?:^|\n)\s*((?:AC\d+|AC\s*\d+)[\s\S]*)', raw_desc, re.IGNORECASE)
                        if ac_direct:
                            clean_desc = raw_desc[:ac_direct.start()].strip()
                            extracted_ac = ac_direct.group(1).strip()
                            if not task_ac:
                                task_ac = extracted_ac
                            if clean_desc:
                                task_desc = clean_desc

                # Strip repository configuration block from description and AC
                repo_block_pattern = r'(?:^|\n)\s*(?:(?:🛠️|⚙️|🌐|📚|📦)\s*)?(?:Repository & Reference Configuration|Repository Configuration|Frontend Configuration|Backend Configuration|Reference Configuration|Target Repo(?:sitory)?(?:\s*\d+)?)'
                repo_match_desc = re.search(repo_block_pattern, task_desc, re.IGNORECASE)
                if repo_match_desc:
                    task_desc = task_desc[:repo_match_desc.start()].strip()
                    
                if task_ac:
                    repo_match_ac = re.search(repo_block_pattern, task_ac, re.IGNORECASE)
                    if repo_match_ac:
                        task_ac = task_ac[:repo_match_ac.start()].strip()

                # If raw description contains explicit "Title: ...", use it
                t_match = re.search(r'(?:^|\n)\s*Title\s*:\s*([^\n\r]+)', raw_desc, re.IGNORECASE)
                if t_match and t_match.group(1).strip():
                    task_title = t_match.group(1).strip()

                # ── Extract repository details from description if present ──
                from app.services.token_secret_service import TokenSecretService

                story_repos = []

                # Find all Target Repos using finditer
                target_repo_matches = list(re.finditer(r'(?:^|\n)\s*(?:Target\s*Repo(?:sitory)?(?:\s*\d+)?|Target(?:\s*\d+)?|Repository|Repo(?:\s*\d+)?)\s*[:\-]\s*([^\s\n\r]+)', raw_desc, re.IGNORECASE))
                target_repo_indices = [m.start() for m in target_repo_matches]

                if not target_repo_matches:
                    repo_info = {
                        "name": "main-repo",
                        "url": default_repo_url,
                        "branch": default_base_branch,
                        "external_assignee": task.assignee_email,
                        "priority": task_prio
                    }
                    if getattr(task, 'due_date', None):
                        repo_info["due_date"] = task.due_date
                    if getattr(task, 'start_date', None):
                        repo_info["start_date"] = task.start_date
                    story_repos.append(repo_info)
                else:
                    for i, m in enumerate(target_repo_matches):
                        parsed_name = m.group(1).strip().strip('`').strip('"').strip("'")
                        
                        start_idx = m.end()
                        end_idx = target_repo_indices[i+1] if i + 1 < len(target_repo_indices) else len(raw_desc)
                        block = raw_desc[start_idx:end_idx]
                        
                        ref_idx = re.search(r'(?:^|\n)\s*(?:Reference\s*Repo|Ref\s*Repo|Reference)\s*[:\-]', block, re.IGNORECASE)
                        if ref_idx:
                            block = block[:ref_idx.start()]
                        
                        b_match = re.search(r'(?:^|\n)\s*(?:Target\s*Branch|Base\s*Branch|Source\s*Branch|Branch)\s*[:\-]\s*([^\s\n\r]+)', block, re.IGNORECASE)
                        u_match = re.search(r'(?:^|\n)\s*(?:Target\s*URL|Target\s*Git|URL|Link|Git)\s*[:\-]\s*(https?://[^\s\n\r]+|git@[^\s\n\r]+)', block, re.IGNORECASE)
                        
                        t_name = parsed_name
                        t_url = default_repo_url
                        t_branch = default_base_branch
                        
                        repo_cfg = TokenSecretService.get_repo_config(parsed_name)
                        if repo_cfg:
                            if repo_cfg.get('url'):
                                t_url = repo_cfg.get('url')
                            if repo_cfg.get('branch'):
                                t_branch = repo_cfg.get('branch')
                        
                        if u_match:
                            t_url = u_match.group(1).strip().strip('`')
                        if b_match:
                            t_branch = b_match.group(1).strip().strip('`')
                            
                        repo_info = {
                            "name": t_name,
                            "url": t_url,
                            "branch": t_branch,
                            "external_assignee": task.assignee_email,
                            "priority": task_prio
                        }
                        if getattr(task, 'due_date', None):
                            repo_info["due_date"] = task.due_date
                        if getattr(task, 'start_date', None):
                            repo_info["start_date"] = task.start_date
                        story_repos.append(repo_info)

                target_repo_branch = story_repos[0]["branch"] if story_repos else default_base_branch

                # Optional Reference Repo
                ref_repo_m = re.search(r'(?:Reference\s*Repo(?:sitory)?|Ref\s*Repo(?:sitory)?|Reference)\s*[:\-]\s*([^\s\n\r]+)', raw_desc, re.IGNORECASE)
                ref_repo_info = None
                if ref_repo_m:
                    ref_parsed = ref_repo_m.group(1).strip().strip('`').strip('"').strip("'")
                    
                    # Extract branch right after reference repo
                    ref_branch_m = re.search(r'Branch\s*[:\-]\s*([^\s\n\r]+)', raw_desc[ref_repo_m.end():], re.IGNORECASE)
                    
                    ref_cfg = TokenSecretService.get_repo_config(ref_parsed)
                    ref_url = (ref_cfg.get('url') if ref_cfg else None) or (ref_parsed if ref_parsed.startswith('http') or ref_parsed.startswith('git@') else f"https://github.com/Devil-008/{ref_parsed}.git")
                    ref_branch = (ref_branch_m.group(1).strip().strip('`') if ref_branch_m else None) or (ref_cfg.get('branch') if ref_cfg else 'main') or 'main'
                    if ref_parsed and ref_url:
                        ref_repo_info = {
                            "name": ref_parsed,
                            "url": ref_url,
                            "branch": ref_branch,
                            "is_reference": True
                        }
                if ref_repo_info:
                    story_repos.append(ref_repo_info)

                # Map external task status to DEVAA status
                ext_status = (task.status or 'TO-DO').upper().replace(' ', '-')
                if ext_status in ('DONE', 'COMPLETED', 'RESOLVED', 'CLOSED'):
                    mapped_status = 'DONE'
                elif ext_status in ('IN-PROGRESS', 'IN PROGRESS', 'DEVELOPMENT'):
                    mapped_status = 'IN-PROGRESS'
                elif ext_status in ('QA-TESTING', 'QA', 'IN-REVIEW', 'REVIEW'):
                    mapped_status = 'QA-TESTING'
                else:
                    mapped_status = 'TO-DO'

                if existing:
                    # Update any missing or modified fields on existing story
                    updated = False
                    if not existing.source_branch or existing.source_branch != target_repo_branch:
                        existing.source_branch = target_repo_branch
                        updated = True
                    if task_desc is not None and (existing.description or '') != task_desc:
                        existing.description = task_desc
                        updated = True
                    if task_ac is not None and (existing.acceptance_criteria or '') != task_ac:
                        existing.acceptance_criteria = task_ac
                        updated = True
                    # Update title if changed in Jira
                    if task_title and existing.title != task_title:
                        existing.title = task_title
                        updated = True
                    
                    # Keep story_points if it exists in previous details and isn't provided by task
                    old_details = [dict(d) for d in (existing.repository_details or [{}])]
                    if old_details and isinstance(old_details[0], dict):
                        old_sp = old_details[0].get('story_points')
                        if task.story_points is not None:
                            old_sp = task.story_points
                        if old_sp is not None:
                            for sr in story_repos:
                                sr['story_points'] = old_sp

                    # Compare and update repository details if changed
                    import json
                    if json.dumps(old_details, sort_keys=True) != json.dumps(story_repos, sort_keys=True):
                        existing.repository_details = story_repos
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(existing, 'repository_details')
                        updated = True

                    # Status sync: only update if no active pipeline workflow is currently executing
                    from app.models.workflow import Workflow
                    from app.models.devaa_models import PullRequest
                    active_wf = Workflow.query.filter_by(story_id=existing.id, status='running').first()
                    awaiting_qa_wf = Workflow.query.filter(
                        Workflow.story_id == existing.id,
                        Workflow.status.in_(['Awaiting QA', 'Completed', 'completed'])
                    ).order_by(Workflow.id.desc()).first()
                    open_pr = PullRequest.query.filter_by(story_id=existing.id, pr_status='open').first()

                    if not active_wf:
                        is_awaiting_qa = (existing.status == 'QA-TESTING') or bool(awaiting_qa_wf) or bool(open_pr)
                        # When Jira workflow only has To Do / In Progress / Done, Jira stays 'In Progress' during QA review.
                        # Do not downgrade QA-TESTING or stories with open PRs to IN-PROGRESS.
                        if is_awaiting_qa and mapped_status == 'IN-PROGRESS':
                            if existing.status != 'QA-TESTING':
                                existing.status = 'QA-TESTING'
                                updated = True
                        elif existing.status != mapped_status:
                            existing.status = mapped_status
                            updated = True
                    elif existing.status == 'INVALID':
                        existing.status = 'TO-DO'
                        updated = True

                    if updated:
                        try:
                            db.session.commit()
                            updated_count += 1
                        except Exception as e:
                            db.session.rollback()
                            logger.warning(f"Could not update existing story {existing.id}: {e}")
                            skipped_count += 1
                    else:
                        skipped_count += 1
                    # ── Check for DEVAA Rework Comments ──
                    if provider_name == 'jira':
                        from app.services.jira_service import JiraService
                        from app.models.devaa_models import QAReview
                        from app.agents.orchestrator import Orchestrator
                        try:
                            comments = JiraService.get_issue_comments(existing.jira_story_key or existing.external_task_id)
                        except Exception as ce:
                            logger.warning(f"Could not fetch comments for {existing.external_task_id}: {ce}")
                            comments = []
                        for c in comments:
                            c_id = c.get('id')
                            raw_body = c.get('body', '')

                            # Jira v2 returns body as string, v3 returns ADF (dict).
                            # Normalise to plain text.
                            if isinstance(raw_body, dict):
                                # ADF → extract text from content nodes
                                def _adf_text(node):
                                    if isinstance(node, str):
                                        return node
                                    if isinstance(node, dict):
                                        txt = node.get('text', '')
                                        children = node.get('content', [])
                                        return txt + ''.join(_adf_text(ch) for ch in children)
                                    if isinstance(node, list):
                                        return ''.join(_adf_text(n) for n in node)
                                    return ''
                                c_body = _adf_text(raw_body).strip()
                            else:
                                c_body = str(raw_body).strip()

                            # Skip DEVAA's own auto-comments (bot replies)
                            if c_body.startswith('🚀') or c_body.startswith('🤖') or c_body.startswith('✅'):
                                continue
                            if '*DEVAA:' in c_body or 'DEVAA Agent received' in c_body:
                                continue

                            # Match user-written DEVAA tags:
                            #  - [DEVAA] ...          (square bracket tag)
                            #  - @DEVAA ...           (plain mention)
                            #  - {{@DEVAA}} ...       (Jira monospace mention)
                            #  - [~DEVAA] ...         (Jira wiki mention)
                            import re as _re
                            is_devaa = bool(_re.search(r'(?:\[DEVAA\]|\{\{@DEVAA\}\}|@DEVAA|\[~DEVAA\])', c_body, _re.IGNORECASE))

                            if is_devaa:
                                tag = f"[JIRA-{c_id}]"
                                existing_qa = QAReview.query.filter(QAReview.story_id == existing.id, QAReview.comments.like(f"{tag}%")).first()
                                if not existing_qa:
                                    # Clean the body: remove the DEVAA tag prefix for cleaner feedback
                                    clean_body = _re.sub(r'^\s*(?:\{\{@DEVAA\}\}|\[DEVAA\]|@DEVAA|DEVAA)\s*', '', c_body, flags=_re.IGNORECASE).strip()
                                    if not clean_body:
                                        clean_body = c_body

                                    # Look up workflow_id and pr_id for this story (required by QAReview model)
                                    from app.models.workflow import Workflow
                                    from app.models.devaa_models import PullRequest as PRModel
                                    latest_wf = Workflow.query.filter_by(story_id=existing.id).order_by(Workflow.id.desc()).first()
                                    latest_pr = PRModel.query.filter_by(story_id=existing.id).order_by(PRModel.id.desc()).first()

                                    if not latest_wf:
                                        logger.warning(f"No workflow found for story {existing.id}, cannot create QAReview from Jira comment")
                                        continue

                                    # ── REWORK INTENT ──
                                    logger.info(f"🔄 Triggering pipeline for {existing.external_task_id} from Jira comment {c_id}: {clean_body[:80]}")
                                    new_qa = QAReview(
                                        story_id=existing.id,
                                        workflow_id=latest_wf.id,
                                        pr_id=latest_pr.id if latest_pr else None,
                                        reviewer_id=user.id,
                                        decision='rejected',
                                        comments=f"{tag} {clean_body}",
                                        is_rework=True
                                    )
                                    db.session.add(new_qa)
                                    db.session.commit()

                                    # Post acknowledgment back to Jira
                                    try:
                                        JiraService.add_comment(
                                            existing.jira_story_key or existing.external_task_id,
                                            f"🤖 DEVAA Agent received your feedback and has started rework:\n\n\"{clean_body[:200]}\"\n\nRework pipeline initiated. You'll be notified when it's complete."
                                        )
                                    except Exception:
                                        pass

                                    # If workflow is not active, resume it from ReworkHandler
                                    target_wf = awaiting_qa_wf or latest_wf
                                    if target_wf:
                                        target_wf.status = 'running'
                                        target_wf.workflow_type = 'rework'
                                        db.session.commit()
                                        try:
                                            import threading
                                            def run_async_orchestrator(wf_id, app, ui_trigger_user):
                                                with app.app_context():
                                                    from app.agents.orchestrator import Orchestrator
                                                    orchestrator = Orchestrator()
                                                    orchestrator.run(workflow_id=wf_id, triggered_by_user_id=ui_trigger_user)

                                            # Pass current_app._get_current_object() safely
                                            threading.Thread(
                                                target=run_async_orchestrator,
                                                args=(target_wf.id, current_app._get_current_object(), user.id),
                                                daemon=True
                                            ).start()
                                        except Exception as e:
                                            logger.error(f"Failed to trigger async orchestrator for rework: {e}")

                    continue

                # story_repos list is already built during the extraction phase above.
                # Just passing through to Story creation.

                # Create a new local DEVAA Story based on the external task
                new_story = Story(
                    external_task_id=task.external_id,
                    external_provider=provider_name,
                    jira_story_key=task.external_id if provider_name == 'jira' else None,
                    title=task_title,
                    description=task_desc,
                    acceptance_criteria=task_ac,
                    source_branch=target_repo_branch,
                    assignee_id=user.id,
                    owner_id=user.id,
                    status=mapped_status,
                    repository_details=story_repos
                )
                try:
                    db.session.add(new_story)
                    db.session.commit()
                    created_count += 1
                except Exception as insert_err:
                    db.session.rollback()
                    logger.warning(f"Failed to insert story {task.external_id} (already exists or constraint violation): {insert_err}")
                    skipped_count += 1

            # ── Check for stories in DEVAA that were deleted in Jira ──
            if provider_name == 'jira':
                from app.services.jira_service import JiraService
                all_local_stories = Story.query.all()
                for story in all_local_stories:
                    key = (story.jira_story_key or story.external_task_id or '').strip()
                    if not key:
                        continue
                    # Only check stories belonging to the target Jira project (e.g. SCRUM-...)
                    if proj_key and not key.upper().startswith(f"{proj_key}-"):
                        continue

                    # If this story's key is NOT among active Jira tasks
                    if key.lower() not in seen_keys:
                        status_info = JiraService.check_issue_status(key)
                        if status_info.get('status_code') == 404:
                            logger.info(f"Story {key} (id={story.id}) was deleted from Jira (404). Pruning from DEVAA...")
                            if SyncService.delete_story_and_relations(story.id):
                                deleted_count += 1
                        else:
                            logger.debug(f"Story {key} not in fetch_tasks list, but check_issue_status returned status {status_info.get('status_code')}. Keeping.")

            return {
                "message": "Sync completed successfully.",
                "tasks_fetched": len(external_tasks),
                "stories_created": created_count,
                "stories_updated": updated_count,
                "stories_deleted": deleted_count,
                "stories_skipped": skipped_count
            }
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Error during task sync: {e}")
            return {"error": str(e)}
