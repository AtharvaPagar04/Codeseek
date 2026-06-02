"""Deterministic code-excerpt responses for explicit snippet requests."""

from __future__ import annotations

import re
from pathlib import Path

from retrieval.config import get_repo_root

_DIRECT_CODE_PHRASES = (
    "show code",
    "show me the code",
    "give me the code",
    "i want the code",
    "code snippet",
    "show snippet",
    "full code",
    "source code",
)

_EXPLANATION_PHRASES = (
    "explain the code",
    "explain this code",
    "explain the following code",
    "explain this section",
    "explain the following section",
    "detailed explanation",
    "need a detailed explanation",
    "walk me through",
    "how does this work",
)

_OVERVIEW_PHRASES = (
    "what is this project about",
    "whats this project about",
    "explain the project",
    "project overview",
    "overview of the project",
    "give me an overview",
    "what does this app do",
    "what does this project do",
    "tech stack",
)


def is_code_request(raw_query: str) -> bool:
    query = raw_query.strip().lower()
    if not query:
        return False
    if any(phrase in query for phrase in _DIRECT_CODE_PHRASES):
        return True
    if any(phrase in query for phrase in _EXPLANATION_PHRASES):
        return False

    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query))
    explanation_tokens = {
        "explain",
        "explanation",
        "describe",
        "analysis",
        "analyze",
        "walkthrough",
        "detail",
        "detailed",
        "understand",
        "working",
    }
    if tokens & explanation_tokens:
        return False

    return "snippet" in tokens


def is_explanation_request(raw_query: str) -> bool:
    query = raw_query.strip().lower()
    if not query:
        return False
    if any(phrase in query for phrase in _EXPLANATION_PHRASES):
        return True
    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query))
    return bool(
        tokens
        & {
            "explain",
            "explanation",
            "describe",
            "analysis",
            "analyze",
            "walkthrough",
            "detail",
            "detailed",
            "understand",
            "working",
            "overview",
        }
    )


def is_overview_request(raw_query: str) -> bool:
    query = raw_query.strip().lower()
    if not query:
        return False
    if any(phrase in query for phrase in _OVERVIEW_PHRASES):
        return True
    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query))
    if {"tech", "stack"} <= tokens:
        return True
    return bool(
        tokens & {"overview", "project", "architecture", "stack"}
    ) and bool(tokens & {"about", "purpose", "summary", "explain", "describe", "what"})


def build_code_answer(raw_query: str, sources: list[dict], chunks: list[dict]) -> str:
    selected_sources = _preferred_sources(sources)
    snippets: list[str] = []

    for source in selected_sources:
        formatted = _format_source_snippet(source)
        if formatted:
            snippets.append(formatted)

    for support in find_supporting_import_exports(raw_query, selected_sources, chunks, limit=2):
        if support["formatted"] not in snippets:
            snippets.append(str(support["formatted"]))

    if not snippets:
        return "Not found in retrieved context."

    intro = "Code snippets from retrieved context:"
    return f"{intro}\n\n" + "\n\n".join(snippets[:2])


def _preferred_sources(sources: list[dict]) -> list[dict]:
    primary = [source for source in sources if source.get("expansion_type") == "primary"]
    chosen = primary or list(sources)
    chosen = sorted(
        chosen,
        key=lambda item: (
            item.get("relative_path", ""),
            int(item.get("start_line", 0)),
            int(item.get("end_line", 0)),
        ),
    )
    return chosen[:2]


def _format_source_snippet(source: dict) -> str | None:
    relative_path = str(source.get("relative_path", "")).strip()
    if not relative_path:
        return None

    path = Path(get_repo_root()) / relative_path
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    start_line = max(1, int(source.get("start_line", 1)))
    end_line = max(start_line, int(source.get("end_line", start_line)))
    excerpt = "\n".join(lines[start_line - 1 : end_line]).rstrip()
    if not excerpt:
        return None

    symbol = str(source.get("symbol_name", "")).strip() or "<file>"
    header = f"{relative_path} :: {symbol} (lines {start_line}-{end_line})"
    language = _code_fence_language(relative_path)
    return f"{header}\n```{language}\n{excerpt}\n```"


def find_supporting_import_export(
    raw_query: str,
    selected_sources: list[dict],
    chunks: list[dict],
) -> dict | None:
    matches = find_supporting_import_exports(raw_query, selected_sources, chunks, limit=1)
    return matches[0] if matches else None


def find_supporting_import_exports(
    raw_query: str,
    selected_sources: list[dict],
    chunks: list[dict],
    limit: int = 2,
) -> list[dict]:
    query_tokens = _query_tokens(raw_query)
    if not query_tokens:
        return []

    chunk_by_key = {_source_key(chunk): chunk for chunk in chunks}
    matches: list[tuple[int, dict]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for source in selected_sources:
        source_chunk = chunk_by_key.get(_source_key(source), {})
        relative_path = str(source.get("relative_path", "")).strip()
        if not relative_path:
            continue

        imports = list(source_chunk.get("imports") or []) or _read_imports(relative_path)
        for statement in imports:
            for imported_name, module_path in _parse_named_imports(statement):
                score = _identifier_score(imported_name, query_tokens)
                if score <= 0:
                    continue
                resolved = _resolve_import_path(relative_path, module_path)
                if not resolved:
                    continue

                export_block = _extract_export_block(resolved, imported_name)
                if export_block:
                    key = _source_key(export_block)
                    if key in seen:
                        continue
                    seen.add(key)
                    matches.append((score, export_block))

    matches.sort(
        key=lambda item: (
            -item[0],
            item[1]["relative_path"],
            item[1]["start_line"],
        )
    )
    return [block for _, block in matches[: max(1, limit)]]


def _source_key(item: dict) -> tuple[str, str, int, int]:
    return (
        str(item.get("relative_path", "")),
        str(item.get("symbol_name", "")),
        int(item.get("start_line", 0)),
        int(item.get("end_line", 0)),
    )


def _read_imports(relative_path: str) -> list[str]:
    path = Path(get_repo_root()) / relative_path
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip().startswith("import ")]


def _parse_named_imports(statement: str) -> list[tuple[str, str]]:
    match = re.search(r'import\s+\{([^}]+)\}\s+from\s+["\']([^"\']+)["\']', statement)
    if not match:
        return []

    names = []
    for part in match.group(1).split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        imported_name = cleaned.split(" as ", 1)[0].strip()
        if imported_name:
            names.append((imported_name, match.group(2).strip()))
    return names


def _query_tokens(raw_query: str) -> set[str]:
    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", raw_query.lower()))
    return {_singularize(token) for token in tokens if token not in {"the", "this", "that", "section"}}


def _identifier_score(identifier: str, query_tokens: set[str]) -> int:
    parts = {_singularize(token) for token in _split_identifier(identifier)}
    lowered = identifier.lower()
    score = 0
    for token in query_tokens:
        if token in parts:
            score += 3
        elif token in lowered:
            score += 2
    return score


def _split_identifier(identifier: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier)
    return re.findall(r"[a-zA-Z]+", spaced.lower())


def _resolve_import_path(source_relative_path: str, module_path: str) -> Path | None:
    repo_root = Path(get_repo_root())
    source_path = repo_root / source_relative_path

    if module_path.startswith("@/"):
        base = repo_root / "src" / module_path[2:]
    elif module_path.startswith("./") or module_path.startswith("../"):
        base = (source_path.parent / module_path).resolve()
    else:
        return None

    candidates = [
        base,
        base.with_suffix(".ts"),
        base.with_suffix(".tsx"),
        base.with_suffix(".js"),
        base.with_suffix(".jsx"),
        base / "index.ts",
        base / "index.tsx",
        base / "index.js",
        base / "index.jsx",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _extract_export_block(path: Path, identifier: str) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    pattern = re.compile(rf"^\s*export\s+const\s+{re.escape(identifier)}\s*=")
    for index, line in enumerate(lines):
        if not pattern.search(line):
            continue
        start = index
        end = _find_block_end(lines, index)
        excerpt = "\n".join(lines[start : end + 1]).rstrip()
        if not excerpt:
            return None
        relative_path = str(path.relative_to(Path(get_repo_root())))
        header = f"{relative_path} :: {identifier} (lines {start + 1}-{end + 1})"
        language = _code_fence_language(relative_path)
        return {
            "relative_path": relative_path,
            "symbol_name": identifier,
            "start_line": start + 1,
            "end_line": end + 1,
            "formatted": f"{header}\n```{language}\n{excerpt}\n```",
            "context_block": (
                f"### {relative_path} — {identifier} (export, lines {start + 1}-{end + 1})\n\n"
                f"{excerpt}"
            ),
        }
    return None


def _find_block_end(lines: list[str], start_index: int) -> int:
    balance = 0
    started = False
    for index in range(start_index, len(lines)):
        line = lines[index]
        if not started:
            if "[" in line or "{" in line:
                started = True
            balance += line.count("[") + line.count("{")
            balance -= line.count("]") + line.count("}")
            if started and balance <= 0 and line.strip().endswith(("];", "};")):
                return index
            continue

        balance += line.count("[") + line.count("{")
        balance -= line.count("]") + line.count("}")
        if balance <= 0 and line.strip().endswith(("];", "};")):
            return index
    return min(len(lines) - 1, start_index + 40)


def _code_fence_language(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    return {
        ".py": "python",
        ".ts": "ts",
        ".tsx": "tsx",
        ".js": "js",
        ".jsx": "jsx",
        ".json": "json",
        ".css": "css",
        ".md": "md",
    }.get(suffix, "")


def _singularize(token: str) -> str:
    return token[:-1] if token.endswith("s") and len(token) > 3 else token
