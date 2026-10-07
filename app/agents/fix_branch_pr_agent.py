filepath = r"d:\New Agent\new_agent_15\backend\app\agents\branch_pr_agent.py"
with open(filepath, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = lines[:260] + [
    '            pr_descriptions_map = developer_output.get("pr_descriptions", {})\n',
    '            repo_specific_body = pr_descriptions_map.get(repo_name)\n',
    '\n',
    '            if repo_specific_body:\n',
    '                repo_specific_body = (\n',
    '                    f"**Note: This PR contains the `{repo_name}` repository changes for this story.**\\n\\n"\n',
    '                    f"{repo_specific_body}\\n\\n"\n',
    '                    f"---\\n## 🔄 Rework & Conversation History\\n{context.get(\'qa_feedback\', \'No previous QA feedback for this PR.\')}\\n\\n"\n',
    '                    f"---\\n## 🔒 Code Preservation & Quality Assurance\\n- Pre-existing endpoints, functions, and tests remain intact.\\n- Code validated through DEVAA autonomous engineering pipeline."\n',
    '                )\n',
    '            else:\n',
    '                repo_specific_body = (\n',
    '                    f"**Note: This PR contains the `{repo_name}` repository changes for this story.** "\n',
    '                    f"Please check other related repositories for the complete implementation.\\n\\n"\n',
    '                    f"{pr_meta.get(\'pr_description\', \'\')}"\n',
    '                )\n',
    '\n',
    '            return self._create_github_pr(\n'
] + lines[289:]

with open(filepath, "w", encoding="utf-8") as f:
    f.writelines(new_lines)
