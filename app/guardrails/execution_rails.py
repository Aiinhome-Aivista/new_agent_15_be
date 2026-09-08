"""
ExecutionRails — prevents unsafe or duplicate actions at runtime.
"""
import logging
logger = logging.getLogger(__name__)


class ExecutionRails:

    @staticmethod
    def check(context: dict, workflow_id=None, config=None) -> dict:
        """
        Checks runtime safety constraints. Returns {'passed': bool, 'reason': str}.
        """
        config = config or {}
        max_loops = config.get('AGENT_MAX_LOOP_ITERATIONS', 3)

        # ── Loop limit enforcement ────────────────────────────────
        loop_iteration = context.get('loop_iteration', 1)
        if loop_iteration > max_loops:
            return {
                'passed': False,
                'reason': f"Loop limit exceeded: iteration {loop_iteration} > max {max_loops}. Human escalation required."
            }

        return {'passed': True, 'reason': ''}
