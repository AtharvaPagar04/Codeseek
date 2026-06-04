"""Unit tests for retrieval source display filtering."""

import unittest

from retrieval.source_filter import select_sources_for_display


class SourceFilteringTests(unittest.TestCase):
    def test_non_test_query_filters_test_sources(self) -> None:
        query = "Trace account_info to final HTTP request and signature attachment"
        sources = [
            {
                "relative_path": "backend/src/exchange/binance_rest_client.py",
                "symbol_name": "account_info",
                "start_line": 250,
                "end_line": 260,
                "expansion_type": "primary",
            },
            {
                "relative_path": "backend/tests/test_account_info_method.py",
                "symbol_name": "test_account_info_method_exists",
                "start_line": 6,
                "end_line": 14,
                "expansion_type": "primary",
            },
        ]

        selected = select_sources_for_display(query, sources)
        self.assertEqual(len(selected), 1)
        self.assertIn("binance_rest_client.py", selected[0]["relative_path"])

    def test_test_query_keeps_test_sources(self) -> None:
        query = "Which test verifies authenticated_get exists?"
        sources = [
            {
                "relative_path": "backend/tests/test_authenticated_get.py",
                "symbol_name": "test_authenticated_get_exists",
                "start_line": 6,
                "end_line": 14,
                "expansion_type": "primary",
            },
            {
                "relative_path": "backend/src/exchange/binance_rest_client.py",
                "symbol_name": "authenticated_get",
                "start_line": 210,
                "end_line": 248,
                "expansion_type": "primary",
            },
        ]

        selected = select_sources_for_display(query, sources)
        joined = " ".join(src["relative_path"] for src in selected).lower()
        self.assertIn("test_authenticated_get.py", joined)

    def test_relevance_prunes_noisy_primary_when_strong_match_exists(self) -> None:
        query = "Compare signed_params and sign_query for timestamp/signature injection."
        sources = [
            {
                "relative_path": "backend/src/exchange/binance_rest_client.py",
                "symbol_name": "signed_params",
                "start_line": 170,
                "end_line": 189,
                "expansion_type": "primary",
            },
            {
                "relative_path": "backend/src/exchange/binance_rest_client.py",
                "symbol_name": "sign_query",
                "start_line": 148,
                "end_line": 168,
                "expansion_type": "primary",
            },
            {
                "relative_path": "backend/src/exchange/binance_rest_client.py",
                "symbol_name": "create_listen_key",
                "start_line": 284,
                "end_line": 301,
                "expansion_type": "primary",
            },
        ]

        selected = select_sources_for_display(query, sources)
        symbols = {src["symbol_name"] for src in selected}
        self.assertIn("signed_params", symbols)
        self.assertIn("sign_query", symbols)
        self.assertNotIn("create_listen_key", symbols)

    def test_project_overview_query_allows_broader_source_set(self) -> None:
        query = "what is this project about"
        sources = [
            {
                "relative_path": f"src/components/Section{i}.tsx",
                "symbol_name": f"Section{i}",
                "start_line": 1,
                "end_line": 20,
                "expansion_type": "primary",
            }
            for i in range(7)
        ]

        selected = select_sources_for_display(query, sources)
        self.assertEqual(len(selected), 6)

    def test_phase1_flow_query_keeps_core_flow_anchors(self) -> None:
        query = "walk me through backend request orchestration flow"
        sources = [
            {
                "relative_path": "retrieval/api_service.py",
                "symbol_name": "_query_impl",
                "start_line": 501,
                "end_line": 650,
                "expansion_type": "primary",
            },
            {
                "relative_path": "retrieval/main.py",
                "symbol_name": "run_query",
                "start_line": 55,
                "end_line": 310,
                "expansion_type": "primary",
            },
        ] + [
            {
                "relative_path": f"retrieval/noisy_{index}.py",
                "symbol_name": f"backend_request_helper_{index}",
                "start_line": 1,
                "end_line": 2,
                "expansion_type": "primary",
            }
            for index in range(8)
        ]

        selected = select_sources_for_display(query, sources)
        symbols = {source["symbol_name"] for source in selected}

        self.assertIn("_query_impl", symbols)
        self.assertIn("run_query", symbols)
        self.assertLessEqual(len(selected), 7)


if __name__ == "__main__":
    unittest.main()
