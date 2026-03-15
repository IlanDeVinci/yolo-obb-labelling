from __future__ import annotations

import unittest

from sync_backend.sync_core import can_delete_user


class TestSyncUserPolicy(unittest.TestCase):
    def test_self_delete_allowed(self) -> None:
        self.assertTrue(
            can_delete_user(
                session_username="alice",
                session_role="user",
                target_username="alice",
                created_by_lookup=lambda _u: None,
            )
        )

    def test_owner_can_delete_anyone(self) -> None:
        self.assertTrue(
            can_delete_user(
                session_username="owner1",
                session_role="owner",
                target_username="bob",
                created_by_lookup=lambda _u: "someone",
            )
        )

    def test_admin_can_delete_only_created_users(self) -> None:
        self.assertTrue(
            can_delete_user(
                session_username="admin1",
                session_role="admin",
                target_username="bob",
                created_by_lookup=lambda _u: "admin1",
            )
        )
        self.assertFalse(
            can_delete_user(
                session_username="admin1",
                session_role="admin",
                target_username="carol",
                created_by_lookup=lambda _u: "other-admin",
            )
        )

    def test_regular_user_cannot_delete_others(self) -> None:
        self.assertFalse(
            can_delete_user(
                session_username="user1",
                session_role="user",
                target_username="user2",
                created_by_lookup=lambda _u: "user1",
            )
        )


if __name__ == "__main__":
    unittest.main()


