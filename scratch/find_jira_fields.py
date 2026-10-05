import requests
from app import create_app
from flask import current_app
import json

app = create_app()
with app.app_context():
    auth = (current_app.config['JIRA_EMAIL'], current_app.config['JIRA_API_TOKEN'])
    url = f"{current_app.config['JIRA_BASE_URL'].rstrip('/')}/rest/api/3/issue/SCRUM-25"
    r = requests.get(url, auth=auth)
    
    fields = r.json().get('fields', {})
    start_date_fields = {k: v for k, v in fields.items() if str(v).startswith('2026-10-05')}
    print("Start Date fields:", start_date_fields)
