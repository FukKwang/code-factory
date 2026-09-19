from dataclasses import dataclass, field
from typing import Any

from .host_functions import (
    AskChoiceArgs,
    AskConfirmArgs,
    AskNumberArgs,
    AskUserArgs,
)
from .registry import build_external_lookup, get_human_input_functions


@dataclass
class RunResult:
    success: bool = False
    value: Any = None
    error: str | None = None
    suspensions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TestResult:
    passed: bool = False
    failures: list[str] = field(default_factory=list)
    total: int = 0


def _collect_human_input(call_name: str, call_args: tuple) -> Any:
    """Prompt user in TUI for ask_* host function calls."""
    args_dict = call_args[0] if call_args else {}

    if call_name == "ask_user":
        parsed = AskUserArgs.model_validate(args_dict)
        return input(f"  📝 {parsed.prompt}: ")

    if call_name == "ask_number":
        parsed = AskNumberArgs.model_validate(args_dict)
        prompt = parsed.prompt
        if parsed.min is not None or parsed.max is not None:
            bounds = f" [{parsed.min or ''}-{parsed.max or ''}]"
            prompt += bounds
        while True:
            raw = input(f"  🔢 {prompt}: ")
            try:
                val = float(raw)
                if parsed.min is not None and val < parsed.min:
                    print(f"    Must be >= {parsed.min}")
                    continue
                if parsed.max is not None and val > parsed.max:
                    print(f"    Must be <= {parsed.max}")
                    continue
                return val
            except ValueError:
                print("    Enter a number.")

    if call_name == "ask_confirm":
        parsed = AskConfirmArgs.model_validate(args_dict)
        raw = input(f"  ❓ {parsed.prompt} [y/n]: ").strip().lower()
        return raw in ("y", "yes")

    if call_name == "ask_choice":
        parsed = AskChoiceArgs.model_validate(args_dict)
        print(f"  📋 {parsed.prompt}")
        for i, opt in enumerate(parsed.options, 1):
            print(f"    {i}. {opt}")
        while True:
            raw = input("  Pick number: ").strip()
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(parsed.options):
                    return parsed.options[idx]
                print(f"    Enter 1-{len(parsed.options)}")
            except ValueError:
                print(f"    Enter 1-{len(parsed.options)}")

    return None


def run_solution(code: str, inputs: dict[str, Any] | None = None,
                 host_function_allowlist: list[str] | None = None,
                 limits: dict[str, float | int] | None = None,
                 interactive: bool = True) -> RunResult:
    try:
        from pydantic_monty import Monty, MontyComplete, ResourceLimits
    except ImportError as e:
        raise RuntimeError("pydantic-monty is required for sandboxed execution: pip install pydantic-monty") from e

    external_lookup = build_external_lookup(host_function_allowlist)
    wrapped = {"inputs": inputs or {}}
    resource_limits = ResourceLimits(**limits) if limits else None

    has_human_funcs = any(f in (host_function_allowlist or []) for f in get_human_input_functions())
    if not has_human_funcs:
        # No ask_* functions — use fast path
        try:
            with Monty() as pool:
                with pool.checkout(limits=resource_limits) as session:
                    session.feed_run(code, inputs=wrapped, external_lookup=external_lookup)
                    value = session.feed_run("result")
                    return RunResult(success=True, value=value)
        except Exception as e:
            return RunResult(success=False, error=f"{type(e).__name__}: {e}")

    # Snapshot loop — handles ask_* via human input
    suspensions = []
    try:
        with Monty() as pool:
            with pool.checkout(limits=resource_limits) as session:
                snapshot = session.feed_start(code, inputs=wrapped)

                while not isinstance(snapshot, MontyComplete):
                    name = snapshot.function_name
                    args = snapshot.args

                    suspensions.append({"function": name, "args": list(args)})

                    if name in get_human_input_functions():
                        if interactive:
                            value = _collect_human_input(name, args)
                        else:
                            value = external_lookup[name](*args) if name in external_lookup else None
                        snapshot = snapshot.resume({"return_value": value})
                    elif name in external_lookup:
                        result = external_lookup[name](*args)
                        snapshot = snapshot.resume({"return_value": result})
                    else:
                        snapshot = snapshot.resume_not_handled()

                value = session.feed_run("result")
                return RunResult(success=True, value=value, suspensions=suspensions)
    except Exception as e:
        return RunResult(success=False, error=f"{type(e).__name__}: {e}", suspensions=suspensions)


def run_tests(solution_code: str, test_code: str,
              host_function_allowlist: list[str] | None = None,
              inputs: dict[str, Any] | None = None,
              limits: dict[str, float | int] | None = None) -> TestResult:
    try:
        from pydantic_monty import Monty, ResourceLimits
    except ImportError as e:
        raise RuntimeError("pydantic-monty is required for sandboxed execution: pip install pydantic-monty") from e

    external_lookup = build_external_lookup(host_function_allowlist)
    wrapped = {"inputs": inputs or {}}
    combined = f"{solution_code}\n\n{test_code}"
    resource_limits = ResourceLimits(**limits) if limits else None
    try:
        with Monty() as pool:
            with pool.checkout(limits=resource_limits) as session:
                session.feed_run(combined, inputs=wrapped, external_lookup=external_lookup)
                return TestResult(passed=True, total=test_code.count("assert "))
    except Exception as e:
        return TestResult(passed=False, failures=[str(e)], total=test_code.count("assert "))


