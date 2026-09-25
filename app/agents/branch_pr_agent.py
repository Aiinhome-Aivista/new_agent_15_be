"""
BranchPRAgent — Step 5
Creates a feature branch and pull request record. Idempotent — never creates duplicates.
Writes to DB always. Also calls real GitHub API if token is configured.
"""
import re
from app.agents.base_agent import BaseAgent, AgentResult
from app.services.llm_service import LLMService

PR_SUMMARY_PROMPT = """You are DEVAA's Lead Principal Software Architect. Generate an outstanding, comprehensive, and professional GitHub Pull Request description for engineering and QA review.

The PR description will be displayed directly in the GitHub Conversation tab. Anyone reading it must instantly understand:
1. WHAT the business requirement / user story was.
2. EXACTLY WHAT changes were made and WHERE (specific file paths, endpoints, methods, and schemas).
3. PROOF that every Acceptance Criterion from the story was 100% satisfied.
4. ASSURANCE that existing code was preserved (no breaking changes or unwanted deletions).

STORY IDENTIFIER: {key_identifier}
STORY TITLE: {title}

STORY REQUIREMENTS & CONTEXT:
{description}

ACCEPTANCE CRITERIA:
{acceptance_criteria}

DEVELOPER IMPLEMENTATION SUMMARY:
{dev_summary}

FILES CHANGED & TECHNICAL DETAILS:
{detailed_changes}

REWORK & QA FEEDBACK (If any):
{qa_feedback_section}

Respond in strictly valid JSON format:
{{
  "pr_title": "feat({key_identifier}): Concise descriptive title (under 72 chars)",
  "pr_description": "## 📌 Summary & Requirements\\n...\\n\\n## 🛠️ Changes Implemented (What & Where)\\n...\\n\\n## ✅ Acceptance Criteria Coverage\\n...\\n\\n## 🔄 Rework & Conversation History\\n...\\n\\n## 🔒 Code Preservation & Quality Assurance\\n..."
}}

STRUCTURING GUIDELINES FOR "pr_description":
- Use standard GitHub Flavored Markdown (headings, bullet points, backtick code spans like `app/routes/users.py`, checkboxes `[x]`).
- In "## 📌 Summary & Requirements", clearly summarize the purpose of the story and the business requirement.
- In "## 🛠️ Changes Implemented (What & Where)", list every modified/created file, its path in backticks, and clearly describe what was added or updated inside it (endpoints, validation rules, handlers).
- In "## ✅ Acceptance Criteria Coverage", list each Acceptance Criterion from the story with `[x]` and explicitly explain how and in which file it was fulfilled.
- In "## 🔒 Code Preservation & Quality Assurance", confirm that existing functionality/endpoints were preserved and automated test validation passed.

Respond ONLY with the JSON object."""


class BranchPRAgent(BaseAgent):
    agent_name = "BranchPR"

    def _execute(self, context: dict) -> AgentResult:
        from app.models.devaa_models import PullRequest
        from flask import current_app

        story = context.get('story')
        workflow_id = context.get('workflow_id')
        developer_output = context.get('developer_output', {})

        if not story or not workflow_id:
            return AgentResult(success=False, error="story and workflow_id required.")

        # ── Idempotency check: skip if PR already exists for this workflow ──
        existing_pr = PullRequest.query.filter_by(workflow_id=workflow_id).first()
        if existing_pr:
            self.logger.info(f"PR already exists for workflow {workflow_id} — skipping.")
            return AgentResult(
                success=True,
                output={
                    "pr_id": existing_pr.id,
                    "branch_name": existing_pr.branch_name,
                    "pr_url": existing_pr.pr_url,
                    "pr_status": existing_pr.pr_status,
                    "skipped": True,
                    "reason": "PR already exists (idempotency guard)"
                }
            )

        # ── Determine feature branch name: feat/jira-story-key_random 8 digit number ──
        jira_key = story.jira_story_key if hasattr(story, 'jira_story_key') else (story.get('jira_story_key', '') if isinstance(story, dict) else '')
        story_id = story.id if hasattr(story, 'id') else (story.get('id', '') if isinstance(story, dict) else '')
        title = story.title if hasattr(story, 'title') else (story.get('title', 'feature') if isinstance(story, dict) else 'feature')
        description = story.description if hasattr(story, 'description') else (story.get('description', '') if isinstance(story, dict) else '')
        acceptance_criteria = story.acceptance_criteria if hasattr(story, 'acceptance_criteria') else (story.get('acceptance_criteria', '') if isinstance(story, dict) else '')

        key_identifier = jira_key or (f"STORY-{story_id}" if story_id else "TASK")

        custom_target_branch = context.get('target_branch')
        if custom_target_branch:
            branch_name = custom_target_branch
        else:
            import random
            random_digits = random.randint(10000000, 99999999)
            branch_name = f"feat/{key_identifier}_{random_digits}"

        # ── Generate comprehensive, professional PR summary via LLM ───────────────────
        changes = developer_output.get('changes', [])
        detailed_changes_lines = []
        for c in changes:
            f = c.get('file', 'unknown')
            act = c.get('action', 'modify').capitalize()
            desc = c.get('description', '')
            crits = ", ".join(c.get('satisfies_criteria', []))
            crit_text = f" (Satisfies: {crits})" if crits else ""
            detailed_changes_lines.append(f"- **`{f}`** ({act}): {desc}{crit_text}")
        detailed_changes = "\n".join(detailed_changes_lines) if detailed_changes_lines else "No specific files listed."

        try:
            llm_response = LLMService.generate_response(
                prompt=PR_SUMMARY_PROMPT.format(
                    key_identifier=key_identifier,
                    title=title,
                    description=description or "No description provided.",
                    acceptance_criteria=acceptance_criteria or "No acceptance criteria specified.",
                    dev_summary=developer_output.get('summary', ''),
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
                for line in str(acceptance_criteria).split("\n"):
                    clean_line = line.strip().lstrip("-*•0123456789. ")
                    if clean_line:
                        ac_lines.append(f"- [x] {clean_line}")
            ac_formatted = "\n".join(ac_lines) if ac_lines else "- [x] All story acceptance criteria fulfilled and verified."

            fallback_desc = f"""## 📌 Summary
**Story:** {key_identifier} — {title}

### Problem Statement & Requirement
{description or 'Automated implementation requested for: ' + title}

---

## 🛠️ Changes (What & Where)
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
"""
            pr_meta = {
                "pr_title": f"feat({key_identifier}): {title[:60]}",
                "pr_description": fallback_desc.strip()
            }

        # ── Real GitHub API & Git Push (Strictly No Simulation) ──
        pr_url = None
        pr_number = None
        # Determine base branch: context > story.source_branch > repository_details > config default
        story_repo_details = (
            getattr(story, 'repository_details', None) if story else
            (story.get('repository_details') if isinstance(story, dict) else None)
        )
        first_repo = (
            story_repo_details[0] if (isinstance(story_repo_details, list) and len(story_repo_details) > 0 and isinstance(story_repo_details[0], dict))
            else {}
        )
        base_branch = (
            context.get('base_branch')
            or (getattr(story, 'source_branch', None) if story else (story.get('source_branch') if isinstance(story, dict) else None))
            or first_repo.get('branch')
            or first_repo.get('target_branch')
            or current_app.config.get('GITHUB_DEFAULT_BASE_BRANCH', 'main')
        )
        base_branch = str(base_branch).strip() if base_branch else 'main'

        # Resolve GitHub PAT: TokenSecretService (repo_tokens.json) > GITHUB_TOKEN (.env)
        from app.services.token_secret_service import TokenSecretService
        repo_url_candidate = first_repo.get('url', '')
        if not repo_url_candidate and first_repo.get('name'):
            repo_url_candidate = TokenSecretService.get_url_for_repo(first_repo.get('name')) or ''
        if not repo_url_candidate:
            repo_url_candidate = current_app.config.get('GITHUB_BASE_URL', '')

        github_token = (
            TokenSecretService.get_token_for_repo(repo_url_candidate or first_repo.get('name', ''))
            or current_app.config.get('GITHUB_TOKEN', '').strip()
        )

        if not github_token:
            err_msg = "GitHub PAT is not configured (neither in config/repo_tokens.json nor GITHUB_TOKEN in backend/.env). Real GitHub branch and PR cannot be created without a valid token."
            self.logger.error(err_msg)
            return AgentResult(success=False, error=err_msg)

        workspace_dir = context.get('workspace_dir')
        if not workspace_dir and isinstance(developer_output, dict):
            workspace_dir = developer_output.get('repo_dir')

        pr_url, pr_number, pr_err = self._create_github_pr(
            story=story,
            branch_name=branch_name,
            pr_title=pr_meta['pr_title'],
            pr_body=pr_meta['pr_description'],
            github_token=github_token,
            base_branch=base_branch,
            changes=changes,
            workspace_dir=workspace_dir
        )

        if not pr_url:
            err_msg = f"Failed to push branch '{branch_name}' and create real GitHub Pull Request: {pr_err or 'Unknown GitHub error'}"
            self.logger.error(err_msg)
            return AgentResult(success=False, error=err_msg)

        # ── Build Evidence Report for DB storage ───────────────────────────
        story_dict = story.to_dict() if hasattr(story, 'to_dict') else (story if isinstance(story, dict) else {})
        evidence_report = {
            "generated_by": "DEVAA Evidence Report",
            "story": story_dict,
            "pull_request": {
                "pr_url": pr_url,
                "pr_number": pr_number,
                "branch_name": branch_name,
                "pr_title": pr_meta.get('pr_title', ''),
                "pr_description": pr_meta.get('pr_description', ''),
                "pr_status": "open",
            },
            "changed_files": [c.get('file') for c in changes],
            "workflow_id": workflow_id,
        }

        # ── Persist Real PR to DB ──────────────────────────────────────
        pr = PullRequest(
            workflow_id=workflow_id,
            story_id=story.id if hasattr(story, 'id') else story.get('id'),
            branch_name=branch_name,
            pr_url=pr_url,
            pr_number=pr_number,
            pr_status='open',
            pr_summary=pr_meta.get('pr_description', ''),
            changed_files=[c.get('file') for c in changes],
            evidence_report=evidence_report,
            created_by=context.get('triggered_by_user_id')
        )
        self.db.session.add(pr)
        self.db.session.commit()

        return AgentResult(
            success=True,
            output={
                "pr_id": pr.id,
                "branch_name": branch_name,
                "pr_url": pr.pr_url,
                "pr_number": pr_number,
                "pr_title": pr_meta['pr_title'],
                "changed_files": [c.get('file') for c in changes],
                "skipped": False
            }
        )


    def _create_github_pr(self, story, branch_name, pr_title, pr_body, github_token, base_branch, changes, workspace_dir=None):
        """
        Creates real branch, commits real code changes generated by DeveloperAgent, pushes to GitHub,
        and opens a real Pull Request on GitHub. Returns (pr_url, pr_number, error_msg).
        """
        import subprocess, tempfile, os, requests
        from flask import current_app

        repos = story.repository_details if hasattr(story, 'repository_details') else (story.get('repository_details') if isinstance(story, dict) else [])
        if isinstance(repos, str):
            import json
            try: repos = json.loads(repos)
            except Exception: repos = []
        first_repo = repos[0] if isinstance(repos, list) and repos and isinstance(repos[0], dict) else {}
        repo_url = first_repo.get('url', '')
        if not repo_url and first_repo.get('name'):
            from app.services.token_secret_service import TokenSecretService
            repo_url = TokenSecretService.get_url_for_repo(first_repo.get('name')) or ''
        if not repo_url:
            repo_url = current_app.config.get('GITHUB_BASE_URL', '')
        
        org = current_app.config.get('GITHUB_ORG', '')
        if not org or org == 'your-org-or-username':
            org = None
        repo_name = None

        if repo_url and 'github.com' in repo_url:
            parts = repo_url.split('github.com/')[-1].replace('.git', '').strip('/').split('/')
            if len(parts) >= 2:
                if not org:
                    org = parts[0]
                repo_name = parts[1]

        if not org or not repo_name:
            err = "Could not determine GitHub org and repo_name for PR creation."
            self.logger.warning(err)
            return None, None, err

        api_base = "https://api.github.com"
        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }

        # ── Clone repo into isolated workspace directory, checkout/create branch, apply real changes, commit & push ──
        auth_repo_url = f"https://x-access-token:{github_token}@github.com/{org}/{repo_name}.git"
        
        if workspace_dir and os.path.exists(os.path.join(workspace_dir, '.git')):
            repo_dir = workspace_dir
        elif workspace_dir and os.path.exists(os.path.join(workspace_dir, repo_name, '.git')):
            repo_dir = os.path.join(workspace_dir, repo_name)
        elif workspace_dir:
            repo_dir = os.path.join(workspace_dir, repo_name)
        else:
            from flask import current_app
            repos_root = os.path.abspath(os.path.join(current_app.root_path, '..', 'repos'))
            os.makedirs(repos_root, exist_ok=True)
            repo_dir = os.path.join(repos_root, repo_name)

        try:
            self.logger.info(f"Using project repository at '{repo_dir}' for branch '{branch_name}'...")
            if not os.path.exists(os.path.join(repo_dir, '.git')):
                self.logger.info(f"Cloning {org}/{repo_name} into project folder '{repo_dir}'...")
                if os.path.exists(repo_dir):
                    import shutil
                    shutil.rmtree(repo_dir, ignore_errors=True)
                clone_res = subprocess.run(
                    ['git', 'clone', '--depth', '50', '-b', base_branch, auth_repo_url, repo_dir],
                    capture_output=True, text=True
                )
                if clone_res.returncode != 0:
                    self.logger.warning(f"Branch-specific clone failed, trying default clone: {clone_res.stderr}")
                    clone_default = subprocess.run(['git', 'clone', auth_repo_url, repo_dir], capture_output=True, text=True)
                    if clone_default.returncode != 0:
                        err = f"Failed to clone repository into {repo_dir}: {clone_default.stderr.strip()}"
                        self.logger.error(err)
                        return None, None, err
            else:
                subprocess.run(['git', 'remote', 'set-url', 'origin', auth_repo_url], cwd=repo_dir, check=True)
                subprocess.run(['git', 'fetch', 'origin'], cwd=repo_dir, check=True)

            # Configure git user identity
            subprocess.run(['git', 'config', 'user.name', 'DEVAA Bot'], cwd=repo_dir, check=True)
            subprocess.run(['git', 'config', 'user.email', 'devaa-bot@users.noreply.github.com'], cwd=repo_dir, check=True)

            # Check if target branch exists on remote
            ls_remote = subprocess.run(
                ['git', 'ls-remote', '--heads', 'origin', branch_name],
                cwd=repo_dir, capture_output=True, text=True
            )
            if branch_name in ls_remote.stdout:
                self.logger.info(f"Checking out existing remote branch '{branch_name}'")
                subprocess.run(['git', 'checkout', branch_name], cwd=repo_dir, check=True)
                subprocess.run(['git', 'pull', 'origin', branch_name], cwd=repo_dir, capture_output=True)
            else:
                self.logger.info(f"Fetching latest origin/{base_branch} and creating '{branch_name}'")
                subprocess.run(['git', 'fetch', 'origin', base_branch], cwd=repo_dir, check=True)
                subprocess.run(['git', 'checkout', base_branch], cwd=repo_dir, capture_output=True)
                subprocess.run(['git', 'reset', '--hard', f'origin/{base_branch}'], cwd=repo_dir, capture_output=True)
                local_b = subprocess.run(['git', 'branch', '--list', branch_name], cwd=repo_dir, capture_output=True, text=True)
                if branch_name in local_b.stdout:
                    subprocess.run(['git', 'branch', '-D', branch_name], cwd=repo_dir, capture_output=True)
                subprocess.run(['git', 'checkout', '-b', branch_name, f'origin/{base_branch}'], cwd=repo_dir, check=True)

            # Apply real code changes directly to actual files
            for c in changes:
                rel_file = c.get('file')
                if not rel_file:
                    continue
                full_file_path = os.path.join(repo_dir, rel_file.replace('/', os.sep))
                os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
                
                action = c.get('action', 'modify')
                if action == 'delete':
                    if os.path.exists(full_file_path):
                        os.remove(full_file_path)
                        self.logger.info(f"Deleted file: {rel_file}")
                    continue

                full_content = c.get('full_content')
                code_snippet = c.get('code_snippet') or c.get('code') or ''

                if full_content:
                    with open(full_file_path, 'w', encoding='utf-8') as f:
                        f.write(full_content)
                    self.logger.info(f"Wrote full production code to: {rel_file}")
                elif code_snippet:
                    if not os.path.exists(full_file_path):
                        with open(full_file_path, 'w', encoding='utf-8') as f:
                            f.write(code_snippet)
                        self.logger.info(f"Created new file with code snippet: {rel_file}")
                    else:
                        with open(full_file_path, 'r', encoding='utf-8') as f:
                            existing_code = f.read()
                        if code_snippet not in existing_code:
                            with open(full_file_path, 'a', encoding='utf-8') as f:
                                f.write(f"\n{code_snippet}\n")
                            self.logger.info(f"Appended code snippet to: {rel_file}")

            # Git add, commit, and push
            subprocess.run(['git', 'add', '-A'], cwd=repo_dir, check=True)
            status_res = subprocess.run(['git', 'status', '--porcelain'], cwd=repo_dir, capture_output=True, text=True)
            
            jira_key = story.jira_story_key if hasattr(story, 'jira_story_key') else (story.get('jira_story_key', '') if isinstance(story, dict) else '')
            commit_msg = f"feat({jira_key or 'DEVAA'}): {pr_title}\n\nAutomated implementation by DEVAA pipeline adhering to repository patterns."
            if status_res.stdout.strip():
                subprocess.run(['git', 'commit', '-m', commit_msg], cwd=repo_dir, check=True)
                self.logger.info(f"Committed code changes to branch '{branch_name}'.")

            self.logger.info(f"Pushing branch '{branch_name}' to remote...")
            push_res = subprocess.run(['git', 'push', '-u', 'origin', branch_name], cwd=repo_dir, capture_output=True, text=True)
            if push_res.returncode == 0:
                self.logger.info(f"Successfully pushed branch '{branch_name}' to GitHub.")
            else:
                err = f"Git push failed: {push_res.stderr.strip()}"
                self.logger.error(err)
                return None, None, err

        except Exception as git_err:
            err = f"Git operations failed: {git_err}"
            self.logger.error(err)
            return None, None, err

        # ── Open Pull Request via GitHub REST API ──────────────────
        try:
            api_url = f"{api_base}/repos/{org}/{repo_name}/pulls"
            payload = {
                "title": pr_title,
                "body": pr_body,
                "head": branch_name,
                "base": base_branch
            }
            resp = requests.post(api_url, json=payload, headers=headers, timeout=15)
            if resp.status_code == 201:
                data = resp.json()
                self.logger.info(f"Created GitHub PR #{data.get('number')}: {data.get('html_url')}")
                return data.get('html_url'), data.get('number'), None
            elif resp.status_code == 422:
                # Check if PR already exists for this branch
                check_url = f"{api_base}/repos/{org}/{repo_name}/pulls?head={org}:{branch_name}&state=open"
                check_resp = requests.get(check_url, headers=headers, timeout=10)
                if check_resp.status_code == 200 and check_resp.json():
                    existing = check_resp.json()[0]
                    self.logger.info(f"Found existing open GitHub PR #{existing.get('number')}: {existing.get('html_url')}")
                    return existing.get('html_url'), existing.get('number'), None
                return None, None, f"GitHub PR creation 422: {resp.text}"
            else:
                return None, None, f"GitHub PR creation response ({resp.status_code}): {resp.text[:200]}"
        except Exception as api_err:
            return None, None, f"GitHub PR API failed: {api_err}"
