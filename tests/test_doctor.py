"""Synthetic diagnostics tests: no Ollama service or private inventory needed."""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion import doctor


class Response(io.BytesIO):
    status = 200


class DoctorTests(unittest.TestCase):
    def run_diagnostic(self, inventory):
        responses = [Response(b'{"version":"synthetic"}'), Response(inventory)]
        opener = MagicMock()
        opener.open.side_effect = responses
        with patch.object(doctor, "build_opener", return_value=opener) as builder:
            result = doctor.diagnose()
        return result, opener, builder

    def test_available_uses_get_fixed_loopback_routes_and_no_proxy(self):
        result, opener, builder = self.run_diagnostic(
            b'{"models":[{"name":"private-synthetic-name"}]}'
        )
        self.assertEqual(result, doctor.Diagnostic("available", 1))
        calls = opener.open.call_args_list
        self.assertEqual([call.args[0].get_method() for call in calls], ["GET", "GET"])
        self.assertEqual([call.args[0].full_url for call in calls], [
            "http://127.0.0.1:11434/api/version",
            "http://127.0.0.1:11434/api/tags",
        ])
        self.assertTrue(all(call.kwargs == {"timeout": 2.0} for call in calls))
        handlers = builder.call_args.args
        proxy = next(handler for handler in handlers if isinstance(handler, ProxyHandler))
        self.assertEqual(proxy.proxies, {})
        self.assertTrue(any(isinstance(handler, doctor._RejectRedirects) for handler in handlers))

    def test_no_models(self):
        result, _, _ = self.run_diagnostic(b'{"models":[]}')
        self.assertEqual(result, doctor.Diagnostic("no_models"))

    def test_invalid_json_and_inventory(self):
        for data in (b'not json', b'[]', b'{}', b'{"models":[{}]}', b'\xff'):
            with self.subTest(data=data):
                result, _, _ = self.run_diagnostic(data)
                self.assertEqual(result.status, "invalid_response")

    def test_timeout_and_connection_failure(self):
        for error in (TimeoutError(), URLError("synthetic unavailable")):
            with self.subTest(error=type(error)):
                opener = MagicMock()
                opener.open.side_effect = error
                with patch.object(doctor, "build_opener", return_value=opener):
                    self.assertEqual(doctor.diagnose().status, "unavailable")

    def test_redirect_rejected(self):
        handler = doctor._RejectRedirects()
        request = Request("http://127.0.0.1:11434/api/version")
        self.assertIsNone(handler.redirect_request(
            request, None, 302, "redirect", {}, "https://example.invalid/"
        ))
        opener = MagicMock()
        opener.open.side_effect = HTTPError(request.full_url, 302, "redirect", {}, None)
        with patch.object(doctor, "build_opener", return_value=opener):
            self.assertEqual(doctor.diagnose().status, "invalid_response")
        self.assertEqual(opener.open.call_count, 1)

    def test_oversize_response_is_bounded(self):
        response = MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        response.read.return_value = b'x' * (doctor.MAX_RESPONSE_BYTES + 1)
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(doctor, "build_opener", return_value=opener):
            self.assertEqual(doctor.diagnose().status, "invalid_response")
        response.read.assert_called_once_with(doctor.MAX_RESPONSE_BYTES + 1)

    def test_invalid_version_stops_before_inventory(self):
        opener = MagicMock()
        opener.open.return_value = Response(b'{"version":null}')
        with patch.object(doctor, "build_opener", return_value=opener):
            self.assertEqual(doctor.diagnose().status, "invalid_response")
        self.assertEqual(opener.open.call_count, 1)

    def test_cli_does_not_print_model_names(self):
        output = io.StringIO()
        with patch.object(doctor, "diagnose", return_value=doctor.Diagnostic("available", 3)):
            with redirect_stdout(output):
                self.assertEqual(doctor.main(), 0)
        self.assertEqual(output.getvalue(), "status=available model_count=3\n")

    def test_reject_invalid_port_and_timeout(self):
        for port in (True, 0, 65536, "11434", "example.invalid"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                doctor.diagnose(port=port)
        for timeout in (True, 0, -1, float("inf"), float("nan"), "2"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                doctor.diagnose(timeout=timeout)


if __name__ == "__main__":
    unittest.main()
