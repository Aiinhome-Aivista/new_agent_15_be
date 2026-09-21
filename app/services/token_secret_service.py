"""
TokenSecretService — Repo-wise GitHub PAT Token & Repository Manager
Loads Personal Access Tokens (PATs) and repository configurations from config/repo_tokens.json.
Supports both repository aliases (e.g. "python_devva_api") and direct clone URLs.

Secret file format (backend/config/repo_tokens.json):
{
    "default_pat": "ghp_default_fallback_token",
    "repositories": {
        "python_devva_api": {
            "url": "https://github.com/org/python-devaa-api.git",
            "token": "ghp_specific_pat_for_python_api",
            "default_branch": "main"
        },
        "frontend_react_app": {
            "url": "https://github.com/org/frontend-react.git",
            "token": "ghp_specific_pat_for_frontend",
            "default_branch": "develop"
        },
        "org/legacy-repo": "ghp_simple_string_token"
    }
}

Resolution Rules:
1. Lookup by repository name / alias (e.g., "python_devva_api").
2. Lookup by repository URL or normalized org/repo path.
3. Fallback to "default_pat" in repo_tokens.json.
4. Final fallback to GITHUB_TOKEN in backend/.env.

SECURITY: config/repo_tokens.json is in .gitignore and MUST NOT be committed to git.
"""
import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Path to the secret tokens file (relative to backend root)
def _get_tokens_file_path() -> Optional[str]:
    """Resolve repo_tokens.json across backend config, backend root, or workspace root."""
    base_be = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    candidates = [
        os.path.join(base_be, "config", "repo_tokens.json"),
        os.path.join(base_be, "repo_tokens.json"),
        os.path.abspath(os.path.join(base_be, "..", "repo_tokens.json"))
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

_tokens_cache: Optional[dict] = None


def _load_tokens() -> dict:
    """Load and cache the repo_tokens.json file. Returns empty dict if not found."""
    global _tokens_cache
    if _tokens_cache is not None:
        return _tokens_cache

    tokens_file = _get_tokens_file_path()
    if not tokens_file:
        logger.debug(
            "TokenSecretService: repo_tokens.json not found in candidate paths. "
            "Using GITHUB_TOKEN from .env as fallback."
        )
        _tokens_cache = {}
        return _tokens_cache

    try:
        with open(tokens_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        _tokens_cache = data if isinstance(data, dict) else {}
        logger.info(
            f"TokenSecretService: Loaded {len((_tokens_cache.get('repositories') or {}))} "
            "repo-specific PAT tokens from config/repo_tokens.json"
        )
    except Exception as e:
        logger.error(f"TokenSecretService: Failed to load repo_tokens.json: {e}")
        _tokens_cache = {}

    return _tokens_cache


class TokenSecretService:

    @staticmethod
    def get_repo_config(repo_url_or_name: str) -> Optional[dict]:
        """
        Get complete repository configuration (url, token, default_branch) by alias/name or URL.
        Supports both dict format and simple token string in repo_tokens.json.
        """
        if not repo_url_or_name:
            return None

        tokens_data = _load_tokens()
        repositories = tokens_data.get("repositories") or {}

        # 1. Exact match
        raw_val = repositories.get(repo_url_or_name)
        matched_key = repo_url_or_name if raw_val is not None else None

        # 2. Case-insensitive match on name
        if raw_val is None:
            lower_target = repo_url_or_name.strip().lower()
            for k, v in repositories.items():
                if k.strip().lower() == lower_target:
                    raw_val = v
                    matched_key = k
                    break

        # 3. Normalised match on key (strips github.com/ and .git)
        if raw_val is None:
            normalised = TokenSecretService._normalise(repo_url_or_name)
            for k, v in repositories.items():
                if TokenSecretService._normalise(k) == normalised:
                    raw_val = v
                    matched_key = k
                    break

        # 4. Match against 'url' field inside dict entry
        if raw_val is None:
            normalised = TokenSecretService._normalise(repo_url_or_name)
            for k, v in repositories.items():
                if isinstance(v, dict) and v.get("url"):
                    if v.get("url") == repo_url_or_name or TokenSecretService._normalise(v.get("url")) == normalised:
                        raw_val = v
                        matched_key = k
                        break

        if raw_val is not None:
            if isinstance(raw_val, dict):
                return {
                    "name": raw_val.get("name") or matched_key,
                    "url": raw_val.get("url") or (matched_key if ("github.com" in matched_key or matched_key.startswith("http")) else None),
                    "token": raw_val.get("token") or raw_val.get("pat"),
                    "branch": raw_val.get("default_branch") or raw_val.get("branch") or "main"
                }
            elif isinstance(raw_val, str):
                is_url = "github.com" in matched_key or matched_key.startswith("http")
                return {
                    "name": matched_key,
                    "url": matched_key if is_url else None,
                    "token": raw_val,
                    "branch": "main"
                }

        return None

    @staticmethod
    def get_url_for_repo(repo_url_or_name: str) -> Optional[str]:
        """
        Resolve git clone URL for a given repository alias/name.
        Returns None if not configured.
        """
        cfg = TokenSecretService.get_repo_config(repo_url_or_name)
        if cfg and cfg.get("url"):
            return cfg.get("url")
        # If repo_url_or_name already is a URL, return it
        if repo_url_or_name and ("github.com" in repo_url_or_name or repo_url_or_name.startswith("http") or repo_url_or_name.startswith("git@")):
            return repo_url_or_name
        return None

    @staticmethod
    def get_token_for_repo(repo_url_or_name: str, is_reference: bool = False) -> Optional[str]:
        """
        Resolve the GitHub PAT for a given repository URL or name.

        Resolution order (Works 100% with or without repo_tokens.json):
        1. Exact/normalised match in repositories dict in repo_tokens.json (if present)
        2. Environment variable for specific repo: GITHUB_TOKEN_<REPO_SLUG> (e.g. GITHUB_TOKEN_REFERENCE_STRUCTURE_API)
        3. REFERENCE_GITHUB_TOKEN from environment if is_reference=True or 'reference' in repo URL
        4. default_pat from repo_tokens.json (if present)
        5. GITHUB_TOKEN from Flask app config / environment variable

        Returns the resolved token string, or None if no token is configured.
        """
        cfg = TokenSecretService.get_repo_config(repo_url_or_name)
        if cfg and cfg.get("token"):
            logger.debug(f"TokenSecretService: Found token for '{repo_url_or_name}' via repo config")
            return cfg.get("token")

        # Check repo-slug environment variable (e.g. GITHUB_TOKEN_REFERENCE_STRUCTURE_API)
        if repo_url_or_name:
            norm = TokenSecretService._normalise(repo_url_or_name)
            repo_slug = norm.split('/')[-1].replace('-', '_').replace('.', '_').upper()
            env_specific_key = f"GITHUB_TOKEN_{repo_slug}"
            env_specific_token = os.environ.get(env_specific_key, "").strip()
            if env_specific_token:
                logger.debug(f"TokenSecretService: Found token via environment variable '{env_specific_key}'")
                return env_specific_token

        # Check REFERENCE_GITHUB_TOKEN if is_reference is True or URL indicates reference
        if is_reference or (repo_url_or_name and ("ref" in repo_url_or_name.lower() or "reference" in repo_url_or_name.lower())):
            ref_env_token = os.environ.get("REFERENCE_GITHUB_TOKEN", "").strip()
            if ref_env_token:
                logger.debug("TokenSecretService: Using REFERENCE_GITHUB_TOKEN from environment variable")
                return ref_env_token

        tokens_data = _load_tokens()

        # default_pat from file
        default_pat = tokens_data.get("default_pat", "").strip()
        if default_pat and not default_pat.startswith("ghp_xxx"):
            logger.debug("TokenSecretService: Using default_pat from repo_tokens.json")
            return default_pat

        # GITHUB_TOKEN from Flask app config
        try:
            from flask import current_app
            flask_token = current_app.config.get("GITHUB_TOKEN", "").strip()
            if flask_token:
                logger.debug("TokenSecretService: Using GITHUB_TOKEN from Flask app config")
                return flask_token
        except Exception:
            pass

        # GITHUB_TOKEN from environment variable
        env_token = os.environ.get("GITHUB_TOKEN", "").strip()
        if env_token:
            logger.debug("TokenSecretService: Using GITHUB_TOKEN from environment variable")
            return env_token

        logger.warning(
            f"TokenSecretService: No PAT token found for repo '{repo_url_or_name}'. "
            "Configure config/repo_tokens.json or set GITHUB_TOKEN in .env."
        )
        return None

    @staticmethod
    def _normalise(repo_url_or_name: str) -> str:
        """
        Normalise a repo URL or name to 'org/repo-name' format for matching.
        Examples:
            'https://github.com/org/repo.git' -> 'org/repo'
            'org/repo' -> 'org/repo'
        """
        s = (repo_url_or_name or "").strip().rstrip("/")
        if "github.com/" in s:
            s = s.split("github.com/")[-1]
        s = s.replace(".git", "").strip("/")
        return s.lower()

    @staticmethod
    def reload():
        """Force reload of the tokens cache (e.g., after updating the file)."""
        global _tokens_cache
        _tokens_cache = None
        logger.info("TokenSecretService: Tokens cache cleared — will reload on next call.")

