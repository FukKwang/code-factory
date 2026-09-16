"""Generate DPO preference pairs from validated training triples.

Creates (chosen, rejected) pairs by synthetically corrupting good code
to match real failure modes observed during testing:
  1. Wrong host function (swaps function calls)
  2. Hardcoded values (replaces inputs[] with literals)
  3. Missing result assignment
  4. Redefined host functions (adds mock definitions)

Usage:
  python3 scripts/generate_dpo_pairs.py
"""
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_factory.agents.coder import MONTY_LIMITATIONS
from code_factory.sandbox.host_functions import HOST_FUNCTION_DESCRIPTIONS

TRIPLES_FILE = Path(__file__).parent.parent / "training_data" / "triples.json"
VAULT_DIR = Path("/home/kwang/Documents/dev/code-factory-repo/tickets")
OUT_FILE = Path(__file__).parent.parent / "training_data" / "dpo_pairs.jsonl"

random.seed(42)

SYSTEM_PROMPT = f"""{MONTY_LIMITATIONS}

You will be given:
1. A specification describing what the program should do
2. Available host functions with their signatures
3. Input parameters the program receives via the `inputs` dict
4. Test assertions the code must pass

Write ONLY the Python code. No markdown fences, no explanations."""

ALL_HOST_FUNCTIONS = list(HOST_FUNCTION_DESCRIPTIONS.keys())


def build_fn_docs(fn_list):
    return "\n".join(
        f"- {HOST_FUNCTION_DESCRIPTIONS[f]}" for f in fn_list
        if f in HOST_FUNCTION_DESCRIPTIONS
    )


def build_user_prompt(spec, host_functions, input_schema, tests):
    fn_doc = build_fn_docs(host_functions)
    parts = [f"## Spec\n{spec}"]
    parts.append(f"\n## Host functions\n{fn_doc}")
    if input_schema:
        schema_lines = "\n".join(f"  - {k}: {v}" for k, v in input_schema.items())
        parts.append(f"\n## Input parameters (in `inputs` dict)\n{schema_lines}")
    parts.append(f"\n## Tests (code must pass these)\n```\n{tests}\n```")
    return "\n".join(parts)


# --- Corruption strategies ---

def corrupt_wrong_function(code, host_functions):
    """Swap one host function call with a different host function."""
    other_fns = [f for f in ALL_HOST_FUNCTIONS if f not in host_functions]
    if not other_fns:
        return None
    for fn in host_functions:
        if fn + "(" in code:
            replacement = random.choice(other_fns)
            return code.replace(fn + "(", replacement + "(", 1)
    return None


def corrupt_hardcoded(code, input_schema):
    """Replace inputs["key"] with hardcoded literal values."""
    if not input_schema:
        return None
    corrupted = code
    replacements = 0
    for key in input_schema:
        patterns = [f'inputs["{key}"]', f"inputs['{key}']", f'inputs.get("{key}"', f"inputs.get('{key}'"]
        for pat in patterns:
            if pat in corrupted:
                if "city" in key.lower() or "name" in key.lower():
                    val = '"Jakarta"'
                elif "amount" in key.lower() or "score" in key.lower():
                    val = "1000000"
                elif "id" in key.lower():
                    val = '"LOAN-001"'
                else:
                    val = '"test_value"'
                if pat.startswith("inputs.get"):
                    val_with_close = val + ")"
                    corrupted = corrupted.replace(pat + ")", val_with_close, 1)
                    corrupted = corrupted.replace(pat + ",", val + " #", 1)
                else:
                    corrupted = corrupted.replace(pat, val, 1)
                replacements += 1
                break
    return corrupted if replacements > 0 else None


def corrupt_no_result(code):
    """Remove or rename the result assignment."""
    if not re.search(r'^result\s*=', code, re.MULTILINE):
        return None
    lines = code.split("\n")
    new_lines = []
    for line in lines:
        if re.match(r'^result\s*=', line):
            new_lines.append(line.replace("result", "output", 1))
        else:
            new_lines.append(line)
    return "\n".join(new_lines)


def corrupt_redefine_functions(code, host_functions):
    """Add mock host function definitions before the real code."""
    fn = host_functions[0]
    mock = f'def {fn}(args):\n    return {{"id": "mock-1", "name": "Mock Data", "status": "active"}}\n\n'
    return mock + code


def corrupt_print_instead(code):
    """Replace result assignment with print statement (common model failure)."""
    if not re.search(r'^result\s*=', code, re.MULTILINE):
        return None
    lines = code.split("\n")
    new_lines = []
    for line in lines:
        if re.match(r'^result\s*=', line):
            val = line.split("=", 1)[1].strip()
            new_lines.append(f"print({val})")
        else:
            new_lines.append(line)
    return "\n".join(new_lines)


CORRUPTIONS = [
    ("wrong_function", corrupt_wrong_function),
    ("hardcoded", corrupt_hardcoded),
    ("no_result", corrupt_no_result),
    ("redefine", corrupt_redefine_functions),
    ("print_instead", corrupt_print_instead),
]


def load_vault_examples():
    examples = []
    if not VAULT_DIR.exists():
        return examples
    for ticket_dir in sorted(VAULT_DIR.iterdir()):
        if not ticket_dir.is_dir():
            continue
        yaml_file = ticket_dir / "ticket.yaml"
        sol_file = ticket_dir / "solution.py"
        test_file = ticket_dir / "test_solution.py"
        spec_file = ticket_dir / "spec.md"
        if not all(f.exists() for f in [yaml_file, sol_file, test_file, spec_file]):
            continue
        yaml_text = yaml_file.read_text()
        quality = ""
        for line in yaml_text.split("\n"):
            if line.startswith("quality:"):
                quality = line.split(":", 1)[1].strip()
        if quality != "good":
            continue
        host_functions = []
        in_hf = False
        for line in yaml_text.split("\n"):
            if line.startswith("host_function_allowlist:"):
                in_hf = True
                continue
            if in_hf:
                if line.startswith("- "):
                    host_functions.append(line[2:].strip())
                else:
                    in_hf = False
        input_schema = {}
        in_schema = False
        for line in yaml_text.split("\n"):
            if line.startswith("input_schema:"):
                in_schema = True
                continue
            if in_schema:
                if line.startswith("  "):
                    parts = line.strip().split(":", 1)
                    if len(parts) == 2:
                        input_schema[parts[0].strip()] = parts[1].strip()
                else:
                    in_schema = False
        examples.append({
            "spec": spec_file.read_text().strip(),
            "host_functions": host_functions,
            "input_schema": input_schema,
            "tests": test_file.read_text().strip(),
            "code": sol_file.read_text().strip(),
        })
    return examples


def main():
    triples = []
    if TRIPLES_FILE.exists():
        with open(TRIPLES_FILE) as f:
            triples = json.load(f)

    vault = load_vault_examples()
    all_examples = triples + vault
    print(f"Source examples: {len(triples)} triples + {len(vault)} vault = {len(all_examples)}")

    pairs = []
    corruption_counts = {}

    for ex in all_examples:
        prompt = build_user_prompt(
            ex["spec"], ex["host_functions"],
            ex.get("input_schema", {}), ex["tests"],
        )
        chosen = ex["code"]

        for name, corrupt_fn in CORRUPTIONS:
            if name == "wrong_function":
                rejected = corrupt_fn(chosen, ex["host_functions"])
            elif name == "hardcoded":
                rejected = corrupt_fn(chosen, ex.get("input_schema", {}))
            elif name == "redefine":
                rejected = corrupt_fn(chosen, ex["host_functions"])
            else:
                rejected = corrupt_fn(chosen)

            if rejected and rejected != chosen:
                pairs.append({
                    "prompt": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "chosen": [{"role": "assistant", "content": chosen}],
                    "rejected": [{"role": "assistant", "content": rejected}],
                })
                corruption_counts[name] = corruption_counts.get(name, 0) + 1

    random.shuffle(pairs)

    with open(OUT_FILE, "w") as f:
        for pair in pairs:
            f.write(json.dumps(pair) + "\n")

    print(f"\nGenerated {len(pairs)} DPO pairs:")
    for name, count in sorted(corruption_counts.items()):
        print(f"  {name}: {count}")
    print(f"\nSaved to {OUT_FILE}")


if __name__ == "__main__":
    main()
