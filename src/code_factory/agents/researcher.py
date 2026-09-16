from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from ..config import Settings, get_settings, resolve_model
from ..sandbox.host_functions import HOST_FUNCTION_DESCRIPTIONS


def build_researcher(settings: "Settings | None" = None) -> Agent:
    fn_docs = "\n".join(f"- {desc}" for desc in HOST_FUNCTION_DESCRIPTIONS.values())
    if settings is None:
        settings = get_settings()
    return Agent(
        resolve_model(settings.model_researcher, settings),
        output_type=str,
        model_settings=ModelSettings(max_tokens=min(settings.max_tokens, 2048)),
        instructions=f"""You analyze requirements for a coding task.

Given a user requirement, produce a structured findings document:

1. OBJECTIVE: What the user wants (one sentence)
2. DATA NEEDED: What data must be fetched and from which host functions
3. PROCESSING: What transformations/filters/aggregations are needed
4. OUTPUT FORMAT: What the result should look like
5. HOST FUNCTIONS TO USE: Which ones and with what arguments
6. EDGE CASES: What could go wrong

Available host functions:
{fn_docs}

Be concise. This document will be used to generate a spec and tests.""",
        name="researcher",
    )
