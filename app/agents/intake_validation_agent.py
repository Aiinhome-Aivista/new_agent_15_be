"""
IntakeValidationAgent — Step 1
Validates that a story has all mandatory DEVAA fields before allowing a run.
"""
from app.agents.base_agent import BaseAgent, AgentResult
from app.services.llm_service import LLMService


MANDATORY_FIELDS = ['title', 'description', 'acceptance_criteria', 'source_branch', 'repository_details', 'assignee_id']

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

        # ── Auto-recovery for fields commonly missing in Jira-synced tasks ───
        # 1. source_branch fallback
        curr_branch = getattr(story, 'source_branch', None) if hasattr(story, 'source_branch') else (story.get('source_branch') if isinstance(story, dict) else None)
        if not curr_branch:
            default_branch = self.config.get('GITHUB_DEFAULT_BASE_BRANCH', 'main')
            repo_det = getattr(story, 'repository_details', None) if hasattr(story, 'repository_details') else (story.get('repository_details') if isinstance(story, dict) else None)
            if isinstance(repo_det, list) and len(repo_det) > 0 and isinstance(repo_det[0], dict) and repo_det[0].get('branch'):
                default_branch = repo_det[0].get('branch')
            if hasattr(story, 'source_branch'):
                story.source_branch = default_branch
            elif isinstance(story, dict):
                story['source_branch'] = default_branch

        # 2. acceptance_criteria: auto-extract if embedded in description
        curr_ac = getattr(story, 'acceptance_criteria', None) if hasattr(story, 'acceptance_criteria') else (story.get('acceptance_criteria') if isinstance(story, dict) else None)
        if not curr_ac:
            desc = getattr(story, 'description', '') if hasattr(story, 'description') else (story.get('description', '') if isinstance(story, dict) else '')
            if desc:
                import re
                ac_match = re.search(
                    r'(?:Acceptance Criteria|ACs?)\s*[:\n\-]+(.*?)(?=(?:\n\s*(?:Technical Constraints|Constraints|Expected Outcome|Notes|Expected Output)|$))',
                    desc,
                    re.IGNORECASE | re.DOTALL
                )
                if not ac_match:
                    ac_match = re.search(r'((?:AC\d+|AC\s*\d+)[\s\S]*)', desc, re.IGNORECASE)
                if ac_match:
                    found_ac = ac_match.group(1).strip()
                    if hasattr(story, 'acceptance_criteria'):
                        story.acceptance_criteria = found_ac
                    elif isinstance(story, dict):
                        story['acceptance_criteria'] = found_ac

        # 3. title fallback if generic
        curr_title = getattr(story, 'title', '') if hasattr(story, 'title') else (story.get('title', '') if isinstance(story, dict) else '')
        if curr_title and (curr_title.lower().startswith('task ') or curr_title.lower().startswith('scrum-')):
            desc = getattr(story, 'description', '') if hasattr(story, 'description') else (story.get('description', '') if isinstance(story, dict) else '')
            import re
            t_match = re.search(r'(?:^|\n)\s*Title\s*:\s*([^\n\r]+)', desc, re.IGNORECASE)
            if t_match and t_match.group(1).strip():
                new_t = t_match.group(1).strip()
                if hasattr(story, 'title'):
                    story.title = new_t
                elif isinstance(story, dict):
                    story['title'] = new_t

        # 4. repository_details fallback if URL empty
        curr_repo = getattr(story, 'repository_details', None) if hasattr(story, 'repository_details') else (story.get('repository_details') if isinstance(story, dict) else None)
        default_repo_url = self.config.get('GITHUB_BASE_URL', '')
        if not curr_repo or (isinstance(curr_repo, list) and len(curr_repo) > 0 and isinstance(curr_repo[0], dict) and not curr_repo[0].get('url')):
            target_repo = [{"name": "main-repo", "url": default_repo_url, "branch": self.config.get('GITHUB_DEFAULT_BASE_BRANCH', 'main')}]
            if hasattr(story, 'repository_details'):
                story.repository_details = target_repo
            elif isinstance(story, dict):
                story['repository_details'] = target_repo

        # Commit auto-filled fields to DB if story is a DB model
        if hasattr(self, 'db') and hasattr(self.db, 'session'):
            try:
                self.db.session.commit()
            except Exception as e:
                self.logger.warning(f"Could not persist auto-filled story fields: {e}")

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
                system_instruction="You are a strict validation agent. Respond ONLY with valid JSON.",
                agent_name="IntakeValidation"
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
