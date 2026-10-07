import os
import re

directory = r"d:\New Agent\new_agent_15\backend\app\routes"

for filename in ["auth.py", "connectors.py", "pull_requests.py", "qa.py", "stories.py", "admin.py"]:
    filepath = os.path.join(directory, filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Fix imports
    content = content.replace("from flask import Blueprint,\nfrom app.utils.responses import api_response", "from flask import Blueprint, request, jsonify\nfrom app.utils.responses import api_response")
    
    # Fix broken api_response calls with trailing '}), 409'
    content = re.sub(r'return api_response\((.*?)\}\),\s*\d+', r'return api_response(\1)', content)
    
    # Let's fix specific lines based on error logs
    # return api_response(400, False, f"PR is already '{pr.pr_status}'"}), 409
    content = re.sub(r'return api_response\((.*?)\)"\}\),\s*\d+', r'return api_response(\1)")', content)
    
    # Let's manually replace any stray "}), 40x" if we just screwed up the regex
    content = content.replace('return api_response(409, False, f"PR is already \'{pr.pr_status}\'"}), 409', 'return api_response(409, False, f"PR is already \'{pr.pr_status}\'")')
    content = content.replace('return api_response(500, False, f"Failed to delete story {story_id} from database."}), 500', 'return api_response(500, False, f"Failed to delete story {story_id} from database.")')

    # Since there are multiline `}), 409` left, it's because my regex replaced the first line of a multi-line jsonify, leaving `}), 409` below it.
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
