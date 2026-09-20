# CLAUDE.md

## Project

code-factory: TUI coding harness using Pydantic AI + Pydantic Monty. Takes natural language, writes sandboxed Python via TDD, stores reusable programs in git-tracked vault.

## Model Serving

Finetuned Qwen3-4B-Instruct-2507 (Q4_K_M, SFT-only) via llama.cpp server.

```bash
./serve.sh  # defaults below
```

| Parameter | Default | Env var |
|-----------|---------|---------|
| Model | `models/monty-coder-gguf_gguf/qwen3-4b-instruct-2507.Q4_K_M.gguf` | arg $1 |
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
| `vault_backend` | `filesystem` | `CODE_FACTORY_VAULT_BACKEND` | Storage backend: `filesystem` or `sqlite` |
| `vault_fs.git` | `false` | `CODE_FACTORY_VAULT_FS_GIT` | Enable git tracking (filesystem backend only) |
| `request_limit` | `25` | `CODE_FACTORY_REQUEST_LIMIT` | Max pydantic-ai requests per run |

## Training Environments

Two Python virtualenvs exist for finetuning. **Always use `.venv-train`** for training.

| Env | Path | Purpose | Key versions |
|-----|------|---------|-------------|
| `.venv-train` | `.venv-train/bin/python3` | **SFT/DPO finetuning** (use this) | unsloth 2026.9.4, torch 2.6.0+cu124 |
| `unsloth_env` | `~/Documents/unsloth/unsloth_env/bin/python3` | Experimental (do not use for training) | unsloth 2026.6.9, torch 2.10.0+cu130 |

Run training: `.venv-train/bin/python3 scripts/finetune.py`

## Finetuning

See [EXPERIMENTS.md](EXPERIMENTS.md) for full experiment log: training rounds, test results, known failure patterns.

## Key Paths

- Source: `src/code_factory/`
- Registry: `src/code_factory/sandbox/registry.py`
- Models (gitignored): `models/`
- Training data (gitignored): `training_data/`
- Scripts (gitignored): `scripts/`
- CI: `.github/workflows/build.yml`

## Code Navigation

Prefer LSP tool over grep/bash for Python code navigation:
- **Definitions**: `goToDefinition` instead of grep
- **References**: `findReferences` instead of grep
- **Symbols**: `workspaceSymbol` / `documentSymbol` instead of grep or find
- **Type info**: `hover` instead of reading source

Fall back to grep only for string literals, config values, or when LSP returns no results.

## Hugging Face

HF account: `tfukkwang`. Two repos for gitignored artifacts:

| Repo | Type | Contents |
|------|------|----------|
| [`tfukkwang/code-factory-models`](https://huggingface.co/tfukkwang/code-factory-models) | model | Production weights (GGUF quant, full safetensors, LoRA adapter) |
| [`tfukkwang/code-factory-training`](https://huggingface.co/datasets/tfukkwang/code-factory-training) | dataset | `scripts/` and `training_data/` |

Upload/resume: `.venv-train/bin/hf upload tfukkwang/code-factory-models models/<dir> <dir> --exclude '*.cache*'`

Model dirs on HF:
- `monty-coder-gguf_gguf/` — Q4_K_M GGUF (production, used by `serve.sh`)
- `monty-coder-gguf/` — full merged safetensors
- `monty-coder-lora/` — LoRA adapter (partial upload, resume with command above)

## Sensitive

- `.env` contains API keys — never commit
- `models/` directory gitignored — contains GGUF weights
