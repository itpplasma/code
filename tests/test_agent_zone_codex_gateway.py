"""Synthetic Responses fixtures, including the installed Codex CLI consumer.

Run with: python3 -m unittest discover -s tests -p test_agent_zone_codex_gateway.py
No real Codex home, account, credentials or provider are used.
"""

import copy
import http.client
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from unittest import mock


MODULE = Path(__file__).resolve().parents[1] / "sandbox/ai_zones/codex_gateway.py"
SPEC = importlib.util.spec_from_file_location("codex_gateway_under_test", MODULE)
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)
MODEL = "fixture-model"


def tool(name="exec_command"):
    return {"type": "function", "name": name, "description": "Guest-local fixture",
            "parameters": {"type": "object", "properties": {},
                           "additionalProperties": False}, "strict": False}


def custom():
    return {"type": "custom", "name": "apply_patch", "format": {
        "type": "grammar", "syntax": "lark", "definition": 'start: "fixture"'}}


def request():
    return {"model": MODEL, "stream": True, "store": False,
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "Synthetic fixture https://example.invalid"}]}],
            "tools": [tool(), custom()], "tool_choice": "auto",
            "include": ["reasoning.encrypted_content"]}


def event(kind, **fields):
    doc = dict(type=kind, **fields)
    return ("event: " + kind + "\ndata: " + json.dumps(doc, ensure_ascii=False) + "\n\n").encode()


def response(status, items):
    return {"id": "resp_fixture", "object": "response", "status": status,
            "model": MODEL, "store": False, "output": items,
            "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 1}}}


def stream(items=None):
    if items is None:
        items = [{"type": "message", "id": "msg_fixture", "role": "assistant",
                  "status": "completed", "content": [
                      {"type": "output_text", "text": "synthetic final", "annotations": []}]}]
    pieces = [event("response.created", response=response("in_progress", []))]
    for index, item in enumerate(items):
        initial = copy.deepcopy(item)
        initial["status"] = "in_progress"
        if item["type"] == "message":
            initial["content"] = []
        field = {"function_call": "arguments", "custom_tool_call": "input"}.get(item["type"])
        if field:
            initial[field] = ""
        pieces.append(event("response.output_item.added", output_index=index, item=initial))
        if field:
            prefix = ("response.function_call_arguments" if field == "arguments"
                      else "response.custom_tool_call_input")
            value = item[field]
            for delta in (value[:len(value) // 2], value[len(value) // 2:]):
                pieces.append(event(prefix + ".delta", item_id=item["id"], output_index=index,
                                    delta=delta))
            pieces.append(event(prefix + ".done", item_id=item["id"], output_index=index,
                                **{field: value}))
        if item["type"] == "message":
            part = item["content"][0]
            pieces.append(event("response.content_part.added", item_id=item["id"],
                                output_index=index, content_index=0,
                                part={"type": "output_text", "text": "", "annotations": []}))
            pieces.append(event("response.output_text.delta", item_id=item["id"],
                                output_index=index, content_index=0, delta=part["text"]))
            pieces.append(event("response.output_text.done", item_id=item["id"],
                                output_index=index, content_index=0, text=part["text"]))
            pieces.append(event("response.content_part.done", item_id=item["id"],
                                output_index=index, content_index=0, part=part))
        pieces.append(event("response.output_item.done", output_index=index, item=item))
    pieces.append(event("response.completed", response=response("completed", items)))
    return b"".join(pieces)


class Server:
    def __init__(self, callback):
        self.calls = []
        self.callback = callback
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                owner.calls.append((self.path, dict(self.headers), body))
                try:
                    owner.callback(self, body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.http.serve_forever(poll_interval=0.01),
                                       daemon=True)

    @property
    def origin(self):
        return "http://127.0.0.1:" + str(self.http.server_port)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(2)


def send(handler, body, status=200, content_type="text/event-stream", headers=None,
         fragment=0):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    size = fragment or len(body) or 1
    for start in range(0, len(body), size):
        handler.wfile.write(body[start:start + size])
        handler.wfile.flush()


class ValidationTests(unittest.TestCase):
    def test_complete_contiguous_tool_batches_and_decoded_arguments(self):
        call = {"type": "function_call", "id": "fc1", "call_id": "c1",
                "name": "exec_command", "arguments": "{}"}
        output = {"type": "function_call_output", "id": "fco1", "call_id": "c1", "output": "x"}
        second = dict(call, id="fc2", call_id="c2")
        second_output = dict(output, id="fco2", call_id="c2")
        doc = request()
        doc["input"] += [call, second, second_output, output]
        self.assertEqual(gateway.validate_request(doc, MODEL)["input"], doc["input"])
        histories = [[call], [call, {"role": "user", "content": "intervening"}, output],
                     [call, second, output, {"role": "user", "content": "intervening"}, second_output],
                     [call, second, output, dict(call, id="fc3", call_id="c3"), second_output],
                     [call, output, output], [call, dict(output, id="fc1")],
                     [call, output, call, output]]
        for arguments in ('{"cmd":"x","cmd":"y"}', '[' * 100 + '0' + ']' * 100,
                          '{"value":NaN}', '{"value":Infinity}', '{"value":"\\ud800"}'):
            histories.append([dict(call, arguments=arguments), output])
        for history in histories:
            doc = request()
            doc["input"] += history
            with self.subTest(history=history), self.assertRaises(gateway.GatewayError):
                gateway.validate_request(doc, MODEL)
        doc = request()
        doc["tools"][0]["parameters"] = {"type": "object", "$ref": "#/default",
                                          "default": {"$ref": "https://example.invalid/hidden"}}
        with self.assertRaises(gateway.GatewayError):
            gateway.validate_request(doc, MODEL)

    def test_stateless_function_custom_and_reasoning_replay(self):
        doc = request()
        history = [
            {"type": "reasoning", "id": "rs_fixture", "summary": [],
             "encrypted_content": "opaque-synthetic-reasoning"},
            {"type": "function_call", "call_id": "f", "name": "exec_command", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "f", "output": "synthetic result"},
            {"type": "custom_tool_call", "call_id": "c", "name": "apply_patch", "input": "fixture"},
            {"type": "custom_tool_call_output", "call_id": "c", "output": [
                {"type": "input_text", "text": "synthetic patch result"}]}]
        doc["input"].extend(history)
        result = gateway.validate_request(doc, MODEL)
        self.assertEqual(result["input"][1:], history)
        self.assertIsNot(result["input"], doc["input"])
        self.assertEqual(doc, gateway.validate_request(result, MODEL))
        del doc["model"]
        self.assertEqual(gateway.validate_request(doc, MODEL)["model"], MODEL)

    def test_local_json_schema_and_output_format(self):
        doc = request()
        schema = {"type": "object", "$defs": {"word": {"type": "string"}},
                  "properties": {"name": {"$ref": "#/$defs/word"}}, "required": ["name"],
                  "additionalProperties": False}
        doc["tools"][0]["parameters"] = schema
        doc["text"] = {"verbosity": "low", "format": {
            "type": "json_schema", "name": "fixture", "schema": schema, "strict": True}}
        self.assertEqual(gateway.validate_request(doc, MODEL)["text"], doc["text"])

    def test_reject_capability_and_nested_overrides_before_network(self):
        edits = [
            ("model", "other"), ("previous_response_id", "resp_other"),
            ("conversation", "conv_other"), ("background", True), ("store", True),
            ("headers", {"Authorization": "caller"}), ("base_url", "https://example.invalid"),
            ("url", "https://example.invalid"), ("account_id", "other"),
            ("include", ["web_search_call.action.sources"]),
            ("reasoning", {"effort": "low", "url": "https://example.invalid"}),
            ("text", {"format": {"type": "json_schema", "name": "x", "schema": {
                "type": "string", "$ref": "https://example.invalid/schema"}}}),
            ("client_metadata", {"Authorization": "caller"}),
            ("tool_choice", {"type": "function", "name": "undeclared"}),
            ("tool_choice", {"type": "web_search"})]
        variants = []
        for key, value in edits:
            doc = request()
            doc[key] = value
            variants.append(doc)
        for kind in ("web_search", "file_search", "mcp", "computer", "computer_use_preview",
                     "code_interpreter", "shell", "tool_search", "namespace"):
            doc = request()
            doc["tools"].append({"type": kind, "name": "remote", "tools": [tool()]})
            variants.append(doc)
        for kind in ("input_image", "input_audio", "input_file", "item_reference"):
            doc = request()
            doc["input"][0]["content"] = [{"type": kind, "url": "https://example.invalid"}]
            variants.append(doc)
        for key, value in (("connector_id", "x"), ("parameters", {"type": "object", "x": True})):
            doc = request()
            doc["tools"][0][key] = value
            variants.append(doc)
        doc = request()
        doc["input"].append({"type": "function_call_output", "call_id": "unknown", "output": "x"})
        variants.append(doc)
        with Server(lambda h, b: self.fail("rejected request reached upstream")) as server:
            adapter = gateway.CodexGateway(server.origin, MODEL, "dummy-host-token")
            for doc in variants:
                with self.subTest(doc=doc), self.assertRaises(gateway.GatewayError):
                    adapter.request("/v1/responses", doc)
            self.assertEqual(server.calls, [])

    def test_bounds_and_invalid_values(self):
        docs = []
        for key, value in (("input", "x" * (gateway.MAX_STRING + 1)),
                           ("max_output_tokens", True), ("temperature", float("nan")),
                           ("max_output_tokens", 10 ** 1000), ("input", "\ud800"),
                           ("stream", False), ("tools", [tool()] * 65)):
            doc = request()
            doc[key] = value
            docs.append(doc)
        for nested in ({"type": "string", "$ref": "#/$defs/missing"},
                       {"type": "string", "unexpected": True}):
            doc = request()
            doc["tools"][0]["parameters"] = nested
            docs.append(doc)
        cycle = []
        cycle.append(cycle)
        docs.append({"input": cycle})
        for doc in docs:
            with self.subTest(doc=str(doc)[:120]), self.assertRaises(gateway.GatewayError):
                gateway.validate_request(doc, MODEL)
        with mock.patch.object(gateway, "MAX_REQUEST_BYTES", 10):
            with self.assertRaises(gateway.GatewayError) as caught:
                gateway.validate_request(request(), MODEL)
            self.assertEqual(caught.exception.status, 413)

    def test_fixed_routes_origins_and_catalog(self):
        for origin in ("http://example.invalid", "https://u:p@example.invalid",
                       "https://example.invalid?x=1", "https://example.invalid/#x",
                       "https://example.invalid/other", "http://127.0.0.1/%76%31"):
            with self.subTest(origin=origin), self.assertRaises(gateway.GatewayError):
                gateway.CodexGateway(origin, MODEL, "dummy")
        for token, account in (("bad\r\nToken", None), (None, None), ("dummy", "bad\n")):
            with self.assertRaises(gateway.GatewayError):
                gateway.CodexGateway("https://example.invalid", MODEL, token, account)
        adapter = gateway.CodexGateway("https://example.invalid", MODEL, "dummy")
        for path in ("https://example.invalid/v1/responses", "/v1/responses?x=1", "/responses",
                     "/v1/responses/compact", "/v1/../v1/responses", "/v1/files", "/login"):
            with self.subTest(path=path), self.assertRaises(gateway.GatewayError):
                adapter.request(path, request())
        with adapter.request("/v1/models") as result:
            self.assertEqual(json.loads(b"".join(result.iter_bytes()))["data"][0]["id"], MODEL)

    def test_duplicate_raw_keys_require_broker_decoder_rejection(self):
        for raw in (b'{"input":"x","input":"y"}',
                    b'{"input":[],"reasoning":{"summary":"auto","summary":"none"}}'):
            with self.subTest(raw=raw), self.assertRaises(gateway.GatewayError):
                gateway._json(raw)


class TransportTests(unittest.TestCase):
    def test_strict_chunk_endings_never_forward_terminal(self):
        payload = stream() + b"data: [DONE]\n\n"
        prefix = ("%x\r\n" % len(payload)).encode() + payload
        late = event("response.output_text.delta", item_id="msg_fixture", output_index=0,
                     content_index=0, delta="late")
        tails = [b"\r\n0\r\n", b"XX0\r\n\r\n", b"\r\n0\r\nBad Trailer\r\n\r\n",
                 b"\r\n0\r\nX-Fixture: value\n\n", b"\r\nZ\r\n\r\n",
                 b"\r\n" + ("%x\r\n" % len(late)).encode() + late + b"\r\n0\r\n\r\n"]
        for tail in tails:
            forwarded = []
            with self.subTest(tail=tail), Server(lambda h, b: send(h, prefix + tail,
                    headers={"Transfer-Encoding": "chunked"})) as server:
                with self.assertRaises(gateway.GatewayError):
                    with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                            "/v1/responses", request()) as result:
                        for chunk in result.iter_bytes():
                            forwarded.append(chunk)
            self.assertNotIn(b"response.completed", b"".join(forwarded))
            self.assertNotIn(b"[DONE]", b"".join(forwarded))

    def test_text_remains_incremental_but_terminal_waits_for_trailer(self):
        payload = stream() + b"data: [DONE]\n\n"
        release, delta, finished = threading.Event(), threading.Event(), threading.Event()
        forwarded, errors = [], []

        def upstream(handler, body):
            send(handler, ("%x\r\n" % len(payload)).encode() + payload + b"\r\n",
                 headers={"Transfer-Encoding": "chunked"})
            release.wait(1)
            handler.wfile.write(b"0\r\nX-Fixture: value\r\n\r\n")
            handler.wfile.flush()

        with Server(upstream) as server:
            def read():
                try:
                    with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                            "/v1/responses", request(), deadline=time.monotonic() + 1) as result:
                        for chunk in result.iter_bytes():
                            forwarded.append(chunk)
                            if b"response.output_text.delta" in chunk:
                                delta.set()
                except Exception as error:
                    errors.append(error)
                finally:
                    finished.set()

            worker = threading.Thread(target=read)
            worker.start()
            try:
                self.assertTrue(delta.wait(0.5))
                self.assertNotIn(b"response.completed", b"".join(forwarded))
                self.assertNotIn(b"[DONE]", b"".join(forwarded))
                self.assertFalse(finished.is_set())
                release.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(b"".join(forwarded), payload)
            finally:
                release.set()
                worker.join(1)

    def test_exact_framing_completes_while_provider_keeps_tcp_open(self):
        payload = stream()
        for chunked, whitespace in ((False, ""), (False, " \t"), (True, ""), (True, " \t")):
            release = threading.Event()

            def upstream(handler, body):
                headers = {"Connection": "keep-alive"}
                if chunked:
                    headers["Transfer-Encoding"] = "chunked" + whitespace
                    wire = ("%x\r\n" % len(payload)).encode() + payload + b"\r\n0\r\n\r\n"
                else:
                    headers["Content-Length"] = str(len(payload)) + whitespace
                    wire = payload
                send(handler, wire, headers=headers)
                release.wait(1)

            with self.subTest(chunked=chunked, whitespace=whitespace), Server(upstream) as server:
                try:
                    start = time.monotonic()
                    with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                            "/v1/responses", request(), deadline=start + 0.4) as result:
                        self.assertEqual(b"".join(result.iter_bytes()), payload)
                    self.assertLess(time.monotonic() - start, 0.4)
                    self.assertFalse(release.is_set())
                finally:
                    release.set()

    def test_content_length_eof_and_incomplete_http_framing(self):
        body = stream()
        for fragment in (0, 7):
            with self.subTest(fragment=fragment), Server(lambda h, b: send(h, body,
                    headers={"Content-Length": str(len(body))}, fragment=fragment)) as server:
                with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                        "/v1/responses", request()) as result:
                    self.assertIsInstance(result._upstream, http.client.HTTPResponse)
                    self.assertEqual(b"".join(result.iter_bytes()), body)
                    self.assertTrue(result._upstream.isclosed())
        late = event("response.output_text.delta", output_index=0, item_id="msg_fixture",
                     content_index=0, delta="after terminal")
        for payload, length in ((body, len(body) + 1), (body[:-1], len(body)),
                                (body + late, len(body + late))):
            forwarded = []
            with self.subTest(length=length), Server(lambda h, b: send(h, payload,
                    headers={"Content-Length": str(length)})) as server:
                with self.assertRaises(gateway.GatewayError):
                    with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                            "/v1/responses", request()) as result:
                        for chunk in result.iter_bytes():
                            forwarded.append(chunk)
            self.assertNotIn(b"response.completed", b"".join(forwarded))

    def test_trusted_deadline_and_cancel_interrupt_pending_headers(self):
        for mode in ("deadline", "cancel"):
            entered, release, observed = threading.Event(), threading.Event(), threading.Event()
            callbacks, outcomes, peer_closed = [], [], []
            slots = threading.BoundedSemaphore(1)
            count = [0]

            def upstream(handler, body):
                count[0] += 1
                if count[0] > 1:
                    send(handler, stream())
                    return
                entered.set()
                release.wait(2)
                handler.connection.settimeout(0.2)
                try:
                    peer_closed.append(handler.connection.recv(1) == b"")
                except socket.timeout:
                    peer_closed.append(False)
                observed.set()

            with self.subTest(mode=mode), Server(upstream) as server:
                adapter = gateway.CodexGateway(server.origin, MODEL, "dummy")

                def run():
                    try:
                        with adapter.request("/v1/responses", request(),
                                deadline=time.monotonic() + (0.08 if mode == "deadline" else 2),
                                register_cancel=callbacks.append) as result:
                            list(result.iter_bytes())
                        outcomes.append("accepted")
                    except gateway.GatewayError as error:
                        outcomes.append(error.code)

                worker = threading.Thread(target=run)
                with mock.patch.object(gateway, "_OPERATIONS", slots):
                    try:
                        worker.start()
                        self.assertTrue(entered.wait(1))
                        self.assertEqual(len(callbacks), 1)
                        start = time.monotonic()
                        if mode == "cancel":
                            callbacks[0]()
                        worker.join(0.3)
                        self.assertFalse(worker.is_alive())
                        self.assertLess(time.monotonic() - start, 0.3)
                        self.assertEqual(outcomes, ["upstream_timeout"])
                        self.assertTrue(slots.acquire(timeout=0.2))
                        slots.release()
                        release.set()
                        self.assertTrue(observed.wait(0.5))
                        self.assertEqual(peer_closed, [True])
                        with adapter.request("/v1/responses", request(), deadline=time.monotonic() + 0.5) as result:
                            self.assertEqual(b"".join(result.iter_bytes()), stream())
                    finally:
                        release.set()
                        worker.join(1)

    def test_trusted_cancel_before_dns_and_expired_deadlines_send_nothing(self):
        with Server(lambda h, b: self.fail("cancelled request reached upstream")) as server:
            adapter = gateway.CodexGateway(server.origin, MODEL, "dummy")
            with mock.patch.object(socket, "getaddrinfo") as resolve:
                for kwargs in ({"register_cancel": lambda cancel: cancel()},
                               {"deadline": time.monotonic() - 1}):
                    with self.assertRaises(gateway.GatewayError) as caught:
                        adapter.request("/v1/responses", request(), **kwargs)
                    self.assertEqual(caught.exception.code, "upstream_timeout")
                resolve.assert_not_called()
            self.assertEqual(server.calls, [])
            for kwargs in ({"deadline": float("nan")}, {"deadline": True}, {"deadline": "later"},
                           {"deadline": 10 ** 1000},
                           {"register_cancel": False}):
                with self.assertRaises(gateway.GatewayError) as caught:
                    adapter.request("/v1/responses", request(), **kwargs)
                self.assertEqual(caught.exception.status, 500)

    def test_trusted_cancellation_wakes_dns_without_late_connect(self):
        entered, release, resolved, registered = [threading.Event() for _ in range(4)]
        callbacks, outcomes = [], []
        original = socket.getaddrinfo

        def resolve(*args, **kwargs):
            entered.set()
            release.wait(2)
            try:
                return original(*args, **kwargs)
            finally:
                resolved.set()

        def register(cancel):
            callbacks.append(cancel)
            registered.set()

        with Server(lambda h, b: send(h, stream())) as server:
            adapter = gateway.CodexGateway(server.origin, MODEL, "dummy")

            def run():
                try:
                    adapter.request("/v1/responses", request(), register_cancel=register)
                    outcomes.append("accepted")
                except gateway.GatewayError as error:
                    outcomes.append(error.code)

            worker = threading.Thread(target=run)
            with mock.patch.object(socket, "getaddrinfo", resolve):
                try:
                    worker.start()
                    self.assertTrue(registered.wait(1))
                    self.assertTrue(entered.wait(1))
                    callbacks[0]()
                    worker.join(0.2)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(outcomes, ["upstream_timeout"])
                    self.assertEqual(server.calls, [])
                finally:
                    release.set()
                    self.assertTrue(resolved.wait(1))
                    worker.join(1)
            self.assertEqual(server.calls, [])

    def test_trusted_deadline_caps_response_body(self):
        with Server(lambda h, b: send(h, stream())) as server:
            with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                    "/v1/responses", request(), deadline=time.monotonic() + 0.05) as result:
                iterator = result.iter_bytes()
                next(iterator)
                time.sleep(0.08)
                with self.assertRaises(gateway.GatewayError) as caught:
                    next(iterator)
                self.assertEqual(caught.exception.code, "upstream_timeout")

    def test_parallel_calls_and_incremental_reasoning_remain_valid(self):
        calls = [{"type": "function_call", "id": "fc" + str(index), "call_id": "c" + str(index),
                  "name": "exec_command", "arguments": '{"cmd":"printf synthetic"}',
                  "status": "completed"} for index in range(2)]
        docs = [json.loads(block.split(b"data: ", 1)[1])
                for block in stream(calls).split(b"\n\n") if block]
        parallel = [docs[index] for index in (0, 1, 6, 7, 2, 8, 3, 9, 4, 10, 5, 11)]
        reasoning = {"type": "reasoning", "id": "rs", "summary": [
            {"type": "summary_text", "text": "Synthetic reasoning"}], "content": None,
            "encrypted_content": "opaque-synthetic-reasoning", "status": "completed"}
        part = reasoning["summary"][0]
        reasoning_stream = [
            dict(type="response.created", response=response("in_progress", [])),
            dict(type="response.output_item.added", output_index=0,
                 item={"type": "reasoning", "id": "rs", "summary": [], "status": "in_progress"}),
            dict(type="response.reasoning_summary_part.added", output_index=0, item_id="rs",
                 summary_index=0, part={"type": "summary_text", "text": ""}),
            dict(type="response.reasoning_summary_text.delta", output_index=0, item_id="rs",
                 summary_index=0, delta=part["text"]),
            dict(type="response.reasoning_summary_text.done", output_index=0, item_id="rs",
                 summary_index=0, text=part["text"]),
            dict(type="response.reasoning_summary_part.done", output_index=0, item_id="rs",
                 summary_index=0, part=part),
            dict(type="response.output_item.done", output_index=0, item=reasoning),
            dict(type="response.completed", response=response("completed", [reasoning]))]
        partial = dict(calls[0], arguments='{"cmd":', status="incomplete")
        failure = [dict(type="response.created", response=response("in_progress", [])),
                   dict(type="response.output_item.added", output_index=0,
                        item=dict(partial, arguments="", status="in_progress")),
                   dict(type="response.function_call_arguments.delta", output_index=0,
                        item_id=partial["id"], delta=partial["arguments"]),
                   dict(type="response.failed", response=dict(response("failed", [partial]),
                        error={"code": "server_error", "message": "Synthetic failure"}))]
        for documents in (parallel, reasoning_stream, failure):
            body = b"".join(event(doc["type"], **{key: value for key, value in doc.items() if key != "type"})
                            for doc in documents)
            with Server(lambda h, b: send(h, body, fragment=7)) as server:
                with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                        "/v1/responses", request()) as result:
                    self.assertEqual(b"".join(result.iter_bytes()), body)

    def test_incremental_item_argument_and_terminal_sequence(self):
        call = {"type": "function_call", "id": "fc1", "call_id": "c1", "name": "exec_command",
                "arguments": '{"cmd":"printf synthetic"}', "status": "completed"}
        base = [json.loads(block.split(b"data: ", 1)[1])
                for block in stream([call]).split(b"\n\n") if block]
        variants = []
        for position, field, value in ((2, "item_id", "undeclared"), (2, "output_index", 123),
                                       (4, "arguments", '{}')):
            docs = copy.deepcopy(base)
            docs[position][field] = value
            variants.append(docs)
        docs = copy.deepcopy(base)
        docs[-1]["response"]["output"] = []
        variants.append(docs)
        docs = copy.deepcopy(base)
        docs[1]["item"]["id"] = "different"
        variants.append(docs)
        docs = copy.deepcopy(base)
        docs.insert(2, copy.deepcopy(docs[1]))
        variants.append(docs)
        docs = copy.deepcopy(base)
        docs.insert(-1, copy.deepcopy(docs[-2]))
        variants.append(docs)
        docs = copy.deepcopy(base)
        docs[0]["sequence_number"], docs[1]["sequence_number"] = 2, 1
        variants.append(docs)
        variants.append([base[0], dict(type="response.function_call_arguments.delta",
                         item_id="never_declared", output_index=123, delta='{}'),
                         dict(type="response.completed", response=response("completed", []))])
        for arguments in ('{"cmd":"x","cmd":"y"}', '[' * 100 + '0' + ']' * 100,
                          '{"value":NaN}', '{"value":Infinity}'):
            variants.append([json.loads(block.split(b"data: ", 1)[1]) for block in
                             stream([dict(call, arguments=arguments)]).split(b"\n\n") if block])
        docs = copy.deepcopy(base)
        docs[2]["delta"] = "x" * gateway.MAX_STRING
        variants.append(docs)
        for docs in variants:
            body = b"".join(event(doc["type"], **{key: value for key, value in doc.items() if key != "type"})
                            for doc in docs)
            with self.subTest(body=body[:100]), Server(lambda h, b: send(h, body)) as server:
                with self.assertRaises(gateway.GatewayError):
                    with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                            "/v1/responses", request()) as result:
                        list(result.iter_bytes())

    def test_stream_cannot_reuse_history_item_or_call_identifiers(self):
        call = {"type": "function_call", "id": "fc1", "call_id": "c1", "name": "exec_command",
                "arguments": '{}', "status": "completed"}
        prior = dict(call, id="fc_old")
        for history, body in (([prior, {"type": "function_call_output", "call_id": "c1", "output": "x"}],
                               stream([call])),
                              ([{"type": "message", "id": "msg_fixture", "role": "assistant", "content": []}],
                               stream())):
            doc = request()
            doc["input"] += history
            with Server(lambda h, b: send(h, body)) as server:
                with self.assertRaises(gateway.GatewayError):
                    with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                            "/v1/responses", doc) as result:
                        list(result.iter_bytes())

    def test_absolute_dns_and_header_deadline(self):
        original = socket.getaddrinfo
        resolved = threading.Event()

        def slow_resolve(*args, **kwargs):
            try:
                time.sleep(0.2)
                return original(*args, **kwargs)
            finally:
                resolved.set()

        with Server(lambda h, b: send(h, stream())) as server:
            with mock.patch.object(gateway, "IO_TIMEOUT", 0.04), mock.patch.object(gateway, "STREAM_DEADLINE", 0.04):
                with mock.patch.object(socket, "getaddrinfo", slow_resolve):
                    start = time.monotonic()
                    with self.assertRaises(gateway.GatewayError) as caught:
                        gateway.CodexGateway(server.origin, MODEL, "dummy").request("/v1/responses", request())
                    self.assertEqual(caught.exception.code, "upstream_timeout")
                    self.assertLess(time.monotonic() - start, 0.15)
                    self.assertTrue(resolved.wait(1))
                    self.assertEqual(server.calls, [])

        def drip_headers(handler, body):
            for byte in b"HTTP/1.0 200 OK\r\nContent-Type: text/event-stream\r\n\r\n":
                handler.connection.sendall(bytes([byte]))
                time.sleep(0.006)
            handler.wfile.write(stream())

        with Server(drip_headers) as server:
            with mock.patch.object(gateway, "IO_TIMEOUT", 0.04), mock.patch.object(gateway, "STREAM_DEADLINE", 0.04):
                start = time.monotonic()
                with self.assertRaises(gateway.GatewayError) as caught:
                    gateway.CodexGateway(server.origin, MODEL, "dummy").request("/v1/responses", request())
                self.assertEqual(caught.exception.code, "upstream_timeout")
                self.assertLess(time.monotonic() - start, 0.15)

    def test_absolute_deadline_applies_to_buffered_events(self):
        with Server(lambda h, b: send(h, stream())) as server:
            with mock.patch.object(gateway, "STREAM_DEADLINE", 0.05):
                with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                        "/v1/responses", request()) as result:
                    iterator = result.iter_bytes()
                    next(iterator)
                    time.sleep(0.08)
                    with self.assertRaises(gateway.GatewayError) as caught:
                        next(iterator)
                    self.assertEqual(caught.exception.code, "upstream_timeout")

    def test_absolute_tls_handshake_deadline(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        hello, release = threading.Event(), threading.Event()

        def hold_tls():
            peer, _ = listener.accept()
            with peer:
                if peer.recv(65536):
                    hello.set()
                release.wait(1)

        worker = threading.Thread(target=hold_tls, daemon=True)
        worker.start()
        try:
            with mock.patch.object(gateway, "IO_TIMEOUT", 0.15), mock.patch.object(gateway, "STREAM_DEADLINE", 0.15):
                start = time.monotonic()
                with self.assertRaises(gateway.GatewayError) as caught:
                    gateway.CodexGateway("https://127.0.0.1:" + str(listener.getsockname()[1]),
                                         MODEL, "dummy").request("/v1/responses", request())
                self.assertEqual(caught.exception.code, "upstream_timeout")
                self.assertTrue(hello.is_set())
                self.assertLess(time.monotonic() - start, 0.4)
        finally:
            release.set()
            listener.close()
            worker.join(1)

    def test_bounded_resolvers_never_own_sending_sockets(self):
        release, entered = threading.Event(), threading.Condition()
        count, outcomes = [0], []
        original = socket.getaddrinfo

        def held_resolve(*args, **kwargs):
            with entered:
                count[0] += 1
                entered.notify_all()
            release.wait(2)
            return original(*args, **kwargs)

        with Server(lambda h, b: send(h, stream())) as server:
            def run():
                try:
                    gateway.CodexGateway(server.origin, MODEL, "dummy").request("/v1/responses", request())
                    outcomes.append("accepted")
                except gateway.GatewayError as error:
                    outcomes.append(error.code)

            workers = [threading.Thread(target=run) for _ in range(4)]
            with mock.patch.object(socket, "getaddrinfo", held_resolve), mock.patch.object(gateway, "IO_TIMEOUT", 0.1), \
                    mock.patch.object(gateway, "_RESOLVERS", threading.BoundedSemaphore(4)):
                try:
                    for worker in workers:
                        worker.start()
                    with entered:
                        self.assertTrue(entered.wait_for(lambda: count[0] == 4, timeout=1))
                    run()
                    for worker in workers:
                        worker.join(1)
                    self.assertEqual(sorted(outcomes), ["resolver_busy"] + ["upstream_timeout"] * 4)
                    self.assertEqual(count[0], 4)
                    self.assertEqual(server.calls, [])
                finally:
                    release.set()
                    for worker in workers:
                        worker.join(1)

    def test_close_interrupts_a_concurrent_blocked_reader(self):
        release, reading, finished = threading.Event(), threading.Event(), threading.Event()
        errors = []

        def hold(handler, body):
            send(handler, event("response.created", response=response("in_progress", [])))
            release.wait(1)

        with Server(hold) as server:
            result = gateway.CodexGateway(server.origin, MODEL, "dummy").request("/v1/responses", request())
            iterator = result.iter_bytes()
            next(iterator)

            def read():
                reading.set()
                try:
                    next(iterator)
                except (gateway.GatewayError, StopIteration):
                    pass
                except Exception as error:
                    errors.append(error)
                finally:
                    finished.set()

            worker = threading.Thread(target=read)
            worker.start()
            self.assertTrue(reading.wait(1))
            time.sleep(0.02)
            try:
                start = time.monotonic()
                result.close()
                self.assertLess(time.monotonic() - start, 0.15)
                self.assertTrue(finished.wait(0.15))
                self.assertEqual(errors, [])
            finally:
                release.set()
                worker.join(1)

    def test_fragmented_sse_fixed_auth_and_both_upstream_prefixes(self):
        body = stream()
        for prefix, expected in (("", "/v1/responses"), ("/v1", "/v1/responses"),
                                 ("/backend-api/codex", "/backend-api/codex/responses")):
            with Server(lambda h, b: send(h, body, fragment=7, headers={
                    "X-RateLimit-Remaining-Tokens": "100"})) as server:
                adapter = gateway.CodexGateway(server.origin + prefix, MODEL,
                                               "dummy-host-token", "dummy-fixed-account")
                with mock.patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1",
                                                   "HTTP_PROXY": "http://127.0.0.1:1"}):
                    with adapter.request("/v1/responses", request()) as result:
                        self.assertEqual(result.status, 200)
                        self.assertEqual(result.headers["x-ratelimit-remaining-tokens"], "100")
                        self.assertEqual(b"".join(result.iter_bytes()), body)
                path, headers, encoded = server.calls[0]
                self.assertEqual(path, expected)
                self.assertEqual(headers["Authorization"], "Bearer dummy-host-token")
                self.assertEqual(headers["ChatGPT-Account-ID"], "dummy-fixed-account")
                self.assertEqual(json.loads(encoded)["model"], MODEL)
                self.assertNotIn("Cookie", headers)

    def test_custom_tool_stream_and_reasoning(self):
        items = [{"type": "reasoning", "id": "rs_fixture", "summary": [],
                  "encrypted_content": "opaque-synthetic-reasoning"},
                 {"type": "custom_tool_call", "id": "ct_fixture", "call_id": "c1",
                  "name": "apply_patch", "input": "fixture", "status": "completed"}]
        body = stream(items)
        with Server(lambda h, b: send(h, body, fragment=1)) as server:
            with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                    "/v1/responses", request()) as result:
                self.assertEqual(b"".join(result.iter_bytes()), body)

    def test_fragmented_utf8_crlf_and_multiline_data(self):
        items = [{"type": "message", "id": "msg_unicode", "role": "assistant",
                  "status": "completed", "content": [{"type": "output_text",
                  "text": "Synthetic café\u2028文字", "annotations": []}]}]
        body = stream(items).replace(b"\n", b"\r\n")
        body = body.replace(b'data: {"type": "response.created", ',
                            b'data: {"type": "response.created",\r\ndata: ')
        with Server(lambda h, b: send(h, body, fragment=1)) as server:
            with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                    "/v1/responses", request()) as result:
                self.assertEqual(b"".join(result.iter_bytes()), body)

    def test_status_rate_limit_redirect_content_type(self):
        for status in (401, 429):
            with Server(lambda h, b: send(h, b"provider body deliberately hidden", status,
                                         "application/json", {"Retry-After": "3",
                                         "X-RateLimit-Remaining-Requests": "0",
                                         "Set-Cookie": "never-forward"})) as server:
                with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                        "/v1/responses", request()) as result:
                    self.assertEqual(result.status, status)
                    self.assertEqual(result.headers["retry-after"], "3")
                    self.assertNotIn("set-cookie", result.headers)
                    self.assertNotIn(b"provider body", b"".join(result.iter_bytes()))
        for status, content_type, headers in ((302, "text/event-stream", {"Location": "https://example.invalid"}),
                                              (200, "text/html", {}),
                                              (200, "text/event-stream", {"Content-Encoding": "gzip"})):
            with Server(lambda h, b: send(h, b"", status, content_type, headers)) as server:
                with self.assertRaises(gateway.GatewayError):
                    gateway.CodexGateway(server.origin, MODEL, "dummy").request("/v1/responses", request())
                self.assertEqual(len(server.calls), 1)

    def test_malformed_hosted_truncated_and_duplicate_sse(self):
        created = event("response.created", response=response("in_progress", []))
        bad = [b"data: not-json\n\n", b"data: {\"type\":\"error\",\"type\":\"error\"}\n\n",
               b"data: [DONE]\n\n", created, created + b"data: {}",
               created + event("response.web_search_call.in_progress"),
               created + event("response.output_item.added", output_index=0,
                               item={"type": "web_search_call", "id": "hosted"}),
               created + event("response.completed", response=response("completed", [
                   {"type": "function_call", "call_id": "c", "name": "undeclared", "arguments": "{}"}])),
               created + event("response.completed", response=dict(response("completed", []), unknown=True)),
               created + event("response.output_text.delta", delta="missing item and indices"),
               stream() + event("response.output_text.delta", output_index=0, content_index=0,
                                item_id="msg_fixture", delta="after completion")]
        for body in bad:
            with self.subTest(body=body[:120]), Server(lambda h, b: send(h, body)) as server:
                with self.assertRaises(gateway.GatewayError):
                    with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                            "/v1/responses", request()) as result:
                        list(result.iter_bytes())
                self.assertTrue(result._closed)

    def test_error_failed_and_incomplete_streams_preserved(self):
        created = event("response.created", response=response("in_progress", []))
        failed = dict(response("failed", []), error={"code": "server_error", "message": "Synthetic failure"})
        incomplete = dict(response("incomplete", []), incomplete_details={"reason": "max_output_tokens"})
        bodies = [event("error", code="server_error", message="Synthetic failure", param=None),
                  created + event("response.failed", response=failed),
                  created + event("response.incomplete", response=incomplete)]
        for body in bodies:
            with Server(lambda h, b: send(h, body)) as server:
                with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                        "/v1/responses", request()) as result:
                    self.assertEqual(b"".join(result.iter_bytes()), body)

    def test_stream_limits_and_deadline(self):
        body = stream()
        for constant, limit in (("MAX_RESPONSE_BYTES", 30), ("MAX_EVENT_BYTES", 30), ("MAX_EVENTS", 1)):
            with self.subTest(constant=constant), Server(lambda h, b: send(h, body)) as server:
                with mock.patch.object(gateway, constant, limit):
                    with self.assertRaises(gateway.GatewayError) as caught:
                        with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                                "/v1/responses", request()) as result:
                            list(result.iter_bytes())
                    self.assertEqual(caught.exception.code, "upstream_limit")

        def slow(handler, body):
            send(handler, b": ping\n\n")
            time.sleep(0.2)

        with Server(slow) as server, mock.patch.object(gateway, "IO_TIMEOUT", 0.04):
            with self.assertRaises(gateway.GatewayError) as caught:
                with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                        "/v1/responses", request()) as result:
                    list(result.iter_bytes())
            self.assertEqual(caught.exception.code, "upstream_timeout")
        with Server(lambda h, b: send(h, body)) as server:
            with mock.patch.object(gateway, "STREAM_DEADLINE", -1):
                with self.assertRaises(gateway.GatewayError) as caught:
                    with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                            "/v1/responses", request()) as result:
                        list(result.iter_bytes())
                self.assertEqual(caught.exception.code, "upstream_timeout")

        def dribble(handler, body):
            send(handler, b": ping\n\n")
            for _ in range(20):
                time.sleep(0.02)
                handler.wfile.write(b": ping\n\n")
                handler.wfile.flush()

        with Server(dribble) as server, mock.patch.object(gateway, "STREAM_DEADLINE", 0.06):
            with mock.patch.object(gateway, "IO_TIMEOUT", 0.3):
                start = time.monotonic()
                with self.assertRaises(gateway.GatewayError) as caught:
                    with gateway.CodexGateway(server.origin, MODEL, "dummy").request(
                            "/v1/responses", request()) as result:
                        list(result.iter_bytes())
                self.assertEqual(caught.exception.code, "upstream_timeout")
                self.assertLess(time.monotonic() - start, 0.25)

    def test_request_header_timeout(self):
        def slow_headers(handler, body):
            time.sleep(0.15)
            send(handler, stream())

        with Server(slow_headers) as server, mock.patch.object(gateway, "IO_TIMEOUT", 0.03):
            with self.assertRaises(gateway.GatewayError) as caught:
                gateway.CodexGateway(server.origin, MODEL, "dummy").request("/v1/responses", request())
            self.assertEqual(caught.exception.code, "upstream_timeout")

    def test_close_on_consumer_disconnect(self):
        with Server(lambda h, b: send(h, stream())) as server:
            result = gateway.CodexGateway(server.origin, MODEL, "dummy").request("/v1/responses", request())
            iterator = result.iter_bytes()
            self.assertIn(b"response.created", next(iterator))
            result.close()
            iterator.close()
            self.assertTrue(result._upstream.closed)
            self.assertIsNone(result._connection.sock)


def installed_codex():
    vendor = Path("/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai")
    matches = list(vendor.glob("codex-*/vendor/*/bin/codex")) if vendor.is_dir() else []
    return str(matches[0]) if matches else shutil.which("codex")


def fixture_catalog():
    # Required fields were checked against the installed 0.165.0 catalog parser.
    return {"models": [{"slug": MODEL, "display_name": "Synthetic fixture",
        "description": "Local protocol test", "base_instructions": "Synthetic protocol test only.",
        "shell_type": "shell_command", "visibility": "list", "supported_in_api": True,
        "priority": 0, "supported_reasoning_levels": [], "support_verbosity": False,
        "supports_reasoning_summaries": True, "supports_parallel_tool_calls": True,
        "apply_patch_tool_type": "freeform", "context_window": 8192,
        "truncation_policy": {"mode": "tokens", "limit": 1000},
        "input_modalities": ["text"], "experimental_supported_tools": []}]}


class ActualCodexTests(unittest.TestCase):
    @unittest.skipUnless(installed_codex(), "installed Codex CLI unavailable")
    def test_actual_cli_function_tool_result_continuation(self):
        failures, captures, paths = [], [], []
        reasoning = {"type": "reasoning", "id": "rs_synthetic", "summary": [],
                     "content": None, "encrypted_content": "opaque-synthetic-reasoning"}
        call = {"type": "function_call", "id": "fc_synthetic", "call_id": "synthetic_call",
                "name": "exec_command", "arguments": json.dumps({
                    "cmd": "printf 'synthetic-tool-result\\n'", "login": False,
                    "max_output_tokens": 100, "yield_time_ms": 1000}), "status": "completed"}

        def upstream(handler, body):
            doc = json.loads(body)
            captures.append(doc)
            payload = stream([reasoning, call]) if len(captures) == 1 else stream()
            send(handler, payload, fragment=11, headers={"Content-Length": str(len(payload))})

        with Server(upstream) as provider:
            adapter = gateway.CodexGateway(provider.origin, MODEL, "dummy-host-token")

            class Harness(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    paths.append(self.path)
                    if self.path != "/v1/responses":
                        self.send_error(404)
                        return
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= gateway.MAX_REQUEST_BYTES:
                        self.send_error(413)
                        return
                    try:
                        doc = gateway._json(self.rfile.read(size))
                        with adapter.request(self.path, doc) as result:
                            self.send_response(result.status)
                            self.send_header("Content-Type", result.content_type)
                            self.end_headers()
                            for chunk in result.iter_bytes():
                                self.wfile.write(chunk)
                                self.wfile.flush()
                    except Exception as error:
                        failures.append(traceback.format_exc())
                        self.close_connection = True

                def do_GET(self):
                    paths.append(self.path)
                    self.send_error(404)

                def do_CONNECT(self):
                    self.send_error(405)

            local = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Harness)
            local.daemon_threads = True
            thread = threading.Thread(target=local.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory(prefix="codex-gateway-test-") as temporary:
                    root = Path(temporary).resolve()
                    home, codex_home, work = [root / name for name in ("home", "codex", "work")]
                    for directory in (home, codex_home, work):
                        directory.mkdir(mode=0o700)
                    catalog = root / "catalog.json"
                    catalog.write_text(json.dumps(fixture_catalog()))
                    config = f'''model = "{MODEL}"
model_provider = "sandbox-broker"
model_catalog_json = {json.dumps(str(catalog))}
web_search = "disabled"
check_for_update_on_startup = false
[analytics]
enabled = false
[features]
multi_agent = false
multi_agent_v2 = false
goals = false
plugins = false
apps = false
hooks = false
view_image = false
shell_snapshot = false
[model_providers.sandbox-broker]
name = "Synthetic sandbox broker"
base_url = "http://127.0.0.1:{local.server_port}/v1"
wire_api = "responses"
env_key = "SANDBOX_GATEWAY_TOKEN"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
[model_providers.sandbox-broker.capabilities]
remote_compaction = "unsupported"
external_web_access = false
'''
                    (codex_home / "config.toml").write_text(config)
                    env = {"HOME": str(home), "CODEX_HOME": str(codex_home),
                           "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
                           "PATH": "/usr/bin:/bin", "TMPDIR": str(root), "RUST_LOG": "off",
                           "SANDBOX_GATEWAY_TOKEN": "dummy-local-capability", "MPLBACKEND": "Agg"}
                    for method, path, body in (("POST", "/v1/responses?url=https://example.invalid", b"{}"),
                                               ("POST", "/v1/responses/compact", b"{}"),
                                               ("POST", "https://example.invalid/v1/responses", b"{}"),
                                               ("CONNECT", "example.invalid:443", None),
                                               ("PUT", "/v1/responses", b"{}")):
                        connection = http.client.HTTPConnection("127.0.0.1", local.server_port, timeout=2)
                        connection.request(method, path, body)
                        refused = connection.getresponse()
                        self.assertGreaterEqual(refused.status, 400)
                        refused.read()
                        connection.close()
                    self.assertEqual(provider.calls, [])
                    paths.clear()
                    binary = installed_codex()
                    version = subprocess.run([binary, "--version"], cwd=work, env=env,
                                             text=True, capture_output=True, timeout=5)
                    self.assertEqual(version.returncode, 0, version.stderr)
                    args = [binary, "--no-daemon", "-a", "never", "exec", "--strict-config",
                            "--ephemeral", "--ignore-rules", "--skip-git-repo-check", "-s",
                            "danger-full-access", "-C", str(work), "--json",
                            "Run only the supplied synthetic printf tool, then return synthetic final."]
                    if sys.platform == "darwin":
                        profile = ('(version 1)(allow default)(deny network*)'
                                   '(allow network-outbound (remote tcp "localhost:'
                                   + str(local.server_port) + '"))')
                        args = ["/usr/bin/sandbox-exec", "-p", profile] + args
                    completed = subprocess.run(args, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                               text=True, capture_output=True, timeout=30)
                    self.assertEqual(completed.returncode, 0,
                                     completed.stdout[-2000:] + completed.stderr[-2000:] + str(failures))
                    self.assertFalse(failures, failures)
                    records = [json.loads(line) for line in completed.stdout.splitlines() if line]
                    self.assertTrue(any(record.get("type") == "turn.completed" for record in records))
                    self.assertTrue(any(record.get("item", {}).get("text") == "synthetic final"
                                        for record in records))
                    commands = [record["item"] for record in records
                                if record.get("item", {}).get("type") == "command_execution"]
                    self.assertTrue(commands, completed.stdout)
                    self.assertTrue(all("printf" in item.get("command", "") for item in commands))
                    self.assertFalse(any(record.get("type") in ("turn.failed", "error") for record in records))
            finally:
                local.shutdown()
                local.server_close()
                thread.join(2)
            self.assertEqual(paths, ["/v1/responses", "/v1/responses"])
            self.assertEqual(len(captures), 2)
            names = {entry["type"] for entry in captures[0]["tools"]}
            self.assertEqual(names, {"function", "custom"})
            replay = captures[1]["input"]
            self.assertIn(reasoning, replay)
            self.assertTrue(any(item.get("type") == "function_call" and
                                item.get("call_id") == "synthetic_call" for item in replay))
            outputs = [item for item in replay if item.get("type") == "function_call_output"]
            self.assertEqual(len(outputs), 1)
            self.assertIn("synthetic-tool-result", outputs[0]["output"])
            self.assertTrue(all(doc["model"] == MODEL and doc["store"] is False for doc in captures))


if __name__ == "__main__":
    unittest.main()
