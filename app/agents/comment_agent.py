"""
CommentAgent — Step 6
Posts structured comments to Jira (if configured) and updates story status.
Always persists to DB regardless of Jira connectivity.
"""
from app.agents.base_agent import BaseAgent, AgentResult
from app.services.jira_service import JiraService


from collections import defaultdict


class SafeDict(defaultdict):
    def __missing__(self, key):
        return ""


COMMENT_TEMPLATES = {
    "validation_failure": (
        "🚫 *DEVAA Intake Validation Failed*\n\n"
        "The story could not be processed due to missing or incomplete fields:\n\n"
        "{details}\n\n"
        "Please update the story on Jira/DEVAA dashboard and trigger a new run."
    ),
    "ready_for_dev": (
        "🚀 *DEVAA: Story Validated — Development Pipeline Initiated*\n\n"
        "All mandatory fields and acceptance criteria have been verified.\n\n"
        "{repositories_info}"
        "• **Target Feature Branch:** `{branch_name}`\n"
        "• **Pipeline Status:** `{new_status}`\n"
        "• **Execution Path:** Intake Passed ➔ Repo Analysis ➔ Implementation ➔ Self-Validation ➔ PR Creation\n\n"
        "**Acceptance Criteria In Scope:**\n"
        "{acceptance_criteria}\n\n"
        "Developer Agent is now implementing changes to satisfy all acceptance criteria."
    ),
    "pr_ready": (
        "🔀 *DEVAA: Pull Request Ready for QA Review*\n\n"
        "{prs_info}\n\n"
        "**Changed Files Summary:**\n{changed_files}\n\n"
        "**Summary:** {pr_summary}\n\n"
        "Please review and approve/reject on the DEVAA QA dashboard."
    ),
    "rework_triggered": (
        "🔁 *DEVAA: Rework Cycle Initiated*\n\n"
        "QA Reviewer rejected the previous implementation with the following feedback:\n\n"
        "{qa_feedback}\n\n"
        "DEVAA is re-running the automated development pipeline addressing all of the above comments. "
        "A fresh Pull Request will be created upon validation."
    ),
    "task_done_pr_raised": (
        "🚀 *DEVAA: Task Complete — Pull Request(s) Raised*\n\n"
        "{prs_info}\n\n"
        "📄 **Evidence Report:** `{evidence_filename}` has been generated and archived in `backend/evidence/` (available on DEVAA PO / QA Dashboard for audit & download).\n\n"
        "**Developer Implementation Summary:**\n"
        "{pr_summary}\n\n"
        "**Status:** Story moved to QA-TESTING. Human QA review required."
    ),
    "pipeline_failed": (
        "⚠️ *DEVAA: Pipeline Failed*\n\n"
        "The automated development pipeline encountered an error and could not complete.\n\n"
        "• **Failed At:** `{failed_stage}`\n"
        "• **Error:** {error_detail}\n"
        "• **Workflow ID:** #{workflow_id}\n\n"
        "Story has been moved back to TO-DO with an automated clean rollback.\n"
        "Check DEVAA Live Pipeline Logs for detailed diagnostics."
    ),
    "done": (
        "🎉 *DEVAA: Story Complete & Merged*\n\n"
        "All Pull Requests have been approved and successfully merged into target base branch(es). This story is now DONE.\n\n"
        "{merged_prs_info}"
    ),
    "general": (
        "{body}"
    )
}


class CommentAgent(BaseAgent):
    agent_name = "Comment"

    def _execute(self, context: dict) -> AgentResult:
        from app.models.story import Story
        from app.models.devaa_models import AuditLog

        story = context.get('story')
        comment_type = context.get('comment_type', 'ready_for_dev')
        new_status = context.get('new_status')
        extra = dict(context.get('extra', {}))
        workflow_id = context.get('workflow_id')

        if not story:
            return AgentResult(success=False, error="No story provided to CommentAgent.")

        # ── Compute dynamic helpers for multi-repo and multi-PR ───
        pr_url = extra.get('pr_url', '')
        branch_name = extra.get('branch_name', '')
        repo_name = extra.get('repo_name', 'Primary Repository')
        secondary_pr_url = extra.get('secondary_pr_url')
        secondary_branch = extra.get('secondary_branch_name') or branch_name
        secondary_repo_name = extra.get('secondary_repo_name', 'Secondary Repository')

        if 'prs_info' not in extra:
            if secondary_pr_url and secondary_pr_url != 'skipped':
                extra['prs_info'] = (
                    "**📦 Target Repositories & Pull Requests:**\n"
                    f"• **`{repo_name}` (Primary):** {pr_url} (Branch: `{branch_name}`)\n"
                    f"• **`{secondary_repo_name}` (Secondary):** {secondary_pr_url} (Branch: `{secondary_branch}`)"
                )
            elif pr_url and pr_url != '#':
                extra['prs_info'] = (
                    "**📦 Target Repository & Pull Request:**\n"
                    f"• **`{repo_name}`:** {pr_url} (Branch: `{branch_name}`)"
                )
            else:
                extra['prs_info'] = f"**Branch:** `{branch_name}`"

        if 'merged_prs_info' not in extra:
            if secondary_pr_url and secondary_pr_url != 'skipped':
                extra['merged_prs_info'] = (
                    f"• **`{repo_name}`:** {pr_url}\n"
                    f"• **`{secondary_repo_name}`:** {secondary_pr_url}"
                )
            elif pr_url:
                extra['merged_prs_info'] = f"• **Merged PR:** {pr_url}"
            else:
                extra['merged_prs_info'] = "Pull Requests merged."

        if 'repositories_info' not in extra and extra.get('target_repos'):
            repos_list = extra.get('target_repos')
            if isinstance(repos_list, list):
                repo_strs = [f"`{r.get('name')}` (Base: `{r.get('branch', 'main')}`)" for r in repos_list if isinstance(r, dict)]
                extra['repositories_info'] = f"• **Target Repositories:** {', '.join(repo_strs)}\n" if repo_strs else ""

        # ── Build comment body ────────────────────────────────────
        template = COMMENT_TEMPLATES.get(comment_type, COMMENT_TEMPLATES['ready_for_dev'])
        try:
            safe_extra = SafeDict(str, **extra)
            comment_body = template.format_map(safe_extra)
        except Exception as fmt_err:
            self.logger.warning(f"Error formatting template {comment_type}: {fmt_err}")
            comment_body = template

        # ── Try Jira comment ──────────────────────────────────────
        jira_comment_id = None
        jira_key = story.jira_story_key if hasattr(story, 'jira_story_key') else story.get('jira_story_key')
        if jira_key:
            try:
                jira_comment_id = JiraService.add_comment(jira_key, comment_body)
            except Exception as e:
                self.logger.error(f"Failed to post Jira comment: {e}")

        # ── Update Jira status ────────────────────────────────────
        jira_transitioned = False
        if new_status and jira_key:
            try:
                jira_transitioned = JiraService.transition_issue(jira_key, new_status)
            except Exception as e:
                self.logger.error(f"Failed to transition Jira issue: {e}")

        # ── Update story status in DB ─────────────────────────────
        story_updated = False
        story_id = story.id if hasattr(story, 'id') else story.get('id')
        story_model = story if isinstance(story, Story) else (Story.query.get(story_id) if story_id else None)
        if new_status and story_model:
            story_model.status = new_status
            if jira_comment_id:
                story_model.jira_comment_id = jira_comment_id
            try:
                self.db.session.commit()
                story_updated = True
            except Exception as e:
                self.logger.error(f"Failed to update story status: {e}")

        # ── Write to jira_comments table (auto-create if missing) ──
        try:
            from sqlalchemy import text
            self.db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS jira_comments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    story_id INT NULL,
                    jira_story_key VARCHAR(50) NULL,
                    comment_body TEXT NULL,
                    comment_type VARCHAR(50) NULL,
                    jira_comment_id VARCHAR(50) NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            self.db.session.execute(
                text("INSERT INTO jira_comments (story_id, jira_story_key, comment_body, comment_type, jira_comment_id) "
                     "VALUES (:sid, :jkey, :body, :ctype, :jcid)"),
                {
                    "sid": story_id,
                    "jkey": jira_key,
                    "body": comment_body,
                    "ctype": comment_type,
                    "jcid": jira_comment_id
                }
            )
            self.db.session.commit()
        except Exception as e:
            self.logger.warning(f"jira_comments record: {e}")

        # ── Audit log ─────────────────────────────────────────────
        try:
            audit = AuditLog(
                workflow_id=workflow_id,
                story_id=story.id if hasattr(story, 'id') else story.get('id'),
                event_type=f"comment_agent_{comment_type}",
                event_data={
                    "comment_type": comment_type,
                    "new_status": new_status,
                    "jira_transitioned": jira_transitioned,
                    "jira_comment_id": jira_comment_id
                }
            )
            self.db.session.add(audit)
            self.db.session.commit()
        except Exception as e:
            self.logger.warning(f"Audit log failed: {e}")

        return AgentResult(
            success=True,
            output={
                "comment_type": comment_type,
                "comment_body": comment_body,
                "jira_comment_id": jira_comment_id,
                "new_status": new_status,
                "story_updated": story_updated,
                "jira_transitioned": jira_transitioned
            }
        )
