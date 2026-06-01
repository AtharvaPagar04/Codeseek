"""LLM stage for grounded answer generation."""

import os
import time

from retrieval.config import (
    GROQ_API_KEY_ENV,
    GROQ_MODEL,
    MAX_RESPONSE_TOKENS,
    RETRIEVAL_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    RETRIEVAL_CIRCUIT_BREAKER_THRESHOLD,
    RETRIEVAL_GROQ_TIMEOUT_SECONDS,
    RETRIEVAL_RETRY_ATTEMPTS,
    RETRIEVAL_RETRY_BACKOFF_SECONDS,
)

SYSTEM_PROMPT = (
    "You are a repository-grounded code assistant.\n"
    "Rules:\n"
    "1) Use only the provided CODE CONTEXT; do not use outside knowledge.\n"
    "2) Do not propose new code, refactors, or hypothetical implementations unless explicitly requested.\n"
    "3) If required evidence is missing, reply with exactly: "
    "'Insufficient context in retrieved code to answer confidently.'\n"
    "4) Be concise and technical. Prefer direct method/file traces over general explanation.\n"
    "5) Do not claim behavior that is not visible in the provided context.\n"
    "5a) Only mention files, symbols, classes, or methods that are present in the provided context blocks.\n"
    "5b) If uncertain, omit the claim instead of guessing or broadening scope.\n"
    "6) Response format:\n"
    "   - Start with a one-line direct answer.\n"
    "   - Then provide 2-5 short bullet points with concrete evidence.\n"
    "   - No markdown code blocks unless user explicitly asks for code.\n"
    "7) For absence/negative answers, never make absolute repo-wide claims. "
    "Use wording like: 'Not found in retrieved context.'"
)

_llm_failures = 0
_llm_circuit_open_until = 0.0


def generate_answer(
    raw_query: str,
    context: str,
    history_block: str,
    allowed_sources: list[dict] | None = None,
) -> str:
    """Generate a grounded answer from context using Groq."""
    prompt = _build_prompt(raw_query, context, history_block, allowed_sources or [])
    groq_key = os.getenv(GROQ_API_KEY_ENV, "")
    if groq_key:
        return _groq_answer(prompt, groq_key)

    return "No GROQ_API_KEY found in environment. Set it in .env and rerun."


def _build_prompt(
    raw_query: str,
    context: str,
    history_block: str,
    allowed_sources: list[dict],
) -> str:
    parts = []
    if history_block:
        parts.append(history_block)
    if allowed_sources:
        parts.append("--- ALLOWED SOURCES (STRICT) ---")
        for src in allowed_sources:
            parts.append(
                f"{src.get('relative_path','')} :: {src.get('symbol_name','')} "
                f"(lines {src.get('start_line',0)}-{src.get('end_line',0)})"
            )
        parts.append("--- END ALLOWED SOURCES ---")
        parts.append(
            "You must only reference files/symbols from ALLOWED SOURCES. "
            "If other code appears in context, ignore it."
        )
    parts.append("--- CODE CONTEXT ---")
    parts.append(context)
    parts.append("--- END CODE CONTEXT ---")
    parts.append(f"Question: {raw_query}")
    return "\n\n".join(parts)


def _groq_answer(prompt: str, api_key: str) -> str:
    global _llm_failures, _llm_circuit_open_until
    now = time.time()
    if _llm_circuit_open_until > now:
        remaining = int(_llm_circuit_open_until - now)
        return f"LLM temporarily unavailable (circuit open for {remaining}s)."

    from groq import Groq

    client = Groq(api_key=api_key, timeout=RETRIEVAL_GROQ_TIMEOUT_SECONDS)
    last_exc: Exception | None = None
    for attempt in range(1, RETRIEVAL_RETRY_ATTEMPTS + 1):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=MAX_RESPONSE_TOKENS,
                temperature=0.1,
            )
            _llm_failures = 0
            return (response.choices[0].message.content or "").strip() or "No response text returned from model."
        except Exception as exc:  # pragma: no cover
            last_exc = exc
            if attempt < RETRIEVAL_RETRY_ATTEMPTS:
                time.sleep(RETRIEVAL_RETRY_BACKOFF_SECONDS * attempt)

    _llm_failures += 1
    if _llm_failures >= RETRIEVAL_CIRCUIT_BREAKER_THRESHOLD:
        _llm_circuit_open_until = time.time() + RETRIEVAL_CIRCUIT_BREAKER_COOLDOWN_SECONDS
    return f"LLM call failed after retries: {last_exc}"
