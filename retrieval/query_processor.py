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


def process_query(raw_query: str) -> dict:
    """Classify intent and extract symbols/file hints from query text."""
    query = raw_query.strip()
    lower = query.lower()

    symbols = sorted(set(SNAKE_CASE_RE.findall(query) + CAMEL_CASE_RE.findall(query)))
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
