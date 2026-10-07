import os
import re

directory = r"d:\New Agent\new_agent_15\backend\app\routes"

def refactor_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Add api_response import if not present
    if "api_response" not in content:
        content = content.replace("from flask import Blueprint,", "from flask import Blueprint,\nfrom app.utils.responses import api_response\n")

    # This is a naive regex for standard cases: `return api_response(200, True, "Success", {...})`
    # Replace single line `return api_response(200, True, "Success", {...})` -> `return api_response(200, True, "Success", {...})`
    # We will do this carefully for different status codes
    
    # 200/201 OK
    content = re.sub(r'return\s+jsonify\((.*?)\),\s*200', r'return api_response(200, True, "Success", \1)', content, flags=re.DOTALL)
    content = re.sub(r'return\s+jsonify\((.*?)\),\s*201', r'return api_response(201, True, "Created", \1)', content, flags=re.DOTALL)
    
    # 400 Bad Request
    content = re.sub(r'return\s+jsonify\(\s*{\s*"error"\s*:\s*(.*?)\s*}\s*\),\s*400', r'return api_response(400, False, \1)', content, flags=re.DOTALL)
    # 401 Unauthorized
    content = re.sub(r'return\s+jsonify\(\s*{\s*"error"\s*:\s*(.*?)\s*}\s*\),\s*401', r'return api_response(401, False, \1)', content, flags=re.DOTALL)
    # 403 Forbidden
    content = re.sub(r'return\s+jsonify\(\s*{\s*"error"\s*:\s*(.*?)\s*}\s*\),\s*403', r'return api_response(403, False, \1)', content, flags=re.DOTALL)
    # 404 Not Found
    content = re.sub(r'return\s+jsonify\(\s*{\s*"error"\s*:\s*(.*?)\s*}\s*\),\s*404', r'return api_response(404, False, \1)', content, flags=re.DOTALL)
    # 409 Conflict
    content = re.sub(r'return\s+jsonify\(\s*{\s*"error"\s*:\s*(.*?)\s*}\s*\),\s*409', r'return api_response(409, False, \1)', content, flags=re.DOTALL)
    # 500 Server Error
    content = re.sub(r'return\s+jsonify\(\s*{\s*"error"\s*:\s*(.*?)\s*}\s*\),\s*500', r'return api_response(500, False, \1)', content, flags=re.DOTALL)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

for filename in os.listdir(directory):
    if filename.endswith(".py"):
        refactor_file(os.path.join(directory, filename))
        print(f"Refactored {filename}")
