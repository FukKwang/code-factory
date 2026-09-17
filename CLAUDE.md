# CLAUDE.md

## Project

code-factory: TUI coding harness using Pydantic AI + Pydantic Monty. Takes natural language, writes sandboxed Python via TDD, stores reusable programs in git-tracked vault.

## Model Serving

Finetuned Qwen3-4B (Q4_K_M) via llama.cpp server.

```bash
./serve.sh  # defaults below
```

| Parameter | Default | Env var |
|-----------|---------|---------|
| Model | `models/monty-coder-gguf_gguf/qwen3-4b.Q4_K_M.gguf` | arg $1 |
| Port | 8081 | `PORT` |
| Host | 0.0.0.0 | `HOST` |
| Context | 32768 | `CTX` |
| GPU layers | 99 (all) | `GPU_LAYERS` |
| Parallel slots | 4 | `PARALLEL` |
| Flash attention | on | — |
| Binary | `llama-server` | `LLAMA_SERVER` |

Training context: 40960. Safe to serve up to 40960 without RoPE scaling.

Hardware: RTX 3070 8GB VRAM. At ctx 32768 with 4 slots, uses ~7.5GB. If OOM, reduce `PARALLEL=2` or `CTX=8192`.

LAN access: `http://192.168.100.40:8081/v1` (may need `sudo ufw allow 8081/tcp`).

## Package

```bash
pip install git+https://github.com/FukKwang/code-factory.git
```

Build wheel: `python -m build`

## Host Function Registry

Decorator-based. Consumers register custom data sources:

```python
from code_factory import register_host_function, clear_registry
```

See README.md for full examples.

## Settings

| Setting | Default | Env var | Notes |
|---------|---------|---------|-------|
| `vault_git` | `false` | `CODE_FACTORY_VAULT_GIT` | Enable git tracking in vault |
| `request_limit` | `25` | `CODE_FACTORY_REQUEST_LIMIT` | Max pydantic-ai requests per run |

## Key Paths

- Source: `src/code_factory/`
- Registry: `src/code_factory/sandbox/registry.py`
- Models (gitignored): `models/`
- Training data (gitignored): `training_data/`
- Scripts (gitignored): `scripts/`
- CI: `.github/workflows/build.yml`

## Sensitive

- `.env` contains API keys — never commit
- `models/` directory gitignored — contains GGUF weights
