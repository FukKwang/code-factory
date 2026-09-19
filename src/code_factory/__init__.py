from __future__ import annotations

from pathlib import Path

from .sandbox.registry import clear_registry, register_host_function


class CodeFactory:
    """Single entry point for library use.

    Usage:
        factory = CodeFactory(
            model="qwen3-4b-instruct",
            base_url="http://localhost:8081/v1",
        )

        @factory.host_function("get_customer",
            "get_customer({'id': str}) -> dict: customer profile")
        def get_customer(args_dict):
            return db.query(args_dict["id"])

        factory.run()  # interactive TUI
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:8081/v1",
        api_key: str = "not-needed",
        sub_model: str | None = None,
        sub_base_url: str | None = None,
        sub_api_key: str | None = None,
        vault_path: str | Path = "vault",
        vault_git: bool = False,
        request_limit: int = 25,
        max_tokens: int = 8192,
        context_window: int = 32768,
        max_duration_secs: float = 10.0,
        max_memory: int = 64_000_000,
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.sub_model = sub_model or model
        self.sub_base_url = sub_base_url or base_url
        self.sub_api_key = sub_api_key or api_key
        self.vault_path = Path(vault_path)
        self.vault_git = vault_git
        self.request_limit = request_limit
        self.max_tokens = max_tokens
        self.context_window = context_window
        self.max_duration_secs = max_duration_secs
        self.max_memory = max_memory
        self._host_fns: list[tuple[str, str, object, bool]] = []
        self._built = False

    def host_function(self, name: str, description: str, *, human_input: bool = False):
        """Decorator to register a host function."""
        def decorator(fn):
            self._host_fns.append((name, description, fn, human_input))
            return fn
        return decorator

    def _make_model(self, model_name: str, base_url: str, api_key: str):
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        return OpenAIChatModel(model_name, provider=OpenAIProvider(
            base_url=base_url, api_key=api_key))

    def _build(self):
        import os

        from .config import Settings, SandboxLimits
        from .agents.programmer import FactoryDeps, build_programmer
        from .vault.manager import VaultManager

        # ponytail: global registry, single-instance assumption
        clear_registry()
        for name, desc, fn, hi in self._host_fns:
            register_host_function(name, desc, human_input=hi)(fn)

        main_model = self._make_model(self.model, self.base_url, self.api_key)
        sub_model = self._make_model(self.sub_model, self.sub_base_url, self.sub_api_key)

        sandbox = SandboxLimits(
            default_duration_secs=self.max_duration_secs,
            default_memory=self.max_memory,
        )
        settings = Settings(
            model_main=f"openai-chat:{self.model}",
            model_sub=f"openai-chat:{self.sub_model}",
            openai_base_url=self.base_url,
            openai_api_key=self.api_key,
            vault_path=self.vault_path,
            vault_git=self.vault_git,
            request_limit=self.request_limit,
            max_tokens=self.max_tokens,
            context_window=self.context_window,
            sandbox=sandbox,
        )

        os.environ.setdefault("OPENAI_API_KEY", self.api_key)
        os.environ.setdefault("OPENAI_BASE_URL", self.base_url)
        os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

        vault = VaultManager(settings.vault_path, git_enabled=settings.vault_git)
        agent = build_programmer(settings, model_main=main_model, model_sub=sub_model)
        deps = FactoryDeps(vault=vault, settings=settings)

        self._settings = settings
        self._deps = deps
        self._agent = agent
        self._history: list = []
        self._built = True

    def run(self):
        """Start interactive TUI loop."""
        import asyncio
        import sys

        self._build()
        self._deps.interactive = sys.stdin.isatty()

        if sys.stdin.isatty():
            asyncio.run(self._loop())
        else:
            self._agent.to_cli_sync(deps=self._deps)

    async def ask(self, query: str) -> str:
        """Run single query programmatically."""
        if not self._built:
            self._build()
            self._deps.interactive = False

        from pydantic_ai import UsageLimits
        from .context.manager import maybe_compact

        self._history = maybe_compact(self._history, self._settings.context_window)
        result = await self._agent.run(
            query, deps=self._deps, message_history=self._history,
            usage_limits=UsageLimits(request_limit=self.request_limit),
        )
        self._history = result.all_messages()
        return result.output

    async def _loop(self):
        from pydantic_ai import UsageLimits
        from .context.manager import compact_messages, maybe_compact

        settings = self._settings
        deps = self._deps
        agent = self._agent

        print(f"model: {self.model}  sub: {self.sub_model}")
        history: list = []
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
                print("Session cleared.\n")
                continue

            try:
                history = maybe_compact(history, settings.context_window)
                result = await agent.run(
                    user_input, deps=deps, message_history=history,
                    usage_limits=UsageLimits(request_limit=settings.request_limit),
                )
                print(result.output)
                u = result.usage
                details = u.details or {}
                cache_hit = details.get("prompt_cache_hit_tokens", 0)
                cache_miss = details.get("prompt_cache_miss_tokens", 0)
                total_in = cache_hit + cache_miss
                hit_pct = (cache_hit / total_in * 100) if total_in else 0
                print(f"\n\033[2mtokens: in={total_in} out={u.output_tokens} "
                      f"cache_hit={cache_hit} cache_miss={cache_miss} ({hit_pct:.0f}% hit)\033[0m")
                print()
                history = result.all_messages()
            except Exception as e:
                err_str = str(e).lower()
                task_done = "already closed" in err_str or "task complete" in err_str
                if task_done or "usagelimitexceeded" in type(e).__name__.lower() or "request_limit" in err_str:
                    self._show_last_result(deps)
                    history = compact_messages(history, keep_last=2)
                elif "token limit" in err_str or "exceeded" in err_str:
                    print(f"Error: {type(e).__name__}: {e}")
                    history = compact_messages(history, keep_last=2)
                    print("(context compacted, try again)")
                else:
                    print(f"Error: {type(e).__name__}: {e}")
                print()

    @staticmethod
    def _show_last_result(deps):
        import json
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
        data = json.loads(run_files[0].read_text())
        output = data.get("output", data.get("error", ""))
        if isinstance(output, str) and len(output) > 1000:
            output = output[:1000] + "..."
        print(f"{tid} completed. Last output:\n{output}")
