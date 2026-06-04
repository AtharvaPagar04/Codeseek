import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path

from retrieval.searcher import _inject_overview_candidates, _overview_priority, search


class SearcherOverviewTests(unittest.TestCase):
    def test_overview_priority_prefers_representative_files(self) -> None:
        repo_summary = {"relative_path": "__repo_summary__.md", "chunk_type": "repo_summary", "file_type": "repo_summary"}
        readme = {"relative_path": "README.md", "symbol_name": "README", "chunk_type": "file_summary"}
        package_json = {"relative_path": "package.json", "symbol_name": "package_json", "chunk_type": "file_summary"}
        env_example = {"relative_path": ".env.example", "symbol_name": ".env.example", "chunk_type": "file_summary"}
        component = {"relative_path": "src/components/Skills.tsx", "symbol_name": "Skills", "chunk_type": "function"}
        test_file = {"relative_path": "tests/test_skills.py", "symbol_name": "test_skills", "chunk_type": "function"}

        self.assertGreater(_overview_priority(repo_summary), _overview_priority(readme))
        self.assertGreater(_overview_priority(readme), _overview_priority(component))
        self.assertGreater(_overview_priority(package_json), _overview_priority(component))
        self.assertGreater(_overview_priority(env_example), _overview_priority(component))
        self.assertLess(_overview_priority(test_file), 0)

    def test_inject_overview_candidates_prepends_unique_candidates(self) -> None:
        current = [{"chunk_id": "1", "relative_path": "src/App.tsx"}]
        overview = [
            {"chunk_id": "1", "relative_path": "src/App.tsx"},   # already in current → skip
            {"chunk_id": "2", "relative_path": "README.md", "chunk_type": "file_summary"},
        ]

        with patch("retrieval.searcher._repository_overview_candidates", return_value=overview):
            merged = _inject_overview_candidates(current)

        # unique overview chunk ("2") is PREPENDED before the existing candidate ("1")
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["chunk_id"], "2")
        self.assertEqual(merged[1]["chunk_id"], "1")

    def test_search_injects_overview_candidates_for_project_queries(self) -> None:
        query_info = {"raw_query": "what is this project about", "intent": "SEMANTIC", "entities": {}}
        overview_payload = {
            "chunk_id": "overview-1",
            "relative_path": "README.md",
            "symbol_name": "README",
            "start_line": 1,
            "end_line": 20,
            "chunk_type": "file_summary",
        }

        with patch("retrieval.searcher._dense_search", return_value=[]), patch(
            "retrieval.searcher._metadata_search", return_value=[]
        ), patch("retrieval.searcher._repository_overview_candidates", return_value=[overview_payload]):
            results = search(query_info)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], "overview-1")

    def test_search_injects_import_backing_candidate_for_section_query(self) -> None:
        query_info = {"raw_query": "what skills are listed in the skills section", "intent": "SEMANTIC", "entities": {}}
        component = {
            "chunk_id": "skills-1",
            "relative_path": "src/components/Skills.tsx",
            "symbol_name": "Skills",
            "start_line": 1,
            "end_line": 8,
            "chunk_type": "function",
            "imports": ['import { skillCategories } from "@/lib/data";'],
        }
        backing = {
            "chunk_id": "data-1",
            "relative_path": "src/lib/data.ts",
            "symbol_name": "skillCategories",
            "start_line": 1,
            "end_line": 10,
            "chunk_type": "const",
        }

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/lib").mkdir(parents=True)
            (repo_root / "src/components/Skills.tsx").write_text(
                'import { skillCategories } from "@/lib/data";\nexport default function Skills() { return null; }\n',
                encoding="utf-8",
            )
            (repo_root / "src/lib/data.ts").write_text(
                'export const skillCategories = [{ title: "Programming Languages" }];\n',
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False), patch(
                "retrieval.searcher._dense_search", return_value=[(component, 0.9, "dense")]
            ), patch(
                "retrieval.searcher._metadata_search", return_value=[]
            ), patch(
                "retrieval.searcher._qdrant_call", side_effect=[([type("Hit", (), {"payload": backing})()], None)]
            ):
                results = search(query_info)

        self.assertEqual(results[0]["chunk_id"], "skills-1")
        self.assertTrue(any(item["chunk_id"] == "data-1" for item in results))

    def test_search_injects_import_backing_candidate_for_default_import(self) -> None:
        query_info = {"raw_query": "what is in skills data", "intent": "SEMANTIC", "entities": {}}
        component = {
            "chunk_id": "skills-default-1",
            "relative_path": "src/components/Skills.tsx",
            "symbol_name": "Skills",
            "start_line": 1,
            "end_line": 8,
            "chunk_type": "function",
            "imports": ['import SkillsData from "@/lib/data";'],
        }
        backing = {
            "chunk_id": "data-default-1",
            "relative_path": "src/lib/data.ts",
            "symbol_name": "SkillsData",
            "start_line": 1,
            "end_line": 10,
            "chunk_type": "const",
        }

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/lib").mkdir(parents=True)
            (repo_root / "src/components/Skills.tsx").write_text(
                'import SkillsData from "@/lib/data";\nexport default function Skills() { return null; }\n',
                encoding="utf-8",
            )
            (repo_root / "src/lib/data.ts").write_text(
                'const SkillsData = [{ title: "Programming Languages" }];\nexport default SkillsData;\n',
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False), patch(
                "retrieval.searcher._dense_search", return_value=[(component, 0.9, "dense")]
            ), patch(
                "retrieval.searcher._metadata_search", return_value=[]
            ), patch(
                "retrieval.searcher._exact_entity_search", return_value=[]
            ), patch(
                "retrieval.searcher._qdrant_call", side_effect=[([type("Hit", (), {"payload": backing})()], None)]
            ):
                results = search(query_info)

        self.assertEqual(results[0]["chunk_id"], "skills-default-1")
        self.assertTrue(any(item["chunk_id"] == "data-default-1" for item in results))

    def test_search_injects_import_backing_candidate_for_namespace_import(self) -> None:
        query_info = {"raw_query": "where does data come from", "intent": "SEMANTIC", "entities": {}}
        component = {
            "chunk_id": "skills-namespace-1",
            "relative_path": "src/components/Skills.tsx",
            "symbol_name": "Skills",
            "start_line": 1,
            "end_line": 8,
            "chunk_type": "function",
            "imports": ['import * as data from "@/lib/data";'],
        }
        backing = {
            "chunk_id": "data-namespace-1",
            "relative_path": "src/lib/data.ts",
            "symbol_name": "data",
            "start_line": 1,
            "end_line": 10,
            "chunk_type": "const",
        }

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/lib").mkdir(parents=True)
            (repo_root / "src/components/Skills.tsx").write_text(
                'import * as data from "@/lib/data";\nexport default function Skills() { return null; }\n',
                encoding="utf-8",
            )
            (repo_root / "src/lib/data.ts").write_text(
                'export const data = [{ title: "Programming Languages" }];\n',
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False), patch(
                "retrieval.searcher._dense_search", return_value=[(component, 0.9, "dense")]
            ), patch(
                "retrieval.searcher._metadata_search", return_value=[]
            ), patch(
                "retrieval.searcher._exact_entity_search", return_value=[]
            ), patch(
                "retrieval.searcher._qdrant_call", side_effect=[([type("Hit", (), {"payload": backing})()], None)]
            ):
                results = search(query_info)

        self.assertEqual(results[0]["chunk_id"], "skills-namespace-1")
        self.assertTrue(any(item["chunk_id"] == "data-namespace-1" for item in results))

    def test_search_injects_import_backing_candidate_through_reexport_chain(self) -> None:
        query_info = {"raw_query": "what skills are listed in skills data", "intent": "SEMANTIC", "entities": {}}
        component = {
            "chunk_id": "skills-reexport-1",
            "relative_path": "src/components/Skills.tsx",
            "symbol_name": "Skills",
            "start_line": 1,
            "end_line": 8,
            "chunk_type": "function",
            "imports": ['import { skillCategories } from "@/lib";'],
        }
        backing = {
            "chunk_id": "data-reexport-1",
            "relative_path": "src/lib/data.ts",
            "symbol_name": "skillCategories",
            "start_line": 1,
            "end_line": 10,
            "chunk_type": "const",
        }

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/lib").mkdir(parents=True)
            (repo_root / "src/components/Skills.tsx").write_text(
                'import { skillCategories } from "@/lib";\nexport default function Skills() { return null; }\n',
                encoding="utf-8",
            )
            (repo_root / "src/lib/index.ts").write_text(
                'export { skillCategories } from "./data";\n',
                encoding="utf-8",
            )
            (repo_root / "src/lib/data.ts").write_text(
                'export const skillCategories = [{ title: "Programming Languages" }];\n',
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False), patch(
                "retrieval.searcher._dense_search", return_value=[(component, 0.9, "dense")]
            ), patch(
                "retrieval.searcher._metadata_search", return_value=[]
            ), patch(
                "retrieval.searcher._exact_entity_search", return_value=[]
            ), patch(
                "retrieval.searcher._qdrant_call",
                side_effect=[
                    ([], None),
                    ([type("Hit", (), {"payload": backing})()], None),
                ],
            ):
                results = search(query_info)

        self.assertEqual(results[0]["chunk_id"], "skills-reexport-1")
        self.assertTrue(any(item["chunk_id"] == "data-reexport-1" for item in results))

    def test_search_injects_import_backing_candidate_for_json_config_import(self) -> None:
        query_info = {"raw_query": "what is in app config", "intent": "SEMANTIC", "entities": {}}
        component = {
            "chunk_id": "config-json-1",
            "relative_path": "src/components/ConfigView.tsx",
            "symbol_name": "ConfigView",
            "start_line": 1,
            "end_line": 8,
            "chunk_type": "function",
            "imports": ['import appConfig from "@/config/app.json";'],
        }

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/config").mkdir(parents=True)
            (repo_root / "src/components/ConfigView.tsx").write_text(
                'import appConfig from "@/config/app.json";\nexport default function ConfigView() { return null; }\n',
                encoding="utf-8",
            )
            (repo_root / "src/config/app.json").write_text(
                '{\n  "featureFlag": true,\n  "apiBase": "/v1"\n}\n',
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False), patch(
                "retrieval.searcher._dense_search", return_value=[(component, 0.9, "dense")]
            ), patch(
                "retrieval.searcher._metadata_search", return_value=[]
            ), patch(
                "retrieval.searcher._exact_entity_search", return_value=[]
            ), patch(
                "retrieval.searcher._qdrant_call", side_effect=[([], None)]
            ):
                results = search(query_info)

        self.assertEqual(results[0]["chunk_id"], "config-json-1")
        config_hits = [item for item in results if item["relative_path"] == "src/config/app.json"]
        self.assertEqual(len(config_hits), 1)
        self.assertEqual(config_hits[0]["symbol_name"], "appConfig")
        self.assertEqual(config_hits[0]["file_type"], "json")

    def test_search_does_not_follow_reexport_chain_past_default_depth_limit(self) -> None:
        query_info = {"raw_query": "what skills are listed in skills data", "intent": "SEMANTIC", "entities": {}}
        component = {
            "chunk_id": "skills-depth-1",
            "relative_path": "src/components/Skills.tsx",
            "symbol_name": "Skills",
            "start_line": 1,
            "end_line": 8,
            "chunk_type": "function",
            "imports": ['import { skillCategories } from "@/lib";'],
        }
        backing = {
            "chunk_id": "data-depth-1",
            "relative_path": "src/lib/three.ts",
            "symbol_name": "skillCategories",
            "start_line": 1,
            "end_line": 10,
            "chunk_type": "const",
        }

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/lib").mkdir(parents=True)
            (repo_root / "src/components/Skills.tsx").write_text(
                'import { skillCategories } from "@/lib";\nexport default function Skills() { return null; }\n',
                encoding="utf-8",
            )
            (repo_root / "src/lib/index.ts").write_text(
                'export { skillCategories } from "./one";\n',
                encoding="utf-8",
            )
            (repo_root / "src/lib/one.ts").write_text(
                'export { skillCategories } from "./two";\n',
                encoding="utf-8",
            )
            (repo_root / "src/lib/two.ts").write_text(
                'export { skillCategories } from "./three";\n',
                encoding="utf-8",
            )
            (repo_root / "src/lib/three.ts").write_text(
                'export const skillCategories = [{ title: "Programming Languages" }];\n',
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False), patch(
                "retrieval.searcher._dense_search", return_value=[(component, 0.9, "dense")]
            ), patch(
                "retrieval.searcher._metadata_search", return_value=[]
            ), patch(
                "retrieval.searcher._exact_entity_search", return_value=[]
            ), patch(
                "retrieval.searcher._qdrant_call",
                side_effect=[
                    ([], None),
                    ([], None),
                    ([], None),
                    ([type("Hit", (), {"payload": backing})()], None),
                ],
            ):
                results = search(query_info)

        self.assertEqual(results[0]["chunk_id"], "skills-depth-1")
        self.assertFalse(any(item.get("chunk_id") == "data-depth-1" for item in results))

    def test_search_caps_total_import_backing_candidates(self) -> None:
        query_info = {"raw_query": "what config data is imported", "intent": "SEMANTIC", "entities": {}}
        imports = [f'import config{idx} from "@/config/config{idx}.json";' for idx in range(8)]
        component = {
            "chunk_id": "config-many-1",
            "relative_path": "src/components/ConfigView.tsx",
            "symbol_name": "ConfigView",
            "start_line": 1,
            "end_line": 8,
            "chunk_type": "function",
            "imports": imports,
        }

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "src/components").mkdir(parents=True)
            (repo_root / "src/config").mkdir(parents=True)
            (repo_root / "src/components/ConfigView.tsx").write_text(
                "\n".join(imports) + "\nexport default function ConfigView() { return null; }\n",
                encoding="utf-8",
            )
            for idx in range(8):
                (repo_root / "src/config" / f"config{idx}.json").write_text(
                    '{\n  "featureFlag": true\n}\n',
                    encoding="utf-8",
                )

            qdrant_hits = [
                ([type("Hit", (), {"payload": {
                    "chunk_id": f"json-{idx}",
                    "relative_path": f"src/config/config{idx}.json",
                    "symbol_name": f"config{idx}",
                    "start_line": 1,
                    "end_line": 2,
                    "chunk_type": "file_summary",
                    "file_type": "json",
                }})()], None)
                for idx in range(8)
            ]

            with patch.dict("os.environ", {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False), patch(
                "retrieval.searcher._dense_search", return_value=[(component, 0.9, "dense")]
            ), patch(
                "retrieval.searcher._metadata_search", return_value=[]
            ), patch(
                "retrieval.searcher._exact_entity_search", return_value=[]
            ), patch(
                "retrieval.searcher._qdrant_call", side_effect=qdrant_hits
            ):
                results = search(query_info)

        support_hits = [item for item in results if str(item.get("support_kind", "")) == "import_backing"]
        self.assertEqual(len(support_hits), 6)

    def test_search_injects_python_import_backing_candidate_for_trace_query(self) -> None:
        query_info = {"raw_query": "what is the max context token limit", "intent": "SEMANTIC", "entities": {}}
        caller = {
            "chunk_id": "main-1",
            "relative_path": "retrieval/main.py",
            "symbol_name": "run",
            "start_line": 1,
            "end_line": 4,
            "chunk_type": "function",
            "imports": ["from retrieval.config import MAX_CONTEXT_TOKENS, HISTORY_TOKEN_CAP"],
        }
        backing = {
            "chunk_id": "config-1",
            "relative_path": "retrieval/config.py",
            "symbol_name": "MAX_CONTEXT_TOKENS",
            "start_line": 1,
            "end_line": 1,
            "chunk_type": "const",
        }

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "retrieval").mkdir(parents=True)
            (repo_root / "retrieval/main.py").write_text(
                "from retrieval.config import MAX_CONTEXT_TOKENS, HISTORY_TOKEN_CAP\n\ndef run():\n    return MAX_CONTEXT_TOKENS\n",
                encoding="utf-8",
            )
            (repo_root / "retrieval/config.py").write_text(
                "MAX_CONTEXT_TOKENS = 7000\nHISTORY_TOKEN_CAP = 1500\n",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"RETRIEVAL_REPO_ROOT": str(repo_root)}, clear=False), patch(
                "retrieval.searcher._dense_search", return_value=[(caller, 0.9, "dense")]
            ), patch(
                "retrieval.searcher._metadata_search", return_value=[]
            ), patch(
                "retrieval.searcher._exact_entity_search", return_value=[]
            ), patch(
                "retrieval.searcher._qdrant_call",
                side_effect=[
                    ([type("Hit", (), {"payload": backing})()], None),
                    ([], None),
                ],
            ):
                results = search(query_info)

        self.assertEqual(results[0]["chunk_id"], "main-1")
        self.assertTrue(any(item["chunk_id"] == "config-1" for item in results))


if __name__ == "__main__":
    unittest.main()
