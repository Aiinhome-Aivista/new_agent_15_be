"""
DeveloperAgent — Step 3
Uses the implementation map from RepoAnalysisAgent to generate code changes.
Can receive QA feedback for rework cycles.
"""
from app.agents.base_agent import BaseAgent, AgentResult
from app.services.llm_service import LLMService


DEVELOPER_PROMPT_TEMPLATE = """You are a senior full-stack software engineer.
Implement the required changes for the following story adhering strictly to the architecture and patterns of the repository.

STORY:
Title: {title}
Description: {description}
Acceptance Criteria: {acceptance_criteria}

IMPLEMENTATION MAP:
Context: {context_summary}
Files to Modify: {files_to_modify}
Patterns to Follow: {patterns_found}
Implementation Notes: {implementation_notes}

{existing_code_section}

{reference_section}

{rework_section}

INSTRUCTIONS:
1. STRICT REAL CODE MANDATE: Zero placeholders, zero ellipsis (...), zero mock comments (such as '# rest of code here'), and zero fictitious file names.
2. EXISTING API vs NEW API IMPLEMENTATION MANDATE:
   - If modifying an existing file: You MUST preserve 100% of all preexisting endpoints, routes, helper functions, classes, imports, and docstrings. Do NOT omit or remove any preexisting code. Cleanly integrate the requested updates alongside existing code.
   - If building a new API/feature: Create a new dedicated file/module (or cleanly register the new route) as indicated by the Implementation Map. DO NOT alter or wipe out existing APIs.
   - DELETION RULE: Never delete or remove any preexisting functions or routes unless the story Acceptance Criteria explicitly and specifically commands their deprecation/deletion.
3. For every file being created or modified, provide the `full_content` field containing the COMPLETE, 100% PRODUCTION-READY source code for the entire file (including all preserved existing code).
4. Strictly fulfill every Acceptance Criterion and add real automated test cases covering valid scenarios, error handling, edge cases, and regression checks.
5. ALWAYS update (or create) the target repository's README.md with a `## Changelog & Recent Updates` entry detailing the feature, new endpoints/components, and usage examples.

Respond in this exact JSON format:
{{
  "summary": "Detailed summary of all changes made",
  "changes": [
    {{
      "file": "path/to/file.py",
      "action": "create|modify|delete",
      "description": "Explanation of changes made",
      "code_snippet": "Key diff or function modified",
      "full_content": "The COMPLETE working source code of the entire file",
      "satisfies_criteria": ["criterion 1", "criterion 2"]
    }}
  ],
  "total_files_changed": 2,
  "ready_for_validation": true
}}

Respond ONLY with the JSON object."""

REWORK_SECTION_TEMPLATE = """
⚠️ REWORK CYCLE — QA REJECTION FEEDBACK (Iteration {iteration}):
The previous implementation was rejected by QA with these comments:
{qa_feedback}

You MUST address ALL of the above QA comments in this implementation.
"""


class DeveloperAgent(BaseAgent):
    agent_name = "Developer"

    def _execute(self, context: dict) -> AgentResult:
        story = context.get('story')
        impl_map = context.get('implementation_map', {})
        qa_feedback = context.get('qa_feedback')
        loop_iteration = context.get('loop_iteration', 1)

        if not story:
            return AgentResult(success=False, error="No story provided to DeveloperAgent.")

        title = story.title if hasattr(story, 'title') else story.get('title', '')
        description = story.description if hasattr(story, 'description') else story.get('description', '')
        acceptance_criteria = story.acceptance_criteria if hasattr(story, 'acceptance_criteria') else story.get('acceptance_criteria', '')

        rework_section = ""
        if qa_feedback:
            rework_section = REWORK_SECTION_TEMPLATE.format(
                iteration=loop_iteration,
                qa_feedback=qa_feedback
            )

        # ── RAG Query #2: Targeted Snippets for Files to Modify ──
        existing_code_section = ""
        try:
            from app.services.rag_service import RagService
            rag_service = RagService.get_instance()

            base_col_name = impl_map.get('base_collection')
            overlay_col_name = impl_map.get('overlay_collection')

            base_col = rag_service.client.get_collection(base_col_name) if base_col_name else None
            overlay_col = rag_service.client.get_collection(overlay_col_name) if overlay_col_name else None

            target_files = [f.get('file', '') for f in impl_map.get('files_to_modify', []) if f.get('file')]
            query_dev = f"Functions, routes, schemas, and models for: {' '.join(target_files)} {title} {acceptance_criteria}"
            if qa_feedback:
                query_dev += f"\nAddress QA Feedback: {qa_feedback}"

            snippets = rag_service.query_hybrid_rag(base_col, overlay_col, query_dev, n_results=6)
            if snippets:
                existing_code_section = "EXISTING REPOSITORY CODE CONTEXT (Hybrid RAG: Overlay > Base):\n"
                for s in snippets:
                    existing_code_section += f"\n--- FILE: {s['file_path']} (Lines {s['start_line']}-{s['end_line']}) [{s['source']}] ---\n{s['code']}\n"
        except Exception as e:
            self.logger.warning(f"[DeveloperAgent] RAG Query #2 failed, using raw repo_files fallback: {e}")

        # Fallback to in-memory files if RAG retrieved nothing
        if not existing_code_section:
            repo_files = impl_map.get('repo_files', {})
            if repo_files:
                existing_code_section = "EXISTING REPOSITORY CODE CONTEXT:\n"
                for fpath, fcontent in repo_files.items():
                    if fpath.endswith(('.py', '.js', '.ts', '.html', '.json')) and len(fcontent) < 4000:
                        existing_code_section += f"\n--- FILE: {fpath} ---\n{fcontent}\n"

        # ── RAG Query for Reference Sources (Jira Attachments, Git Links, KB) ──
        reference_section = ""
        reference_collection_name = context.get('reference_collection')
        story_id_val = story.id if hasattr(story, 'id') else (story.get('id') if isinstance(story, dict) else None)
        if not reference_collection_name and story_id_val:
            reference_collection_name = f"story_refs_{story_id_val}"
        try:
            if reference_collection_name:
                from app.services.reference_source_service import ReferenceSourceService
                ref_query = f"{title} {acceptance_criteria} {description}"[:500]
                ref_results = ReferenceSourceService.query_references(
                    story_id=story_id_val or 0,
                    query=ref_query,
                    n_results=5
                )
                if ref_results:
                    reference_section = "\n## REFERENCE SOURCES (Jira Attachments, Git Links, Knowledge Base):\n"
                    for ref in ref_results:
                        src_type = ref.get('source_type', 'reference')
                        fname = ref.get('filename', '')
                        text = ref.get('text', '')
                        reference_section += f"\n--- [{src_type.upper()}] {fname} ---\n{text[:1500]}\n"
        except Exception as ref_err:
            self.logger.warning(f"[DeveloperAgent] Reference source query failed (non-fatal): {ref_err}")

        prompt = DEVELOPER_PROMPT_TEMPLATE.format(

            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            context_summary=impl_map.get('context_summary', 'No context available'),
            files_to_modify=str(impl_map.get('files_to_modify', [])),
            patterns_found=str(impl_map.get('patterns_found', [])),
            implementation_notes=impl_map.get('implementation_notes', ''),
            existing_code_section=existing_code_section,
            reference_section=reference_section,
            rework_section=rework_section
        )

        try:
            llm_response = LLMService.generate_response(
                prompt=prompt,
                system_instruction="You are a senior software developer. Respond ONLY with valid JSON.",
                agent_name="Developer"
            )

            import json
            llm_response = llm_response.strip()
            if llm_response.startswith("```"):
                llm_response = llm_response.split("```")[1]
                if llm_response.startswith("json"):
                    llm_response = llm_response[4:]

            parsed = json.loads(llm_response)

            summary = parsed.get('summary', '')
            changes = parsed.get('changes', [])

            # Write changes directly to isolated disk workspace with Code Preservation Guard
            import os, re
            repo_dir = impl_map.get('repo_dir') or context.get('workspace_dir')
            if repo_dir and os.path.exists(repo_dir):
                for c in changes:
                    rf = c.get('file')
                    action = c.get('action', 'modify').lower()
                    if not rf:
                        continue
                    full_p = os.path.join(repo_dir, rf)
                    if action == 'delete':
                        if os.path.exists(full_p):
                            try:
                                os.remove(full_p)
                                self.logger.info(f"[DeveloperAgent] Deleted file per action: {rf}")
                            except Exception:
                                pass
                    elif action in ('create', 'modify'):
                        cnt = c.get('full_content') or c.get('code_snippet')
                        if cnt:
                            # ── Preservation Guard for existing files ──
                            if action == 'modify' and os.path.exists(full_p) and rf.endswith(('.py', '.js', '.ts')):
                                try:
                                    with open(full_p, 'r', encoding='utf-8', errors='replace') as f_old:
                                        old_text = f_old.read()
                                    if rf.endswith('.py'):
                                        old_defs = set(re.findall(r'(?:def|class)\s+([a-zA-Z0-9_]+)\s*[\(:]', old_text))
                                        new_defs = set(re.findall(r'(?:def|class)\s+([a-zA-Z0-9_]+)\s*[\(:]', cnt))
                                        missing_defs = old_defs - new_defs
                                        if missing_defs:
                                            self.logger.warning(
                                                f"[DeveloperAgent] Preservation Notice: Function/class definitions {missing_defs} "
                                                f"not found in updated {rf}. Verifying story scope."
                                            )
                                except Exception as guard_err:
                                    self.logger.warning(f"[DeveloperAgent] Preservation check warning: {guard_err}")

                            os.makedirs(os.path.dirname(full_p), exist_ok=True)
                            with open(full_p, 'w', encoding='utf-8') as f:
                                f.write(cnt)
            
            # ── README.md Fail-Safe ────────────────────────────────────────────
            # Guarantee that README.md is always updated, even if LLM omitted it
            readme_in_changes = any(
                'readme' in (c.get('file') or '').lower()
                for c in changes
            )
            if not readme_in_changes and repo_dir and os.path.exists(repo_dir):
                try:
                    changelog_entry = (
                        f"\n\n## Changelog & Recent Updates\n\n"
                        f"### {title}\n\n"
                        f"{description[:600] if description else 'No description provided.'}\n\n"
                        f"**Acceptance Criteria:**\n{acceptance_criteria[:400] if acceptance_criteria else 'N/A'}\n"
                    )
                    readme_path = os.path.join(repo_dir, 'README.md')
                    if os.path.exists(readme_path):
                        with open(readme_path, 'r', encoding='utf-8', errors='replace') as f_in:
                            existing_readme = f_in.read()
                    else:
                        existing_readme = f"# {title}\n"
                    new_readme = existing_readme.rstrip() + changelog_entry
                    with open(readme_path, 'w', encoding='utf-8') as f_out:
                        f_out.write(new_readme)
                    changes.append({
                        "file": "README.md",
                        "action": "modify",
                        "description": f"Auto-generated changelog entry for: {title}",
                        "code_snippet": changelog_entry,
                        "full_content": new_readme,
                        "satisfies_criteria": ["README documentation updated"]
                    })
                    self.logger.info(f"[DeveloperAgent] README.md auto-updated for story: {title}")
                except Exception as readme_err:
                    self.logger.warning(f"[DeveloperAgent] README.md update failed: {readme_err}")

            # Metric: pr_summary_matches_diff (semantic filename check)

            pr_summary_matches_diff = 0.0
            actual_files = [c.get('file') for c in changes if c.get('file')]
            if actual_files and summary:
                matches = sum(1 for f in actual_files if f in summary)
                pr_summary_matches_diff = float(matches) / len(actual_files)
                
                try:
                    from app.services.metrics_service import MetricsService
                    workflow_id = context.get('workflow_id')
                    story_id = story.id if hasattr(story, 'id') else (story.get('id') if isinstance(story, dict) else None)
                    if workflow_id and story_id:
                        MetricsService.record_metrics(
                            workflow_id=workflow_id,
                            story_id=story_id,
                            metrics={"pr_summary_matches_diff": pr_summary_matches_diff}
                        )
                except Exception as metric_err:
                    self.logger.error(f"Failed to record metric pr_summary_matches_diff: {metric_err}")

            return AgentResult(
                success=True,
                output={
                    "summary": summary,
                    "changes": changes,
                    "total_files_changed": parsed.get('total_files_changed', len(changes)),
                    "ready_for_validation": parsed.get('ready_for_validation', True),
                    "loop_iteration": loop_iteration,
                    "repo_dir": repo_dir
                }
            )

        except Exception as e:
            self.logger.error(f"DeveloperAgent code generation failed: {e}")
            return AgentResult(
                success=False,
                error=f"DeveloperAgent failed to generate code for '{title}': {e}"
            )
