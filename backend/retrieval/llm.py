"""LLM stage for grounded answer generation."""

import os
import time
from typing import Any

import httpx

from retrieval.code_answers import (
    is_code_request,
    is_explanation_request,
    is_overview_request,
)
from retrieval.config import (
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
    "2) Do not propose new code, refactors, or hypothetical implementations "
    "unless the user explicitly asks for them.\n"
    "3) If required evidence is missing, reply with exactly: "
    "'Insufficient context in retrieved code to answer confidently.'\n"
    "4) Be concise and technical, but complete enough to answer the user's actual question. "
    "Prefer direct method/file/class traces over vague summaries.\n"
    "5) Grounding discipline:\n"
    "   5a) Only mention files, symbols, classes, or methods that are present in the provided context blocks.\n"
    "   5b) If uncertain, omit the claim instead of guessing or broadening scope.\n"
    "   5c) Do not claim behavior that is not visible in the provided context.\n"
    "6) Code and snippet rules:\n"
    "   - Always use inline references (e.g. `ClassName.method_name`) when naming symbols from context.\n"
    "   - Include a short fenced code block ONLY when the user explicitly asks to \"show\", \"provide\", "
    "\"give\", or \"write\" code — or when a snippet is the clearest way to answer a how/where question.\n"
    "   - When showing a fenced block, use exactly 1-2 blocks maximum; identify the file and symbol above each.\n"
    "   - Never reproduce entire files or large function bodies verbatim; excerpt only the essential lines.\n"
    "7) Response format (default path):\n"
    "   - Start with a one-line direct answer.\n"
    "   - Then provide 3-6 short bullet points with concrete evidence from the context.\n"
    "   - For overview, explanation, or trace questions, explain how behavior is assembled across "
    "files rather than repeating symbol names without connecting them.\n"
    "   - For deep-dive / symbol-level questions, give a technical walk-through: purpose, inputs, "
    "key logic steps, return values, and side effects — referencing exact files and line ranges.\n"
    "8) For absence/negative answers, never make absolute repo-wide claims. "
    "Use wording like: 'Not found in the retrieved context.' and suggest a more specific search term."
)

OPENAI_MODEL = os.getenv("RETRIEVAL_OPENAI_MODEL", "gpt-4o-mini")
OPENROUTER_MODEL = os.getenv("RETRIEVAL_OPENROUTER_MODEL", "openai/gpt-4o-mini")
GEMINI_MODEL = os.getenv("RETRIEVAL_GEMINI_MODEL", "gemini-1.5-flash")

_llm_failures = 0
_llm_circuit_open_until = 0.0


class LlmProviderError(Exception):
    """Structured upstream-provider failure surfaced to the API layer."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = detail


def generate_answer(
    raw_query: str,
    context: str,
    history_block: str,
    allowed_sources: list[dict] | None = None,
    extra_context_blocks: list[str] | None = None,
    provider_config: dict[str, Any] | None = None,
) -> str:
    """Generate a grounded answer from context using a selected provider."""
    prompt = _build_prompt(
        raw_query,
        context,
        history_block,
        allowed_sources or [],
        extra_context_blocks=extra_context_blocks or [],
    )
    resolved = _resolve_provider_config(provider_config)
    if resolved:
        return _provider_answer(
            prompt,
            provider=resolved["provider"],
            api_key=resolved["api_key"],
            model=resolved["model"],
        )
    return "No LLM provider API key configured. Add one in the frontend API config and make it active."


def _build_prompt(
    raw_query: str,
    context: str,
    history_block: str,
    allowed_sources: list[dict],
    extra_context_blocks: list[str] | None = None,
) -> str:
    parts = []
    if history_block:
        parts.append(history_block)
    if is_code_request(raw_query):
        parts.append("--- RESPONSE MODE: CODE REQUEST ---")
        parts.append(
            "The user explicitly asked for code. "
            "Return the smallest complete snippet that directly answers the question, "
            "using only the CODE CONTEXT provided. "
            "Include 1-2 fenced code blocks at most; identify the file and symbol name above each block. "
            "Do not paraphrase or reconstruct code when an exact verbatim excerpt is available. "
            "Do not add explanatory prose beyond a one-line context sentence per block unless asked."
        )
    elif is_overview_request(raw_query):
        parts.append("--- RESPONSE MODE: OVERVIEW ---")
        parts.append(
            "The user wants a grounded project overview. "
            "Explain what the project does, which modules or entry points drive core behavior, "
            "the main tech stack or runtime shape visible in context, and any important "
            "backing data, config, or service dependencies. "
            "When arrays or objects list concrete technologies, categories, or entities, "
            "name the most important ones explicitly rather than describing them abstractly. "
            "Format: one short overview paragraph followed by 4-6 evidence-backed bullet points. "
            "Do not include fenced code blocks unless the user asked for code."
        )
    elif is_explanation_request(raw_query):
        parts.append("--- RESPONSE MODE: EXPLANATION ---")
        parts.append(
            "The user asked for an explanation, not a raw code dump. "
            "Explain the structure and behavior of the referenced code: what it does, "
            "what it reads and writes, how it transforms or routes data, and any key "
            "design decisions visible in the context. "
            "For UI code: explain what is rendered, where data comes from, how loops or maps "
            "produce output, and any interaction handlers. "
            "For backend code: explain the request/response flow, key function calls, "
            "data transformations, and any external dependencies (DB, cache, APIs). "
            "Reference concrete files and symbols from the allowed sources. "
            "If backing data arrays or objects are present, name important entries or fields "
            "rather than referring to them generically. "
            "Format: one short summary sentence followed by 4-7 focused bullet points. "
            "Include an inline code reference (e.g. `module.function`) for each bullet point."
        )
    else:
        # TRACE / DEPENDENCY / SYMBOL / deep-dive fallthrough
        parts.append("--- RESPONSE MODE: TECHNICAL TRACE ---")
        parts.append(
            "Answer with a grounded technical walk-through. "
            "For each step: name the exact file and symbol, state what it does, what it "
            "reads/writes/calls, and how it connects to the next step. "
            "Include inputs, return values, and any notable side effects or error handling "
            "visible in the context. "
            "Keep the answer concrete and implementation-based — avoid generic descriptions "
            "that could apply to any codebase. "
            "Format: one-line answer, then 4-8 numbered steps or bullet points. "
            "Use inline references (e.g. `file.py :: ClassName.method`) rather than fenced "
            "code blocks unless the user explicitly asked for code."
        )
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
    for block in extra_context_blocks or []:
        parts.append(block)
    parts.append("--- END CODE CONTEXT ---")
    parts.append(f"Question: {raw_query}")
    return "\n\n".join(parts)


def _resolve_provider_config(provider_config: dict[str, Any] | None) -> dict[str, str] | None:
    if provider_config:
        provider = str(provider_config.get("provider", "")).strip().lower()
        api_key = str(provider_config.get("api_key", "")).strip()
        model = str(provider_config.get("model", "")).strip()
        if provider and api_key:
            if provider not in {"groq", "openai", "openrouter", "gemini"}:
                return {
                    "provider": "unsupported",
                    "api_key": api_key,
                    "model": provider,
                }
            return {
                "provider": provider,
                "api_key": api_key,
                "model": model or _default_model(provider),
            }
    return None


def _default_model(provider: str) -> str:
    if provider == "groq":
        return GROQ_MODEL
    if provider == "openai":
        return OPENAI_MODEL
    if provider == "openrouter":
        return OPENROUTER_MODEL
    if provider == "gemini":
        return GEMINI_MODEL
    return ""


def _provider_answer(prompt: str, provider: str, api_key: str, model: str) -> str:
    global _llm_failures, _llm_circuit_open_until
    now = time.time()
    if _llm_circuit_open_until > now:
        remaining = int(_llm_circuit_open_until - now)
        raise LlmProviderError(
            503,
            f"LLM provider temporarily unavailable. Retry after {remaining}s.",
        )

    last_exc: Exception | None = None
    if provider == "unsupported":
        raise LlmProviderError(
            400,
            f"Unsupported LLM provider configuration: {model}",
        )
    for attempt in range(1, RETRIEVAL_RETRY_ATTEMPTS + 1):
        try:
            response = _chat_completion_request(
                provider=provider,
                api_key=api_key,
                model=model,
                prompt=prompt,
            )
            _llm_failures = 0
            content = _extract_message_content(response)
            return content or "No response text returned from model."
        except Exception as exc:  # pragma: no cover
            last_exc = exc
            if attempt < RETRIEVAL_RETRY_ATTEMPTS:
                time.sleep(RETRIEVAL_RETRY_BACKOFF_SECONDS * attempt)

    _llm_failures += 1
    if _llm_failures >= RETRIEVAL_CIRCUIT_BREAKER_THRESHOLD:
        _llm_circuit_open_until = time.time() + RETRIEVAL_CIRCUIT_BREAKER_COOLDOWN_SECONDS
    raise _classify_provider_error(last_exc)


def _chat_completion_request(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
) -> dict[str, Any]:
    url, headers = _provider_endpoint(provider, api_key)
    response = httpx.post(
        url,
        headers=headers,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": MAX_RESPONSE_TOKENS,
            "temperature": 0.1,
        },
        timeout=RETRIEVAL_GROQ_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _provider_endpoint(provider: str, api_key: str) -> tuple[str, dict[str, str]]:
    if provider == "groq":
        return (
            "https://api.groq.com/openai/v1/chat/completions",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
    if provider == "openai":
        return (
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
    if provider == "openrouter":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        site_url = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
        app_name = os.getenv("OPENROUTER_APP_NAME", "Codeseek").strip()
        if site_url:
            headers["HTTP-Referer"] = site_url
        if app_name:
            headers["X-Title"] = app_name
        return ("https://openrouter.ai/api/v1/chat/completions", headers)
    if provider == "gemini":
        return (
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
    raise ValueError(f"Unsupported provider: {provider}")


def _classify_provider_error(exc: Exception | None) -> LlmProviderError:
    if isinstance(exc, LlmProviderError):
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return LlmProviderError(
                429,
                "Provider rate limit reached. Wait and retry, or switch provider credentials.",
            )
        if status in {401, 403}:
            return LlmProviderError(
                400,
                "Provider API key rejected or lacks permission.",
            )
        if 400 <= status < 500:
            return LlmProviderError(
                400,
                f"Provider request rejected ({status}). Check provider, model, and key configuration.",
            )
        return LlmProviderError(
            502,
            f"Provider request failed upstream ({status}).",
        )
    if isinstance(exc, httpx.TimeoutException):
        return LlmProviderError(
            504,
            "Provider request timed out. Retry or choose a faster model.",
        )
    if exc is None:
        return LlmProviderError(502, "Provider request failed after retries.")
    return LlmProviderError(
        502,
        f"Provider request failed after retries: {type(exc).__name__}.",
    )


def _extract_message_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
    return ""
