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

        # ── RAG LOGIC ──────────────────────────────────────────────
        allowed_prefixes = self.config.get('ALLOWED_REPO_PREFIXES', '').split(',')
        allowed_prefixes = [p.strip() for p in allowed_prefixes if p.strip()]

        repo_texts = []
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

            # Clone
            with tempfile.TemporaryDirectory() as temp_dir:
                try:
                    github_token = self.config.get('GITHUB_TOKEN')
                    clone_url = repo_url
                    if github_token and "github.com" in repo_url:
                        clone_url = repo_url.replace("https://github.com/", f"https://oauth2:{github_token}@github.com/")

                    target_branch = source_branch or repo.get('branch') or self.config.get('GITHUB_DEFAULT_BASE_BRANCH', 'main')
                    clone_cmd = ['git', 'clone', '--depth', '1']
                    if target_branch:
                        clone_cmd.extend(['-b', target_branch])
                    clone_cmd.extend([clone_url, temp_dir])

                    try:
                        subprocess.check_call(clone_cmd)
                    except subprocess.CalledProcessError:
                        self.logger.warning(f"Branch '{target_branch}' not found on remote, falling back to default clone.")
                        subprocess.check_call(['git', 'clone', '--depth', '1', clone_url, temp_dir])
                    
                    # Read files
                    for root, _, files in os.walk(temp_dir):
                        if '.git' in root or 'node_modules' in root or 'venv' in root:
                            continue
                        for file in files:
                            if file.endswith(('.py', '.js', '.jsx', '.ts', '.tsx', '.md', '.html', '.css')):
                                file_path = os.path.join(root, file)
                                try:
                                    with open(file_path, 'r', encoding='utf-8') as f:
                                        content = f.read()
                                        rel_path = os.path.relpath(file_path, temp_dir)
                                        repo_texts.append(f"File: {rel_path}\n{content}")
                                except Exception:
                                    pass
                except Exception as e:
                    self.logger.error(f"Failed to clone/read repo {repo_url}: {e}")

        rag_context = ""
        if repo_texts:
            try:
                from langchain.text_splitter import RecursiveCharacterTextSplitter
                from langchain_community.embeddings import HuggingFaceEmbeddings
                from langchain_community.vectorstores import Chroma
                
                text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
                docs = text_splitter.create_documents(repo_texts)
                
                embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
                vectorstore = Chroma.from_documents(documents=docs, embedding=embeddings)
                
                query = f"{title}\n{description}\n{acceptance_criteria}"
                if context.get('qa_feedback'):
                    query += f"\nQA REJECTION FEEDBACK:\n{context.get('qa_feedback')}"
                relevant_docs = vectorstore.similarity_search(query, k=15)
                rag_context = "\n\n".join([doc.page_content for doc in relevant_docs])
            except Exception as e:
                self.logger.error(f"RAG embedding failed: {e}")
                rag_context = "RAG processing failed."
        else:
            rag_context = "No relevant repository context could be loaded (check ALLOWED_REPO_PREFIXES or token)."

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
                    "estimated_complexity": parsed.get('estimated_complexity', 'medium')
                }
            )

        except Exception as e:
            self.logger.error(f"RepoAnalysisAgent LLM error: {e}")
            return AgentResult(success=False, error=f"Repository analysis failed: {str(e)}")
