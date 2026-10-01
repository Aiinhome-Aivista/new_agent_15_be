"""
ClarificationAgent — triggered by Orchestrator when RepoAnalysisAgent
returns needs_clarification=True.

Responsibilities:
1. Post the question + options as a Jira comment on the story ticket.
2. Save the jira_comment_id back to the StoryClarification record.
3. Trigger an email notification to the PO / Reporter.
4. Return so the Orchestrator can set workflow status = 'needs_clarification'.

Resolution paths (handled elsewhere):
  - Dashboard: POST /api/stories/<id>/clarification/answer   → sets answer, resumes pipeline
  - Jira webhook: picks up PO reply comment, same resolution logic
"""
import logging
from app.agents.base_agent import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class ClarificationAgent(BaseAgent):
    agent_name = "Clarification"

    def _execute(self, context: dict) -> AgentResult:
        from app import db
        from app.models.devaa_models import StoryClarification
        from app.services.jira_service import JiraService
        from app.services.email_service import EmailService

        story = context.get('story')
        workflow_id = context.get('workflow_id')
        question = context.get('clarification_question', 'DEVAA needs clarification before proceeding.')
        options = context.get('clarification_options') or []

        if not story:
            return AgentResult(success=False, error="No story provided to ClarificationAgent.")

        story_id = story.id if hasattr(story, 'id') else (story.get('id') if isinstance(story, dict) else None)
        jira_key = story.jira_story_key if hasattr(story, 'jira_story_key') else (story.get('jira_story_key') if isinstance(story, dict) else None)
        title = story.title if hasattr(story, 'title') else (story.get('title', 'Story') if isinstance(story, dict) else 'Story')

        # ── 1. Build Jira comment body ──────────────────────────────
        option_lines = "\n".join([f"  *[{chr(65+i)}]* {opt}" for i, opt in enumerate(options)]) if options else ""
        if option_lines:
            option_block = f"\n\n*Options:*\n{option_lines}\n\n_Reply to this comment with the letter (A, B, C...) or type your own answer._"
        else:
            option_block = "\n\n_Reply to this comment with your answer so DEVAA can continue._"

        jira_comment_body = (
            f"🤖 *DEVAA Clarification Request*\n\n"
            f"DEVAA has paused pipeline execution for story *{jira_key or title}* "
            f"and needs your guidance before proceeding with development:\n\n"
            f"*Question:* {question}"
            f"{option_block}\n\n"
            f"You can also answer directly from the DEVAA PO Dashboard."
        )

        jira_comment_id = None

        # ── 2. Post to Jira ─────────────────────────────────────────
        if jira_key:
            try:
                result = JiraService.add_comment(jira_key, jira_comment_body)
                jira_comment_id = str(result) if result else None
                self.logger.info(f"[ClarificationAgent] Posted clarification to Jira {jira_key}, comment id: {jira_comment_id}")
            except Exception as je:
                self.logger.warning(f"[ClarificationAgent] Jira comment failed (non-fatal): {je}")

        # ── 3. Update StoryClarification record with jira_comment_id ─
        if story_id:
            try:
                clarif = StoryClarification.query.filter_by(
                    story_id=story_id, workflow_id=workflow_id, status='pending'
                ).order_by(StoryClarification.created_at.desc()).first()

                if clarif:
                    clarif.jira_comment_id = jira_comment_id
                    db.session.commit()
                else:
                    # Create fresh record if missing
                    clarif = StoryClarification(
                        story_id=story_id,
                        workflow_id=workflow_id,
                        question=question,
                        options=options or None,
                        jira_comment_id=jira_comment_id,
                        status='pending'
                    )
                    db.session.add(clarif)
                    db.session.commit()
            except Exception as de:
                self.logger.warning(f"[ClarificationAgent] DB update failed: {de}")

        # ── 4. Email notification ────────────────────────────────────
        try:
            EmailService.send_event(
                event_type='clarification_requested',
                story=story,
                context_data={
                    'question': question,
                    'options': options,
                    'jira_key': jira_key,
                    'workflow_id': workflow_id
                }
            )
        except Exception as ee:
            self.logger.warning(f"[ClarificationAgent] Email notification failed (non-fatal): {ee}")

        self.logger.info(f"[ClarificationAgent] Clarification request dispatched for story {story_id}.")
        return AgentResult(
            success=True,
            output={
                "clarification_posted": True,
                "jira_comment_id": jira_comment_id,
                "question": question,
                "options": options
            }
        )
