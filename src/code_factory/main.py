import os

from .agents.orchestrator import FactoryDeps, build_orchestrator
from .config import get_settings
from .vault.manager import VaultManager

settings = get_settings()

os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
os.environ.setdefault("OPENAI_BASE_URL", settings.openai_base_url)
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

vault = VaultManager(settings.vault_path)
deps = FactoryDeps(vault=vault, settings=settings)
agent = build_orchestrator(settings)


def main():
    agent.to_cli_sync(deps=deps)


if __name__ == "__main__":
    main()
