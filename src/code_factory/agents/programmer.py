from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings

from ..config import Settings, get_settings, resolve_model
from ..sandbox.registry import get_descriptions as _get_host_descriptions
from ..sandbox.runner import run_tests
from ..vault.manager import VaultManager
from ..vault.models import TicketStatus
from .code_utils import (
    clean_llm_code,
    fn_docs,
    run_and_record,
    status,
    syntax_check,
    verify_code,
    verify_tests,
)
from .coder import build_coder
from .test_writer import build_test_writer


@dataclass
class FactoryDeps:
    vault: VaultManager
    settings: Settings
    interactive: bool = True
    current_ticket_id: str | None = None
    _used_tools: dict[str, int] = field(default_factory=dict)


def build_programmer(settings: Settings | None = None) -> Agent[FactoryDeps, str]:
    if settings is None:
        settings = get_settings()

    host_descs = _get_host_descriptions()
    fn_names = list(host_descs.keys())

    ms = ModelSettings(max_tokens=settings.max_tokens)

    agent: Agent[FactoryDeps, str] = Agent(
        resolve_model(settings.model_main, settings),
        deps_type=FactoryDeps,
        model_settings=ms,
        instructions=f"""You are a programmer. Users describe what they want in natural language. Your job is to understand, clarify, and build it.

Workflow:
1. UNDERSTAND — Read the requirement. Call list_functions to see what data sources exist.
2. CLARIFY — If the requirement is vague, ambiguous, or you're unsure which functions to use, call ask_human. Propose options. Never guess silently.
3. CHECK VAULT — Call search_vault to see if a similar program already exists. If found, use run_existing.
4. PLAN — Tell the user what you'll build and which functions you'll use. Call ask_human to confirm before coding.
5. CODE — Call generate_code with a precise spec, only the specific host functions needed, input schema, and runtime inputs.
6. DELIVER — Call peek_result to get output data. Present results in plain language. Never show code.
7. SAVE — Call save_ticket when user is satisfied.

Rules:
- NEVER send all host functions to generate_code. Pick only the ones the task needs.
- NEVER invent data or capabilities. You can ONLY use the host functions listed.
- If no host function matches what the user needs, tell them and suggest alternatives or ask them to register one.
- Present results in plain language. Never show raw code to the user.
- When reusing a ticket, extract the right runtime inputs from the user's query.
- When the user asks to show, get, or look up specific data (e.g. "show payment history for loan X"), ALWAYS call search_vault first — a reusable program likely exists. Use run_existing if found instead of generating new code.
- When the user references an existing ticket (TKT-xxxx) and wants changes, use iterate_code — not generate_code.
- If the user gives an affirmative reply ("yes", "go ahead", "try again") after you proposed a plan or reported an error, PROCEED with the action. Do not ask again.

Available host functions:
{chr(10).join(f'- {desc}' for desc in host_descs.values())}""",
        name="programmer",
    )

    coder = build_coder(settings)
    test_writer = build_test_writer(settings)

    async def _llm_call(sub_agent: Agent, prompt: str) -> str:
        r = await asyncio.wait_for(sub_agent.run(prompt), timeout=120)
        return r.output

    @agent.tool
    async def ask_human(ctx: RunContext[FactoryDeps], question: str,
                        options: list[str] | None = None) -> str:
        """Ask the user a clarifying question. Use when the requirement is ambiguous, no function matches, you want to propose alternatives, or you need to confirm your plan before coding."""
        count = ctx.deps._used_tools.get("ask_human", 0) + 1
        ctx.deps._used_tools["ask_human"] = count
        if count > 3:
            return "You have already asked multiple questions. Proceed with the information you have — use list_functions, search_vault, or generate_code."
        if not ctx.deps.interactive:
            return "Non-interactive mode. Proceed with best guess."
        prompt = question
        if options:
            prompt += "\n" + "\n".join(f"  {i+1}. {o}" for i, o in enumerate(options))
        prompt += "\n> "
        loop = asyncio.get_event_loop()
        answer = await loop.run_in_executor(None, lambda: input(prompt))
        return answer.strip() or "(no answer)"

    @agent.tool
    async def list_functions(ctx: RunContext[FactoryDeps],  # noqa: ARG001
                             filter: str | None = None) -> str:
        """List available host functions. Optionally filter by keyword to find relevant ones."""
        descs = _get_host_descriptions()
        if filter:
            kw = filter.lower()
            descs = {k: v for k, v in descs.items() if kw in k.lower() or kw in v.lower()}
        if not descs:
            return "No matching functions found."
        return "\n".join(f"- {v}" for v in descs.values())

    @agent.tool
    async def search_vault(ctx: RunContext[FactoryDeps], query: str) -> str:
        """Search vault for existing reusable programs. Returns matching tickets with their IDs and titles."""
        vault = ctx.deps.vault
        results = vault.search(query)
        reusable = [r for r in results if r.status in (
            TicketStatus.APPROVED, TicketStatus.CLOSED, TicketStatus.REUSED)]
        if not reusable:
            return "No existing programs found."
        lines = []
        for r in reusable[:5]:
            lines.append(f"- {r.id}: {r.title} (functions: {', '.join(r.host_functions)}, inputs: {', '.join(r.input_keys)})")
        return "\n".join(lines)

    @agent.tool
    async def run_existing(ctx: RunContext[FactoryDeps], ticket_id: str,
                           runtime_inputs: dict[str, str] | None = None) -> str:
        """Reuse an existing vault program with new inputs. Use after search_vault finds a match."""
        vault = ctx.deps.vault
        ticket = vault.load_ticket(ticket_id)
        if not ticket:
            return f"{ticket_id} not found."
        code = vault.read_stage_file(ticket_id, "solution.py")
        if not code:
            return f"{ticket_id} has no solution code."
        ri = runtime_inputs or {}
        if not ri and ticket.input_schema:
            return f"{ticket_id} needs inputs: {ticket.input_schema}. Call ask_human to get values from user."
        ticket.status = TicketStatus.REUSED
        vault.save_ticket(ticket)
        vault.commit(ticket_id, "reused")
        limits = ctx.deps.settings.sandbox.clamp()
        run_result = run_and_record(vault, ticket_id, code, ticket.host_function_allowlist,
                                    ri, limits=limits, interactive=ctx.deps.interactive)
        ctx.deps.current_ticket_id = ticket_id
        if run_result.success:
            return f"DONE. Reused {ticket_id} ({ticket.title}). Call peek_result to get output."
        return f"FAILED reuse of {ticket_id}: {(run_result.error or 'unknown error')[:200]}"

    @agent.tool
    async def generate_code(ctx: RunContext[FactoryDeps], spec: str,
                            host_functions: list[str],
                            title: str | None = None,
                            input_schema: dict[str, str] | None = None,
                            runtime_inputs: dict[str, str] | None = None) -> str:
        """Generate, test, and run a new program. Provide a precise spec, only the specific host functions needed (NOT all of them), input schema for parameters, and runtime input values for this run."""
        vault = ctx.deps.vault
        valid = [f for f in host_functions if f in host_descs] or fn_names
        limits = ctx.deps.settings.sandbox.clamp()

        status("📝", f"creating ticket: {title or spec[:50]}")
        ticket = vault.create_ticket(title or spec[:60], spec, host_functions=valid)
        if input_schema:
            ticket.input_schema = input_schema
            vault.save_ticket(ticket)
            vault.commit(ticket.id, "set input_schema")
        ctx.deps.current_ticket_id = ticket.id
        ticket_id = ticket.id

        func_doc = fn_docs(valid)
        schema_doc = "\n".join(f"  - {k}: {v}" for k, v in (input_schema or {}).items())
        test_inputs = runtime_inputs or {k: "test" for k in (input_schema or {})}

        # Generate tests
        status("✏️", "generating tests...")
        ticket.status = TicketStatus.GATHERING
        vault.save_ticket(ticket)
        test_prompt = f"Spec:\n{spec}\n\nHost functions:\n{func_doc}\n\n"
        if schema_doc:
            test_prompt += f"Code reads parameters from `inputs` dict:\n{schema_doc}\nTests can assume `inputs` is populated. Test `result` structure.\n\n"
        test_prompt += "Write assert statements to validate `result`. Return only code."
        test_code = clean_llm_code(await _llm_call(test_writer, test_prompt))
        test_code, _ = verify_tests(test_code)
        vault.write_stage_file(ticket_id, "test_solution.py", test_code)
        ticket.status = TicketStatus.TESTS_DRAFTED
        vault.save_ticket(ticket)
        vault.commit(ticket_id, "generate tests")

        # Generate code
        status("💻", "writing solution...")
        code_prompt = f"Spec:\n{spec}\n\nTests your code must pass:\n{test_code}\n\nHost functions:\n{func_doc}\n\n"
        if schema_doc:
            code_prompt += f"Runtime parameters available in `inputs` dict:\n{schema_doc}\nRead ALL request-specific values from inputs[\"key\"]. Never hardcode them.\n\n"
        code_prompt += "Return only Python code."
        code = clean_llm_code(await _llm_call(coder, code_prompt))
        code, _ = verify_code(code, ticket.host_function_allowlist, spec, input_schema)
        vault.write_stage_file(ticket_id, "solution.py", code)
        ticket.status = TicketStatus.CODE_DRAFTED
        vault.save_ticket(ticket)
        vault.commit(ticket_id, "generate code")

        # Syntax check + test + retry
        MAX_RETRIES = 1
        test_result = None
        for attempt in range(MAX_RETRIES + 1):
            syntax_err = syntax_check(code)
            if syntax_err and attempt < MAX_RETRIES:
                status("🔧", f"syntax error, retrying: {syntax_err}")
                code = clean_llm_code(await _llm_call(coder,
                    f"This code has a syntax error: {syntax_err}\n\n{code}\n\nFix the error. Return only Python code."))
                code, _ = verify_code(code, ticket.host_function_allowlist, spec, input_schema)
                vault.write_stage_file(ticket_id, "solution.py", code)
                vault.commit(ticket_id, f"syntax fix attempt {attempt + 1}")
                continue
            if syntax_err:
                return f"{ticket_id} FAILED: syntax error: {syntax_err[:100]}"

            status("🧪", "running tests...")
            test_result = run_tests(code, test_code, ticket.host_function_allowlist, test_inputs, limits=limits)
            if test_result.passed:
                status("✅", "tests passed")
                break
            status("❌", f"tests failed: {'; '.join(test_result.failures[:2])}")
            if attempt < MAX_RETRIES:
                status("🔧", "retrying with error feedback...")
                code = clean_llm_code(await _llm_call(coder,
                    f"Code failed tests.\nError: {'; '.join(test_result.failures[:3])}\n\nCode:\n{code}\n\nTests:\n{test_code}\n\nHost functions:\n{func_doc}\n\nFix the code to pass tests. Return only Python code."))
                code, _ = verify_code(code, ticket.host_function_allowlist, spec, input_schema)
                vault.write_stage_file(ticket_id, "solution.py", code)
                vault.commit(ticket_id, f"test fix attempt {attempt + 1}")

        # Run solution
        status("▶️", "executing solution...")
        run_result = run_and_record(vault, ticket_id, code, ticket.host_function_allowlist,
                                    test_inputs, limits=limits, interactive=ctx.deps.interactive)

        if run_result.success and test_result and test_result.passed:
            ticket.status = TicketStatus.APPROVED
            vault.save_ticket(ticket)
            vault.commit(ticket_id, "approved")
            return f"{ticket_id} DONE. Tests passed. Call peek_result to get output data."

        if run_result.success:
            ticket.status = TicketStatus.APPROVED
            vault.save_ticket(ticket)
            vault.commit(ticket_id, "approved (tests imprecise)")
            return f"{ticket_id} DONE. Call peek_result to get data."

        return f"{ticket_id} FAILED: {(run_result.error or 'unknown error')[:200]}"

    @agent.tool
    async def iterate_code(ctx: RunContext[FactoryDeps], ticket_id: str, feedback: str,
                           runtime_inputs: dict[str, str] | None = None) -> str:
        """Modify an existing program based on feedback. Use when the user wants changes to a program that already exists."""
        vault = ctx.deps.vault
        ticket = vault.load_ticket(ticket_id)
        if not ticket:
            return f"{ticket_id} not found."

        current_code = vault.read_stage_file(ticket_id, "solution.py") or ""
        spec = vault.read_stage_file(ticket_id, "spec.md") or ""
        test_code = vault.read_stage_file(ticket_id, "test_solution.py") or ""
        func_doc = fn_docs(ticket.host_function_allowlist)
        schema_doc = "\n".join(f"  - {k}: {v}" for k, v in ticket.input_schema.items())

        status("🔄", f"iterating {ticket_id}: {feedback[:60]}")

        # Update tests
        new_test_code = clean_llm_code(await _llm_call(test_writer,
            f"Original spec:\n{spec[:300]}\nFeedback:\n{feedback}\n"
            f"Current tests:\n{test_code}\nHost functions:\n{func_doc}\n"
            + (f"Input parameters:\n{schema_doc}\n" if schema_doc else "")
            + "Update tests to cover feedback. Keep original assertions. Add new ones. Return only code."))
        new_test_code, _ = verify_tests(new_test_code)
        vault.write_stage_file(ticket_id, "test_solution.py", new_test_code)

        # Update code
        code = clean_llm_code(await _llm_call(coder,
            f"Modify code. Keep requirements.\nSpec:\n{spec[:300]}\nCode:\n{current_code}\n"
            f"Feedback:\n{feedback}\nTests to pass:\n{new_test_code}\nHost functions:\n{func_doc}\n"
            + (f"Input parameters in `inputs` dict:\n{schema_doc}\n" if schema_doc else "")
            + "Return only Python code."))
        code, _ = verify_code(code, ticket.host_function_allowlist, spec + "\n" + feedback, ticket.input_schema)
        vault.write_stage_file(ticket_id, "solution.py", code)
        ticket.status = TicketStatus.ITERATING
        vault.save_ticket(ticket)
        vault.commit(ticket_id, f"iterate: {feedback[:40]}")

        # Test + run
        limits = ctx.deps.settings.sandbox.clamp()
        test_inputs = runtime_inputs or {k: "test" for k in ticket.input_schema}

        syntax_err = syntax_check(code)
        if syntax_err:
            return f"Updated but syntax error: {syntax_err}"

        test_result = run_tests(code, new_test_code, ticket.host_function_allowlist, test_inputs, limits=limits)
        run_result = run_and_record(vault, ticket_id, code, ticket.host_function_allowlist,
                                    test_inputs, limits=limits, interactive=ctx.deps.interactive)

        if run_result.success and test_result.passed:
            ticket.status = TicketStatus.APPROVED
            vault.save_ticket(ticket)
            vault.commit(ticket_id, "approved")
            return f"{ticket_id} updated. Tests passed. Call peek_result to get data."
        if run_result.success:
            return f"{ticket_id} updated. Tests failed: {'; '.join(test_result.failures[:3])}"
        return f"Updated but failed: {(run_result.error or 'unknown error')[:200]}"

    @agent.tool
    async def peek_result(ctx: RunContext[FactoryDeps], ticket_id: str,
                          max_chars: int = 500) -> str:
        """Get the latest run output for a ticket. Call after generate_code or run_existing to get data for your response to the user."""
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
    async def save_ticket(ctx: RunContext[FactoryDeps], ticket_id: str) -> str:
        """Save and close a ticket after the user is satisfied with the result."""
        status("🔒", f"closing {ticket_id}")
        vault = ctx.deps.vault
        ticket = vault.load_ticket(ticket_id)
        if not ticket:
            return f"{ticket_id} not found."
        if ticket.status == TicketStatus.CLOSED:
            return f"{ticket_id} already closed."
        ticket.status = TicketStatus.CLOSED
        vault.save_ticket(ticket)
        vault.commit(ticket_id, "closed")
        return f"{ticket_id} closed."

    return agent
