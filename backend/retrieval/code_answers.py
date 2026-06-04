"""Deterministic code-excerpt responses for explicit snippet requests."""

from __future__ import annotations

import json
import re
import tomllib
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
    "architecture overview",
)

_FLOW_TERMS = {
    "orchestration": {"query", "request", "api", "run_query", "provider", "thread", "source", "response"},
    "auth_session": {"auth", "authentication", "oauth", "github", "session", "cookie", "login", "logout", "credential"},
    "indexing_session": {"index", "indexing", "ingestion", "session", "repo", "clone", "collection", "qdrant"},
}

FLOW_EVIDENCE_MODEL = {
    "orchestration": {
        "title": "Backend Request Orchestration",
        "roles": [
            {
                "name": "API query endpoint",
                "symbols": {"_query_impl"},
                "step": "The API query endpoint resolves auth, provider configuration, session/thread binding, and collection isolation before retrieval runs.",
                "required": True,
            },
            {
                "name": "Retrieval pipeline",
                "symbols": {"run_query"},
                "step": "`run_query()` loads memory, processes the query, searches, expands, assembles context, then chooses deterministic or LLM-backed response generation.",
                "required": True,
            },
            {
                "name": "Source gating",
                "symbols": {"select_sources_for_display"},
                "step": "Source gating limits which retrieved chunks can be shown and cited.",
                "required": False,
            },
            {
                "name": "LLM fallback",
                "symbols": {"generate_answer"},
                "step": "If no deterministic response path applies, the assembled context is sent to the configured LLM provider.",
                "required": False,
            },
        ],
    },
    "auth_session": {
        "title": "Auth And Session Lifecycle",
        "roles": [
            {
                "name": "Auth entrypoint",
                "symbols": {"auth_github", "auth_github_token", "auth_github_callback"},
                "step": "Auth entrypoints exchange or validate GitHub credentials, persist the user/credential, create an auth session, and set the session cookie.",
                "required": True,
            },
            {
                "name": "Session creation",
                "symbols": {"create_auth_session"},
                "step": "`create_auth_session()` stores a hashed auth session token with expiry metadata.",
                "required": True,
            },
            {
                "name": "Session lookup",
                "symbols": {"get_user_for_session_token"},
                "step": "Later requests resolve the cookie by hashing the submitted token and loading the associated user.",
                "required": True,
            },
            {
                "name": "Auth guard",
                "symbols": {"_require_auth_user", "_current_auth_user"},
                "step": "Protected endpoints require a valid auth user before accessing sessions, credentials, or query execution.",
                "required": False,
            },
            {
                "name": "Logout/session deletion",
                "symbols": {"auth_logout", "delete_auth_session"},
                "step": "Logout deletes the auth session and clears the auth cookie.",
                "required": False,
            },
        ],
    },
    "indexing_session": {
        "title": "Indexing And Session Creation Flow",
        "roles": [
            {
                "name": "Session creation",
                "symbols": {"create_session"},
                "step": "`create_session()` normalizes repo identity, creates or reuses a session record, and enqueues indexing work.",
                "required": True,
            },
            {
                "name": "Indexing job",
                "symbols": {"_index_job"},
                "step": "`_index_job()` clones or pulls the repo, checks for reusable indexed commits, runs ingestion, invalidates lexical cache, and marks the session ready.",
                "required": True,
            },
            {
                "name": "Ingestion pipeline",
                "symbols": {"run_pipeline"},
                "step": "The ingestion pipeline parses files, builds chunks and repo-summary metadata, embeds them, and stores them in Qdrant.",
                "required": False,
            },
            {
                "name": "Retry flow",
                "symbols": {"retry_indexing"},
                "step": "Retry flow resets failed sessions and re-enqueues the indexing job when needed.",
                "required": False,
            },
        ],
    },
}


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


def is_flow_explanation_request(raw_query: str) -> bool:
    query = raw_query.strip().lower()
    if not query:
        return False
    tokens = _query_tokens(query)
    flow_markers = {
        "flow",
        "lifecycle",
        "orchestration",
        "pipeline",
        "trace",
        "step",
        "steps",
        "sequence",
        "process",
        "work",
        "works",
    }
    phase_one_terms = set().union(*_FLOW_TERMS.values())
    if not (tokens & flow_markers):
        return False
    return bool(tokens & phase_one_terms)


def build_flow_answer(
    raw_query: str,
    sources: list[dict],
    chunks: list[dict],
    *,
    return_sources: bool = False,
) -> str | tuple[str, list[dict]]:
    selected_sources = _preferred_flow_sources(raw_query, sources)
    if not selected_sources:
        answer = "Insufficient context in retrieved code to explain this flow confidently."
        if return_sources:
            return answer, []
        return answer

    flow_kind = _flow_kind(raw_query)
    model = FLOW_EVIDENCE_MODEL.get(flow_kind, FLOW_EVIDENCE_MODEL["orchestration"])
    title = str(model["title"])
    role_matches = _flow_role_matches(flow_kind, selected_sources)

    evidence_state = _flow_evidence_state(flow_kind, selected_sources)
    lines = [f"{title} ({evidence_state} evidence)", ""]
    if evidence_state != "strong":
        missing = _missing_flow_roles(flow_kind, role_matches)
        if missing:
            lines.append(f"Missing expected evidence roles: {', '.join(missing)}.")
        lines.append("This answer is based on partial retrieved evidence; some adjacent helpers may be outside the selected source set.")
        lines.append("")

    lines.append("Flow:")
    steps = _flow_steps(flow_kind, selected_sources)
    lines.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    lines.append("")
    lines.append("Key evidence:")
    lines.extend(_flow_evidence_lines(selected_sources))
    lines.append("")
    lines.append("Sources:")
    lines.extend(_source_reference_lines(selected_sources[:7]))
    answer = "\n".join(lines)
    if return_sources:
        return answer, selected_sources[:7]
    return answer


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


def build_overview_answer(raw_query: str, sources: list[dict], chunks: list[dict]) -> str:
    selected_sources = _preferred_overview_sources(sources)
    direct = _project_summary(selected_sources, chunks)
    if not direct:
        direct = "This repository's purpose is only partially visible in retrieved context."

    bullets: list[str] = []
    technologies = _extract_tech_stack(selected_sources)
    architecture = _overview_architecture_points(selected_sources)
    if technologies:
        bullets.append(f"- Tech stack: {', '.join(technologies[:8])}.")
    bullets.extend(f"- {point}" for point in architecture[:4])
    if not bullets:
        bullets.append("- Retrieved overview evidence is limited to the currently selected source set.")

    lines = [direct, ""]
    lines.extend(bullets)
    lines.append("")
    lines.append("Sources:")
    lines.extend(_source_reference_lines(selected_sources[:5]))
    return "\n".join(lines)


def build_explanation_answer(raw_query: str, sources: list[dict], chunks: list[dict]) -> str:
    selected_sources = _preferred_sources(sources)
    if not selected_sources:
        return "Insufficient context in retrieved code to explain this confidently."

    primary = selected_sources[0]
    snippet = _read_source_excerpt(primary)
    support = find_supporting_import_exports(raw_query, selected_sources, chunks, limit=2)

    direct = _render_summary(primary, snippet)
    if not direct:
        direct = (
            f"{primary.get('symbol_name', '<file>')} is implemented in "
            f"{primary.get('relative_path', '')}."
        )

    bullets = [
        f"- Render source: {primary.get('relative_path', '')} :: {primary.get('symbol_name', '') or '<file>'} "
        f"(lines {primary.get('start_line', 0)}-{primary.get('end_line', 0)})."
    ]
    data_summary = _data_summary(support)
    if data_summary:
        bullets.append(f"- Backing data: {data_summary}")
    interaction_summary = _interaction_summary(snippet)
    if interaction_summary:
        bullets.append(f"- Interaction/behavior: {interaction_summary}")
    concrete_values = _concrete_values_summary(snippet, support)
    if concrete_values:
        bullets.append(f"- Concrete values: {concrete_values}")

    all_sources = selected_sources + support
    bullets.append(
        f"- Source coverage: {', '.join(line[2:] for line in _source_reference_lines(all_sources[:5]))}."
    )

    lines = [direct, ""]
    lines.extend(bullets)
    lines.append("")
    lines.append("Sources:")
    lines.extend(_source_reference_lines(all_sources[:5]))
    return "\n".join(lines)


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


def _preferred_flow_sources(raw_query: str, sources: list[dict]) -> list[dict]:
    flow_kind = _flow_kind(raw_query)
    role_matches = _flow_role_matches(flow_kind, sources)
    role_sources: list[dict] = []
    for role in FLOW_EVIDENCE_MODEL.get(flow_kind, {}).get("roles", []):
        match = role_matches.get(str(role["name"]))
        if match:
            role_sources.append(match)
    role_ids = {_source_key(source) for source in role_sources}
    terms = _FLOW_TERMS.get(flow_kind, set()) | _query_tokens(raw_query)
    scored: list[tuple[int, dict]] = []
    for source in sources:
        if _source_key(source) in role_ids:
            continue
        text = _source_search_text(source)
        score = 0
        for term in terms:
            if term and term in text:
                score += 2
        path = str(source.get("relative_path", "")).lower()
        symbol = str(source.get("symbol_name", "")).lower()
        if flow_kind == "orchestration" and path.endswith("api_service.py"):
            score += 8
        if flow_kind == "orchestration" and symbol in {"_query_impl", "run_query"}:
            score += 10
        if flow_kind == "auth_session" and any(part in path for part in ("auth_store.py", "api_service.py", "github_store.py")):
            score += 8
        if flow_kind == "auth_session" and any(term in symbol for term in ("auth", "session", "credential")):
            score += 10
        if flow_kind == "indexing_session" and path.endswith("session_indexer.py"):
            score += 10
        if flow_kind == "indexing_session" and symbol in {"create_session", "_index_job", "retry_indexing"}:
            score += 10
        if score > 0:
            scored.append((score, source))

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].get("relative_path", ""),
            int(item[1].get("start_line", 0)),
        )
    )
    supplemental = [source for _, source in scored]
    selected = role_sources + supplemental
    deduped: list[dict] = []
    seen: set[tuple[str, str, int, int]] = set()
    for source in selected:
        key = _source_key(source)
        if key in seen:
            continue
        deduped.append(source)
        seen.add(key)
        if len(deduped) >= 7:
            break
    return deduped


def _preferred_overview_sources(sources: list[dict]) -> list[dict]:
    return sorted(
        list(sources),
        key=lambda item: (
            -_overview_source_priority(item),
            item.get("relative_path", ""),
            int(item.get("start_line", 0)),
        ),
    )[:5]


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


def _read_source_excerpt(source: dict) -> str:
    relative_path = str(source.get("relative_path", "")).strip()
    if not relative_path:
        return ""
    path = Path(get_repo_root()) / relative_path
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start_line = max(1, int(source.get("start_line", 1)))
    end_line = max(start_line, int(source.get("end_line", start_line)))
    return "\n".join(lines[start_line - 1 : end_line]).rstrip()


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


def _project_summary(sources: list[dict], chunks: list[dict]) -> str:
    for source in sources:
        if _is_repo_summary_source(source):
            purpose = str(source.get("purpose", "")).strip()
            if purpose:
                return purpose.rstrip(".") + "."
            direct = _summary_direct_answer(str(source.get("summary", "")).strip())
            if direct:
                return direct.rstrip(".") + "."

    for source in sources:
        relative_path = str(source.get("relative_path", "")).strip()
        lower = relative_path.lower()
        excerpt = _read_source_excerpt(source)
        if lower.startswith("readme"):
            summary = _readme_summary(excerpt)
            if summary:
                return summary

    for source in sources:
        relative_path = str(source.get("relative_path", "")).strip()
        if relative_path.lower().endswith("package.json"):
            package = _read_json_file(relative_path)
            if isinstance(package, dict):
                name = str(package.get("name", "")).strip()
                desc = str(package.get("description", "")).strip()
                if name and desc:
                    return f"{name} is {desc.rstrip('.')}."
                if name:
                    return f"{name} is a JavaScript/TypeScript project described in package.json."

    for source in sources:
        summary = _summary_line(source)
        if summary:
            return summary.rstrip(".") + "."

    for chunk in chunks:
        summary = str(chunk.get("summary", "")).strip()
        if summary:
            direct = _summary_direct_answer(summary)
            if direct:
                return direct.rstrip(".") + "."
            return summary.rstrip(".") + "."
    return ""


def _readme_summary(text: str) -> str:
    if not text:
        return ""
    lines = [line.strip().lstrip("# ").strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if len(line.split()) >= 5:
            return line.rstrip(".") + "."
    return ""


def _overview_architecture_points(sources: list[dict]) -> list[str]:
    points: list[str] = []
    for source in sources:
        relative_path = str(source.get("relative_path", "")).strip()
        symbol = str(source.get("symbol_name", "")).strip() or "<file>"
        lower = relative_path.lower()
        summary = _summary_line(source)
        if _is_repo_summary_source(source):
            services = list(source.get("services") or [])
            env_keys = list(source.get("env_keys") or [])
            entrypoints = list(source.get("entrypoints") or [])
            if services:
                points.append(f"Runtime services summarized for this repo: {', '.join(services[:5])}.")
            if entrypoints:
                points.append(f"Entrypoints surfaced by repo summary: {', '.join(entrypoints[:5])}.")
            if env_keys:
                points.append(f"Configuration keys summarized for this repo: {', '.join(env_keys[:5])}.")
        elif lower.startswith("readme"):
            points.append(f"Repository overview content is anchored in {relative_path}.")
        elif lower.endswith("package.json"):
            points.append(f"Runtime and dependency metadata are declared in {relative_path}.")
        elif lower.endswith(("requirements.txt", "pyproject.toml")):
            points.append(f"Python dependency/configuration details are declared in {relative_path}.")
        elif lower.endswith(("docker-compose.yml", "docker-compose.yaml")):
            services = _services_from_text(summary or _read_source_excerpt(source))
            if services:
                points.append(f"Deployment services visible in {relative_path}: {', '.join(services[:5])}.")
            else:
                points.append(f"Deployment service wiring is declared in {relative_path}.")
        elif lower.endswith("dockerfile") or lower == "dockerfile":
            base_image = _base_image_from_text(summary or _read_source_excerpt(source))
            if base_image:
                points.append(f"Container build is based on {base_image} in {relative_path}.")
            else:
                points.append(f"Container build instructions are declared in {relative_path}.")
        elif lower.endswith(".env.example"):
            env_keys = _env_keys_from_text(summary or _read_source_excerpt(source))
            if env_keys:
                points.append(f"Expected environment configuration is documented in {relative_path}: {', '.join(env_keys[:5])}.")
            else:
                points.append(f"Expected environment configuration is documented in {relative_path}.")
        elif "/src/" in lower or lower.startswith("src/"):
            points.append(f"Application behavior is implemented in {relative_path} via {symbol}.")
        elif any(part in lower for part in ("config", ".env", "docker", "vite", "tailwind")):
            points.append(f"Deployment or build configuration is visible in {relative_path}.")
        if summary and not lower.startswith(("readme", "src/")):
            points.append(summary.rstrip(".") + ".")
    return _dedupe(points)


def _extract_tech_stack(sources: list[dict]) -> list[str]:
    found: list[str] = []
    for source in sources:
        relative_path = str(source.get("relative_path", "")).strip()
        lower = relative_path.lower()
        found.extend(str(item) for item in source.get("detected_frameworks") or [])
        found.extend(_map_dependency_names(list(source.get("dependencies") or [])))
        if lower.endswith("package.json"):
            package = _read_json_file(relative_path)
            if isinstance(package, dict):
                deps = {}
                deps.update(package.get("dependencies") or {})
                deps.update(package.get("devDependencies") or {})
                found.extend(_map_dependency_names(list(deps.keys())))
        elif lower.endswith("requirements.txt"):
            path = Path(get_repo_root()) / relative_path
            try:
                names = [
                    line.split("==", 1)[0].split(">=", 1)[0].strip()
                    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
            except OSError:
                names = []
            found.extend(_map_dependency_names(names))
        elif lower.endswith("pyproject.toml"):
            payload = _read_toml_file(relative_path)
            if isinstance(payload, dict):
                names = []
                project = payload.get("project") or {}
                for item in project.get("dependencies") or []:
                    names.append(str(item).split("[", 1)[0].split(">=", 1)[0].split("==", 1)[0])
                found.extend(_map_dependency_names(names))
        elif lower.endswith(("vite.config.js", "vite.config.ts")):
            found.append("Vite")
        elif lower.endswith(("tailwind.config.js", "tailwind.config.ts")):
            found.append("Tailwind CSS")
        elif lower.endswith("docker-compose.yml"):
            found.extend(["Docker Compose", "Postgres", "Qdrant"])
        summary = _summary_line(source)
        found.extend(_stack_from_summary(summary))
    return _dedupe(found)


def _flow_kind(raw_query: str) -> str:
    tokens = _query_tokens(raw_query)
    scores = {
        kind: len(tokens & terms)
        for kind, terms in _FLOW_TERMS.items()
    }
    best = max(scores, key=lambda key: scores[key])
    return best if scores[best] > 0 else "orchestration"


def _source_search_text(source: dict) -> str:
    parts = [
        source.get("relative_path", ""),
        source.get("symbol_name", ""),
        source.get("qualified_symbol", ""),
        source.get("signature", ""),
        source.get("summary", ""),
        source.get("docstring", ""),
    ]
    for key in ("calls", "imports", "parameters", "methods", "file_symbols", "summary_facts"):
        value = source.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    return " ".join(str(part).lower() for part in parts if part)


def _flow_evidence_state(flow_kind: str, sources: list[dict]) -> str:
    role_matches = _flow_role_matches(flow_kind, sources)
    roles = FLOW_EVIDENCE_MODEL.get(flow_kind, {}).get("roles", [])
    required = [str(role["name"]) for role in roles if role.get("required")]
    matched_required = [name for name in required if role_matches.get(name)]
    if required and len(matched_required) == len(required):
        return "strong"
    if matched_required:
        return "partial"
    return "weak"


def _flow_steps(flow_kind: str, sources: list[dict]) -> list[str]:
    model = FLOW_EVIDENCE_MODEL.get(flow_kind, FLOW_EVIDENCE_MODEL["orchestration"])
    role_matches = _flow_role_matches(flow_kind, sources)
    steps = [
        str(role["step"])
        for role in model["roles"]
        if role_matches.get(str(role["name"]))
    ]
    if steps:
        return steps
    return [
        "The retrieved evidence identifies the relevant files and symbols, but not enough adjacent helpers were selected for a complete deterministic trace.",
        "Use the cited sources as the reliable starting point and ask for a narrower symbol-level trace if more detail is needed.",
    ]


def _flow_role_matches(flow_kind: str, sources: list[dict]) -> dict[str, dict]:
    model = FLOW_EVIDENCE_MODEL.get(flow_kind, FLOW_EVIDENCE_MODEL["orchestration"])
    matches: dict[str, dict] = {}
    for role in model["roles"]:
        role_name = str(role["name"])
        role_symbols = set(role.get("symbols") or [])
        for source in sources:
            symbol = str(source.get("symbol_name", "")).strip()
            if symbol in role_symbols:
                matches[role_name] = source
                break
    return matches


def _missing_flow_roles(flow_kind: str, role_matches: dict[str, dict]) -> list[str]:
    model = FLOW_EVIDENCE_MODEL.get(flow_kind, FLOW_EVIDENCE_MODEL["orchestration"])
    return [
        str(role["name"])
        for role in model["roles"]
        if role.get("required") and not role_matches.get(str(role["name"]))
    ]


def _flow_evidence_lines(sources: list[dict]) -> list[str]:
    lines = []
    for source in sources[:7]:
        symbol = str(source.get("symbol_name", "")).strip() or "<file>"
        relative_path = str(source.get("relative_path", "")).strip()
        summary = str(source.get("summary", "")).strip().splitlines()[0:1]
        suffix = f" - {summary[0]}" if summary else ""
        lines.append(
            f"- `{relative_path} :: {symbol}` lines {source.get('start_line', 0)}-{source.get('end_line', 0)}{suffix}"
        )
    return lines


def _map_dependency_names(names: list[str]) -> list[str]:
    mapping = {
        "react": "React",
        "react-dom": "React DOM",
        "react-router-dom": "React Router",
        "vite": "Vite",
        "tailwindcss": "Tailwind CSS",
        "fastapi": "FastAPI",
        "uvicorn": "Uvicorn",
        "httpx": "HTTPX",
        "psycopg": "Postgres",
        "psycopg[binary]": "Postgres",
        "qdrant-client": "Qdrant",
        "sentence-transformers": "SentenceTransformers",
        "tree-sitter": "Tree-sitter",
        "groq": "Groq",
        "openai": "OpenAI",
        "uuid": "UUID",
    }
    found = []
    for name in names:
        normalized = name.strip().lower()
        if normalized in mapping:
            found.append(mapping[normalized])
        elif normalized in {"typescript", "ts-node"}:
            found.append("TypeScript")
        elif normalized == "python":
            found.append("Python")
    return found


def _render_summary(source: dict, snippet: str) -> str:
    symbol = str(source.get("symbol_name", "")).strip() or "<file>"
    relative_path = str(source.get("relative_path", "")).strip()
    tags = re.findall(r"<([A-Za-z][A-Za-z0-9]*)", snippet)
    unique_tags = _dedupe([tag for tag in tags if tag.lower() not in {"fragment"}])
    mapped_sources = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\.map\(", snippet)

    parts = [f"{symbol} is implemented in {relative_path}"]
    if unique_tags:
        parts.append(f"and renders {', '.join(unique_tags[:4])}")
    if mapped_sources:
        parts.append(f"using mapped data from {', '.join(_dedupe(mapped_sources)[:3])}")
    return " ".join(parts).rstrip(".") + "."


def _data_summary(support: list[dict]) -> str:
    if not support:
        return ""
    items = []
    for item in support:
        values = _extract_export_values(item)
        label = f"{item.get('relative_path', '')} :: {item.get('symbol_name', '')}"
        if values:
            label += f" with values like {', '.join(values[:3])}"
        items.append(label)
    return "; ".join(items[:2]) + "."


def _interaction_summary(snippet: str) -> str:
    handlers = sorted(set(re.findall(r"\b(on[A-Z][A-Za-z0-9_]*)\s*=", snippet)))
    calls = sorted(set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\(", snippet)))
    calls = [call for call in calls if call not in {"return", "map"}]
    if handlers:
        text = f"event handlers include {', '.join(handlers[:4])}"
        if calls:
            text += f"; helper calls include {', '.join(calls[:4])}"
        return text + "."
    if calls:
        return f"helper calls include {', '.join(calls[:4])}."
    return ""


def _concrete_values_summary(snippet: str, support: list[dict]) -> str:
    values = []
    values.extend(re.findall(r'id="([^"]+)"', snippet))
    values.extend(re.findall(r'"([^"]{3,40})"', snippet))
    for item in support:
        values.extend(_extract_export_values(item))
    values = [value for value in values if len(value.split()) <= 6 and not value.startswith("@/")]
    values = _dedupe(values)
    return ", ".join(values[:5])


def _extract_export_values(item: dict) -> list[str]:
    formatted = str(item.get("formatted", ""))
    values = re.findall(r'(?:title|name|label)\s*:\s*"([^"]+)"', formatted)
    if values:
        return _dedupe(values)
    values = re.findall(r"(?:title|name|label)\s*:\s*'([^']+)'", formatted)
    return _dedupe(values)


def _source_reference_lines(sources: list[dict]) -> list[str]:
    lines = []
    seen = set()
    for src in sources:
        key = (
            src.get("relative_path", ""),
            src.get("symbol_name", ""),
            int(src.get("start_line", 0)),
            int(src.get("end_line", 0)),
        )
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"- {src.get('relative_path', '')} :: {src.get('symbol_name', '') or '<file>'} "
            f"(lines {src.get('start_line', 0)}-{src.get('end_line', 0)})"
        )
    return lines


def _overview_source_priority(source: dict) -> int:
    relative_path = str(source.get("relative_path", "")).lower()
    chunk_type = str(source.get("chunk_type", "")).lower()
    file_type = str(source.get("file_type", "")).lower()
    score = 0
    if chunk_type == "repo_summary" or file_type == "repo_summary" or relative_path == "__repo_summary__.md":
        score += 100
    if relative_path.startswith("readme"):
        score += 50
    if relative_path.endswith("package.json"):
        score += 40
    if relative_path.endswith(("requirements.txt", "pyproject.toml")):
        score += 36
    if any(part in relative_path for part in ("config", ".env", "docker", "vite", "tailwind")):
        score += 18
    if "/src/" in relative_path or relative_path.startswith("src/"):
        score += 12
    return score


def _is_repo_summary_source(source: dict) -> bool:
    return (
        str(source.get("chunk_type", "")).lower() == "repo_summary"
        or str(source.get("file_type", "")).lower() == "repo_summary"
        or str(source.get("relative_path", "")).lower() == "__repo_summary__.md"
    )


def _read_json_file(relative_path: str):
    path = Path(get_repo_root()) / relative_path
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _read_toml_file(relative_path: str):
    path = Path(get_repo_root()) / relative_path
    try:
        return tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _dedupe(items: list[str]) -> list[str]:
    out = []
    seen = set()
    for item in items:
        cleaned = str(item).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(cleaned)
    return out


def _singularize(token: str) -> str:
    return token[:-1] if token.endswith("s") and len(token) > 3 else token


def _summary_line(source: dict) -> str:
    return str(source.get("summary", "")).strip()


def _summary_direct_answer(summary: str) -> str:
    for prefix in ("Overview:", "Description:", "Project:"):
        if summary.startswith(prefix):
            return summary.split(":", 1)[1].strip()
    return ""


def _stack_from_summary(summary: str) -> list[str]:
    if not summary:
        return []
    match = re.search(r"(?:Dependencies|Python dependencies):\s*(.+)", summary)
    if not match:
        return []
    raw = [part.strip() for part in match.group(1).split(",")]
    return _map_dependency_names(raw)


def _services_from_text(text: str) -> list[str]:
    if not text:
        return []
    match = re.search(r"Services:\s*(.+)", text)
    if match:
        return _dedupe([part.strip() for part in match.group(1).split(",")])
    return []


def _base_image_from_text(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"Base image:\s*([^\n|]+)", text)
    return match.group(1).strip() if match else ""


def _env_keys_from_text(text: str) -> list[str]:
    if not text:
        return []
    match = re.search(r"Environment keys:\s*(.+)", text)
    if not match:
        return []
    return _dedupe([part.strip() for part in match.group(1).split(",")])
