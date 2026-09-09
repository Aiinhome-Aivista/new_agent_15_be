"""
BaseAgent — Abstract base class for all DEVAA specialist agents.
Every agent logs its run to DB and applies guardrails before execution.
"""
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    success: bool
    output: dict = field(default_factory=dict)
    error: str = None
    token_count: int = 0
    cost_usd: float = 0.0
    guardrail_triggered: bool = False
    guardrail_reason: str = None


class BaseAgent(ABC):
    """All DEVAA agents extend this class."""

    agent_name: str = "BaseAgent"

    def __init__(self, db, config):
        self.db = db
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def _execute(self, context: dict) -> AgentResult:
        """Core agent logic — implemented by each specialist agent."""
        pass

    def run(self, context: dict, workflow_id: int = None, step_record=None) -> AgentResult:
        """
        Public entry point. Applies guardrails, calls _execute, persists results.
        context: dict containing all inputs the agent needs.
        step_record: a WorkflowStep ORM instance (already created by orchestrator).
        """
        from app.guardrails.input_rails import InputRails
        from app.guardrails.execution_rails import ExecutionRails

        self.logger.info(f"[{self.agent_name}] Starting. Context keys: {list(context.keys())}")
        
        # Inject workflow_id into context if not present, useful for metrics
        if workflow_id is not None and 'workflow_id' not in context:
            context['workflow_id'] = workflow_id

        # ── Input Guardrails ──────────────────────────────────────
        rail_result = InputRails.validate(context, workflow_id=workflow_id, config=self.config)
        if not rail_result['passed']:
            self.logger.warning(f"[{self.agent_name}] Input rail blocked: {rail_result['reason']}")
            self._log_guardrail_event(
                workflow_id=workflow_id,
                rail_type='input',
                action_blocked=self.agent_name,
                reason=rail_result['reason']
            )
            result = AgentResult(
                success=False,
                error=f"Input guardrail blocked: {rail_result['reason']}",
                guardrail_triggered=True,
                guardrail_reason=rail_result['reason']
            )
            self._update_step(step_record, result)
            return result

        # ── Execution Guardrails ──────────────────────────────────
        exec_result = ExecutionRails.check(context, workflow_id=workflow_id, config=self.config)
        if not exec_result['passed']:
            self.logger.warning(f"[{self.agent_name}] Execution rail blocked: {exec_result['reason']}")
            self._log_guardrail_event(
                workflow_id=workflow_id,
                rail_type='execution',
                action_blocked=self.agent_name,
                reason=exec_result['reason']
            )
            result = AgentResult(
                success=False,
                error=f"Execution guardrail blocked: {exec_result['reason']}",
                guardrail_triggered=True,
                guardrail_reason=exec_result['reason']
            )
            self._update_step(step_record, result)
            return result

        # ── Core Execution ────────────────────────────────────────
        try:
            result = self._execute(context)
        except Exception as e:
            self.logger.exception(f"[{self.agent_name}] Unhandled exception: {e}")
            result = AgentResult(success=False, error=str(e))

        self._update_step(step_record, result)
        self.logger.info(f"[{self.agent_name}] Done. Success={result.success}")
        return result

    # ── Helpers ────────────────────────────────────────────────────

    def _update_step(self, step_record, result: AgentResult):
        """Persist result into the WorkflowStep record."""
        if step_record is None:
            return
        import json
        try:
            step_record.agent_response = json.dumps(result.output) if result.output else result.error
            step_record.status = 'Completed' if result.success else 'Failed'
            step_record.token_count = result.token_count
            step_record.cost_usd = result.cost_usd
            step_record.guardrail_triggered = result.guardrail_triggered
            step_record.guardrail_reason = result.guardrail_reason
            self.db.session.commit()
        except Exception as e:
            self.logger.error(f"Failed to update step record: {e}")

    def _log_guardrail_event(self, workflow_id, rail_type, action_blocked, reason):
        """Write a guardrail event to DB."""
        try:
            from app.models.devaa_models import GuardrailEvent
            event = GuardrailEvent(
                workflow_id=workflow_id,
                rail_type=rail_type,
                action_blocked=action_blocked,
                reason=reason
            )
            self.db.session.add(event)
            self.db.session.commit()
        except Exception as e:
            self.logger.error(f"Failed to log guardrail event: {e}")
