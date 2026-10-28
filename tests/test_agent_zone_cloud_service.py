"""Synthetic Unix-socket HTTP tests against the integrated provider adapters."""
import concurrent.futures
import http.client
import http.server
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
from ai_zones import cloud_service as service

MODEL = "fixture-model"


def configuration(provider="codex", origin="http://127.0.0.1:1"):
    return {"zone": "internal", "provider": provider, "model": MODEL,
            "origin": origin, "service_group": os.getegid() + 10000, "socket_group": os.getegid()}


def event(name, **fields):
    return ("event: " + name + "\ndata: " + json.dumps(dict(type=name, **fields)) + "\n\n").encode()


def codex_stream():
    return (event("response.created", response={"id": "r1", "status": "in_progress", "output": []}) +
            event("response.completed", response={"id": "r1", "status": "completed", "output": []}))


def anthropic_stream():
    return (event("message_start", message={"id": "m1", "type": "message", "role": "assistant",
                  "model": MODEL, "content": [], "usage": {"input_tokens": 1, "output_tokens": 0}}) +
            event("message_delta", delta={"stop_reason": "end_turn", "stop_sequence": None},
                  usage={"output_tokens": 0}) + event("message_stop"))


class Upstream:
    def __init__(self, body=None, callback=None, *, delayed_headers=False, content_length=False, status=200, headers=None):
        self.calls, self.callback, self.body = [], callback, body
        self.delayed_headers, self.content_length = delayed_headers, content_length
        self.status, self.headers = status, headers or {}
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                owner.calls.append((self.path, dict(self.headers), json.loads(raw)))
                if not owner.delayed_headers:
                    self.send_response(owner.status)
                    self.send_header("Content-Type", "text/event-stream")
                    for key, value in owner.headers.items():
                        self.send_header(key, value)
                    if owner.content_length:
                        self.send_header("Content-Length", str(len(owner.body)))
                    self.end_headers()
                try:
                    if owner.callback:
                        owner.callback(self)
                    else:
                        self.wfile.write(owner.body)
                        self.wfile.flush()
                except (OSError, ValueError):
                    pass
        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.http.serve_forever(poll_interval=.01), daemon=True)
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


class Running:
    def __init__(self, config, token="synthetic-secret"):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "service.sock")
        cfg = service.validate_config(config)
        self.server = service.CloudService(cfg, service._adapter(cfg, token), self.path,
                                           preflight=self.fixture_preflight)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
    @staticmethod
    def fixture_preflight(path, group):
        if os.path.lexists(path):
            raise service.ServiceError()
    def __enter__(self):
        self.thread.start()
        return self
    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.directory.cleanup()
    def connect(self):
        peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        peer.settimeout(3)
        peer.connect(self.path)
        return peer
    def raw(self, data, *, allow_early_close=False):
        with self.connect() as peer:
            try:
                peer.sendall(data)
            except (BrokenPipeError, ConnectionResetError):
                if allow_early_close:
                    return b""
                raise
            out = b""
            while True:
                try:
                    chunk = peer.recv(65536)
                except ConnectionResetError:
                    if allow_early_close and not out:
                        return b""
                    raise
                if not chunk:
                    return out
                out += chunk
    def request(self, path="/v1/responses", document=None, *, allow_early_close=False):
        data = json.dumps(document if document is not None else {"input": "fixture"}).encode()
        return self.raw(b"POST " + path.encode() + b" HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/json\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data, allow_early_close=allow_early_close)
    def http_request(self, provider, *, include_headers=False):
        path = "/v1/responses" if provider == "codex" else "/v1/messages?beta=true"
        doc = {"input": "x"} if provider == "codex" else {
            "model": MODEL, "stream": True, "max_tokens": 10,
            "messages": [{"role": "user", "content": "x"}]}
        body = json.dumps(doc).encode()
        with self.connect() as peer:
            peer.sendall(b"POST " + path.encode() + b" HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            response = http.client.HTTPResponse(peer)
            response.begin()
            body = response.read()
            if include_headers:
                return response.status, dict(response.getheaders()), body
            return response.status, body

    def models(self, *, allow_early_close=False):
        return self.raw(b"GET /v1/models HTTP/1.1\r\nHost: fixture\r\n\r\n", allow_early_close=allow_early_close)


class ConfigurationTests(unittest.TestCase):
    def test_exact_internal_only_schema(self):
        self.assertEqual(service.validate_config(configuration()).provider, "codex")
        for changes in ({"zone": "confidential"}, {"token": "x"}, {"socket": "/tmp/x"},
                        {"command": "sh"}, {"provider": "generic"}, {"service_group": True},
                        {"origin": "https://x.invalid/redirect"}, {"account_id": "x\r\nx"},
                        {"oauth_beta": "x"}, {"model": "x\n"}):
            with self.subTest(changes=changes), self.assertRaises(Exception):
                service.validate_config(dict(configuration(), **changes))
        with self.assertRaises(service.ServiceError):
            service.strict_json(b'{"provider":"codex","provider":"anthropic"}')

    def test_root_launch_and_cli_bypass_refused_before_io(self):
        with mock.patch.object(service.os, "geteuid", return_value=0), mock.patch.object(service, "load_configuration") as load:
            self.assertEqual(service.main([]), 1)
            load.assert_not_called()
        with mock.patch.object(service, "load_configuration") as load:
            self.assertEqual(service.main(["--unprotected"]), 1)
            load.assert_not_called()

    def test_protected_paths_and_credentials_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "secret.json"
            target.write_text('{"token":"synthetic"}')
            target.chmod(0o640)
            with self.assertRaises(Exception):
                service.protected_json(str(target), os.getegid())
            target.unlink()
            target.symlink_to("/etc/hosts")
            with self.assertRaises(Exception):
                service.protected_json(str(target), os.getegid())
        with mock.patch.object(service, "protected_json", side_effect=[configuration(), {"token": "x", "origin": "y"}]):
            with self.assertRaises(service.ServiceError):
                service.load_configuration("codex")

    def test_loader_reads_only_fixed_files_with_private_primary_group(self):
        config = configuration()
        with mock.patch.object(service.os, "getegid", return_value=config["service_group"]), mock.patch.object(service, "protected_json", side_effect=[config, {"token": "synthetic"}]) as read:
            result, adapter = service.load_configuration("codex")
            self.assertEqual(result.socket_group, config["socket_group"])
            self.assertIsInstance(adapter, service.CodexGateway)
            self.assertEqual(read.call_args_list, [mock.call(service.provider_paths("codex")[0], config["service_group"]),
                                                 mock.call(service.provider_paths("codex")[1], config["service_group"])])
        for token in ("", "x\nsecret", 1):
            with mock.patch.object(service.os, "getegid", return_value=config["service_group"]), mock.patch.object(service, "protected_json", side_effect=[config, {"token": token}]):
                with self.assertRaises(service.ServiceError):
                    service.load_configuration("codex")

    def test_provider_launch_role_has_fixed_distinct_authority_paths(self):
        for provider in ("codex", "anthropic"):
            paths = service.provider_paths(provider)
            self.assertEqual(paths, (service.PREFIX + "/config/internal/" + provider + ".json",
                                    service.PREFIX + "/config/internal/" + provider + "-credentials.json",
                                    service.PREFIX + "/run/internal/" + provider + "/cloud.sock"))
        self.assertTrue(set(service.provider_paths("codex")).isdisjoint(service.provider_paths("anthropic")))
        for provider in ("codex", "anthropic"):
            config = service.validate_config(configuration(provider))
            adapter = object()
            with mock.patch.object(service, "load_configuration", return_value=(config, adapter)) as load, mock.patch.object(service, "CloudService") as server:
                self.assertEqual(service.main(["--provider", provider]), 0)
                load.assert_called_once_with(provider)
                server.assert_called_once_with(config, adapter)
        for args in ([], ["--provider", "generic"], ["--provider", "codex", "--config", "/tmp/x"],
                     ["--provider", "confidential"], ["--provider", "../anthropic"]):
            with mock.patch.object(service, "load_configuration") as load:
                self.assertEqual(service.main(args), 1)
                load.assert_not_called()
        config = configuration()
        with mock.patch.object(service.os, "getegid", return_value=config["service_group"]), mock.patch.object(service, "protected_json", return_value=config) as read:
            with self.assertRaises(service.ServiceError):
                service.load_configuration("anthropic")
            self.assertEqual(read.call_count, 1)

    def test_response_metadata_allowlist_and_caps(self):
        raw = {"Retry-After": "19", "retry-after": "20", "Set-Cookie": "secret",
               "x-should-retry": "true", "x-ratelimit-remaining-requests": "3",
               "x-ratelimit-secret": "secret", "anthropic-ratelimit-unified-status": "allowed",
               "x-ratelimit-reset-tokens": "injection\r\nAuthorization: secret",
               "x-ratelimit-limit-tokens": "x" * 513, "x-ratelimit-limit-requests": "\u2603",
               "anthropic-ratelimit-unified-" + "a" * 128: "x"}
        self.assertEqual(service._safe_headers(raw), {
            "x-should-retry": "true", "x-ratelimit-remaining-requests": "3",
            "anthropic-ratelimit-unified-status": "allowed"})
        many = {"anthropic-ratelimit-unified-fixture-" + str(index): "x" * 512 for index in range(100)}
        bounded = service._safe_headers(many)
        self.assertLessEqual(len(bounded), 32)
        self.assertLessEqual(sum(len(k) + len(v) + 4 for k, v in bounded.items()), 8192)
        self.assertEqual(service._safe_headers({str(i): "x" for i in range(129)}), {})

    def test_socket_group_parent_mode_and_existing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "run"
            parent.mkdir(mode=0o710)
            path = str(parent / "service.sock")
            with mock.patch.object(service.policy, "assert_protected"), mock.patch.object(service, "_no_acl"):
                service.socket_preflight(path, os.getegid())
                parent.chmod(0o770)
                with self.assertRaises(service.ServiceError):
                    service.socket_preflight(path, os.getegid())
                parent.chmod(0o710)
                Path(path).write_text("attacker")
                with self.assertRaises(service.ServiceError):
                    service.socket_preflight(path, os.getegid())
                self.assertEqual(Path(path).read_text(), "attacker")
                with self.assertRaises(service.ServiceError):
                    service.socket_preflight(path, 2147483647)


class BehavioralTests(unittest.TestCase):
    def test_codex_and_models_actual_uds(self):
        with Upstream(codex_stream()) as upstream, Running(configuration(origin=upstream.origin)) as running:
            info = os.lstat(running.path)
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o660)
            self.assertEqual(info.st_gid, os.getegid())
            reply = running.request()
            self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
            self.assertIn(b"response.completed", reply)
            self.assertTrue(reply.endswith(b"0\r\n\r\n"))
            self.assertIn(MODEL.encode(), running.models())
            self.assertEqual(len(upstream.calls), 1)
            path, headers, doc = upstream.calls[0]
            self.assertEqual(path, "/v1/responses")
            self.assertEqual(headers["Authorization"], "Bearer synthetic-secret")
            self.assertEqual(doc["model"], MODEL)

    def test_anthropic_actual_uds(self):
        with Upstream(anthropic_stream()) as upstream, Running(configuration("anthropic", upstream.origin)) as running:
            doc = {"model": MODEL, "max_tokens": 10, "stream": True,
                   "messages": [{"role": "user", "content": "fixture"}]}
            reply = running.request("/v1/messages?beta=true", doc)
            self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
            self.assertIn(b"message_stop", reply)
            self.assertEqual(upstream.calls[0][0], "/v1/messages?beta=true")
            self.assertNotIn("anthropic-beta", upstream.calls[0][1])

    def test_both_provider_services_concurrently_use_distinct_uds_and_credentials(self):
        with Upstream(codex_stream()) as codex_upstream, Upstream(anthropic_stream()) as anthropic_upstream, Running(configuration("codex", codex_upstream.origin), "codex-fixture") as codex, Running(configuration("anthropic", anthropic_upstream.origin), "anthropic-fixture") as anthropic:
            self.assertNotEqual(codex.path, anthropic.path)
            barrier = threading.Barrier(2)
            def consume(running, provider):
                barrier.wait(timeout=2)
                return running.http_request(provider)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(consume, codex, "codex")
                second = pool.submit(consume, anthropic, "anthropic")
                for future, marker in ((first, b"response.completed"), (second, b"message_stop")):
                    status, body = future.result(timeout=3)
                    self.assertEqual(status, 200)
                    self.assertIn(marker, body)
            self.assertEqual(codex_upstream.calls[0][1]["Authorization"], "Bearer codex-fixture")
            self.assertEqual(anthropic_upstream.calls[0][1]["Authorization"], "Bearer anthropic-fixture")
            self.assertTrue(codex.request("/v1/messages?beta=true", {}).startswith(b"HTTP/1.1 400"))
            self.assertTrue(anthropic.request("/v1/responses", {}).startswith(b"HTTP/1.1 400"))
            self.assertEqual(len(codex_upstream.calls), 1)
            self.assertEqual(len(anthropic_upstream.calls), 1)

    def test_both_provider_429_consumers_preserve_retry_metadata_without_secrets(self):
        headers = {"Retry-After": "19", "X-Should-Retry": "true",
                   "Anthropic-RateLimit-Unified-Status": "allowed",
                   "X-RateLimit-Remaining-Requests": "3", "X-RateLimit-Secret": "synthetic-secret",
                   "Set-Cookie": "synthetic-cookie", "Authorization": "Bearer synthetic-secret",
                   "X-Private-Upstream": "synthetic-secret", "Location": "https://example.invalid/secret"}
        with Upstream(b"synthetic-secret-provider-error-body", status=429, headers=headers) as codex_upstream, Upstream(b"synthetic-secret-provider-error-body", status=429, headers=headers) as anthropic_upstream, Running(configuration("codex", codex_upstream.origin)) as codex, Running(configuration("anthropic", anthropic_upstream.origin)) as anthropic:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                calls = [("codex", pool.submit(codex.http_request, "codex", include_headers=True)),
                         ("anthropic", pool.submit(anthropic.http_request, "anthropic", include_headers=True))]
                for provider, future in calls:
                    status, received, body = future.result(timeout=3)
                    self.assertEqual(status, 429)
                    self.assertEqual(received["retry-after"], "19")
                    if provider == "codex":
                        self.assertEqual(received["x-ratelimit-remaining-requests"], "3")
                    else:
                        self.assertEqual(received["x-should-retry"], "true")
                        self.assertEqual(received["anthropic-ratelimit-unified-status"], "allowed")
                    self.assertEqual(json.loads(body), {"error": {"type": "gateway_error", "code": "request_failed"}})
                    for secret in (b"synthetic-secret", b"synthetic-cookie", b"example.invalid"):
                        self.assertNotIn(secret, repr(received).encode() + body)
                    for forbidden in ("set-cookie", "authorization", "x-private-upstream", "location", "x-ratelimit-secret"):
                        self.assertNotIn(forbidden, received)

    def test_first_invalid_event_preserves_only_safe_retry_metadata(self):
        for provider in ("codex", "anthropic"):
            headers = {"Retry-After": "19", "Set-Cookie": "synthetic-secret"}
            with self.subTest(provider=provider), Upstream(b"synthetic-secret-invalid-stream", headers=headers) as upstream, Running(configuration(provider, upstream.origin)) as running:
                status, received, body = running.http_request(provider, include_headers=True)
                self.assertEqual(status, 502)
                self.assertEqual(received["retry-after"], "19")
                self.assertNotIn("set-cookie", received)
                self.assertNotIn(b"synthetic-secret", repr(received).encode() + body)

    def test_success_metadata_reaches_full_http_consumers(self):
        for provider, payload in (("codex", codex_stream()), ("anthropic", anthropic_stream())):
            headers = {"Retry-After": "19", "X-Should-Retry": "false",
                       "Anthropic-RateLimit-Unified-Status": "allowed", "X-RateLimit-Remaining-Tokens": "99",
                       "Set-Cookie": "synthetic-secret"}
            with self.subTest(provider=provider), Upstream(payload, content_length=True, headers=headers) as upstream, Running(configuration(provider, upstream.origin)) as running:
                status, received, body = running.http_request(provider, include_headers=True)
                self.assertEqual(status, 200)
                self.assertEqual(body, payload)
                self.assertEqual(received["retry-after"], "19")
                self.assertNotIn("set-cookie", received)
                if provider == "codex":
                    self.assertEqual(received["x-ratelimit-remaining-tokens"], "99")
                else:
                    self.assertEqual(received["x-should-retry"], "false")
                    self.assertEqual(received["anthropic-ratelimit-unified-status"], "allowed")

    def test_content_length_upstream_completes_full_http_consumer(self):
        for provider, payload in (("codex", codex_stream()), ("anthropic", anthropic_stream())):
            with self.subTest(provider=provider), Upstream(payload, content_length=True) as upstream, Running(configuration(provider, upstream.origin)) as running:
                status, body = running.http_request(provider)
                self.assertEqual(status, 200)
                self.assertEqual(body, payload)

    def test_hosted_tools_and_guest_overrides_never_reach_provider(self):
        with Upstream(codex_stream()) as upstream, Running(configuration(origin=upstream.origin)) as running:
            for doc in ({"input": "x", "tools": [{"type": "web_search"}]},
                        {"input": "x", "model": "override"}, {"input": "x", "headers": {}},
                        {"input": "x", "origin": upstream.origin}, {"input": "x", "command": "sh"}):
                self.assertTrue(running.request(document=doc).startswith(b"HTTP/1.1 400"))
            self.assertEqual(upstream.calls, [])

    def test_malformed_http_never_reaches_provider(self):
        base = b"POST /v1/responses HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/json\r\n"
        cases = [base + x for x in (
            b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
            b"Content-Length: +2\r\n\r\n{}", b"Content-Length: 02\r\n\r\n{}",
            b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
            b"Content-Length: 2\r\nAuthorization: Bearer guest\r\n\r\n{}",
            b"Content-Length: 2\r\nX-Forwarded-Host: evil\r\n\r\n{}",
            b"Content-Length: 2\r\n\r\n{}GET /v1/models HTTP/1.1\r\n\r\n")]
        cases += [b"CONNECT example.invalid:443 HTTP/1.1\r\nHost: fixture\r\n\r\n",
                  b"GET http://example.invalid/ HTTP/1.1\r\nHost: fixture\r\n\r\n",
                  b"GET /v1/models HTTP/1.1\nHost: fixture\n\n",
                  b"GET /v1/models HTTP/1.1\r\nHost: fixture\r\n bad: fold\r\n\r\n"]
        with Upstream(codex_stream()) as upstream, Running(configuration(origin=upstream.origin)) as running:
            for wire in cases:
                with self.subTest(wire=wire):
                    self.assertTrue(running.raw(wire).startswith(b"HTTP/1.1 400"))
            self.assertEqual(upstream.calls, [])

    def test_late_upstream_error_does_not_terminate_chunked_success(self):
        payload = codex_stream().split(b"event: response.completed")[0] + b"event: bad\ndata: {}\n\n"
        with Upstream(payload) as upstream, Running(configuration(origin=upstream.origin)) as running:
            reply = running.request()
            self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
            self.assertIn(b"response.created", reply)
            self.assertFalse(reply.endswith(b"0\r\n\r\n"))

    def test_disconnect_closes_upstream_and_releases_slot(self):
        cancelled = threading.Event()
        def callback(handler):
            handler.wfile.write(codex_stream().split(b"event: response.completed")[0])
            handler.wfile.flush()
            handler.connection.settimeout(2)
            try:
                if handler.connection.recv(1) == b"":
                    cancelled.set()
            except OSError:
                pass
        with mock.patch.object(service, "MAX_ACTIVE", 1), Upstream(callback=callback) as upstream, Running(configuration(origin=upstream.origin)) as running:
            peer = running.connect()
            body = b'{"input":"x"}'
            peer.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/json\r\nContent-Length: 13\r\n\r\n" + body)
            self.assertIn(b"200", peer.recv(4096))
            peer.close()
            self.assertTrue(cancelled.wait(2), "upstream did not observe closure")
            for _ in range(50):
                reply = running.models(allow_early_close=True)
                if reply.startswith(b"HTTP/1.1 200"):
                    break
                time.sleep(.02)
            self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)

    def test_header_deadline_and_connection_cap_release(self):
        with mock.patch.object(service, "MAX_ACTIVE", 1), mock.patch.object(service, "HEADER_SECONDS", .15), Running(configuration()) as running:
            idle = running.connect()
            idle.sendall(b"GET ")
            reply = running.models(allow_early_close=True)
            self.assertTrue(not reply or reply.startswith(b"HTTP/1.1 503"), reply)
            self.assertTrue(idle.recv(4096).startswith(b"HTTP/1.1 408"))
            idle.close()
            time.sleep(.03)
            self.assertTrue(running.models().startswith(b"HTTP/1.1 200"))

    def test_body_deadline_releases_capacity(self):
        with mock.patch.object(service, "MAX_ACTIVE", 1), mock.patch.object(service, "BODY_SECONDS", .08), Running(configuration()) as running:
            peer = running.connect()
            peer.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/json\r\nContent-Length: 10\r\n\r\n{")
            self.assertTrue(peer.recv(4096).startswith(b"HTTP/1.1 408"))
            peer.close()
            time.sleep(.03)
            self.assertTrue(running.models().startswith(b"HTTP/1.1 200"))

    def test_total_output_deadline_closes_waiting_provider(self):
        cancelled = threading.Event()
        def callback(handler):
            handler.wfile.write(codex_stream().split(b"event: response.completed")[0])
            handler.wfile.flush()
            handler.connection.settimeout(2)
            if handler.connection.recv(1) == b"":
                cancelled.set()
        with mock.patch.object(service, "TOTAL_SECONDS", .12), Upstream(callback=callback) as upstream, Running(configuration(origin=upstream.origin)) as running:
            reply = running.request()
            self.assertTrue(reply.startswith(b"HTTP/1.1 200"))
            self.assertFalse(reply.endswith(b"0\r\n\r\n"))
            self.assertTrue(cancelled.wait(1))

    def test_slow_reader_closes_upstream_and_releases_capacity(self):
        finished = threading.Event()
        def callback(handler):
            try:
                handler.wfile.write(codex_stream().split(b"event: response.completed")[0])
                # Repeated valid progress events exceed the downstream socket buffer.
                payload = event("response.in_progress", response={"id": "r1", "status": "in_progress", "output": []})
                for _ in range(40000):
                    handler.wfile.write(payload)
                handler.wfile.flush()
            finally:
                finished.set()
        with mock.patch.object(service, "MAX_ACTIVE", 1), mock.patch.object(service, "WRITE_SECONDS", .06), Upstream(callback=callback) as upstream, Running(configuration(origin=upstream.origin)) as running:
            peer = running.connect()
            peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            peer.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/json\r\nContent-Length: 13\r\n\r\n{\"input\":\"x\"}")
            time.sleep(.3)
            for _ in range(40):
                reply = running.models(allow_early_close=True)
                if reply.startswith(b"HTTP/1.1 200"):
                    break
                time.sleep(.03)
            peer.close()
            self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
            self.assertTrue(finished.wait(2))

    def _delayed_headers(self, provider, disconnect):
        entered, cancelled = threading.Event(), threading.Event()
        payload = codex_stream() if provider == "codex" else anthropic_stream()
        call_count = [0]
        def callback(handler):
            call_count[0] += 1
            if call_count[0] == 1:
                entered.set()
                handler.connection.settimeout(2)
                if handler.connection.recv(1) == b"":
                    cancelled.set()
                return
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(payload)
            handler.wfile.flush()
        path = "/v1/responses" if provider == "codex" else "/v1/messages?beta=true"
        doc = {"input": "x"} if provider == "codex" else {
            "model": MODEL, "stream": True, "max_tokens": 10,
            "messages": [{"role": "user", "content": "x"}]}
        with mock.patch.object(service, "MAX_ACTIVE", 1), mock.patch.object(service, "TOTAL_SECONDS", .2), Upstream(callback=callback, delayed_headers=True) as upstream, Running(configuration(provider, upstream.origin)) as running:
            start = time.monotonic()
            if disconnect:
                peer = running.connect()
                body = json.dumps(doc).encode()
                peer.sendall(b"POST " + path.encode() + b" HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                self.assertTrue(entered.wait(1))
                peer.close()
            else:
                reply = running.request(path, doc)
                self.assertTrue(reply.startswith(b"HTTP/1.1 504"), reply)
            self.assertTrue(cancelled.wait(1), "provider socket remained open before headers")
            self.assertLess(time.monotonic() - start, 1.2)
            # The next real request proves the sole worker slot was released.
            for _ in range(40):
                reply = running.request(path, doc, allow_early_close=True)
                if reply.startswith(b"HTTP/1.1 200"):
                    break
                self.assertTrue(not reply or reply.startswith(b"HTTP/1.1 503"), reply)
                time.sleep(.02)
            self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
            self.assertEqual(len(upstream.calls), 2)

    def test_preheader_total_deadline_closes_both_providers_and_releases_slot(self):
        for provider in ("codex", "anthropic"):
            with self.subTest(provider=provider):
                self._delayed_headers(provider, False)

    def test_preheader_disconnect_closes_both_providers_and_releases_slot(self):
        for provider in ("codex", "anthropic"):
            with self.subTest(provider=provider):
                self._delayed_headers(provider, True)

    def test_cancel_before_registration_invokes_callback_immediately(self):
        cancellation = service._Cancellation()
        called = []
        cancellation.cancel()
        cancellation.register(lambda: called.append(True))
        self.assertEqual(called, [True])
        cancellation.cancel()
        self.assertEqual(called, [True])

    def test_cleanup_preserves_replacement_path(self):
        with Running(configuration()) as running:
            os.unlink(running.path)
            Path(running.path).write_text("replacement")
            running.server.server_close()
            self.assertEqual(Path(running.path).read_text(), "replacement")


if __name__ == "__main__":
    unittest.main()
