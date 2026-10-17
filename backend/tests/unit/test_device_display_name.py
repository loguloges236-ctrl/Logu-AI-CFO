"""Device labels must fit devices.display_name (VARCHAR(120)); long browser User-Agents used to fail login."""

import unittest

from starlette.datastructures import Headers

from app.services.auth import DEVICE_DISPLAY_NAME_MAX, _device_display_name


class _Req:
    def __init__(self, user_agent: str | None):
        self.headers = Headers({"user-agent": user_agent} if user_agent is not None else {})


class DeviceDisplayNameTests(unittest.TestCase):
    def test_long_user_agent_is_truncated_to_column_length(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0"
        name = _device_display_name(_Req(ua))
        self.assertEqual(len(name), DEVICE_DISPLAY_NAME_MAX)
        self.assertTrue(ua.startswith(name))

    def test_short_user_agent_is_kept(self):
        self.assertEqual(_device_display_name(_Req("curl/8.0")), "curl/8.0")

    def test_missing_user_agent_is_none(self):
        self.assertIsNone(_device_display_name(_Req(None)))


if __name__ == "__main__":
    unittest.main()
