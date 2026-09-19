"""Tests for pluggable check chain, retry decorator, and output validation."""
import asyncio

from code_factory.agents.code_utils import (
    CheckCtx,
    CODE_CHECKS,
    check_host_fn_usage,
    check_inputs_usage,
    check_missing_imports,
    check_no_redefines,
    check_result_assignment,
    validate_output,
    verify_code,
    with_remediation,
)


# ---------------------------------------------------------------------------
# 1. Pluggable check chain
# ---------------------------------------------------------------------------

class TestCheckResultAssignment:
    def test_adds_result_call_for_no_arg_function(self):
        code = "def main():\n    return 42"
        ctx = CheckCtx([], "")
        new_code, warnings = check_result_assignment(code, ctx)
        assert "result = main()" in new_code
        assert any("auto-fixed" in w for w in warnings)

    def test_adds_result_call_with_spec_args(self):
        code = "def compute(name):\n    return name"
        ctx = CheckCtx([], "'Ahmad'")
        new_code, warnings = check_result_assignment(code, ctx)
        assert "result = compute('Ahmad')" in new_code

    def test_noop_when_result_exists(self):
        code = "result = 42"
        ctx = CheckCtx([], "")
        new_code, warnings = check_result_assignment(code, ctx)
        assert new_code == code
        assert warnings == []


class TestCheckHostFnUsage:
    def test_warns_when_no_host_fn_called(self):
        code = "result = 42"
        ctx = CheckCtx(["query_borrower"], "")
        _, warnings = check_host_fn_usage(code, ctx)
        assert any("calls none of" in w for w in warnings)

    def test_no_warning_when_fn_called(self):
        code = 'result = query_borrower({"name": "x"})'
        ctx = CheckCtx(["query_borrower"], "")
        _, warnings = check_host_fn_usage(code, ctx)
        assert not any("calls none of" in w for w in warnings)


class TestCheckNoRedefines:
    def test_removes_redefined_host_function(self):
        code = "def query_borrower(args):\n    return {}\nresult = query_borrower({})"
        ctx = CheckCtx(["query_borrower"], "")
        new_code, warnings = check_no_redefines(code, ctx)
        assert "def query_borrower" not in new_code
        assert any("removed redefinition" in w for w in warnings)


class TestCheckInputsUsage:
    def test_warns_when_inputs_not_used(self):
        code = 'result = query_borrower({"name": "hardcoded"})'
        ctx = CheckCtx([], "", {"borrower_name": "Name"})
        _, warnings = check_inputs_usage(code, ctx)
        assert any("inputs dict" in w for w in warnings)

    def test_no_warning_when_inputs_used(self):
        code = 'result = query_borrower({"name": inputs["borrower_name"]})'
        ctx = CheckCtx([], "", {"borrower_name": "Name"})
        _, warnings = check_inputs_usage(code, ctx)
        assert not any("inputs dict" in w for w in warnings)


class TestCheckMissingImports:
    def test_adds_missing_json_import(self):
        code = "result = json.dumps({})"
        ctx = CheckCtx([], "")
        new_code, warnings = check_missing_imports(code, ctx)
        assert "import json" in new_code
        assert any("added import json" in w for w in warnings)

    def test_no_add_when_already_imported(self):
        code = "import json\nresult = json.dumps({})"
        ctx = CheckCtx([], "")
        new_code, warnings = check_missing_imports(code, ctx)
        assert new_code == code
        assert warnings == []


class TestVerifyCodeChain:
    def test_runs_all_checks(self):
        code = "def main():\n    return json.dumps({})"
        new_code, warnings = verify_code(code, [], "")
        assert "import json" in new_code
        assert "result = main()" in new_code

    def test_custom_check_list(self):
        code = "result = 42"
        only_imports = [check_missing_imports]
        new_code, warnings = verify_code(code, [], "", checks=only_imports)
        assert new_code == code
        assert warnings == []

    def test_empty_check_list(self):
        code = "x = 1"
        new_code, warnings = verify_code(code, [], "", checks=[])
        assert new_code == code
        assert warnings == []


# ---------------------------------------------------------------------------
# 2. Retry decorator (with_remediation)
# ---------------------------------------------------------------------------

class TestWithRemediation:
    def test_passes_on_first_try(self):
        call_count = 0

        @with_remediation(retries=1, post=lambda x: None)
        async def good_call(prompt):
            nonlocal call_count
            call_count += 1
            return "good code"

        result = asyncio.get_event_loop().run_until_complete(good_call("write code"))
        assert result == "good code"
        assert call_count == 1

    def test_retries_on_post_failure(self):
        attempts = []

        @with_remediation(retries=1, post=lambda x: "bad" if "broken" in x else None)
        async def flaky_call(prompt):
            attempts.append(prompt)
            if len(attempts) == 1:
                return "broken code"
            return "fixed code"

        result = asyncio.get_event_loop().run_until_complete(flaky_call("write code"))
        assert result == "fixed code"
        assert len(attempts) == 2
        assert "failed validation" in attempts[1]

    def test_returns_last_result_after_exhausted_retries(self):
        @with_remediation(retries=1, post=lambda x: "still bad")
        async def always_bad(prompt):
            return "bad"

        result = asyncio.get_event_loop().run_until_complete(always_bad("write code"))
        assert result == "bad"

    def test_no_post_means_no_retry(self):
        call_count = 0

        @with_remediation(retries=3, post=None)
        async def no_check(prompt):
            nonlocal call_count
            call_count += 1
            return "anything"

        result = asyncio.get_event_loop().run_until_complete(no_check("go"))
        assert result == "anything"
        assert call_count == 1


# ---------------------------------------------------------------------------
# 3. Output validation
# ---------------------------------------------------------------------------

class TestValidateOutput:
    def test_none_value_fails(self):
        assert validate_output(None) is not None

    def test_none_value_passes_without_expected_type(self):
        assert validate_output(None, None) == "output is None"

    def test_any_non_none_passes_without_type(self):
        assert validate_output(42) is None
        assert validate_output("hello") is None
        assert validate_output([]) is None

    def test_dict_check(self):
        assert validate_output({"a": 1}, "dict") is None
        assert validate_output([1, 2], "dict") is not None

    def test_list_check(self):
        assert validate_output([1, 2], "list") is None
        assert validate_output({"a": 1}, "list") is not None

    def test_str_check(self):
        assert validate_output("hello", "str") is None
        assert validate_output(42, "str") is not None

    def test_number_check(self):
        assert validate_output(42, "number") is None
        assert validate_output(3.14, "number") is None
        assert validate_output("42", "number") is not None

    def test_list_dict_check(self):
        assert validate_output([{"a": 1}], "list[dict]") is None
        assert validate_output([1, 2], "list[dict]") is not None
        assert validate_output([], "list[dict]") is None

    def test_nonempty_check(self):
        assert validate_output([1], "nonempty") is None
        assert validate_output([], "nonempty") is not None
        assert validate_output("", "nonempty") is not None

    def test_composite_checks(self):
        assert validate_output([{"a": 1}], "list[dict], nonempty") is None
        assert validate_output([], "list[dict], nonempty") is not None

    def test_unknown_type_passes(self):
        assert validate_output(42, "foobar") is None
