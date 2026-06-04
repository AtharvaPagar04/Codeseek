"""Follow-up query resolution helpers (WS7).

Implements the follow-up memory contract defined in the response quality
refinement plan:

- Per-turn entity memory (cited files, symbols, routes, env keys, services)
- Topic-shift detection heuristics
- Entity-aware query rewriting that resolves pronouns and vague references
  against the most recent cited entities
"""

from __future__ import annotations

import re
from typing import Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Follow-up markers that indicate the query continues the previous topic.
FOLLOW_UP_PHRASES: frozenset[str] = frozenset(
    {
        "it",
        "that",
        "this",
        "those",
        "there",
        "where is it used",
        "how does that",
        "how does that work",
        "what about",
        "and that",
        "this function",
        "also",
        "same",
        "more",
        "details",
        "expand",
        "further",
    }
)

#: Short (≤4 token) queries with no clear new entity are treated as follow-ups.
SHORT_QUERY_THRESHOLD = 4

#: Number of recent turns to check first for topic-shift comparison.
TOPIC_SHIFT_RECENT_TURNS = 2

#: Maximum number of turns to look back when resolving entity context.
ENTITY_RETENTION_TURNS = 8

#: Minimum overlap for a query to be considered a continuation (not a shift).
OVERLAP_THRESHOLD = 1


# ---------------------------------------------------------------------------
# Entity extraction from answer sources
# ---------------------------------------------------------------------------

def extract_cited_entities(sources: Sequence[dict]) -> dict[str, list[str]]:
    """Extract entity sets from a list of retrieved/displayed sources.

    Returns a dict with keys: files, symbols, routes, env_keys, services.
    These are stored in per-turn entity memory after each answer.
    """
    files: list[str] = []
    symbols: list[str] = []
    routes: list[str] = []
    env_keys: list[str] = []
    services: list[str] = []

    for src in sources:
        path = str(src.get("relative_path", "") or "").strip()
        if path:
            files.append(path)
        sym = str(src.get("symbol_name", "") or "").strip()
        if sym:
            symbols.append(sym)
        # routes and env_keys can appear in source metadata
        for r in src.get("routes", []) or []:
            routes.append(str(r))
        for k in src.get("env_keys", []) or []:
            env_keys.append(str(k))
        for s in src.get("services", []) or []:
            services.append(str(s))

    return {
        "files": _dedup(files),
        "symbols": _dedup(symbols),
        "routes": _dedup(routes),
        "env_keys": _dedup(env_keys),
        "services": _dedup(services),
    }


# ---------------------------------------------------------------------------
# Compact recent-entity set
# ---------------------------------------------------------------------------

def build_recent_entity_set(
    recent_turns: Sequence[dict],
    max_turns: int = ENTITY_RETENTION_TURNS,
) -> dict[str, list[str]]:
    """Merge cited entities from the most recent *max_turns* turns into one set.

    Each entry in *recent_turns* must have an ``entities`` key produced by
    ``extract_cited_entities()``.  Turns are ordered oldest-first; the most
    recent turns get priority (later entries overwrite earlier ones for
    deduplication order).
    """
    files: list[str] = []
    symbols: list[str] = []
    routes: list[str] = []
    env_keys: list[str] = []
    services: list[str] = []

    for turn in list(recent_turns)[-max_turns:]:
        ents = turn.get("entities") or {}
        files.extend(ents.get("files", []) or [])
        symbols.extend(ents.get("symbols", []) or [])
        routes.extend(ents.get("routes", []) or [])
        env_keys.extend(ents.get("env_keys", []) or [])
        services.extend(ents.get("services", []) or [])

    return {
        "files": _dedup(files),
        "symbols": _dedup(symbols),
        "routes": _dedup(routes),
        "env_keys": _dedup(env_keys),
        "services": _dedup(services),
    }


# ---------------------------------------------------------------------------
# Topic-shift detection
# ---------------------------------------------------------------------------

def detect_topic_shift(
    raw_query: str,
    query_entities: dict,
    recent_turns: Sequence[dict],
) -> bool:
    """Return True when the new query appears to start a new topic.

    Rules (from the plan):
    - Treat as a follow-up when the query contains follow-up markers.
    - Compare new-query entities against the last TOPIC_SHIFT_RECENT_TURNS
      turns first; expand to the full ENTITY_RETENTION_TURNS window only
      when ambiguous.
    - Treat as a topic shift when there is a new explicit entity/subsystem
      and zero overlap with recent cited files, symbols, routes, env_keys,
      or services.
    - Treat as a topic shift when the primary intent changes strongly and
      the new query names a concrete new topic.
    """
    lower = raw_query.strip().lower()
    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", lower))

    # Rule 1: explicit follow-up marker → never a topic shift.
    if _has_followup_phrase(lower):
        return False

    # Rule 2: very short queries with no new entities → follow-up.
    query_has_entities = _any_entities(query_entities)
    if len(tokens) <= SHORT_QUERY_THRESHOLD and not query_has_entities:
        return False

    # No previous turns → first question, no shift needed.
    if not recent_turns:
        return False

    # Build entity overlap windows.
    close_recent = build_recent_entity_set(recent_turns, max_turns=TOPIC_SHIFT_RECENT_TURNS)
    broad_recent = build_recent_entity_set(recent_turns, max_turns=ENTITY_RETENTION_TURNS)

    # If there are no recent entities at all, treat as independent.
    if not _any_entities(close_recent) and not _any_entities(broad_recent):
        return not query_has_entities  # no context at all → not a real shift

    # Rule 3: compute overlap against the close window first.
    overlap = _entity_overlap(query_entities, close_recent)
    if overlap >= OVERLAP_THRESHOLD:
        return False

    # Expand to the broad window if the new query has entities.
    if query_has_entities:
        broad_overlap = _entity_overlap(query_entities, broad_recent)
        if broad_overlap >= OVERLAP_THRESHOLD:
            return False
        # New entities, no overlap with any recent window → topic shift.
        return True

    # Short query with no new entities → treat as follow-up.
    return False


def _has_followup_phrase(lower: str) -> bool:
    """Return True when the lowercased query contains a follow-up phrase."""
    for phrase in FOLLOW_UP_PHRASES:
        if phrase in lower:
            return True
    return False


def _any_entities(entities: dict) -> bool:
    return any(entities.get(k) for k in ("files", "symbols", "routes", "env_keys", "services"))


def _entity_overlap(query_entities: dict, recent_entities: dict) -> int:
    """Count the number of entity values shared between query and recent turns."""
    overlap = 0
    for key in ("files", "symbols", "routes", "env_keys", "services"):
        q_set = set(str(v).lower() for v in (query_entities.get(key) or []))
        r_set = set(str(v).lower() for v in (recent_entities.get(key) or []))
        overlap += len(q_set & r_set)
    return overlap


# ---------------------------------------------------------------------------
# Entity-aware query rewriting
# ---------------------------------------------------------------------------

def rewrite_follow_up_query(
    raw_query: str,
    recent_entity_set: dict[str, list[str]],
    previous_resolved_query: str,
) -> str:
    """Produce a resolved query by injecting recent entity context.

    Strategy:
    - If the raw query has explicit pronouns / vague references and the
      recent entity set has symbols/files, build a resolved query that
      prepends the anchor context so the search layer sees real names.
    - The result is used only for retrieval and stored as resolved_query.
      The original raw_query is what the LLM prompt shows the user asked.
    """
    lower = raw_query.strip().lower()
    if not lower:
        return raw_query.strip()

    # Already has an anchor from the previous resolved query.
    anchor = previous_resolved_query.strip()

    # If the query is purely a pronoun/vague reference and we have recent
    # entities, inject the most salient entity name into the resolved query.
    vague_query = _is_vague_query(lower)
    has_recent = _any_entities(recent_entity_set)

    if vague_query and has_recent:
        # Pick the most recent symbol or file as the anchor term.
        anchor_term = _most_salient_entity(recent_entity_set)
        if anchor_term and anchor_term.lower() not in lower:
            resolved = f"{anchor_term} — {raw_query.strip()}"
            # Always prepend the previous anchor context for multi-hop continuity.
            if anchor:
                return f"{anchor}\n{resolved}"
            return resolved

    # Default: combine with the previous anchor context.
    if anchor and anchor != raw_query.strip():
        return f"{anchor}\n{raw_query.strip()}"
    return raw_query.strip()


def _is_vague_query(lower: str) -> bool:
    """Return True when the query contains only vague pronoun-like references.

    A query is vague when its tokens are mostly question-words, pronouns, or
    common filler words — leaving at most 1 concrete content token.
    """
    vague_tokens = {
        # pronouns
        "it", "that", "this", "those", "there", "they", "them",
        "its", "their", "the", "same", "also",
        # question words
        "where", "what", "which", "when", "how", "why", "who",
        # auxiliary / copula
        "is", "are", "was", "were", "be", "been", "being",
        "do", "does", "did", "have", "has", "had",
        "can", "could", "will", "would", "should", "may", "might",
        # fillers
        "a", "an", "of", "in", "on", "at", "to", "for", "by", "with",
        "from", "and", "or", "not", "but", "so", "as",
        # common follow-up content words that don't anchor a new topic
        "used", "show", "me", "please", "provide", "give", "tell",
        "code", "snippet", "example", "more", "details", "about",
        "explain", "describe", "list", "find", "look",
    }
    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", lower))
    # Vague if the non-stopword content is at most 1 concrete term
    content_tokens = tokens - vague_tokens
    return len(content_tokens) <= 1


def _most_salient_entity(entity_set: dict[str, list[str]]) -> str:
    """Pick the best single entity to use as the anchor in a rewritten query."""
    # Prefer symbols over files over services.
    for key in ("symbols", "files", "services", "routes", "env_keys"):
        values = entity_set.get(key) or []
        if values:
            return values[-1]  # most recently cited
    return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedup(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in reversed(values):  # keep most-recent first
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out
