import unittest
from unittest.mock import patch

from retrieval.main import run_query
from retrieval.memory import ConversationMemory


class RetrievalNoEvidenceGuardTests(unittest.TestCase):
    def test_run_query_skips_llm_when_no_displayable_sources(self) -> None:
        memory = ConversationMemory(max_turns=2)
        with patch("retrieval.main.process_query", return_value={"raw_query": "q", "intent": "SEMANTIC", "entities": {}}), patch(
            "retrieval.main.search", return_value=[]
        ), patch("retrieval.main.expand", return_value=[]), patch(
            "retrieval.main.assemble", return_value=("context", [], 42)
        ), patch(
            "retrieval.main.select_sources_for_display", return_value=[]
        ), patch("retrieval.main.generate_answer") as gen:
            answer, sources, token_count = run_query("q", memory)

        self.assertEqual(answer, "Not found in retrieved context.")
        self.assertEqual(sources, [])
        self.assertEqual(token_count, 42)
        gen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
