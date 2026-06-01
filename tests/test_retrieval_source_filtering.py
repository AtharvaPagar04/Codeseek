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


if __name__ == "__main__":
    unittest.main()
