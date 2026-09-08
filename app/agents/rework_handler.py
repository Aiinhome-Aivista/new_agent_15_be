"""
ReworkHandler — Step 8
Reads QA rejection comments, filters out agent-generated comments,
and prepares context to restart the orchestration from Step 2.
"""
from app.agents.base_agent import BaseAgent, AgentResult


class ReworkHandler(BaseAgent):
    agent_name = "ReworkHandler"

    def _execute(self, context: dict) -> AgentResult:
        from app.models.devaa_models import QAReview

        story = context.get('story')
        workflow_id = context.get('workflow_id')

        if not story:
            return AgentResult(success=False, error="No story provided to ReworkHandler.")

        story_id = story.id if hasattr(story, 'id') else story.get('id')

        # ── Fetch QA rejections for this story ───────────────────
        # is_rework=True means these are genuine human QA rejections
        qa_rejections = QAReview.query.filter_by(
            story_id=story_id,
            decision='rejected',
            is_rework=True
        ).order_by(QAReview.created_at.desc()).all()

        if not qa_rejections:
            return AgentResult(
                success=False,
                error="No QA rejection comments found for rework."
            )

        # ── Aggregate all unique QA comments ─────────────────────
        seen = set()
        aggregated_feedback = []
        for review in qa_rejections:
            comment = (review.comments or '').strip()
            if comment and comment not in seen:
                seen.add(comment)
                aggregated_feedback.append(f"• {comment}")

        combined_feedback = "\n".join(aggregated_feedback)
        rework_count = len(qa_rejections)

        return AgentResult(
            success=True,
            output={
                "qa_feedback": combined_feedback,
                "rework_count": rework_count,
                "restart_from": "RepoAnalysis",
                "restart_context": {
                    "story": story,
                    "workflow_id": workflow_id,
                    "qa_feedback": combined_feedback,
                    "loop_iteration": rework_count + 1
                }
            }
        )
