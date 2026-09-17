from __future__ import annotations

from typing import Any, Callable

_FUNCTIONS: dict[str, Callable] = {}
_DESCRIPTIONS: dict[str, str] = {}
_HUMAN_INPUT: set[str] = set()


def register_host_function(
    name: str, description: str, *, human_input: bool = False,
) -> Callable:
    """Decorator to register a host function for the Monty sandbox.

    Usage:
        @register_host_function("get_customer",
            "get_customer({'id': str}) -> dict: customer profile")
        def get_customer(args_dict: dict) -> dict:
            return db.query(args_dict["id"])
    """
    def decorator(fn: Callable) -> Callable:
        _FUNCTIONS[name] = fn
        _DESCRIPTIONS[name] = description
        if human_input:
            _HUMAN_INPUT.add(name)
        return fn
    return decorator


def get_functions() -> dict[str, Callable]:
    return _FUNCTIONS


def get_descriptions() -> dict[str, str]:
    return _DESCRIPTIONS


def get_human_input_functions() -> set[str]:
    return _HUMAN_INPUT


def build_external_lookup(allowlist: list[str] | None = None) -> dict[str, Callable]:
    if allowlist is None:
        return dict(_FUNCTIONS)
    return {k: v for k, v in _FUNCTIONS.items() if k in allowlist}


def clear_registry() -> None:
    _FUNCTIONS.clear()
    _DESCRIPTIONS.clear()
    _HUMAN_INPUT.clear()
