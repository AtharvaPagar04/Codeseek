"""Assemble final LLM context from retrieved chunks."""

from functools import lru_cache
from pathlib import Path

import tiktoken

from retrieval.config import (
    FILE_CACHE_MAX_SIZE,
    HISTORY_TOKEN_CAP,
    INTENT_CONTEXT_BUDGETS,
    INTENT_HISTORY_CAPS,
    MAX_CONTEXT_TOKENS,
    get_repo_root,
)

_enc = tiktoken.get_encoding("cl100k_base")


@lru_cache(maxsize=FILE_CACHE_MAX_SIZE)
def _read_file_lines(repo_root: str, relative_path: str) -> tuple[str, ...]:
    path = Path(repo_root) / relative_path
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return tuple(handle.readlines())


def assemble(chunks: list[dict], history_block: str) -> tuple[str, list[dict], int]:
    """Create context blocks under token budget and return cited sources."""
    ranked = sorted(
        chunks,
        key=lambda c: (
            _tier(c.get("expansion_type", "primary")),
            -float(c.get("retrieval_score", 0.0)),
            c.get("relative_path", ""),
            int(c.get("start_line", 0)),
        ),
    )

    history_tokens = len(_enc.encode(history_block)) if history_block else 0
    budget = max(1, MAX_CONTEXT_TOKENS - history_tokens)

    blocks = []
    sources = []
    used = 0

    for chunk in ranked:
        content = _read_chunk_content(chunk)
        if content is None:
            continue
        block = _format_block(chunk, content)
        block_tokens = len(_enc.encode(block))
        if used + block_tokens > budget and chunk.get("expansion_type") != "primary":
            continue
        if used + block_tokens > budget and chunk.get("expansion_type") == "primary":
            block = _truncate_to_budget(block, budget - used)
            block_tokens = len(_enc.encode(block))
        blocks.append(block)
        used += block_tokens
        sources.append(
            {
                "relative_path": chunk.get("relative_path", ""),
                "symbol_name": chunk.get("symbol_name", ""),
                "start_line": int(chunk.get("start_line", 0)),
                "end_line": int(chunk.get("end_line", 0)),
                "expansion_type": chunk.get("expansion_type", "primary"),
            }
        )
        if used >= budget:
            break

    return "\n\n".join(blocks), sources, used


def intent_context_budget(primary_intent: str | None) -> int:
    """Return the token budget for the given intent string.

    Falls back to MAX_CONTEXT_TOKENS when the intent is unknown or None.
    History tokens are *not* subtracted here — the caller (assemble / assemble_for_reasoning)
    subtracts them from the returned value before filling chunks.
    """
    if not primary_intent:
        return MAX_CONTEXT_TOKENS
    return INTENT_CONTEXT_BUDGETS.get(primary_intent.upper(), MAX_CONTEXT_TOKENS)


def intent_history_cap(primary_intent: str | None) -> int:
    """Return the max tokens history is allowed to occupy for this intent.

    Returns the minimum of the global HISTORY_TOKEN_CAP and any tighter
    intent-specific cap.  Broad synthesis intents (OVERVIEW, TRACE, etc.)
    have lower caps so more of the context window stays available for code.
    """
    global_cap = HISTORY_TOKEN_CAP
    if not primary_intent:
        return global_cap
    intent_cap = INTENT_HISTORY_CAPS.get(primary_intent.upper(), global_cap)
    return min(global_cap, intent_cap)


def assemble_for_reasoning(
    reasoning_chunks: list[dict],
    history_block: str,
    primary_intent: str | None = None,
) -> tuple[str, list[dict], int]:
    """Assemble LLM context from the broader reasoning_sources set.

    Identical to assemble() in structure but uses the intent-aware budget from
    intent_context_budget().  Used by main.py for the LLM path when two-layer
    source gating is enabled.

    reasoning_chunks — the reasoning_sources list produced by split_sources_two_layer().
    history_block    — conversation history string (tokens counted against budget).
    primary_intent   — intent string from query_info (e.g. "SEMANTIC", "TRACE").

    Returns (context_string, assembled_source_list, token_count).
    """
    budget_ceiling = intent_context_budget(primary_intent)
    ranked = sorted(
        reasoning_chunks,
        key=lambda c: (
            _tier(c.get("expansion_type", "primary")),
            -float(c.get("retrieval_score", 0.0)),
            c.get("relative_path", ""),
            int(c.get("start_line", 0)),
        ),
    )

    history_tokens = len(_enc.encode(history_block)) if history_block else 0
    budget = max(1, budget_ceiling - history_tokens)

    blocks: list[str] = []
    sources: list[dict] = []
    used = 0

    for chunk in ranked:
        content = _read_chunk_content(chunk)
        if content is None:
            continue
        block = _format_block(chunk, content)
        block_tokens = len(_enc.encode(block))
        if used + block_tokens > budget and chunk.get("expansion_type") != "primary":
            continue
        if used + block_tokens > budget and chunk.get("expansion_type") == "primary":
            block = _truncate_to_budget(block, budget - used)
            block_tokens = len(_enc.encode(block))
        blocks.append(block)
        used += block_tokens
        sources.append(
            {
                "relative_path": chunk.get("relative_path", ""),
                "symbol_name": chunk.get("symbol_name", ""),
                "start_line": int(chunk.get("start_line", 0)),
                "end_line": int(chunk.get("end_line", 0)),
                "expansion_type": chunk.get("expansion_type", "primary"),
            }
        )
        if used >= budget:
            break

    return "\n\n".join(blocks), sources, used


def _tier(expansion_type: str) -> int:
    order = {"primary": 0, "split_part": 1, "parent_class": 2, "callee": 3}
    return order.get(expansion_type, 9)


def _read_chunk_content(chunk: dict) -> str | None:
    relative_path = chunk.get("relative_path")
    if not relative_path:
        return None
    repo_root = get_repo_root()
    try:
        lines = _read_file_lines(repo_root, relative_path)
    except OSError:
        return None

    start = max(0, int(chunk.get("start_line", 1)) - 1)
    end = max(start, int(chunk.get("end_line", start + 1)))
    return "".join(lines[start:end])


def _format_block(chunk: dict, content: str) -> str:
    label = chunk.get("expansion_type", "primary")
    symbol = chunk.get("symbol_name") or "<file>"
    header = (
        f"### {chunk.get('relative_path', '')} — {symbol} "
        f"({chunk.get('chunk_type', '')}, lines {chunk.get('start_line', 0)}-{chunk.get('end_line', 0)})"
    )
    lines = [header]
    if label != "primary":
        lines.append(f"[included as: {label}]")
    if chunk.get("signature"):
        lines.append(f"Signature: {chunk['signature']}")
    if chunk.get("summary"):
        lines.append(f"Summary: {chunk['summary']}")
    calls = chunk.get("calls") or []
    if calls:
        lines.append(f"Calls: {', '.join(calls[:8])}")
    lines.append("")
    lines.append(content.rstrip())
    return "\n".join(lines)


def _truncate_to_budget(text: str, remaining_tokens: int) -> str:
    if remaining_tokens <= 0:
        return ""
    tokens = _enc.encode(text)
    if len(tokens) <= remaining_tokens:
        return text
    trimmed = _enc.decode(tokens[:remaining_tokens])
    return trimmed + "\n[content truncated to fit context budget]"
