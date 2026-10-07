from app import create_app, db
from sqlalchemy import text

app = create_app()
with app.app_context():
    db.session.execute(text("UPDATE stories SET status='IN-PROGRESS' WHERE status='In Progress'"))
    db.session.commit()
    print("Fixed stuck stories")
