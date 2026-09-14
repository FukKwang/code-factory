from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from ..config import get_settings


def build_test_writer() -> Agent:
    settings = get_settings()
    return Agent(
        settings.model_test_writer,
        output_type=str,
        model_settings=ModelSettings(max_tokens=settings.max_tokens),
        instructions="""\
Write test assertions for Monty sandbox code.

Given a spec, write Python test code that validates the solution's output.
Tests must be simple assert statements that check the `result` variable.

Rules:
- `result` is the variable set by solution code
- Use only assert statements
- Test structure: assert isinstance(result, dict), assert "key" in result, etc.
- Test expected types, required keys, value ranges
- No test frameworks. No imports. Just assert statements.
- Return ONLY Python code. No markdown, no explanations.""",
        name="test_writer",
    )
