import os
import unittest
from unittest.mock import patch

from retrieval.llm import generate_answer


class LlmProviderConfigTests(unittest.TestCase):
    def test_generate_answer_uses_request_scoped_provider_config(self) -> None:
        with patch(
            "retrieval.llm._provider_answer",
            return_value="ok",
        ) as provider_answer:
            answer = generate_answer(
                raw_query="What does this code do?",
                context="def hello(): pass",
                history_block="",
                provider_config={
                    "provider": "openai",
                    "api_key": "sk-test",
                    "model": "",
                },
            )

        self.assertEqual(answer, "ok")
        provider_answer.assert_called_once()
        _, kwargs = provider_answer.call_args
        self.assertEqual(kwargs["provider"], "openai")
        self.assertEqual(kwargs["api_key"], "sk-test")
        self.assertEqual(kwargs["model"], "gpt-4o-mini")

    def test_generate_answer_reports_missing_provider_key(self) -> None:
        answer = generate_answer(
            raw_query="What does this code do?",
            context="def hello(): pass",
            history_block="",
        )

        self.assertIn("No LLM provider API key configured", answer)

    def test_generate_answer_uses_gemini_default_model(self) -> None:
        with patch(
            "retrieval.llm._provider_answer",
            return_value="ok",
        ) as provider_answer:
            answer = generate_answer(
                raw_query="What does this code do?",
                context="def hello(): pass",
                history_block="",
                provider_config={
                    "provider": "gemini",
                    "api_key": "AIza-test",
                    "model": "",
                },
            )

        self.assertEqual(answer, "ok")
        _, kwargs = provider_answer.call_args
        self.assertEqual(kwargs["provider"], "gemini")
        self.assertEqual(kwargs["api_key"], "AIza-test")
        self.assertEqual(kwargs["model"], "gemini-1.5-flash")


if __name__ == "__main__":
    unittest.main()
