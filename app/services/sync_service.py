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

                # If raw description contains explicit "Title: ...", use it
                t_match = re.search(r'(?:^|\n)\s*Title\s*:\s*([^\n\r]+)', raw_desc, re.IGNORECASE)
                if t_match and t_match.group(1).strip():
                    task_title = t_match.group(1).strip()

                # ── Extract repository details from description if present ──
                target_repo_name = "main-repo"
                target_repo_url = default_repo_url
                target_repo_branch = default_base_branch

                from app.services.token_secret_service import TokenSecretService

                # Primary / Target Repo
                repo_name_m = re.search(r'(?:^|\n)\s*(?:Target\s*Repo(?:sitory)?|Target|Repository|Repo|Name)\s*[:\-]\s*([^\s\n\r]+)', raw_desc, re.IGNORECASE)
                repo_url_m = re.search(r'(?:^|\n)\s*(?:Target\s*URL|Target\s*Git|URL|Link|Git)\s*[:\-]\s*(https?://[^\s\n\r]+|git@[^\s\n\r]+)', raw_desc, re.IGNORECASE)
                branch_m = re.search(r'(?:^|\n)\s*(?:Target\s*Branch|Base\s*Branch|Source\s*Branch|Branch)\s*[:\-]\s*([^\s\n\r]+)', raw_desc, re.IGNORECASE)

                if repo_name_m:
                    parsed_name = repo_name_m.group(1).strip().strip('`').strip('"').strip("'")
                    target_repo_name = parsed_name
                    repo_cfg = TokenSecretService.get_repo_config(parsed_name)
                    if repo_cfg:
                        if repo_cfg.get('url'):
                            target_repo_url = repo_cfg.get('url')
                        if repo_cfg.get('branch'):
                            target_repo_branch = repo_cfg.get('branch')

                if repo_url_m:
                    target_repo_url = repo_url_m.group(1).strip().strip('`')
                if branch_m:
                    target_repo_branch = branch_m.group(1).strip().strip('`')

                # Optional Reference Repo
                ref_repo_m = re.search(r'(?:^|\n)\s*(?:Reference\s*Repo(?:sitory)?|Ref\s*Repo(?:sitory)?|Reference)\s*[:\-]\s*([^\s\n\r]+)', raw_desc, re.IGNORECASE)
                ref_branch_m = re.search(r'(?:^|\n)\s*(?:Reference\s*Branch|Ref\s*Branch)\s*[:\-]\s*([^\s\n\r]+)', raw_desc, re.IGNORECASE)
                ref_repo_info = None
                if ref_repo_m:
                    ref_parsed = ref_repo_m.group(1).strip().strip('`').strip('"').strip("'")
                    ref_cfg = TokenSecretService.get_repo_config(ref_parsed)
                    ref_url = (ref_cfg.get('url') if ref_cfg else None) or (ref_parsed if ref_parsed.startswith('http') or ref_parsed.startswith('git@') else '')
                    ref_branch = (ref_branch_m.group(1).strip().strip('`') if ref_branch_m else None) or (ref_cfg.get('branch') if ref_cfg else 'main') or 'main'
                    if ref_parsed and ref_url:
                        ref_repo_info = {
                            "name": ref_parsed,
                            "url": ref_url,
                            "branch": ref_branch,
                            "is_reference": True
                        }

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
                    if not existing.source_branch or (branch_m and existing.source_branch != target_repo_branch):
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
                    
                    details = [dict(d) for d in (existing.repository_details or [{}])]
                    if details and isinstance(details[0], dict):
                        first_det = details[0]
                        det_updated = False
                        if repo_name_m and first_det.get('name') != target_repo_name:
                            first_det['name'] = target_repo_name
                            det_updated = True
                        if target_repo_url and first_det.get('url') != target_repo_url:
                            first_det['url'] = target_repo_url
                            det_updated = True
                        if target_repo_branch and first_det.get('branch') != target_repo_branch:
                            first_det['branch'] = target_repo_branch
                            det_updated = True
                        if task_prio and first_det.get('priority') != task_prio:
                            first_det['priority'] = task_prio
                            det_updated = True
                        if task.assignee_email and first_det.get('external_assignee') != task.assignee_email:
                            first_det['external_assignee'] = task.assignee_email
                            det_updated = True
                        if task.story_points is not None and first_det.get('story_points') != task.story_points:
                            first_det['story_points'] = task.story_points
                            det_updated = True
                        if task.due_date and first_det.get('due_date') != task.due_date:
                            first_det['due_date'] = task.due_date
                            det_updated = True
                        if ref_repo_info:
                            has_ref = False
                            for idx, d in enumerate(details):
                                if idx > 0 and (d.get('is_reference') or d.get('name') == ref_repo_info['name']):
                                    d.update(ref_repo_info)
                                    has_ref = True
                                    det_updated = True
                                    break
                            if not has_ref:
                                details.append(ref_repo_info)
                                det_updated = True

                        if det_updated:
                            existing.repository_details = details
                            from sqlalchemy.orm.attributes import flag_modified
                            flag_modified(existing, 'repository_details')
                            updated = True

                    # Status sync: only update if no active pipeline workflow is currently executing
                    from app.models.workflow import Workflow
                    from app.models.devaa_models import PullRequest
                    active_wf = Workflow.query.filter_by(story_id=existing.id, status='running').first()
                    awaiting_qa_wf = Workflow.query.filter_by(story_id=existing.id, status='Awaiting QA').first()
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

                                    logger.info(f"🔄 Triggering rework for {existing.external_task_id} from Jira comment {c_id}: {clean_body[:80]}")
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
                                    if awaiting_qa_wf:
                                        awaiting_qa_wf.status = 'running'
                                        db.session.commit()
                                        try:
                                            import threading
                                            threading.Thread(target=Orchestrator.run_workflow, args=(awaiting_qa_wf.id, current_app._get_current_object()), daemon=True).start()
                                        except Exception as e:
                                            logger.error(f"Failed to trigger async orchestrator for rework: {e}")

                    continue

                repo_info = {
                    "name": target_repo_name,
                    "url": target_repo_url,
                    "branch": target_repo_branch,
                    "external_assignee": task.assignee_email,
                    "priority": task_prio
                }
                if task.due_date:
                    repo_info["due_date"] = task.due_date

                story_repos = [repo_info]
                if ref_repo_info:
                    story_repos.append(ref_repo_info)

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
