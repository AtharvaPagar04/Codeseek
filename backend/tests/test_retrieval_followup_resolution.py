import unittest
from unittest.mock import patch

from retrieval.main import run_query
from retrieval.memory import ConversationMemory


class RetrievalFollowUpResolutionTests(unittest.TestCase):
    def test_follow_up_query_reuses_previous_symbol_for_search(self) -> None:
        memory = ConversationMemory(max_turns=3)
        memory.add(
            "What does account_info do?",
            "The account_info method retrieves account information.",
        )
        captured: dict = {}

        def record_search(query_info: dict) -> list[dict]:
            captured["query_info"] = query_info
            return []

        with patch("retrieval.main.search", side_effect=record_search), patch(
            "retrieval.main.expand", return_value=[]
        ), patch("retrieval.main.assemble", return_value=("context", [], 0)), patch(
            "retrieval.main.select_sources_for_display", return_value=[]
        ), patch(
            "retrieval.main.generate_answer"
        ) as gen:
            answer, sources, token_count = run_query("also provide code", memory)

        self.assertEqual(answer, "Not found in retrieved context.")
        self.assertEqual(sources, [])
        self.assertEqual(token_count, 0)
        self.assertEqual(
            captured["query_info"]["entities"]["symbols"],
            ["account_info"],
        )
        self.assertEqual(
            captured["query_info"]["follow_up_to"],
            "What does account_info do?",
        )
        gen.assert_not_called()

    def test_second_follow_up_reuses_last_resolved_query(self) -> None:
        memory = ConversationMemory(max_turns=4)
        memory.add(
            "What does account_info do?",
            "The account_info method retrieves account information.",
            resolved_query="What does account_info do?",
        )
        memory.add(
            "also provide code",
            "The account_info method is in backend/src/exchange/binance_rest_client.py.",
            resolved_query="What does account_info do?\nalso provide code",
        )
        captured: dict = {}

        def record_search(query_info: dict) -> list[dict]:
            captured["query_info"] = query_info
            return []

        with patch("retrieval.main.search", side_effect=record_search), patch(
            "retrieval.main.expand", return_value=[]
        ), patch("retrieval.main.assemble", return_value=("context", [], 0)), patch(
            "retrieval.main.select_sources_for_display", return_value=[]
        ), patch(
            "retrieval.main.generate_answer"
        ) as gen:
            answer, sources, token_count = run_query("i want code snippit", memory)

        self.assertEqual(answer, "Not found in retrieved context.")
        self.assertEqual(sources, [])
        self.assertEqual(token_count, 0)
        self.assertEqual(
            captured["query_info"]["entities"]["symbols"],
            ["account_info"],
        )
        self.assertEqual(
            captured["query_info"]["follow_up_resolved_to"],
            "What does account_info do?\nalso provide code",
        )
        gen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
