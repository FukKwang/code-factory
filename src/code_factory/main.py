import asyncio
import os
import sys

from .agents.orchestrator import FactoryDeps, build_orchestrator
from .config import get_settings
from .context.manager import compact_messages
from .vault.manager import VaultManager

settings = get_settings()

os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
os.environ.setdefault("OPENAI_BASE_URL", settings.openai_base_url)
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

vault = VaultManager(settings.vault_path)
deps = FactoryDeps(vault=vault, settings=settings)
agent = build_orchestrator(settings)


async def _run_loop():
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

        # Reset tool call counts each turn
        deps._tool_counts = None

        try:
            result = await agent.run(user_input, deps=deps, message_history=history)
            print(result.output)
            print()
            history = compact_messages(result.all_messages())
        except Exception as e:
            print(f"Error: {type(e).__name__}: {e}")
            # On token limit errors, aggressively compact and continue
            if "token limit" in str(e).lower() or "exceeded" in str(e).lower():
                history = compact_messages(history, keep_last=2)
                print("(context compacted, try again)")
            print()


def main():
    if sys.stdin.isatty():
        asyncio.run(_run_loop())
    else:
        # Pipe mode: single input, use built-in CLI
        agent.to_cli_sync(deps=deps)


if __name__ == "__main__":
    main()
