from pathlib import Path


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
