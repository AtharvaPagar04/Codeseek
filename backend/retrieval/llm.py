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
    LOCAL_LLM_BASE_URL,
    LOCAL_LLM_COMPLEX_MODEL,
    LOCAL_LLM_PRIMARY_MODEL,
    LOCAL_LLM_TIMEOUT_SECONDS,
    QUERY_MAX_TOKENS,
    QUERY_NUM_CTX,
    QUERY_OLLAMA_KEEP_ALIVE,
    MAX_RESPONSE_TOKENS,
    RETRIEVAL_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    RETRIEVAL_CIRCUIT_BREAKER_THRESHOLD,
    RETRIEVAL_GROQ_TIMEOUT_SECONDS,
    RETRIEVAL_RETRY_ATTEMPTS,
    RETRIEVAL_RETRY_BACKOFF_SECONDS,
)
from retrieval.local_llm_runtime import (
    background_prime_primary_model,
    get_provider_runtime_state,
    wait_for_model_ready,
)

SYSTEM_PROMPT = (
    "You are a repository-grounded code assistant.\n"
    "Core rules:\n"
    "1. Use only the provided CODE CONTEXT and ALLOWED SOURCES. Do not invent files, functions, classes, endpoints, routes, or behavior.\n"
    "2. Prefer implementation files over docs, tests, generated reports, scratch files, and benchmark scripts unless the user explicitly asks for them.\n"
    "3. Never expose retrieval internals to the user. Do not expose retrieval internals such as internal payload metadata, scoring fields, injected candidates, reranker boosts, routing/debug details, source weights, or hidden retrieval heuristics.\n"
    "   Do not remove, rename, sanitize, or alter legitimate source-code identifiers inside code blocks. Preserve source-code identifiers such as payload, score, rank, metadata, source, or context exactly as written in the source file.\n"
    "   Avoid phrases like:\n"
    "   * direct injected candidate\n"
    "   * direct injected file candidate\n"
    "   * reranker boost\n"
    "   * source role classifier\n"
    "   * exact retrieval hit\n"
    "   * internal score\n"
    "4. If evidence is weak or incomplete, say so clearly (e.g., reply with 'I could not find strong evidence...').\n"
    "5. If the answer mentions a file, that file must appear in the provided ALLOWED SOURCES.\n"
    "6. If the answer mentions a function/symbol, it must appear in the provided source metadata or code excerpt.\n"
    "7. Keep answers concise by default. Add detail only when the user asks 'how', 'explain', 'flow', 'architecture', or 'why'."
)

OPENAI_MODEL = os.getenv("RETRIEVAL_OPENAI_MODEL", "gpt-4o-mini")
OPENROUTER_MODEL = os.getenv("RETRIEVAL_OPENROUTER_MODEL", "openai/gpt-4o-mini")
GEMINI_MODEL = os.getenv("RETRIEVAL_GEMINI_MODEL", "gemini-1.5-flash")
AICREDITS_MODEL = os.getenv("RETRIEVAL_AICREDITS_MODEL", "gpt-5.4-mini")
AICREDITS_BASE_URL = os.getenv("AICREDITS_BASE_URL", "https://api.aicredits.io/v1")

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
    query_info: dict[str, Any] | None = None,
    evidence_confidence: dict[str, Any] | str | None = None,
    selection_meta: dict[str, Any] | None = None,
) -> str:
    """Generate a grounded answer from context using a selected provider."""
    # Resolve the expected response mode
    response_mode = "technical_trace"
    if query_info:
        intent = str(query_info.get("primary_intent") or query_info.get("intent") or "").upper()
        if intent == "SYMBOL":
            response_mode = "source_location"
        elif intent in ("FLOW", "TRACE") or is_explanation_request(raw_query):
            response_mode = "flow_summary"
        elif intent == "OVERVIEW" or is_overview_request(raw_query):
            response_mode = "overview_summary"
        elif intent == "CODE_REQUEST":
            response_mode = "code_snippet"
        elif intent == "CONFIG":
            response_mode = "source_location"
            
    if evidence_confidence:
        if isinstance(evidence_confidence, dict):
            level = str(evidence_confidence.get("level", "")).lower()
        else:
            level = str(evidence_confidence).lower()
        if level == "weak" and response_mode not in ("flow_summary", "overview_summary", "code_snippet"):
            response_mode = "low_context"

    prompt = _build_prompt(
        raw_query,
        context,
        history_block,
        allowed_sources or [],
        extra_context_blocks=extra_context_blocks or [],
        response_mode=response_mode,
    )
    resolved = _resolve_provider_config(
        provider_config,
        raw_query=raw_query,
        query_info=query_info or {},
        evidence_confidence=evidence_confidence,
    )
    if selection_meta is not None and resolved:
        runtime_state = get_provider_runtime_state(resolved["provider"], resolved["model"])
        selection_meta.update(
            {
                "provider": resolved["provider"],
                "model": resolved["model"],
                "routing_mode": resolved.get("routing_mode", ""),
                "timeout_seconds": resolved.get("timeout_seconds", 0.0),
                "runtime_status": runtime_state.get("status", ""),
                "runtime_detail": runtime_state.get("detail", ""),
            }
        )
    if resolved:
        if resolved["provider"] == "local":
            try:
                if resolved["model"] == LOCAL_LLM_COMPLEX_MODEL:
                    wait_for_model_ready(
                        resolved["model"],
                        timeout_seconds=resolved["timeout_seconds"],
                        reason="query_requires_complex_model",
                    )
                else:
                    background_prime_primary_model()
            except TimeoutError as exc:
                raise LlmProviderError(503, str(exc)) from exc
            except RuntimeError as exc:
                raise LlmProviderError(502, str(exc)) from exc
        answer = _provider_answer(
            prompt,
            provider=resolved["provider"],
            api_key=resolved["api_key"],
            model=resolved["model"],
            timeout_seconds=resolved["timeout_seconds"],
            base_url=resolved.get("base_url", ""),
            max_tokens=QUERY_MAX_TOKENS if resolved["provider"] == "local" else None,
        )
        if (
            resolved["provider"] == "local"
            and resolved.get("routing_mode", "").startswith("auto")
            and resolved["model"] == LOCAL_LLM_PRIMARY_MODEL
            and _should_escalate_local_answer(answer)
        ):
            try:
                wait_for_model_ready(
                    LOCAL_LLM_COMPLEX_MODEL,
                    timeout_seconds=resolved["timeout_seconds"],
                    reason="auto_escalation_required",
                )
            except TimeoutError as exc:
                raise LlmProviderError(503, str(exc)) from exc
            except RuntimeError as exc:
                raise LlmProviderError(502, str(exc)) from exc
            fallback_answer = _provider_answer(
                prompt,
                provider=resolved["provider"],
                api_key=resolved["api_key"],
                model=LOCAL_LLM_COMPLEX_MODEL,
                timeout_seconds=resolved["timeout_seconds"],
                base_url=resolved.get("base_url", ""),
                max_tokens=QUERY_MAX_TOKENS,
            )
            if selection_meta is not None:
                selection_meta.update(
                    {
                        "escalated": True,
                        "initial_model": LOCAL_LLM_PRIMARY_MODEL,
                        "model": LOCAL_LLM_COMPLEX_MODEL,
                        "fallback_reason": "insufficient_first_pass",
                    }
                )
            return fallback_answer
        return answer
    return "No LLM provider API key configured. Add one in the frontend API config and make it active."


def _build_prompt(
    raw_query: str,
    context: str,
    history_block: str,
    allowed_sources: list[dict],
    extra_context_blocks: list[str] | None = None,
    response_mode: str = "technical_trace",
) -> str:
    parts = []
    if response_mode == "code_snippet" or is_code_request(raw_query):
        header = "CODE REQUEST"
    elif response_mode == "overview_summary" or response_mode == "overview" or is_overview_request(raw_query):
        header = "OVERVIEW"
    elif response_mode == "explanation" or is_explanation_request(raw_query):
        header = "EXPLANATION"
    elif response_mode == "source_location":
        header = "SOURCE_LOCATION"
    elif response_mode == "flow_summary":
        header = "FLOW_SUMMARY"
    elif response_mode == "low_context":
        header = "LOW_CONTEXT"
    else:
        header = "TECHNICAL_TRACE"

    parts.append(f"--- RESPONSE MODE: {header} ---")
    
    if header == "CODE REQUEST":
        parts.append(
            "Response mode: code_snippet\n\n"
            "The user explicitly asked for code.\n"
            "You must return actual code snippets from the provided sources.\n\n"
            "Rules:\n"
            "1. Use only provided source text.\n"
            "2. Preserve code exactly.\n"
            "3. Do not invent code.\n"
            "4. Do not rename or remove identifiers.\n"
            "5. Do not sanitize source-code words that look like retrieval terms.\n"
            "6. If the current query names an exact symbol, return that symbol only unless the user asks for related functions.\n"
            "7. If the current query names a feature/topic, use only sources matching that current feature/topic.\n"
            "8. Do not include code from previous-turn topics unless the current query is a vague follow-up.\n"
            "9. Do not summarize before showing code.\n"
            "10. Start with the most relevant file/function.\n"
            "11. Use fenced code blocks with the correct language.\n"
            "12. Include only a short note before/after code if needed.\n"
            "13. Do not include flow summaries unless the user explicitly asks for explanation.\n"
            "14. Every file/function mentioned must exist in the selected sources.\n"
            "15. If the code body is not available, clearly say it was not included in the retrieved context."
        )
    elif header == "SOURCE_LOCATION":
        parts.append(
            "You MUST follow this exact format for the answer:\n\n"
            "The implementation is in:\n\n"
            "* `{primary_file}`\n"
            "  * symbol/function: `{symbol_if_available}`\n"
            "  * why: {short user-facing reason}\n\n"
            "Related sources:\n"
            "* `{related_file}`\n\n"
            "Rules:\n"
            "- The primary file must be the best implementation source, not a docs/test/report/scratch file.\n"
            "- Prefer executable implementation files over docs/tests when implementation sources are available.\n"
            "- Docs/tests may be related sources only when the user explicitly asks for docs/tests or no implementation file is available.\n"
            "- Do not include 'Related sources' if there are none.\n"
            "- Do not mention internal routing, injection, ranking, or scoring.\n"
            "- If the exact implementation is uncertain, start with:\n"
            "  'I found partial evidence. The likely implementation is in:'"
        )
    elif header == "FLOW_SUMMARY":
        parts.append(
            "You MUST follow this exact format for the answer:\n\n"
            "The flow appears to be:\n\n"
            "1. {role name}\n"
            "   * file: `{file_path}`{optional_symbol}\n"
            "   * role: {what this file/symbol does in the flow}\n\n"
            "2. {role name}\n"
            "   * file: `{file_path}`{optional_symbol}\n"
            "   * role: {what this file/symbol does in the flow}\n\n"
            "Evidence status:\n"
            "* complete\n\n"
            "OR:\n\n"
            "Evidence status:\n"
            "* partial\n"
            "* missing: {missing_role_1}, {missing_role_2}\n\n"
            "Rules:\n"
            "- Do not mark evidence as complete if any required role is missing.\n"
            "- Do not list a role as missing if a displayed source already covers that role.\n"
            "- Prefer implementation sources.\n"
            "- Hide docs/tests unless the user explicitly asks for tests/docs."
        )
    elif header == "OVERVIEW":
        parts.append(
            "The user wants a grounded project overview.\n"
            "You MUST follow this exact format for the answer:\n\n"
            "{repo_or_feature_name} is {one-sentence purpose}.\n\n"
            "At a high level:\n\n"
            "1. {main capability 1}\n"
            "2. {main capability 2}\n"
            "3. {main capability 3}\n\n"
            "Key areas from the retrieved sources:\n\n"
            "* `{file_path}`: {short role}\n\n"
            "Rules:\n"
            "- Explain the system, not random helper functions.\n"
            "- Prefer architecture, API, ingestion, retrieval, frontend, and config entrypoints.\n"
            "- Avoid dumping source paths without explanation."
        )
    elif header == "LOW_CONTEXT":
        parts.append(
            "You MUST follow this exact format for the answer:\n\n"
            "I could not find strong evidence for that in the indexed repository context.\n\n"
            "Try asking with:\n"
            "* a file name\n"
            "* a function name\n"
            "* a feature name\n\n"
            "If partial evidence exists, include:\n"
            "Possible related sources:\n"
            "* `{file_path}`: {why it might be related}"
        )
    elif header == "EXPLANATION":
        parts.append(
            "The user asked for an explanation, not a raw code dump. "
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
    else:
        # TECHNICAL TRACE
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

    parts.append("--- CURRENT USER QUESTION ---")
    parts.append(raw_query)
    parts.append(
        "The CURRENT USER QUESTION is the source of truth for this answer.\n"
        "Conversation history is only for resolving vague follow-ups. If the current question explicitly names a file, function, class, symbol, endpoint, feature, or subsystem, answer using the current question and current allowed sources. Do not reuse previous-turn sources unless they directly match the current question.\n"
        "Use conversation history only when the current question is ambiguous, such as \"that\", \"it\", \"same function\", \"same file\", \"continue\", or \"explain that\"."
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
    if history_block:
        parts.append("--- CONVERSATION HISTORY (SECONDARY REFERENCE ONLY) ---")
        parts.append(history_block)
    return "\n\n".join(parts)


def _resolve_provider_config(
    provider_config: dict[str, Any] | None,
    *,
    raw_query: str,
    query_info: dict[str, Any],
    evidence_confidence: dict[str, Any] | str | None,
) -> dict[str, Any] | None:
    if provider_config:
        provider = str(provider_config.get("provider", "")).strip().lower()
        api_key = str(provider_config.get("api_key", "")).strip()
        model = str(provider_config.get("model", "")).strip()
        if provider:
            if provider not in {"groq", "openai", "openrouter", "gemini", "aicredits", "local"}:
                return {
                    "provider": "unsupported",
                    "api_key": api_key,
                    "model": provider,
                    "timeout_seconds": RETRIEVAL_GROQ_TIMEOUT_SECONDS,
                    "base_url": "",
                }
            if provider == "local":
                requested_model = model or _default_model(provider)
                chosen_model, routing_mode = _resolve_local_model(
                    raw_query=raw_query,
                    query_info=query_info,
                    evidence_confidence=evidence_confidence,
                    requested_model=requested_model,
                )
                return {
                    "provider": provider,
                    "api_key": api_key,
                    "model": chosen_model,
                    "routing_mode": routing_mode,
                    "timeout_seconds": LOCAL_LLM_TIMEOUT_SECONDS,
                    "base_url": LOCAL_LLM_BASE_URL,
                }
            if api_key:
                return {
                    "provider": provider,
                    "api_key": api_key,
                    "model": model or _default_model(provider),
                    "routing_mode": "manual" if model else "default",
                    "timeout_seconds": RETRIEVAL_GROQ_TIMEOUT_SECONDS,
                    "base_url": "",
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
    if provider == "aicredits":
        return AICREDITS_MODEL
    if provider == "local":
        return "auto"
    return ""


def _resolve_local_model(
    *,
    raw_query: str,
    query_info: dict[str, Any],
    evidence_confidence: dict[str, Any] | str | None,
    requested_model: str,
) -> tuple[str, str]:
    normalized = requested_model.strip().lower()
    if normalized in {
        "qwen2.5-coder:3b-5k",
        "qwen2.5-coder:3b-8k",
        "qwen-coder-7b-8192",
        "qwen-coder-3b",
        "qwen-coder-7b",
    }:
        return requested_model, "manual"
    if normalized not in {"", "default", "auto"}:
        return requested_model, "manual"

    score = 0
    intent = str(query_info.get("primary_intent") or query_info.get("intent") or "").upper()
    entities = query_info.get("entities") or {}
    entity_breadth = 0
    if isinstance(entities, dict):
        for value in entities.values():
            if isinstance(value, list):
                entity_breadth += len(value)
            elif isinstance(value, dict):
                entity_breadth += len(value)
    words = [token for token in raw_query.lower().split() if token.strip()]

    if intent in {"TRACE", "ARCHITECTURE", "EXPLANATION", "FOLLOWUP", "DEPENDENCY"}:
        score += 2
    elif intent in {"OVERVIEW", "SEMANTIC"} and len(words) >= 12:
        score += 1

    if entity_breadth >= 4:
        score += 1
    if entity_breadth >= 8:
        score += 1
    if len(words) >= 18:
        score += 1
    if any(marker in raw_query.lower() for marker in ("how does", "walk through", "trace", "explain", "architecture", "lifecycle")):
        score += 1

    if isinstance(evidence_confidence, dict):
        level = str(evidence_confidence.get("level", "")).lower()
    else:
        level = str(evidence_confidence or "").lower()
    if level == "weak":
        score += 2
    elif level == "partial":
        score += 1

    model = LOCAL_LLM_COMPLEX_MODEL if score >= 3 else LOCAL_LLM_PRIMARY_MODEL
    return model, f"auto(score={score})"


def _should_escalate_local_answer(answer: str) -> bool:
    normalized = answer.strip().lower()
    if not normalized:
        return True
    if "insufficient context in retrieved code to answer confidently" in normalized:
        return True
    if normalized.startswith("no response text returned from model"):
        return True
    weak_markers = ("cannot", "unable", "insufficient", "missing context", "not enough context")
    return len(normalized) < 160 and any(marker in normalized for marker in weak_markers)


def _provider_answer(
    prompt: str,
    provider: str,
    api_key: str,
    model: str,
    *,
    timeout_seconds: float,
    base_url: str = "",
    max_tokens: int | None = None,
) -> str:
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
                timeout_seconds=timeout_seconds,
                base_url=base_url,
                max_tokens=max_tokens,
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
    *,
    timeout_seconds: float,
    base_url: str = "",
    system_prompt: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    url, headers = _provider_endpoint(provider, api_key, base_url=base_url)
    sys_prompt = system_prompt if system_prompt is not None else SYSTEM_PROMPT
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    effective_max_tokens = max_tokens
    if effective_max_tokens is None:
        effective_max_tokens = QUERY_MAX_TOKENS if provider == "local" else MAX_RESPONSE_TOKENS
    payload["max_tokens"] = effective_max_tokens
    if provider == "local":
        payload["options"] = {
            "temperature": 0.1,
            "num_ctx": QUERY_NUM_CTX,
            "num_predict": effective_max_tokens,
        }
        payload["keep_alive"] = QUERY_OLLAMA_KEEP_ALIVE

    response = httpx.post(
        url,
        headers=headers,
        json=payload,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def _provider_endpoint(provider: str, api_key: str, *, base_url: str = "") -> tuple[str, dict[str, str]]:
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
    if provider == "aicredits":
        return (
            f"{AICREDITS_BASE_URL}/chat/completions",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
    if provider == "local":
        local_base = (base_url or LOCAL_LLM_BASE_URL).rstrip("/")
        if local_base.endswith("/chat/completions"):
            url = local_base
        else:
            url = f"{local_base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return (url, headers)
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
