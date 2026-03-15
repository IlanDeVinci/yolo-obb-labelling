from __future__ import annotations

import unittest

from sync_backend.sync_core import hash_password, verify_password


class TestSyncSecurity(unittest.TestCase):
    def test_hash_and_verify_roundtrip(self) -> None:
        hashed = hash_password("secret-pass")
        self.assertTrue(verify_password("secret-pass", hashed))
        self.assertFalse(verify_password("wrong-pass", hashed))

    def test_verify_invalid_encoded(self) -> None:
        self.assertFalse(verify_password("abc", "not-a-valid-hash"))


if __name__ == "__main__":
    unittest.main()


