"""Finetune Qwen3.5-4B on Monty sandbox code generation using Unsloth LoRA.

Usage:
  .venv-train/bin/python3 scripts/finetune.py

Outputs:
  models/monty-coder-lora/  — LoRA adapter
  models/monty-coder-gguf/  — GGUF quantized for llama.cpp
"""
import json
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "training_data" / "finetune.jsonl"
OUTPUT_DIR = Path(__file__).parent.parent / "models" / "monty-coder-lora"
GGUF_DIR = Path(__file__).parent.parent / "models" / "monty-coder-gguf"

# Base model — Qwen3.5-4B, fits in 8GB VRAM with 4-bit LoRA
BASE_MODEL = "unsloth/Qwen3-4B-unsloth-bnb-4bit"

# LoRA config
LORA_R = 16
LORA_ALPHA = 16
LORA_DROPOUT = 0
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj",
                  "gate_proj", "up_proj", "down_proj"]

# Training config
EPOCHS = 5
BATCH_SIZE = 1
GRAD_ACCUM = 4
LR = 2e-4
MAX_SEQ_LEN = 4096
WARMUP_STEPS = 10


def load_data():
    examples = []
    with open(DATA_FILE) as f:
        for line in f:
            examples.append(json.loads(line))
    print(f"Loaded {len(examples)} training examples")
    return examples


def format_for_training(examples, tokenizer):
    """Convert chat messages to tokenized format using Unsloth's chat template."""
    texts = []
    for ex in examples:
        text = tokenizer.apply_chat_template(
            ex["messages"],
            tokenize=False,
            add_generation_prompt=False,
        )
        texts.append(text)
    return texts


def main():
    from unsloth import FastLanguageModel
    from unsloth import is_bfloat16_supported
    from datasets import Dataset
    from trl import SFTTrainer
    from transformers import TrainingArguments

    print(f"Loading base model: {BASE_MODEL}")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL,
        max_seq_length=MAX_SEQ_LEN,
        dtype=None,  # auto-detect
        load_in_4bit=True,
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        target_modules=TARGET_MODULES,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    examples = load_data()
    texts = format_for_training(examples, tokenizer)
    dataset = Dataset.from_dict({"text": texts})

    print(f"Dataset: {len(dataset)} examples")
    print(f"Sample length: {len(texts[0])} chars")

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=TrainingArguments(
            output_dir=str(OUTPUT_DIR),
            num_train_epochs=EPOCHS,
            per_device_train_batch_size=BATCH_SIZE,
            gradient_accumulation_steps=GRAD_ACCUM,
            learning_rate=LR,
            warmup_steps=WARMUP_STEPS,
            fp16=not is_bfloat16_supported(),
            bf16=is_bfloat16_supported(),
            logging_steps=5,
            save_strategy="epoch",
            optim="adamw_8bit",
            seed=42,
            max_grad_norm=0.3,
            lr_scheduler_type="cosine",
        ),
        max_seq_length=MAX_SEQ_LEN,
        dataset_text_field="text",
        packing=True,
    )

    print("Starting training...")
    stats = trainer.train()
    print(f"\nTraining done. Loss: {stats.training_loss:.4f}")

    # Save LoRA adapter
    model.save_pretrained(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    print(f"LoRA adapter saved to {OUTPUT_DIR}")

    # Export GGUF for llama.cpp
    print(f"\nExporting GGUF to {GGUF_DIR}...")
    model.save_pretrained_gguf(
        str(GGUF_DIR),
        tokenizer,
        quantization_method="q4_k_m",
    )
    print(f"GGUF saved to {GGUF_DIR}")


if __name__ == "__main__":
    main()
