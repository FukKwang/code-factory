import argparse
import asyncio
import os
import sys

from .config import PROVIDER_PRESETS, Settings, get_settings


def _parse_args():
    p = argparse.ArgumentParser(prog="code-factory")
    p.add_argument("--provider", "-p", choices=list(PROVIDER_PRESETS), help="model provider")
    p.add_argument("--model", "-m", help="override model for all roles")
    return p.parse_args()


def _build(args):
    overrides = {}
    if args.provider:
        overrides["provider"] = args.provider
    if args.model:
        for role in ("model_orchestrator", "model_researcher", "model_coder", "model_test_writer", "model_reviewer"):
            overrides[role] = args.model

    settings = Settings(**overrides) if overrides else get_settings()

    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    os.environ.setdefault("OPENAI_BASE_URL", settings.openai_base_url)
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

    from pydantic_ai import UsageLimits

    from .agents.orchestrator import FactoryDeps, build_orchestrator
    from .context.manager import compact_messages, maybe_compact
    from .vault.manager import VaultManager

    vault = VaultManager(settings.vault_path, git_enabled=settings.vault_git)
    deps = FactoryDeps(vault=vault, settings=settings)
    agent = build_orchestrator(settings)
    return settings, deps, agent, compact_messages, maybe_compact


def _show_last_result(deps):
    tid = deps.current_ticket_id
    if not tid:
        print("Task completed.")
        return
    runs_dir = deps.vault.ticket_dir(tid) / "runs"
    if not runs_dir.exists():
        print(f"{tid} completed (no run output).")
        return
    run_files = sorted(runs_dir.glob("run_*.json"), reverse=True)
    if not run_files:
        print(f"{tid} completed (no run output).")
        return
    import json
    data = json.loads(run_files[0].read_text())
    output = data.get("output", data.get("error", ""))
    if isinstance(output, str) and len(output) > 1000:
        output = output[:1000] + "..."
    print(f"{tid} completed. Last output:\n{output}")


async def _run_loop(settings, deps, agent, compact_messages, maybe_compact):
    print(f"provider: {settings.provider}  model: {settings.model_orchestrator}")
    history = []
    while True:
        try:
            user_input = input("code-factory> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            break
        if user_input.lower() in ("clear", "/clear", "reset", "/reset"):
            history = []
            deps._tool_counts = None
            ph = getattr(agent, "_pipeline_history", None)
            if ph is not None:
                ph.clear()
            print("Session cleared.\n")
            continue

        deps._tool_counts = None

        try:
            history = maybe_compact(history, settings.context_window)
            pu = getattr(agent, "_pipeline_usage", None)
            if pu:
                pu["cache_hit"] = pu["cache_miss"] = pu["output"] = 0
            result = await agent.run(
                user_input, deps=deps, message_history=history,
                usage_limits=UsageLimits(request_limit=settings.request_limit),
            )
            print(result.output)
            u = result.usage
            details = u.details or {}
            cache_hit = details.get("prompt_cache_hit_tokens", 0)
            cache_miss = details.get("prompt_cache_miss_tokens", 0)
            if pu:
                cache_hit += pu["cache_hit"]
                cache_miss += pu["cache_miss"]
            total_in = cache_hit + cache_miss
            hit_pct = (cache_hit / total_in * 100) if total_in else 0
            total_out = u.output_tokens + (pu["output"] if pu else 0)
            print(f"\n\033[2mtokens: in={cache_hit + cache_miss} out={total_out} "
                  f"cache_hit={cache_hit} cache_miss={cache_miss} ({hit_pct:.0f}% hit)\033[0m")
            print()
            history = result.all_messages()
        except Exception as e:
            err_str = str(e).lower()
            task_done = "already closed" in err_str or "task complete" in err_str
            if task_done or "usagelimitexceeded" in type(e).__name__.lower() or "request_limit" in err_str:
                _show_last_result(deps)
                history = compact_messages(history, keep_last=2)
            elif "token limit" in err_str or "exceeded" in err_str:
                print(f"Error: {type(e).__name__}: {e}")
                history = compact_messages(history, keep_last=2)
                print("(context compacted, try again)")
            else:
                print(f"Error: {type(e).__name__}: {e}")
            print()


def main():
    args = _parse_args()
    settings, deps, agent, compact_messages, maybe_compact = _build(args)

    if sys.stdin.isatty():
        asyncio.run(_run_loop(settings, deps, agent, compact_messages, maybe_compact))
    else:
        agent.to_cli_sync(deps=deps)


if __name__ == "__main__":
    main()
