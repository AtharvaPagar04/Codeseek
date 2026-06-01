"""Intent classification and entity extraction for retrieval."""

import re

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

SNAKE_CASE_RE = re.compile(r"\b[a-z][a-z0-9_]{2,}\b")
CAMEL_CASE_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]{2,}\b")
FILE_RE = re.compile(r"\b\S+\.(py|js|ts|tsx|jsx)\b")
CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\(\)")

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


def process_query(raw_query: str) -> dict:
    """Classify intent and extract symbols/file hints from query text."""
    query = raw_query.strip()
    lower = query.lower()

    symbols = _extract_symbols(query)
    files = sorted(set(FILE_RE.findall(query)))

    intent = "SEMANTIC"
    if any(re.search(pattern, lower) for pattern in DEPENDENCY_PATTERNS):
        intent = "DEPENDENCY"
    elif files or symbols or any(re.search(pattern, lower) for pattern in SYMBOL_HINT_PATTERNS):
        intent = "SYMBOL"

    extracted_files = []
    for token in query.split():
        token = token.strip(".,()[]{}\"'`")
        if FILE_RE.fullmatch(token):
            extracted_files.append(token)

    return {
        "raw_query": query,
        "intent": intent,
        "entities": {
            "symbols": symbols,
            "files": sorted(set(extracted_files)),
        },
    }


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
        # Keep CamelCase symbols even if they contain stopword-like parts.
        if c.lower() in STOPWORDS and not re.search(r"[A-Z]", c):
            continue
        cleaned.append(c)

    return sorted(set(cleaned))
