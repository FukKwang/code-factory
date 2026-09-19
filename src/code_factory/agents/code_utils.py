from __future__ import annotations

import re
import sys
from typing import Any, Callable

from ..sandbox.registry import get_descriptions as _get_host_descriptions
from ..sandbox.runner import RunResult, run_solution
from ..vault.manager import VaultManager
from ..vault.models import RunRecord

_DIM = "\033[2m"
_RESET = "\033[0m"


def status(icon: str, msg: str):
    print(f"{_DIM}{icon} {msg}{_RESET}", file=sys.stderr, flush=True)


def strip_fences(code: str) -> str:
    if code.startswith("```"):
        lines = code.split("\n")
        end = -1 if lines[-1].strip() == "```" else len(lines)
        return "\n".join(lines[1:end])
    return code


def clean_llm_code(text: str) -> str:
    # Strip Qwen3 tool_call degeneration
    text = re.sub(r'</?tool_call>\s*', '', text).strip()
    if not text:
        return ""
    m = re.search(r'```(?:python)?\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    lines = text.strip().split("\n")
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and (stripped.startswith(("import ", "from ", "def ", "class ", "result", "#"))
                         or re.match(r'^[a-z_]\w*\s*=', stripped)
                         or stripped.startswith("assert ")):
            start = i
            break
    end = len(lines)
    for i in range(len(lines) - 1, start, -1):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith(("#", "assert ", "result")) or re.match(r'^[a-z_)\]\}]', stripped):
            end = i + 1
            break
    return "\n".join(lines[start:end]).strip()


def syntax_check(code: str) -> str | None:
    try:
        compile(code, "<check>", "exec")
        return None
    except SyntaxError as e:
        return f"line {e.lineno}: {e.msg}"


def fn_docs(allowlist: list[str]) -> str:
    descs = _get_host_descriptions()
    return "\n".join(
        f"- {descs[f]}" for f in allowlist
        if f in descs
    )


def extract_inputs_from_query(query: str, input_schema: dict[str, str]) -> dict[str, str]:
    if not input_schema:
        return {}
    inputs = {}
    quoted = re.findall(r'["\']([^"\']+)["\']', query)
    caps = re.findall(r'\b([A-Z][A-Z0-9_]{1,})\b', query)
    _COMMON = {'Tell', 'Show', 'Find', 'Get', 'List', 'What', 'How', 'The', 'For', 'And', 'Use', 'Create', 'Query', 'About'}
    titlecase = [w for w in re.findall(r'\b([A-Z][a-z]{2,})\b', query) if w not in _COMMON]
    numbers = re.findall(r'\b(\d+(?:\.\d+)?)\b', query)
    candidates = quoted + caps + titlecase + numbers

    for key, description in input_schema.items():
        desc_lower = description.lower()
        if candidates:
            inputs[key] = candidates.pop(0)
        else:
            for word in desc_lower.split():
                pattern = rf'{re.escape(word)}\s+(\S+)'
                m = re.search(pattern, query, re.IGNORECASE)
                if m:
                    inputs[key] = m.group(1)
                    break
    return inputs


def extract_args_from_spec(spec: str) -> list[str]:
    values: list[str] = []
    values.extend(re.findall(r'["\']([^"\']+)["\']', spec))
    values.extend(re.findall(r'\b([A-Z][A-Z0-9_]{1,})\b', spec))
    values.extend(re.findall(r'\b(\d+(?:\.\d+)?)\b', spec))
    seen = set()
    return [v for v in values if not (v in seen or seen.add(v))]


# ---------------------------------------------------------------------------
# Pluggable code check chain
# ---------------------------------------------------------------------------
# Each check: (code, ctx) -> (code, warnings)
# ctx carries allowlist, spec, input_schema so checks stay independent.

class CheckCtx:
    __slots__ = ("allowlist", "spec", "input_schema", "spec_args")

    def __init__(self, allowlist: list[str], spec: str = "",
                 input_schema: dict[str, str] | None = None):
        self.allowlist = allowlist
        self.spec = spec
        self.input_schema = input_schema or {}
        self.spec_args = extract_args_from_spec(spec)


CodeCheck = Callable[[str, CheckCtx], tuple[str, list[str]]]


def check_result_assignment(code: str, ctx: CheckCtx) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if re.search(r'^result\s*=', code, re.MULTILINE):
        return code, warnings
    func_defs = re.findall(r'^def\s+(\w+)\s*\(', code, re.MULTILINE)
    if not func_defs:
        return code, warnings
    last_fn = func_defs[-1]
    match = re.search(rf'^def\s+{last_fn}\s*\(([^)]*)\)', code, re.MULTILINE)
    params = [p.strip().split(':')[0].split('=')[0].strip()
              for p in match.group(1).split(',') if p.strip()] if match else []
    if not params:
        code += f"\nresult = {last_fn}()\n"
    else:
        args = []
        for i in range(len(params)):
            if i < len(ctx.spec_args):
                args.append(repr(ctx.spec_args[i]))
            else:
                args.append(repr(''))
        code += f"\nresult = {last_fn}({', '.join(args)})\n"
    warnings.append(f"auto-fixed: added result = {last_fn}() call")
    return code, warnings


def check_host_fn_usage(code: str, ctx: CheckCtx) -> tuple[str, list[str]]:
    warnings: list[str] = []
    called = [fn for fn in ctx.allowlist if fn + "(" in code]
    if not called:
        warnings.append(f"WARNING: code calls none of {ctx.allowlist}. Likely hardcoded data.")
    for fn in _get_host_descriptions():
        if fn not in ctx.allowlist and fn + "(" in code:
            ctx.allowlist.append(fn)
            warnings.append(f"auto-fixed: added {fn} to allowlist (used in code)")
    return code, warnings


def check_no_redefines(code: str, ctx: CheckCtx) -> tuple[str, list[str]]:
    warnings: list[str] = []
    for fn in ctx.allowlist:
        if re.search(rf'^def\s+{fn}\s*\(', code, re.MULTILINE):
            code = re.sub(rf'^def\s+{fn}\s*\(.*?(?=\ndef\s|\Z)', '', code, flags=re.MULTILINE | re.DOTALL)
            warnings.append(f"auto-fixed: removed redefinition of host function {fn}")
    return code, warnings


def check_inputs_usage(code: str, ctx: CheckCtx) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if ctx.input_schema and 'inputs[' not in code and 'inputs.get(' not in code:
        warnings.append("WARNING: code does not read from inputs dict. May have hardcoded values.")
    return code, warnings


_ALLOWED_IMPORTS = ["json", "math", "datetime", "re", "collections", "itertools", "functools", "dataclasses"]


def check_dedent(code: str, ctx: CheckCtx) -> tuple[str, list[str]]:  # noqa: ARG001
    """Fix inconsistent leading whitespace (model sometimes indents non-first lines)."""
    import textwrap
    # Case 1: all lines share common indent
    dedented = textwrap.dedent(code)
    if dedented != code:
        return dedented, ["auto-fixed: removed common leading whitespace"]
    # Case 2: first line at col 0, rest indented by N spaces (common Qwen3 artifact)
    lines = code.split("\n")
    if len(lines) < 2 or not lines[0] or lines[0][0].isspace():
        return code, []
    rest = "\n".join(lines[1:])
    rest_dedented = textwrap.dedent(rest)
    if rest_dedented != rest:
        fixed = lines[0] + "\n" + rest_dedented
        try:
            compile(fixed, "<check>", "exec")
            return fixed, ["auto-fixed: dedented lines after first"]
        except SyntaxError:
            pass
    return code, []


def check_missing_imports(code: str, ctx: CheckCtx) -> tuple[str, list[str]]:  # noqa: ARG001
    warnings: list[str] = []
    for mod in _ALLOWED_IMPORTS:
        if f"{mod}." in code and f"import {mod}" not in code:
            code = f"import {mod}\n{code}"
            warnings.append(f"auto-fixed: added import {mod}")
    return code, warnings


CODE_CHECKS: list[CodeCheck] = [
    check_dedent,
    check_result_assignment,
    check_host_fn_usage,
    check_no_redefines,
    check_inputs_usage,
    check_missing_imports,
]


def verify_code(code: str, allowlist: list[str], spec: str = "",
                input_schema: dict[str, str] | None = None,
                checks: list[CodeCheck] | None = None) -> tuple[str, list[str]]:
    ctx = CheckCtx(allowlist, spec, input_schema)
    all_warnings: list[str] = []
    for check in (CODE_CHECKS if checks is None else checks):
        code, warnings = check(code, ctx)
        all_warnings.extend(warnings)
    return code, all_warnings


def verify_tests(test_code: str) -> tuple[str, list[str]]:
    warnings = []
    if "assert " not in test_code:
        warnings.append("WARNING: test code has no assert statements")
    return test_code, warnings


# ---------------------------------------------------------------------------
# Retry-with-feedback decorator (Design-by-Contract style)
# ---------------------------------------------------------------------------

def with_remediation(*, retries: int = 1,
                     post: Callable[[str], str | None] | None = None):
    """Decorator for async LLM calls. Retries when post-condition fails.

    post(result) returns None on success, or error string to feed back.
    """
    def decorator(fn):
        async def wrapper(prompt: str, *args, **kwargs) -> str:
            result = ""
            for attempt in range(retries + 1):
                result = await fn(prompt, *args, **kwargs)
                if post is None:
                    return result
                error = post(result)
                if error is None:
                    return result
                if attempt < retries:
                    prompt = f"Previous output failed validation: {error}\n\nOriginal request:\n{prompt}"
            return result
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Output validation (post-condition on RunResult)
# ---------------------------------------------------------------------------

def validate_output(value: Any, expected_type: str | None = None) -> str | None:
    """Validate RunResult.value shape. Returns None on pass, error string on fail."""
    if value is None:
        return "output is None"

    if expected_type is None:
        return None

    checks: dict[str, Callable[[Any], bool]] = {
        "dict": lambda v: isinstance(v, dict),
        "list": lambda v: isinstance(v, list),
        "str": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)),
        "list[dict]": lambda v: isinstance(v, list) and all(isinstance(x, dict) for x in v),
        "nonempty": lambda v: bool(v),
    }

    for type_part in expected_type.split(","):
        type_part = type_part.strip()
        checker = checks.get(type_part)
        if checker and not checker(value):
            return f"expected {type_part}, got {type(value).__name__}: {str(value)[:100]}"

    return None


def run_and_record(vault: VaultManager, ticket_id: str, code: str,
                   allowlist: list[str], inputs: dict | None = None,
                   limits: dict[str, float | int] | None = None,
                   interactive: bool = False,
                   expected_type: str | None = None) -> RunResult:
    run_result = run_solution(code, inputs or {}, allowlist, limits=limits, interactive=interactive)

    if run_result.success and expected_type:
        error = validate_output(run_result.value, expected_type)
        if error:
            run_result.success = False
            run_result.error = f"output validation failed: {error}"

    record = RunRecord(
        inputs=inputs or {},
        output=run_result.value if run_result.success else None,
        success=run_result.success,
        error=run_result.error,
    )
    vault.save_run(ticket_id, record)
    vault.commit(ticket_id, f"run {'ok' if run_result.success else 'fail'}")
    return run_result
