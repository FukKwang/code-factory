# code-factory

TUI coding harness that takes natural language requirements, writes sandboxed Python code via TDD, and stores reusable programs in a git-tracked vault. Built on [Pydantic AI](https://github.com/pydantic/pydantic-ai) + [Pydantic Monty](https://github.com/pydantic/pydantic-monty).

Think of it as a junior developer that writes, tests, and version-controls small data programs — then reuses them when you ask similar questions later.

## Features

- **Multi-agent TDD pipeline** — Researcher, Test Writer, Coder, and Reviewer collaborate under an Orchestrator
- **Sandboxed execution** — Code runs in Pydantic Monty (restricted Python), never on host
- **Git-tracked vault** — Every ticket, spec, test, solution, and run is committed automatically
- **Reusable programs** — Tickets are parameterized; asking a similar question reuses existing code with new inputs
- **Pluggable host functions** — Register custom data sources via decorator, no source editing needed
- **KV cache optimization** — Budget-based compaction preserves LLM server's KV cache prefix between turns
- **Multi-provider** — Local (llama.cpp), DeepSeek API, Qwen, or any OpenAI-compatible endpoint

## Install

```bash
# From GitHub
pip install git+https://github.com/FukKwang/code-factory.git

# From local source (editable)
pip install -e /path/to/code-factory

# From wheel
python -m build  # produces dist/*.whl
pip install dist/code_factory-0.1.0-py3-none-any.whl
```

Requirements: Python 3.11+

## Quick Start

### CLI

```bash
# Default: local llama.cpp on port 8081
code-factory

# DeepSeek API
CODE_FACTORY_DEEPSEEK_API_KEY=sk-... code-factory -p deepseek

# Qwen (llama.cpp on port 8082)
code-factory -p qwen

# Override model for all roles
code-factory -m "openai-chat:my-model"
```

### As a library

```python
import asyncio
from code_factory.config import Settings
from code_factory.agents.programmer import build_programmer, FactoryDeps
from code_factory.vault.manager import VaultManager

settings = Settings(vault_path="./my_vault")
vault = VaultManager(settings.vault_path)
deps = FactoryDeps(vault=vault, settings=settings)
agent = build_programmer(settings)

result = asyncio.run(agent.run("Show loans for borrower Ahmad", deps=deps))
print(result.output)
```

## Architecture

```
User Query
    │
    ▼
┌──────────────────────────────────────────────┐
│  PROGRAMMER (main model)                     │
│  Tools: ask_human, list_functions,           │
│         search_vault, generate_code,         │
│         run_existing, iterate_code,          │
│         peek_result, save_ticket             │
│                                              │
│  Workflow:                                   │
│    1. Understand requirement                 │
│    2. ask_human to clarify if vague          │
│    3. list_functions to find capabilities    │
│    4. search_vault for reuse                 │
│    5. generate_code (delegates to sub-agents)│
│    6. Present result, iterate if needed      │
└───────┬──────────────┬───────────────────────┘
        │              │
   ┌────▼────┐    ┌────▼────┐
   │  VAULT  │    │ SANDBOX │
   │ (git)   │    │ (Monty) │
   │         │    │         │
   │ tickets │    │ code    │
   │ specs   │    │ can ONLY│
   │ tests   │    │ call    │
   │ code    │    │ host    │
   │ runs    │    │ funcs   │
   └─────────┘    └────┬────┘
                       │
              ┌────────▼────────┐
              │ HOST FUNCTIONS  │
              │ (pluggable)     │
              │                 │
              │ Default: Faker  │
              │ Custom: your    │
              │ DB/API/CSV      │
              └─────────────────┘
```

### Agents

Two model tiers:

| Agent | Model Tier | Role |
|-------|-----------|------|
| **Programmer** | main (`model_main`) | Understands requirements, negotiates with user, picks host functions, reviews results |
| **Coder** | sub (`model_sub`) | Writes Monty-safe Python from precise spec + tests |
| **Test Writer** | sub (`model_sub`) | Generates assert statements for `result` variable |

Configure via `CODE_FACTORY_MODEL_MAIN` and `CODE_FACTORY_MODEL_SUB` env vars.

## Host Functions

Generated code accesses data **only** through host functions. No raw database queries or API calls in sandboxed code.

### Registry pattern

Register custom host functions via decorator — no source editing needed:

```python
from code_factory import register_host_function, clear_registry

# Remove default Faker functions
clear_registry()

# Register your own
@register_host_function(
    "get_customer",
    "get_customer({'id': str}) -> dict: customer profile from CRM"
)
def get_customer(args_dict: dict) -> dict:
    return db.query("SELECT * FROM customers WHERE id = %s", args_dict["id"])

@register_host_function(
    "get_orders",
    "get_orders({'customer_id': str}) -> list[dict]: order history"
)
def get_orders(args_dict: dict) -> list:
    return db.query("SELECT * FROM orders WHERE customer_id = %s", args_dict["customer_id"])

@register_host_function(
    "ask_date_range",
    "ask_date_range({'prompt': str}) -> str: ask user for date range",
    human_input=True,  # pauses sandbox for user input
)
def ask_date_range(args_dict: dict) -> str:
    return args_dict.get("prompt", "")
```

**Function contract:**
- Takes `args_dict: dict[str, Any]` — validated via Pydantic model at trust boundary
- Returns JSON-serializable data (dict, list, str, number, bool)
- Description string tells the LLM what the function does and its signature
- `human_input=True` marks functions that pause sandbox execution for user input

### Default functions (Faker-based)

Included as reference implementation for development/testing:

**Domain** (interconnected via `borrower_id` / `loan_id`):
- `query_borrower(name)` → borrower profile
- `query_loans(borrower_id)` → list of loans
- `query_borrowers_by_city(city, limit)` → borrower summaries
- `query_payments(loan_id)` → payment history
- `query_collateral(loan_id)` → collateral records
- `query_guarantors(borrower_id)` → guarantor list
- `query_collection_records(loan_id)` → collection activity
- `query_transactions(borrower_id, limit)` → recent transactions
- `query_portfolio_summary(city)` → portfolio-level stats
- `query_delinquency_stats(bucket)` → DPD distribution

**Library bridges** (wrap pandas/numpy/scipy/networkx for sandbox code):
- `tabulate_data` → sort/filter/select columns (pandas)
- `aggregate_data` → group-by aggregation (pandas)
- `pivot_data` → pivot table (pandas)
- `compute_statistics` → descriptive stats (numpy)
- `compute_correlation` → Pearson/Spearman (scipy)
- `analyze_network` → graph analysis (networkx)
- `find_related_entities` → BFS traversal (networkx)

**Human input** (snapshot-based, pauses sandbox):
- `ask_user(prompt)` → free text
- `ask_number(prompt, min, max)` → number with bounds
- `ask_confirm(prompt)` → yes/no
- `ask_choice(prompt, options)` → pick from list

### Example: real database host functions

```python
from code_factory import register_host_function, clear_registry
import psycopg2

clear_registry()

conn = psycopg2.connect("postgresql://user:pass@localhost/mydb")

@register_host_function(
    "query_employee",
    "query_employee({'name': str}) -> dict: employee_id, name, department, salary, hire_date"
)
def query_employee(args_dict: dict) -> dict:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM employees WHERE name ILIKE %s LIMIT 1", (args_dict["name"],))
    return dict(cur.fetchone() or {})

@register_host_function(
    "query_team_members",
    "query_team_members({'department': str}) -> list[dict]: employees in department"
)
def query_team_members(args_dict: dict) -> list:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM employees WHERE department = %s", (args_dict["department"],))
    return [dict(r) for r in cur.fetchall()]
```

Then run:
```python
agent = build_programmer(settings)
result = await agent.run("Show all employees in Engineering department", deps=deps)
```

The programmer agent sees your registered functions, negotiates requirements with you, then the coder writes sandbox code calling them, and the sandbox executes against your real database.

## Program Reuse

Tickets are reusable programs, not one-off queries. When you ask "show loans for borrower ABC":

1. **First time**: Creates ticket "Find loans by borrower name" with `input_schema: {borrower_name: "Name of borrower"}`. Generates code that reads `inputs["borrower_name"]`.
2. **Next time**: Vault search finds existing ticket, extracts "DEF" from "show loans for borrower DEF", re-runs same code with new inputs.

Generated code never hardcodes request-specific values — it reads from the `inputs` dict.

## Ticket Lifecycle

```
NEW → GATHERING → SPEC_DRAFTED → TESTS_DRAFTED → CODE_DRAFTED
                                                       │
                                          tests pass? ──┤
                                                        │ no
                                                   ITERATING
                                                        │
                                                       yes
                                                        │
                                                    APPROVED → CLOSED
                                                        │
                                            (future similar query)
                                                        │
                                                      REUSED
```

Every state transition creates a git commit in the vault.

## Code Verification

Before execution, generated code is automatically verified and fixed:

1. **`result` assignment** — auto-appends `result = fn()` if missing
2. **Host function usage** — warns if no host functions called (likely hardcoded data)
3. **Allowlist auto-expand** — if code calls a registered host function not in the ticket's allowlist, it's added automatically
4. **Host function redefinition** — auto-removes if code redefines a host function
5. **`inputs` dict usage** — warns if code doesn't read from `inputs` when `input_schema` is defined
6. **Missing imports** — auto-adds stdlib imports (json, math, datetime, etc.) when used but not imported

## Configuration

### CLI flags

| Flag | Description |
|------|-------------|
| `-p`, `--provider` | Provider preset: `ling`, `deepseek`, `qwen` |
| `-m`, `--model` | Override model string for all agent roles |

### Environment variables

All prefixed with `CODE_FACTORY_` (supports `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `PROVIDER` | `ling` | Provider preset name |
| `VAULT_PATH` | `./vault` | Git vault directory (relative to working directory) |
| `OPENAI_BASE_URL` | `http://localhost:8081/v1` | Default OpenAI-compatible endpoint |
| `DEEPSEEK_API_KEY` | — | Required for `deepseek` provider |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek API endpoint |
| `QWEN_BASE_URL` | `http://localhost:8082/v1` | Qwen endpoint |
| `MODEL_MAIN` | (from preset) | Override programmer agent model |
| `MODEL_SUB` | (from preset) | Override coder/test_writer model |

### Provider presets

| Preset | Model | Endpoint | Max Tokens |
|--------|-------|----------|------------|
| `ling` (default) | `openai-chat:ling-3.0-tiny` | `localhost:8081/v1` | 8192 |
| `deepseek` | `deepseek:deepseek-flash` | `api.deepseek.com` | 8192 |
| `qwen` | `qwen:qwen3-59b` | `localhost:8082/v1` | 32768 |

## Vault Structure

```
vault/
├── .git/
├── registry.json              # search index
└── tickets/
    └── TICKET-001/
        ├── ticket.yaml        # metadata, status, input_schema, allowlist
        ├── spec.md            # generated spec
        ├── test_solution.py   # assert statements
        ├── solution.py        # Monty-compatible Python
        └── runs/
            ├── research.md    # researcher findings
            └── run_001.json   # {inputs, output, success, error, timestamp}
```

## Monty Sandbox Rules

Generated code runs in Pydantic Monty with these constraints:

- Must assign to `result` at module level
- Host functions are pre-defined globals — never redefine them
- Read runtime parameters from `inputs` dict — never hardcode
- Allowed: `def`, `lambda`, `dataclass(eq/frozen)`, comprehensions, `try/except`, loops, `f-strings`, `with`
- Allowed imports: `json`, `math`, `datetime`, `re`, `collections`, `itertools`, `functools`, `dataclasses`, `typing`
- Not allowed: inheritance, `yield`, `del`, `eval`/`exec`, third-party imports

## Project Structure

```
src/code_factory/
├── __init__.py                # exports: register_host_function, clear_registry
├── main.py                    # CLI entry, argparse, REPL loop
├── config.py                  # Settings, provider presets, model resolution
├── agents/
│   ├── programmer.py          # Main agent with human-in-the-loop tools
│   ├── code_utils.py          # Shared utilities (code verification, cleanup)
│   ├── coder.py               # Monty code generation sub-agent
│   └── test_writer.py         # Test generation sub-agent
├── sandbox/
│   ├── registry.py            # Host function decorator + registry
│   ├── host_functions.py      # Default Faker-based implementations (22 functions)
│   └── runner.py              # Monty session wrapper, test runner, fallback exec
├── context/
│   └── manager.py             # Token estimation, history compaction
└── vault/
    ├── models.py              # Ticket, TicketStatus, RunRecord, TicketSummary
    └── manager.py             # Git-backed CRUD, search (text + structural), git ops
```

## KV Cache Optimization

LLM servers (llama.cpp, vLLM, DeepSeek) cache key-value pairs by token prefix. Modifying old messages breaks prefix match, forcing full recomputation.

code-factory uses budget-based compaction:

| Token Budget Used | Action | Cache Impact |
|-------------------|--------|-------------|
| < 70% | No change | Full prefix reuse |
| 70–90% | Light compact (keep last 6 msgs, stub old tool returns) | Partial reuse |
| > 90% | Aggressive compact (keep last 2 msgs) | Minimal reuse |

## Adding a Provider

1. Add preset to `PROVIDER_PRESETS` in `config.py`
2. Add `<name>_base_url` and `<name>_api_key` fields to `Settings`
3. Add entry to `CUSTOM_PROVIDERS` dict
4. Use: `code-factory -p <name>`

## Edge Cases and Limitations

- **Model quality**: Small models (4B params) may struggle with complex multi-hop queries or produce vague answers for ambiguous requests
- **Token overflow**: Complex tool calls with large data payloads can exceed model's max_tokens — increase via `CODE_FACTORY_MAX_TOKENS`
- **Faker data**: Default host functions return synthetic data. Same input key always produces same fake data (deterministic seeding)
- **Sandbox restrictions**: No network access, no filesystem, no third-party imports inside sandbox. All data must flow through host functions
- **Allowlist mismatch**: If the programmer picks wrong host functions, auto-expand in `verify_code` catches and adds missing ones
- **Human input functions**: `ask_*` functions pause sandbox via snapshot loop — only works in interactive mode (TTY)

## License

Private.
