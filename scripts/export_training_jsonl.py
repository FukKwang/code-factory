"""Export training triples + vault examples to chat-format JSONL for finetuning."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_factory.agents.coder import MONTY_LIMITATIONS
from code_factory.sandbox.host_functions import HOST_FUNCTION_DESCRIPTIONS

VAULT_DIR = Path("/home/kwang/Documents/dev/code-factory-repo/tickets")
TRIPLES_FILE = Path(__file__).parent.parent / "training_data" / "triples.json"
OUT_FILE = Path(__file__).parent.parent / "training_data" / "finetune.jsonl"

SYSTEM_PROMPT = f"""{MONTY_LIMITATIONS}

You will be given:
1. A specification describing what the program should do
2. Available host functions with their signatures
3. Input parameters the program receives via the `inputs` dict
4. Test assertions the code must pass

Write ONLY the Python code. No markdown fences, no explanations."""


def build_fn_docs(fn_list: list[str]) -> str:
    return "\n".join(
        f"- {HOST_FUNCTION_DESCRIPTIONS[f]}" for f in fn_list
        if f in HOST_FUNCTION_DESCRIPTIONS
    )


def build_user_prompt(spec: str, host_functions: list[str],
                      input_schema: dict[str, str], tests: str) -> str:
    fn_doc = build_fn_docs(host_functions)
    parts = [f"## Spec\n{spec}"]
    parts.append(f"\n## Host functions\n{fn_doc}")
    if input_schema:
        schema_lines = "\n".join(f"  - {k}: {v}" for k, v in input_schema.items())
        parts.append(f"\n## Input parameters (in `inputs` dict)\n{schema_lines}")
    parts.append(f"\n## Tests (code must pass these)\n```\n{tests}\n```")
    return "\n".join(parts)


def triple_to_chat(triple: dict) -> dict:
    user = build_user_prompt(
        triple["spec"],
        triple["host_functions"],
        triple.get("input_schema", {}),
        triple["tests"],
    )
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
            {"role": "assistant", "content": triple["code"]},
        ]
    }


def load_vault_examples() -> list[dict]:
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

        # Parse yaml manually (avoid pyyaml import issues)
        yaml_text = yaml_file.read_text()
        quality = ""
        for line in yaml_text.split("\n"):
            if line.startswith("quality:"):
                quality = line.split(":", 1)[1].strip()
        if quality != "good":
            continue

        # Extract host functions from yaml
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

        # Extract input_schema
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

        spec = spec_file.read_text().strip()
        code = sol_file.read_text().strip()
        tests = test_file.read_text().strip()

        if not code or not tests:
            continue

        examples.append({
            "spec": spec,
            "host_functions": host_functions,
            "input_schema": input_schema,
            "tests": tests,
            "code": code,
        })
    return examples


def main():
    # Load generated triples
    triples = []
    if TRIPLES_FILE.exists():
        with open(TRIPLES_FILE) as f:
            triples = json.load(f)

    # Load vault examples
    vault = load_vault_examples()

    all_examples = triples + vault
    print(f"Generated triples: {len(triples)}")
    print(f"Vault examples: {len(vault)}")
    print(f"Total: {len(all_examples)}")

    # Export as JSONL
    with open(OUT_FILE, "w") as f:
        for ex in all_examples:
            chat = triple_to_chat(ex)
            f.write(json.dumps(chat) + "\n")

    # Stats
    sizes = []
    for ex in all_examples:
        chat = triple_to_chat(ex)
        total = sum(len(m["content"]) for m in chat["messages"])
        sizes.append(total)

    avg_chars = sum(sizes) / len(sizes)
    avg_tokens = avg_chars / 4  # rough estimate
    print(f"\nAvg example size: {avg_chars:.0f} chars (~{avg_tokens:.0f} tokens)")
    print(f"Total dataset: {sum(sizes)} chars (~{sum(sizes)//4} tokens)")
    print(f"Saved to {OUT_FILE}")


if __name__ == "__main__":
    main()
