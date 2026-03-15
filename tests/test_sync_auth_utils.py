from __future__ import annotations

import unittest

from sync_backend.sync_core import extract_bearer_token, is_valid_bootstrap_token


class TestSyncAuthUtils(unittest.TestCase):
    def test_extract_bearer_token(self) -> None:
        self.assertEqual(extract_bearer_token("Bearer abc123"), "abc123")
        self.assertEqual(extract_bearer_token("Bearer   abc123   "), "abc123")

    def test_extract_bearer_token_invalid(self) -> None:
        with self.assertRaises(ValueError):
            extract_bearer_token(None)
        with self.assertRaises(ValueError):
            extract_bearer_token("Token abc")
        with self.assertRaises(ValueError):
            extract_bearer_token("Bearer   ")

    def test_bootstrap_token_validation(self) -> None:
        self.assertTrue(is_valid_bootstrap_token("x", ""))
        self.assertTrue(is_valid_bootstrap_token("abc", "abc"))
        self.assertFalse(is_valid_bootstrap_token("abc", "def"))
        self.assertFalse(is_valid_bootstrap_token("", "abc"))


if __name__ == "__main__":
    unittest.main()


