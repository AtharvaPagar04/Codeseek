import unittest

from retrieval import api_service


class ApiServiceProviderConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        api_service._provider_configs.clear()

    def test_register_provider_config_stores_secret_in_memory_only(self) -> None:
        record = api_service._register_provider_config(
            provider="groq",
            api_key="gsk_secret",
            model="llama-3.3-70b-versatile",
            label="Personal Groq",
        )

        self.assertTrue(record["id"])
        self.assertEqual(record["provider"], "groq")
        self.assertNotIn("api_key", record)

        stored = api_service._get_provider_config(record["id"])
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["api_key"], "gsk_secret")
        self.assertEqual(stored["label"], "Personal Groq")

    def test_get_provider_config_returns_copy(self) -> None:
        record = api_service._register_provider_config(
            provider="openai",
            api_key="sk-secret",
            model="gpt-4o-mini",
            label="Work OpenAI",
        )
        stored = api_service._get_provider_config(record["id"])
        assert stored is not None
        stored["provider"] = "mutated"

        fresh = api_service._get_provider_config(record["id"])
        assert fresh is not None
        self.assertEqual(fresh["provider"], "openai")


if __name__ == "__main__":
    unittest.main()
