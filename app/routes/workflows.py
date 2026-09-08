from flask import Blueprint, request, jsonify
from app.models.workflow import Workflow, WorkflowStep
from app.models.user import User
from app.utils.auth import require_auth, require_role
from app.services.llm_service import LLMService
from app import db
import logging

workflows_bp = Blueprint('workflows', __name__)
logger = logging.getLogger(__name__)

@workflows_bp.route('/', methods=['GET'])
@require_auth
def get_workflows():
    # Example logic: Product Owners see their own, Engineering Leads see all active.
    user = request.current_user
    workflows = Workflow.query.all() # Keeping it simple for demo purposes
    
    return jsonify([{
        "id": w.id,
        "title": w.title,
        "status": w.status,
        "owner_id": w.owner_id,
        "created_at": w.created_at
    } for w in workflows]), 200

@workflows_bp.route('/initiate', methods=['POST'])
@require_auth
@require_role(['Product Owner', 'Admin'])
def initiate_workflow():
    data = request.get_json()
    title = data.get('title')
    requirements = data.get('requirements')
    provider_override = data.get('provider') # Optional
    
    if not title or not requirements:
        return jsonify({"error": "Title and requirements are required."}), 400

    # 1. Persist the new workflow state
    workflow = Workflow(
        title=title,
        requirements_doc=requirements,
        owner_id=request.current_user.id,
        status='Planning'
    )
    db.session.add(workflow)
    db.session.commit()

    # 2. Persist the first step (Architecture Planning)
    system_instruction = "You are an expert Software Architect. Analyze these requirements and output a detailed architecture plan."
    
    step = WorkflowStep(
        workflow_id=workflow.id,
        step_type='Architecture',
        agent_prompt=f"Requirements:\n{requirements}",
        status='In Progress'
    )
    db.session.add(step)
    db.session.commit()

    # 3. Call the LLM Service (Synchronously for now, should be async in production via Celery)
    try:
        response_text = LLMService.generate_response(
            prompt=step.agent_prompt,
            system_instruction=system_instruction,
            provider_override=provider_override
        )
        
        # 4. Update state upon success
        step.agent_response = response_text
        step.status = 'Completed'
        db.session.commit()
        
    except Exception as e:
        logger.error(f"LLM Generation failed: {e}")
        step.agent_response = str(e)
        step.status = 'Failed'
        db.session.commit()
        return jsonify({"error": "Failed to generate architecture plan.", "details": str(e)}), 500

    return jsonify({
        "message": "Workflow initiated successfully.",
        "workflow_id": workflow.id,
        "step_id": step.id,
        "architecture_plan": step.agent_response
    }), 201

@workflows_bp.route('/<int:workflow_id>', methods=['GET'])
@require_auth
def get_workflow_details(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    steps = WorkflowStep.query.filter_by(workflow_id=workflow.id).order_by(WorkflowStep.created_at.asc()).all()
    
    return jsonify({
        "id": workflow.id,
        "title": workflow.title,
        "status": workflow.status,
        "requirements": workflow.requirements_doc,
        "owner": User.query.get(workflow.owner_id).name,
        "steps": [{
            "id": s.id,
            "type": s.step_type,
            "status": s.status,
            "response": s.agent_response,
            "created_at": s.created_at
        } for s in steps]
    }), 200
