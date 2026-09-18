from __future__ import annotations

import re
import sys

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


def verify_code(code: str, allowlist: list[str], spec: str = "",
                input_schema: dict[str, str] | None = None) -> tuple[str, list[str]]:
    warnings = []
    spec_args = extract_args_from_spec(spec)

    has_module_result = bool(re.search(r'^result\s*=', code, re.MULTILINE))
    if not has_module_result:
        func_defs = re.findall(r'^def\s+(\w+)\s*\(', code, re.MULTILINE)
        if func_defs:
            last_fn = func_defs[-1]
            match = re.search(rf'^def\s+{last_fn}\s*\(([^)]*)\)', code, re.MULTILINE)
            params = [p.strip().split(':')[0].split('=')[0].strip()
                      for p in match.group(1).split(',') if p.strip()] if match else []
            if not params:
                code += f"\nresult = {last_fn}()\n"
            else:
                args = []
                for i in range(len(params)):
                    if i < len(spec_args):
                        args.append(repr(spec_args[i]))
                    else:
                        args.append(repr(''))
                code += f"\nresult = {last_fn}({', '.join(args)})\n"
            warnings.append(f"auto-fixed: added result = {last_fn}() call")

    called = [fn for fn in allowlist if fn + "(" in code]
    if not called:
        warnings.append(f"WARNING: code calls none of {allowlist}. Likely hardcoded data.")
    for fn in _get_host_descriptions():
        if fn not in allowlist and fn + "(" in code:
            allowlist.append(fn)
            warnings.append(f"auto-fixed: added {fn} to allowlist (used in code)")

    for fn in allowlist:
        if re.search(rf'^def\s+{fn}\s*\(', code, re.MULTILINE):
            code = re.sub(rf'^def\s+{fn}\s*\(.*?(?=\ndef\s|\Z)', '', code, flags=re.MULTILINE | re.DOTALL)
            warnings.append(f"auto-fixed: removed redefinition of host function {fn}")

    if input_schema and 'inputs[' not in code and 'inputs.get(' not in code:
        warnings.append("WARNING: code does not read from inputs dict. May have hardcoded values.")

    ALLOWED_IMPORTS = ["json", "math", "datetime", "re", "collections", "itertools", "functools", "dataclasses"]
    for mod in ALLOWED_IMPORTS:
        if f"{mod}." in code and f"import {mod}" not in code:
            code = f"import {mod}\n{code}"
            warnings.append(f"auto-fixed: added import {mod}")

    return code, warnings


def verify_tests(test_code: str) -> tuple[str, list[str]]:
    warnings = []
    if "assert " not in test_code:
        warnings.append("WARNING: test code has no assert statements")
    return test_code, warnings


def run_and_record(vault: VaultManager, ticket_id: str, code: str,
                   allowlist: list[str], inputs: dict | None = None,
                   limits: dict[str, float | int] | None = None,
                   interactive: bool = False) -> RunResult:
    run_result = run_solution(code, inputs or {}, allowlist, limits=limits, interactive=interactive)
    record = RunRecord(
        inputs=inputs or {},
        output=run_result.value if run_result.success else None,
        success=run_result.success,
        error=run_result.error,
    )
    vault.save_run(ticket_id, record)
    vault.commit(ticket_id, f"run {'ok' if run_result.success else 'fail'}")
    return run_result
