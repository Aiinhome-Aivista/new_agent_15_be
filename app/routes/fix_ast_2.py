import os

def fix_file(path, replacements):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    for old, new in replacements.items():
        content = content.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

fix_file(r"d:\New Agent\new_agent_15\backend\app\routes\pull_requests.py", {
    "return api_response(409, False, f\"PR is already '{pr.pr_status)": "return api_response(409, False, f\"PR is already '{pr.pr_status}'\")"
})

fix_file(r"d:\New Agent\new_agent_15\backend\app\routes\qa.py", {
    "return api_response(409, False, f\"Merge failed on GitHub: {err_msg)": "return api_response(409, False, f\"Merge failed on GitHub: {err_msg}\")"
})

fix_file(r"d:\New Agent\new_agent_15\backend\app\routes\stories.py", {
    "return api_response(409, False, f\"Story is already in '{story.status)": "return api_response(409, False, f\"Story is already in '{story.status}' state. Cannot trigger a new run.\")",
    "return api_response(409, False, f\"Story is already in '{story.status}' state. Cannot trigger a new run.\")\n\n' state. Cannot trigger a new run.\")": "return api_response(409, False, f\"Story is already in '{story.status}' state. Cannot trigger a new run.\")"
})
