from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings

from ..config import Settings, get_settings, resolve_model
from ..context.manager import write_findings_file
from ..sandbox.host_functions import HOST_FUNCTION_DESCRIPTIONS
from ..sandbox.runner import RunResult, run_solution, run_tests
from ..vault.manager import VaultManager
from ..vault.models import RunRecord, StructuralMatch, TicketStatus
from .coder import MONTY_LIMITATIONS, build_coder
from .researcher import build_researcher
from .reviewer import build_reviewer
from .test_writer import build_test_writer

ROLE_INSTRUCTIONS: dict[str, str] = {
    "researcher": """Analyze requirements for a coding task.
Produce: 1) OBJECTIVE 2) DATA NEEDED 3) PROCESSING 4) OUTPUT FORMAT 5) HOST FUNCTIONS TO USE 6) EDGE CASES.
Be concise.""",
    "test_writer": """Write test assertions for Monty sandbox code.
Rules: use only assert statements on `result` variable. Test types, required keys, value ranges.
No test frameworks. No imports. Return ONLY Python code.""",
    "coder": MONTY_LIMITATIONS,
    "reviewer": """Review code execution results. Write plain-language summary for user (no code).
Focus on whether output answers the requirement. If tests failed, explain simply. One paragraph max.""",
}

MAX_TOOL_CALLS = 1

_DIM = "\033[2m"
_RESET = "\033[0m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"


def _status(icon: str, msg: str):
    print(f"{_DIM}{icon} {msg}{_RESET}", file=sys.stderr, flush=True)


@dataclass
class FactoryDeps:
    vault: VaultManager
    settings: Settings
    current_ticket_id: str | None = None
    _tool_counts: dict[str, int] | None = None
    pipeline_usage: dict[str, int] | None = None

    def check_limit(self, tool_name: str, limit: int = MAX_TOOL_CALLS) -> str | None:
        if self._tool_counts is None:
            self._tool_counts = {}
        self._tool_counts[tool_name] = self._tool_counts.get(tool_name, 0) + 1
        if self._tool_counts[tool_name] > limit:
            return f"STOP. {tool_name} limit reached ({limit}). Do NOT call it again."
        return None


def _strip_fences(code: str) -> str:
    if code.startswith("```"):
        lines = code.split("\n")
        end = -1 if lines[-1].strip() == "```" else len(lines)
        return "\n".join(lines[1:end])
    return code


def _clean_llm_code(text: str) -> str:
    """Extract code from LLM response. Handles fences, preamble, trailing explanation."""
    # Find fenced code block anywhere in response
    m = re.search(r'```(?:python)?\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Strip leading prose lines (small models add "Here's the code:" etc)
    lines = text.strip().split("\n")
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and (stripped.startswith(("import ", "from ", "def ", "class ", "result", "#"))
                         or re.match(r'^[a-z_]\w*\s*=', stripped)
                         or stripped.startswith("assert ")):
            start = i
            break
    # Strip trailing prose after code
    end = len(lines)
    for i in range(len(lines) - 1, start, -1):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith(("#", "assert ", "result")) or re.match(r'^[a-z_)\]\}]', stripped):
            end = i + 1
            break
    return "\n".join(lines[start:end]).strip()


def _syntax_check(code: str) -> str | None:
    """Return error string if code has syntax errors, None if valid."""
    try:
        compile(code, "<check>", "exec")
        return None
    except SyntaxError as e:
        return f"line {e.lineno}: {e.msg}"


def _fn_docs(allowlist: list[str]) -> str:
    return "\n".join(
        f"- {HOST_FUNCTION_DESCRIPTIONS[f]}" for f in allowlist
        if f in HOST_FUNCTION_DESCRIPTIONS
    )


def _extract_inputs_from_query(query: str, input_schema: dict[str, str]) -> dict[str, str]:
    """Extract runtime input values from user query based on ticket's input_schema."""
    if not input_schema:
        return {}
    inputs = {}
    quoted = re.findall(r'["\']([^"\']+)["\']', query)
    caps = re.findall(r'\b([A-Z][A-Z0-9_]{1,})\b', query)
    # Title-case words not common English (likely proper nouns: names, cities)
    _COMMON = {'Tell', 'Show', 'Find', 'Get', 'List', 'What', 'How', 'The', 'For', 'And', 'Use', 'Create', 'Query', 'About'}
    titlecase = [w for w in re.findall(r'\b([A-Z][a-z]{2,})\b', query) if w not in _COMMON]
    numbers = re.findall(r'\b(\d+(?:\.\d+)?)\b', query)
    candidates = quoted + caps + titlecase + numbers

    for key, description in input_schema.items():
        desc_lower = description.lower()
        # Match by keyword in description vs query words
        if candidates:
            inputs[key] = candidates.pop(0)
        else:
            # Try to find value near the description keyword in query
            for word in desc_lower.split():
                pattern = rf'{re.escape(word)}\s+(\S+)'
                m = re.search(pattern, query, re.IGNORECASE)
                if m:
                    inputs[key] = m.group(1)
                    break
    return inputs


def _verify_code(code: str, allowlist: list[str], spec: str = "",
                 input_schema: dict[str, str] | None = None) -> tuple[str, list[str]]:
    """Verify and fix generated code. Returns (fixed_code, warnings)."""
    warnings = []
    spec_args = _extract_args_from_spec(spec)

    # Check 1: result assigned at module level
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
                for i, param in enumerate(params):
                    if i < len(spec_args):
                        args.append(repr(spec_args[i]))
                    else:
                        args.append(repr(''))
                code += f"\nresult = {last_fn}({', '.join(args)})\n"
            warnings.append(f"auto-fixed: added result = {last_fn}() call")

    # Check 2: host functions actually called + auto-expand allowlist
    called = [fn for fn in allowlist if fn + "(" in code]
    if not called:
        warnings.append(f"WARNING: code calls none of {allowlist}. Likely hardcoded data.")
    for fn in HOST_FUNCTION_DESCRIPTIONS:
        if fn not in allowlist and fn + "(" in code:
            allowlist.append(fn)
            warnings.append(f"auto-fixed: added {fn} to allowlist (used in code)")

    # Check 3: no function redefinitions of host functions
    for fn in allowlist:
        if re.search(rf'^def\s+{fn}\s*\(', code, re.MULTILINE):
            code = re.sub(rf'^def\s+{fn}\s*\(.*?(?=\ndef\s|\Z)', '', code, flags=re.MULTILINE | re.DOTALL)
            warnings.append(f"auto-fixed: removed redefinition of host function {fn}")

    # Check 4: code uses inputs dict when input_schema defined
    if input_schema and 'inputs[' not in code and 'inputs.get(' not in code:
        warnings.append("WARNING: code does not read from inputs dict. May have hardcoded values.")

    # Check 5: auto-add missing stdlib imports
    ALLOWED_IMPORTS = ["json", "math", "datetime", "re", "collections", "itertools", "functools", "dataclasses"]
    for mod in ALLOWED_IMPORTS:
        if f"{mod}." in code and f"import {mod}" not in code:
            code = f"import {mod}\n{code}"
            warnings.append(f"auto-fixed: added import {mod}")

    return code, warnings


def _extract_args_from_spec(spec: str) -> list[str]:
    """Pull likely argument values from spec text."""
    values: list[str] = []
    values.extend(re.findall(r'["\']([^"\']+)["\']', spec))
    values.extend(re.findall(r'\b([A-Z][A-Z0-9_]{1,})\b', spec))
    values.extend(re.findall(r'\b(\d+(?:\.\d+)?)\b', spec))
    seen = set()
    return [v for v in values if not (v in seen or seen.add(v))]


def _verify_tests(test_code: str) -> tuple[str, list[str]]:
    """Verify test code has assert statements."""
    warnings = []
    if "assert " not in test_code:
        warnings.append("WARNING: test code has no assert statements")
    return test_code, warnings


def _run_and_record(vault: VaultManager, ticket_id: str, code: str,
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


def build_orchestrator(settings: Settings | None = None) -> Agent:
    if settings is None:
        settings = get_settings()

    fn_names = list(HOST_FUNCTION_DESCRIPTIONS.keys())

    ms = ModelSettings(max_tokens=settings.max_tokens)

    agent: Agent[FactoryDeps, str] = Agent(
        resolve_model(settings.model_orchestrator, settings),
        deps_type=FactoryDeps,
        model_settings=ms,
        instructions=f"""Coding agent that creates REUSABLE programs. You are the BRAIN — delegate work, format results.

Pipeline:
1. execute_task(query, title, spec, host_functions, input_schema, runtime_inputs) — searches vault, reuses or creates+tests program. Returns STATUS only.
2. peek_result(ticket_id) — get actual data output to present to user.
3. Present result in plain language. Never show code.

IMPORTANT: execute_task returns STATUS only, not data. After DONE, ALWAYS call peek_result to get data before responding.

Generalization:
- title = reusable program name, NOT specific query. Example: "Find loans by borrower name", NOT "Find loans for borrower ABC"
- input_schema = parameters the program needs. Example: {{"borrower_name": "Name of borrower to look up"}}
- runtime_inputs = specific values for THIS run. Example: {{"borrower_name": "ABC"}}

Other tools:
- iterate_code(ticket_id, feedback) — modify existing program
- close_ticket(ticket_id) — mark done

Rules:
- Never invent ticket_ids. Only use IDs returned by execute_task or iterate_code.
- host_functions choices: {fn_names}""",
        name="orchestrator",
    )

    researcher = build_researcher(settings)
    test_writer = build_test_writer(settings)
    coder = build_coder(settings)
    reviewer = build_reviewer(settings)

    _sub_agents = {"researcher": researcher, "test_writer": test_writer, "coder": coder, "reviewer": reviewer}

    _pipeline_agent: Agent | None = None
    if settings.single_agent:
        _pipeline_agent = Agent(
            resolve_model(settings.model_orchestrator, settings),
            output_type=str,
            model_settings=ModelSettings(max_tokens=settings.max_tokens * 2),
            instructions=f"""You are a TDD code generation pipeline. Given a spec and host functions, produce ALL of the following in ONE response using exact section markers.

{MONTY_LIMITATIONS}

## Response format

===RESEARCH===
Analyze: 1) OBJECTIVE 2) DATA NEEDED 3) HOST FUNCTIONS TO USE 4) EDGE CASES. Be concise.

===TESTS===
Write assert statements that validate `result` variable. No imports, no test frameworks. Only assert statements.

===CODE===
Write Monty sandbox Python code. Assign final output to `result`. Read parameters from `inputs` dict.
NEVER redefine host functions. NEVER hardcode values. NEVER create mock data.

===REVIEW===
One paragraph: does output match the requirement? Plain language, no code.

CRITICAL: Use exact markers ===RESEARCH===, ===TESTS===, ===CODE===, ===REVIEW===. Each section must be present.""",
            name="pipeline",
        )

    _pipeline_usage: dict[str, int] = {"cache_hit": 0, "cache_miss": 0, "output": 0}
    # Grow one conversation for prefix cache reuse (DeepSeek KV cache needs exact prefix match)
    _pipeline_history: list = []

    def _parse_pipeline_response(text: str) -> dict[str, str]:
        sections = {}
        for key in ("RESEARCH", "TESTS", "CODE", "REVIEW"):
            marker = f"==={key}==="
            start = text.find(marker)
            if start == -1:
                continue
            start += len(marker)
            next_markers = [text.find(f"==={k}===", start) for k in ("RESEARCH", "TESTS", "CODE", "REVIEW") if text.find(f"==={k}===", start) > start]
            end = min(next_markers) if next_markers else len(text)
            sections[key.lower()] = text[start:end].strip()
        return sections

    async def _llm_call(role: str, prompt: str) -> str:
        import asyncio
        r = await asyncio.wait_for(_sub_agents[role].run(prompt), timeout=30)
        d = r.usage.details or {}
        _pipeline_usage["cache_hit"] += d.get("prompt_cache_hit_tokens", 0)
        _pipeline_usage["cache_miss"] += d.get("prompt_cache_miss_tokens", 0)
        _pipeline_usage["output"] += r.usage.output_tokens
        return r.output

    async def _run_pipeline(spec: str, fn_doc: str, schema_doc: str) -> dict[str, str]:
        prompt = f"Spec:\n{spec}\n\nHost functions:\n{fn_doc}"
        if schema_doc:
            prompt += f"\n\nInput parameters in `inputs` dict:\n{schema_doc}"
        prompt += "\n\nGenerate all sections: RESEARCH, TESTS, CODE, REVIEW."
        r = await _pipeline_agent.run(prompt, message_history=_pipeline_history)
        d = r.usage.details or {}
        _pipeline_usage["cache_hit"] += d.get("prompt_cache_hit_tokens", 0)
        _pipeline_usage["cache_miss"] += d.get("prompt_cache_miss_tokens", 0)
        _pipeline_usage["output"] += r.usage.output_tokens
        _pipeline_history.clear()
        _pipeline_history.extend(r.all_messages())
        return _parse_pipeline_response(r.output)

    @agent.tool
    async def execute_task(ctx: RunContext[FactoryDeps], query: str, title: str, spec: str,
                           host_functions: list[str],
                           input_schema: dict[str, str] | None = None,
                           runtime_inputs: dict[str, str] | None = None) -> str:
        """Full pipeline: search vault for reuse, or create+test new program. Returns STATUS only — call peek_result for data."""
        limit_msg = ctx.deps.check_limit("execute_task", 1)
        if limit_msg:
            return limit_msg + " Present what you have to the user."
        vault = ctx.deps.vault
        valid = [f for f in host_functions if f in HOST_FUNCTION_DESCRIPTIONS] or fn_names
        input_keys = list((input_schema or {}).keys())
        reusable_statuses = (TicketStatus.APPROVED, TicketStatus.CLOSED, TicketStatus.REUSED, TicketStatus.ITERATING)
        limits = ctx.deps.settings.sandbox.clamp()

        # Phase 1: Search vault for reuse
        _status("🔍", f"searching vault: {query}")
        if valid and input_keys:
            structural = vault.search_structural(valid, input_keys)
            for m in structural:
                if m.ticket.status not in reusable_statuses:
                    continue
                code = vault.read_stage_file(m.ticket.id, "solution.py")
                if not code:
                    continue
                ticket = vault.load_ticket(m.ticket.id)
                if not ticket:
                    continue
                if m.match_type == "exact":
                    ri = runtime_inputs or _extract_inputs_from_query(query, ticket.input_schema)
                    ticket.status = TicketStatus.REUSED
                    vault.save_ticket(ticket)
                    vault.commit(m.ticket.id, "reused (structural exact)")
                    run_result = _run_and_record(vault, m.ticket.id, code, ticket.host_function_allowlist, ri, limits=limits, interactive=sys.stdin.isatty())
                    if run_result.success:
                        return f"DONE. Reused {m.ticket.id} ({m.ticket.title}). Inputs: {ri}."
                    _status("⚠️", f"reuse failed {m.ticket.id}, creating new ticket")
                    break

        results = vault.search(query)
        reusable = [r for r in results if r.status in reusable_statuses]
        if reusable:
            best = reusable[0]
            code = vault.read_stage_file(best.id, "solution.py")
            if code:
                ticket = vault.load_ticket(best.id)
                if ticket:
                    ri = runtime_inputs or _extract_inputs_from_query(query, ticket.input_schema)
                    ticket.status = TicketStatus.REUSED
                    vault.save_ticket(ticket)
                    vault.commit(best.id, "reused")
                    run_result = _run_and_record(vault, best.id, code, ticket.host_function_allowlist, ri, limits=limits, interactive=sys.stdin.isatty())
                    if run_result.success:
                        return f"DONE. Reused {best.id} ({best.title}). Inputs: {ri}."
                    _status("⚠️", f"reuse failed {best.id}, creating new ticket")

        # Phase 2: Create ticket
        _status("📝", f"creating ticket: {title}")
        dupes = vault.search_structural(valid, input_keys)
        exact = [d for d in dupes if d.match_type == "exact" and d.ticket.status in (
            TicketStatus.APPROVED, TicketStatus.CLOSED, TicketStatus.REUSED,
        )]
        if exact:
            return f"DUPLICATE of {exact[0].ticket.id} ({exact[0].ticket.title}). Use iterate_code({exact[0].ticket.id!r}, ...) instead."

        ticket = vault.create_ticket(title, spec, host_functions=valid)
        if input_schema:
            ticket.input_schema = input_schema
            vault.save_ticket(ticket)
            vault.commit(ticket.id, "set input_schema")
        ctx.deps.current_ticket_id = ticket.id
        ticket_id = ticket.id

        # Phase 3: Generate and test (sub-agents do the work)
        import asyncio as _aio
        _status("⚙️", f"generate_and_test: {ticket_id}")

        vault.write_stage_file(ticket_id, "spec.md", spec)
        fn_doc = _fn_docs(ticket.host_function_allowlist)
        schema_doc = "\n".join(f"  - {k}: {v}" for k, v in ticket.input_schema.items())
        test_inputs = runtime_inputs or {k: "test" for k in ticket.input_schema}

        MAX_RETRIES = 1

        try:
            if _pipeline_agent:
                _status("🚀", "pipeline generating research + tests + code...")
                ticket.status = TicketStatus.GATHERING
                vault.save_ticket(ticket)
                sections = await _run_pipeline(spec, fn_doc, schema_doc)
                findings = sections.get("research", "")
                test_code = _clean_llm_code(sections.get("tests", ""))
                code = _clean_llm_code(sections.get("code", ""))
                review = sections.get("review", "")
                test_code, test_warnings = _verify_tests(test_code)
                code, code_warnings = _verify_code(code, ticket.host_function_allowlist, spec, ticket.input_schema)
                write_findings_file(vault.ticket_dir(ticket_id) / "runs", "research", findings)
                vault.write_stage_file(ticket_id, "test_solution.py", test_code)
                vault.write_stage_file(ticket_id, "solution.py", code)
                ticket.status = TicketStatus.CODE_DRAFTED
                vault.save_ticket(ticket)
                vault.commit(ticket_id, "pipeline generate")
            else:
                _status("🔬", "researcher analyzing requirements...")
                ticket.status = TicketStatus.GATHERING
                vault.save_ticket(ticket)
                findings = await _llm_call("researcher",
                    f"Requirement: {spec}\n\nAvailable host functions:\n{fn_doc}"
                    + (f"\n\nInput parameters:\n{schema_doc}" if schema_doc else ""))
                findings = _strip_fences(findings)
                write_findings_file(vault.ticket_dir(ticket_id) / "runs", "research", findings)
                vault.commit(ticket_id, "research")

                _status("✏️", "generating tests...")
                ticket.status = TicketStatus.SPEC_DRAFTED
                vault.save_ticket(ticket)
                vault.commit(ticket_id, "spec drafted")
                test_prompt = (f"Spec:\n{spec}\n\nResearch findings:\n{findings[:500]}\n\nHost functions:\n{fn_doc}\n\n")
                if schema_doc:
                    test_prompt += f"Code reads parameters from `inputs` dict:\n{schema_doc}\nTests can assume `inputs` is populated. Test `result` structure.\n\n"
                test_prompt += "Write assert statements to validate `result`. Return only code."
                test_code = _clean_llm_code(await _llm_call("test_writer", test_prompt))
                test_code, test_warnings = _verify_tests(test_code)
                vault.write_stage_file(ticket_id, "test_solution.py", test_code)
                ticket.status = TicketStatus.TESTS_DRAFTED
                vault.save_ticket(ticket)
                vault.commit(ticket_id, "generate tests")

                _status("💻", "coder writing solution...")
                code_prompt = f"Spec:\n{spec}\n\nTests your code must pass:\n{test_code}\n\nHost functions:\n{fn_doc}\n\n"
                if schema_doc:
                    code_prompt += f"Runtime parameters available in `inputs` dict:\n{schema_doc}\nRead ALL request-specific values from inputs[\"key\"]. Never hardcode them.\n\n"
                code_prompt += "Return only Python code."
                code = _clean_llm_code(await _llm_call("coder", code_prompt))
                code, code_warnings = _verify_code(code, ticket.host_function_allowlist, spec, ticket.input_schema)
                vault.write_stage_file(ticket_id, "solution.py", code)
                ticket.status = TicketStatus.CODE_DRAFTED
                vault.save_ticket(ticket)
                vault.commit(ticket_id, "generate code")
                review = ""
        except _aio.TimeoutError:
            return f"{ticket_id} FAILED: sub-agent timed out during code generation. Try again or simplify the query."

        # Syntax check + retry loop
        for attempt in range(MAX_RETRIES + 1):
            syntax_err = _syntax_check(code)
            if syntax_err and attempt < MAX_RETRIES:
                _status("🔧", f"syntax error, retrying: {syntax_err}")
                code = _clean_llm_code(await _llm_call("coder",
                    f"This code has a syntax error: {syntax_err}\n\n{code}\n\nFix the error. Return only Python code."))
                code, _ = _verify_code(code, ticket.host_function_allowlist, spec, ticket.input_schema)
                vault.write_stage_file(ticket_id, "solution.py", code)
                vault.commit(ticket_id, f"syntax fix attempt {attempt + 1}")
                continue
            if syntax_err:
                return f"{ticket_id} FAILED: syntax error: {syntax_err[:100]}"

            _status("🧪", "running tests...")
            test_result = run_tests(code, test_code, ticket.host_function_allowlist, test_inputs, limits=limits)
            if test_result.passed:
                _status("✅", "tests passed")
                break
            _status("❌", f"tests failed: {'; '.join(test_result.failures[:2])}")
            if attempt < MAX_RETRIES:
                _status("🔧", "retrying code generation with error feedback...")
                code = _clean_llm_code(await _llm_call("coder",
                    f"Code failed tests.\nError: {'; '.join(test_result.failures[:3])}\n\nCode:\n{code}\n\nTests:\n{test_code}\n\nHost functions:\n{fn_doc}\n\nFix the code to pass tests. Return only Python code."))
                code, _ = _verify_code(code, ticket.host_function_allowlist, spec, ticket.input_schema)
                vault.write_stage_file(ticket_id, "solution.py", code)
                vault.commit(ticket_id, f"test fix attempt {attempt + 1}")

        vault.commit(ticket_id, f"tests {'pass' if test_result.passed else 'fail'}")

        _status("▶️", "executing solution...")
        run_result = _run_and_record(vault, ticket_id, code, ticket.host_function_allowlist, test_inputs, limits=limits, interactive=sys.stdin.isatty())

        if not review:
            _status("📋", "reviewer evaluating output...")
            all_warnings = (test_warnings if 'test_warnings' in dir() else []) + (code_warnings if 'code_warnings' in dir() else [])
            review_input = (
                f"Requirement: {spec[:300]}\n"
                f"Tests: {'PASSED' if test_result.passed else 'FAILED: ' + '; '.join(test_result.failures[:3])}\n"
                f"Output: {str(run_result.value)[:300] if run_result.success else run_result.error[:200]}"
                + (f"\nAuto-fixes applied: {'; '.join(all_warnings)}" if all_warnings else ""))
            review = await _llm_call("reviewer", review_input)

        if run_result.success and test_result.passed:
            ticket.status = TicketStatus.APPROVED
            vault.save_ticket(ticket)
            vault.commit(ticket_id, "approved")
            return f"{ticket_id} DONE. Tests passed. {review[:200]}"

        if run_result.success:
            ticket.status = TicketStatus.APPROVED
            vault.save_ticket(ticket)
            vault.commit(ticket_id, "approved (tests imprecise)")
            return f"{ticket_id} DONE. Call peek_result to get data. Do NOT iterate or retry."

        return f"{ticket_id} FAILED: {run_result.error[:200]}"

    @agent.tool
    async def close_ticket(ctx: RunContext[FactoryDeps], ticket_id: str) -> str:
        """Close ticket after user satisfied."""
        _status("🔒", f"closing {ticket_id}")
        vault = ctx.deps.vault
        ticket = vault.load_ticket(ticket_id)
        if not ticket:
            return f"{ticket_id} not found."
        ticket.status = TicketStatus.CLOSED
        vault.save_ticket(ticket)
        vault.commit(ticket_id, "closed")
        return f"{ticket_id} closed."

    @agent.tool
    async def peek_result(ctx: RunContext[FactoryDeps], ticket_id: str, max_chars: int = 500) -> str:
        """Get latest run output for a ticket. Call after execute_task DONE to get data for user response."""
        vault = ctx.deps.vault
        runs_dir = vault.ticket_dir(ticket_id) / "runs"
        if not runs_dir.exists():
            return f"{ticket_id} has no runs."
        run_files = sorted(runs_dir.glob("run_*.json"), reverse=True)
        if not run_files:
            return f"{ticket_id} has no runs."
        import json
        record = json.loads(run_files[0].read_text())
        output = str(record.get("output", ""))
        if len(output) > max_chars:
            output = output[:max_chars] + "... (truncated)"
        return output

    @agent.tool
    async def iterate_code(ctx: RunContext[FactoryDeps], ticket_id: str, feedback: str,
                           runtime_inputs: dict[str, str] | None = None,
                           sandbox_limits: dict[str, float | int | None] | None = None) -> str:
        """Modify existing program based on feedback. Call directly with ticket_id.
        sandbox_limits = optional resource overrides: max_duration_secs (5-30), max_memory (16M-128M), max_recursion_depth (up to 500)."""
        _status("🔄", f"iterating {ticket_id}: {feedback[:60]}")
        limit_msg = ctx.deps.check_limit("iterate_code", 1)
        if limit_msg:
            return limit_msg + " Present what you have to the user."
        vault = ctx.deps.vault
        ticket = vault.load_ticket(ticket_id)
        if not ticket:
            return f"{ticket_id} not found."

        current_code = vault.read_stage_file(ticket_id, "solution.py") or ""
        spec = vault.read_stage_file(ticket_id, "spec.md") or ""
        test_code = vault.read_stage_file(ticket_id, "test_solution.py") or ""
        fn_doc = _fn_docs(ticket.host_function_allowlist)
        schema_doc = "\n".join(f"  - {k}: {v}" for k, v in ticket.input_schema.items())

        if _pipeline_agent:
            iterate_prompt = (
                f"Modify existing code based on feedback.\n\nOriginal spec:\n{spec[:300]}\n"
                f"Feedback:\n{feedback}\n\nCurrent code:\n{current_code}\n"
                f"Current tests:\n{test_code}\n\nHost functions:\n{fn_doc}\n"
                + (f"Input parameters in `inputs` dict:\n{schema_doc}\n" if schema_doc else "")
                + "\nUpdate TESTS to cover feedback (keep original assertions, add new ones)."
                + "\nUpdate CODE to satisfy feedback and pass all tests."
                + "\nGenerate all sections: RESEARCH, TESTS, CODE, REVIEW."
            )
            sections = await _run_pipeline(iterate_prompt, "", "")
            new_test_code = _clean_llm_code(sections.get("tests", test_code))
            code = _clean_llm_code(sections.get("code", current_code))
        else:
            new_test_code = await _llm_call("test_writer",
                f"Original spec:\n{spec[:300]}\nFeedback:\n{feedback}\n"
                f"Current tests:\n{test_code}\n"
                f"Host functions:\n{fn_doc}\n"
                + (f"Input parameters:\n{schema_doc}\n" if schema_doc else "")
                + "Update tests to cover feedback. Keep original assertions. Add new ones. Return only code."
            )
            new_test_code = _clean_llm_code(new_test_code)
            code = await _llm_call("coder",
                f"Modify code. Keep requirements.\nSpec:\n{spec[:300]}\nCode:\n{current_code}\n"
                f"Feedback:\n{feedback}\nTests to pass:\n{new_test_code}\n"
                f"Host functions:\n{fn_doc}\n"
                + (f"Input parameters in `inputs` dict:\n{schema_doc}\n" if schema_doc else "")
                + "Return only Python code."
            )
            code = _clean_llm_code(code)

        new_test_code, _ = _verify_tests(new_test_code)
        vault.write_stage_file(ticket_id, "test_solution.py", new_test_code)
        code, _ = _verify_code(code, ticket.host_function_allowlist, spec + "\n" + feedback, ticket.input_schema)

        vault.write_stage_file(ticket_id, "solution.py", code)
        ticket.status = TicketStatus.ITERATING
        vault.save_ticket(ticket)
        vault.commit(ticket_id, f"iterate: {feedback[:40]}")

        # Syntax check + retry loop
        limits = ctx.deps.settings.sandbox.clamp(sandbox_limits)
        test_inputs = runtime_inputs or {k: "test" for k in ticket.input_schema}
        MAX_ITER_RETRIES = 0 if _pipeline_agent else 1

        for attempt in range(MAX_ITER_RETRIES + 1):
            syntax_err = _syntax_check(code)
            if syntax_err and attempt < MAX_ITER_RETRIES:
                _status("🔧", f"syntax error, retrying: {syntax_err}")
                code = _clean_llm_code(await _llm_call("coder",
                    f"Syntax error: {syntax_err}\n\n{code}\n\nFix. Return only Python code."))
                code, _ = _verify_code(code, ticket.host_function_allowlist, spec + "\n" + feedback, ticket.input_schema)
                vault.write_stage_file(ticket_id, "solution.py", code)
                vault.commit(ticket_id, f"iterate syntax fix {attempt + 1}")
                continue
            if syntax_err:
                return f"Updated but syntax error: {syntax_err}"

            test_result = run_tests(code, new_test_code, ticket.host_function_allowlist, test_inputs, limits=limits)
            if test_result.passed:
                break
            if attempt < MAX_ITER_RETRIES:
                _status("🔧", f"tests failed, retrying: {'; '.join(test_result.failures[:2])}")
                code = _clean_llm_code(await _llm_call("coder",
                    f"Code failed tests.\nError: {'; '.join(test_result.failures[:3])}\n\n"
                    f"Code:\n{code}\n\nTests:\n{new_test_code}\n\nFix. Return only Python code."))
                code, _ = _verify_code(code, ticket.host_function_allowlist, spec + "\n" + feedback, ticket.input_schema)
                vault.write_stage_file(ticket_id, "solution.py", code)
                vault.commit(ticket_id, f"iterate test fix {attempt + 1}")

        run_result = _run_and_record(vault, ticket_id, code, ticket.host_function_allowlist, test_inputs, limits=limits, interactive=sys.stdin.isatty())

        if run_result.success and test_result.passed:
            ticket.status = TicketStatus.APPROVED
            vault.save_ticket(ticket)
            vault.commit(ticket_id, "approved")
            return f"{ticket_id} updated. Tests passed. Use peek_result({ticket_id!r}) to get data for user response."
        if run_result.success:
            return f"{ticket_id} updated. Tests failed: {'; '.join(test_result.failures[:3])}"
        return f"Updated but failed: {run_result.error}"

    agent._pipeline_usage = _pipeline_usage  # type: ignore[attr-defined]
    agent._pipeline_history = _pipeline_history  # type: ignore[attr-defined]
    return agent
