"""Intent classification and entity extraction for retrieval."""

import re

from retrieval.config import ENABLE_SCORED_INTENT

DEPENDENCY_PATTERNS = [
    r"\bcalls\b",
    r"\bdepends on\b",
    r"\buses\b",
    r"\breferences\b",
    r"\bcallers of\b",
    r"\bcalled by\b",
    r"\bwho uses\b",
]

SYMBOL_HINT_PATTERNS = [
    r"\bwhere is\b",
    r"\bshow me\b",
    r"\blist\b",
    r"\bdefined\b",
]

INTENT_FAMILIES = (
    "OVERVIEW",
    "ARCHITECTURE",
    "TECH_STACK",
    "EXPLANATION",
    "SYMBOL",
    "FILE",
    "TRACE",
    "DEPENDENCY",
    "CONFIG",
    "CODE_REQUEST",
    "FOLLOWUP",
    "LOW_CONTEXT",
    "SEMANTIC",
)

SNAKE_CASE_RE = re.compile(r"\b[a-z][a-z0-9_]{2,}\b")
CAMEL_CASE_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]{2,}\b")
FILE_RE = re.compile(r"\b\S+\.(py|js|ts|tsx|jsx)\b")
CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\(\)")
ENV_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
ROUTE_RE = re.compile(r"(?<!\w)/(?:api|auth|v\d|[A-Za-z0-9_.:-]+)[A-Za-z0-9_./{}:-]*")
PACKAGE_TOKEN_RE = re.compile(r"\b@?[A-Za-z0-9][A-Za-z0-9_.@/-]*(?:[-/.][A-Za-z0-9][A-Za-z0-9_.@/-]*)+\b")
HYPHENATED_API_TERM_RE = re.compile(r"\b[a-z][a-z0-9]+(?:-[a-z0-9]+)+\b")

KNOWN_DEPENDENCY_TERMS = {
    "fastapi",
    "uvicorn",
    "qdrant",
    "qdrant-client",
    "sentence-transformers",
    "pytest",
    "httpx",
    "pydantic",
    "postgres",
    "postgresql",
    "react",
    "vite",
    "typescript",
    "tailwind",
    "groq",
    "openai",
    "gemini",
}

STOPWORDS = {
    "where",
    "what",
    "which",
    "when",
    "does",
    "from",
    "with",
    "this",
    "that",
    "there",
    "implemented",
    "function",
    "class",
    "tests",
    "test",
    "call",
    "calls",
    "trace",
    "exact",
    "show",
    "find",
    "list",
}

FLOW_SYMBOLS = {
    "orchestration": ["_query_impl", "run_query"],
    "auth_session": [
        "auth_github",
        "auth_github_callback",
        "auth_github_token",
        "auth_logout",
        "create_auth_session",
        "delete_auth_session",
        "get_user_for_session_token",
        "_current_auth_user",
        "_require_auth_user",
    ],
    "indexing_session": ["create_session", "_index_job", "run_pipeline"],
    "deployment_config": [],
    "provider_credentials": [
        "list_provider_credentials_v1",
        "create_provider_credential_v1",
        "activate_provider_credential_v1",
        "delete_provider_credential_v1",
        "list_provider_credentials",
        "create_provider_credential",
        "set_active_provider_credential",
        "delete_provider_credential",
        "get_active_provider_credential",
    ],
}

FLOW_FILES = {
    "deployment_config": [
        "docker-compose.yml",
        "Dockerfile",
        ".env.example",
        "docs/deployment_runbook.md",
        "scripts/run_local_backend.sh",
    ],
}

ARCHITECTURE_FILES = [
    "README.md",
    "docker-compose.yml",
    "Dockerfile",
    ".env.example",
    "docs/deployment_runbook.md",
    "retrieval/api_service.py",
    "retrieval/main.py",
    "retrieval/session_indexer.py",
    "rag_ingestion/main.py",
]


def process_query(raw_query: str) -> dict:
    """Classify intent and extract symbols/file hints from query text."""
    query = raw_query.strip()
    lower = query.lower()

    symbols = _extract_symbols(query)
    extracted_files = _extract_files(query)

    intent = "SEMANTIC"
    if any(re.search(pattern, lower) for pattern in DEPENDENCY_PATTERNS):
        intent = "DEPENDENCY"
    elif extracted_files or symbols or any(re.search(pattern, lower) for pattern in SYMBOL_HINT_PATTERNS):
        intent = "SYMBOL"

    entities = {
        "symbols": symbols,
        "files": sorted(set(extracted_files)),
    }
    _inject_flow_symbols(query, entities)
    _inject_architecture_files(query, entities)

    if ENABLE_SCORED_INTENT:
        entities.update(_extract_scored_entities(query))
        intent_scores = _score_intents(query, intent, entities)
    else:
        entities.update(_empty_scored_entities())
        intent_scores = _legacy_intent_scores(intent)

    primary_intent = max(intent_scores, key=intent_scores.get)
    confidence = float(intent_scores.get(primary_intent, 0.0))
    return {
        "raw_query": query,
        "intent": intent,
        "primary_intent": primary_intent,
        "intent_scores": intent_scores,
        "entities": entities,
        "is_followup": primary_intent == "FOLLOWUP" or intent_scores.get("FOLLOWUP", 0.0) >= 0.6,
        "topic_shift": False,
        "confidence": confidence,
    }


def _inject_flow_symbols(query: str, entities: dict) -> None:
    flow_kind = _flow_kind(query)
    if not flow_kind:
        return
    symbols = list(entities.get("symbols") or [])
    symbols.extend(FLOW_SYMBOLS[flow_kind])
    entities["symbols"] = sorted(set(symbols))
    files = list(entities.get("files") or [])
    files.extend(FLOW_FILES.get(flow_kind, []))
    entities["files"] = sorted(set(files))


def _inject_architecture_files(query: str, entities: dict) -> None:
    lower = query.lower()
    if not any(
        phrase in lower
        for phrase in (
            "architecture",
            "system design",
            "project structure",
            "how is this project structured",
            "how is the project structured",
            "module layout",
            "runtime shape",
        )
    ):
        return
    files = list(entities.get("files") or [])
    files.extend(ARCHITECTURE_FILES)
    entities["files"] = sorted(set(files))


def _flow_kind(query: str) -> str:
    lower = query.lower()
    if not any(
        marker in lower
        for marker in (
            "flow",
            "lifecycle",
            "orchestration",
            "trace",
            "walk me through",
            "step",
            "deployment",
            "configuration",
            "config",
            "provider",
            "credential",
            "credentials",
            "api key",
            "llm",
        )
    ):
        return ""
    if any(term in lower for term in ("provider", "llm provider", "model")) and any(term in lower for term in ("credential", "credentials", "api key", "key")):
        return "provider_credentials"
    if any(term in lower for term in ("auth", "oauth", "login", "cookie", "credential")):
        return "auth_session"
    if any(term in lower for term in ("index", "indexing", "ingestion", "repo session", "session creation", "clone")):
        return "indexing_session"
    if any(term in lower for term in ("deploy", "deployment", "docker", "compose", "container", "environment", "configuration", "config")):
        return "deployment_config"
    if any(term in lower for term in ("provider", "credential", "credentials", "api key", "llm provider", "model")):
        return "provider_credentials"
    if any(term in lower for term in ("backend", "request", "query", "orchestration", "api")):
        return "orchestration"
    return ""


def _extract_files(query: str) -> list[str]:
    extracted_files = []
    for token in query.split():
        token = token.strip(".,()[]{}\"'`")
        if FILE_RE.fullmatch(token):
            extracted_files.append(token)
    return sorted(set(extracted_files))


def _extract_scored_entities(query: str) -> dict[str, list[str]]:
    env_keys = sorted(set(ENV_KEY_RE.findall(query)))
    routes = sorted(set(match.rstrip(".,)") for match in ROUTE_RE.findall(query)))
    dependencies = _extract_dependency_names(query)
    config_keys = sorted(set(env_keys + _extract_config_keys(query)))
    api_terms = sorted(set(routes + _extract_api_terms(query)))
    exact_terms = sorted(set(env_keys + dependencies + config_keys + api_terms))
    return {
        "env_keys": env_keys,
        "dependencies": dependencies,
        "config_keys": config_keys,
        "routes": routes,
        "api_terms": api_terms,
        "exact_terms": exact_terms,
    }


def _empty_scored_entities() -> dict[str, list[str]]:
    return {
        "env_keys": [],
        "dependencies": [],
        "config_keys": [],
        "routes": [],
        "api_terms": [],
        "exact_terms": [],
    }


def _extract_dependency_names(query: str) -> list[str]:
    lower = query.lower()
    dependencies = set()
    for token in PACKAGE_TOKEN_RE.findall(query):
        cleaned = token.strip(".,()[]{}\"'`")
        if cleaned and not FILE_RE.fullmatch(cleaned):
            dependencies.add(cleaned)
    for token in re.findall(r"\b[a-z][a-z0-9-]{2,}\b", lower):
        if token in KNOWN_DEPENDENCY_TERMS:
            dependencies.add(token)
    for quoted in re.findall(r"[`'\"]([^`'\"]+)[`'\"]", query):
        cleaned = quoted.strip()
        if _looks_like_dependency(cleaned):
            dependencies.add(cleaned)
    return sorted(dependencies, key=str.lower)


def _extract_config_keys(query: str) -> list[str]:
    keys = []
    for token in re.findall(r"[`'\"]([^`'\"]+)[`'\"]", query):
        cleaned = token.strip()
        if ENV_KEY_RE.fullmatch(cleaned):
            keys.append(cleaned)
    return keys


def _extract_api_terms(query: str) -> list[str]:
    terms = []
    lower = query.lower()
    for token in HYPHENATED_API_TERM_RE.findall(lower):
        if any(part in token for part in ("api", "auth", "key", "endpoint", "route", "session", "submission")):
            terms.append(token)
    return sorted(set(terms))


def _looks_like_dependency(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered in KNOWN_DEPENDENCY_TERMS
        or bool(PACKAGE_TOKEN_RE.fullmatch(value))
        or "/" in value
        or "-" in value
    )


def _score_intents(query: str, legacy_intent: str, entities: dict[str, list[str]]) -> dict[str, float]:
    lower = query.lower()
    scores = {intent: 0.0 for intent in INTENT_FAMILIES}
    scores["SEMANTIC"] = 0.35

    if legacy_intent == "SYMBOL":
        scores["SYMBOL"] = 0.72
    elif legacy_intent == "DEPENDENCY":
        scores["DEPENDENCY"] = 0.76

    if any(phrase in lower for phrase in ("what is this project about", "what does this project do", "overview")):
        scores["OVERVIEW"] = 0.86
    if "architecture" in lower or "design" in lower or "how is this project structured" in lower:
        scores["ARCHITECTURE"] = 0.82
    if any(phrase in lower for phrase in ("tech stack", "stack used", "framework", "library", "dependencies")):
        scores["TECH_STACK"] = 0.82
    if any(phrase in lower for phrase in ("trace", "flow", "lifecycle", "call path", "step by step")):
        scores["TRACE"] = 0.78
    if entities.get("env_keys") or any(word in lower for word in ("env", "environment", "config", "configuration")):
        scores["CONFIG"] = 0.82
    if entities.get("files"):
        scores["FILE"] = 0.78
    if any(phrase in lower for phrase in ("explain", "how does", "what does", "walk me through")):
        scores["EXPLANATION"] = 0.72
    if any(phrase in lower for phrase in ("show code", "show me the code", "code for", "implementation of")):
        scores["CODE_REQUEST"] = 0.83
    if any(phrase in lower for phrase in ("it", "that", "this function", "where is it used", "how does that")):
        scores["FOLLOWUP"] = 0.45
    if len(lower.split()) <= 2 and not any(entities.get(key) for key in ("symbols", "files", "exact_terms")):
        scores["LOW_CONTEXT"] = 0.7
    if entities.get("symbols") or entities.get("files") or entities.get("exact_terms"):
        scores["SYMBOL"] = max(scores["SYMBOL"], 0.68)
    return scores


def _legacy_intent_scores(legacy_intent: str) -> dict[str, float]:
    scores = {intent: 0.0 for intent in INTENT_FAMILIES}
    mapped = legacy_intent if legacy_intent in scores else "SEMANTIC"
    scores["SEMANTIC"] = 0.35
    scores[mapped] = 0.65
    return scores


def _extract_symbols(query: str) -> list[str]:
    snake = [s for s in SNAKE_CASE_RE.findall(query) if "_" in s]
    camel = CAMEL_CASE_RE.findall(query)
    calls = [m.group(1) for m in CALL_RE.finditer(query)]

    explicit = []
    for token in re.findall(r"`([^`]+)`", query):
        t = token.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t):
            explicit.append(t)

    all_candidates = snake + camel + calls + explicit
    cleaned = []
    for candidate in all_candidates:
        c = candidate.strip()
        if not c:
            continue
        if c.lower() in STOPWORDS:
            continue
        cleaned.append(c)

    return sorted(set(cleaned))
