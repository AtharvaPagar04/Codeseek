"""LLM stage for grounded answer generation."""

import os

from retrieval.config import (
    GROQ_API_KEY_ENV,
    GROQ_MODEL,
    MAX_RESPONSE_TOKENS,
)

SYSTEM_PROMPT = (
    "You are a code assistant with full context of a software repository. "
    "Answer using only the provided context. Cite file path, symbol, and lines when possible. "
    "If context is insufficient, state that clearly."
)


def generate_answer(raw_query: str, context: str, history_block: str) -> str:
    """Generate a grounded answer from context using Groq."""
    prompt = _build_prompt(raw_query, context, history_block)
    groq_key = os.getenv(GROQ_API_KEY_ENV, "")
    if groq_key:
        return _groq_answer(prompt, groq_key)

    return "No GROQ_API_KEY found in environment. Set it in .env and rerun."


def _build_prompt(raw_query: str, context: str, history_block: str) -> str:
    parts = [SYSTEM_PROMPT]
    if history_block:
        parts.append(history_block)
    parts.append("--- CODE CONTEXT ---")
    parts.append(context)
    parts.append("--- END CODE CONTEXT ---")
    parts.append(f"Question: {raw_query}")
    return "\n\n".join(parts)


def _groq_answer(prompt: str, api_key: str) -> str:
    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=MAX_RESPONSE_TOKENS,
        )
        return (response.choices[0].message.content or "").strip() or "No response text returned from model."
    except Exception as exc:  # pragma: no cover
        return f"LLM call failed: {exc}"
