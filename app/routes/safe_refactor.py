import os
import re

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        original_content = f.read()

    content = original_content

    # Add import
    if "from app.utils.responses import api_response" not in content and "jsonify" in content:
        content = re.sub(r'from flask import (.*?)jsonify(.*?)', r'from flask import \1jsonify\2\nfrom app.utils.responses import api_response', content)

    # We want to replace `return api_response(200, True, "Success", <something>), <code>`
    # with `return api_response(<code>, is_success, message, <something>)`

    def repl(m):
        inner = m.group(1).strip()
        code = m.group(2).strip()
        code_int = int(code)
        
        # Determine success and message based on status code and content
        is_success = "True" if code_int < 400 else "False"
        
        # If it's an error dict like {"error": "..."}
        if is_success == "False":
            # Extract error message if it's a simple dict
            err_match = re.search(r'\{\s*"error"\s*:\s*(.*?)\s*\}', inner)
            if err_match:
                msg = err_match.group(1)
                return f'return api_response({code}, {is_success}, {msg})'
            return f'return api_response({code}, {is_success}, "Error", {inner})'
        else:
            msg = '"Success"'
            if code_int == 201:
                msg = '"Created"'
            return f'return api_response({code}, {is_success}, {msg}, {inner})'

    # Match `return jsonify( ...)`
    # We use a balanced parentheses regex approach or just simple greedy since `), \d\d\d` is usually unique
    # We'll use a safer regex:
    # `return api_response(123, True, "Success", ` followed by anything up to `)`
    
    # regex for single line:
    content = re.sub(r'return\s+jsonify\((.*?)\),\s*(\d{3})', repl, content, flags=re.DOTALL)

    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Refactored {filepath}")

directory = r"d:\New Agent\new_agent_15\backend\app\routes"
for filename in os.listdir(directory):
    if filename.endswith(".py"):
        process_file(os.path.join(directory, filename))
