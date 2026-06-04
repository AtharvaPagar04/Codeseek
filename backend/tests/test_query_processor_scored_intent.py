import unittest
from unittest.mock import patch

from retrieval import query_processor


class QueryProcessorScoredIntentTests(unittest.TestCase):
    def test_extracts_env_key_as_config_entity(self) -> None:
        result = query_processor.process_query("Where is CODESEEK_DATABASE_URL configured?")

        self.assertEqual(result["primary_intent"], "CONFIG")
        self.assertIn("CODESEEK_DATABASE_URL", result["entities"]["env_keys"])
        self.assertIn("CODESEEK_DATABASE_URL", result["entities"]["exact_terms"])
        self.assertGreaterEqual(result["intent_scores"]["CONFIG"], 0.8)
        self.assertGreaterEqual(result["confidence"], 0.8)
        self.assertFalse(result["topic_shift"])

    def test_extracts_dependency_and_model_names(self) -> None:
        result = query_processor.process_query("Where is qdrant-client or BAAI/bge-small-en-v1.5 used?")

        self.assertIn("qdrant-client", result["entities"]["dependencies"])
        self.assertIn("BAAI/bge-small-en-v1.5", result["entities"]["dependencies"])
        self.assertIn("qdrant-client", result["entities"]["exact_terms"])

    def test_extracts_api_term_for_endpoint_lookup(self) -> None:
        result = query_processor.process_query("Explain the submission-key endpoint")

        self.assertIn("submission-key", result["entities"]["api_terms"])
        self.assertIn("submission-key", result["entities"]["exact_terms"])

    def test_injects_phase1_flow_symbols_for_metadata_search(self) -> None:
        orchestration = query_processor.process_query("walk me through backend request orchestration flow")
        auth = query_processor.process_query("explain the auth session lifecycle flow")
        indexing = query_processor.process_query("trace the indexing session creation flow")

        self.assertIn("_query_impl", orchestration["entities"]["symbols"])
        self.assertIn("run_query", orchestration["entities"]["symbols"])
        self.assertIn("create_auth_session", auth["entities"]["symbols"])
        self.assertIn("get_user_for_session_token", auth["entities"]["symbols"])
        self.assertIn("delete_auth_session", auth["entities"]["symbols"])
        self.assertIn("create_session", indexing["entities"]["symbols"])
        self.assertIn("_index_job", indexing["entities"]["symbols"])

    def test_injects_deployment_config_files_for_metadata_search(self) -> None:
        result = query_processor.process_query("how does deployment configuration work")

        self.assertIn("docker-compose.yml", result["entities"]["files"])
        self.assertIn("Dockerfile", result["entities"]["files"])
        self.assertIn(".env.example", result["entities"]["files"])

    def test_injects_provider_credential_symbols_for_metadata_search(self) -> None:
        result = query_processor.process_query("explain provider credential lifecycle")

        self.assertIn("create_provider_credential_v1", result["entities"]["symbols"])
        self.assertIn("create_provider_credential", result["entities"]["symbols"])
        self.assertIn("get_active_provider_credential", result["entities"]["symbols"])

    def test_injects_architecture_files_for_metadata_search(self) -> None:
        result = query_processor.process_query("architecture overview")

        self.assertIn("README.md", result["entities"]["files"])
        self.assertIn("docker-compose.yml", result["entities"]["files"])
        self.assertIn("retrieval/api_service.py", result["entities"]["files"])

    def test_injects_auth_flow_symbols_for_varied_lifecycle_wording(self) -> None:
        result = query_processor.process_query("how does authentication cookie lifecycle work")

        self.assertIn("auth_github", result["entities"]["symbols"])
        self.assertIn("create_auth_session", result["entities"]["symbols"])
        self.assertIn("get_user_for_session_token", result["entities"]["symbols"])
        self.assertIn("auth_logout", result["entities"]["symbols"])

    def test_scored_intent_flag_still_emits_contract_in_legacy_mode(self) -> None:
        with patch("retrieval.query_processor.ENABLE_SCORED_INTENT", False):
            result = query_processor.process_query("where is create_session")

        self.assertEqual(result["intent"], "SYMBOL")
        self.assertEqual(result["primary_intent"], "SYMBOL")
        self.assertIn("intent_scores", result)
        self.assertIn("entities", result)
        self.assertEqual(result["entities"]["exact_terms"], [])
        self.assertEqual(result["confidence"], result["intent_scores"]["SYMBOL"])


if __name__ == "__main__":
    unittest.main()
