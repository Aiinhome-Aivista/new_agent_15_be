"""
TokenSecretService — Repo-wise GitHub PAT Token Manager
Loads Personal Access Tokens (PATs) from config/repo_tokens.json.
Falls back to default_pat, then GITHUB_TOKEN from .env.

Secret file format (backend/config/repo_tokens.json):
{
    "default_pat": "ghp_xxxx",
    "repositories": {
        "org/repo-name": "ghp_repo_specific_token",
        "https://github.com/org/other-repo.git": "ghp_another_token"
    }
}

SECURITY: config/repo_tokens.json is in .gitignore and MUST NOT be committed.
"""
import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Path to the secret tokens file (relative to backend root)
_TOKENS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config",
    "repo_tokens.json"
)

_tokens_cache: Optional[dict] = None


def _load_tokens() -> dict:
    """Load and cache the repo_tokens.json file. Returns empty dict if not found."""
    global _tokens_cache
    if _tokens_cache is not None:
        return _tokens_cache

    if not os.path.exists(_TOKENS_FILE):
        logger.debug(
            f"TokenSecretService: repo_tokens.json not found at {_TOKENS_FILE}. "
            "Using GITHUB_TOKEN from .env as fallback."
        )
        _tokens_cache = {}
        return _tokens_cache

    try:
        with open(_TOKENS_FILE, "r", encoding="utf-8") as f:
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
    def get_token_for_repo(repo_url_or_name: str) -> Optional[str]:
        """
        Resolve the GitHub PAT for a given repository URL or name.

        Resolution order:
        1. Exact match of repo_url_or_name in repositories dict
        2. Normalised org/name match (strips https://github.com/ and .git)
        3. default_pat from repo_tokens.json
        4. GITHUB_TOKEN from Flask app config
        5. GITHUB_TOKEN environment variable

        Returns the resolved token string, or None if no token is configured.
        """
        tokens_data = _load_tokens()
        repositories = tokens_data.get("repositories") or {}

        if repo_url_or_name:
            # 1. Exact match
            if repo_url_or_name in repositories:
                logger.debug(f"TokenSecretService: Exact match for '{repo_url_or_name}'")
                return repositories[repo_url_or_name]

            # 2. Normalised org/name match
            normalised = TokenSecretService._normalise(repo_url_or_name)
            for key, token in repositories.items():
                if TokenSecretService._normalise(key) == normalised:
                    logger.debug(f"TokenSecretService: Normalised match '{key}' for '{repo_url_or_name}'")
                    return token

        # 3. default_pat from file
        default_pat = tokens_data.get("default_pat", "").strip()
        if default_pat and not default_pat.startswith("ghp_xxx"):
            logger.debug("TokenSecretService: Using default_pat from repo_tokens.json")
            return default_pat

        # 4. GITHUB_TOKEN from Flask app config
        try:
            from flask import current_app
            flask_token = current_app.config.get("GITHUB_TOKEN", "").strip()
            if flask_token:
                logger.debug("TokenSecretService: Using GITHUB_TOKEN from Flask app config")
                return flask_token
        except Exception:
            pass

        # 5. GITHUB_TOKEN from environment variable
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

