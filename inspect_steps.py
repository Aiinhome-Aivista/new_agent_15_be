import os, sys
sys.path.insert(0, r"C:\Users\Himanshu\AppData\Roaming\Python\Python312\site-packages")
from app import create_app, db
from app.models.workflow import WorkflowStep, Workflow

app = create_app()
with app.app_context():
    wf = Workflow.query.order_by(Workflow.id.desc()).first()
    if wf:
        print(f"Latest Workflow #{wf.id}: status={wf.status}, current_agent={wf.current_agent}")
    steps = WorkflowStep.query.filter_by(workflow_id=wf.id if wf else 1).order_by(WorkflowStep.id.asc()).all()
    for s in steps:
        print(f"\n=== Step #{s.id} | {s.step_type} | iter={s.loop_iteration} | status={s.status} ===")
        out = str(s.agent_output)
        if len(out) > 600:
            print(out[:600] + "... [truncated]")
        else:
            print(out)
