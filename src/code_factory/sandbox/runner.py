from dataclasses import dataclass, field
from typing import Any

from .host_functions import build_external_lookup


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


def run_solution(code: str, inputs: dict[str, Any] | None = None,
                 host_function_allowlist: list[str] | None = None,
                 limits: dict[str, float | int] | None = None) -> RunResult:
    try:
        from pydantic_monty import Monty, ResourceLimits
    except ImportError:
        return _run_fallback(code, inputs, host_function_allowlist)

    external_lookup = build_external_lookup(host_function_allowlist)
    wrapped = {"inputs": inputs or {}}
    resource_limits = ResourceLimits(**limits) if limits else None
    try:
        with Monty() as pool:
            with pool.checkout(limits=resource_limits) as session:
                session.feed_run(
                    code,
                    inputs=wrapped,
                    external_lookup=external_lookup,
                )
                value = session.feed_run("result")
                return RunResult(success=True, value=value)
    except Exception as e:
        return RunResult(success=False, error=f"{type(e).__name__}: {e}")


def run_tests(solution_code: str, test_code: str,
              host_function_allowlist: list[str] | None = None,
              inputs: dict[str, Any] | None = None,
              limits: dict[str, float | int] | None = None) -> TestResult:
    try:
        from pydantic_monty import Monty, ResourceLimits
    except ImportError:
        return _run_tests_fallback(solution_code, test_code, host_function_allowlist, inputs)

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


def _run_fallback(code: str, inputs: dict[str, Any] | None = None,
                  host_function_allowlist: list[str] | None = None) -> RunResult:
    external_lookup = build_external_lookup(host_function_allowlist)
    namespace = {"inputs": inputs or {}, **external_lookup}
    try:
        exec(compile(code, "<solution>", "exec"), namespace)  # noqa: S102
        result = namespace.get("result", namespace.get("output", None))
        if result is None:
            callables = [k for k, v in namespace.items()
                         if callable(v) and not k.startswith("_") and k not in external_lookup]
            if callables:
                result = namespace[callables[-1]]()
        return RunResult(success=True, value=result)
    except Exception as e:
        return RunResult(success=False, error=f"{type(e).__name__}: {e}")


def _run_tests_fallback(solution_code: str, test_code: str,
                        host_function_allowlist: list[str] | None = None,
                        inputs: dict[str, Any] | None = None) -> TestResult:
    external_lookup = build_external_lookup(host_function_allowlist)
    namespace = {"inputs": inputs or {}, **external_lookup}
    combined = f"{solution_code}\n\n{test_code}"
    total = test_code.count("assert ")
    try:
        exec(compile(combined, "<test>", "exec"), namespace)  # noqa: S102
        return TestResult(passed=True, total=total)
    except AssertionError as e:
        return TestResult(passed=False, failures=[f"AssertionError: {e}"], total=total)
    except Exception as e:
        return TestResult(passed=False, failures=[f"{type(e).__name__}: {e}"], total=total)
