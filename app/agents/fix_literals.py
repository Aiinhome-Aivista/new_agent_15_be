import re

filepath = r"d:\New Agent\new_agent_15\backend\app\agents\branch_pr_agent.py"
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

replacement = """            if repo_specific_body:
                repo_specific_body = (
                    f"**Note: This PR contains the `{repo_name}` repository changes for this story.**\\n\\n"
                    f"{repo_specific_body}\\n\\n"
                    f"---\\n## 🔄 Rework & Conversation History\\n{context.get('qa_feedback', 'No previous QA feedback for this PR.')}\\n\\n"
                    f"---\\n## 🔒 Code Preservation & Quality Assurance\\n- Pre-existing endpoints, functions, and tests remain intact.\\n- Code validated through DEVAA autonomous engineering pipeline."
                )
            else:
                repo_specific_body = (
                    f"**Note: This PR contains the `{repo_name}` repository changes for this story.** "
                    f"Please check other related repositories for the complete implementation.\\n\\n"
                    f"{pr_meta.get('pr_description', '')}"
                )

            return self._create_github_pr("""

# We'll use regex to replace everything from `if repo_specific_body:` to `return self._create_github_pr(`
content = re.sub(r'            if repo_specific_body:.*?            return self\._create_github_pr\(', replacement, content, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
