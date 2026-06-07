"""Provider readiness validation for optional LLM features (e.g. chunk descriptions).

This module is intentionally independent of the ingestion pipeline.
It only performs validation — never starts model loading on its own for remote providers.
For local/Ollama providers it does a lightweight availability probe.
"""

from __future__ import annotations

import httpx

from retrieval.config import (
    LOCAL_LLM_BASE_URL,
    LOCAL_LLM_TIMEOUT_SECONDS,
)
from retrieval.provider_store import get_active_provider_credential


class ProviderNotConfiguredError(RuntimeError):
    """Raised when no usable LLM provider is configured for the requesting user."""


class ProviderNotReadyError(RuntimeError):
    """Raised when the provider is configured but not reachable / not loaded."""


def _is_auto_model(model: str | None) -> bool:
    return (model or "").strip().lower() in {"", "auto", "default"}


def _ollama_api_root() -> str:
    base = LOCAL_LLM_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        return base[:-3]
    return base


def _check_ollama_available() -> None:
    """Probe Ollama's /api/ps endpoint. Raises ProviderNotReadyError on failure."""
    url = f"{_ollama_api_root()}/api/ps"
    try:
        response = httpx.get(url, timeout=LOCAL_LLM_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception as exc:
        raise ProviderNotReadyError(
            "Local LLM provider is selected but Ollama is not reachable. "
            "Start Ollama and make sure it is listening on the configured base URL "
            f"({LOCAL_LLM_BASE_URL})."
        ) from exc


def require_llm_ready_for_user(user_id: str) -> dict:
    """Validate that the user's active LLM provider is configured and reachable.

    Returns the decrypted provider credential dict on success.
    Raises ProviderNotConfiguredError or ProviderNotReadyError on failure.
    Never mutates global config.
    """
    credential = get_active_provider_credential(user_id)

    if not credential:
        raise ProviderNotConfiguredError(
            "No active LLM provider configured. "
            "Configure one in API Tokens before enabling LLM chunk descriptions."
        )

    provider = (credential.get("provider") or "").strip().lower()
    model = (credential.get("model") or "").strip()
    api_key = (credential.get("api_key") or "").strip()

    if not provider:
        raise ProviderNotConfiguredError(
            "Active LLM provider credential is missing the provider name."
        )

    if provider == "local":
        # Local/Ollama — API key is not required.
        _check_ollama_available()
        # If a specific (non-auto) model is pinned, verify it is loaded.
        if not _is_auto_model(model):
            from retrieval.local_llm_runtime import get_model_status
            status = get_model_status(model)
            if status.get("status") not in {"ready", "loading"}:
                raise ProviderNotReadyError(
                    f"Local model '{model}' is not loaded in Ollama. "
                    "Pull or load the model and try again."
                )
        return credential

    # Remote providers require an API key.
    if not api_key:
        raise ProviderNotConfiguredError(
            f"Provider '{provider}' is selected but no API key is configured. "
            "Add the API key in API Tokens."
        )

    return credential
