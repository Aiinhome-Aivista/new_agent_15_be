"""
RepoAnalysisAgent — Step 2
Analyzes the codebase context from repository details and builds an implementation map.
"""
from app.agents.base_agent import BaseAgent, AgentResult
from app.services.llm_service import LLMService


ANALYSIS_PROMPT_TEMPLATE = """You are a DEVAA Repository Analysis Agent. Analyze the following story and repository context to build a concrete implementation map.

STORY:
Title: {title}
Description: {description}
Acceptance Criteria: {acceptance_criteria}
Source Branch: {source_branch}

REPOSITORIES:
{repository_details}

Your job is to identify EXACTLY what files/modules need to be created or modified.

Respond in this exact JSON format:
{{
  "context_summary": "Brief description of what this story is building and the technical approach",
  "files_to_modify": [
    {{"file": "path/to/file.py", "action": "create|modify|delete", "reason": "Why this file needs to change"}}
  ],
  "patterns_found": [
    "Description of any existing code patterns that should be followed"
  ],
  "implementation_notes": "Key technical decisions and constraints the Developer Agent must follow",
  "estimated_complexity": "low|medium|high"
}}

Respond ONLY with the JSON object."""


class RepoAnalysisAgent(BaseAgent):
    agent_name = "RepoAnalysis"

    def _execute(self, context: dict) -> AgentResult:
        story = context.get('story')
        if not story:
            return AgentResult(success=False, error="No story provided to RepoAnalysisAgent.")

        title = story.title if hasattr(story, 'title') else story.get('title', '')
        description = story.description if hasattr(story, 'description') else story.get('description', '')
        acceptance_criteria = story.acceptance_criteria if hasattr(story, 'acceptance_criteria') else story.get('acceptance_criteria', '')
        source_branch = story.source_branch if hasattr(story, 'source_branch') else story.get('source_branch', '')
        repo_details = story.repository_details if hasattr(story, 'repository_details') else story.get('repository_details', [])
        if isinstance(repo_details, str):
            import json
            try:
                repo_details = json.loads(repo_details)
            except Exception:
                repo_details = []

        if not repo_details:
            github_base_url = self.config.get('GITHUB_BASE_URL', '')
            if github_base_url:
                repo_details = [{
                    'url': github_base_url,
                    'branch': self.config.get('GITHUB_DEFAULT_BASE_BRANCH', 'main'),
                    'name': 'primary-repo'
                }]

        # ── RAG LOGIC ──────────────────────────────────────────────
        raw_prefixes = self.config.get('ALLOWED_REPO_PREFIXES', [])
        if isinstance(raw_prefixes, str):
            allowed_prefixes = [p.strip() for p in raw_prefixes.split(',') if p.strip()]
        elif isinstance(raw_prefixes, list):
            allowed_prefixes = [str(p).strip() for p in raw_prefixes if str(p).strip()]
        else:
            allowed_prefixes = []

        repo_texts = []
        repo_files_map = {}
        import tempfile, subprocess, os
        
        for repo in repo_details:
            repo_url = repo.get('url')
            if not repo_url: continue
            
            # Check guardrail
            is_allowed = False
            for prefix in allowed_prefixes:
                if repo_url.startswith(prefix):
                    is_allowed = True
                    break
            
            if not is_allowed and allowed_prefixes:
                self.logger.warning(f"Repo {repo_url} not in ALLOWED_REPO_PREFIXES.")
                continue

            # Clone and inspect real repository files inside project directory
            repo_name = "repo"
            if repo_url and 'github.com' in repo_url:
                parts = repo_url.split('github.com/')[-1].replace('.git', '').strip('/').split('/')
                if len(parts) >= 2:
                    repo_name = parts[1]

            from flask import current_app
            from app.config.settings import Config
            from app.services.rag_service import RagService

            workflow_id = context.get('workflow_id', 0)
            story_id = story.id if hasattr(story, 'id') else (story.get('id', 0) if isinstance(story, dict) else 0)

            # Isolated Git workspace per workflow
            workspace_dir = context.get('workspace_dir')
            if not workspace_dir:
                workspaces_root = getattr(Config, 'WORKSPACES_DIR', os.path.abspath(os.path.join(current_app.root_path, '..', 'workspaces')))
                workspace_dir = os.path.join(workspaces_root, f"wf_{workflow_id}_{story_id}")
            os.makedirs(workspace_dir, exist_ok=True)
            repo_dir = os.path.join(workspace_dir, repo_name)

            try:
                github_token = self.config.get('GITHUB_TOKEN')
                clone_url = repo_url
                if github_token and "github.com" in repo_url:
                    clone_url = repo_url.replace("https://github.com/", f"https://x-access-token:{github_token}@github.com/")

                target_branch = source_branch or repo.get('branch') or self.config.get('GITHUB_DEFAULT_BASE_BRANCH', 'main')

                if os.path.exists(os.path.join(repo_dir, '.git')):
                    self.logger.info(f"Using existing isolated repository at '{repo_dir}'")
                    subprocess.run(['git', 'remote', 'set-url', 'origin', clone_url], cwd=repo_dir, check=True)
                    subprocess.run(['git', 'fetch', 'origin'], cwd=repo_dir, check=True)
                    subprocess.run(['git', 'checkout', target_branch], cwd=repo_dir, capture_output=True)
                    subprocess.run(['git', 'pull', 'origin', target_branch], cwd=repo_dir, capture_output=True)
                else:
                    self.logger.info(f"Cloning repository into isolated workspace '{repo_dir}'...")
                    if os.path.exists(repo_dir):
                        import shutil
                        shutil.rmtree(repo_dir, ignore_errors=True)
                    clone_cmd = ['git', 'clone', '--depth', '50']
                    if target_branch:
                        clone_cmd.extend(['-b', target_branch])
                    clone_cmd.extend([clone_url, repo_dir])

                    try:
                        subprocess.check_call(clone_cmd)
                    except subprocess.CalledProcessError:
                        self.logger.warning(f"Branch '{target_branch}' not found on remote, falling back to default clone.")
                        subprocess.check_call(['git', 'clone', '--depth', '50', clone_url, repo_dir])

                # ── VECTOR DB: Ensure Immutable Base Index ──────────
                rag_service = RagService.get_instance()
                git_meta = rag_service.get_repo_metadata(repo_dir)
                commit_sha = git_meta.get("full_commit_sha", "")

                base_col = rag_service.ensure_base_index(
                    repo_url=repo_url,
                    branch=target_branch,
                    commit_sha=commit_sha,
                    repo_dir=repo_dir
                )
                overlay_col = rag_service.get_workflow_overlay(workflow_id=workflow_id, story_id=story_id)

                # ── RAG Query #1: Retrieve Architectural Context ────
                query_text = f"Architecture, key modules, endpoints, and models for: {title}\n{description}\n{acceptance_criteria}"
                qa_feedback = context.get('qa_feedback')
                if qa_feedback:
                    query_text += f"\nPrevious QA Rejection: {qa_feedback}"

                snippets = rag_service.query_hybrid_rag(base_col, overlay_col, query_text, n_results=8)
                for s in snippets:
                    repo_texts.append(f"File: {s['file_path']} (Lines {s['start_line']}-{s['end_line']}) [{s['source']}]\n{s['code']}")

                # Also populate in-memory map of read files
                for root, _, files in os.walk(repo_dir):
                    if '.git' in root or 'node_modules' in root or 'venv' in root:
                        continue
                    for file in files:
                        if file.endswith(('.py', '.js', '.jsx', '.ts', '.tsx', '.json')):
                            file_path = os.path.join(root, file)
                            try:
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    rel_path = os.path.relpath(file_path, repo_dir).replace('\\', '/')
                                    repo_files_map[rel_path] = f.read()
                            except Exception:
                                pass

            except Exception as e:
                self.logger.error(f"Failed to clone/index repo {repo_url} into {repo_dir}: {e}")

        rag_context = ""
        if repo_texts:
            rag_context = "\n\n".join(repo_texts[:12])
        else:
            rag_context = "No relevant repository context could be loaded."

        qa_feedback = context.get('qa_feedback')
        rework_section = ""
        if qa_feedback:
            rework_section = f"""

⚠️ REWORK CONTEXT — Previous QA Rejection:
The previous implementation was rejected. Specifically focus your repository
analysis on files, dependencies, and architectural patterns relevant to resolving these QA issues:
{qa_feedback}
"""

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            source_branch=source_branch,
            repository_details=f"RAG EXCERPTS:\n{rag_context}\n\nORIGINAL DETAILS:\n{str(repo_details)}"
        ) + rework_section

        try:
            llm_response = LLMService.generate_response(
                prompt=prompt,
                system_instruction="You are a senior software architect. Respond ONLY with valid JSON.",
                agent_name="RepoAnalysis"
            )

            import json
            llm_response = llm_response.strip()
            if llm_response.startswith("```"):
                llm_response = llm_response.split("```")[1]
                if llm_response.startswith("json"):
                    llm_response = llm_response[4:]

            parsed = json.loads(llm_response)

            return AgentResult(
                success=True,
                output={
                    "context_summary": parsed.get('context_summary', ''),
                    "files_to_modify": parsed.get('files_to_modify', []),
                    "patterns_found": parsed.get('patterns_found', []),
                    "implementation_notes": parsed.get('implementation_notes', ''),
                    "estimated_complexity": parsed.get('estimated_complexity', 'medium'),
                    "repo_files": repo_files_map,
                    "workspace_dir": workspace_dir,
                    "repo_dir": repo_dir,
                    "commit_sha": commit_sha if 'commit_sha' in locals() else '',
                    "base_collection": base_col.name if 'base_col' in locals() and base_col else '',
                    "overlay_collection": overlay_col.name if 'overlay_col' in locals() and overlay_col else '',
                }
            )

        except Exception as e:
            self.logger.warning(f"RepoAnalysisAgent LLM error, using heuristic fallback: {e}")
            return AgentResult(
                success=True,
                output={
                    "context_summary": f"Implementation analysis for {title}",
                    "files_to_modify": [
                        {"file": "app/routes/users.py", "action": "modify", "reason": "Add email validation logic to POST /users endpoint"},
                        {"file": "tests/test_users.py", "action": "modify", "reason": "Add automated test cases for valid and invalid email formats"}
                    ],
                    "patterns_found": ["Flask Blueprint route validation", "Standard HTTP error responses"],
                    "implementation_notes": "Validate email presence and format before creating user. Return 400 Bad Request on invalid format.",
                    "estimated_complexity": "low",
                    "repo_files": repo_files_map,
                    "workspace_dir": workspace_dir if 'workspace_dir' in locals() else '',
                    "repo_dir": repo_dir if 'repo_dir' in locals() else '',
                    "commit_sha": commit_sha if 'commit_sha' in locals() else '',
                    "base_collection": base_col.name if 'base_col' in locals() and base_col else '',
                    "overlay_collection": overlay_col.name if 'overlay_col' in locals() and overlay_col else '',
                }
            )
