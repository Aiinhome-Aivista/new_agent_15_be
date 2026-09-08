"""
InputRails — validates and sanitizes all agent inputs before execution.
"""
import re
import logging

logger = logging.getLogger(__name__)

# Common prompt injection patterns
INJECTION_PATTERNS = [
    r"ignore previous instructions",
    r"disregard.*system",
    r"you are now",
    r"pretend you are",
    r"act as",
    r"jailbreak",
    r"DAN mode",
    r"override.*instructions",
]


class InputRails:

    @staticmethod
    def validate(context: dict, workflow_id=None, config=None) -> dict:
        """
        Run all input checks. Returns {'passed': bool, 'reason': str}.
        """
        config = config or {}

        # ── 1. Prompt injection check ──────────────────────────────
        text_inputs = []
        story = context.get('story')
        if story:
            if hasattr(story, 'description'):
                text_inputs.append(story.description or '')
                text_inputs.append(story.acceptance_criteria or '')
            elif isinstance(story, dict):
                text_inputs.append(story.get('description', ''))
                text_inputs.append(story.get('acceptance_criteria', ''))

        for text in text_inputs:
            if not text:
                continue
            for pattern in INJECTION_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    return {
                        'passed': False,
                        'reason': f"Prompt injection pattern detected: '{pattern}'"
                    }

        # ── 2. Repo URL allow-list check ──────────────────────────
        allowed_prefixes = config.get('ALLOWED_REPO_PREFIXES', [])
        if allowed_prefixes and story:
            repo_details = (
                story.repository_details if hasattr(story, 'repository_details')
                else story.get('repository_details', [])
            ) or []
            if isinstance(repo_details, list):
                for repo in repo_details:
                    url = repo.get('url', '') if isinstance(repo, dict) else ''
                    if url and not any(url.startswith(p) for p in allowed_prefixes):
                        return {
                            'passed': False,
                            'reason': f"Repository URL '{url}' not in allowed list."
                        }

        return {'passed': True, 'reason': ''}
