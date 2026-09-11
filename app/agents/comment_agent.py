"""
CommentAgent — Step 6
Posts structured comments to Jira (if configured) and updates story status.
Always persists to DB regardless of Jira connectivity.
"""
from app.agents.base_agent import BaseAgent, AgentResult
from app.services.jira_service import JiraService


COMMENT_TEMPLATES = {
    "validation_failure": (
        "🚫 *DEVAA Intake Validation Failed*\n\n"
        "The story could not be processed due to missing or incomplete fields:\n\n"
        "{details}\n\n"
        "Please update the story and trigger a new run."
    ),
    "ready_for_dev": (
        "🚀 *DEVAA: Story Validated — Starting Development*\n\n"
        "All mandatory fields and acceptance criteria have been verified. The automated development pipeline has been initiated.\n\n"
        "• **Target Branch:** `{branch_name}` (Base: `{base_branch}`)\n"
        "• **Status:** `{new_status}`\n"
        "• **Pipeline:** Intake Passed ➔ Repository Analysis ➔ Implementation ➔ Self-Validation ➔ PR Creation\n\n"
        "**Acceptance Criteria In Scope:**\n"
        "{acceptance_criteria}\n\n"
        "Developer Agent is now implementing changes to satisfy all acceptance criteria."
    ),
    "pr_ready": (
        "🔀 *DEVAA: Pull Request Ready for QA Review*\n\n"
        "**PR:** {pr_url}\n"
        "**Branch:** `{branch_name}`\n\n"
        "**Changed Files:**\n{changed_files}\n\n"
        "**Summary:** {pr_summary}\n\n"
        "Please review and approve/reject on the QA dashboard."
    ),
    "rework_triggered": (
        "🔁 *DEVAA: Rework Cycle Initiated*\n\n"
        "QA rejected the previous implementation with the following feedback:\n\n"
        "{qa_feedback}\n\n"
        "DEVAA is re-running the development pipeline addressing the above comments. "
        "A new PR will be created upon completion."
    ),
    "task_done_pr_raised": (
        "🚀 *DEVAA: Task Done — PR Raised*\n\n"
        "**PR:** {pr_url}\n"
        "**Branch:** `{branch_name}`\n\n"
        "📎 Evidence report `{evidence_filename}` attached to this issue.\n\n"
        "**Summary:** {pr_summary}\n\n"
        "Story moved to QA-TESTING. Human review required."
    ),
    "done": (
        "🎉 *DEVAA: Story Complete*\n\n"
        "The PR has been approved and merged. This story is now DONE.\n\n"
        "**Merged PR:** {pr_url}"
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
        extra = context.get('extra', {})
        workflow_id = context.get('workflow_id')

        if not story:
            return AgentResult(success=False, error="No story provided to CommentAgent.")

        # ── Build comment body ────────────────────────────────────
        template = COMMENT_TEMPLATES.get(comment_type, COMMENT_TEMPLATES['ready_for_dev'])
        try:
            comment_body = template.format(**extra)
        except KeyError:
            comment_body = template  # use as-is if format keys missing

        # ── Try Jira comment ──────────────────────────────────────
        jira_comment_id = None
        jira_key = story.jira_story_key if hasattr(story, 'jira_story_key') else story.get('jira_story_key')
        if jira_key:
            jira_comment_id = JiraService.add_comment(jira_key, comment_body)

        # ── Update Jira status ────────────────────────────────────
        jira_transitioned = False
        if new_status and jira_key:
            jira_transitioned = JiraService.transition_issue(jira_key, new_status)

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
