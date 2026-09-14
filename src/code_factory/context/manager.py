from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage


def estimate_tokens(text: str) -> int:
    return len(text) // 4


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
    """Compact message history by truncating old tool results.

    Keeps last `keep_last` messages intact. Earlier tool return parts
    get their content replaced with a short summary.
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
