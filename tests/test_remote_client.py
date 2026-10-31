"""Remote adapters tested with synthetic bodies and fake sockets only."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.remote_client import RemoteClient, RemoteError, Cancelled, MAX_RESPONSE


def fake_connection(data=None, status=200, headers=()):
    connection = MagicMock()
    response = connection.getresponse.return_value
    response.status = status
    response.getheaders.return_value = headers
    response.read1.side_effect = [json.dumps(data or {}).encode(), b""]
    return connection


MESSAGES = [{"role": "system", "content": "Synthetic companion"},
            {"role": "user", "content": "Synthetic greeting"}]
ANSWER = {"choices": [{"message": {"content": "Hola.", "reasoning_content": "not exposed"},
                       "finish_reason": "stop"}],
          "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14,
                    "private_vendor_text": "never expose", "cached": True}}


class RemoteTests(unittest.TestCase):
    def test_deepseek_request_and_reported_usage_only(self):
        connection = fake_connection(ANSWER, headers=[
            ("x-ratelimit-remaining-tokens", "500"), ("set-cookie", "secret")])
        with patch("companion.remote_client.http.client.HTTPSConnection", return_value=connection) as factory:
            result = RemoteClient("deepseek", "synthetic-key").complete("deepseek-flash", MESSAGES)
        factory.assert_called_once_with("api.deepseek.com", timeout=4)
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ("POST", "/chat/completions"))
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(result["answer"], "Hola.")
        self.assertNotIn("reasoning_content", result)
        self.assertEqual(result["usage"], {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14})
        self.assertEqual(result["limits"], {"x-ratelimit-remaining-tokens": "500"})

    def test_deep_mode_and_openai_storage_settings(self):
        for provider in ("deepseek", "openai"):
            connection = fake_connection(ANSWER)
            with patch("companion.remote_client.http.client.HTTPSConnection", return_value=connection):
                result = RemoteClient(provider, "synthetic-key").complete("test-model", MESSAGES, "deep", 2048)
            payload = json.loads(connection.request.call_args.kwargs["body"])
            if provider == "deepseek":
                self.assertEqual(payload["thinking"]["type"], "enabled")
                self.assertEqual(payload["reasoning_effort"], "high")
            else:
                self.assertEqual(payload["max_completion_tokens"], 2048)
                self.assertFalse(payload["store"])
                self.assertNotIn("max_tokens", payload)
            self.assertEqual(result["answer"], "Hola.")

    def test_auth_quota_rate_and_redirect_do_not_expose_payload(self):
        for status, expected in [(401, "auth"), (403, "auth"), (402, "quota"),
                                 (429, "rate"), (503, "network"), (302, "protocol")]:
            connection = fake_connection({"error": {"message": "secret synthetic-key"}}, status=status)
            with patch("companion.remote_client.http.client.HTTPSConnection", return_value=connection):
                with self.assertRaises(RemoteError) as error:
                    RemoteClient("deepseek", "synthetic-key").complete("deepseek-flash", MESSAGES)
            self.assertEqual(error.exception.code, expected)
            self.assertNotIn("secret", str(error.exception))
            self.assertNotIn("synthetic-key", str(error.exception))
            connection.getresponse.return_value.read1.assert_not_called()

    def test_cancel_before_connect_never_transmits(self):
        client = RemoteClient("deepseek", "synthetic-key")
        client.cancel()
        with patch("companion.remote_client.http.client.HTTPSConnection") as factory:
            with self.assertRaises(Cancelled):
                client.complete("deepseek-flash", MESSAGES)
            factory.assert_not_called()

    def test_cancel_interrupts_socket_and_drops_response(self):
        connection = fake_connection(ANSWER)
        client = RemoteClient("deepseek", "synthetic-key")
        connection.getresponse.side_effect = lambda: (client.cancel(), connection.getresponse.return_value)[1]
        with patch("companion.remote_client.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaises(Cancelled):
                client.complete("deepseek-flash", MESSAGES)
        connection.sock.shutdown.assert_called_once()
        connection.getresponse.return_value.read1.assert_not_called()

    def test_invalid_configuration_never_connects(self):
        with patch("companion.remote_client.http.client.HTTPSConnection") as factory:
            for provider, key in [("https://evil.example", "key"), ("deepseek", "key\r\nheader"), ("openai", "")]:
                with self.assertRaises(RemoteError):
                    RemoteClient(provider, key)
            client = RemoteClient("openai", "synthetic-key")
            for messages in ([], [{"role": "tool", "content": "x"}], [{"role": "user", "content": "x"*65537}]):
                with self.assertRaises(RemoteError):
                    client.complete("test-model", messages)
            factory.assert_not_called()

    def test_network_error_is_redacted(self):
        connection = fake_connection(ANSWER)
        connection.connect.side_effect = OSError("synthetic-key private prompt")
        with patch("companion.remote_client.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaises(RemoteError) as error:
                RemoteClient("deepseek", "synthetic-key").complete("deepseek-flash", MESSAGES)
        self.assertEqual(error.exception.code, "network")
        self.assertNotIn("private", str(error.exception))

    def test_invalid_response_and_reasoning_without_answer_rejected(self):
        for data in ({}, {"choices": []}, {"choices": [{"message": {"reasoning_content": "secret"}}]}):
            connection = fake_connection(data)
            with patch("companion.remote_client.http.client.HTTPSConnection", return_value=connection):
                with self.assertRaises(RemoteError):
                    RemoteClient("deepseek", "synthetic-key").complete("deepseek-flash", MESSAGES)

    def test_oversized_body_rejected(self):
        connection = fake_connection()
        connection.getresponse.return_value.read1.side_effect = [b"x"*(MAX_RESPONSE+1)]
        with patch("companion.remote_client.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaises(RemoteError) as error:
                RemoteClient("deepseek", "synthetic-key").complete("deepseek-flash", MESSAGES)
        self.assertEqual(error.exception.code, "protocol")

    def test_balance_is_reported_and_not_invented(self):
        data = {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "1.50",
                "granted_balance": "0.50", "topped_up_balance": "1.00"}]}
        connection = fake_connection(data)
        with patch("companion.remote_client.http.client.HTTPSConnection", return_value=connection):
            result = RemoteClient("deepseek", "synthetic-key").balance()
        self.assertEqual(connection.request.call_args.args, ("GET", "/user/balance"))
        self.assertEqual(result["balance_infos"][0]["total_balance"], "1.50")
        with patch("companion.remote_client.http.client.HTTPSConnection") as factory:
            self.assertFalse(RemoteClient("openai", "synthetic-key").balance()["reported"])
            factory.assert_not_called()

    def test_nonfinite_balance_rejected(self):
        data = {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "NaN",
                "granted_balance": "0", "topped_up_balance": "0"}]}
        connection = fake_connection(data)
        with patch("companion.remote_client.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaises(RemoteError):
                RemoteClient("deepseek", "synthetic-key").balance()

    def test_length_finish_is_marked_without_fabricating_quota(self):
        data = {"choices": [{"message": {"content": "Partial"}, "finish_reason": "length"}]}
        connection = fake_connection(data)
        with patch("companion.remote_client.http.client.HTTPSConnection", return_value=connection):
            result = RemoteClient("deepseek", "synthetic-key").complete("deepseek-flash", MESSAGES)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["usage"], {})
        self.assertEqual(result["limits"], {})
