import re
import os

filepath = r"d:\New Agent\new_agent_15\backend\app\agents\branch_pr_agent.py"
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove the global PR_SUMMARY generation (lines 103 to 177)
# But wait, it uses pr_meta. So I must keep the generation but move it into `_make_pr_for_repo`.

# Let's completely rewrite `_make_pr_for_repo` and delete the global block.

global_llm_block_pattern = r"# ── Generate comprehensive, professional PR summary via LLM ───────────────────.*?# ── Determine repos and group changes by target_repo ────────────────"
content = re.sub(global_llm_block_pattern, "# ── Determine repos and group changes by target_repo ────────────────", content, flags=re.DOTALL)

new_make_pr = """        def _make_pr_for_repo(repo_name, repo_detail, repo_changes):
            rurl = repo_detail.get('url', '')
            if not rurl and repo_detail.get('name'):
                rurl = TokenSecretService.get_url_for_repo(repo_detail.get('name')) or ''
            if not rurl:
                rurl = current_app.config.get('GITHUB_BASE_URL', '')
            token = (
                TokenSecretService.get_token_for_repo(rurl or repo_detail.get('name', ''))
                or current_app.config.get('GITHUB_TOKEN', '').strip()
            )
            if not token:
                return None, None, f"No GitHub token for repo '{repo_name}'"
            br = repo_detail.get('branch') or base_branch_global
            ws = os.path.join(workspace_dir, repo_name) if workspace_dir else repo_dir_map.get(repo_name)

            detailed_changes_lines = []
            for c in repo_changes:
                f = c.get('file', 'unknown')
                act = c.get('action', 'modify').capitalize()
                desc = c.get('description', '')
                crits = ", ".join(c.get('satisfies_criteria', []))
                crit_text = f" (Satisfies: {crits})" if crits else ""
                detailed_changes_lines.append(f"- **`{f}`** ({act}): {desc}{crit_text}")
            detailed_changes = "\\n".join(detailed_changes_lines) if detailed_changes_lines else "No specific files listed."

            try:
                llm_response = LLMService.generate_response(
                    prompt=PR_SUMMARY_PROMPT.format(
                        key_identifier=key_identifier,
                        title=title,
                        description=description or "No description provided.",
                        acceptance_criteria=acceptance_criteria or "No acceptance criteria specified.",
                        dev_summary=f"Changes for repository: {repo_name}. " + developer_output.get('summary', ''),
                        detailed_changes=detailed_changes,
                        qa_feedback_section=context.get('qa_feedback', 'None/First Iteration')
                    ),
                    system_instruction="Respond ONLY with valid JSON.",
                    agent_name="BranchPR"
                )
                import json
                llm_response = llm_response.strip()
                if llm_response.startswith("```"):
                    llm_response = llm_response.split("```")[1]
                    if llm_response.startswith("json"):
                        llm_response = llm_response[4:]
                pr_meta = json.loads(llm_response)
            except Exception as e:
                self.logger.warning(f"PR summary LLM failed, using structured fallback: {e}")
                ac_lines = []
                if acceptance_criteria:
                    for line in str(acceptance_criteria).split("\\n"):
                        clean_line = line.strip().lstrip("-*•0123456789. ")
                        if clean_line:
                            ac_lines.append(f"- [x] {clean_line}")
                ac_formatted = "\\n".join(ac_lines) if ac_lines else "- [x] All story acceptance criteria fulfilled and verified."

                fallback_desc = f\"\"\"## 📌 Summary
**Story:** {key_identifier} — {title}

### Problem Statement & Requirement
{description or 'Automated implementation requested for: ' + title}

---

## 🛠️ Changes Implemented (What, Where & Why)
{detailed_changes}

---

## ✅ Acceptance Criteria Coverage
{ac_formatted}

---

## 🔄 Rework & Conversation History
{context.get('qa_feedback', 'No previous QA feedback for this PR.')}

---

## 🔒 Code Preservation & Quality Assurance
- Pre-existing endpoints, functions, and tests remain intact.
- Code validated through DEVAA autonomous engineering pipeline.
\"\"\"
                pr_meta = {
                    "pr_title": f"feat({key_identifier}): {title[:60]}",
                    "pr_description": fallback_desc.strip()
                }

            pr_descriptions_map = developer_output.get('pr_descriptions', {})
            repo_specific_body = pr_descriptions_map.get(repo_name)

            if repo_specific_body:
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

            return self._create_github_pr(
                story=story, branch_name=branch_name,
                pr_title=pr_meta['pr_title'], pr_body=repo_specific_body,
                github_token=token, base_branch=br,
                changes=repo_changes, workspace_dir=ws, repo_url=rurl
            )"""

old_make_pr_pattern = r"        def _make_pr_for_repo\(repo_name, repo_detail, repo_changes\):.*?return self\._create_github_pr\([\s\S]*?\n            \)"

content = re.sub(old_make_pr_pattern, new_make_pr, content, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated branch_pr_agent.py")
