from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from ..config import get_settings


def build_reviewer() -> Agent:
    settings = get_settings()
    return Agent(
        settings.model_reviewer,
        output_type=str,
        model_settings=ModelSettings(max_tokens=settings.max_tokens),
        instructions="""\
Review code execution results. Given:
- Original requirement
- Test results (pass/fail)
- Code output

Write a plain-language summary for the user. User does not know code.
Focus on whether output answers the original requirement.
If tests failed, explain what went wrong in simple terms.
Be concise — one paragraph max.""",
        name="reviewer",
    )
