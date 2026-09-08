import logging
from app import db
from app.models.devaa_models import SuccessMetric

logger = logging.getLogger(__name__)

class MetricsService:
    @staticmethod
    def record_metrics(workflow_id: int, story_id: int, metrics: dict):
        """
        Record workflow success metrics after each workflow completes.
        
        Expected metrics in dict:
        - eligibility_accuracy (numeric)
        - file_selection_correctness (numeric/flag)
        - acceptance_criteria_coverage (numeric/boolean)
        - rework_count (numeric)
        - guardrail_triggered_count (numeric)
        - loop_iterations (numeric)
        
        Args:
            workflow_id (int): ID of the workflow
            story_id (int): ID of the story
            metrics (dict): Dictionary containing metric names and values
        """
        try:
            for metric_name, metric_value in metrics.items():
                new_metric = SuccessMetric(
                    workflow_id=workflow_id,
                    story_id=story_id,
                    metric_name=metric_name,
                    metric_value=metric_value,
                    notes=None
                )
                db.session.add(new_metric)
            
            db.session.commit()
            logger.info(f"Recorded {len(metrics)} success metrics for workflow {workflow_id}, story {story_id}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error recording metrics: {str(e)}")
            return False

    @staticmethod
    def get_metrics_by_workflow(workflow_id: int):
        """
        Retrieve all success metrics for a given workflow.
        """
        return SuccessMetric.query.filter_by(workflow_id=workflow_id).all()
        
    @staticmethod
    def get_metrics_by_story(story_id: int):
        """
        Retrieve all success metrics for a given story.
        """
        return SuccessMetric.query.filter_by(story_id=story_id).all()
