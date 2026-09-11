"""
BranchPRAgent — Step 5
Creates a feature branch and pull request record. Idempotent — never creates duplicates.
Writes to DB always. Also calls real GitHub API if token is configured.
"""
import re
from app.agents.base_agent import BaseAgent, AgentResult
from app.services.llm_service import LLMService

PR_SUMMARY_PROMPT = """You are a DEVAA PR Summary Agent. Generate a concise, professional pull request title and description.

STORY: {title}
CHANGES SUMMARY: {dev_summary}
FILES CHANGED: {files_list}

Respond in JSON format:
{{
  "pr_title": "feat: Short PR title (max 72 chars)",
  "pr_description": "## Summary\\n...\\n\\n## Changes\\n- ...\\n\\n## Acceptance Criteria Coverage\\n- ..."
}}

Respond ONLY with JSON."""


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

        # ── Check for user-specified target branch or generate default ────
        repo_details = story.repository_details if hasattr(story, 'repository_details') else (story.get('repository_details') if isinstance(story, dict) else [])
        first_repo = repo_details[0] if isinstance(repo_details, list) and len(repo_details) > 0 and isinstance(repo_details[0], dict) else {}
        custom_target_branch = getattr(story, 'target_branch', None) or first_repo.get('target_branch') or first_repo.get('work_branch')

        title = story.title if hasattr(story, 'title') else story.get('title', 'feature')
        jira_key = story.jira_story_key if hasattr(story, 'jira_story_key') else story.get('jira_story_key', '')
        safe_title = re.sub(r'[^a-zA-Z0-9\-]', '-', title.lower())[:40].strip('-')
        branch_name = custom_target_branch or f"devaa/{jira_key.lower() + '/' if jira_key else ''}{safe_title}-wf{workflow_id}"

        # ── Generate PR summary via LLM ───────────────────────────
        changes = developer_output.get('changes', [])
        files_list = "\n".join([f"- {c.get('file', 'unknown')} ({c.get('action', '')})" for c in changes])

        try:
            llm_response = LLMService.generate_response(
                prompt=PR_SUMMARY_PROMPT.format(
                    title=title,
                    dev_summary=developer_output.get('summary', ''),
                    files_list=files_list or "No files listed"
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
            self.logger.warning(f"PR summary LLM failed, using defaults: {e}")
            pr_meta = {
                "pr_title": f"feat: {title}",
                "pr_description": f"## Changes\n{developer_output.get('summary', 'No summary.')}"
            }

        # ── Try GitHub API if configured ──────────────────────────
        pr_url = None
        pr_number = None
        github_token = current_app.config.get('GITHUB_TOKEN', '')
        github_org = current_app.config.get('GITHUB_ORG', '')

        if github_token and github_org and hasattr(story, 'repository_details') and story.repository_details:
            pr_url, pr_number = self._create_github_pr(
                story=story,
                branch_name=branch_name,
                pr_title=pr_meta['pr_title'],
                pr_body=pr_meta['pr_description'],
                github_token=github_token,
                base_branch=current_app.config.get('GITHUB_DEFAULT_BASE_BRANCH', 'main')
            )

        # ── Persist PR to DB ──────────────────────────────────────
        pr = PullRequest(
            workflow_id=workflow_id,
            story_id=story.id if hasattr(story, 'id') else story.get('id'),
            branch_name=branch_name,
            pr_url=pr_url or f"#simulated-pr-{workflow_id}",
            pr_number=pr_number,
            pr_status='open',
            pr_summary=pr_meta.get('pr_description', ''),
            changed_files=[c.get('file') for c in changes],
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

    def _create_github_pr(self, story, branch_name, pr_title, pr_body, github_token, base_branch):
        """Attempt to create a real GitHub PR. Returns (pr_url, pr_number) or (None, None).
        Ensures the branch exists on remote; if not, creates it from base_branch.
        """
        import requests
        try:
            from flask import current_app
            repos = story.repository_details or []
            first_repo = repos[0] if isinstance(repos, list) and repos else repos
            repo_name = first_repo.get('name', '') if isinstance(first_repo, dict) else str(first_repo)
            repo_url = first_repo.get('url', '') if isinstance(first_repo, dict) else ''

            raw_base = current_app.config.get('GITHUB_BASE_URL', 'https://api.github.com')
            api_base = "https://api.github.com" if "github.com" in raw_base and not raw_base.startswith("https://api.github.com") else raw_base.rstrip('/')
            org = current_app.config.get('GITHUB_ORG', '')

            # Extract org / repo_name from URL if config is just the repo clone URL
            if (not org or not repo_name) and (repo_url or raw_base):
                u = repo_url or raw_base
                if 'github.com/' in u:
                    parts = u.split('github.com/')[-1].replace('.git', '').strip('/').split('/')
                    if len(parts) >= 2:
                        if not org: org = parts[0]
                        if not repo_name: repo_name = parts[1]

            if not org or not repo_name:
                self.logger.warning("Could not determine GitHub org and repo_name for PR creation.")
                return None, None

            headers = {
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github.v3+json"
            }

            # ── Check if branch exists; if not, create it from base_branch ─
            branch_ref_url = f"{api_base}/repos/{org}/{repo_name}/git/ref/heads/{branch_name}"
            ref_resp = requests.get(branch_ref_url, headers=headers, timeout=10)

            if ref_resp.status_code == 404:
                # Branch does not exist on remote -> fetch base_branch commit SHA
                base_ref_url = f"{api_base}/repos/{org}/{repo_name}/git/ref/heads/{base_branch}"
                base_resp = requests.get(base_ref_url, headers=headers, timeout=10)
                if base_resp.status_code == 200:
                    base_sha = base_resp.json().get('object', {}).get('sha')
                    if base_sha:
                        create_ref_url = f"{api_base}/repos/{org}/{repo_name}/git/refs"
                        create_resp = requests.post(create_ref_url, json={
                            "ref": f"refs/heads/{branch_name}",
                            "sha": base_sha
                        }, headers=headers, timeout=10)
                        if create_resp.status_code in (200, 201):
                            self.logger.info(f"Created new branch '{branch_name}' from '{base_branch}' on GitHub.")
                        else:
                            self.logger.warning(f"Could not create branch on GitHub: {create_resp.status_code} {create_resp.text}")
            elif ref_resp.status_code == 200:
                self.logger.info(f"Branch '{branch_name}' already exists on GitHub. Using existing branch.")

            # ── Create Pull Request ───────────────────────────────────────
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
                return data.get('html_url'), data.get('number')
            else:
                self.logger.warning(f"GitHub PR creation response: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            self.logger.warning(f"GitHub API call failed: {e}")
        return None, None
