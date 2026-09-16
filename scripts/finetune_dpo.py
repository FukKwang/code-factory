"""DPO finetune on top of existing LoRA adapter.

Teaches model to prefer correct code over common failure modes:
wrong host functions, hardcoded values, missing result, mock redefinitions.

Usage:
  .venv-train/bin/python3 scripts/finetune_dpo.py

Requires: generate_dpo_pairs.py run first.
"""
import json
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "training_data" / "dpo_pairs.jsonl"
LORA_DIR = Path(__file__).parent.parent / "models" / "monty-coder-lora"
OUTPUT_DIR = Path(__file__).parent.parent / "models" / "monty-coder-dpo"
GGUF_DIR = Path(__file__).parent.parent / "models" / "monty-coder-dpo-gguf"

BASE_MODEL = "unsloth/Qwen3-4B-unsloth-bnb-4bit"
MAX_SEQ_LEN = 4096

# DPO config
EPOCHS = 3
BATCH_SIZE = 1
GRAD_ACCUM = 4
LR = 5e-5  # lower than SFT — DPO is more sensitive
BETA = 0.1  # KL penalty weight
WARMUP_STEPS = 10
MAX_LENGTH = 768
MAX_PROMPT_LENGTH = 512


def load_pairs():
    pairs = []
    with open(DATA_FILE) as f:
        for line in f:
            pairs.append(json.loads(line))
    print(f"Loaded {len(pairs)} DPO pairs")
    return pairs


def format_for_dpo(pairs, tokenizer):
    """Convert pairs to DPO dataset format."""
    prompts = []
    chosens = []
    rejecteds = []

    for pair in pairs:
        prompt_text = tokenizer.apply_chat_template(
            pair["prompt"], tokenize=False, add_generation_prompt=True,
        )
        chosen_text = tokenizer.apply_chat_template(
            pair["prompt"] + pair["chosen"], tokenize=False, add_generation_prompt=False,
        )
        rejected_text = tokenizer.apply_chat_template(
            pair["prompt"] + pair["rejected"], tokenize=False, add_generation_prompt=False,
        )
        prompts.append(prompt_text)
        chosens.append(chosen_text)
        rejecteds.append(rejected_text)

    return prompts, chosens, rejecteds


def main():
    from unsloth import FastLanguageModel, is_bfloat16_supported
    from datasets import Dataset
    from trl import DPOConfig, DPOTrainer

    print(f"Loading SFT model from {LORA_DIR}...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=str(LORA_DIR),
        max_seq_length=MAX_SEQ_LEN,
        dtype=None,
        load_in_4bit=True,
    )

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    pairs = load_pairs()
    prompts, chosens, rejecteds = format_for_dpo(pairs, tokenizer)

    dataset = Dataset.from_dict({
        "prompt": prompts,
        "chosen": chosens,
        "rejected": rejecteds,
    })

    print(f"Dataset: {len(dataset)} pairs")
    print(f"Sample prompt length: {len(prompts[0])} chars")

    FastLanguageModel.for_training(model)

    trainer = DPOTrainer(
        model=model,
        train_dataset=dataset,
        tokenizer=tokenizer,
        args=DPOConfig(
            output_dir=str(OUTPUT_DIR),
            num_train_epochs=EPOCHS,
            per_device_train_batch_size=BATCH_SIZE,
            gradient_accumulation_steps=GRAD_ACCUM,
            learning_rate=LR,
            beta=BETA,
            warmup_steps=WARMUP_STEPS,
            fp16=not is_bfloat16_supported(),
            bf16=is_bfloat16_supported(),
            logging_steps=5,
            save_strategy="epoch",
            optim="adamw_8bit",
            seed=42,
            max_length=MAX_LENGTH,
            max_prompt_length=MAX_PROMPT_LENGTH,
            lr_scheduler_type="cosine",
            gradient_checkpointing=True,
            precompute_ref_log_probs=True,
            max_grad_norm=0.3,
        ),
    )

    print("Starting DPO training...")
    stats = trainer.train()
    print(f"\nDPO training done. Loss: {stats.training_loss:.4f}")

    model.save_pretrained(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    print(f"DPO adapter saved to {OUTPUT_DIR}")

    print(f"\nExporting GGUF to {GGUF_DIR}...")
    model.save_pretrained_gguf(
        str(GGUF_DIR),
        tokenizer,
        quantization_method="q4_k_m",
    )
    print(f"GGUF saved to {GGUF_DIR}")
    print("Done.")


if __name__ == "__main__":
    main()
