from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    vault_path: Path = Path("/home/kwang/Documents/dev/code-factory-repo")

    model_orchestrator: str = "openai-chat:ling-3.0-tiny"
    model_researcher: str = "openai-chat:ling-3.0-tiny"  # cheap: structured analysis
    model_coder: str = "openai-chat:ling-3.0-tiny"
    model_test_writer: str = "openai-chat:ling-3.0-tiny"  # cheap: assert generation
    model_reviewer: str = "openai-chat:ling-3.0-tiny"  # cheap: plain-language summary

    max_tokens: int = 32768

    openai_base_url: str = "http://localhost:8081/v1"
    openai_api_key: str = "not-needed"

    model_config = SettingsConfigDict(env_prefix="CODE_FACTORY_", env_file=".env")


@lru_cache
def get_settings() -> Settings:
    return Settings()
