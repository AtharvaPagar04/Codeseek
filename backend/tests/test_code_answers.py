import os
import sys
import tempfile
import textwrap
import unittest
import types
from importlib.machinery import ModuleSpec
from pathlib import Path
from unittest.mock import patch

fake_tiktoken = types.ModuleType("tiktoken")
fake_tiktoken.__spec__ = ModuleSpec("tiktoken", loader=None)


class _FakeEncoding:
    def encode(self, text):
        return list(text.encode("utf-8"))

    def decode(self, tokens):
        return bytes(tokens).decode("utf-8", errors="ignore")


fake_tiktoken.get_encoding = lambda _name: _FakeEncoding()
sys.modules.setdefault("tiktoken", fake_tiktoken)

from retrieval.code_answers import (
    build_architecture_answer,
    build_code_answer,
    build_explanation_answer,
    build_flow_answer,
    build_overview_answer,
    find_supporting_import_export,
    find_supporting_import_exports,
    is_architecture_request,
    is_code_request,
    is_explanation_request,
    is_flow_explanation_request,
    is_overview_request,
)
from retrieval.llm import _build_prompt
from retrieval.main import run_query
from retrieval.memory import ConversationMemory


class CodeAnswerTests(unittest.TestCase):
    def test_detects_explicit_code_request(self) -> None:
        self.assertTrue(is_code_request("i want the code"))
        self.assertTrue(is_code_request("show me a code snippet for the contact section"))
        self.assertTrue(is_code_request("give me the full code for the contact section"))
        self.assertFalse(is_code_request("what is this project about"))
        self.assertFalse(is_code_request("need a detailed explanation of the code section"))
        self.assertFalse(is_code_request("explain this code section"))
        self.assertTrue(is_explanation_request("need a detailed explanation of the code section"))
        self.assertTrue(is_explanation_request("explain the code in skill section"))
        self.assertTrue(is_overview_request("what is this project about"))
        self.assertTrue(is_overview_request("tech stack"))
        self.assertTrue(is_architecture_request("architecture overview"))
        self.assertTrue(is_architecture_request("how is this project structured"))
        self.assertTrue(is_flow_explanation_request("explain the auth session lifecycle"))
        self.assertTrue(is_flow_explanation_request("trace the indexing session creation flow"))
        self.assertTrue(is_flow_explanation_request("walk me through backend request orchestration"))
        self.assertTrue(is_flow_explanation_request("how does deployment configuration work"))
        self.assertTrue(is_flow_explanation_request("explain provider credential lifecycle"))
        self.assertFalse(is_flow_explanation_request("what is this project about"))

    def test_prompt_includes_code_mode_when_requested(self) -> None:
        prompt = _build_prompt(
            raw_query="show me the code for the contact section",
            context="const x = 1;",
            history_block="",
            allowed_sources=[],
        )
        self.assertIn("--- RESPONSE MODE ---", prompt)
        self.assertIn("The user explicitly asked for code.", prompt)

    def test_prompt_includes_explanation_mode_when_requested(self) -> None:
        prompt = _build_prompt(
            raw_query="give me a detailed explanation of the skills section",
            context="const x = 1;",
            history_block="",
            allowed_sources=[],
        )
        self.assertIn("--- RESPONSE MODE ---", prompt)
        self.assertIn("The user asked for an explanation, not a raw code dump.", prompt)

    def test_prompt_includes_overview_mode_when_requested(self) -> None:
        prompt = _build_prompt(
            raw_query="what is this project about",
            context="const x = 1;",
            history_block="",
            allowed_sources=[],
        )
        self.assertIn("--- RESPONSE MODE ---", prompt)
        self.assertIn("The user wants a grounded project overview.", prompt)

    def test_build_code_answer_includes_component_and_supporting_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/lib").mkdir(parents=True)
            (repo_root / "src/components/Skills.tsx").write_text(
                textwrap.dedent(
                    """
                    import { skillCategories } from "@/lib/data";

                    export default function Skills() {
                        return (
                            <section id="skills">
                                {skillCategories.map((cat) => (
                                    <span key={cat.title}>{cat.title}</span>
                                ))}
                            </section>
                        );
                    }
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (repo_root / "src/lib/data.ts").write_text(
                textwrap.dedent(
                    """
                    export const skillCategories = [
                        { title: "Programming Languages", skills: ["Java", "Python"] },
                    ];
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            source = {
                "relative_path": "src/components/Skills.tsx",
                "symbol_name": "Skills",
                "start_line": 3,
                "end_line": 10,
                "expansion_type": "primary",
            }
            chunk = dict(source)
            chunk["imports"] = ['import { skillCategories } from "@/lib/data";']

            with patch.dict(os.environ, {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False):
                answer = build_code_answer("show me the code snippet for the skills section", [source], [chunk])

            self.assertIn("src/components/Skills.tsx :: Skills", answer)
            self.assertIn("export default function Skills()", answer)
            self.assertIn("src/lib/data.ts :: skillCategories", answer)
            self.assertIn("export const skillCategories = [", answer)

    def test_build_overview_answer_extracts_summary_and_tech_stack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "README.md").write_text(
                "# Codeseek\nRepository-grounded assistant for source code search and answers.\n",
                encoding="utf-8",
            )
            (repo_root / "package.json").write_text(
                json_text := textwrap.dedent(
                    """
                    {
                      "name": "codeseek-frontend",
                      "description": "Frontend for repository-grounded answers",
                      "dependencies": {
                        "react": "^18.0.0",
                        "react-router-dom": "^6.0.0"
                      },
                      "devDependencies": {
                        "vite": "^5.0.0",
                        "tailwindcss": "^3.0.0"
                      }
                    }
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            self.assertTrue(json_text)
            sources = [
                {"relative_path": "README.md", "symbol_name": "README", "start_line": 1, "end_line": 2, "expansion_type": "primary"},
                {"relative_path": "package.json", "symbol_name": "package_json", "start_line": 1, "end_line": 12, "expansion_type": "primary"},
            ]

            with patch.dict(os.environ, {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False):
                answer = build_overview_answer("what is this project about", sources, [])

            self.assertIn("Repository-grounded assistant for source code search and answers.", answer)
            self.assertIn("Tech stack: React, React Router, Vite, Tailwind CSS.", answer)
            self.assertIn("Sources:", answer)

    def test_build_overview_answer_extracts_python_stack_from_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "README.md").write_text(
                "# Retrieval API\nFastAPI service for repository-grounded answers.\n",
                encoding="utf-8",
            )
            (repo_root / "requirements.txt").write_text(
                "fastapi==0.116.1\nuvicorn==0.35.0\nhttpx==0.28.1\nqdrant-client==1.15.1\n",
                encoding="utf-8",
            )
            sources = [
                {"relative_path": "README.md", "symbol_name": "README", "start_line": 1, "end_line": 2, "expansion_type": "primary"},
                {"relative_path": "requirements.txt", "symbol_name": "requirements", "start_line": 1, "end_line": 4, "expansion_type": "primary"},
            ]

            with patch.dict(os.environ, {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False):
                answer = build_overview_answer("tech stack", sources, [])

            self.assertIn("FastAPI service for repository-grounded answers.", answer)
            self.assertIn("Tech stack: FastAPI, Uvicorn, HTTPX, Qdrant.", answer)

    def test_build_overview_answer_uses_structured_file_summaries(self) -> None:
        sources = [
            {
                "relative_path": "README.md",
                "symbol_name": "README",
                "start_line": 1,
                "end_line": 2,
                "expansion_type": "primary",
                "summary": "Overview: Codeseek indexes repositories and answers questions with cited evidence",
            },
            {
                "relative_path": "docker-compose.yml",
                "symbol_name": "<file>",
                "start_line": 1,
                "end_line": 10,
                "expansion_type": "primary",
                "summary": "File: docker-compose.yml\nServices: codeseek-api, postgres, qdrant",
            },
            {
                "relative_path": ".env.example",
                "symbol_name": "<file>",
                "start_line": 1,
                "end_line": 10,
                "expansion_type": "primary",
                "summary": "File: .env.example\nEnvironment keys: CODESEEK_API_KEY, CODESEEK_DATABASE_URL, CODESEEK_FRONTEND_URL",
            },
        ]

        answer = build_overview_answer("architecture overview", sources, sources)

        self.assertIn("Codeseek indexes repositories and answers questions with cited evidence.", answer)
        self.assertIn("codeseek-api, postgres, qdrant", answer)
        self.assertIn("CODESEEK_API_KEY", answer)

    def test_build_overview_answer_prefers_repo_summary_source(self) -> None:
        sources = [
            {
                "relative_path": "__repo_summary__.md",
                "symbol_name": "repo_summary",
                "chunk_type": "repo_summary",
                "file_type": "repo_summary",
                "start_line": 1,
                "end_line": 12,
                "purpose": "CodeSeek indexes repositories and answers questions with cited evidence",
                "detected_frameworks": ["FastAPI", "React"],
                "dependencies": ["qdrant-client"],
                "services": ["api", "qdrant"],
                "env_keys": ["CODESEEK_DATABASE_URL"],
                "entrypoints": ["retrieval.api_service:app"],
                "summary": "Purpose: CodeSeek indexes repositories and answers questions with cited evidence",
                "expansion_type": "primary",
            },
            {
                "relative_path": "README.md",
                "symbol_name": "README",
                "start_line": 1,
                "end_line": 2,
                "summary": "Overview: lower priority summary",
                "expansion_type": "primary",
            },
        ]

        answer = build_overview_answer("what is this project about", sources, sources)

        self.assertIn("CodeSeek indexes repositories and answers questions with cited evidence.", answer)
        self.assertIn("Tech stack: FastAPI, React, Qdrant.", answer)
        self.assertIn("Runtime services summarized for this repo: api, qdrant.", answer)

    def test_build_architecture_answer_uses_structured_repo_evidence(self) -> None:
        sources = [
            {
                "relative_path": "__repo_summary__.md",
                "symbol_name": "repo_summary",
                "chunk_type": "repo_summary",
                "file_type": "repo_summary",
                "start_line": 1,
                "end_line": 12,
                "purpose": "CodeSeek indexes repositories and answers questions with cited evidence",
                "detected_frameworks": ["FastAPI", "React"],
                "services": ["api", "postgres", "qdrant"],
                "env_keys": ["CODESEEK_DATABASE_URL"],
                "entrypoints": ["retrieval.api_service:app"],
                "summary": "Purpose: CodeSeek indexes repositories and answers questions with cited evidence",
                "expansion_type": "primary",
            },
            {
                "relative_path": "docker-compose.yml",
                "symbol_name": "docker-compose.yml",
                "start_line": 1,
                "end_line": 64,
                "summary": "Services: postgres, qdrant, codeseek-api",
                "expansion_type": "primary",
            },
            {
                "relative_path": ".env.example",
                "symbol_name": ".env.example",
                "start_line": 1,
                "end_line": 16,
                "summary": "Environment keys: CODESEEK_DATABASE_URL, CODESEEK_CORS_ORIGINS",
                "expansion_type": "primary",
            },
        ]

        answer = build_architecture_answer("architecture overview", sources, sources)

        self.assertIn("Architecture Summary", answer)
        self.assertIn("Runtime Shape:", answer)
        self.assertIn("Runtime services are summarized as: api, postgres, qdrant.", answer)
        self.assertIn("Entrypoints surfaced by repo summary: retrieval.api_service:app.", answer)
        self.assertIn("Configuration boundary includes env keys such as: CODESEEK_DATABASE_URL.", answer)

    def test_build_flow_answer_explains_auth_session_lifecycle(self) -> None:
        sources = [
            {
                "relative_path": "retrieval/api_service.py",
                "symbol_name": "auth_github",
                "start_line": 1093,
                "end_line": 1124,
                "summary": "Function: auth_github",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/auth_store.py",
                "symbol_name": "create_auth_session",
                "start_line": 100,
                "end_line": 128,
                "summary": "Function: create_auth_session",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/auth_store.py",
                "symbol_name": "get_user_for_session_token",
                "start_line": 130,
                "end_line": 154,
                "summary": "Function: get_user_for_session_token",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/auth_store.py",
                "symbol_name": "delete_auth_session",
                "start_line": 157,
                "end_line": 164,
                "summary": "Function: delete_auth_session",
                "expansion_type": "primary",
            },
        ]

        answer = build_flow_answer("explain the auth session lifecycle", sources, sources)

        self.assertIn("Auth And Session Lifecycle (strong evidence)", answer)
        self.assertIn("Lifecycle:", answer)
        self.assertIn("**Auth entrypoint** - Auth entrypoints exchange or validate GitHub credentials", answer)
        self.assertIn("**Session creation** - `create_auth_session()` stores a hashed auth session token", answer)
        self.assertIn("**Logout/session deletion** - Logout deletes the auth session", answer)
        self.assertIn("Evidence: `retrieval/auth_store.py :: create_auth_session` lines 100-128.", answer)
        self.assertNotIn("Key evidence:", answer)
        self.assertNotIn("Sources:", answer)

    def test_build_flow_answer_returns_only_selected_flow_sources(self) -> None:
        sources = [
            {
                "relative_path": "DB_IMPLEMENTATION_PLAN.md",
                "symbol_name": "DB_IMPLEMENTATION_PLAN",
                "start_line": 1,
                "end_line": 503,
                "summary": "Broad implementation notes",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/api_service.py",
                "symbol_name": "auth_github",
                "start_line": 1093,
                "end_line": 1124,
                "summary": "Function: auth_github",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/auth_store.py",
                "symbol_name": "create_auth_session",
                "start_line": 100,
                "end_line": 128,
                "summary": "Function: create_auth_session",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/auth_store.py",
                "symbol_name": "get_user_for_session_token",
                "start_line": 130,
                "end_line": 154,
                "summary": "Function: get_user_for_session_token",
                "expansion_type": "primary",
            },
        ]

        answer, selected = build_flow_answer(
            "how does authentication cookie lifecycle work",
            sources,
            sources,
            return_sources=True,
        )

        self.assertIn("Auth And Session Lifecycle (strong evidence)", answer)
        self.assertEqual(
            [
                "retrieval/api_service.py",
                "retrieval/auth_store.py",
                "retrieval/auth_store.py",
            ],
            [source["relative_path"] for source in selected],
        )

    def test_build_flow_answer_does_not_confuse_auth_session_with_repo_session(self) -> None:
        sources = [
            {
                "relative_path": "retrieval/api_service.py",
                "symbol_name": "create_session_v1",
                "start_line": 758,
                "end_line": 790,
                "summary": "Function: create_session_v1",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/session_indexer.py",
                "symbol_name": "_update_session",
                "start_line": 162,
                "end_line": 199,
                "summary": "Function: _update_session",
                "expansion_type": "primary",
            },
        ]

        answer = build_flow_answer("explain authentication session lifecycle", sources, sources)

        self.assertIn("Auth And Session Lifecycle (weak evidence)", answer)
        self.assertIn("Missing expected evidence roles: Auth entrypoint, Session creation, Session lookup.", answer)
        self.assertNotIn("creates or reuses a session record", answer)

    def test_build_flow_answer_explains_indexing_session_creation(self) -> None:
        sources = [
            {
                "relative_path": "retrieval/session_indexer.py",
                "symbol_name": "create_session",
                "start_line": 101,
                "end_line": 153,
                "summary": "Function: create_session",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/session_indexer.py",
                "symbol_name": "_index_job",
                "start_line": 313,
                "end_line": 358,
                "summary": "Function: _index_job",
                "expansion_type": "primary",
            },
            {
                "relative_path": "rag_ingestion/main.py",
                "symbol_name": "run_pipeline",
                "start_line": 39,
                "end_line": 103,
                "summary": "Function: run_pipeline",
                "expansion_type": "primary",
            },
        ]

        answer = build_flow_answer("trace the indexing session creation flow", sources, sources)

        self.assertIn("Indexing And Session Creation Flow (strong evidence)", answer)
        self.assertIn("**Session creation** - `create_session()` normalizes repo identity", answer)
        self.assertIn("**Indexing job** - `_index_job()` clones or pulls the repo", answer)
        self.assertIn("**Ingestion pipeline** - The ingestion pipeline parses files", answer)

    def test_build_flow_answer_explains_deployment_configuration(self) -> None:
        sources = [
            {
                "relative_path": "docker-compose.yml",
                "symbol_name": "docker-compose.yml",
                "start_line": 1,
                "end_line": 60,
                "summary": "Services: postgres, qdrant, codeseek-api",
                "expansion_type": "primary",
            },
            {
                "relative_path": "Dockerfile",
                "symbol_name": "Dockerfile",
                "start_line": 1,
                "end_line": 16,
                "summary": "Base image: python:3.11-slim",
                "expansion_type": "primary",
            },
            {
                "relative_path": ".env.example",
                "symbol_name": ".env.example",
                "start_line": 1,
                "end_line": 20,
                "summary": "Environment keys: CODESEEK_DATABASE_URL, CODESEEK_CORS_ORIGINS",
                "expansion_type": "primary",
            },
            {
                "relative_path": "docs/deployment_runbook.md",
                "symbol_name": "deployment_runbook",
                "start_line": 1,
                "end_line": 80,
                "summary": "Deployment runbook",
                "expansion_type": "primary",
            },
        ]

        answer = build_flow_answer("how does deployment configuration work", sources, sources)

        self.assertIn("Deployment And Configuration Flow (strong evidence)", answer)
        self.assertIn("**Runtime services** - Docker Compose defines the runtime services", answer)
        self.assertIn("**Backend container** - The backend Dockerfile builds the Python runtime", answer)
        self.assertIn("**Environment contract** - The environment template documents required secrets", answer)
        self.assertIn("Evidence: `docker-compose.yml :: docker-compose.yml` lines 1-60.", answer)

    def test_build_flow_answer_explains_deployment_configuration_with_monorepo_paths(self) -> None:
        sources = [
            {
                "relative_path": "backend/docker-compose.yml",
                "symbol_name": "docker-compose.yml",
                "start_line": 1,
                "end_line": 60,
                "summary": "Services: postgres, qdrant, codeseek-api",
                "expansion_type": "primary",
            },
            {
                "relative_path": "backend/Dockerfile",
                "symbol_name": "Dockerfile",
                "start_line": 1,
                "end_line": 16,
                "summary": "Base image: python:3.11-slim",
                "expansion_type": "primary",
            },
            {
                "relative_path": "backend/.env.example",
                "symbol_name": ".env.example",
                "start_line": 1,
                "end_line": 20,
                "summary": "Environment keys: CODESEEK_DATABASE_URL, CODESEEK_CORS_ORIGINS",
                "expansion_type": "primary",
            },
        ]

        answer = build_flow_answer("how does deployment configuration work", sources, sources)

        self.assertIn("Deployment And Configuration Flow (strong evidence)", answer)
        self.assertIn("Evidence: `backend/docker-compose.yml :: docker-compose.yml` lines 1-60.", answer)

    def test_build_flow_answer_explains_provider_credential_lifecycle(self) -> None:
        sources = [
            {
                "relative_path": "retrieval/api_service.py",
                "symbol_name": "list_provider_credentials_v1",
                "start_line": 684,
                "end_line": 691,
                "summary": "Function: list_provider_credentials_v1",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/api_service.py",
                "symbol_name": "create_provider_credential_v1",
                "start_line": 694,
                "end_line": 726,
                "summary": "Function: create_provider_credential_v1",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/provider_store.py",
                "symbol_name": "create_provider_credential",
                "start_line": 62,
                "end_line": 116,
                "summary": "Function: create_provider_credential",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/provider_store.py",
                "symbol_name": "set_active_provider_credential",
                "start_line": 119,
                "end_line": 140,
                "summary": "Function: set_active_provider_credential",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/provider_store.py",
                "symbol_name": "delete_provider_credential",
                "start_line": 143,
                "end_line": 152,
                "summary": "Function: delete_provider_credential",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/provider_store.py",
                "symbol_name": "get_active_provider_credential",
                "start_line": 45,
                "end_line": 59,
                "summary": "Function: get_active_provider_credential",
                "expansion_type": "primary",
            },
        ]

        answer = build_flow_answer("explain provider credential lifecycle", sources, sources)

        self.assertIn("Provider Credential Lifecycle (strong evidence)", answer)
        self.assertIn("**Create credential API** - The create endpoint validates provider", answer)
        self.assertIn("**Credential storage** - `create_provider_credential()` encrypts the API key", answer)
        self.assertIn("**Query-time lookup** - Query execution requires an active provider credential", answer)

    def test_build_explanation_answer_mentions_rendering_and_backing_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/lib").mkdir(parents=True)
            (repo_root / "src/components/Skills.tsx").write_text(
                textwrap.dedent(
                    """
                    import { skillCategories } from "@/lib/data";

                    export default function Skills() {
                        return (
                            <section id="skills">
                                {skillCategories.map((cat) => (
                                    <span key={cat.title}>{cat.title}</span>
                                ))}
                            </section>
                        );
                    }
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (repo_root / "src/lib/data.ts").write_text(
                textwrap.dedent(
                    """
                    export const skillCategories = [
                        { title: "Programming Languages", skills: ["Java", "Python"] },
                        { title: "Frameworks", skills: ["React", "FastAPI"] }
                    ];
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            source = {
                "relative_path": "src/components/Skills.tsx",
                "symbol_name": "Skills",
                "start_line": 3,
                "end_line": 10,
                "expansion_type": "primary",
            }
            chunk = dict(source)
            chunk["imports"] = ['import { skillCategories } from "@/lib/data";']

            with patch.dict(os.environ, {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False):
                answer = build_explanation_answer(
                    "give me a detailed explanation of the skills section",
                    [source],
                    [chunk],
                )

            self.assertIn("Skills is implemented in src/components/Skills.tsx", answer)
            self.assertIn("Backing data: src/lib/data.ts :: skillCategories", answer)
            self.assertIn("Programming Languages", answer)
            self.assertIn("Sources:", answer)

    def test_supporting_import_export_detects_backing_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/lib").mkdir(parents=True)
            (repo_root / "src/components/Skills.tsx").write_text(
                'import { skillCategories } from "@/lib/data";\nexport default function Skills() { return null; }\n',
                encoding="utf-8",
            )
            (repo_root / "src/lib/data.ts").write_text(
                "export const skillCategories = [{ title: 'Programming Languages' }];\n",
                encoding="utf-8",
            )

            source = {
                "relative_path": "src/components/Skills.tsx",
                "symbol_name": "Skills",
                "start_line": 2,
                "end_line": 2,
                "expansion_type": "primary",
            }
            chunk = dict(source)
            chunk["imports"] = ['import { skillCategories } from "@/lib/data";']

            with patch.dict(os.environ, {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False):
                support = find_supporting_import_export(
                    "give me a detailed explanation of the skills section",
                    [source],
                    [chunk],
                )

            assert support is not None
            self.assertEqual(support["relative_path"], "src/lib/data.ts")
            self.assertEqual(support["symbol_name"], "skillCategories")

    def test_supporting_import_exports_can_return_multiple_backing_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/lib").mkdir(parents=True)
            (repo_root / "src/components/Portfolio.tsx").write_text(
                textwrap.dedent(
                    """
                    import { personal, projects } from "@/lib/data";

                    export default function Portfolio() {
                        return <main>{personal.name}{projects.length}</main>;
                    }
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (repo_root / "src/lib/data.ts").write_text(
                textwrap.dedent(
                    """
                    export const personal = { name: "Atharva Pagar" };
                    export const projects = [{ title: "Portfolio" }];
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            source = {
                "relative_path": "src/components/Portfolio.tsx",
                "symbol_name": "Portfolio",
                "start_line": 3,
                "end_line": 5,
                "expansion_type": "primary",
            }
            chunk = dict(source)
            chunk["imports"] = ['import { personal, projects } from "@/lib/data";']

            with patch.dict(os.environ, {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False):
                supports = find_supporting_import_exports(
                    "what is this project about and show the personal details and projects",
                    [source],
                    [chunk],
                    limit=2,
                )

            self.assertEqual(len(supports), 2)
            self.assertEqual({item["symbol_name"] for item in supports}, {"personal", "projects"})

    def test_run_query_bypasses_llm_for_code_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/lib").mkdir(parents=True)
            (repo_root / "src/components/Skills.tsx").write_text(
                textwrap.dedent(
                    """
                    import { skillCategories } from "@/lib/data";

                    export default function Skills() {
                        return <section id="skills" />;
                    }
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (repo_root / "src/lib/data.ts").write_text(
                "export const skillCategories = [];\n",
                encoding="utf-8",
            )

            source = {
                "relative_path": "src/components/Skills.tsx",
                "symbol_name": "Skills",
                "start_line": 3,
                "end_line": 5,
                "expansion_type": "primary",
            }
            chunk = dict(source)
            chunk["chunk_id"] = "abc"
            chunk["imports"] = ['import { skillCategories } from "@/lib/data";']
            chunk["retrieval_score"] = 1.0

            memory = ConversationMemory(max_turns=2)
            with patch.dict(
                os.environ,
                {
                    "RETRIEVAL_REPO_ROOT": str(repo_root),
                    "QDRANT_COLLECTION_NAME": "repository_chunks__local__tmprepo",
                    "CODESEEK_STRICT_ISOLATION": "0",
                },
                clear=False,
            ), patch("retrieval.main.process_query", return_value={"raw_query": "show me the code", "intent": "SEMANTIC", "entities": {}}), patch(
                "retrieval.main.search", return_value=[chunk]
            ), patch("retrieval.main.expand", return_value=[chunk]), patch(
                "retrieval.main.assemble", return_value=("context", [source], 12)
            ), patch(
                "retrieval.main.select_sources_for_display", return_value=[source]
            ), patch(
                "retrieval.main.generate_answer"
            ) as generate_answer, patch(
                "retrieval.main.score_evidence_confidence",
                return_value={"level": "strong", "count": 1, "has_primary": True, "overlap": 1.0},
            ):
                answer, sources, token_count = run_query("show me the code", memory)

            self.assertIn("Code snippets from retrieved context:", answer)
            self.assertEqual(sources, [source])
            self.assertEqual(token_count, 12)

    def test_run_query_bypasses_llm_for_overview_requests(self) -> None:
        source = {
            "relative_path": "README.md",
            "symbol_name": "README",
            "start_line": 1,
            "end_line": 5,
            "expansion_type": "primary",
        }
        chunk = dict(source)
        chunk["chunk_id"] = "overview-1"
        chunk["retrieval_score"] = 1.0
        memory = ConversationMemory(max_turns=2)

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "README.md").write_text(
                "# Codeseek\nRepository-grounded assistant for source code search and answers.\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "RETRIEVAL_REPO_ROOT": str(repo_root),
                    "QDRANT_COLLECTION_NAME": "repository_chunks__local__tmprepo",
                    "CODESEEK_STRICT_ISOLATION": "0",
                },
                clear=False,
            ), patch("retrieval.main.process_query", return_value={"raw_query": "what is this project about", "intent": "SEMANTIC", "entities": {}}), patch(
                "retrieval.main.search", return_value=[chunk]
            ), patch("retrieval.main.expand", return_value=[chunk]), patch(
                "retrieval.main.assemble", return_value=("context", [source], 12)
            ), patch(
                "retrieval.main.select_sources_for_display", return_value=[source]
            ), patch(
                "retrieval.main.generate_answer"
            ) as generate_answer:
                answer, sources, token_count = run_query("what is this project about", memory)

        self.assertIn("Repository-grounded assistant for source code search and answers.", answer)
        self.assertEqual(sources, [source])
        self.assertEqual(token_count, 12)
        generate_answer.assert_not_called()

    def test_run_query_bypasses_llm_for_architecture_requests(self) -> None:
        source = {
            "relative_path": "__repo_summary__.md",
            "symbol_name": "repo_summary",
            "chunk_type": "repo_summary",
            "file_type": "repo_summary",
            "start_line": 1,
            "end_line": 12,
            "purpose": "CodeSeek indexes repositories and answers questions with cited evidence",
            "services": ["api", "qdrant"],
            "entrypoints": ["retrieval.api_service:app"],
            "expansion_type": "primary",
        }
        chunk = dict(source)
        chunk["chunk_id"] = "architecture-1"
        chunk["retrieval_score"] = 1.0
        memory = ConversationMemory(max_turns=2)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "RETRIEVAL_REPO_ROOT": tmp,
                    "QDRANT_COLLECTION_NAME": "repository_chunks__local__tmprepo",
                    "CODESEEK_STRICT_ISOLATION": "0",
                },
                clear=False,
            ), patch("retrieval.main.process_query", return_value={"raw_query": "architecture overview", "intent": "SEMANTIC", "entities": {}}), patch(
                "retrieval.main.search", return_value=[chunk]
            ), patch("retrieval.main.expand", return_value=[chunk]), patch(
                "retrieval.main.assemble", return_value=("context", [source], 12)
            ), patch(
                "retrieval.main.select_sources_for_display", return_value=[source]
            ), patch(
                "retrieval.main.generate_answer"
            ) as generate_answer:
                answer, sources, token_count, meta = run_query(
                    "architecture overview",
                    memory,
                    return_meta=True,
                )

        self.assertIn("Architecture Summary", answer)
        self.assertEqual(sources, [source])
        self.assertEqual(token_count, 12)
        self.assertEqual(meta["response_mode"], "architecture_summary")
        generate_answer.assert_not_called()

    def test_run_query_bypasses_llm_for_flow_requests(self) -> None:
        sources = [
            {
                "relative_path": "retrieval/session_indexer.py",
                "symbol_name": "create_session",
                "start_line": 101,
                "end_line": 153,
                "summary": "Function: create_session",
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/session_indexer.py",
                "symbol_name": "_index_job",
                "start_line": 313,
                "end_line": 358,
                "summary": "Function: _index_job",
                "expansion_type": "primary",
            },
        ]
        chunks = []
        for index, source in enumerate(sources, start=1):
            chunk = dict(source)
            chunk["chunk_id"] = f"flow-{index}"
            chunk["retrieval_score"] = 1.0
            chunks.append(chunk)
        memory = ConversationMemory(max_turns=2)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "RETRIEVAL_REPO_ROOT": tmp,
                    "QDRANT_COLLECTION_NAME": "repository_chunks__local__tmprepo",
                    "CODESEEK_STRICT_ISOLATION": "0",
                },
                clear=False,
            ), patch("retrieval.main.process_query", return_value={"raw_query": "trace the indexing session creation flow", "intent": "SEMANTIC", "entities": {}}), patch(
                "retrieval.main.search", return_value=chunks
            ), patch("retrieval.main.expand", return_value=chunks), patch(
                "retrieval.main.assemble", return_value=("context", sources, 12)
            ), patch(
                "retrieval.main.select_sources_for_display", return_value=sources
            ), patch(
                "retrieval.main.generate_answer"
            ) as generate_answer:
                answer, returned_sources, token_count, meta = run_query(
                    "trace the indexing session creation flow",
                    memory,
                    return_meta=True,
                )

        self.assertIn("Indexing And Session Creation Flow", answer)
        self.assertEqual(returned_sources, sources)
        self.assertEqual(token_count, 12)
        self.assertEqual(meta["stage_latency_ms"]["search"], 0)
        generate_answer.assert_not_called()

    def test_run_query_includes_supporting_data_for_factual_section_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/lib").mkdir(parents=True)
            (repo_root / "src/components/Skills.tsx").write_text(
                textwrap.dedent(
                    """
                    import { skillCategories } from "@/lib/data";

                    export default function Skills() {
                        return <section id="skills">{skillCategories.length}</section>;
                    }
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (repo_root / "src/lib/data.ts").write_text(
                textwrap.dedent(
                    """
                    export const skillCategories = [
                        { title: "Programming Languages", skills: ["Java", "Python"] },
                    ];
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            source = {
                "relative_path": "src/components/Skills.tsx",
                "symbol_name": "Skills",
                "start_line": 3,
                "end_line": 5,
                "expansion_type": "primary",
            }
            chunk = dict(source)
            chunk["chunk_id"] = "skills-1"
            chunk["imports"] = ['import { skillCategories } from "@/lib/data";']
            chunk["retrieval_score"] = 1.0

            memory = ConversationMemory(max_turns=2)
            with patch.dict(
                os.environ,
                {
                    "RETRIEVAL_REPO_ROOT": str(repo_root),
                    "QDRANT_COLLECTION_NAME": "repository_chunks__local__tmprepo",
                    "CODESEEK_STRICT_ISOLATION": "0",
                },
                clear=False,
            ), patch("retrieval.main.process_query", return_value={"raw_query": "what are the skills mentioned in skill section", "intent": "SEMANTIC", "entities": {}}), patch(
                "retrieval.main.search", return_value=[chunk]
            ), patch("retrieval.main.expand", return_value=[chunk]), patch(
                "retrieval.main.assemble", return_value=("context", [source], 12)
            ), patch(
                "retrieval.main.assemble_for_reasoning", return_value=("reasoning context", [source], 12)
            ), patch(
                "retrieval.main.select_sources_for_display", return_value=[source]
            ), patch(
                "retrieval.main.generate_answer", return_value="ok"
            ) as generate_answer:
                answer, sources, token_count = run_query(
                    "what are the skills mentioned in skill section",
                    memory,
                )
            # The answer may have an evidence-quality banner prepended; the LLM stub returned "ok".
            self.assertIn("ok", answer)

            self.assertEqual(token_count, 12)
            self.assertEqual(sources[0]["symbol_name"], "Skills")
            self.assertTrue(any(src["symbol_name"] == "skillCategories" for src in sources))
            _, kwargs = generate_answer.call_args
            self.assertTrue(any(src["symbol_name"] == "skillCategories" for src in kwargs["allowed_sources"]))
            self.assertTrue(kwargs["extra_context_blocks"])


if __name__ == "__main__":
    unittest.main()
