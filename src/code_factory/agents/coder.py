from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from ..config import get_settings

MONTY_LIMITATIONS = """\
Write Python for Monty sandbox.

CRITICAL RULES:
1. Assign final output to `result` at module level.
2. Host functions are PRE-DEFINED and available as globals. CALL them directly.
3. NEVER redefine or mock host functions. They already exist in the runtime.
4. NEVER create fake/mock data. Host functions return real data.
5. Read runtime parameters from `inputs` dict (pre-defined global). NEVER hardcode request-specific values like names, IDs, cities.
6. Write GENERALIZED reusable code. Same code runs for any input values.

Example (inputs = {"borrower_name": "ABC"}):
  borrower = query_borrower({"name": inputs["borrower_name"]})
  loans = query_loans({"borrower_id": borrower["id"]})
  result = {"borrower": borrower, "loans": loans}

ALLOWED: def, lambda, dataclass(eq/frozen only), comprehensions, try/except, loops, f-strings, with.
IMPORTS: json, math, datetime, re, collections, itertools, functools, dataclasses, typing.
NOT ALLOWED: inheritance, yield, del, eval/exec, third-party imports, redefining host functions.
Return ONLY Python code. No markdown, no explanations, no comments."""


def build_coder() -> Agent:
    settings = get_settings()
    return Agent(
        settings.model_coder,
        output_type=str,
        model_settings=ModelSettings(max_tokens=settings.max_tokens),
        instructions=MONTY_LIMITATIONS,
        name="coder",
    )
