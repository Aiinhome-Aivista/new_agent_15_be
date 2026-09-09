import requests
import json
from requests.auth import HTTPBasicAuth
from app import create_app

app = create_app()

with app.app_context():
    email = app.config['JIRA_EMAIL']
    token = app.config['JIRA_API_TOKEN']
    base_url = app.config['JIRA_BASE_URL']
    auth = HTTPBasicAuth(email, token)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    
    url3 = f"{base_url.rstrip('/')}/rest/api/3/search/jql"
    
    payload = {
        "jql": 'status = "To Do" ORDER BY created DESC',
        "fields": ["summary", "description", "status", "priority", "assignee", "key"]
    }
    
    resp = requests.post(url3, auth=auth, json=payload, headers=headers)
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print("Issue count:", len(data.get('issues', [])))
        for issue in data.get('issues', []):
            print("Issue object:", json.dumps(issue, indent=2))
    else:
        print("Error:", resp.text)
