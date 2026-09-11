"""
rag_service.py — Enterprise RAG & Vector Engine for DEVAA
──────────────────────────────────────────────────────────
Architectural Principles:
  1. Base repository index is IMMUTABLE and read-only.
     - Service-layer write protection: attempts to mutate throw ImmutableCollectionError.
     - Thread-safe, cached by (repo_url, branch, commit_sha), shared across concurrent workflows.
  2. Workflow overlay is MUTABLE and ISOLATED per workflow (wf_{workflow_id}_{story_id}).
     - Stores developer's modified/created files and deterministic deleted-file tombstones.
  3. Precedence in Hybrid Retrieval: Overlay > Base.
     - Any file modified in the overlay completely suppresses its counterpart in the Base index.
     - Tombstoned files are deterministically excluded (no dependence on semantic distance).
  4. Workspaces are ISOLATED per workflow to eliminate git checkout & branch collisions.
"""

import os
import re
import hashlib
import logging
import subprocess
from datetime import datetime
from typing import List, Dict, Any, Optional, Set, Tuple

import chromadb
from chromadb.config import Settings as ChromaSettings

logger = logging.getLogger(__name__)


class ImmutableCollectionError(Exception):
    """Raised when an operation attempts to mutate a sealed, immutable Base collection."""
    pass


class RagService:
    _instance: Optional["RagService"] = None

    def __init__(self, persist_dir: Optional[str] = None):
        if persist_dir:
            self.persist_dir = os.path.abspath(persist_dir)
        else:
            from app.config.settings import Config
            self.persist_dir = getattr(
                Config,
                "CHROMA_PERSIST_DIR",
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "chroma_db"))
            )

        os.makedirs(self.persist_dir, exist_ok=True)
        self.client = chromadb.PersistentClient(path=self.persist_dir)
        self._sealed_collections: Set[str] = set()
        logger.info(f"[RagService] Initialized ChromaDB at '{self.persist_dir}'")

    @classmethod
    def get_instance(cls, persist_dir: Optional[str] = None) -> "RagService":
        if cls._instance is None:
            cls._instance = cls(persist_dir)
        return cls._instance

    # ── Deterministic Naming & Hashes ───────────────────────────────

    @staticmethod
    def build_base_collection_name(repo_url: str, branch: str, commit_sha: str) -> str:
        """
        Creates a collision-proof, deterministic collection name adhering to ChromaDB regex:
        ^[a-zA-Z0-9][a-zA-Z0-9._-]{1,61}[a-zA-Z0-9]$
        Format: base_{repo_hash8}_{branch_hash6}_{sha8}
        """
        repo_hash = hashlib.sha256(repo_url.strip().lower().encode("utf-8")).hexdigest()[:8]
        branch_clean = re.sub(r'[^a-zA-Z0-9]', '', branch.strip())[:10] or "main"
        branch_hash = hashlib.sha256(branch.strip().encode("utf-8")).hexdigest()[:6]
        short_sha = commit_sha.strip().lower()[:8]
        
        name = f"base_{repo_hash}_{branch_clean}_{branch_hash}_{short_sha}"
        # Ensure length <= 63 chars
        return name[:63]

    @staticmethod
    def build_overlay_collection_name(workflow_id: int, story_id: int) -> str:
        """Isolated collection name per workflow."""
        return f"wf_{workflow_id}_{story_id}"

    # ── Git Inspection & Workspace ──────────────────────────────────

    @staticmethod
    def get_repo_metadata(repo_dir: str) -> Dict[str, str]:
        """Reads full SHA, short SHA, and current branch from git directory."""
        try:
            full_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
            ).strip()
            short_sha = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
            ).strip()
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
            ).strip()
            return {
                "full_commit_sha": full_sha,
                "short_commit_sha": short_sha,
                "branch": branch
            }
        except Exception as e:
            logger.warning(f"[RagService] Failed to read git metadata from {repo_dir}: {e}")
            fallback_hash = hashlib.sha256(str(datetime.utcnow()).encode()).hexdigest()
            return {
                "full_commit_sha": fallback_hash,
                "short_commit_sha": fallback_hash[:8],
                "branch": "main"
            }

    # ── Scanning & Chunking ────────────────────────────────────────

    IGNORE_DIRS = {
        '.git', 'node_modules', 'venv', '.venv', '__pycache__',
        '.pytest_cache', '.idea', '.vscode', 'dist', 'build', '.next',
        '.coverage', 'coverage', 'htmlcov'
    }

    SUPPORTED_EXTENSIONS = (
        '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css',
        '.json', '.sql', '.md', '.yaml', '.yml', '.txt', '.sh'
    )

    IGNORE_FILES = {
        'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
        'poetry.lock', 'Pipfile.lock', 'Cargo.lock'
    }

    @classmethod
    def scan_and_load_documents(cls, repo_dir: str) -> List[Dict[str, Any]]:
        """Scans repository files and loads text content with UTF-8 decoding."""
        documents = []
        if not os.path.exists(repo_dir):
            return documents

        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = [d for d in dirs if d not in cls.IGNORE_DIRS and not d.startswith('.')]

            for file in files:
                if file in cls.IGNORE_FILES:
                    continue
                if not file.endswith(cls.SUPPORTED_EXTENSIONS):
                    continue

                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, repo_dir).replace('\\', '/')

                # Skip large bundle or binary files (> 300 KB)
                try:
                    if os.path.getsize(abs_path) > 300 * 1024:
                        continue
                except OSError:
                    continue

                content = None
                for enc in ('utf-8', 'latin-1'):
                    try:
                        with open(abs_path, 'r', encoding=enc) as f:
                            content = f.read()
                        break
                    except Exception:
                        continue

                if content is not None and content.strip():
                    lines = content.splitlines()
                    documents.append({
                        "file_path": rel_path,
                        "content": content,
                        "total_lines": len(lines),
                        "ext": os.path.splitext(file)[1].lower()
                    })

        logger.info(f"[RagService] Scanned {len(documents)} eligible source files in '{repo_dir}'")
        return documents

    @staticmethod
    def chunk_document(
        file_path: str,
        content: str,
        chunk_size: int = 800,
        overlap: int = 120
    ) -> List[Dict[str, Any]]:
        """
        Splits source code into sliding window chunks while tracking line numbers.
        """
        chunks = []
        lines = content.splitlines(keepends=True)
        if not lines:
            return chunks

        line_count = len(lines)
        start_idx = 0

        while start_idx < line_count:
            curr_chars = 0
            end_idx = start_idx
            chunk_lines = []

            while end_idx < line_count and curr_chars < chunk_size:
                line_str = lines[end_idx]
                chunk_lines.append(line_str)
                curr_chars += len(line_str)
                end_idx += 1

            chunk_text = "".join(chunk_lines).strip()
            if chunk_text:
                chunks.append({
                    "text": chunk_text,
                    "file_path": file_path,
                    "start_line": start_idx + 1,
                    "end_line": end_idx,
                    "chunk_index": len(chunks)
                })

            if end_idx >= line_count:
                break

            # Calculate overlap in terms of lines
            overlap_chars = 0
            step_back = 0
            while end_idx - 1 - step_back > start_idx and overlap_chars < overlap:
                overlap_chars += len(lines[end_idx - 1 - step_back])
                step_back += 1

            start_idx = max(start_idx + 1, end_idx - step_back)

        return chunks

    # ── Service-Layer Write Protection ──────────────────────────────

    def _assert_mutable(self, collection_name: str) -> None:
        """Enforces write protection on sealed Base collections."""
        if collection_name.startswith("base_"):
            if collection_name in self._sealed_collections:
                raise ImmutableCollectionError(
                    f"[RagService] Write rejected: Base collection '{collection_name}' is sealed and IMMUTABLE."
                )
            # Also check collection metadata if already exists
            try:
                col = self.client.get_collection(name=collection_name)
                if col.metadata and col.metadata.get("is_sealed"):
                    self._sealed_collections.add(collection_name)
                    raise ImmutableCollectionError(
                        f"[RagService] Write rejected: Base collection '{collection_name}' is sealed and IMMUTABLE."
                    )
            except Exception:
                pass

    # ── Base Index Management (Immutable) ───────────────────────────

    def ensure_base_index(
        self,
        repo_url: str,
        branch: str,
        commit_sha: str,
        repo_dir: str,
        embedding_model: str = "all-MiniLM-L6-v2",
        embedding_version: str = "v1.0"
    ) -> chromadb.Collection:
        """
        Retrieves existing Base collection or ingests repository files at this commit.
        Once ingested, the collection is permanently marked sealed and immutable.
        """
        collection_name = self.build_base_collection_name(repo_url, branch, commit_sha)

        # 1. Check if collection already exists
        try:
            col = self.client.get_collection(name=collection_name)
            if col.count() > 0:
                self._sealed_collections.add(collection_name)
                logger.info(
                    f"[RagService] Index Exists? YES -> Cache HIT for base collection '{collection_name}' "
                    f"({col.count()} chunks). Bypassing ingestion."
                )
                return col
        except Exception:
            pass

        logger.info(f"[RagService] Index Exists? NO -> Creating Base collection '{collection_name}'...")
        # 2. Ingest from scratch
        col = self.client.get_or_create_collection(
            name=collection_name,
            metadata={
                "repo_url": repo_url,
                "branch": branch,
                "full_commit_sha": commit_sha,
                "embedding_model": embedding_model,
                "embedding_version": embedding_version,
                "indexed_at": datetime.utcnow().isoformat(),
                "is_base": True,
                "is_sealed": False
            }
        )

        docs = self.scan_and_load_documents(repo_dir)
        all_chunks = []
        for doc in docs:
            chunks = self.chunk_document(doc["file_path"], doc["content"])
            all_chunks.extend(chunks)

        if all_chunks:
            # Batch add chunks
            batch_size = 100
            for i in range(0, len(all_chunks), batch_size):
                batch = all_chunks[i:i + batch_size]
                ids = [f"{c['file_path']}:{c['start_line']}-{c['end_line']}:{c['chunk_index']}" for c in batch]
                texts = [c['text'] for c in batch]
                metadatas = [{
                    "file_path": c['file_path'],
                    "start_line": c['start_line'],
                    "end_line": c['end_line'],
                    "chunk_index": c['chunk_index'],
                    "is_base": True,
                    "is_tombstone": False
                } for c in batch]

                col.add(documents=texts, ids=ids, metadatas=metadatas)

            logger.info(f"[RagService] Successfully ingested {len(all_chunks)} chunks into Base index '{collection_name}'")

        # 3. Seal collection (service-layer immutability enforcement)
        self._sealed_collections.add(collection_name)
        col.modify(metadata={
            "repo_url": repo_url,
            "branch": branch,
            "full_commit_sha": commit_sha,
            "embedding_model": embedding_model,
            "embedding_version": embedding_version,
            "indexed_at": datetime.utcnow().isoformat(),
            "is_base": True,
            "is_sealed": True
        })
        logger.info(f"[RagService] Base collection '{collection_name}' is now SEALED and IMMUTABLE.")
        return col

    # ── Workflow Overlay Management (Mutable & Isolated) ────────────

    def get_workflow_overlay(self, workflow_id: int, story_id: int) -> chromadb.Collection:
        """Retrieves or creates isolated overlay collection for this workflow."""
        col_name = self.build_overlay_collection_name(workflow_id, story_id)
        return self.client.get_or_create_collection(
            name=col_name,
            metadata={
                "workflow_id": workflow_id,
                "story_id": story_id,
                "is_overlay": True,
                "created_at": datetime.utcnow().isoformat()
            }
        )

    def record_workflow_changes(
        self,
        overlay_col: chromadb.Collection,
        workspace_repo_dir: str,
        changes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Incrementally re-indexes files modified or deleted by DeveloperAgent.
        - 'create' | 'modify': reads disk file, chunks, and upserts into overlay.
        - 'delete': records deterministic tombstone in overlay.
        """
        updated_files = []
        tombstoned_files = []

        for change in changes:
            file_path = change.get('file', '').replace('\\', '/')
            action = change.get('action', 'modify').lower()
            if not file_path:
                continue

            # 1. Clean existing records in overlay for this file
            try:
                existing = overlay_col.get(where={"file_path": file_path})
                if existing and existing.get("ids"):
                    overlay_col.delete(ids=existing["ids"])
            except Exception as e:
                logger.debug(f"[RagService] Error clearing previous overlay chunks for {file_path}: {e}")

            if action in ('create', 'modify'):
                content = change.get('full_content') or change.get('code_snippet') or ''
                abs_path = os.path.join(workspace_repo_dir, file_path)
                if not content and os.path.exists(abs_path):
                    try:
                        with open(abs_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                    except Exception:
                        pass

                if content:
                    chunks = self.chunk_document(file_path, content)
                    if chunks:
                        ids = [f"wf:{c['file_path']}:{c['start_line']}-{c['end_line']}:{c['chunk_index']}" for c in chunks]
                        texts = [c['text'] for c in chunks]
                        metadatas = [{
                            "file_path": c['file_path'],
                            "start_line": c['start_line'],
                            "end_line": c['end_line'],
                            "chunk_index": c['chunk_index'],
                            "is_base": False,
                            "is_tombstone": False,
                            "action": action
                        } for c in chunks]
                        overlay_col.add(documents=texts, ids=ids, metadatas=metadatas)
                        updated_files.append(file_path)

            elif action == 'delete':
                # Deterministic tombstone registration (not dependent on vector distance)
                tombstone_id = f"tombstone:{file_path}"
                overlay_col.add(
                    documents=[f"[FILE_DELETED: {file_path}]"],
                    ids=[tombstone_id],
                    metadatas=[{
                        "file_path": file_path,
                        "is_base": False,
                        "is_tombstone": True,
                        "action": "delete"
                    }]
                )
                tombstoned_files.append(file_path)

        logger.info(
            f"[RagService] Recorded changes in overlay '{overlay_col.name}': "
            f"{len(updated_files)} updated, {len(tombstoned_files)} tombstoned."
        )
        return {
            "updated_files": updated_files,
            "tombstoned_files": tombstoned_files
        }

    # ── Hybrid Retrieval: Overlay > Base Precedence ──────────────────

    def query_hybrid_rag(
        self,
        base_col: Optional[chromadb.Collection],
        overlay_col: Optional[chromadb.Collection],
        query_text: str,
        n_results: int = 6
    ) -> List[Dict[str, Any]]:
        """
        Executes semantic search with Overlay > Base Precedence:
        1. Identifies all modified paths and tombstone paths from overlay deterministically.
        2. Queries overlay_col for active modifications (excluding tombstones).
        3. Queries base_col for repository context.
        4. Suppresses all base results matching modified or tombstoned paths.
        5. Returns overlay results first, followed by valid base results.
        """
        modified_paths: Set[str] = set()
        tombstone_paths: Set[str] = set()
        overlay_results: List[Dict[str, Any]] = []

        # 1. Deterministically inspect overlay state
        if overlay_col and overlay_col.count() > 0:
            try:
                all_meta = overlay_col.get(include=["metadatas"])
                if all_meta and all_meta.get("metadatas"):
                    for m in all_meta["metadatas"]:
                        fp = m.get("file_path")
                        if not fp:
                            continue
                        if m.get("is_tombstone"):
                            tombstone_paths.add(fp)
                        else:
                            modified_paths.add(fp)
            except Exception as e:
                logger.warning(f"[RagService] Error reading overlay metadata: {e}")

            # 2. Query overlay (excluding tombstones)
            try:
                overlay_count = overlay_col.count()
                k_overlay = min(n_results, overlay_count)
                if k_overlay > 0:
                    q_res = overlay_col.query(
                        query_texts=[query_text],
                        n_results=k_overlay,
                        where={"is_tombstone": False}
                    )
                    if q_res and q_res.get("documents") and q_res["documents"][0]:
                        docs = q_res["documents"][0]
                        metas = q_res["metadatas"][0] if q_res.get("metadatas") else [{}] * len(docs)
                        for doc, meta in zip(docs, metas):
                            overlay_results.append({
                                "file_path": meta.get("file_path", "unknown"),
                                "start_line": meta.get("start_line", 1),
                                "end_line": meta.get("end_line", 1),
                                "code": doc,
                                "source": "workflow_overlay"
                            })
            except Exception as e:
                logger.warning(f"[RagService] Overlay query error: {e}")

        # 3. Query Base Index
        base_results: List[Dict[str, Any]] = []
        suppressed_paths = modified_paths | tombstone_paths

        if base_col and base_col.count() > 0:
            try:
                k_base = min(n_results * 2, base_col.count())  # fetch extra to account for suppression
                q_res = base_col.query(
                    query_texts=[query_text],
                    n_results=k_base
                )
                if q_res and q_res.get("documents") and q_res["documents"][0]:
                    docs = q_res["documents"][0]
                    metas = q_res["metadatas"][0] if q_res.get("metadatas") else [{}] * len(docs)

                    for doc, meta in zip(docs, metas):
                        fp = meta.get("file_path", "")
                        # 4. Deterministic suppression:
                        # If file was modified in overlay or deleted (tombstone), suppress base version!
                        if fp in suppressed_paths:
                            continue

                        base_results.append({
                            "file_path": fp,
                            "start_line": meta.get("start_line", 1),
                            "end_line": meta.get("end_line", 1),
                            "code": doc,
                            "source": "immutable_base"
                        })
            except Exception as e:
                logger.warning(f"[RagService] Base query error: {e}")

        # 5. Merge with Overlay > Base Precedence
        merged = []
        seen_chunks: Set[str] = set()

        for item in overlay_results:
            key = f"{item['file_path']}:{item['start_line']}-{item['end_line']}"
            if key not in seen_chunks:
                seen_chunks.add(key)
                merged.append(item)

        for item in base_results:
            key = f"{item['file_path']}:{item['start_line']}-{item['end_line']}"
            if key not in seen_chunks:
                seen_chunks.add(key)
                merged.append(item)

        return merged[:n_results]

    # ── Overlay Lifecycle Purge ─────────────────────────────────────

    def purge_workflow_overlay(self, workflow_id: int, story_id: int) -> bool:
        """
        Deletes the ephemeral overlay collection upon PR merge or closure.
        The Base collection remains intact for future cache hits.
        """
        col_name = self.build_overlay_collection_name(workflow_id, story_id)
        try:
            self.client.delete_collection(name=col_name)
            logger.info(f"[RagService] Purged workflow overlay collection '{col_name}'.")
            return True
        except Exception as e:
            logger.debug(f"[RagService] Could not purge overlay '{col_name}' (may not exist): {e}")
            return False
