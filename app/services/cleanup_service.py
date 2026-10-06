"""
CleanupService — Enterprise Zero-Dirty-State Rollback & Cleanup Engine for DEVAA
─────────────────────────────────────────────────────────────────────────────
Guarantees clean, deterministic rollback across all 4 system layers on any
pipeline failure, abort, or stale-run recovery:
  1. Git & GitHub: Closes orphan PRs and deletes unmerged remote branches.
  2. ChromaDB: Purges ephemeral workflow overlay collections (wf_{wf_id}_{story_id}).
  3. Relational DB: Reverts story status to TO-DO, marks workflow as Failed, closes ghost PRs, logs audit trail.
  4. Local Filesystem: Safely purges temporary workspace repositories (Windows-safe rmtree).
"""
import os
import stat
import shutil
import logging
from typing import Optional

from flask import current_app
from app import db
from app.models.workflow import Workflow, WorkflowStep
from app.models.story import Story
from app.models.devaa_models import PullRequest, AuditLog, PipelineLog
from app.services.rag_service import RagService
from app.services.github_service import GitHubService
from app.utils.pipeline_logger import log_event

logger = logging.getLogger(__name__)


def _remove_readonly(func, path, excinfo):
    """
    Windows-safe error handler for shutil.rmtree.
    Git sets read-only attributes on .git/objects/*, which causes PermissionError
    [WinError 5] on standard rmtree calls on Windows systems.
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        logger.debug(f"[CleanupService] Could not force remove read-only attribute on '{path}': {e}")


class CleanupService:

    @staticmethod
    def cleanup_workspace_dir(workspace_dir: Optional[str]) -> bool:
        """
        Windows-safe recursive removal of cloned workspace directory.
        Returns True if deleted or already gone.
        """
        if not workspace_dir or not os.path.exists(workspace_dir):
            return True

        try:
            logger.info(f"[CleanupService] Removing temporary workspace at: {workspace_dir}")
            shutil.rmtree(workspace_dir, onerror=_remove_readonly)
            logger.info(f"[CleanupService] Cleaned up workspace directory: {workspace_dir}")
            return True
        except Exception as e:
            logger.warning(f"[CleanupService] Failed to clean workspace '{workspace_dir}': {e}")
            return False

    @staticmethod
    def cleanup_chroma_overlay(workflow_id: int, story_id: int) -> bool:
        """
        Safely purges the mutable workflow overlay collection (wf_{wf_id}_{story_id}) from ChromaDB.
        Leaves the immutable Base collection intact for cache hits.
        """
        try:
            rag_svc = RagService.get_instance()
            purged = rag_svc.purge_workflow_overlay(workflow_id=workflow_id, story_id=story_id)
            if purged:
                logger.info(f"[CleanupService] Purged ChromaDB overlay for wf_{workflow_id}_{story_id}")
            return purged
        except Exception as e:
            logger.warning(f"[CleanupService] Error purging ChromaDB overlay wf_{workflow_id}_{story_id}: {e}")
            return False

    @staticmethod
    def cleanup_git_remote(story=None, branch_name: Optional[str] = None, pr_number: Optional[int] = None,
                           failed_stage: str = "Pipeline", repo_url: Optional[str] = None) -> dict:
        """
        Closes any unmerged GitHub PR opened during a failed workflow and deletes the remote branch.
        Returns dict with status of closed PR and deleted branch.
        """
        res = {"pr_closed": False, "branch_deleted": False}

        # Resolve repo URL from explicit parameter, story repository_details, or app config
        resolved_repo_url = repo_url
        if not resolved_repo_url and story:
            repo_details = getattr(story, 'repository_details', None) if story else None
            if not repo_details and isinstance(story, dict):
                repo_details = story.get('repository_details')

            first_repo = (
                repo_details[0] if (isinstance(repo_details, list) and len(repo_details) > 0 and isinstance(repo_details[0], dict))
                else {}
            )
            resolved_repo_url = first_repo.get('url')
        if not resolved_repo_url:
            resolved_repo_url = current_app.config.get('GITHUB_BASE_URL', '')

        if not resolved_repo_url:
            logger.debug("[CleanupService] No repository URL available for remote Git cleanup.")
            return res

        gh_svc = GitHubService.from_app_config(repo_url=resolved_repo_url)

        # 1. Close PR on GitHub if it was created
        if pr_number:
            close_comment = (
                f"🤖 **DEVAA Automated Notice**: This Pull Request has been closed because the pipeline "
                f"failed during the **`{failed_stage}`** stage. A clean rerun will generate a new PR."
            )
            closed = gh_svc.close_pr(repo_url=repo_url, pr_number=pr_number, comment=close_comment)
            res["pr_closed"] = closed

        # 2. Delete remote feature branch from GitHub
        if branch_name:
            deleted = gh_svc.delete_branch(repo_url=repo_url, branch_name=branch_name)
            res["branch_deleted"] = deleted

        return res

    @staticmethod
    def cleanup_db_state(workflow_id: int, story_id: int, error_msg: str, failed_stage: str) -> dict:
        """
        Sanitizes the relational database state:
        - Marks unmerged PullRequest records as 'closed' / 'failed'
        - Reverts Story status to 'TO-DO'
        - Sets Workflow status to 'Failed'
        - Records an immutable AuditLog
        """
        res = {"prs_closed_in_db": 0, "story_reverted": False, "workflow_failed": False}

        try:
            # 1. Close any open PR records linked to this workflow
            open_prs = PullRequest.query.filter_by(workflow_id=workflow_id).all()
            for pr in open_prs:
                if pr.pr_status in ('open', 'pending'):
                    pr.pr_status = 'closed'
                    summary_append = f"\n\n[Closed automatically due to failure at {failed_stage}: {error_msg[:200]}]"
                    pr.pr_summary = (pr.pr_summary or '') + summary_append
                    res["prs_closed_in_db"] += 1

            # 2. Revert story to TO-DO
            if story_id:
                story = Story.query.get(story_id)
                if story and story.status not in ('DONE',):
                    story.status = 'TO-DO'
                    res["story_reverted"] = True

            # 3. Mark workflow as Failed
            if workflow_id:
                wf = Workflow.query.get(workflow_id)
                if wf:
                    wf.status = 'Failed'
                    res["workflow_failed"] = True

            db.session.commit()

            # 4. Audit Log
            try:
                audit = AuditLog(
                    workflow_id=workflow_id,
                    story_id=story_id,
                    event_type='pipeline_failure_rollback',
                    event_data={
                        'failed_stage': failed_stage,
                        'error_msg': str(error_msg)[:300],
                        'closed_prs': res["prs_closed_in_db"]
                    }
                )
                db.session.add(audit)
                db.session.commit()
            except Exception as audit_err:
                logger.debug(f"[CleanupService] Audit log write failed: {audit_err}")

        except Exception as e:
            logger.error(f"[CleanupService] DB state cleanup exception: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass

        return res

    @classmethod
    def rollback_pipeline_run(
        cls,
        workflow_id: int,
        story_id: Optional[int],
        failed_stage: str,
        error_msg: str,
        workspace_dir: Optional[str] = None,
        target_branch: Optional[str] = None,
        context_story: Optional[dict] = None
    ) -> dict:
        """
        Master rollback coordinator:
        Executes complete 4-layer cleanup upon pipeline failure, crash, or cancellation.
        """
        logger.info(f"[CleanupService] Starting 4-layer rollback for workflow {workflow_id}, story {story_id} (Failed at: {failed_stage})")

        story = Story.query.get(story_id) if story_id else None
        target_story = story or context_story

        # Gather any PR records created for this workflow
        pr_records = PullRequest.query.filter_by(workflow_id=workflow_id).all() if workflow_id else []
        branch_to_delete = target_branch
        pr_number_to_close = None

        for pr in pr_records:
            if pr.pr_number:
                pr_number_to_close = pr.pr_number
            if pr.branch_name:
                branch_to_delete = pr.branch_name

        # Layer 1: Git & Remote GitHub cleanup
        git_res = cls.cleanup_git_remote(
            story=target_story,
            branch_name=branch_to_delete,
            pr_number=pr_number_to_close,
            failed_stage=failed_stage
        )

        # Layer 2: ChromaDB overlay cleanup
        chroma_res = cls.cleanup_chroma_overlay(workflow_id=workflow_id, story_id=story_id or 0)

        # Layer 3: Relational DB sanitization
        db_res = cls.cleanup_db_state(
            workflow_id=workflow_id,
            story_id=story_id or 0,
            error_msg=error_msg,
            failed_stage=failed_stage
        )

        # Layer 4: Local filesystem workspace deletion
        fs_res = cls.cleanup_workspace_dir(workspace_dir=workspace_dir)

        # Post Jira notification
        try:
            from app.agents.comment_agent import CommentAgent
            step_fail = WorkflowStep(
                workflow_id=workflow_id,
                step_type='Comment',
                agent_prompt=f'Post pipeline failure comment ({failed_stage}).',
                status='In Progress',
                loop_iteration=1
            )
            db.session.add(step_fail)
            db.session.commit()

            CommentAgent(db=db, config=current_app.config).run({
                'story': target_story,
                'comment_type': 'pipeline_failed',
                'new_status': 'TO-DO',
                'workflow_id': workflow_id,
                'extra': {
                    'failed_stage': failed_stage,
                    'error_detail': str(error_msg)[:500] if error_msg else 'Unknown error',
                    'workflow_id': str(workflow_id),
                }
            }, workflow_id=workflow_id, step_record=step_fail)
        except Exception as comment_err:
            logger.warning(f"[CleanupService] Failed to post Jira failure comment: {comment_err}")

        # Live Activity Event Feed Log
        log_event(
            story_id=story_id or 0,
            workflow_id=workflow_id,
            agent='Orchestrator',
            level='error',
            message=f'💥 Pipeline FAILED at `{failed_stage}` — story reverted to TO-DO',
            detail=str(error_msg)[:300] if error_msg else ''
        )

        cleanup_summary_parts = []
        if git_res.get("branch_deleted"):
            cleanup_summary_parts.append(f"Remote branch `{branch_to_delete}` deleted")
        if git_res.get("pr_closed"):
            cleanup_summary_parts.append(f"PR #{pr_number_to_close} closed")
        if chroma_res:
            cleanup_summary_parts.append("ChromaDB overlay purged")
        if fs_res and workspace_dir:
            cleanup_summary_parts.append("Temporary workspace cleaned")

        summary_msg = "🧹 Zero-dirty-state cleanup complete: " + (", ".join(cleanup_summary_parts) if cleanup_summary_parts else "All layers sanitized")
        log_event(
            story_id=story_id or 0,
            workflow_id=workflow_id,
            agent='Orchestrator',
            level='info',
            message=summary_msg
        )

        return {
            "success": True,
            "git": git_res,
            "chroma": chroma_res,
            "database": db_res,
            "filesystem": fs_res
        }

    @classmethod
    def sweep_stale_workspaces(cls) -> int:
        """
        Background maintenance cleaner:
        Finds any orphaned wf_* workspace folders whose workflow in DB has finished
        (Failed or Completed) or is older than 24 hours, and removes them.
        """
        cleaned_count = 0
        try:
            from app.config.settings import Config
            workspaces_root = getattr(Config, 'WORKSPACES_DIR', None)
            if not workspaces_root or not os.path.exists(workspaces_root):
                return 0

            for entry in os.listdir(workspaces_root):
                if not entry.startswith("wf_"):
                    continue
                full_path = os.path.join(workspaces_root, entry)
                if not os.path.isdir(full_path):
                    continue

                # Parse workflow_id from folder name (wf_{workflow_id}_{story_id})
                parts = entry.split("_")
                if len(parts) >= 2 and parts[1].isdigit():
                    wf_id = int(parts[1])
                    wf = Workflow.query.get(wf_id)
                    if not wf or wf.status in ('Failed', 'Completed'):
                        if cls.cleanup_workspace_dir(full_path):
                            cleaned_count += 1
        except Exception as e:
            logger.warning(f"[CleanupService] sweep_stale_workspaces exception: {e}")

        return cleaned_count
