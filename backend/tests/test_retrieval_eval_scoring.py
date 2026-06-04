import unittest

from scripts import retrieval_eval


class RetrievalEvalScoringTests(unittest.TestCase):
    def test_expected_file_score_matches_relative_paths(self) -> None:
        items = [
            {"relative_path": "README.md"},
            {"relative_path": "backend/retrieval/searcher.py"},
        ]

        self.assertEqual(
            retrieval_eval._expected_file_score(items, ["README.md", "backend/retrieval/searcher.py"]),
            1.0,
        )
        self.assertEqual(retrieval_eval._expected_file_score(items, ["missing.py"]), 0.0)

    def test_expected_symbol_score_matches_symbol_names(self) -> None:
        items = [{"symbol_name": "search"}, {"symbol_name": "run_pipeline"}]

        self.assertEqual(retrieval_eval._expected_symbol_score(items, ["run_pipeline"]), 1.0)
        self.assertEqual(retrieval_eval._expected_symbol_score(items, ["create_session"]), 0.0)

    def test_expected_term_score_matches_frameworks_and_dependencies(self) -> None:
        items = [
            {
                "relative_path": "package.json",
                "summary": "Dependencies include react, vite, and lucide-react.",
            }
        ]

        self.assertEqual(retrieval_eval._expected_term_score(items, ["react", "vite"]), 1.0)
        self.assertEqual(retrieval_eval._expected_term_score(items, ["fastapi"]), 0.0)

    def test_expected_term_score_reads_structured_metadata_fields(self) -> None:
        items = [
            {
                "relative_path": "__repo_summary__.md",
                "detected_frameworks": ["React", "FastAPI"],
                "dependencies": ["qdrant-client"],
                "services": ["web", "api", "qdrant"],
                "env_keys": ["DATABASE_URL"],
                "scripts": {"dev": "vite --host 0.0.0.0"},
            }
        ]

        self.assertEqual(retrieval_eval._expected_term_score(items, ["React", "qdrant-client", "DATABASE_URL"]), 1.0)
        self.assertEqual(retrieval_eval._expected_term_score(items, ["missing-service"]), 0.0)

    def test_expected_no_answer_score_requires_no_candidates_or_sources(self) -> None:
        self.assertEqual(retrieval_eval._expected_no_answer_score([], [], True), 1.0)
        self.assertEqual(retrieval_eval._expected_no_answer_score([{"chunk_id": "1"}], [], True), 0.0)
        self.assertEqual(retrieval_eval._expected_no_answer_score([], [{"chunk_id": "1"}], True), 0.0)
        self.assertEqual(retrieval_eval._expected_no_answer_score([{"chunk_id": "1"}], [], False), 1.0)

    def test_expected_response_mode_and_answer_terms(self) -> None:
        self.assertEqual(retrieval_eval._expected_response_mode_score("flow_summary", "flow_summary"), 1.0)
        self.assertEqual(retrieval_eval._expected_response_mode_score("llm", "flow_summary"), 0.0)
        self.assertEqual(
            retrieval_eval._expected_answer_term_score(
                "The flow creates a session and runs ingestion.",
                ["creates a session", "runs ingestion"],
            ),
            1.0,
        )
        self.assertEqual(
            retrieval_eval._expected_answer_term_score("Only partial evidence.", ["runs ingestion"]),
            0.0,
        )

    def test_latency_percentiles(self) -> None:
        self.assertEqual(retrieval_eval._p50([30, 10, 20]), 20)
        self.assertEqual(retrieval_eval._p95([10, 20, 30]), 30)

    def test_hit_and_mrr_can_use_expected_files_without_sources(self) -> None:
        candidates = [
            {"relative_path": "README.md", "symbol_name": "README"},
            {"relative_path": "retrieval/config.py", "symbol_name": ""},
        ]

        self.assertEqual(retrieval_eval._hit_at_k(candidates, [], ["retrieval/config.py"], [], 10), 1)
        self.assertEqual(retrieval_eval._mrr_at_k(candidates, [], ["retrieval/config.py"], [], 10), 0.5)


if __name__ == "__main__":
    unittest.main()
