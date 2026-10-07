import os
import re

directory = r"d:\New Agent\new_agent_15\backend\app\routes"

for filename in os.listdir(directory):
    if filename.endswith(".py"):
        filepath = os.path.join(directory, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Add import if missing and if we are replacing jsonify
        if "from app.utils.responses import api_response" not in content and "jsonify" in content:
            content = content.replace("from flask import Blueprint, request, jsonify", "from flask import Blueprint, request, jsonify\nfrom app.utils.responses import api_response")
            content = content.replace("from flask import Blueprint, jsonify", "from flask import Blueprint, jsonify\nfrom app.utils.responses import api_response")
            content = content.replace("from flask import Blueprint, request, jsonify, current_app", "from flask import Blueprint, request, jsonify, current_app\nfrom app.utils.responses import api_response")
            
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
