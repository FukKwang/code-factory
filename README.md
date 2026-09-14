# code-factory

TUI coding harness that takes natural language requirements, writes sandboxed Python code via TDD, and stores reusable programs in a git-tracked vault. Built on [Pydantic AI](https://github.com/pydantic/pydantic-ai) + [Pydantic Monty](https://github.com/pydantic/pydantic-monty).

Think of it as a junior developer that writes, tests, and version-controls small data programs — then reuses them when you ask similar questions later.

## Features

- **Multi-agent TDD pipeline** — Researcher, Test Writer, Coder, and Reviewer collaborate under an Orchestrator
- **Sandboxed execution** — Code runs in Pydantic Monty (restricted Python), never on host
- **Git-tracked vault** — Every ticket, spec, test, solution, and run is committed automatically
- **Reusable programs** — Tickets are parameterized; asking a similar question reuses existing code with new inputs
- **KV cache optimization** — Budget-based compaction preserves LLM server's KV cache prefix between turns
- **Multi-provider** — Local (llama.cpp), DeepSeek API, Qwen, or any OpenAI-compatible endpoint
- **Host functions** — Data access only through declared functions (no raw SQL/URLs in generated code)

## Install

```bash
git clone <repo-url> && cd code-factory
pip install -e .
```

Requirements: Python 3.11+

## Quick Start

```bash
# Local model (llama.cpp on port 8081)
code-factory

# DeepSeek API
CODE_FACTORY_DEEPSEEK_API_KEY=sk-... code-factory -p deepseek

# Qwen on local llama.cpp (port 8082)
code-factory -p qwen

# Override model for all roles
code-factory -m "openai-chat:my-model"
```

## CLI Flags

| Flag | Description |
|------|-------------|
| `-p`, `--provider` | Provider preset: `local`, `deepseek`, `qwen` |
| `-m`, `--model` | Override model string for all agent roles |

## Provider Presets

| Preset | Model | Endpoint | Max Tokens |
|--------|-------|----------|------------|
| `local` (default) | `openai-chat:ling-3.0-tiny` | `localhost:8081/v1` | 32768 |
| `deepseek` | `deepseek:deepseek-chat` | `api.deepseek.com` | 8192 |
| `qwen` | `qwen:qwen3-59b` | `localhost:8082/v1` | 32768 |

## Environment Variables

All prefixed with `CODE_FACTORY_`:

| Variable | Default | Description |
|----------|---------|-------------|
| `PROVIDER` | `local` | Provider preset name |
| `DEEPSEEK_API_KEY` | — | Required for `deepseek` provider |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek API endpoint |
| `QWEN_BASE_URL` | `http://localhost:8082/v1` | Qwen endpoint |
| `OPENAI_BASE_URL` | `http://localhost:8081/v1` | Default OpenAI-compatible endpoint |
| `VAULT_PATH` | `~/Documents/dev/code-factory-repo` | Git vault location |
| `MODEL_ORCHESTRATOR` | (from preset) | Override per-role model |
| `MODEL_RESEARCHER` | (from preset) | Override per-role model |
| `MODEL_CODER` | (from preset) | Override per-role model |
| `MODEL_REVIEWER` | (from preset) | Override per-role model |
| `MODEL_TEST_WRITER` | (from preset) | Override per-role model |

Supports `.env` file in working directory.

## Architecture

```
User
  |
  v
Orchestrator (main agent, owns tools)
  |
  |-- search_vault          → find & reuse existing programs
  |-- create_ticket          → new parameterized program
  |-- generate_and_test      → full TDD pipeline (below)
  |-- iterate_code           → modify based on feedback
  |-- close_ticket           → mark done
  |
  |  TDD Pipeline (generate_and_test):
  |  1. Researcher       → analyzes requirements, writes findings
  |  2. Test Writer      → generates assert-based tests from spec
  |  3. Coder            → writes Monty-compatible code (TDD)
  |  4. Monty Sandbox    → runs code + tests
  |  5. Reviewer         → plain-language summary for user
  |
  v
Vault (git repo)           Sandbox (Pydantic Monty)
```

### Agents

| Agent | Role | Instructions |
|-------|------|-------------|
| **Orchestrator** | Routes user requests, manages ticket lifecycle | Pipeline coordination, tool dispatch |
| **Researcher** | Analyzes requirements | Produces structured findings (objective, data needed, host functions, edge cases) |
| **Coder** | Writes Monty-safe Python | Strict rules: `result` at module level, use `inputs` dict, no imports beyond stdlib subset |
| **Test Writer** | Generates validation | Assert-only tests against `result` variable |
| **Reviewer** | Evaluates output | Plain-language summary, no code shown to user |

Each agent can use a different model via `MODEL_*` env vars or per-role settings.

## Ticket Lifecycle

```
NEW → GATHERING → SPEC_DRAFTED → TESTS_DRAFTED → CODE_DRAFTED
                                                       |
                                          tests pass? --+-- no → ITERATING → back to CODE_DRAFTED
                                                        |
                                                       yes
                                                        |
                                                    APPROVED → CLOSED
                                                        |
                                            (future similar query)
                                                        |
                                                      REUSED
```

Every state transition creates a git commit in the vault.

## Vault Structure

```
code-factory-repo/
├── .git/
├── registry.json                  # search index (id, title, tags, summary)
└── tickets/
    └── TICKET-001/
        ├── ticket.yaml            # metadata, status, requirements, input_schema
        ├── spec.md                # generated spec
        ├── test_solution.py       # generated tests (assert statements)
        ├── solution.py            # Monty-compatible Python
        └── runs/
            ├── research.md        # researcher findings
            ├── run_001.json       # execution record (inputs, output, success)
            └── run_002.json
```

## Program Reuse

Tickets are reusable programs, not one-off queries. When you ask "show loans for borrower ABC":

1. **First time**: Creates ticket "Find loans by borrower name" with `input_schema: {borrower_name: "Name of borrower"}`. Generates code that reads `inputs["borrower_name"]`.
2. **Next time**: `search_vault` finds existing ticket, extracts "DEF" from "show loans for borrower DEF", re-runs same code with new inputs.

This means generated code never hardcodes request-specific values — it reads from the `inputs` dict.

## Host Functions

Generated code accesses data only through declared host functions. No raw database queries or API calls in sandboxed code.

Currently available (Faker-based for development):

| Function | Signature | Returns |
|----------|-----------|---------|
| `query_borrower` | `({'name': str})` | Borrower profile (id, address, credit score, income, etc.) |
| `query_loans` | `({'borrower_id': str})` | List of loan records (amount, tenor, interest, status, DPD) |
| `query_borrowers_by_city` | `({'city': str, 'limit': int})` | List of borrower summaries in a city |

Host functions validate arguments via Pydantic models at the trust boundary. Each ticket declares which host functions its code may call (`host_function_allowlist`).

To add real data sources, replace Faker implementations in `src/code_factory/sandbox/host_functions.py` with actual DB/API/CSV connectors.

## KV Cache Optimization

LLM servers (llama.cpp, vLLM, DeepSeek) cache key-value pairs by token prefix. Modifying old messages breaks prefix match, forcing full recomputation.

code-factory uses budget-based compaction instead of per-turn compaction:

| Token Budget Used | Action | Cache Impact |
|-------------------|--------|-------------|
| < 70% | No change | Full prefix reuse |
| 70–90% | Light compact (keep last 6 messages, stub old tool returns) | Partial reuse |
| > 90% | Aggressive compact (keep last 2 messages) | Minimal reuse |

This follows DeepSeek/Leyline patterns: stable system prompt first, never modify already-sent prefix tokens, stub old tool outputs rather than deleting them, pin recent messages.

## Monty Sandbox Rules

Generated code runs in Pydantic Monty with these constraints:

- Must assign to `result` at module level
- Host functions are pre-defined globals — never redefine them
- Read runtime parameters from `inputs` dict — never hardcode
- Allowed: `def`, `lambda`, `dataclass(eq/frozen)`, comprehensions, `try/except`, loops, `f-strings`, `with`
- Allowed imports: `json`, `math`, `datetime`, `re`, `collections`, `itertools`, `functools`, `dataclasses`, `typing`
- Not allowed: inheritance, `yield`, `del`, `eval`/`exec`, third-party imports

## Code Verification

Before execution, generated code is automatically verified:

1. **`result` assignment** — Auto-appends `result = fn()` call if missing
2. **Host function usage** — Warns if no host functions called (likely hardcoded data)
3. **Host function redefinition** — Auto-removes if code redefines a host function
4. **`inputs` dict usage** — Warns if code doesn't read from `inputs` when `input_schema` is defined

## Project Structure

```
src/code_factory/
├── main.py                    # CLI entry, argparse, REPL loop
├── config.py                  # Settings, provider presets, model resolution
├── agents/
│   ├── orchestrator.py        # Main agent, pipeline tools, code verification
│   ├── researcher.py          # Requirement analysis agent
│   ├── coder.py               # Monty code generation agent
│   ├── test_writer.py         # Test generation agent
│   └── reviewer.py            # Output review agent
├── vault/
│   ├── models.py              # Ticket, TicketStatus, RunRecord, TicketSummary
│   └── manager.py             # CRUD, search (difflib), git operations
├── sandbox/
│   ├── runner.py              # Monty session wrapper, fallback exec
│   └── host_functions.py      # Faker-based data functions, allowlist builder
└── context/
    └── manager.py             # Token estimation, compaction, findings files
```

## Adding a Provider

1. Add preset to `PROVIDER_PRESETS` in `config.py`
2. Add `<name>_base_url` and `<name>_api_key` fields to `Settings`
3. Add entry to `CUSTOM_PROVIDERS` dict
4. Use: `code-factory -p <name>`

## Adding a Host Function

1. Define function in `sandbox/host_functions.py` with Pydantic args model
2. Add to `HOST_FUNCTIONS` dict
3. Add description to `HOST_FUNCTION_DESCRIPTIONS`
4. Function is now available to all generated code

## License

Private.
