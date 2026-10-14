import unittest
from unittest.mock import patch

from coaching_bot import server


class Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class AuthTests(unittest.TestCase):
    def make_handler(self, headers):
        handler = object.__new__(server.Handler)
        handler.headers = Headers(headers)
        return handler

    def test_missing_admin_token_fails_closed(self):
        with patch.object(server, "ADMIN_TOKEN", ""):
            self.assertFalse(self.make_handler({})._authed())
            self.assertFalse(self.make_handler({"X-Admin-Token": "anything"})._authed())

    def test_correct_admin_token_is_accepted(self):
        with patch.object(server, "ADMIN_TOKEN", "test-secret"):
            self.assertTrue(self.make_handler({"X-Admin-Token": "test-secret"})._authed())
            self.assertFalse(self.make_handler({"X-Admin-Token": "wrong"})._authed())


if __name__ == "__main__":
    unittest.main()
