"""
RepoAnalysisAgent — Step 2
Analyzes the codebase context from ALL mentioned repositories (max 2) and builds
an implementation map. Each file in the map carries a `target_repo` field so that
DeveloperAgent and BranchPRAgent can route changes to the correct repository.

Cascade Detection: After LLM builds the file list, we grep for usages of the
changed symbols across the same repo and auto-append impacted files.

Clarification Trigger: If LLM signals low confidence, we store a
StoryClarification record and return needs_clarification=True so the
Orchestrator can pause and ask the PO.
"""
import os
import re
import json
import subprocess
from app.agents.base_agent import BaseAgent, AgentResult
from app.services.llm_service import LLMService


ANALYSIS_PROMPT_TEMPLATE = """You are a DEVAA Repository Analysis Agent. Analyze the following story and ALL repository contexts to build a concrete implementation map.

STORY:
Title: {title}
Description: {description}
Acceptance Criteria: {acceptance_criteria}
Source Branch: {source_branch}

TARGET REPOSITORIES (max 2):
{repo_names_list}

REPOSITORY CODE CONTEXT (RAG excerpts from all repos):
{rag_context}

ORIGINAL REPOSITORY DETAILS:
{repository_details}

CRITICAL ARCHITECTURAL RULES:
1. EXISTING API vs NEW API SEPARATION:
   - If an existing API/endpoint is specifically requested to be altered: set action to "modify", retain all other existing functions/endpoints untouched.
   - If the requested API/endpoint DOES NOT exist: set action to "create" with a new dedicated file.
   - DO NOT modify or overwrite existing files/APIs unless Acceptance Criteria explicitly demands it.
2. PRESERVATION MANDATE: Zero unauthorized deletions. All preexisting routes, models, and utility functions must remain intact.
3. MULTI-REPO MANDATE: For every file in files_to_modify, you MUST set `target_repo` to the exact repository name (from TARGET REPOSITORIES above) where that file resides. If all files are in one repo, still include the `target_repo` field.
4. REPOSITORY AWARENESS: Carefully examine the root files/structure provided for each repository to deduce its technology stack (e.g., package.json implies JS/TS/Frontend/Node, requirements.txt implies Python/Backend). Ensure code is assigned to the repository that matches its language and framework.
5. CONFIDENCE: Set `confidence` to "low" only if you genuinely cannot determine where a change belongs. In that case, populate `clarification_question` and `clarification_options`.

Respond in this exact JSON format:
{{
  "context_summary": "Brief description of what this story is building and the technical approach",
  "api_classification": "existing_api_modification|new_api_creation|hybrid",
  "confidence": "high|medium|low",
  "clarification_question": null,
  "clarification_options": null,
  "files_to_modify": [
    {{
      "file": "path/to/file.py",
      "target_repo": "repo-name-here",
      "action": "create|modify|delete",
      "reason": "Why this file needs to change and how existing code is preserved"
    }}
  ],
  "patterns_found": [
    "Description of any existing code patterns that should be followed"
  ],
  "implementation_notes": "Key technical decisions and constraints the Developer Agent must follow, including strict preservation of existing APIs",
  "estimated_complexity": "low|medium|high"
}}

Respond ONLY with the JSON object."""


def _detect_cascade_files(repo_dir: str, changed_files: list, repo_name: str, logger) -> list:
    """
    Scans the repository for files that import or reference the symbols
    defined in `changed_files`. Returns a list of additional file entries
    that should also be modified (cascade).
    """
    cascade = []
    already_listed = {f['file'] for f in changed_files if f.get('target_repo') == repo_name}

    for entry in changed_files:
        if entry.get('target_repo') != repo_name:
            continue
        rel_file = entry.get('file', '')
        if not rel_file:
            continue

        # Extract defined symbols (Python functions/classes or JS exports)
        full_path = os.path.join(repo_dir, rel_file.replace('/', os.sep))
        if not os.path.exists(full_path):
            continue

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as fh:
                src = fh.read()
        except Exception:
            continue

        symbols = re.findall(r'(?:def|class)\s+([a-zA-Z_][a-zA-Z0-9_]+)', src)
        # JS/TS named exports
        symbols += re.findall(r'export\s+(?:function|const|class)\s+([a-zA-Z_][a-zA-Z0-9_]+)', src)
        if not symbols:
            continue

        # Grep other files in the same repo for usages
        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', 'venv', '__pycache__', '.pytest_cache')]
            for fname in files:
                if not fname.endswith(('.py', '.js', '.jsx', '.ts', '.tsx')):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, repo_dir).replace('\\', '/')
                if rel in already_listed or rel == rel_file:
                    continue
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='replace') as fh2:
                        content = fh2.read()
                except Exception:
                    continue
                for sym in symbols:
                    if sym in content:
                        cascade.append({
                            'file': rel,
                            'target_repo': repo_name,
                            'action': 'modify',
                            'reason': f'Auto-detected cascade: imports/uses `{sym}` from {rel_file}',
                            '_cascade': True
                        })
                        already_listed.add(rel)
                        logger.info(f'[RepoAnalysis] Cascade detected: {rel} uses {sym} from {rel_file}')
                        break

    return cascade


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
        story_id = story.id if hasattr(story, 'id') else (story.get('id', 0) if isinstance(story, dict) else 0)
        workflow_id = context.get('workflow_id', 0)

        repo_details = story.repository_details if hasattr(story, 'repository_details') else story.get('repository_details', [])
        if isinstance(repo_details, str):
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

        # Cap at 2 repos
        repo_details = repo_details[:2]

        # ── Guardrail: allowed prefixes ──────────────────────────────
        raw_prefixes = self.config.get('ALLOWED_REPO_PREFIXES', [])
        if isinstance(raw_prefixes, str):
            allowed_prefixes = [p.strip() for p in raw_prefixes.split(',') if p.strip()]
        elif isinstance(raw_prefixes, list):
            allowed_prefixes = [str(p).strip() for p in raw_prefixes if str(p).strip()]
        else:
            allowed_prefixes = []

        repo_texts = []
        repo_files_map = {}

        from flask import current_app
        from app.config.settings import Config
        from app.services.rag_service import RagService
        from app.services.token_secret_service import TokenSecretService

        workspace_dir = context.get('workspace_dir')
        if not workspace_dir:
            workspaces_root = getattr(Config, 'WORKSPACES_DIR', os.path.abspath(
                os.path.join(current_app.root_path, '..', 'workspaces')))
            workspace_dir = os.path.join(workspaces_root, f"wf_{workflow_id}_{story_id}")
        os.makedirs(workspace_dir, exist_ok=True)

        target_repo_dir = None
        target_commit_sha = None
        target_base_col = None
        target_overlay_col = None
        reference_collections = []

        # Map: repo_name -> repo_dir (for cascade detection)
        repo_dir_map = {}

        # Collect repo names for prompt
        repo_name_entries = []

        rag_service = RagService.get_instance()
        query_text = f"Architecture, key modules, endpoints, and models for: {title}\n{description}\n{acceptance_criteria}"
        qa_feedback = context.get('qa_feedback')
        if qa_feedback:
            query_text += f"\nPrevious QA Rejection: {qa_feedback}"

        for repo in repo_details:
            repo_url = repo.get('url')
            if not repo_url and repo.get('name'):
                repo_url = TokenSecretService.get_url_for_repo(repo.get('name'))
            if not repo_url:
                continue

            # Guardrail check
            if allowed_prefixes and not any(repo_url.startswith(p) for p in allowed_prefixes):
                self.logger.warning(f"Repo {repo_url} not in ALLOWED_REPO_PREFIXES — skipping.")
                continue

            # Derive repo name from URL
            repo_name = repo.get('name') or 'primary-repo'
            if 'github.com' in repo_url:
                parts = repo_url.split('github.com/')[-1].replace('.git', '').strip('/').split('/')
                if len(parts) >= 2:
                    repo_name = parts[1]

            # Repo dir setup
            repo_dir = os.path.join(workspace_dir, repo_name)
            repo_dir_map[repo_name] = repo_dir

            try:
                github_token = TokenSecretService.get_token_for_repo(repo_url) or self.config.get('GITHUB_TOKEN')
                clone_url = repo_url
                if github_token and "github.com" in repo_url:
                    clone_url = repo_url.replace("https://github.com/", f"https://x-access-token:{github_token}@github.com/")

                target_branch = source_branch or repo.get('branch') or self.config.get('GITHUB_DEFAULT_BASE_BRANCH', 'main')

                if os.path.exists(os.path.join(repo_dir, '.git')):
                    self.logger.info(f"Using existing workspace at '{repo_dir}' for repo '{repo_name}'")
                    subprocess.run(['git', 'remote', 'set-url', 'origin', clone_url], cwd=repo_dir, check=True)
                    subprocess.run(['git', 'fetch', 'origin'], cwd=repo_dir, check=True)
                    subprocess.run(['git', 'checkout', target_branch], cwd=repo_dir, capture_output=True)
                    subprocess.run(['git', 'reset', '--hard', f'origin/{target_branch}'], cwd=repo_dir, capture_output=True)
                else:
                    self.logger.info(f"Cloning '{repo_name}' into workspace '{repo_dir}'...")
                    import shutil
                    if os.path.exists(repo_dir):
                        shutil.rmtree(repo_dir, ignore_errors=True)
                    clone_cmd = ['git', 'clone', '--depth', '50']
                    if target_branch:
                        clone_cmd.extend(['-b', target_branch])
                    clone_cmd.extend([clone_url, repo_dir])
                    try:
                        subprocess.check_call(clone_cmd)
                    except subprocess.CalledProcessError:
                        self.logger.warning(f"Branch '{target_branch}' not found, falling back to default clone.")
                        subprocess.check_call(['git', 'clone', '--depth', '50', clone_url, repo_dir])

                # ── Build/reuse RAG index ────────────────────────────
                git_meta = rag_service.get_repo_metadata(repo_dir)
                commit_sha = git_meta.get("full_commit_sha", "")

                base_col = rag_service.ensure_base_index(
                    repo_url=repo_url,
                    branch=target_branch,
                    commit_sha=commit_sha,
                    repo_dir=repo_dir
                )
                overlay_col = rag_service.get_workflow_overlay(workflow_id=workflow_id, story_id=story_id)

                if target_repo_dir is None or not repo.get('is_reference'):
                    target_repo_dir = repo_dir
                    target_commit_sha = commit_sha
                    target_base_col = base_col
                    target_overlay_col = overlay_col
                else:
                    reference_collections.append(base_col.name)

                # ── Determine Repo Context ──
                root_files = []
                if os.path.exists(repo_dir):
                    root_files = [f for f in os.listdir(repo_dir) if os.path.isfile(os.path.join(repo_dir, f)) and not f.startswith('.')]
                
                tech_indicators = []
                if 'package.json' in root_files: tech_indicators.append('Node/JS/TS/Frontend')
                if 'requirements.txt' in root_files or 'pyproject.toml' in root_files: tech_indicators.append('Python/Backend')
                if 'pom.xml' in root_files or 'build.gradle' in root_files: tech_indicators.append('Java')
                
                context_str = f"- {repo_name} ({repo_url})"
                if tech_indicators:
                    context_str += f" [Tech Stack: {' & '.join(tech_indicators)}]"
                elif root_files:
                    context_str += f" [Root Files: {', '.join(root_files[:5])}]"
                    
                repo_name_entries.append(context_str)

                # ── RAG Query per repo ───────────────────────────────
                snippets = rag_service.query_hybrid_rag(base_col, overlay_col, query_text, n_results=8)
                for s in snippets:
                    repo_texts.append(f"[{repo_name}] File: {s['file_path']} (Lines {s['start_line']}-{s['end_line']}) [{s['source']}]\n{s['code']}")

                # ── In-memory file map ───────────────────────────────
                for root, dirs, files in os.walk(repo_dir):
                    dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', 'venv', '__pycache__')]
                    for file in files:
                        if file.endswith(('.py', '.js', '.jsx', '.ts', '.tsx', '.json')):
                            file_path = os.path.join(root, file)
                            rel_path = os.path.relpath(file_path, repo_dir).replace('\\', '/')
                            key = f"[{repo_name}] {rel_path}"
                            try:
                                with open(file_path, 'r', encoding='utf-8', errors='replace') as fh:
                                    repo_files_map[key] = fh.read()
                            except Exception:
                                pass

            except Exception as e:
                self.logger.error(f"Failed to clone/index repo '{repo_name}': {e}")

        rag_context = "\n\n".join(repo_texts[:12]) if repo_texts else "No relevant repository context could be loaded."

        rework_section = ""
        if qa_feedback:
            rework_section = f"""

⚠️ REWORK CONTEXT — Previous QA Rejection:
Focus your analysis on resolving these QA issues:
{qa_feedback}
"""

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            source_branch=source_branch,
            repo_names_list="\n".join(repo_name_entries) or "- primary-repo",
            rag_context=rag_context,
            repository_details=str(repo_details)
        ) + rework_section

        try:
            llm_response = LLMService.generate_response(
                prompt=prompt,
                system_instruction="You are a senior software architect. Respond ONLY with valid JSON.",
                agent_name="RepoAnalysis"
            )

            llm_response = llm_response.strip()
            if llm_response.startswith("```"):
                llm_response = llm_response.split("```")[1]
                if llm_response.startswith("json"):
                    llm_response = llm_response[4:]

            parsed = json.loads(llm_response)

            # ── Clarification Trigger ────────────────────────────────
            confidence = parsed.get('confidence', 'high')
            if confidence == 'low' and parsed.get('clarification_question'):
                self.logger.info(f"[RepoAnalysis] Low confidence — triggering clarification for story {story_id}")
                try:
                    from app import db
                    from app.models.devaa_models import StoryClarification
                    clarif = StoryClarification(
                        story_id=story_id,
                        workflow_id=workflow_id,
                        question=parsed['clarification_question'],
                        options=parsed.get('clarification_options'),
                        status='pending'
                    )
                    db.session.add(clarif)
                    db.session.commit()
                except Exception as ce:
                    self.logger.warning(f"[RepoAnalysis] Could not save clarification record: {ce}")

                return AgentResult(
                    success=True,
                    output={
                        "needs_clarification": True,
                        "clarification_question": parsed['clarification_question'],
                        "clarification_options": parsed.get('clarification_options'),
                        "workspace_dir": workspace_dir,
                        "repo_dir": target_repo_dir,
                    }
                )

            files_to_modify = parsed.get('files_to_modify', [])

            # ── Cascade Detection: per-repo ─────────────────────────
            for repo_name, repo_dir in repo_dir_map.items():
                cascade_files = _detect_cascade_files(repo_dir, files_to_modify, repo_name, self.logger)
                # Only add files not already listed
                existing = {f['file'] for f in files_to_modify}
                for cf in cascade_files:
                    if cf['file'] not in existing:
                        files_to_modify.append(cf)

            return AgentResult(
                success=True,
                output={
                    "context_summary": parsed.get('context_summary', ''),
                    "files_to_modify": files_to_modify,
                    "patterns_found": parsed.get('patterns_found', []),
                    "implementation_notes": parsed.get('implementation_notes', ''),
                    "estimated_complexity": parsed.get('estimated_complexity', 'medium'),
                    "needs_clarification": False,
                    "repo_files": repo_files_map,
                    "workspace_dir": workspace_dir,
                    "repo_dir": target_repo_dir or (list(repo_dir_map.values())[0] if repo_dir_map else ''),
                    "repo_dir_map": repo_dir_map,
                    "commit_sha": target_commit_sha or '',
                    "base_collection": target_base_col.name if target_base_col else '',
                    "overlay_collection": target_overlay_col.name if target_overlay_col else '',
                    "reference_collections": reference_collections,
                    "repo_context_list": repo_name_entries
                }
            )

        except Exception as e:
            self.logger.error(f"RepoAnalysisAgent analysis failed: {e}")
            return AgentResult(
                success=False,
                error=f"RepoAnalysisAgent failed to analyze repository and requirements: {e}"
            )
