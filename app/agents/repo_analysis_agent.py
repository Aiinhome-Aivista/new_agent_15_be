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

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            source_branch=source_branch,
            repository_details=str(repo_details)
        )

        try:
            llm_response = LLMService.generate_response(
                prompt=prompt,
                system_instruction="You are a senior software architect. Respond ONLY with valid JSON."
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
