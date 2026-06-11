"""Chunk labeling stage using rule-based and LLM-assisted models."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rag_ingestion.label_constants import (
    CODESEEK_INTERNAL_LABELS,
    MAX_CONFIDENCE,
    MAX_LABELS_PER_CATEGORY,
    MAX_TOTAL_LABELS,
    MIN_CONFIDENCE,
    STRONG_MATCH,
    MEDIUM_MATCH,
    WEAK_MATCH,
)

if TYPE_CHECKING:
    from rag_ingestion.models.chunk import Chunk


def add_label(candidates: dict[str, float], label: str, confidence: float) -> None:
    """Add or boost a label's confidence in candidates dict."""
    if label in candidates:
        existing = candidates[label]
        boosted = max(existing, confidence) + 0.05
        candidates[label] = round(min(MAX_CONFIDENCE, boosted), 4)
    else:
        candidates[label] = round(min(MAX_CONFIDENCE, confidence), 4)


def select_top_labels(candidates: dict[str, float]) -> list[str]:
    """Select the top labels from candidates conforming to category and total limits."""
    # 1. Filter out labels below MIN_CONFIDENCE
    filtered = {
        label: conf
        for label, conf in candidates.items()
        if conf >= MIN_CONFIDENCE
    }

    # 2. Group by category
    by_category: dict[str, list[tuple[str, float]]] = {}
    for label, conf in filtered.items():
        category = label.split(":", 1)[0]
        if category not in by_category:
            by_category[category] = []
        by_category[category].append((label, conf))

    # 3. Apply category limits
    selected_candidates: list[tuple[str, float]] = []
    for category, items in by_category.items():
        # Sort items by confidence descending
        items.sort(key=lambda x: x[1], reverse=True)
        # Cap to MAX_LABELS_PER_CATEGORY
        limit = MAX_LABELS_PER_CATEGORY.get(category, 999)
        selected_candidates.extend(items[:limit])

    # 4. Sort all selected candidates by confidence descending
    selected_candidates.sort(key=lambda x: x[1], reverse=True)

    # 5. Cap to MAX_TOTAL_LABELS
    final_candidates = selected_candidates[:MAX_TOTAL_LABELS]

    # 6. Extract label strings and sort alphabetically
    final_labels = [item[0] for item in final_candidates]
    final_labels.sort()

    return final_labels


def _first_sentence(text: str) -> str:
    """Extract the first sentence from a text block, ensuring it ends with a period."""
    if not text:
        return ""
    text = text.strip()
    if not text:
        return ""

    # Look for a sentence terminator followed by a space, capital letter, or end of string
    match = re.search(r'([^.!?]+[.!?])(?:\s|[A-Z]|$)', text)
    if match:
        sentence = match.group(1).strip()
        # If it doesn't end with a proper sentence terminator, append '.'
        if not sentence.endswith((".", "!", "?")):
            sentence += "."
        return sentence

    # No terminator found. Truncate if too long (e.g. 120 chars) and append '.'
    if len(text) > 120:
        truncated = text[:120].rstrip()
        truncated = re.sub(r'[^a-zA-Z0-9]+$', '', truncated)
        return truncated + "."

    if not text.endswith((".", "!", "?")):
        text += "."
    return text


def derive_code_intent(chunk: Chunk) -> str:
    """Derive the user intent explanation for a code chunk."""
    desc = getattr(chunk, "description", "") or ""
    if desc.strip():
        return _first_sentence(desc)

    summary = getattr(chunk, "summary", "") or ""
    if summary.strip():
        return _first_sentence(summary)

    symbol = getattr(chunk, "symbol_name", "") or getattr(chunk, "qualified_symbol", "") or ""
    if symbol.strip():
        type_prefix = chunk.chunk_type.capitalize() if chunk.chunk_type else "Symbol"
        return f"{type_prefix}: {symbol}."

    return ""


def is_codeseek_repo(repo_name: str | None, repo_root: str | None) -> bool:
    """Determine if the repository is CodeSeek itself."""
    name = (repo_name or "").lower()
    root = (repo_root or "").lower()
    return "codeseek" in name or "codeseek" in root


def filter_repo_specific_labels(candidates: dict[str, float], is_codeseek: bool) -> dict[str, float]:
    """Remove CodeSeek-internal labels for external repositories."""
    if is_codeseek:
        return candidates
    return {
        label: conf
        for label, conf in candidates.items()
        if label not in CODESEEK_INTERNAL_LABELS
    }


def label_chunk(chunk: Chunk, *, repo_name: str | None = None, repo_root: str | None = None) -> Chunk:
    """Label a single chunk of code/text."""
    candidates: dict[str, float] = {}
    path = (chunk.relative_path or "").lower()

    # b. Artifact + code_role from chunk_type (STRONG_MATCH)
    if chunk.chunk_type == "function":
        add_label(candidates, "artifact:source-code", STRONG_MATCH)
        add_label(candidates, "code_role:function", STRONG_MATCH)
    elif chunk.chunk_type == "method":
        add_label(candidates, "artifact:source-code", STRONG_MATCH)
        add_label(candidates, "code_role:method", STRONG_MATCH)
    elif chunk.chunk_type == "class":
        add_label(candidates, "artifact:source-code", STRONG_MATCH)
        add_label(candidates, "code_role:class", STRONG_MATCH)
    elif chunk.chunk_type == "repo_summary":
        add_label(candidates, "artifact:repo-summary", STRONG_MATCH)

    # c. Artifact from file_type (STRONG_MATCH)
    if chunk.file_type == "readme" or "readme" in path:
        add_label(candidates, "artifact:readme", STRONG_MATCH)
    elif chunk.file_type == "package_json" or "package.json" in path:
        add_label(candidates, "artifact:package-manifest", STRONG_MATCH)
        add_label(candidates, "capability:dependency-management", STRONG_MATCH)
    elif chunk.file_type == "dockerfile" or "dockerfile" in path:
        add_label(candidates, "artifact:dockerfile", STRONG_MATCH)
        add_label(candidates, "domain:devops", STRONG_MATCH)
        add_label(candidates, "tech:docker", STRONG_MATCH)
    elif chunk.file_type == "docker_compose" or "docker-compose" in path:
        add_label(candidates, "artifact:docker-compose", STRONG_MATCH)
        add_label(candidates, "domain:devops", STRONG_MATCH)
        add_label(candidates, "tech:docker", STRONG_MATCH)
    elif chunk.file_type == "env_example" or ".env.example" in path:
        add_label(candidates, "artifact:env-example", STRONG_MATCH)

    # d. Domain from path segments (STRONG_MATCH)
    if "auth" in path:
        add_label(candidates, "domain:auth", STRONG_MATCH)
    if "retrieval" in path:
        add_label(candidates, "domain:retrieval", STRONG_MATCH)
    if "ingestion" in path:
        add_label(candidates, "domain:ingestion", STRONG_MATCH)
    if "provider" in path:
        add_label(candidates, "domain:provider-management", STRONG_MATCH)
    if "frontend" in path:
        add_label(candidates, "domain:frontend", STRONG_MATCH)
    if "test" in path:
        add_label(candidates, "artifact:test-code", STRONG_MATCH)
        add_label(candidates, "domain:testing", STRONG_MATCH)

    # e. Capability + tech from imports/calls (STRONG_MATCH)
    imports_and_calls = set(chunk.imports or []) | set(chunk.calls or [])
    if any(x in imports_and_calls for x in ("QdrantClient", "qdrant_client")):
        add_label(candidates, "tech:qdrant", STRONG_MATCH)
        add_label(candidates, "domain:vector-db", STRONG_MATCH)
        add_label(candidates, "capability:qdrant-storage", STRONG_MATCH)
    if any(x in imports_and_calls for x in ("upsert", "PointStruct")):
        add_label(candidates, "capability:vector-upsert", STRONG_MATCH)
    if any(x in imports_and_calls for x in ("model.encode", "SentenceTransformer")):
        add_label(candidates, "tech:sentence-transformers", STRONG_MATCH)
        add_label(candidates, "capability:embedding-generation", STRONG_MATCH)
    if any(x in imports_and_calls for x in ("StreamingResponse", "text/event-stream")):
        add_label(candidates, "tech:sse", STRONG_MATCH)
        add_label(candidates, "capability:live-indexing-events", STRONG_MATCH)

    # f. Domain/capability from summary + description (MEDIUM_MATCH)
    text = ((chunk.summary or "") + " " + (chunk.description or "")).lower()
    if "session_token" in text or "auth_sessions" in text:
        add_label(candidates, "domain:auth", MEDIUM_MATCH)
        add_label(candidates, "capability:session-validation", MEDIUM_MATCH)
        add_label(candidates, "capability:token-validation", MEDIUM_MATCH)
    if "qdrant" in text or "qdrantclient" in text:
        add_label(candidates, "domain:vector-db", MEDIUM_MATCH)
        add_label(candidates, "tech:qdrant", MEDIUM_MATCH)
        add_label(candidates, "capability:qdrant-storage", MEDIUM_MATCH)
    if "embedding" in text or "encode" in text:
        add_label(candidates, "capability:embedding-generation", MEDIUM_MATCH)

    # g. Weak content matching (WEAK_MATCH)
    domain_cap_count = sum(1 for k in candidates if k.startswith(("domain:", "capability:")))
    if domain_cap_count < 2:
        content_excerpt = getattr(chunk, "content_excerpt", "") or ""
        content_text = ((chunk.content or "")[:2000] or content_excerpt[:2000]).lower()
        if "session_token" in content_text or "auth_sessions" in content_text:
            add_label(candidates, "domain:auth", WEAK_MATCH)
            add_label(candidates, "capability:session-validation", WEAK_MATCH)
            add_label(candidates, "capability:token-validation", WEAK_MATCH)
        if "qdrant" in content_text or "qdrantclient" in content_text:
            add_label(candidates, "domain:vector-db", WEAK_MATCH)
            add_label(candidates, "tech:qdrant", WEAK_MATCH)
            add_label(candidates, "capability:qdrant-storage", WEAK_MATCH)
        if "embedding" in content_text or "encode" in content_text:
            add_label(candidates, "capability:embedding-generation", WEAK_MATCH)

    # h. question_use from chunk_type
    is_test = "test" in path
    is_config = (
        chunk.file_type in ("dockerfile", "docker_compose", "env_example") or
        any(cfg in path for cfg in ("docker-compose", "dockerfile", "pyproject.toml", "requirements.txt", "setup.py", ".env"))
    )

    if chunk.chunk_type == "repo_summary":
        add_label(candidates, "question_use:repo-overview", STRONG_MATCH)
        add_label(candidates, "question_use:general-context", STRONG_MATCH)
    elif chunk.file_type == "readme" or "readme" in path:
        add_label(candidates, "question_use:repo-overview", STRONG_MATCH)
        add_label(candidates, "question_use:setup", STRONG_MATCH)
    elif chunk.file_type == "package_json" or "package.json" in path:
        add_label(candidates, "question_use:dependency-question", STRONG_MATCH)
        add_label(candidates, "question_use:setup", STRONG_MATCH)
    elif is_config:
        add_label(candidates, "question_use:config-question", STRONG_MATCH)
        add_label(candidates, "question_use:general-context", STRONG_MATCH)
    elif is_test:
        add_label(candidates, "question_use:test-validation", STRONG_MATCH)
        add_label(candidates, "question_use:debugging", STRONG_MATCH)
        add_label(candidates, "question_use:implementation", MEDIUM_MATCH)
    elif chunk.chunk_type in ("function", "method", "class", "component", "hook") or (
        chunk.chunk_type == "file" and not is_test and not is_config
    ):
        add_label(candidates, "question_use:technical-explanation", STRONG_MATCH)
        add_label(candidates, "question_use:code-location", STRONG_MATCH)
        add_label(candidates, "question_use:implementation", MEDIUM_MATCH)
        if chunk.chunk_type in ("function", "method", "class", "component", "hook"):
            add_label(candidates, "question_use:code-snippet", STRONG_MATCH)

    # i. Filter CodeSeek internal labels
    is_codeseek = is_codeseek_repo(repo_name, repo_root)
    candidates = filter_repo_specific_labels(candidates, is_codeseek=is_codeseek)

    # j. Select top labels
    chunk.label_confidences = candidates
    chunk.labels = select_top_labels(candidates)

    # k. Derive code_intent
    chunk.code_intent = derive_code_intent(chunk)

    # l. Apply fallbacks if empty
    if not chunk.labels:
        fallback_val = MIN_CONFIDENCE + 0.01
        if chunk.chunk_type == "repo_summary":
            add_label(candidates, "artifact:repo-summary", fallback_val)
            add_label(candidates, "question_use:repo-overview", fallback_val)
        elif chunk.file_type == "readme" or "readme" in path:
            add_label(candidates, "artifact:readme", fallback_val)
            add_label(candidates, "question_use:repo-overview", fallback_val)
        elif chunk.file_type == "package_json" or "package.json" in path:
            add_label(candidates, "artifact:package-manifest", fallback_val)
            add_label(candidates, "question_use:dependency-question", fallback_val)
        elif "test" in path:
            add_label(candidates, "artifact:test-code", fallback_val)
            add_label(candidates, "question_use:test-validation", fallback_val)
        else:
            add_label(candidates, "artifact:source-code", fallback_val)
            add_label(candidates, "question_use:general-context", fallback_val)

        # Re-run selection
        candidates = filter_repo_specific_labels(candidates, is_codeseek=is_codeseek)
        chunk.label_confidences = candidates
        chunk.labels = select_top_labels(candidates)

    return chunk


def label_chunks(chunks: list[Chunk], *, repo_name: str | None = None, repo_root: str | None = None) -> list[Chunk]:
    """Label all chunks in a collection."""
    for chunk in chunks:
        label_chunk(chunk, repo_name=repo_name, repo_root=repo_root)
    return chunks


# ---------------------------------------------------------------------------
# Group 12 — Optional LLM label refinement
# ---------------------------------------------------------------------------

import json
import logging

import httpx as _httpx_mod  # module-level so tests can patch rag_ingestion.stages.labeler._httpx_mod

from rag_ingestion.config import ENABLE_LLM_LABEL_REFINEMENT  # noqa: E402 — after TYPE_CHECKING block

_refinement_logger = logging.getLogger(__name__)

# Important path keywords that make LLM refinement more likely to add value.
_IMPORTANT_PATH_KEYWORDS = frozenset({
    "auth", "provider", "qdrant", "retrieval", "ingestion",
    "config", "package.json", "readme", "repo_summary",
})


def label_refinement_priority(
    chunk: Chunk,
    label_confidences: dict[str, float] | None = None,
) -> float:
    """Compute a priority score indicating how much LLM refinement may improve labels.

    Higher score → more likely to benefit from LLM refinement.
    Never raises.
    """
    try:
        labels: list[str] = list(getattr(chunk, "labels", None) or [])
        score = 0.0

        # No labels at all: highest priority
        if not labels:
            score += 5.0

        if len(labels) <= 3:
            score += 2.0

        if not any(label.startswith("domain:") for label in labels):
            score += 1.5

        if not any(label.startswith("capability:") for label in labels):
            score += 1.0

        # LLM description present adds signal quality
        if getattr(chunk, "description", ""):
            score += 1.0

        # Substantive chunk types benefit more
        if getattr(chunk, "chunk_type", "") in {
            "function", "method", "class", "file", "repo_summary"
        }:
            score += 0.5

        # Path contains an important area keyword
        path = (getattr(chunk, "relative_path", "") or "").lower()
        if any(kw in path for kw in _IMPORTANT_PATH_KEYWORDS):
            score += 1.0

        # Already at cap: no point refining
        if len(labels) >= MAX_TOTAL_LABELS:
            score -= 5.0

        return score
    except Exception:
        return 0.0


def select_chunks_for_refinement(
    chunks: list[Chunk],
    max_chunks: int | None = None,
) -> list[Chunk]:
    """Return at most *max_chunks* chunks with the highest refinement priority.

    Skips:
    - Chunks that are already at MAX_TOTAL_LABELS.
    - Chunks with no summary, no description, and no content_excerpt.
    Never mutates input chunks.
    """
    from rag_ingestion.config import CHUNK_LABEL_LLM_MAX_CHUNKS  # noqa: PLC0415

    limit = max_chunks if max_chunks is not None else CHUNK_LABEL_LLM_MAX_CHUNKS

    eligible = [
        c for c in chunks
        if _is_eligible_for_refinement(c)
    ]

    ranked = sorted(
        eligible,
        key=lambda c: label_refinement_priority(c, getattr(c, "label_confidences", None)),
        reverse=True,
    )
    return ranked[:limit]


def _is_eligible_for_refinement(chunk: Chunk) -> bool:
    """Return True if the chunk is worth sending to LLM for label refinement."""
    labels = list(getattr(chunk, "labels", None) or [])
    if len(labels) >= MAX_TOTAL_LABELS:
        return False
    summary = (getattr(chunk, "summary", "") or "").strip()
    description = (getattr(chunk, "description", "") or "").strip()
    excerpt = (getattr(chunk, "content_excerpt", "") or "").strip()
    if not summary and not description and not excerpt:
        return False
    return True


def build_label_refinement_prompt(chunk: Chunk) -> str:
    """Build a safe LLM prompt for label refinement.

    Does NOT include full code blocks — only a short content excerpt.
    """
    from rag_ingestion.config import CHUNK_LABEL_LLM_MAX_CONTENT_CHARS  # noqa: PLC0415
    from rag_ingestion.label_constants import LABEL_REGISTRY  # noqa: PLC0415

    labels = list(getattr(chunk, "labels", None) or [])
    allowed_ids = sorted(LABEL_REGISTRY.keys())
    allowed_block = "\n".join(f"  {lid}" for lid in allowed_ids)

    excerpt = (
        getattr(chunk, "content_excerpt", "")
        or (getattr(chunk, "content", "") or "")[:CHUNK_LABEL_LLM_MAX_CONTENT_CHARS]
        or ""
    )
    if len(excerpt) > CHUNK_LABEL_LLM_MAX_CONTENT_CHARS:
        excerpt = excerpt[:CHUNK_LABEL_LLM_MAX_CONTENT_CHARS] + "... [truncated]"

    parts = [
        f"File: {getattr(chunk, 'relative_path', '') or ''}",
        f"Type: {getattr(chunk, 'chunk_type', '') or ''}",
        f"File type: {getattr(chunk, 'file_type', '') or ''}",
        f"Symbol: {getattr(chunk, 'symbol_name', '') or getattr(chunk, 'qualified_symbol', '') or ''}",
        f"Summary: {getattr(chunk, 'summary', '') or ''}",
        f"Description: {getattr(chunk, 'description', '') or ''}",
        f"Existing labels: {', '.join(labels) if labels else '(none)'}",
    ]
    if excerpt:
        parts.append(f"Content excerpt:\n{excerpt}")

    parts += [
        "",
        "Allowed label IDs:",
        allowed_block,
        "",
        "Task: Suggest additional labels for this chunk from the allowed list above.",
        "Rules:",
        "- Choose ONLY from the allowed label IDs listed.",
        "- Do NOT invent new labels.",
        "- Do NOT remove existing labels.",
        "- Only suggest labels that are strongly supported by the chunk content.",
        "- Return an empty list if no additional labels are needed.",
        "- Do NOT include explanations or prose.",
        "",
        'Return ONLY a JSON object: {"labels": ["label:id", ...]}',
    ]
    return "\n".join(parts)


def parse_llm_label_response(raw_response: str) -> list[str]:
    """Parse and validate a raw LLM label response string.

    Accepts JSON optionally wrapped in markdown code fences.
    Returns only registry-known, deduplicated labels.
    Never raises.
    """
    from rag_ingestion.label_constants import LABEL_REGISTRY  # noqa: PLC0415

    try:
        text = raw_response.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            ).strip()

        data = json.loads(text)
        if not isinstance(data, dict):
            return []

        raw_labels = data.get("labels", [])
        if not isinstance(raw_labels, list):
            return []

        seen: set[str] = set()
        result: list[str] = []
        for item in raw_labels:
            if not isinstance(item, str):
                continue
            label = item.strip()
            if label not in LABEL_REGISTRY:
                continue
            if label in seen:
                continue
            seen.add(label)
            result.append(label)
            if len(result) >= MAX_TOTAL_LABELS:
                break

        return result
    except Exception:
        return []


def refine_labels_with_llm(
    chunk: Chunk,
    *,
    provider_config: dict | None = None,
) -> list[str]:
    """Call the LLM to suggest additional labels for a chunk.

    Returns a list of registry-valid label strings.
    Returns [] on any failure — never raises.
    """
    from rag_ingestion.config import CHUNK_LABEL_LLM_TIMEOUT_SECONDS  # noqa: PLC0415
    from retrieval.config import LOCAL_LLM_BASE_URL  # noqa: PLC0415

    try:
        prompt = build_label_refinement_prompt(chunk)

        # Resolve provider: reuse description-stage conventions.
        pc = provider_config or {}
        provider = (pc.get("provider") or "local").strip().lower()

        if provider != "local":
            # Only local Ollama is supported for indexing-time LLM calls.
            _refinement_logger.debug(
                "LLM label refinement: non-local provider '%s' not supported during indexing; skipping",
                provider,
            )
            return []

        from rag_ingestion.config import (
            CODESEEK_LABEL_MODEL,
            CODESEEK_OLLAMA_KEEP_ALIVE,
            CODESEEK_LABEL_NUM_CTX,
            CODESEEK_LABEL_MAX_TOKENS,
        )
        model = CODESEEK_LABEL_MODEL
        base_url = (pc.get("base_url") or LOCAL_LLM_BASE_URL or "").rstrip("/")
        if base_url.endswith("/chat/completions"):
            base_url = base_url.rsplit("/chat/completions", 1)[0]
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        url = f"{base_url}/chat/completions"
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a code chunk classifier. "
                    "Return only valid JSON with a 'labels' key. "
                    "Do not include explanations."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": 0.0,
            "max_tokens": CODESEEK_LABEL_MAX_TOKENS,
            "keep_alive": CODESEEK_OLLAMA_KEEP_ALIVE,
            "options": {
                "num_ctx": CODESEEK_LABEL_NUM_CTX,
                "num_predict": CODESEEK_LABEL_MAX_TOKENS,
                "temperature": 0.0,
            },
        }

        response = _httpx_mod.post(url, json=payload, timeout=CHUNK_LABEL_LLM_TIMEOUT_SECONDS)
        response.raise_for_status()
        raw = (
            (response.json().get("choices") or [{}])[0]
            .get("message", {})
            .get("content", "")
            or ""
        ).strip()

        return parse_llm_label_response(raw)

    except Exception as exc:
        _refinement_logger.warning(
            "LLM label refinement failed for '%s': %s",
            getattr(chunk, "relative_path", "?"),
            exc,
        )
        return []


def merge_llm_labels(
    deterministic_labels: list[str],
    llm_labels: list[str],
) -> list[str]:
    """Merge LLM-suggested labels into the deterministic set (additive only).

    Rules:
    - Deterministic labels are always preserved.
    - LLM labels are appended only if they are registry-valid and not duplicates.
    - Category caps (MAX_LABELS_PER_CATEGORY) are enforced.
    - Total cap (MAX_TOTAL_LABELS) is enforced, deterministic labels take priority.
    - Result is sorted alphabetically (consistent with select_top_labels).
    Never returns an empty list if deterministic_labels was non-empty.
    """
    # (LABEL_REGISTRY imported at module level via label_constants)
    from rag_ingestion.label_constants import LABEL_REGISTRY  # noqa: PLC0415

    # Start from the deterministic set (already registry-valid and capped).
    result = list(deterministic_labels)
    existing = set(result)

    # Count labels per category from existing set.
    category_count: dict[str, int] = {}
    for label in result:
        cat = label.split(":", 1)[0]
        category_count[cat] = category_count.get(cat, 0) + 1

    for label in llm_labels:
        if label not in LABEL_REGISTRY:
            continue
        if label in existing:
            continue
        if len(result) >= MAX_TOTAL_LABELS:
            break
        cat = label.split(":", 1)[0]
        cap = MAX_LABELS_PER_CATEGORY.get(cat, 999)
        if category_count.get(cat, 0) >= cap:
            continue
        result.append(label)
        existing.add(label)
        category_count[cat] = category_count.get(cat, 0) + 1

    result.sort()
    return result


def refine_chunk_labels_with_llm(
    chunks: list[Chunk],
    *,
    provider_config: dict | None = None,
    event_callback=None,
) -> list[Chunk]:
    """Run optional LLM label refinement over selected chunks.

    If ENABLE_LLM_LABEL_REFINEMENT is False, returns chunks unchanged.
    Failures on individual chunks are caught and logged; the pipeline continues.
    """
    if not ENABLE_LLM_LABEL_REFINEMENT:
        return chunks

    def _emit(stage: str, message: str, **kwargs):
        if event_callback:
            try:
                event_callback(stage=stage, message=message, **kwargs)
            except Exception:
                pass

    selected = select_chunks_for_refinement(chunks)
    total = len(selected)

    if total == 0:
        _refinement_logger.info("LLM label refinement: no eligible chunks selected")
        return chunks

    _refinement_logger.info(
        "LLM label refinement: selected %d/%d chunks", total, len(chunks)
    )
    _emit("label_refinement_started", f"LLM label refinement: processing {total} chunks.")

    from rag_ingestion.config import (
        CODESEEK_LABEL_REFINE_BATCH_SIZE,
        CODESEEK_OLLAMA_STOP_MODEL_EVERY,
        CODESEEK_LABEL_MODEL,
    )
    from rag_ingestion.utils.gpu_cleanup import cleanup_after_batch, ollama_stop_model

    batch_size = CODESEEK_LABEL_REFINE_BATCH_SIZE
    if batch_size < 1:
        batch_size = 1

    batches = [selected[i : i + batch_size] for i in range(0, len(selected), batch_size)]

    refined_count = 0
    processed_count = 0
    batch_count = 0

    for batch in batches:
        for chunk in batch:
            processed_count += 1
            try:
                llm_labels = refine_labels_with_llm(chunk, provider_config=provider_config)
                if llm_labels:
                    before = list(chunk.labels or [])
                    chunk.labels = merge_llm_labels(before, llm_labels)
                    added = sorted(set(chunk.labels) - set(before))
                    if added:
                        _refinement_logger.debug(
                            "Refined '%s': added %s",
                            getattr(chunk, "relative_path", "?"),
                            added,
                        )
                        refined_count += 1
            except Exception as exc:
                _refinement_logger.warning(
                    "LLM label refinement error for chunk '%s': %s",
                    getattr(chunk, "relative_path", "?"),
                    exc,
                )

            _emit(
                "label_refinement_progress",
                f"LLM label refinement: {processed_count}/{total} chunks processed.",
                progress=processed_count,
                total=total,
            )

        # Clean up resources after each batch
        cleanup_after_batch()

        # Optional Ollama stop model every N batches
        batch_count += 1
        if (
            CODESEEK_OLLAMA_STOP_MODEL_EVERY > 0
            and batch_count % CODESEEK_OLLAMA_STOP_MODEL_EVERY == 0
        ):
            model_to_stop = CODESEEK_LABEL_MODEL
            base_url = (provider_config or {}).get("base_url") or "http://localhost:11434"
            ollama_stop_model(model_to_stop, base_url)

    _refinement_logger.info(
        "LLM label refinement: %d/%d chunks had labels added", refined_count, total
    )
    _emit(
        "label_refinement_completed",
        f"LLM label refinement complete: {refined_count}/{total} chunks improved.",
        progress=total,
        total=total,
    )
    return chunks
