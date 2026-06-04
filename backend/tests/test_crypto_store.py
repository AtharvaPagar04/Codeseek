import unittest
from unittest.mock import patch

from retrieval.crypto_store import decrypt_secret, encrypt_secret


class CryptoStoreTests(unittest.TestCase):
    def test_encrypt_and_decrypt_round_trip(self) -> None:
        with patch.dict(
            "os.environ",
            {"CODESEEK_APP_ENCRYPTION_KEY": "test-encryption-key"},
            clear=False,
        ):
            encrypted = encrypt_secret("ghp_example_secret")
            self.assertNotEqual(encrypted, "ghp_example_secret")
            self.assertEqual(decrypt_secret(encrypted), "ghp_example_secret")

    def test_decrypt_rejects_tampering(self) -> None:
        with patch.dict(
            "os.environ",
            {"CODESEEK_APP_ENCRYPTION_KEY": "test-encryption-key"},
            clear=False,
        ):
            encrypted = encrypt_secret("ghp_example_secret")
            tampered = encrypted[:-1] + ("A" if encrypted[-1] != "A" else "B")
            with self.assertRaises(ValueError):
                decrypt_secret(tampered)


if __name__ == "__main__":
    unittest.main()
