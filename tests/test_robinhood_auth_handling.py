import unittest
import sys
import types


if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")
    requests_stub.post = lambda *args, **kwargs: types.SimpleNamespace(status_code=200, text="ok")
    sys.modules["requests"] = requests_stub

if "robin_stocks.robinhood" not in sys.modules:
    robin_stocks_stub = types.ModuleType("robin_stocks")
    robinhood_stub = types.ModuleType("robin_stocks.robinhood")
    robinhood_stub.login = lambda *args, **kwargs: None
    robinhood_stub.logout = lambda *args, **kwargs: None
    robinhood_stub.account = types.SimpleNamespace()
    robinhood_stub.crypto = types.SimpleNamespace()
    robinhood_stub.options = types.SimpleNamespace()
    sys.modules["robin_stocks"] = robin_stocks_stub
    sys.modules["robin_stocks.robinhood"] = robinhood_stub

from app.providers import robinhood_provider


class RobinhoodAuthHandlingTests(unittest.TestCase):
    def test_429_prompt_polling_error_is_rate_limited_login_result(self):
        result = robinhood_provider._classify_login_error(
            "429 Client Error: Too Many Requests for url: "
            "https://api.robinhood.com/push/test/get_prompts_status/"
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["rate_limited"])
        self.assertFalse(result["auth_required"])
        self.assertEqual(result["status"], "rate_limited")

    def test_verification_poll_limit_is_auth_required_not_success(self):
        result = robinhood_provider._classify_login_error(
            "Robinhood verification polling exceeded MAX_VERIFICATION_POLLS=10"
        )

        self.assertFalse(result["success"])
        self.assertFalse(result["rate_limited"])
        self.assertTrue(result["auth_required"])
        self.assertEqual(result["status"], "auth_required")


if __name__ == "__main__":
    unittest.main()
