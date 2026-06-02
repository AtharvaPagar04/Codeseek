import unittest
from unittest.mock import patch

from retrieval.searcher import _inject_overview_candidates, _overview_priority, search


class SearcherOverviewTests(unittest.TestCase):
    def test_overview_priority_prefers_representative_files(self) -> None:
        readme = {"relative_path": "README.md", "symbol_name": "README", "chunk_type": "file_summary"}
        component = {"relative_path": "src/components/Skills.tsx", "symbol_name": "Skills", "chunk_type": "function"}
        test_file = {"relative_path": "tests/test_skills.py", "symbol_name": "test_skills", "chunk_type": "function"}

        self.assertGreater(_overview_priority(readme), _overview_priority(component))
        self.assertLess(_overview_priority(test_file), 0)

    def test_inject_overview_candidates_appends_unique_candidates(self) -> None:
        current = [{"chunk_id": "1", "relative_path": "src/App.tsx"}]
        overview = [
            {"chunk_id": "1", "relative_path": "src/App.tsx"},
            {"chunk_id": "2", "relative_path": "README.md"},
        ]

        with patch("retrieval.searcher._repository_overview_candidates", return_value=overview):
            merged = _inject_overview_candidates(current)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[1]["chunk_id"], "2")

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


if __name__ == "__main__":
    unittest.main()
