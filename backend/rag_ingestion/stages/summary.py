"""Summary generation stage."""

from __future__ import annotations

import json
import re
import tomllib

from rag_ingestion.models.chunk import Chunk


def generate_summary(chunk: Chunk) -> str:
    """Generate a deterministic AST-based chunk summary."""
    if chunk.chunk_type == "function":
        lines = [f"Function: {chunk.symbol_name}"]
        if chunk.parameters:
            lines.append(f"Parameters: {', '.join(chunk.parameters)}")
        if chunk.docstring:
            lines.append(f"Docstring: {chunk.docstring}")
        return "\n".join(lines)

    if chunk.chunk_type == "method":
        lines = [f"Method: {chunk.symbol_name}", f"Class: {chunk.parent_symbol}"]
        if chunk.parameters:
            lines.append(f"Parameters: {', '.join(chunk.parameters)}")
        if chunk.docstring:
            lines.append(f"Docstring: {chunk.docstring}")
        return "\n".join(lines)

    if chunk.chunk_type == "class":
        lines = [f"Class: {chunk.symbol_name}"]
        if chunk.methods:
            lines.append(f"Methods: {', '.join(chunk.methods)}")
        if chunk.docstring:
            lines.append(f"Docstring: {chunk.docstring}")
        return "\n".join(lines)

    if chunk.chunk_type == "file":
        lines = [f"File: {chunk.relative_path}"]
        extra = _structured_file_summary(chunk)
        if extra:
            lines.append(extra)
        if chunk.file_symbols:
            lines.append(f"Symbols: {', '.join(chunk.file_symbols)}")
        return "\n".join(lines)

    return ""


def _structured_file_summary(chunk: Chunk) -> str:
    relative_path = chunk.relative_path.lower()
    content = chunk.content

    if relative_path.startswith("readme"):
        _extract_readme_metadata(chunk, content)
    if relative_path.endswith("package.json"):
        _extract_package_json_metadata(chunk, content)
    if relative_path.endswith("requirements.txt"):
        _extract_requirements_metadata(chunk, content)
    if relative_path.endswith("pyproject.toml"):
        _extract_pyproject_metadata(chunk, content)
    if relative_path.endswith("docker-compose.yml") or relative_path.endswith("docker-compose.yaml"):
        _extract_docker_compose_metadata(chunk, content)
    if relative_path == "dockerfile" or relative_path.endswith("/dockerfile"):
        _extract_dockerfile_metadata(chunk, content)
    if relative_path.endswith(".env.example"):
        _extract_env_example_metadata(chunk, content)
    return " | ".join(chunk.summary_facts[:8])


def _extract_readme_metadata(chunk: Chunk, content: str) -> None:
    chunk.file_type = "readme"
    lines = [line.strip().lstrip("# ").strip() for line in content.splitlines() if line.strip()]
    headings = _readme_sections(content)
    for line in lines:
        if len(line.split()) >= 5:
            chunk.purpose = line.rstrip(".")
            chunk.summary_facts.append(f"Overview: {chunk.purpose}")
            break
    chunk.setup_steps = _section_commands(headings, ("install", "setup", "getting started"))
    chunk.usage_commands = _section_commands(headings, ("usage", "run", "development"))
    chunk.architecture_notes = _section_lines(headings, ("architecture", "structure", "design"), limit=4)
    if chunk.setup_steps:
        chunk.summary_facts.append(f"Setup commands: {', '.join(chunk.setup_steps[:4])}")
    if chunk.usage_commands:
        chunk.summary_facts.append(f"Usage commands: {', '.join(chunk.usage_commands[:4])}")
    if chunk.architecture_notes:
        chunk.summary_facts.append(f"Architecture notes: {'; '.join(chunk.architecture_notes[:2])}")


def _extract_package_json_metadata(chunk: Chunk, content: str) -> None:
    chunk.file_type = "package_json"
    try:
        payload = json.loads(content)
    except Exception:
        return
    name = str(payload.get("name", "")).strip()
    description = str(payload.get("description", "")).strip()
    chunk.dependencies = _dedupe(list((payload.get("dependencies") or {}).keys()))
    chunk.dev_dependencies = _dedupe(list((payload.get("devDependencies") or {}).keys()))
    chunk.scripts = {str(k): str(v) for k, v in (payload.get("scripts") or {}).items()}
    chunk.detected_frameworks = _detect_frameworks(chunk.dependencies + chunk.dev_dependencies)
    chunk.config_tools = _detect_config_tools(chunk.dependencies + chunk.dev_dependencies + list(chunk.scripts))
    if "main" in payload:
        chunk.entrypoints.append(str(payload["main"]))
    if "module" in payload:
        chunk.entrypoints.append(str(payload["module"]))
    if name:
        chunk.summary_facts.append(f"Package: {name}")
    if description:
        chunk.summary_facts.append(f"Description: {description.rstrip('.')}")
    if chunk.dependencies or chunk.dev_dependencies:
        deps = _dedupe(chunk.dependencies[:4] + chunk.dev_dependencies[:4])
        chunk.summary_facts.append(f"Dependencies: {', '.join(deps[:8])}")
    if chunk.scripts:
        chunk.summary_facts.append(f"Scripts: {', '.join(list(chunk.scripts)[:8])}")
    if chunk.detected_frameworks:
        chunk.summary_facts.append(f"Frameworks: {', '.join(chunk.detected_frameworks[:8])}")


def _extract_requirements_metadata(chunk: Chunk, content: str) -> None:
    chunk.file_type = "requirements"
    packages = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        packages.append(re.split(r"(==|>=|<=|~=)", stripped, maxsplit=1)[0].strip())
    chunk.dependencies = _dedupe(packages)
    chunk.detected_frameworks = _detect_frameworks(chunk.dependencies)
    if chunk.dependencies:
        chunk.summary_facts.append(f"Python dependencies: {', '.join(chunk.dependencies[:8])}")
    if chunk.detected_frameworks:
        chunk.summary_facts.append(f"Frameworks: {', '.join(chunk.detected_frameworks[:8])}")


def _extract_pyproject_metadata(chunk: Chunk, content: str) -> None:
    chunk.file_type = "pyproject"
    try:
        payload = tomllib.loads(content)
    except Exception:
        return
    project = payload.get("project") or {}
    build_system = payload.get("build-system") or {}
    name = str(project.get("name", "")).strip()
    deps: list[str] = []
    for item in project.get("dependencies") or []:
        deps.append(_normalize_dependency_name(str(item)))
    optional = project.get("optional-dependencies") or {}
    dev_deps: list[str] = []
    for values in optional.values():
        for item in values or []:
            dev_deps.append(_normalize_dependency_name(str(item)))
    poetry = ((payload.get("tool") or {}).get("poetry") or {})
    if poetry:
        deps.extend(str(key) for key in (poetry.get("dependencies") or {}) if key.lower() != "python")
        dev_deps.extend(str(key) for key in ((poetry.get("group") or {}).get("dev") or {}).get("dependencies") or {})
    chunk.dependencies = _dedupe(deps)
    chunk.dev_dependencies = _dedupe(dev_deps)
    chunk.build_system = ", ".join(str(item) for item in build_system.get("requires") or [])
    chunk.config_tools = _detect_pyproject_tools(payload)
    chunk.detected_frameworks = _detect_frameworks(chunk.dependencies + chunk.dev_dependencies)
    if name:
        chunk.summary_facts.append(f"Project: {name}")
    if chunk.dependencies:
        chunk.summary_facts.append(f"Dependencies: {', '.join(chunk.dependencies[:8])}")
    if chunk.dev_dependencies:
        chunk.summary_facts.append(f"Dev dependencies: {', '.join(chunk.dev_dependencies[:8])}")
    if chunk.build_system:
        chunk.summary_facts.append(f"Build system: {chunk.build_system}")
    if chunk.config_tools:
        chunk.summary_facts.append(f"Config tools: {', '.join(chunk.config_tools[:8])}")


def _extract_docker_compose_metadata(chunk: Chunk, content: str) -> None:
    chunk.file_type = "docker_compose"
    service_blocks = _compose_service_blocks(content)
    chunk.services = list(service_blocks)
    service_dependencies: dict[str, list[str]] = {}
    for service, lines in service_blocks.items():
        service_dependencies[service] = _compose_list_values(lines, "depends_on")
        chunk.ports.extend(_compose_list_values(lines, "ports"))
        chunk.volumes.extend(_compose_list_values(lines, "volumes"))
        chunk.env_keys.extend(_compose_env_keys(lines))
    chunk.service_dependencies = {
        key: value for key, value in service_dependencies.items() if value
    }
    chunk.ports = _dedupe(chunk.ports)
    chunk.volumes = _dedupe(chunk.volumes)
    chunk.env_keys = _dedupe(chunk.env_keys)
    if chunk.services:
        chunk.summary_facts.append(f"Services: {', '.join(chunk.services[:8])}")
    if chunk.ports:
        chunk.summary_facts.append(f"Ports: {', '.join(chunk.ports[:8])}")
    if chunk.env_keys:
        chunk.summary_facts.append(f"Environment keys: {', '.join(chunk.env_keys[:8])}")
    if chunk.volumes:
        chunk.summary_facts.append(f"Volumes: {', '.join(chunk.volumes[:8])}")


def _extract_dockerfile_metadata(chunk: Chunk, content: str) -> None:
    chunk.file_type = "dockerfile"
    for line in content.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("FROM ") and not chunk.base_image:
            chunk.base_image = stripped[5:].strip()
        elif upper.startswith("WORKDIR "):
            chunk.workdir = stripped[8:].strip()
        elif upper.startswith("EXPOSE "):
            chunk.ports.extend(stripped[7:].split())
        elif upper.startswith(("CMD ", "ENTRYPOINT ")):
            chunk.entrypoints.append(stripped)
        if "npm install" in stripped or "npm ci" in stripped:
            chunk.package_manager = "npm"
        elif "pnpm install" in stripped:
            chunk.package_manager = "pnpm"
        elif "yarn install" in stripped:
            chunk.package_manager = "yarn"
        elif "pip install" in stripped:
            chunk.package_manager = "pip"
    if chunk.base_image:
        chunk.summary_facts.append(f"Base image: {chunk.base_image}")
    if chunk.workdir:
        chunk.summary_facts.append(f"Workdir: {chunk.workdir}")
    if chunk.ports:
        chunk.summary_facts.append(f"Ports: {', '.join(_dedupe(chunk.ports)[:8])}")
    if chunk.entrypoints:
        chunk.summary_facts.append(f"Entrypoints: {', '.join(chunk.entrypoints[:4])}")
    if chunk.package_manager:
        chunk.summary_facts.append(f"Package manager: {chunk.package_manager}")


def _extract_env_example_metadata(chunk: Chunk, content: str) -> None:
    chunk.file_type = "env_example"
    keys = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        keys.append(key)
    chunk.env_keys = _dedupe(keys)
    chunk.feature_flags = [key for key in chunk.env_keys if key.endswith(("_ENABLED", "_ENABLE")) or "ENABLE" in key or "FEATURE" in key]
    chunk.provider_keys = [key for key in chunk.env_keys if any(term in key for term in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))]
    if chunk.env_keys:
        chunk.summary_facts.append(f"Environment keys: {', '.join(chunk.env_keys[:8])}")
    if chunk.feature_flags:
        chunk.summary_facts.append(f"Feature flags: {', '.join(chunk.feature_flags[:8])}")
    if chunk.provider_keys:
        chunk.summary_facts.append(f"Provider/secret keys: {', '.join(chunk.provider_keys[:8])}")


def _readme_sections(content: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = "root"
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            current = stripped.lstrip("#").strip().lower()
            sections.setdefault(current, [])
            continue
        if stripped:
            sections.setdefault(current, []).append(stripped)
    return sections


def _section_lines(sections: dict[str, list[str]], names: tuple[str, ...], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for heading, values in sections.items():
        if any(name in heading for name in names):
            for value in values:
                cleaned = value.strip("-*` ")
                if cleaned and not cleaned.startswith("```"):
                    lines.append(cleaned)
                if len(lines) >= limit:
                    return lines
    return lines


def _section_commands(sections: dict[str, list[str]], names: tuple[str, ...]) -> list[str]:
    commands = []
    for line in _section_lines(sections, names, limit=16):
        cleaned = line.strip("` ")
        if re.search(r"\b(npm|pnpm|yarn|pip|uv|python|docker|docker compose|pytest|uvicorn)\b", cleaned):
            commands.append(cleaned)
    return _dedupe(commands)


def _detect_frameworks(names: list[str]) -> list[str]:
    mapping = {
        "fastapi": "FastAPI",
        "flask": "Flask",
        "django": "Django",
        "react": "React",
        "next": "Next.js",
        "next.js": "Next.js",
        "vite": "Vite",
        "vue": "Vue",
        "svelte": "Svelte",
        "express": "Express",
        "tailwindcss": "Tailwind CSS",
        "qdrant-client": "Qdrant",
        "sentence-transformers": "Sentence Transformers",
    }
    detected = []
    for name in names:
        lowered = str(name).lower()
        if lowered in mapping:
            detected.append(mapping[lowered])
    return _dedupe(detected)


def _detect_config_tools(names: list[str]) -> list[str]:
    tools = []
    for name in names:
        lowered = str(name).lower()
        if lowered in {"vite", "webpack", "eslint", "prettier", "tailwindcss", "typescript", "pytest", "ruff", "mypy"}:
            tools.append(lowered)
    return _dedupe(tools)


def _detect_pyproject_tools(payload: dict) -> list[str]:
    tool = payload.get("tool") or {}
    tools = list(tool)
    build_system = payload.get("build-system") or {}
    for item in build_system.get("requires") or []:
        tools.append(_normalize_dependency_name(str(item)))
    return _dedupe(tools)


def _normalize_dependency_name(value: str) -> str:
    return (
        value.split("[", 1)[0]
        .split("==", 1)[0]
        .split(">=", 1)[0]
        .split("<=", 1)[0]
        .split("~=", 1)[0]
        .strip()
    )


def _compose_service_blocks(content: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    current = ""
    in_services = False
    for line in content.splitlines():
        if not in_services:
            if line.strip() == "services:":
                in_services = True
            continue
        if not line.strip():
            continue
        if not line.startswith("  "):
            break
        if line.startswith("  ") and not line.startswith("    ") and ":" in line:
            current = line.strip().rstrip(":")
            blocks[current] = []
            continue
        if current:
            blocks[current].append(line)
    return blocks


def _compose_list_values(lines: list[str], key: str) -> list[str]:
    values: list[str] = []
    in_key = False
    for line in lines:
        stripped = line.strip().strip("'\"")
        if stripped.startswith(f"{key}:"):
            remainder = stripped.split(":", 1)[1].strip()
            if remainder and remainder != "[]":
                values.extend(_inline_list_values(remainder))
            in_key = True
            continue
        if in_key:
            if not line.startswith("      "):
                in_key = False
                continue
            item = stripped.lstrip("-").strip().strip("'\"")
            if item:
                values.append(item)
    return _dedupe(values)


def _inline_list_values(value: str) -> list[str]:
    stripped = value.strip().strip("[]")
    if not stripped:
        return []
    return [part.strip().strip("'\"") for part in stripped.split(",") if part.strip()]


def _compose_env_keys(lines: list[str]) -> list[str]:
    keys: list[str] = []
    in_env = False
    for line in lines:
        stripped = line.strip().strip("'\"")
        if stripped.startswith("environment:"):
            remainder = stripped.split(":", 1)[1].strip()
            if remainder and remainder.startswith("["):
                keys.extend(item.split("=", 1)[0] for item in _inline_list_values(remainder))
            in_env = True
            continue
        if in_env:
            if not line.startswith("      "):
                in_env = False
                continue
            item = stripped.lstrip("-").strip().strip("'\"")
            if "=" in item:
                keys.append(item.split("=", 1)[0].strip())
            elif ":" in item:
                keys.append(item.split(":", 1)[0].strip())
            elif item:
                keys.append(item)
    return _dedupe(keys)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(cleaned)
    return result
