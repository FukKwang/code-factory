from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def estimate_history_tokens(messages: list[ModelMessage]) -> int:
    """Estimate total tokens in message history."""
    total = 0
    for msg in messages:
        for part in msg.parts:
            total += estimate_tokens(str(getattr(part, 'content', '')))
            total += estimate_tokens(str(getattr(part, 'args', '')))
    return total


def truncate_for_context(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"


def write_findings_file(directory: Path, name: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(content)
    return path


def compact_messages(messages: list[ModelMessage], keep_last: int = 4) -> list[ModelMessage]:
    """Compact old tool results to free tokens while preserving KV cache prefix.

    Compacts from the FRONT: oldest tool returns get stubbed first.
    Recent messages (keep_last) stay byte-identical for cache reuse.
    """
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    if len(messages) <= keep_last:
        return messages

    compacted = []
    boundary = len(messages) - keep_last

    for i, msg in enumerate(messages):
        if i < boundary and isinstance(msg, ModelRequest):
            new_parts = []
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    content = str(part.content)
                    if len(content) > 100:
                        summary = content[:80] + "... (compacted)"
                        new_parts.append(ToolReturnPart(
                            tool_name=part.tool_name,
                            content=summary,
                            tool_call_id=part.tool_call_id,
                            timestamp=part.timestamp,
                            outcome=part.outcome,
                        ))
                    else:
                        new_parts.append(part)
                else:
                    new_parts.append(part)
            compacted.append(ModelRequest(parts=new_parts, instructions=msg.instructions))
        else:
            compacted.append(msg)

    return compacted


def maybe_compact(messages: list[ModelMessage], max_tokens: int) -> list[ModelMessage]:
    """Only compact when approaching token budget. Preserves KV cache prefix.

    KV cache optimization: LLM servers match token prefixes to reuse cached
    key-value pairs. Modifying old messages breaks prefix match, forcing full
    recomputation. So we keep history byte-identical between turns and only
    compact when token budget forces it.

    Strategy (from Leyline/DeepSeek patterns):
    - Below 70% budget: no change (full cache reuse)
    - 70-90%: light compact (keep_last=6, stubs for old tool returns)
    - 90%+: aggressive compact (keep_last=2)
    """
    used = estimate_history_tokens(messages)
    ratio = used / max(max_tokens, 1)

    if ratio < 0.7:
        return messages
    if ratio < 0.9:
        return compact_messages(messages, keep_last=6)
    return compact_messages(messages, keep_last=2)
