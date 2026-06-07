from __future__ import annotations

import re

# Word-boundary matched keywords mapping terms to target labels
DOMAIN_KEYWORDS = {
    "domain:auth": [
        "auth",
        "authentication",
        "login",
        "signin",
        "logout",
        "oauth",
        "session",
        "sessions",
        "token",
        "tokens",
    ],
    "capability:session-validation": [
        "session validation",
        "validate session",
        "session validate",
        "check session",
    ],
    "capability:token-validation": [
        "token validation",
        "validate token",
        "token validate",
    ],
    "domain:retrieval": ["retrieval", "retrieve", "retriever", "search"],
    "domain:ingestion": ["ingestion", "ingest", "indexing", "parser", "chunker"],
    "domain:provider-management": ["provider", "providers", "api key", "api keys"],
    "domain:frontend": ["frontend", "ui", "component", "components", "page", "pages", "css", "react"],
    "domain:testing": ["testing", "test", "tests"],
    "artifact:test-code": [
        "test files",
        "test code",
        "unit test",
        "unit tests",
        "integration test",
        "integration tests",
    ],
    "domain:devops": ["devops", "docker", "dockerfile", "docker-compose", "deploy", "deployment"],
    "domain:vector-db": ["vector db", "vector database", "qdrant"],
    "tech:qdrant": ["qdrant"],
}


def _term_in_query(term: str, query: str) -> bool:
    """Check if a term or multi-word phrase exists in query with word boundaries."""
    escaped_term = re.escape(term)
    pattern = r"\b" + escaped_term + r"\b"
    return bool(re.search(pattern, query, re.IGNORECASE))


def _any_term_in_query(terms: list[str], query: str) -> bool:
    """Check if any of the terms are in the query."""
    return any(_term_in_query(term, query) for term in terms)


def extract_domain_hints(query: str) -> list[str]:
    """Scan query for domain/capability/tech keyword hints."""
    hints = []
    for label, terms in DOMAIN_KEYWORDS.items():
        if _any_term_in_query(terms, query):
            hints.append(label)
    return hints


def classify_query_intent(query: str) -> dict:
    """Classify query intent and determine labels to boost."""
    q = query.lower()
    domain_hints = extract_domain_hints(query)

    intent = "general_context"
    boost_labels = []

    # 1. code_snippet
    if _any_term_in_query(["code", "snippet", "example", "show me", "print"], q):
        intent = "code_snippet"
        boost_labels = ["question_use:code-snippet", "question_use:code-location"]

    # 2. implementation
    elif _any_term_in_query(["how do i", "how to", "change", "modify", "write", "create", "add", "refactor"], q):
        intent = "implementation"
        boost_labels = ["question_use:implementation", "question_use:technical-explanation"]

    # 3. "how is/how are ... implemented" compound check → technical_explanation
    elif ("how is" in q or "how are" in q) and "implemented" in q:
        intent = "technical_explanation"
        boost_labels = ["question_use:technical-explanation", "question_use:code-location"]

    # 4. code_location
    elif _any_term_in_query(["where is", "where are", "find", "locate", "path", "paths", "directory"], q):
        intent = "code_location"
        boost_labels = ["question_use:code-location", "question_use:technical-explanation"]

    # 5. technical_explanation (general)
    elif _any_term_in_query(["how does", "how do", "why", "explain", "what is", "work", "works"], q):
        intent = "technical_explanation"
        boost_labels = ["question_use:technical-explanation", "question_use:code-location"]

    # 6/7. general_context (default fallback)
    else:
        intent = "general_context"
        boost_labels = ["question_use:general-context", "question_use:repo-overview"]

    # Merge domain hints into boost_labels
    seen = set()
    merged_boost = []
    for label in boost_labels + domain_hints:
        if label not in seen:
            seen.add(label)
            merged_boost.append(label)

    return {
        "intent": intent,
        "boost_labels": merged_boost,
    }


LABEL_WEIGHTS = {
    "question_use": 0.15,
    "capability": 0.12,
    "domain": 0.10,
    "artifact": 0.08,
    "code_role": 0.08,
    "tech": 0.06,
}


def compute_label_boost(chunk_labels: list[str], query_profile: dict) -> float:
    """Compute label boost score for a candidate chunk based on query profile."""
    boost_labels = set(query_profile.get("boost_labels", []))
    boost = 0.0
    for label in chunk_labels:
        if label not in boost_labels:
            continue
        category = label.split(":", 1)[0]
        boost += LABEL_WEIGHTS.get(category, 0.05)
    return min(boost, 1.0)
