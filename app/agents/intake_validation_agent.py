"""
IntakeValidationAgent — Step 1
Validates that a story has all mandatory DEVAA fields before allowing a run.
"""
from app.agents.base_agent import BaseAgent, AgentResult
from app.services.llm_service import LLMService


MANDATORY_FIELDS = ['title', 'description', 'acceptance_criteria', 'source_branch', 'repository_details']

VALIDATION_PROMPT_TEMPLATE = """You are a DEVAA Intake Validator. Review the following Jira story and determine if it has enough information to begin automated development.

STORY:
Title: {title}
Description: {description}
Acceptance Criteria: {acceptance_criteria}
Source Branch: {source_branch}
Repositories: {repository_details}

Check for:
1. Is the description specific enough to understand what needs to be built?
2. Are the acceptance criteria clear and testable?
3. Is a source branch specified?
4. Are repository details provided?

Respond in this exact JSON format:
{{
  "valid": true or false,
  "missing_or_unclear": ["field1", "field2"],
  "comment": "One paragraph explaining the decision. If invalid, list exactly what is missing."
}}

Respond ONLY with the JSON object, no other text."""


class IntakeValidationAgent(BaseAgent):
    agent_name = "IntakeValidation"

    def _execute(self, context: dict) -> AgentResult:
        story = context.get('story')
        if not story:
            return AgentResult(success=False, error="No story provided to intake agent.")

        # ── Rule-based mandatory field check ─────────────────────
        missing = []
        for field in MANDATORY_FIELDS:
            val = getattr(story, field, None) if hasattr(story, field) else story.get(field)
            if not val:
                missing.append(field)

        if missing:
            comment = f"Story is missing required fields: {', '.join(missing)}. Please complete these before triggering a run."
            return AgentResult(
                success=False,
                output={
                    "valid": False,
                    "missing_fields": missing,
                    "comment": comment,
                    "new_status": "INVALID"
                },
                error=comment
            )

        # ── LLM-based completeness check ──────────────────────────
        title = story.title if hasattr(story, 'title') else story.get('title', '')
        description = story.description if hasattr(story, 'description') else story.get('description', '')
        acceptance_criteria = story.acceptance_criteria if hasattr(story, 'acceptance_criteria') else story.get('acceptance_criteria', '')
        source_branch = story.source_branch if hasattr(story, 'source_branch') else story.get('source_branch', '')
        repo_details = story.repository_details if hasattr(story, 'repository_details') else story.get('repository_details', '')

        prompt = VALIDATION_PROMPT_TEMPLATE.format(
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            source_branch=source_branch,
            repository_details=str(repo_details)
        )

        try:
            llm_response = LLMService.generate_response(
                prompt=prompt,
                system_instruction="You are a strict validation agent. Respond ONLY with valid JSON."
            )

            import json
            # Try to extract JSON from response
            llm_response = llm_response.strip()
            if llm_response.startswith("```"):
                llm_response = llm_response.split("```")[1]
                if llm_response.startswith("json"):
                    llm_response = llm_response[4:]
            
            parsed = json.loads(llm_response)
            is_valid = parsed.get('valid', False)

            return AgentResult(
                success=is_valid,
                output={
                    "valid": is_valid,
                    "missing_or_unclear": parsed.get('missing_or_unclear', []),
                    "comment": parsed.get('comment', ''),
                    "new_status": "IN-PROGRESS" if is_valid else "INVALID"
                },
                error=None if is_valid else parsed.get('comment', 'Validation failed.')
            )

        except Exception as e:
            # If LLM fails, fall back to rule-based (fields were present, so pass)
            self.logger.warning(f"LLM validation failed, falling back to rule-based pass: {e}")
            return AgentResult(
                success=True,
                output={
                    "valid": True,
                    "missing_or_unclear": [],
                    "comment": "All mandatory fields present (LLM check skipped due to error).",
                    "new_status": "IN-PROGRESS"
                }
            )
