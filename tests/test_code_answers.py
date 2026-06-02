import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from retrieval.code_answers import (
    build_code_answer,
    find_supporting_import_export,
    find_supporting_import_exports,
    is_code_request,
    is_explanation_request,
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
            ) as generate_answer:
                answer, sources, token_count = run_query("show me the code", memory)

            self.assertIn("Code snippets from retrieved context:", answer)
            self.assertEqual(sources, [source])
            self.assertEqual(token_count, 12)
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
                "retrieval.main.select_sources_for_display", return_value=[source]
            ), patch(
                "retrieval.main.generate_answer", return_value="ok"
            ) as generate_answer:
                answer, sources, token_count = run_query(
                    "what are the skills mentioned in skill section",
                    memory,
                )

            self.assertEqual(answer, "ok")
            self.assertEqual(token_count, 12)
            self.assertEqual(sources[0]["symbol_name"], "Skills")
            self.assertTrue(any(src["symbol_name"] == "skillCategories" for src in sources))
            _, kwargs = generate_answer.call_args
            self.assertTrue(any(src["symbol_name"] == "skillCategories" for src in kwargs["allowed_sources"]))
            self.assertTrue(kwargs["extra_context_blocks"])


if __name__ == "__main__":
    unittest.main()
