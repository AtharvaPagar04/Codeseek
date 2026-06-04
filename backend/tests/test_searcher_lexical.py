import unittest
from unittest.mock import patch

from retrieval import searcher


class _Hit:
    def __init__(self, payload: dict):
        self.payload = payload


class _FakeClient:
    def __init__(self, payloads: list[dict]):
        self.payloads = payloads
        self.scroll_calls = 0

    def scroll(self, **_kwargs):
        self.scroll_calls += 1
        return [_Hit(payload) for payload in self.payloads], None


class SearcherLexicalTests(unittest.TestCase):
    def setUp(self) -> None:
        searcher.invalidate_lexical_index()

    def tearDown(self) -> None:
        searcher.invalidate_lexical_index()

    def test_lexical_search_matches_summary_and_path_terms(self) -> None:
        payloads = [
            {
                "chunk_id": "readme",
                "relative_path": "README.md",
                "symbol_name": "README",
                "summary": "Trading bot that trains PPO LSTM models for market actions.",
            },
            {
                "chunk_id": "api",
                "relative_path": "backend/api.py",
                "symbol_name": "create_session",
                "summary": "Creates repository indexing sessions.",
            },
        ]
        client = _FakeClient(payloads)

        with patch("retrieval.searcher._get_client", return_value=client), patch(
            "retrieval.searcher.get_collection_name", return_value="test_collection"
        ):
            results = searcher._lexical_search("ppo lstm trading")

        self.assertEqual(results[0][0]["chunk_id"], "readme")
        self.assertEqual(results[0][2], "lexical")

    def test_lexical_search_matches_content_excerpt_terms(self) -> None:
        payloads = [
            {
                "chunk_id": "env",
                "relative_path": ".env.example",
                "summary": "Environment example file.",
                "content_excerpt": "CODESEEK_DATABASE_URL=postgresql://codeseek:codeseek@localhost:5432/codeseek",
            },
            {
                "chunk_id": "readme",
                "relative_path": "README.md",
                "summary": "Repository overview.",
            },
        ]
        client = _FakeClient(payloads)

        with patch("retrieval.searcher._get_client", return_value=client), patch(
            "retrieval.searcher.get_collection_name", return_value="test_collection"
        ):
            results = searcher._lexical_search("CODESEEK_DATABASE_URL")

        self.assertEqual(results[0][0]["chunk_id"], "env")

    def test_lexical_index_is_cached_per_collection_and_invalidated(self) -> None:
        client = _FakeClient(
            [
                {
                    "chunk_id": "readme",
                    "relative_path": "README.md",
                    "summary": "Repository overview.",
                }
            ]
        )

        with patch("retrieval.searcher._get_client", return_value=client):
            searcher._get_lexical_index("collection_a")
            searcher._get_lexical_index("collection_a")
            self.assertEqual(client.scroll_calls, 1)

            searcher.invalidate_lexical_index("collection_a")
            searcher._get_lexical_index("collection_a")
            self.assertEqual(client.scroll_calls, 2)

    def test_search_uses_lexical_layer_when_enabled(self) -> None:
        query_info = {"raw_query": "CODESEEK_DATABASE_URL", "intent": "SEMANTIC", "entities": {}}
        lexical_payload = {
            "chunk_id": "env",
            "relative_path": ".env.example",
            "symbol_name": ".env.example",
            "summary": "CODESEEK_DATABASE_URL configures the database connection.",
        }

        with patch("retrieval.searcher.ENABLE_LEXICAL_RETRIEVAL", True), patch(
            "retrieval.searcher._dense_search", return_value=[]
        ), patch("retrieval.searcher._metadata_search", return_value=[]), patch(
            "retrieval.searcher._lexical_search", return_value=[(lexical_payload, 3.0, "lexical")]
        ):
            results = searcher.search(query_info)

        self.assertEqual(results[0]["chunk_id"], "env")
        self.assertGreater(results[0]["fusion_score"], 0)

    def test_dense_search_can_be_disabled_for_offline_evals(self) -> None:
        with patch("retrieval.searcher.ENABLE_DENSE_RETRIEVAL", False), patch(
            "retrieval.searcher._get_model", side_effect=AssertionError("model should not load")
        ):
            self.assertEqual(searcher._dense_search("anything"), [])

    def test_exact_hits_are_promoted_ahead_of_probabilistic_hits(self) -> None:
        dense_payload = {
            "chunk_id": "dense",
            "relative_path": "src/semantic.py",
            "summary": "Semantic but not exact.",
        }
        exact_payload = {
            "chunk_id": "exact",
            "relative_path": "src/caller.py",
            "summary": "Calls create_session.",
        }

        results = searcher._merge_results(
            [(dense_payload, 0.99, "dense")],
            [(exact_payload, 0.0, "calls")],
        )

        self.assertEqual(results[0]["chunk_id"], "exact")
        self.assertTrue(results[0]["exact_retrieval_hit"])

    def test_exact_entity_search_matches_content_excerpt(self) -> None:
        payloads = [
            {
                "chunk_id": "settings",
                "relative_path": ".env.example",
                "summary": "Environment defaults.",
                "content_excerpt": "CODESEEK_DATABASE_URL=postgresql://codeseek:codeseek@localhost:5432/codeseek",
            },
            {
                "chunk_id": "readme",
                "relative_path": "README.md",
                "summary": "Repository overview.",
            },
        ]
        client = _FakeClient(payloads)

        with patch("retrieval.searcher._get_client", return_value=client), patch(
            "retrieval.searcher.get_collection_name", return_value="test_collection"
        ):
            results = searcher._exact_entity_search({"env_keys": ["CODESEEK_DATABASE_URL"]})

        self.assertEqual(results[0][0]["chunk_id"], "settings")
        self.assertEqual(results[0][2], "exact_entity")

    def test_exact_entity_search_matches_structured_metadata(self) -> None:
        payloads = [
            {
                "chunk_id": "requirements",
                "relative_path": "requirements.txt",
                "summary": "Python dependencies.",
                "dependencies": ["fastapi", "qdrant-client"],
            },
            {
                "chunk_id": "other",
                "relative_path": "README.md",
                "summary": "Mentions a web API in prose.",
            },
        ]
        client = _FakeClient(payloads)

        with patch("retrieval.searcher._get_client", return_value=client), patch(
            "retrieval.searcher.get_collection_name", return_value="test_collection"
        ):
            results = searcher._exact_entity_search({"dependencies": ["qdrant-client"]})

        self.assertEqual(results[0][0]["chunk_id"], "requirements")
        self.assertGreaterEqual(results[0][1], 4.0)


if __name__ == "__main__":
    unittest.main()
