# Finetuning Experiment Log

Base model: `Qwen3-4B-Instruct-2507` (unsloth 4-bit)  
Method: SFT-only (DPO degrades tool-calling)  
Quantization: Q4_K_M GGUF for llama.cpp  
Hardware: RTX 3070 8GB VRAM  

## Training Config (constant across rounds)

| Param | Value |
|-------|-------|
| LORA_R | 16 |
| LORA_ALPHA | 16 |
| EPOCHS | 5 |
| BATCH_SIZE | 1 |
| GRAD_ACCUM | 4 |
| LR | 2e-4 |
| MAX_SEQ_LEN | 4096 |
| WARMUP_STEPS | 10 |
| TARGET_MODULES | q,k,v,o,gate,up,down_proj |

## Test Suite

10 scenarios (5 basic S1-S5, 5 complex C1-C5):

| ID | Name | Tests |
|----|------|-------|
| S1 | Full flow | generate_code + peek_result in output |
| S2 | Vault reuse | search_vault + run_existing (not generate_code) |
| S3 | Vague request | ask_human called, <=2 times |
| S4 | Iterate existing | iterate_code used (not generate_code) for TKT reference |
| S5 | No function | Doesn't loop; tells user capability missing |
| C1 | Generate→iterate→save | Multi-turn: generate, then iterate, then save |
| C2 | Impossible→pivot | Recognizes missing capability, pivots on user redirect, ask_human <=3 |
| C3 | Ambiguous | ask_human to clarify, then generate_code |
| C4 | Error recovery | "yes try again" doesn't trigger ask_human loop, ask_human <=2 |
| C5 | Vault→iterate chain | Multi-turn: vault reuse then iterate, ask_human <=3 |

## Rounds

### Round 1 — 2026-09-15

- **Data**: 89 coder triples (system/user/assistant for code generation)
- **Examples**: ~100 (89 triples + padding)
- **Role**: Coder sub-agent only
- **Checkpoints**: 25, 50, 75, 100, 125
- **Architecture**: 5-agent pipeline (orchestrator→researcher→coder→test_writer→reviewer)
- **Result**: Coder produced better code than base Qwen3-4B. No programmer agent yet.
- **Commit**: `3a398e0` Add finetuning pipeline

### Round 2 — 2026-09-15

- **Data**: 107 coder triples (expanded from 89, targeting weak patterns)
- **Examples**: ~116
- **Checkpoints**: 29, 58, 87, 116, 145
- **Changes**: More diverse tool-call patterns, edge cases
- **Commit**: `eeeb15d` Expand SFT training data: 89→107 triples

### Round 3 — 2026-09-17

- **Data**: 107 coder triples (rebalanced, reduced overrepresented combos)
- **Examples**: ~140 (107 triples + augmented variants)
- **Checkpoints**: 35, 70, 105, 140, 175
- **Changes**: Rebalanced distribution of tool combinations
- **Commit**: `df39a8a` Rebalance SFT training data

### Round 4 — 2026-09-18 (morning)

- **Data**: 151 programmer examples + 107 coder triples = 258 total
- **Examples**: Trained on subset (~176 used based on checkpoint count)
- **Checkpoints**: 44, 88, 132, 176
- **Architecture change**: Replaced 5-agent pipeline with single programmer agent (`6fe8848`)
- **Changes**: First round with programmer role training (tool-calling: ask_human, list_functions, search_vault, generate_code, etc.)
- **Note**: Multiple sub-rounds on Sep 18 as data was iteratively expanded

### Round 5 — 2026-09-18 (evening, latest)

- **Data**: 151 programmer examples + 137 coder/other = 288 total in finetune.jsonl
- **Examples**: 288
- **Checkpoints**: 72, 144, 216, 288, 360
- **Test results (7/10 first run, 6/10 second run)**:

| Test | Run 1 | Run 2 | Issue |
|------|-------|-------|-------|
| S1 | PASS | PASS | |
| S2 | FAIL | FAIL | Skips search_vault, jumps to peek_result with made-up ticket ID |
| S3 | PASS | PASS | |
| S4 | FAIL | FAIL | Uses generate_code instead of iterate_code for TKT reference |
| S5 | PASS | PASS | |
| C1 | PASS | PASS | |
| C2 | PASS | FAIL | ask_human count 3→4, threshold <=3 |
| C3 | PASS | PASS | |
| C4 | FAIL | FAIL | ask_human loop (11-12x), "yes try again" triggers infinite clarification |
| C5 | PASS | PASS | |

## Known Failure Patterns

### S2: Vault reuse
Model skips search_vault entirely. Goes straight to peek_result with fabricated ticket ID. Needs examples showing: user asks about existing data → search_vault first → run_existing if found.

### S4: iterate_code recognition
Model doesn't recognize "TKT-0042 — add collateral" as iteration request. Uses generate_code instead of iterate_code. Needs examples with explicit ticket ID references triggering iterate_code.

### C4: ask_human loop
"yes try again" as response to options-style ask_human causes infinite ask_human loop. Model interprets ambiguous affirmation as needing more clarification. Needs examples where affirmative response → proceed with action.

### C2: Over-clarification
Model asks too many clarifying questions before acting. Needs examples showing: recognize impossible request → tell user → pivot on redirect without excessive follow-up questions.
