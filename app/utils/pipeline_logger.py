"""
pipeline_logger.py — helper to write real-time pipeline events to the DB.

Usage inside orchestrator / agents:
    from app.utils.pipeline_logger import log_event
    log_event(story_id=1, workflow_id=42, agent='Intake',
              level='info', message='Validating story fields…')
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def log_event(
    story_id: int,
    workflow_id: int = None,
    agent: str = 'Orchestrator',
    level: str = 'info',
    message: str = '',
    detail: str = None,
):
    """
    Write a PipelineLog row.  Silently ignores errors so it never
    breaks the main pipeline execution.
    """
    try:
        from app import db
        from app.models.devaa_models import PipelineLog

        entry = PipelineLog(
            story_id=story_id,
            workflow_id=workflow_id,
            agent=agent,
            level=level,
            message=message,
            detail=detail,
            created_at=datetime.utcnow(),
        )
        db.session.add(entry)
        db.session.commit()
    except Exception as exc:
        logger.warning(f"[pipeline_logger] Failed to write log: {exc}")
