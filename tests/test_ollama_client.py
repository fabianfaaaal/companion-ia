"""Synthetic transport tests, without real models, sockets or inference."""
import io
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.ollama_client import OllamaClient, ChatChunk, ClientError, Cancelled, MAX_LINE, MAX_RESPONSE
from companion import ollama_client as transport

REAL_CONNECTION = transport._Connection


class Response(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status = status


def encoded(value):
    return Response(json.dumps(value).encode())


def local():
    return encoded({"details": {"family": "synthetic"}})


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.factory = patch.object(transport, "_Connection")
        self.connection = self.factory.start()
        self.addCleanup(self.factory.stop)
        self.responses = []
        self.connections = []

        def create(*args):
            connection = MagicMock()
            connection.getresponse.side_effect = lambda: self.responses.pop(0)
            self.connections.append(connection)
            return connection
        self.connection.side_effect = create
        self.client = OllamaClient()

    def test_list_filters_remote_and_unverifiable_models(self):
        self.responses.extend([
            encoded({"models": [{"name": name} for name in ["a", "b:cloud", "c", "d", "e"]]}),
            local(), encoded({"remote_host": "synthetic", "details": {"family": "x"}}),
            encoded({"remote_model": "synthetic", "details": {"family": "x"}}), encoded({}),
        ])
        self.assertEqual(self.client.list_models(), ["a"])
        requests = [connection.request.call_args.args[:2] for connection in self.connections]
        self.assertEqual(requests, [("GET", "/api/tags")] + [("POST", "/api/show")] * 4)
        self.assertTrue(all(call.args[0] == 11434 for call in self.connection.call_args_list))

    def test_stream_verifies_model_and_sets_explicit_limits(self):
        self.responses.extend([local(), Response(
            b'{"message":{"content":"Hola"},"done":false}\n'
            b'{"message":{"content":"!"},"done":true,"eval_count":2,"secret":"hidden"}\n'
        )])
        messages = [{"role": "user", "content": "synthetic"}]
        chunks = list(self.client.stream_chat("a", messages, threading.Event()))
        self.assertEqual(chunks, [ChatChunk("Hola"), ChatChunk("!", True, {"eval_count": 2})])
        call = self.connections[-1].request.call_args
        self.assertEqual(call.args[:2], ("POST", "/api/chat"))
        body = json.loads(call.kwargs["body"])
        self.assertEqual(body, {"model": "a", "messages": messages, "think": False,
                                "stream": True, "options": {"num_ctx":4096, "num_predict":1024}})

    def test_remote_model_never_reaches_chat(self):
        self.responses.append(encoded({"remote_model":"private-name"}))
        with self.assertRaises(ClientError) as caught:
            list(self.client.stream_chat("a", [{"role":"user","content":"x"}], threading.Event()))
        self.assertNotIn("private-name", str(caught.exception))
        self.assertEqual(len(self.connections), 1)

    def test_cloud_name_rejected_before_network(self):
        with self.assertRaises(ClientError):
            self.client.verify_local("synthetic:cloud")
        self.connection.assert_not_called()

    def test_redirect_is_error_and_not_followed(self):
        self.responses.append(Response(b"private-provider-error", status=302))
        with self.assertRaises(ClientError) as caught:
            self.client.list_models()
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(len(self.connections), 1)

    def test_malformed_and_incomplete_stream_fail_safely(self):
        for body in (b'not-json\n', b'{"done":false,"message":{"content":"x"}}\n',
                     b'{"done":true,"error":"PRIVATE-DATA"}\n',
                     b'{"done":true,"message":{"content":123}}\n'):
            with self.subTest(body=body):
                self.responses.extend([local(), Response(body)])
                with self.assertRaises(ClientError) as caught:
                    list(self.client.stream_chat("a", [{"role":"user","content":"x"}], threading.Event()))
                self.assertNotIn("PRIVATE-DATA", str(caught.exception))

    def test_oversize_line(self):
        self.responses.extend([local(), Response(b'x' * (MAX_LINE + 1))])
        with self.assertRaises(ClientError):
            list(self.client.stream_chat("a", [{"role":"user","content":"x"}], threading.Event()))

    def test_oversize_inventory(self):
        self.responses.append(Response(b'x' * (MAX_RESPONSE + 1)))
        with self.assertRaises(ClientError):
            self.client.list_models()

    def test_pre_cancelled_request_sends_nothing(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            list(self.client.stream_chat("a", [], cancel))
        self.connection.assert_not_called()

    def test_cancel_interrupts_blocked_read_with_shutdown(self):
        entered, released = threading.Event(), threading.Event()
        response = MagicMock()
        response.status = 200
        def read(*args):
            entered.set()
            released.wait(2)
            return b''
        response.readline.side_effect = read
        self.responses.extend([local(), response])
        errors = []
        def worker():
            try:
                list(self.client.stream_chat("a", [{"role":"user","content":"x"}], threading.Event()))
            except ClientError as exc:
                errors.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(entered.wait(1))
        self.connections[-1].sock.shutdown.side_effect = lambda *args: released.set()
        self.client.cancel()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], Cancelled)
        self.connections[-1].sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        self.connections[-1].close.assert_called()

    def test_required_thinking_is_separated_from_content(self):
        self.responses.extend([
            encoded({"details":{"family":"synthetic"}, "thinking":{"values":[True]}}),
            Response(b'{"message":{"thinking":"PRIVATE-REASONING","content":""},"done":false}\n'
                     b'{"message":{"content":"answer"},"done":true}\n'),
        ])
        chunks = list(self.client.stream_chat("a", [{"role":"user","content":"x"}], threading.Event()))
        self.assertEqual([chunk.text for chunk in chunks], ["", "answer"])
        self.assertTrue(self.client.thinking_required("a"))
        self.assertTrue(json.loads(self.connections[-1].request.call_args.kwargs["body"])["think"])

    def test_optional_thinking_is_disabled(self):
        self.responses.extend([
            encoded({"details":{"family":"synthetic"}, "thinking":{"values":[True,False]}}),
            Response(b'{"message":{"content":"answer"},"done":true}\n'),
        ])
        list(self.client.stream_chat("a", [{"role":"user","content":"x"}], threading.Event()))
        self.assertFalse(self.client.thinking_required("a"))
        self.assertFalse(json.loads(self.connections[-1].request.call_args.kwargs["body"])["think"])

    def test_cancel_between_connection_registration_and_request_blocks_send(self):
        class Gate:
            def __enter__(inner):
                if self.client._connection is not None:
                    self.client.cancel()
            def __exit__(inner, *args):
                return False
        self.client._state_lock = Gate()
        with self.assertRaises(Cancelled):
            self.client.verify_local("a")
        self.connections[0].request.assert_not_called()

    def test_total_stream_limit(self):
        line = json.dumps({"done":False,"message":{"content":"x" * (MAX_LINE // 2)}}).encode() + b"\n"
        self.responses.extend([local(), Response(line * 9)])
        with self.assertRaises(ClientError):
            list(self.client.stream_chat("a", [{"role":"user","content":"x"}], threading.Event()))

    def test_cancel_before_start_is_sticky_and_prevents_all_network(self):
        self.client.cancel()
        for action in (
            self.client.list_models,
            lambda: self.client.verify_local("a"),
            lambda: list(self.client.stream_chat("a", [], threading.Event())),
        ):
            with self.assertRaises(Cancelled):
                action()
        self.connection.assert_not_called()

    def test_http_failure_not_retried(self):
        self.connection.side_effect = OSError("PRIVATE server detail")
        with self.assertRaises(ClientError) as caught:
            self.client.list_models()
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(self.connection.call_count, 1)

    def test_connection_is_hardcoded_loopback_and_cancel_prevents_reconnect(self):
        real = REAL_CONNECTION
        stopped = threading.Event()
        connection = real(11434, stopped)
        self.assertEqual(connection.host, "127.0.0.1")
        self.assertEqual(connection.timeout, 3)
        stopped.set()
        with patch.object(transport.http.client.HTTPConnection, "connect") as connect:
            with self.assertRaises(Cancelled):
                connection.connect()
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
