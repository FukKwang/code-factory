from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROVIDER_PRESETS: dict[str, dict] = {
    "ling": {
        "model": "openai-chat:ling-3.0-tiny",
        "max_tokens": 32768,
    },
    "qwen": {
        "model": "qwen:qwen3-59b",
        "max_tokens": 32768,
    },
    "deepseek": {
        "model": "deepseek:deepseek-chat",
        "max_tokens": 8192,
    },
}


class SandboxLimits(BaseSettings):
    """Min/max bounds for Monty sandbox resource limits."""
    min_duration_secs: float = 5.0
    max_duration_secs: float = 30.0
    default_duration_secs: float = 10.0

    min_memory: int = 16_000_000       # 16 MB
    max_memory: int = 128_000_000      # 128 MB
    default_memory: int = 64_000_000   # 64 MB

    max_recursion_depth: int = 500

    model_config = SettingsConfigDict(env_prefix="CODE_FACTORY_SANDBOX_")

    def clamp(self, requested: dict[str, float | int | None] | None = None) -> dict[str, float | int]:
        r = requested or {}
        dur = r.get("max_duration_secs") or self.default_duration_secs
        mem = r.get("max_memory") or self.default_memory
        return {
            "max_duration_secs": max(self.min_duration_secs, min(dur, self.max_duration_secs)),
            "max_memory": max(self.min_memory, min(int(mem), self.max_memory)),
            "max_recursion_depth": min(int(r.get("max_recursion_depth") or self.max_recursion_depth), self.max_recursion_depth),
        }


class Settings(BaseSettings):
    vault_path: Path = Path("/home/kwang/Documents/dev/code-factory-repo")

    provider: str = "ling"

    sandbox: SandboxLimits = SandboxLimits()

    model_orchestrator: str = ""
    model_researcher: str = ""
    model_coder: str = ""
    model_test_writer: str = ""
    model_reviewer: str = ""

    max_tokens: int = 0

    openai_base_url: str = "http://localhost:8081/v1"
    openai_api_key: str = "not-needed"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    qwen_base_url: str = "http://localhost:8082/v1"
    qwen_api_key: str = "not-needed"

    model_config = SettingsConfigDict(env_prefix="CODE_FACTORY_", env_file=".env")

    @model_validator(mode="after")
    def _apply_preset(self):
        preset = PROVIDER_PRESETS.get(self.provider, {})
        default_model = preset.get("model", PROVIDER_PRESETS["ling"]["model"])
        default_max = preset.get("max_tokens", 32768)

        if not self.model_orchestrator:
            self.model_orchestrator = default_model
        if not self.model_researcher:
            self.model_researcher = default_model
        if not self.model_coder:
            self.model_coder = default_model
        if not self.model_test_writer:
            self.model_test_writer = default_model
        if not self.model_reviewer:
            self.model_reviewer = default_model
        if not self.max_tokens:
            self.max_tokens = default_max
        return self


CUSTOM_PROVIDERS = {
    "deepseek": ("deepseek_base_url", "deepseek_api_key"),
    "qwen": ("qwen_base_url", "qwen_api_key"),
}


def resolve_model(model_str: str, settings: Settings):
    """Resolve model string to pydantic-ai model. Supports deepseek:/qwen: prefixes."""
    prefix = model_str.split(":", 1)[0] if ":" in model_str else ""
    if prefix in CUSTOM_PROVIDERS:
        url_attr, key_attr = CUSTOM_PROVIDERS[prefix]
        api_key = getattr(settings, key_attr)
        base_url = getattr(settings, url_attr)
        if not api_key:
            raise ValueError(f"{prefix}: model requires CODE_FACTORY_{key_attr.upper()}")
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        model_name = model_str.split(":", 1)[1]
        return OpenAIChatModel(model_name, provider=OpenAIProvider(base_url=base_url, api_key=api_key))
    return model_str


@lru_cache
def get_settings() -> Settings:
    return Settings()
