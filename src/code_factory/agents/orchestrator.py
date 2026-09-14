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

MAX_TOOL_CALLS = 2

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

    # Check 2: host functions actually called
    called = [fn for fn in allowlist if fn + "(" in code]
    if not called:
        warnings.append(f"WARNING: code calls none of {allowlist}. Likely hardcoded data.")

    # Check 3: no function redefinitions of host functions
    for fn in allowlist:
        if re.search(rf'^def\s+{fn}\s*\(', code, re.MULTILINE):
            code = re.sub(rf'^def\s+{fn}\s*\(.*?(?=\ndef\s|\Z)', '', code, flags=re.MULTILINE | re.DOTALL)
            warnings.append(f"auto-fixed: removed redefinition of host function {fn}")

    # Check 4: code uses inputs dict when input_schema defined
    if input_schema and 'inputs[' not in code and 'inputs.get(' not in code:
        warnings.append("WARNING: code does not read from inputs dict. May have hardcoded values.")

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
        instructions=f"""Coding agent that creates REUSABLE programs.

Pipeline:
1. search_vault(query, host_functions=[...], input_keys=[...]) — ALWAYS pass host_functions and input_keys for structural matching
2. If found: auto-runs with extracted inputs, returns result
3. If structural match returned: use iterate_code to extend existing ticket, do NOT create duplicate
4. If not found: IMMEDIATELY create_ticket. Do NOT call search_vault again with rephrased query.
5. generate_and_test(ticket_id, spec, runtime_inputs) — full TDD pipeline
6. Present result in plain language. Never show code.

IMPORTANT: Call search_vault AT MOST ONCE per user request. If it returns nothing, create a new ticket immediately.
IMPORTANT: ALWAYS pass host_functions and input_keys to search_vault. This enables deterministic structural dedup.

IMPORTANT — Generalization:
- Ticket title = reusable program name, NOT specific query. Example: "Find loans by borrower name", NOT "Find loans for borrower ABC"
- input_schema = parameters the program needs. Example: {{"borrower_name": "Name of borrower to look up"}}
- runtime_inputs = specific values for THIS run. Example: {{"borrower_name": "ABC"}}
- close_ticket(ticket_id) and iterate_code(ticket_id, feedback) can be called directly without search_vault first.

Human input functions (ask_user, ask_number, ask_confirm, ask_choice):
- Use these when the program needs runtime input from the user beyond initial parameters.
- Sandbox pauses at each call, prompts user, resumes with their answer.
- Include in host_functions allowlist when needed.
- During tests, these return mock defaults automatically.

Rules:
- Never invent ticket_ids. Only use IDs from search_vault or create_ticket.
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
        r = await _sub_agents[role].run(prompt)
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
    async def search_vault(ctx: RunContext[FactoryDeps], query: str,
                           host_functions: list[str] | None = None,
                           input_keys: list[str] | None = None) -> str:
        """Search for existing reusable program. Pass host_functions and input_keys for structural matching. Auto-runs if found."""
        _status("🔍", f"searching vault: {query}")
        limit_msg = ctx.deps.check_limit("search_vault")
        if limit_msg:
            return limit_msg
        vault = ctx.deps.vault
        reusable_statuses = (TicketStatus.APPROVED, TicketStatus.CLOSED, TicketStatus.REUSED, TicketStatus.ITERATING)

        if host_functions and input_keys:
            structural = vault.search_structural(host_functions, input_keys)
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
                    runtime_inputs = _extract_inputs_from_query(query, ticket.input_schema)
                    ticket.status = TicketStatus.REUSED
                    vault.save_ticket(ticket)
                    vault.commit(m.ticket.id, "reused (structural exact)")
                    limits = ctx.deps.settings.sandbox.clamp()
                    run_result = _run_and_record(
                        vault, m.ticket.id, code, ticket.host_function_allowlist, runtime_inputs,
                        limits=limits, interactive=sys.stdin.isatty(),
                    )
                    if run_result.success:
                        return (
                            f"DONE. Reused {m.ticket.id} ({m.ticket.title}) [structural exact match]. "
                            f"Inputs: {runtime_inputs}. Do NOT create_ticket.\n"
                            f"Result:\n{str(run_result.value)[:800]}"
                        )
                    return f"Reuse {m.ticket.id} failed: {run_result.error}. Use iterate_code({m.ticket.id!r}, ...) to fix."
                return (
                    f"STRUCTURAL MATCH: {m.ticket.id} ({m.ticket.title}) — {m.match_type} "
                    f"(fn_overlap={m.fn_overlap:.0%}, input_overlap={m.input_overlap:.0%}). "
                    f"Consider iterate_code({m.ticket.id!r}, ...) to extend it instead of creating duplicate."
                )

        results = vault.search(query)
        if not results:
            return "NONE FOUND. Do NOT search again. Use create_ticket NOW with generalized title and input_schema."
        reusable = [r for r in results if r.status in reusable_statuses]
        if reusable:
            best = reusable[0]
            code = vault.read_stage_file(best.id, "solution.py")
            if code:
                ticket = vault.load_ticket(best.id)
                if ticket:
                    runtime_inputs = _extract_inputs_from_query(query, ticket.input_schema)
                    ticket.status = TicketStatus.REUSED
                    vault.save_ticket(ticket)
                    vault.commit(best.id, "reused")
                    limits = ctx.deps.settings.sandbox.clamp()
                    run_result = _run_and_record(
                        vault, best.id, code, ticket.host_function_allowlist, runtime_inputs,
                        limits=limits, interactive=sys.stdin.isatty(),
                    )
                    if run_result.success:
                        return (
                            f"DONE. Reused {best.id} ({best.title}) with inputs {runtime_inputs}. "
                            f"Present this result to user. Do NOT call generate_and_test or create_ticket.\n"
                            f"Result:\n{str(run_result.value)[:800]}"
                        )
                    return f"Reuse {best.id} failed: {run_result.error}. Use iterate_code({best.id!r}, ...) to fix."
        return "NONE FOUND. Do NOT search again. Use create_ticket NOW with generalized title and input_schema."

    @agent.tool
    async def create_ticket(ctx: RunContext[FactoryDeps], title: str, requirements: str,
                            host_functions: list[str],
                            input_schema: dict[str, str] | None = None) -> str:
        """Create ticket for reusable program. Title must be GENERALIZED (e.g. 'Find loans by borrower name'). input_schema maps parameter names to descriptions."""
        _status("📝", f"creating ticket: {title}")
        valid = [f for f in host_functions if f in HOST_FUNCTION_DESCRIPTIONS] or fn_names
        vault = ctx.deps.vault
        dupes = vault.search_structural(valid, list((input_schema or {}).keys()))
        exact = [d for d in dupes if d.match_type == "exact" and d.ticket.status in (
            TicketStatus.APPROVED, TicketStatus.CLOSED, TicketStatus.REUSED,
        )]
        if exact:
            return (
                f"DUPLICATE BLOCKED. {exact[0].ticket.id} ({exact[0].ticket.title}) has identical "
                f"host_functions and input_keys. Use search_vault or iterate_code({exact[0].ticket.id!r}, ...) instead."
            )
        ticket = vault.create_ticket(title, requirements, host_functions=valid)
        if input_schema:
            ticket.input_schema = input_schema
            ctx.deps.vault.save_ticket(ticket)
            ctx.deps.vault.commit(ticket.id, "set input_schema")
        ctx.deps.current_ticket_id = ticket.id
        return f"{ticket.id} created. Call generate_and_test with spec and runtime_inputs."

    @agent.tool
    async def generate_and_test(ctx: RunContext[FactoryDeps], ticket_id: str, spec: str,
                                runtime_inputs: dict[str, str] | None = None,
                                sandbox_limits: dict[str, float | int | None] | None = None) -> str:
        """Full TDD pipeline: research, tests, code, validate. runtime_inputs = specific values for this run.
        sandbox_limits = optional resource overrides: max_duration_secs (5-30), max_memory (16M-128M), max_recursion_depth (up to 500)."""
        _status("⚙️", f"generate_and_test: {ticket_id}")
        vault = ctx.deps.vault
        limits = ctx.deps.settings.sandbox.clamp(sandbox_limits)
        _status("📏", f"sandbox limits: {limits['max_duration_secs']}s, {limits['max_memory'] // 1_000_000}MB, depth={limits['max_recursion_depth']}")
        ticket = vault.load_ticket(ticket_id)
        if not ticket:
            return f"{ticket_id} not found."

        # If already has working solution, just re-run with inputs
        if ticket.status in (TicketStatus.APPROVED, TicketStatus.CLOSED, TicketStatus.REUSED):
            code = vault.read_stage_file(ticket_id, "solution.py")
            if code:
                run_inputs = runtime_inputs or _extract_inputs_from_query(spec, ticket.input_schema)
                run_result = _run_and_record(vault, ticket_id, code, ticket.host_function_allowlist, run_inputs, limits=limits, interactive=sys.stdin.isatty())
                if run_result.success:
                    return f"Already solved. Re-ran with {run_inputs}. Result:\n{str(run_result.value)[:800]}"

        vault.write_stage_file(ticket_id, "spec.md", spec)
        fn_doc = _fn_docs(ticket.host_function_allowlist)
        schema_doc = "\n".join(f"  - {k}: {v}" for k, v in ticket.input_schema.items())
        test_inputs = runtime_inputs or {k: "test" for k in ticket.input_schema}

        if _pipeline_agent:
            # Single-call pipeline: one LLM call generates research + tests + code + review
            _status("🚀", "pipeline generating research + tests + code...")
            ticket.status = TicketStatus.GATHERING
            vault.save_ticket(ticket)

            sections = await _run_pipeline(spec, fn_doc, schema_doc)
            findings = sections.get("research", "")
            test_code = _strip_fences(sections.get("tests", ""))
            code = _strip_fences(sections.get("code", ""))
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
            # Multi-agent pipeline: separate LLM calls per role
            _status("🔬", "researcher analyzing requirements...")
            ticket.status = TicketStatus.GATHERING
            vault.save_ticket(ticket)

            findings = await _llm_call("researcher",
                f"Requirement: {spec}\n\nAvailable host functions:\n{fn_doc}"
                + (f"\n\nInput parameters:\n{schema_doc}" if schema_doc else "")
            )
            findings = _strip_fences(findings)
            write_findings_file(vault.ticket_dir(ticket_id) / "runs", "research", findings)
            vault.commit(ticket_id, "research")

            _status("✏️", "generating tests...")
            ticket.status = TicketStatus.SPEC_DRAFTED
            vault.save_ticket(ticket)
            vault.commit(ticket_id, "spec drafted")

            test_prompt = (
                f"Spec:\n{spec}\n\nResearch findings:\n{findings[:500]}\n\n"
                f"Host functions:\n{fn_doc}\n\n"
            )
            if schema_doc:
                test_prompt += (
                    f"Code reads parameters from `inputs` dict:\n{schema_doc}\n"
                    "Tests can assume `inputs` is populated. Test `result` structure.\n\n"
                )
            test_prompt += "Write assert statements to validate `result`. Return only code."

            test_code = await _llm_call("test_writer", test_prompt)
            test_code = _strip_fences(test_code)
            test_code, test_warnings = _verify_tests(test_code)
            vault.write_stage_file(ticket_id, "test_solution.py", test_code)
            ticket.status = TicketStatus.TESTS_DRAFTED
            vault.save_ticket(ticket)
            vault.commit(ticket_id, "generate tests")

            _status("💻", "coder writing solution...")
            code_prompt = (
                f"Spec:\n{spec}\n\nTests your code must pass:\n{test_code}\n\n"
                f"Host functions:\n{fn_doc}\n\n"
            )
            if schema_doc:
                code_prompt += (
                    f"Runtime parameters available in `inputs` dict:\n{schema_doc}\n"
                    "Read ALL request-specific values from inputs[\"key\"]. Never hardcode them.\n\n"
                )
            code_prompt += "Return only Python code."

            code = await _llm_call("coder", code_prompt)
            code = _strip_fences(code)
            code, code_warnings = _verify_code(code, ticket.host_function_allowlist, spec, ticket.input_schema)
            vault.write_stage_file(ticket_id, "solution.py", code)
            ticket.status = TicketStatus.CODE_DRAFTED
            vault.save_ticket(ticket)
            vault.commit(ticket_id, "generate code")

            review = ""

        # Run tests
        _status("🧪", "running tests...")
        test_result = run_tests(code, test_code, ticket.host_function_allowlist, test_inputs, limits=limits)
        if test_result.passed:
            _status("✅", "tests passed")
        else:
            _status("❌", f"tests failed: {'; '.join(test_result.failures[:2])}")
        vault.commit(ticket_id, f"tests {'pass' if test_result.passed else 'fail'}")

        # Run solution
        _status("▶️", "executing solution...")
        run_result = _run_and_record(vault, ticket_id, code, ticket.host_function_allowlist, test_inputs, limits=limits, interactive=sys.stdin.isatty())

        # Review (multi-agent generates it here; single-agent already has it)
        if not review:
            _status("📋", "reviewer evaluating output...")
            all_warnings = (test_warnings if 'test_warnings' in dir() else []) + (code_warnings if 'code_warnings' in dir() else [])
            review_input = (
                f"Requirement: {spec[:300]}\n"
                f"Tests: {'PASSED' if test_result.passed else 'FAILED: ' + '; '.join(test_result.failures[:3])}\n"
                f"Output: {str(run_result.value)[:500] if run_result.success else run_result.error}"
                + (f"\nAuto-fixes applied: {'; '.join(all_warnings)}" if all_warnings else "")
            )
            review = await _llm_call("reviewer", review_input)

        if run_result.success and test_result.passed:
            ticket.status = TicketStatus.APPROVED
            vault.save_ticket(ticket)
            vault.commit(ticket_id, "approved")
            return f"Tests passed. {review}\n\nResult:\n{str(run_result.value)[:800]}"

        if run_result.success:
            return f"Code ran but tests failed: {'; '.join(test_result.failures[:3])}. {review}\n\nResult:\n{str(run_result.value)[:500]}"

        return f"Failed: {run_result.error}. {review}"

    @agent.tool
    async def close_ticket(ctx: RunContext[FactoryDeps], ticket_id: str) -> str:
        """Close ticket after user satisfied. Call directly, no search_vault needed."""
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
    async def iterate_code(ctx: RunContext[FactoryDeps], ticket_id: str, feedback: str,
                           runtime_inputs: dict[str, str] | None = None,
                           sandbox_limits: dict[str, float | int | None] | None = None) -> str:
        """Modify existing program based on feedback. Call directly with ticket_id.
        sandbox_limits = optional resource overrides: max_duration_secs (5-30), max_memory (16M-128M), max_recursion_depth (up to 500)."""
        _status("🔄", f"iterating {ticket_id}: {feedback[:60]}")
        limit_msg = ctx.deps.check_limit("iterate_code", 2)
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
            new_test_code = _strip_fences(sections.get("tests", test_code))
            code = _strip_fences(sections.get("code", current_code))
        else:
            new_test_code = await _llm_call("test_writer",
                f"Original spec:\n{spec[:300]}\nFeedback:\n{feedback}\n"
                f"Current tests:\n{test_code}\n"
                f"Host functions:\n{fn_doc}\n"
                + (f"Input parameters:\n{schema_doc}\n" if schema_doc else "")
                + "Update tests to cover feedback. Keep original assertions. Add new ones. Return only code."
            )
            new_test_code = _strip_fences(new_test_code)
            code = await _llm_call("coder",
                f"Modify code. Keep requirements.\nSpec:\n{spec[:300]}\nCode:\n{current_code}\n"
                f"Feedback:\n{feedback}\nTests to pass:\n{new_test_code}\n"
                f"Host functions:\n{fn_doc}\n"
                + (f"Input parameters in `inputs` dict:\n{schema_doc}\n" if schema_doc else "")
                + "Return only Python code."
            )
            code = _strip_fences(code)

        new_test_code, _ = _verify_tests(new_test_code)
        vault.write_stage_file(ticket_id, "test_solution.py", new_test_code)
        code, _ = _verify_code(code, ticket.host_function_allowlist, spec + "\n" + feedback, ticket.input_schema)

        vault.write_stage_file(ticket_id, "solution.py", code)
        ticket.status = TicketStatus.ITERATING
        vault.save_ticket(ticket)
        vault.commit(ticket_id, f"iterate: {feedback[:40]}")

        # Run tests + solution
        limits = ctx.deps.settings.sandbox.clamp(sandbox_limits)
        test_inputs = runtime_inputs or {k: "test" for k in ticket.input_schema}
        test_result = run_tests(code, new_test_code, ticket.host_function_allowlist, test_inputs, limits=limits)
        run_result = _run_and_record(vault, ticket_id, code, ticket.host_function_allowlist, test_inputs, limits=limits, interactive=sys.stdin.isatty())

        if run_result.success and test_result.passed:
            ticket.status = TicketStatus.APPROVED
            vault.save_ticket(ticket)
            vault.commit(ticket_id, "approved")
            return f"Updated. Tests passed. Result:\n{str(run_result.value)[:800]}"
        if run_result.success:
            return f"Updated. Tests failed: {'; '.join(test_result.failures[:3])}. Result:\n{str(run_result.value)[:500]}"
        return f"Updated but failed: {run_result.error}"

    agent._pipeline_usage = _pipeline_usage  # type: ignore[attr-defined]
    agent._pipeline_history = _pipeline_history  # type: ignore[attr-defined]
    return agent
