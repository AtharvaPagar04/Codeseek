import unittest

from retrieval.llm import SYSTEM_PROMPT, _build_prompt


class LlmPromptTests(unittest.TestCase):
    def test_system_prompt_preserves_payload_identifier(self) -> None:
        self.assertNotIn("Do NOT mention payload", SYSTEM_PROMPT)
        self.assertNotIn("do not mention payload", SYSTEM_PROMPT.lower())
        self.assertIn("internal payload metadata", SYSTEM_PROMPT)
        self.assertIn("Preserve source-code identifiers", SYSTEM_PROMPT)
        self.assertIn("legitimate source-code identifiers inside code blocks", SYSTEM_PROMPT)

    def test_code_prompt_preserves_exact_code_identifiers(self) -> None:
        prompt = _build_prompt(
            raw_query="show me the Qdrant upsert code",
            context="payload=_payload(chunk),\nclient.upsert(...)\n",
            history_block="",
            allowed_sources=[
                {
                    "relative_path": "backend/rag_ingestion/stages/storage.py",
                    "symbol_name": "store_chunks",
                    "start_line": 10,
                    "end_line": 42,
                }
            ],
            response_mode="code_snippet",
        )
        self.assertIn("payload=_payload(chunk),", prompt)
        self.assertIn("Preserve code exactly.", prompt)
        self.assertIn("Do not rename or remove identifiers.", prompt)
        self.assertIn("Do not sanitize source-code words that look like retrieval terms.", prompt)

    def test_current_question_precedes_history_and_overrides_previous_turns(self) -> None:
        prompt = _build_prompt(
            raw_query="show me _require_auth code",
            context="def _require_auth():\n    pass\n",
            history_block="Previous turn: show me the Qdrant upsert code",
            allowed_sources=[
                {
                    "relative_path": "backend/retrieval/api_service.py",
                    "symbol_name": "_require_auth",
                    "start_line": 1,
                    "end_line": 8,
                }
            ],
            response_mode="code_snippet",
        )
        current_idx = prompt.index("show me _require_auth code")
        history_idx = prompt.index("--- CONVERSATION HISTORY (SECONDARY REFERENCE ONLY) ---")
        self.assertLess(current_idx, history_idx)
        self.assertIn("The CURRENT USER QUESTION is the source of truth for this answer.", prompt)
        self.assertIn(
            "Conversation history is only for resolving vague follow-ups",
            prompt,
        )
        self.assertIn(
            "If the current question explicitly names a file, function, class, symbol, endpoint, feature, or subsystem",
            prompt,
        )
        self.assertIn(
            "Do not reuse previous-turn sources unless they directly match the current question.",
            prompt,
        )

    def test_vague_followup_keeps_history_as_secondary_reference(self) -> None:
        prompt = _build_prompt(
            raw_query="explain that",
            context="def run_safe_evals():\n    pass\n",
            history_block="Previous turn: show me the safe eval runner code",
            allowed_sources=[],
            response_mode="flow_summary",
        )
        self.assertIn("--- CONVERSATION HISTORY (SECONDARY REFERENCE ONLY) ---", prompt)
        self.assertIn("explain that", prompt)
        self.assertIn("Use conversation history only when the current question is ambiguous", prompt)

    def test_source_location_prompt_prefers_implementation_files(self) -> None:
        prompt = _build_prompt(
            raw_query="Where is safe eval implemented?",
            context="backend/evals/run_safe_evals.py :: main\n",
            history_block="",
            allowed_sources=[],
            response_mode="source_location",
        )
        self.assertIn("Prefer executable implementation files over docs/tests when implementation sources are available.", prompt)
        self.assertIn("Docs/tests may be related sources only when the user explicitly asks for docs/tests or no implementation file is available.", prompt)

    def test_explicit_docs_query_ignores_previous_turns_in_prompt_rules(self) -> None:
        prompt = _build_prompt(
            raw_query="show me safe eval docs",
            context="backend/docs/retrieval_docs/safe_eval_runner.md :: safe_eval_runner_md\n",
            history_block="Previous turn: Where is safe eval implemented?",
            allowed_sources=[],
            response_mode="technical_trace",
        )
        self.assertIn(
            "If the current question explicitly asks for docs, documentation, markdown, reports, policy, guide, or a named document",
            prompt,
        )
        self.assertIn("do not summarize prior turns unless the current question is vague", prompt)


if __name__ == "__main__":
    unittest.main()
