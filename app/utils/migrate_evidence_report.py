"""
Migration: Add evidence_report column to pull_requests table
Run once with: python app/utils/migrate_evidence_report.py
"""
import os
import sys

# Add parent dir to path for Flask app context
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import create_app, db

app = create_app()

with app.app_context():
    try:
        db.engine.execute("ALTER TABLE pull_requests ADD COLUMN evidence_report JSON NULL")
        print("Migration successful: evidence_report column added to pull_requests.")
    except Exception as e:
        if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
            print("Column evidence_report already exists — migration skipped.")
        else:
            print(f"Migration error: {e}")
