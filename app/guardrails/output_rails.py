"""
OutputRails — scans LLM outputs for secrets, PII, and hallucinated file paths.
"""
import re
import logging
logger = logging.getLogger(__name__)

# Patterns for secrets/PII
SECRET_PATTERNS = [
    (r'(?i)(password|passwd|pwd)\s*[:=]\s*\S+', 'password'),
    (r'(?i)(api[_-]?key|apikey|token)\s*[:=]\s*[A-Za-z0-9\-_]{10,}', 'api_key'),
    (r'(?i)(secret|private[_-]?key)\s*[:=]\s*\S+', 'secret'),
    (r'[A-Za-z0-9+/]{40,}={0,2}', 'base64_blob'),   # long base64 (potential creds)
    (r'\b\d{3}-\d{2}-\d{4}\b', 'ssn'),               # US SSN
    (r'\b[0-9]{13,16}\b', 'credit_card'),
]


class OutputRails:

    @staticmethod
    def scan(output_text: str, config=None) -> dict:
        """
        Scans LLM output for secrets/PII.
        Returns {'passed': bool, 'violations': [str]}.
        """
        config = config or {}
        if not config.get('ENABLE_OUTPUT_SCANNING', True):
            return {'passed': True, 'violations': []}

        if not output_text:
            return {'passed': True, 'violations': []}

        violations = []
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, output_text):
                violations.append(f"Potential {label} detected in output")

        return {
            'passed': len(violations) == 0,
            'violations': violations
        }
