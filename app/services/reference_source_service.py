"""
ReferenceSourceService — Multi-Modal Reference Source Parser
Handles:
1. Jira Attachments: .md/.txt (direct text), .docx (paragraph/table extraction),
   Images (.png/.jpg/.jpeg/.webp) — described via Gemini Vision (LLMService)
2. Git Repository Links: fetches referenced files/specs from GitHub using TokenSecretService
3. Knowledge Base Integration: indexes extracted reference texts into ChromaDB
"""
import os
import re
import logging
import requests

logger = logging.getLogger(__name__)


class ReferenceSourceService:

    @classmethod
    def fetch_and_index_references(cls, story, workflow_id: int = None) -> dict:
        """
        Main entry point. Aggregates all reference sources for a story and
        indexes them into ChromaDB collection `story_refs_{story_id}`.

        Returns:
        {
            "sources": [{"type": str, "filename": str, "text": str}, ...],
            "collection_name": "story_refs_<story_id>",
            "indexed_count": int
        }
        """
        story_id = story.id if hasattr(story, 'id') else story.get('id', 0)
        description = story.description if hasattr(story, 'description') else story.get('description', '')
        acceptance_criteria = story.acceptance_criteria if hasattr(story, 'acceptance_criteria') else story.get('acceptance_criteria', '')
        jira_key = story.jira_story_key if hasattr(story, 'jira_story_key') else story.get('jira_story_key', '')

        sources = []

        # 1. Parse Jira attachments
        if jira_key:
            jira_sources = cls._fetch_jira_attachments(jira_key)
            sources.extend(jira_sources)

        # 2. Detect and fetch Git links from description + AC
        combined_text = f"{description or ''}\n{acceptance_criteria or ''}"
        git_sources = cls._fetch_git_links(combined_text)
        sources.extend(git_sources)

        # 3. Index all extracted references into ChromaDB
        collection_name = f"story_refs_{story_id}"
        indexed_count = cls._index_to_chromadb(collection_name, sources)

        logger.info(
            f"[ReferenceSourceService] Story {story_id}: indexed {indexed_count} reference "
            f"chunks from {len(sources)} source(s) into '{collection_name}'"
        )

        return {
            "sources": sources,
            "collection_name": collection_name,
            "indexed_count": indexed_count
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Jira Attachments
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def _fetch_jira_attachments(cls, jira_key: str) -> list:
        """
        Fetch all attachments from a Jira issue and parse each by type.
        Returns list of {type, filename, text} dicts.
        """
        sources = []
        try:
            from app.services.jira_service import JiraService
            issue = JiraService.get_issue(jira_key)
            if not issue:
                return sources

            attachments = (issue.get('fields') or {}).get('attachment') or []
            for att in attachments:
                filename = att.get('filename', '')
                content_url = att.get('content', '')
                mime_type = (att.get('mimeType') or '').lower()

                if not content_url or not filename:
                    continue

                ext = os.path.splitext(filename)[1].lower()
                try:
                    raw = cls._download_attachment(content_url)
                    if raw is None:
                        continue

                    if ext in ('.md', '.txt'):
                        text = cls._parse_text(raw)
                        sources.append({"type": "markdown", "filename": filename, "text": text})

                    elif ext in ('.docx',):
                        text = cls._parse_docx(raw)
                        sources.append({"type": "docx", "filename": filename, "text": text})

                    elif ext in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
                        text = cls._describe_image_via_vision(raw, mime_type or "image/png", filename)
                        sources.append({"type": "image", "filename": filename, "text": text})

                    else:
                        logger.debug(f"[ReferenceSourceService] Skipping unsupported attachment type: {filename}")

                except Exception as att_err:
                    logger.warning(f"[ReferenceSourceService] Failed to process attachment '{filename}': {att_err}")

        except Exception as e:
            logger.warning(f"[ReferenceSourceService] Failed to fetch Jira attachments for {jira_key}: {e}")

        return sources

    @classmethod
    def _download_attachment(cls, url: str) -> bytes:
        """Download an attachment from Jira using the configured auth credentials."""
        try:
            from flask import current_app
            from requests.auth import HTTPBasicAuth
            auth = HTTPBasicAuth(
                current_app.config.get('JIRA_EMAIL', ''),
                current_app.config.get('JIRA_API_TOKEN', '')
            )
            resp = requests.get(url, auth=auth, timeout=30)
            if resp.status_code == 200:
                return resp.content
            logger.warning(f"[ReferenceSourceService] Attachment download returned {resp.status_code}: {url}")
        except Exception as e:
            logger.warning(f"[ReferenceSourceService] Attachment download failed: {e}")
        return None

    @staticmethod
    def _parse_text(raw: bytes) -> str:
        """Parse raw bytes as UTF-8 text (for .md / .txt files)."""
        for encoding in ('utf-8', 'utf-8-sig', 'latin-1'):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode('utf-8', errors='replace')

    @staticmethod
    def _parse_docx(raw: bytes) -> str:
        """
        Extract paragraphs and tables from a .docx file.
        Uses python-docx if available, falls back to raw XML extraction.
        """
        try:
            import io
            from docx import Document
            doc = Document(io.BytesIO(raw))
            parts = []
            for para in doc.paragraphs:
                text = para.text.strip()
                if text:
                    parts.append(text)
            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                    if row_text:
                        parts.append(row_text)
            return "\n".join(parts)
        except ImportError:
            # Fallback: raw XML text extraction (no python-docx)
            import io
            import zipfile
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    with z.open('word/document.xml') as f:
                        xml = f.read().decode('utf-8', errors='replace')
                text = re.sub(r'<[^>]+>', ' ', xml)
                text = re.sub(r'\s+', ' ', text).strip()
                return text
            except Exception:
                return "[DOCX parsing failed — python-docx not installed]"
        except Exception as e:
            logger.warning(f"[ReferenceSourceService] DOCX parse error: {e}")
            return ""

    @staticmethod
    def _describe_image_via_vision(raw: bytes, mime_type: str, filename: str) -> str:
        """
        Use Gemini Vision (LLMService) to generate a detailed technical description
        of a UI mockup, architecture diagram, or flowchart image.
        """
        try:
            from app.services.llm_service import LLMService
            prompt = (
                f"You are analyzing a technical image attachment named '{filename}' from a Jira story. "
                "Describe in detail what this image shows, including: UI components, layout, data flows, "
                "architecture layers, API interactions, or business logic visible in the diagram. "
                "Be specific and technical — your description will be used as context for a software developer "
                "implementing the feature shown in this image."
            )
            description = LLMService.generate_with_image(
                prompt=prompt,
                image_data=raw,
                mime_type=mime_type,
                agent_name="ReferenceSourceService"
            )
            return description or f"[Image: {filename} — vision description unavailable]"
        except Exception as e:
            logger.warning(f"[ReferenceSourceService] Vision description failed for '{filename}': {e}")
            return f"[Image: {filename} — vision description failed: {e}]"

    # ─────────────────────────────────────────────────────────────────────────
    # Git Repository Links
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def _fetch_git_links(cls, text: str) -> list:
        """
        Detect GitHub file/blob links in text and fetch their content.
        Returns list of {type, filename, text} dicts.
        """
        sources = []
        if not text:
            return sources

        # Detect GitHub blob/raw file URLs
        github_pattern = re.compile(
            r'https://github\.com/([^/\s]+)/([^/\s]+)/(?:blob|raw)/([^/\s]+)/([^\s\)>\"\']+)',
            re.IGNORECASE
        )

        seen_urls = set()
        for match in github_pattern.finditer(text):
            org, repo, branch, filepath = match.group(1), match.group(2), match.group(3), match.group(4)
            raw_url = f"https://raw.githubusercontent.com/{org}/{repo}/{branch}/{filepath}"

            if raw_url in seen_urls:
                continue
            seen_urls.add(raw_url)

            try:
                from app.services.token_secret_service import TokenSecretService
                repo_url = f"https://github.com/{org}/{repo}"
                token = TokenSecretService.get_token_for_repo(repo_url)
                headers = {}
                if token:
                    headers["Authorization"] = f"Bearer {token}"

                resp = requests.get(raw_url, headers=headers, timeout=15)
                if resp.status_code == 200:
                    content = resp.text
                    sources.append({
                        "type": "git_link",
                        "filename": f"{org}/{repo}/{filepath}",
                        "text": content[:10000]  # Limit to 10K chars
                    })
                    logger.info(f"[ReferenceSourceService] Fetched git link: {raw_url}")
                else:
                    logger.warning(f"[ReferenceSourceService] Git link fetch returned {resp.status_code}: {raw_url}")
            except Exception as e:
                logger.warning(f"[ReferenceSourceService] Failed to fetch git link {raw_url}: {e}")

        return sources

    # ─────────────────────────────────────────────────────────────────────────
    # ChromaDB Knowledge Base Indexing
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def _index_to_chromadb(cls, collection_name: str, sources: list) -> int:
        """
        Index all extracted reference texts into a ChromaDB collection.
        Returns the number of chunks indexed.
        """
        if not sources:
            return 0

        try:
            from app.services.rag_service import RagService
            rag = RagService.get_instance()

            # Get or create the story refs collection
            try:
                collection = rag.client.get_collection(collection_name)
            except Exception:
                collection = rag.client.create_collection(
                    name=collection_name,
                    metadata={"hnsw:space": "cosine"}
                )

            documents = []
            metadatas = []
            ids = []

            for i, source in enumerate(sources):
                text = (source.get('text') or '').strip()
                if not text:
                    continue

                # Chunk large texts
                chunk_size = 2000
                chunks = [text[j:j + chunk_size] for j in range(0, len(text), chunk_size)]
                for k, chunk in enumerate(chunks):
                    if not chunk.strip():
                        continue
                    documents.append(chunk)
                    metadatas.append({
                        "source_type": source.get("type", "unknown"),
                        "filename": source.get("filename", ""),
                        "chunk_index": k
                    })
                    ids.append(f"{collection_name}_src{i}_chunk{k}")

            if documents:
                collection.add(documents=documents, metadatas=metadatas, ids=ids)
                logger.info(
                    f"[ReferenceSourceService] Indexed {len(documents)} chunks into '{collection_name}'"
                )
            return len(documents)

        except Exception as e:
            logger.error(f"[ReferenceSourceService] ChromaDB indexing failed: {e}")
            return 0

    @classmethod
    def query_references(cls, story_id: int, query: str, n_results: int = 5) -> list:
        """
        Query the story reference collection for relevant context.
        Returns list of {text, source_type, filename} dicts.
        """
        collection_name = f"story_refs_{story_id}"
        try:
            from app.services.rag_service import RagService
            rag = RagService.get_instance()
            try:
                collection = rag.client.get_collection(collection_name)
            except Exception:
                return []

            results = collection.query(query_texts=[query], n_results=min(n_results, 10))
            docs = results.get('documents', [[]])[0]
            metas = results.get('metadatas', [[]])[0]

            return [
                {
                    "text": doc,
                    "source_type": meta.get("source_type", "unknown"),
                    "filename": meta.get("filename", "")
                }
                for doc, meta in zip(docs, metas)
            ]
        except Exception as e:
            logger.warning(f"[ReferenceSourceService] Query failed for '{collection_name}': {e}")
            return []

    # ─────────────────────────────────────────────────────────────────────────
    # Reference Git Knowledge Base & AST Function Harvester
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def index_reference_git_repo(cls, repo_url: str, branch: str = 'main') -> dict:
        """
        Clones a Reference Git repository, parses AST function signatures (Python, JS, TS),
        and indexes the reference knowledge base into a dedicated ChromaDB collection (`ref_kb_<slug>`).
        """
        if not repo_url:
            return {"error": "No reference repository URL provided"}

        import hashlib, subprocess, shutil, ast
        from app.services.token_secret_service import TokenSecretService
        from app.services.rag_service import RagService
        from flask import current_app
        from app.config.settings import Config

        # Normalize slug & collection name
        norm_name = TokenSecretService._normalise(repo_url)
        repo_slug = norm_name.split('/')[-1].replace('-', '_').replace('.', '_')
        collection_name = f"ref_kb_{repo_slug}"

        # Resolve clone workspace
        workspaces_root = getattr(Config, 'WORKSPACES_DIR', os.path.abspath(os.path.join(current_app.root_path, '..', 'workspaces')))
        ref_repos_dir = os.path.join(workspaces_root, "ref_repos")
        os.makedirs(ref_repos_dir, exist_ok=True)
        target_dir = os.path.join(ref_repos_dir, repo_slug)

        token = TokenSecretService.get_token_for_repo(repo_url, is_reference=True)
        clone_url = repo_url
        if token and "github.com" in repo_url:
            clone_url = repo_url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/")

        # Clone or update
        try:
            if os.path.exists(os.path.join(target_dir, '.git')):
                logger.info(f"[ReferenceSourceService] Updating existing reference repo at '{target_dir}'...")
                subprocess.run(['git', 'remote', 'set-url', 'origin', clone_url], cwd=target_dir, check=True)
                subprocess.run(['git', 'fetch', 'origin'], cwd=target_dir, check=True)
                subprocess.run(['git', 'checkout', branch], cwd=target_dir, capture_output=True)
                subprocess.run(['git', 'reset', '--hard', f'origin/{branch}'], cwd=target_dir, capture_output=True)
            else:
                logger.info(f"[ReferenceSourceService] Cloning reference repo '{repo_url}' into '{target_dir}'...")
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir, ignore_errors=True)
                clone_cmd = ['git', 'clone', '--depth', '50']
                if branch:
                    clone_cmd.extend(['-b', branch])
                clone_cmd.extend([clone_url, target_dir])
                try:
                    subprocess.check_call(clone_cmd)
                except subprocess.CalledProcessError:
                    logger.warning(f"Branch '{branch}' not found on remote reference repo, falling back to default branch clone.")
                    subprocess.check_call(['git', 'clone', '--depth', '50', clone_url, target_dir])
        except Exception as e:
            logger.error(f"[ReferenceSourceService] Failed to clone reference repo '{repo_url}': {e}")
            return {"error": f"Failed to clone reference repository: {e}", "collection_name": collection_name}

        # Get commit SHA
        commit_sha = ""
        try:
            res = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=target_dir, capture_output=True, text=True)
            commit_sha = res.stdout.strip()
        except Exception:
            pass

        # Extract function references via AST & regex
        functions = cls.extract_function_references(target_dir)

        # Index into ChromaDB
        rag = RagService.get_instance()
        try:
            collection = rag.client.get_collection(collection_name)
        except Exception:
            collection = rag.client.create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine", "commit_sha": commit_sha, "repo_url": repo_url}
            )

        documents = []
        metadatas = []
        ids = []

        for idx, func in enumerate(functions):
            doc_text = f"Symbol: {func['symbol_name']} ({func['symbol_type']})\nSignature: {func['signature']}\nFile: {func['file_path']}\nDocstring: {func.get('docstring', '')}\nCode:\n{func['code']}"
            documents.append(doc_text)
            metadatas.append({
                "symbol_name": func["symbol_name"],
                "symbol_type": func["symbol_type"],
                "signature": func["signature"],
                "file_path": func["file_path"],
                "language": func["language"],
                "repo_url": repo_url
            })
            ids.append(f"{collection_name}_func_{idx}_{func['symbol_name']}")

        if documents:
            try:
                # Upsert to avoid duplicate ID errors
                collection.upsert(documents=documents, metadatas=metadatas, ids=ids)
            except Exception as e:
                logger.warning(f"[ReferenceSourceService] Collection upsert fallback to add: {e}")
                try:
                    collection.add(documents=documents, metadatas=metadatas, ids=ids)
                except Exception:
                    pass

        logger.info(
            f"[ReferenceSourceService] Indexed {len(functions)} AST function references "
            f"from reference repo '{repo_url}' into collection '{collection_name}'"
        )

        return {
            "repo_url": repo_url,
            "branch": branch,
            "commit_sha": commit_sha,
            "collection_name": collection_name,
            "total_functions": len(functions),
            "functions": functions[:20]  # sample summary
        }

    @classmethod
    def extract_function_references(cls, repo_dir: str) -> list:
        """
        Walks a repository and extracts function/class definitions, signatures, and docstrings
        for Python, JavaScript, and TypeScript files using AST and regex parsing.
        """
        import ast
        functions = []
        skip_dirs = {'.git', 'venv', 'node_modules', '__pycache__', 'dist', 'build', '.idea', '.vscode'}

        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, repo_dir).replace('\\', '/')

                if file.endswith('.py'):
                    cls._extract_python_ast(file_path, rel_path, functions)
                elif file.endswith(('.js', '.jsx', '.ts', '.tsx')):
                    cls._extract_js_ts_functions(file_path, rel_path, functions)

        return functions

    @classmethod
    def _extract_python_ast(cls, file_path: str, rel_path: str, functions: list):
        """Extract Python functions, classes, and routes via AST."""
        import ast
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                code_text = f.read()

            lines = code_text.splitlines()
            tree = ast.parse(code_text, filename=rel_path)

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig_args = [arg.arg for arg in node.args.args]
                    sig = f"def {node.name}({', '.join(sig_args)}):"
                    doc = ast.get_docstring(node) or ""
                    start_line = node.lineno
                    end_line = getattr(node, 'end_lineno', start_line + 20)
                    body_code = "\n".join(lines[start_line - 1:end_line])

                    functions.append({
                        "symbol_name": node.name,
                        "symbol_type": "function",
                        "signature": sig,
                        "docstring": doc,
                        "file_path": rel_path,
                        "code": body_code[:1500],
                        "language": "python"
                    })

                elif isinstance(node, ast.ClassDef):
                    sig = f"class {node.name}:"
                    doc = ast.get_docstring(node) or ""
                    start_line = node.lineno
                    end_line = getattr(node, 'end_lineno', start_line + 30)
                    body_code = "\n".join(lines[start_line - 1:end_line])

                    functions.append({
                        "symbol_name": node.name,
                        "symbol_type": "class",
                        "signature": sig,
                        "docstring": doc,
                        "file_path": rel_path,
                        "code": body_code[:2000],
                        "language": "python"
                    })

        except Exception as e:
            logger.debug(f"[ReferenceSourceService] Could not parse Python AST for '{rel_path}': {e}")

    @classmethod
    def _extract_js_ts_functions(cls, file_path: str, rel_path: str, functions: list):
        """Extract JavaScript / TypeScript functions, classes, and routes via regex pattern matching."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                code_text = f.read()

            lines = code_text.splitlines()

            # Pattern for: function name(...), const name = (...), export function name(...)
            js_func_pattern = re.compile(
                r'(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(([^)]*)\)|'
                r'(?:export\s+)?const\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>|'
                r'(?:router|app)\.(get|post|put|delete|patch)\s*\(\s*[\'\"]([^\'\"]+)[\'\"]'
            )

            for i, line in enumerate(lines):
                match = js_func_pattern.search(line)
                if match:
                    func_name = match.group(1) or match.group(3) or f"{match.group(5).upper()} {match.group(6)}"
                    args = match.group(2) or match.group(4) or ""
                    symbol_type = "route" if match.group(5) else "function"
                    sig = f"function {func_name}({args})" if symbol_type == "function" else f"{match.group(5).upper()} {match.group(6)}"

                    snippet = "\n".join(lines[i:min(i + 25, len(lines))])

                    functions.append({
                        "symbol_name": func_name,
                        "symbol_type": symbol_type,
                        "signature": sig,
                        "docstring": "",
                        "file_path": rel_path,
                        "code": snippet[:1500],
                        "language": "javascript" if rel_path.endswith(('.js', '.jsx')) else "typescript"
                    })

        except Exception as e:
            logger.debug(f"[ReferenceSourceService] Could not parse JS/TS functions for '{rel_path}': {e}")

    @classmethod
    def query_reference_kb(cls, collection_name: str, query: str, n_results: int = 6) -> list:
        """
        Query a reference knowledge base collection for relevant function signatures and code blueprints.
        """
        if not collection_name:
            return []

        try:
            from app.services.rag_service import RagService
            rag = RagService.get_instance()
            try:
                collection = rag.client.get_collection(collection_name)
            except Exception:
                return []

            results = collection.query(query_texts=[query], n_results=min(n_results, 10))
            docs = results.get('documents', [[]])[0]
            metas = results.get('metadatas', [[]])[0]

            out = []
            for doc, meta in zip(docs, metas):
                out.append({
                    "symbol_name": meta.get("symbol_name", ""),
                    "symbol_type": meta.get("symbol_type", "function"),
                    "signature": meta.get("signature", ""),
                    "file_path": meta.get("file_path", ""),
                    "language": meta.get("language", ""),
                    "repo_url": meta.get("repo_url", ""),
                    "code_snippet": doc
                })
            return out
        except Exception as e:
            logger.warning(f"[ReferenceSourceService] Query reference KB failed for '{collection_name}': {e}")
            return []


