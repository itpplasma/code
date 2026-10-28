#!/usr/bin/env python3
"""Executable negative tests for the restricted two-zone host broker.

Everything here runs against synthetic fixtures in a temporary directory and a
loopback mock upstream.  No host configuration, no privileged action, no real
model and no owner service is touched.

Run: python3 -m unittest discover -s tests -p test_agent_zone_broker.py
"""

from __future__ import annotations

import base64
import json
import http.client
import os
import shutil
import signal
import socket
import socketserver
import types
import stat
import subprocess
import sys
import tempfile
import threading
import time
from unittest import mock
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from ai_zones import broker, cloud_service, policy as shared_policy  # noqa: E402


# --------------------------------------------------------------------------
# Client: raw HTTP over the Unix socket, so malformed requests can be sent
# --------------------------------------------------------------------------


def request_bytes(
    method: str = "POST",
    path: str = "/v1/files/read",
    body: bytes = b"",
    extra: dict[str, str] | None = None,
    omit: tuple[str, ...] = (),
    version: str = "HTTP/1.1",
) -> bytes:
    extra = {key.lower(): value for key, value in (extra or {}).items()}
    omit = set(omit) | set(extra)
    lines = [f"{method} {path} {version}", "Host: broker.invalid"]
    if "content-type" not in omit:
        lines.append("Content-Type: application/json")
    if "content-length" not in omit:
        lines.append(f"Content-Length: {len(body)}")
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    return "\r\n".join(lines).encode() + b"\r\n\r\n" + body


def exchange(socket_path: str, payload: bytes, timeout: float = 20.0) -> tuple[int, dict[str, str], bytes]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    connection.connect(socket_path)
    try:
        connection.sendall(payload)
    except (BrokenPipeError, ConnectionResetError):
        pass  # the broker refused early and answered already
    try:
        connection.shutdown(socket.SHUT_WR)
    except OSError:
        pass
    chunks: list[bytes] = []
    while True:
        try:
            chunk = connection.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        chunks.append(chunk)
    connection.close()
    data = b"".join(chunks)
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1])
    headers = {}
    for line in lines[1:]:
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    if headers.get("transfer-encoding") == "chunked":
        chunks = []
        while True:
            line, separator, body = body.partition(b"\r\n")
            if not separator:
                raise ValueError("incomplete chunked response")
            size = int(line, 16)
            if size == 0:
                if body != b"\r\n":
                    raise ValueError("invalid chunked terminator")
                break
            if len(body) < size + 2 or body[size:size + 2] != b"\r\n":
                raise ValueError("incomplete chunk")
            chunks.append(body[:size])
            body = body[size + 2:]
        body = b"".join(chunks)
    return status, headers, body


# --------------------------------------------------------------------------
# Loopback mock upstream
# --------------------------------------------------------------------------


class _UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # keep the test output clean
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self.server.record({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        behaviour = self.server.behaviour
        if behaviour == "slow":
            threading.Event().wait(1.0)
        if behaviour in ("drip_headers", "drip_body"):
            raw = b'{"id":"drip","object":"chat.completion","model":"fixture-model","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
            head = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(raw)).encode() + b"\r\n\r\n"
            try:
                if behaviour == "drip_headers":
                    for byte in head:
                        self.connection.sendall(bytes([byte]))
                        time.sleep(0.03)
                else:
                    self.connection.sendall(head)
                    for byte in raw:
                        self.connection.sendall(bytes([byte]))
                        time.sleep(0.03)
            except OSError:
                pass
            return
        if behaviour in ("pi_tools", "pi_truncated", "pi_partial_truncated", "pi_slow", "pi_blocked"):
            def event(delta, finish=None):
                return {"id": "pi-turn", "object": "chat.completion.chunk", "model": "fixture-model",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            turn = len(self.server.requests)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if behaviour == "pi_blocked":
                events = [event({"role": "assistant", "content": "x" * 65500}) for _ in range(50)] + [event({}, "stop")]
            elif behaviour == "pi_slow":
                events = [event({"role": "assistant", "content": "partial"})]
            elif behaviour in ("pi_truncated", "pi_partial_truncated"):
                events = [event({"role": "assistant", "content": "partial"})] if behaviour == "pi_partial_truncated" else []
                events.append(event({}, "stop"))
            elif turn == 1:
                events = [event({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_echo_1", "type": "function",
                    "function": {"name": "echo", "arguments": '{"tex'}}]}),
                    event({"tool_calls": [{"index": 0, "function": {"arguments": 't":"ping"}'}}]}),
                    event({}, "tool_calls")]
            else:
                events = [event({"role": "assistant", "content": "Done: "}), event({"content": "ping"}, "stop")]
            if behaviour != "pi_slow":
                events.append({"id": "pi-turn", "object": "chat.completion.chunk", "model": "fixture-model", "choices": [],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}})
            wire = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
            if behaviour in ("pi_tools", "pi_blocked"):
                wire += b"data: [DONE]\n\n"
            try:
                fragment = 65536 if behaviour == "pi_blocked" else 13
                for i in range(0, len(wire), fragment):
                    part = wire[i:i + fragment]
                    self.wfile.write(b"%x\r\n" % len(part) + part + b"\r\n")
                    self.wfile.flush()
                if behaviour == "pi_slow":
                    time.sleep(1)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass
            return
        if behaviour == "large_headers":
            self.send_response(200)
            for i in range(6):
                self.send_header("X-Large-" + str(i), "x" * 7000)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if behaviour == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/v1/chat/completions")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if behaviour == "error":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if behaviour == "text":
            raw = b"not json at all"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if behaviour == "duplicate":
            raw = b'{"choices":[{"index":0,"message":{"role":"assistant","content":"x"},"finish_reason":"stop"}],"choices":[]}'
        elif behaviour == "huge":
            raw = json.dumps(
                {
                    "model": "fixture",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "y" * (4 << 20)},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ).encode()
        else:
            raw = json.dumps(
                {
                    "id": "fixture-completion",
                    "object": "chat.completion",
                    "model": "fixture-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
                }
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class MockUpstream:
    def __init__(self, name: str = "fixture-model") -> None:
        self.name = name
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        self.server.daemon_threads = True
        self.server.requests = requests = []
        self.server.behaviour = "ok"
        self.server.record = requests.append
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def requests(self) -> list[dict]:
        return self.server.requests

    @property
    def behaviour(self) -> str:
        return self.server.behaviour

    @behaviour.setter
    def behaviour(self, value: str) -> None:
        self.server.behaviour = value

    def hit(self, path: str = "/v1/chat/completions") -> int:
        return sum(1 for item in self.requests if item["path"] == path)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def make_policy(
    *,
    internal_root: str | None = None,
    internal_writable: bool = True,
    internal_model_url: str | None = None,
    confidential_root: str | None = None,
    confidential_model_url: str | None = None,
    token_file: str | None = None,
    model_name: str = "fixture-model",
) -> shared_policy.Policy:
    def zone(root, url, writable):
        return {
            "cpus": 2,
            "memory_mib": 512,
            "disk_gib": 4,
            "files": None if root is None else {"root": root, "writable": writable},
            "model": None if url is None else {"url": url, "name": model_name, "token_file": token_file},
        }

    return shared_policy.validate_policy({
        "version": 1,
        "zones": {
            "internal": zone(internal_root, internal_model_url, internal_writable),
            "confidential": zone(confidential_root, confidential_model_url, True),
        },
    })


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode("ascii")


PI_API = Path("/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js")
PI_SETUP = r"""
const {stream} = await import(process.argv[3]);
const {normalizeContext} = await import(new URL("../utils/transcript.js", process.argv[3]));
const model = {id:"fixture-model",name:"Synthetic",input:["text"],contextWindow:16384,maxTokens:2048,
 cost:{input:0,output:0,cacheRead:0,cacheWrite:0},api:"openai-completions",provider:"openai",
 baseUrl:process.argv[2]+"/v1",reasoning:false,
 compat:{supportsStore:true,supportsUsageInStreaming:true,maxTokensField:"max_tokens",supportsStrictMode:false}};
const tools=[{name:"echo",description:"Echo in guest",parameters:{type:"object",properties:{text:{type:"string"}},required:["text"],additionalProperties:false}}];
const user={role:"user",content:"Use echo",timestamp:Date.now()};
async function turn(messages){return await stream(model,normalizeContext({systemPrompt:"Synthetic only",messages,tools}),{apiKey:"guest-auth-must-not-forward",env:{},maxRetries:0,maxTokens:1024}).result();}
"""
PI_CYCLE = PI_SETUP + r"""
const first=await turn([user]);
if(first.stopReason!=="toolUse") throw Error(JSON.stringify(first));
const call=first.content.find(x=>x.type==="toolCall");
if(call.name!=="echo"||call.arguments.text!=="ping") throw Error("unexpected guest tool");
const second=await turn([user,{role:"assistant",content:first.content,api:model.api,provider:model.provider,model:model.id,
 stopReason:first.stopReason,usage:first.usage,timestamp:Date.now()},
 {role:"toolResult",toolCallId:call.id,toolName:call.name,isError:false,content:[{type:"text",text:"echoed:"+call.arguments.text}],timestamp:Date.now()}]);
console.log(JSON.stringify({firstStop:first.stopReason,call,final:second}));
"""


class BrokerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ai-zones-test-", dir=os.path.realpath("/tmp")))
        self.internal_root = self.tmp / "internal"
        self.confidential_root = self.tmp / "confidential"
        self.internal_root.mkdir()
        self.confidential_root.mkdir()
        self.upstream = MockUpstream()
        self.evil = MockUpstream(name="evil-model")
        self.brokers: list[broker.Broker] = []
        self.logs: list[str] = []
        self._modules = dict(sys.modules)

    def tearDown(self) -> None:
        for instance in self.brokers:
            instance.stop()
        self.upstream.stop()
        self.evil.stop()
        for name in list(sys.modules):
            if name not in self._modules:
                del sys.modules[name]
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers --------------------------------------------------------
    def token_file(self, secret: str = "protected-token") -> str:
        path = self.tmp / "model.token"
        path.write_text(secret + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        return str(path)

    def start(self, zone: str = "internal", policy: shared_policy.Policy | None = None, socket_name: str | None = None) -> broker.Broker:
        if policy is None:
            policy = self.default_policy()
        instance = broker.Broker(
            policy=policy,
            zone=zone,
            socket_path=str(self.tmp / (socket_name or f"{zone}.sock")),
            log=self.logs.append,
        )
        instance.serve_in_thread()
        self.brokers.append(instance)
        return instance

    def default_policy(self) -> shared_policy.Policy:
        return make_policy(
            internal_root=str(self.internal_root),
            internal_writable=True,
            internal_model_url=self.upstream.url,
            confidential_root=str(self.confidential_root),
            confidential_model_url=self.upstream.url,
        )

    def post(self, instance: broker.Broker, path: str, payload, **kwargs):
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode()
        elif isinstance(payload, str):
            body = payload.encode()
        else:
            body = payload or b""
        return exchange(instance.socket_path, request_bytes(path=path, body=body, **kwargs))

    def assert_denied(self, result, status: int | tuple[int, ...], code: str | None = None):
        got_status, _, body = result
        expected = (status,) if isinstance(status, int) else tuple(status)
        self.assertIn(got_status, expected, f"expected {expected}, got {got_status}: {body[:200]!r}")
        if code is not None:
            try:
                document = json.loads(body)
            except ValueError:
                self.fail(f"denial body is not JSON: {body[:200]!r}")
            self.assertEqual(document.get("error", {}).get("code"), code, f"body: {body[:300]!r}")

    # -- happy path (proves the refusals below are meaningful) ---------
    def test_read_write_list_round_trip(self) -> None:
        instance = self.start()
        (self.internal_root / "notes.txt").write_bytes(b"alpha\n")
        (self.internal_root / "sub").mkdir()
        status, _, body = self.post(instance, "/v1/files/read", {"path": "notes.txt"})
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(json.loads(body)["content_base64"]), b"alpha\n")

        status, _, body = self.post(
            instance, "/v1/files/write", {"path": "sub/out.txt", "content_base64": b64("beta")}
        )
        self.assertEqual(status, 200, body)
        self.assertEqual((self.internal_root / "sub" / "out.txt").read_bytes(), b"beta")
        self.assertEqual(list(self.internal_root.glob("sub/.out.txt.*.partial")), [])

        # overwrite in place, then confirm nothing outside the root was created
        status, _, body = self.post(
            instance, "/v1/files/write", {"path": "sub/out.txt", "content_base64": b64("gamma")}
        )
        self.assertEqual(status, 200, body)
        self.assertEqual((self.internal_root / "sub" / "out.txt").read_bytes(), b"gamma")

        self.assert_denied(
            self.post(instance, "/v1/files/write", {"path": "missing/out.txt", "content_base64": b64("beta")}),
            404,
            "not_found",
        )

        status, _, body = self.post(instance, "/v1/files/list", {"path": ""})
        document = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual({entry["name"] for entry in document["entries"]}, {"notes.txt", "sub"})

    def test_chat_uses_fixed_upstream_and_validates(self) -> None:
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(broker, "read_token", return_value="protected-token").start()
        token = self.token_file()
        policy = make_policy(
            internal_root=str(self.internal_root),
            internal_model_url=self.upstream.url,
            token_file=token,
        )
        instance = self.start("internal", policy)
        status, _, body = self.post(
            instance,
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hello"}], "model": "fixture-model", "temperature": 0.2},
        )
        self.assertEqual(status, 200, body)
        document = json.loads(body)
        self.assertEqual(document["choices"][0]["message"]["content"], "ok")
        self.assertNotIn("x_debug", document)
        self.assertEqual(self.upstream.hit(), 1)
        self.assertEqual(self.evil.hit(), 0)
        sent = self.upstream.requests[-1]
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(json.loads(sent["body"])["model"], "fixture-model")
        self.assertEqual(sent["headers"].get("authorization"), "Bearer protected-token")

    # -- traversal, symlinks, containment ------------------------------
    def test_traversal_and_absolute_paths_denied(self) -> None:
        instance = self.start()
        for bad in [
            "../secret",
            "notes/../../etc/passwd",
            "..",
            "/etc/passwd",
            "./notes.txt",
            "a/./b",
            "a//b",
            "~/.ssh/id_rsa",
            "sub\\..\\secret",
        ]:
            with self.subTest(path=bad):
                self.assert_denied(self.post(instance, "/v1/files/read", {"path": bad}), 400)

    def test_symlink_denied_everywhere(self) -> None:
        secret = self.tmp / "outside-secret.txt"
        secret.write_text("do-not-read\n", encoding="utf-8")
        outside_dir = self.tmp / "outside-dir"
        outside_dir.mkdir()
        (outside_dir / "inside.txt").write_text("no\n", encoding="utf-8")
        (self.internal_root / "link.txt").symlink_to(secret)
        (self.internal_root / "dirlink").symlink_to(outside_dir, target_is_directory=True)
        instance = self.start()

        self.assert_denied(self.post(instance, "/v1/files/read", {"path": "link.txt"}), 403, "symlink_denied")
        self.assert_denied(self.post(instance, "/v1/files/read", {"path": "dirlink/inside.txt"}), 403)
        self.assert_denied(
            self.post(instance, "/v1/files/write", {"path": "link.txt", "content_base64": b64("x")}),
            403,
            "symlink_denied",
        )
        self.assert_denied(
            self.post(instance, "/v1/files/write", {"path": "dirlink/new.txt", "content_base64": b64("x")}),
            403,
        )
        status, _, body = self.post(instance, "/v1/files/list", {"path": ""})
        names = {entry["name"] for entry in json.loads(body)["entries"]}
        self.assertNotIn("link.txt", names)
        self.assertNotIn("dirlink", names)
        self.assertEqual(secret.read_text(), "do-not-read\n")

    def test_cross_zone_paths_are_unreachable(self) -> None:
        (self.confidential_root / "payroll.txt").write_text("top\n", encoding="utf-8")
        internal = self.start("internal")
        self.assert_denied(self.post(internal, "/v1/files/read", {"path": "payroll.txt"}), 404, "not_found")
        self.assert_denied(
            self.post(internal, "/v1/files/read", {"path": f"../confidential/payroll.txt"}), 400
        )
        self.assert_denied(
            self.post(internal, "/v1/files/read", {"path": str(self.confidential_root / "payroll.txt")}), 400
        )
        self.assert_denied(
            self.post(internal, "/v1/files/read", {"path": "notes.txt", "zone": "confidential"}),
            400,
            "unknown_field",
        )
        confidential = self.start("confidential", socket_name="confidential.sock")
        status, _, body = self.post(confidential, "/v1/files/read", {"path": "payroll.txt"})
        self.assertEqual(status, 200, body)
        self.assertEqual(confidential.config.zone, "confidential")
        self.assertEqual(internal.config.zone, "internal")

    def test_read_only_zone_refuses_writes(self) -> None:
        policy = make_policy(internal_root=str(self.internal_root), internal_writable=False)
        instance = self.start("internal", policy)
        self.assert_denied(
            self.post(instance, "/v1/files/write", {"path": "x.txt", "content_base64": b64("x")}),
            403,
            "write_denied",
        )
        self.assertFalse((self.internal_root / "x.txt").exists())

    def test_missing_configuration_denies(self) -> None:
        policy = make_policy(internal_writable=True)  # no root, no model for internal
        instance = self.start("internal", policy)
        self.assert_denied(self.post(instance, "/v1/files/read", {"path": "notes.txt"}), 403, "files_disabled")
        self.assert_denied(self.post(instance, "/v1/files/list", {"path": ""}), 403, "files_disabled")
        self.assert_denied(
            self.post(instance, "/v1/files/write", {"path": "a", "content_base64": b64("x")}), 403, "files_disabled"
        )
        self.assert_denied(
            self.post(instance, "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]}),
            403,
            "chat_disabled",
        )
        self.assertEqual(self.upstream.hit(), 0)

    # -- request framing ------------------------------------------------
    def test_methods_and_unknown_endpoints_denied(self) -> None:
        instance = self.start()
        self.assert_denied(exchange(instance.socket_path, request_bytes(method="GET")), 405, "method_not_allowed")
        self.assert_denied(exchange(instance.socket_path, request_bytes(method="PUT")), 405)
        self.assert_denied(exchange(instance.socket_path, request_bytes(method="DELETE")), 405)
        self.assert_denied(exchange(instance.socket_path, request_bytes(method="OPTIONS")), 405)
        self.assert_denied(
            exchange(instance.socket_path, request_bytes(method="CONNECT", path="example.com:443")), 405
        )
        self.assert_denied(exchange(instance.socket_path, request_bytes(path="/v1/config")), 404, "unknown_endpoint")
        self.assert_denied(exchange(instance.socket_path, request_bytes(path="/mcp")), 404)
        self.assert_denied(
            exchange(instance.socket_path, request_bytes(path="/v1/files/read?path=/etc/passwd")), 400, "query_denied"
        )
        self.assert_denied(
            exchange(
                instance.socket_path,
                request_bytes(path=f"http://127.0.0.1:{self.evil.port}/v1/chat/completions"),
            ),
            400,
            "absolute_uri",
        )
        self.assertEqual(self.evil.hit(), 0)

    def test_chunked_and_unframed_bodies_denied(self) -> None:
        instance = self.start()
        self.assert_denied(
            exchange(
                instance.socket_path,
                request_bytes(body=b'{"path":"notes.txt"}', extra={"Transfer-Encoding": "chunked"}, omit=("content-length",)),
            ),
            411,
            "chunked_denied",
        )
        self.assert_denied(
            exchange(instance.socket_path, request_bytes(body=b"{}", omit=("content-length",))),
            411,
            "length_required",
        )
        self.assert_denied(
            exchange(instance.socket_path, request_bytes(body=b"{}", extra={"Content-Encoding": "gzip"})),
            415,
            "unsupported_encoding",
        )
        self.assert_denied(
            exchange(instance.socket_path, request_bytes(body=b"path=notes.txt", extra={"Content-Type": "application/x-www-form-urlencoded"})),
            415,
            "unsupported_media_type",
        )

    def test_strict_json_rejects_duplicate_and_unknown_fields(self) -> None:
        instance = self.start()
        (self.internal_root / "notes.txt").write_text("a\n", encoding="utf-8")
        self.assert_denied(
            self.post(instance, "/v1/files/read", '{"path":"notes.txt","path":"notes.txt"}'),
            400,
            "duplicate_key",
        )
        self.assert_denied(self.post(instance, "/v1/files/read", '{"path":"a","env":{"A":1}}'), 400, "unknown_field")
        self.assert_denied(self.post(instance, "/v1/files/read", '{"path":"a","command":["ls"]}'), 400)
        self.assert_denied(self.post(instance, "/v1/files/read", '{"path":{"nested":"x"}}'), 400)
        self.assert_denied(self.post(instance, "/v1/files/read", "[]"), 400, "invalid_json")
        self.assert_denied(self.post(instance, "/v1/files/read", "{}"), 400, "missing_field")
        self.assert_denied(
            self.post(instance, "/v1/files/write", {"content_base64": b64("x")}), 400, "missing_field"
        )
        self.assert_denied(self.post(instance, "/v1/files/list", {"prefix": ""}), 400, "unknown_field")
        self.assert_denied(self.post(instance, "/v1/files/read", '{"path":"a","n":NaN}'), 400, "invalid_number")

    def test_oversize_requests_and_files_denied(self) -> None:
        instance = self.start()
        self.assert_denied(
            exchange(instance.socket_path, request_bytes(body=b"", extra={"Content-Length": str(2 << 20)})),
            413,
            "body_too_large",
        )
        big = self.internal_root / "big.bin"
        big.write_bytes(b"x" * ((1 << 20) + 1))
        self.assert_denied(self.post(instance, "/v1/files/read", {"path": "big.bin"}), 413, "file_too_large")
        self.assert_denied(
            self.post(instance, "/v1/files/write", {"path": "big.bin", "content_base64": "QQ==" * ((1 << 20) + 1)}),
            413,
        )
        self.assertEqual(big.read_bytes(), b"x" * ((1 << 20) + 1))

    # -- model surface --------------------------------------------------
    def test_model_and_option_overrides_denied(self) -> None:
        instance = self.start()
        messages = [{"role": "user", "content": "hi"}]
        self.assert_denied(
            self.post(instance, "/v1/chat/completions", {"messages": messages, "model": "gpt-evil"}),
            400,
            "model_override",
        )
        for key in ("url", "upstream", "base_url", "api_base", "endpoint", "host", "socket", "zone"):
            with self.subTest(key=key):
                self.assert_denied(
                    self.post(instance, "/v1/chat/completions", {"messages": messages, key: self.evil.url}),
                    400,
                    "unknown_field",
                )
        for key in ("functions", "tool_choice", "function_call", "response_format", "user", "n"):
            with self.subTest(key=key):
                self.assert_denied(
                    self.post(instance, "/v1/chat/completions", {"messages": messages, key: True}), 400, "unknown_field"
                )
        self.assert_denied(
            self.post(instance, "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi", "tool_calls": [{"id": "1"}]}]}),
            400,
            "unknown_field",
        )
        self.assert_denied(
            self.post(instance, "/v1/chat/completions", {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": self.evil.url}}]}]}),
            400,
            "unknown_field",
        )
        self.assert_denied(
            self.post(instance, "/v1/chat/completions", {"messages": [{"role": "tool", "tool_call_id": "orphan", "content": "x"}]}), 400, "orphan_tool_result"
        )
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages, "temperature": 99}), 400)
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages, "max_tokens": 10**6}), 400)
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages, "stop": ["a"] * 17}), 400)
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages, "tools": True}), 400, "invalid_tools")
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages, "stream": "yes"}), 400, "invalid_field")
        for tool_type in ("web_search", "image_generation", "computer", "mcp", "connector"):
            self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages,
                "tools": [{"type": tool_type}]}), 400, "unsupported_tool_type")
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages, "tools": [{"type": "function",
            "function": {"name": "echo", "parameters": {"$ref": "https://example.invalid/schema"}}}]}), 400, "external_schema_ref")
        self.assertEqual(self.upstream.hit(), 0)
        self.assertEqual(self.evil.hit(), 0)

    def test_caller_credentials_are_never_forwarded(self) -> None:
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(broker, "read_token", return_value="zone-token").start()
        token = self.token_file("zone-token")
        policy = make_policy(
            internal_root=str(self.internal_root), internal_model_url=self.upstream.url, token_file=token
        )
        instance = self.start("internal", policy)
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        status, _, raw = exchange(
            instance.socket_path,
            request_bytes(
                path="/v1/chat/completions",
                body=body,
                extra={
                    "Authorization": "Bearer caller-secret",
                    "Cookie": "session=caller",
                    "X-Forwarded-Host": self.evil.url,
                    "X-Policy-Override": "true",
                },
            ),
        )
        self.assertEqual(status, 200, raw)
        sent = self.upstream.requests[-1]
        self.assertEqual(sent["headers"].get("authorization"), "Bearer zone-token")
        for key in ("cookie", "x-forwarded-host", "x-policy-override"):
            self.assertNotIn(key, {k.lower() for k in sent["headers"]})
        self.assertNotIn("caller-secret", json.dumps(sent["headers"]))

    def test_no_redirects_and_no_fallback_upstream(self) -> None:
        policy = make_policy(internal_model_url=self.upstream.url)
        instance = self.start("internal", policy)
        messages = [{"role": "user", "content": "hi"}]
        self.upstream.behaviour = "redirect"
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages}), 502, "upstream_status")
        self.upstream.behaviour = "error"
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages}), 502, "upstream_status")
        self.upstream.behaviour = "text"
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages}), 502, "content_type")
        self.upstream.behaviour = "duplicate"
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages}), 502, "invalid_response")
        self.upstream.behaviour = "large_headers"
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages}), 502)
        self.upstream.behaviour = "huge"
        self.assert_denied(self.post(instance, "/v1/chat/completions", {"messages": messages}), 502, "response_too_large")
        self.assertEqual(self.evil.hit(), 0)

    def test_closed_upstream_is_refused_without_fallback(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        policy = make_policy(internal_model_url=f"http://127.0.0.1:{port}")
        instance = self.start("internal", policy)
        self.assert_denied(
            self.post(instance, "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]}),
            502,
            "upstream_unavailable",
        )
        self.assertEqual(self.evil.hit(), 0)

    def test_upstream_timeout_is_bounded(self) -> None:
        policy = make_policy(internal_model_url=self.upstream.url)
        instance = broker.Broker(
            policy=policy,
            zone="internal",
            socket_path=str(self.tmp / "timeout.sock"),
            upstream_timeout=0.3,
            log=self.logs.append,
        )
        instance.serve_in_thread()
        self.brokers.append(instance)
        self.upstream.behaviour = "slow"
        self.assert_denied(
            self.post(instance, "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]}),
            504,
            "deadline_exceeded",
        )

    def test_confidential_zone_is_loopback_only(self) -> None:
        for url in ("http://model.internal:8080", "https://api.example.com", "http://localhost:9/v1"):
            with self.subTest(url=url):
                with self.assertRaises(broker.PolicyError):
                    policy = make_policy(confidential_root=str(self.confidential_root), confidential_model_url=url)
                    broker.Broker(policy=policy, zone="confidential", socket_path=str(self.tmp / "c.sock"))
        policy = make_policy(confidential_root=str(self.confidential_root), confidential_model_url=self.upstream.url)
        instance = self.start("confidential", policy, socket_name="c.sock")
        status, _, body = self.post(instance, "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 200, body)
        self.assertIn("127.0.0.1", instance.config.model.origin)

    # -- socket and lifecycle --------------------------------------------
    def test_socket_is_owner_only_and_existing_paths_survive(self) -> None:
        instance = self.start()
        mode = stat.S_IMODE(os.lstat(instance.socket_path).st_mode)
        self.assertEqual(mode, 0o600)
        instance.stop()
        self.assertFalse(os.path.lexists(instance.socket_path))

        stale = self.tmp / "stale.sock"
        keeper = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        keeper.bind(str(stale))
        keeper.listen(1)
        try:
            with self.assertRaises(broker.PolicyError):
                broker.Broker(policy=self.default_policy(), zone="internal", socket_path=str(stale)).start()
            self.assertTrue(os.path.lexists(stale))
        finally:
            keeper.close()

        plain = self.tmp / "someone-else.txt"
        plain.write_text("keep\n", encoding="utf-8")
        with self.assertRaises(broker.PolicyError):
            broker.Broker(policy=self.default_policy(), zone="internal", socket_path=str(plain)).start()
        self.assertEqual(plain.read_text(), "keep\n")

    def test_socket_parent_must_be_private(self) -> None:
        loose = self.tmp / "loose"
        loose.mkdir()
        os.chmod(loose, 0o777)
        with self.assertRaises(broker.PolicyError):
            broker.Broker(policy=self.default_policy(), zone="internal", socket_path=str(loose / "s.sock")).start()

    def test_access_log_keeps_guest_paths_out(self) -> None:
        (self.internal_root / "private-notes.txt").write_text("a\n", encoding="utf-8")
        instance = self.start()
        self.post(instance, "/v1/files/read", {"path": "private-notes.txt"})
        self.post(instance, "/v1/files/read", {"path": "../escape"})
        self.assertTrue(self.logs, "the broker logs nothing")
        joined = "\n".join(self.logs)
        self.assertNotIn("private-notes.txt", joined)
        self.assertNotIn("escape", joined)
        self.assertNotIn(str(self.internal_root), joined)

    def test_list_is_bounded(self) -> None:
        for index in range(broker.MAX_LIST_ENTRIES + 5):
            (self.internal_root / f"f{index}").write_text("x", encoding="utf-8")
        instance = self.start()
        status, _, body = self.post(instance, "/v1/files/list", {"path": ""})
        document = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(document["truncated"])
        self.assertEqual(len(document["entries"]), broker.MAX_LIST_ENTRIES)

    # -- shared policy and protected launch ------------------------------
    def test_actual_policy_model_and_loader(self):
        path = self.tmp / "policy.json"
        raw = {"version": 1, "zones": {name: {"cpus": 2, "memory_mib": 512,
            "disk_gib": 4, "files": None, "model": None} for name in broker.ZONES}}
        path.write_text(json.dumps(raw))
        loaded = shared_policy.load_policy(str(path), protected=False)
        self.assertIsInstance(loaded, shared_policy.Policy)
        self.assertIsNone(broker.project_zone(loaded, "internal").files_root)
        with self.assertRaises(TypeError):
            loaded.zones["internal"] = None
        with self.assertRaises(broker.PolicyError):
            broker.project_zone(raw, "internal")
        with self.assertRaises(broker.PolicyError):
            broker.load_policy_file(str(path))

    def test_policy_loader_always_protected_and_main_bounded(self):
        actual = self.default_policy()
        factory = mock.Mock()
        factory.return_value.run.return_value = 0
        with mock.patch.object(broker, "load_services", return_value=broker.Services()), mock.patch.object(broker, "load_policy", return_value=actual) as loader:
            result = broker.main(["--policy", "/synthetic/policy.json", "--zone", "internal",
                "--socket", str(self.tmp / "cli.sock")], broker_factory=factory)
        self.assertEqual(result, 0)
        loader.assert_called_once_with("/synthetic/policy.json", protected=True)
        self.assertIs(factory.call_args.kwargs["policy"], actual)
        factory.return_value.run.assert_called_once_with()

    def test_cli_rejects_relaxation_and_root_identity(self):
        parser = broker.build_parser()
        for extra in (["--insecure-mode"], ["--root", "/"]):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--policy", "/p", "--zone", "internal", "--socket", "/s"] + extra)
        with mock.patch.object(broker.os, "geteuid", return_value=0):
            self.assertEqual(broker.main(["--policy", "/p", "--zone", "internal", "--socket", "/s"]), 2)

    def test_service_owned_token_refused_without_unauthenticated_fallback(self):
        token = self.token_file()
        with self.assertRaises(broker.PolicyError):
            broker.read_token(token)
        instance = self.start(policy=make_policy(internal_model_url=self.upstream.url, token_file=token))
        self.assert_denied(self.post(instance, "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "x"}]}), 503)
        self.assertEqual(self.upstream.hit(), 0)
        self.assertIsNone(broker.read_token(None))

    def test_raw_invalid_lengths_transfer_upgrade_and_aggregate_headers(self):
        instance = self.start()
        for length in (b"-1", b"+2", b"0", b"01", b"2,2", b"\xb2", b"9" * 7000):
            raw = b"POST /v1/files/read HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: " + length + b"\r\n\r\n{}"
            self.assert_denied(exchange(instance.socket_path, raw), 413 if len(length) > 10 else 400,
                "body_too_large" if len(length) > 10 else "invalid_header" if length == b"\xb2" else "invalid_length")
        for headers in (b"Content-Length: 2\r\nContent-Length: 2", b"Content-Length: 2\r\nTransfer-Encoding: chunked",
                        b"Content-Length: 2\r\nTE: trailers", b"Content-Length: 2\r\nUpgrade: websocket"):
            raw = b"POST /v1/files/read HTTP/1.1\r\nContent-Type: application/json\r\n" + headers + b"\r\n\r\n{}"
            self.assert_denied(exchange(instance.socket_path, raw), (400, 411))
        headers = b"X-Large: " + b"x" * 7000 + b"\r\n"
        raw = b"POST /v1/files/read HTTP/1.1\r\n" + headers * 5 + b"\r\n"
        self.assert_denied(exchange(instance.socket_path, raw), 431)

    def test_repeated_missing_paths_do_not_leak_fds_or_close_root(self):
        instance = self.start()
        (self.internal_root / "sub").mkdir()
        def count():
            with os.scandir("/dev/fd") as entries:
                return len(list(entries))
        before = count()
        for _ in range(60):
            with self.assertRaises(broker.BrokerError):
                instance.root.read(("sub", "missing", "file"))
        self.assertEqual(count(), before)
        (self.internal_root / "good").write_bytes(b"x")
        self.assertEqual(instance.root.read(("good",))["size_bytes"], 1)
        self.assertEqual(instance.root.read(("good",))["size_bytes"], 1)

    def test_root_symlink_ancestor_hardlink_and_fifo_refused(self):
        target = self.tmp / "real"
        target.mkdir()
        (target / "data").mkdir()
        alias = self.tmp / "alias"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(broker.PolicyError):
            broker.RootAccess(str(alias / "data"), writable=True)
        (self.tmp / "outside").write_bytes(b"synthetic")
        os.link(self.tmp / "outside", self.internal_root / "linked")
        os.mkfifo(self.internal_root / "fifo")
        instance = self.start()
        for name in ("linked", "fifo"):
            self.assert_denied(self.post(instance, "/v1/files/read", {"path": name}), 403)
            self.assert_denied(self.post(instance, "/v1/files/write", {"path": name, "content_base64": "eA=="}), 403)

    def test_growing_file_running_byte_limit(self):
        access = broker.RootAccess(str(self.internal_root), writable=True)
        self.addCleanup(access.close)
        (self.internal_root / "grow").write_bytes(b"x")
        real_read = os.read
        grown = [False]
        def grow_then_read(fd, size):
            if not grown[0]:
                grown[0] = True
                with open(self.internal_root / "grow", "ab") as handle:
                    handle.write(b"a" * (broker.MAX_FILE_BYTES + 1))
            return real_read(fd, size)
        with mock.patch.object(broker.os, "read", side_effect=grow_then_read):
            with self.assertRaises(broker.BrokerError) as caught:
                access.read(("grow",))
        self.assertEqual(caught.exception.code, "file_too_large")

    def test_write_short_zero_and_exception_cleanup(self):
        access = broker.RootAccess(str(self.internal_root), writable=True)
        self.addCleanup(access.close)
        real_write = os.write
        with mock.patch.object(broker.os, "write", side_effect=lambda fd, data: real_write(fd, data[:2])):
            access.write(("short",), b"abcdef")
        self.assertEqual((self.internal_root / "short").read_bytes(), b"abcdef")
        for failure in (0, OSError(28, "synthetic storage full")):
            patch = mock.patch.object(broker.os, "write", **({"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}))
            with patch, self.assertRaises(broker.BrokerError):
                access.write(("failed",), b"x")
            self.assertEqual(list(self.internal_root.glob("*.partial")), [])
        access.write(("empty",), b"")
        self.assertEqual((self.internal_root / "empty").read_bytes(), b"")

    def test_list_scan_budget_and_control_metadata(self):
        instance = self.start()
        (self.internal_root / ".git").mkdir()
        (self.internal_root / ".git" / "config").write_text("synthetic")
        with mock.patch.object(broker, "MAX_LIST_SCAN_ENTRIES", 2):
            for i in range(4):
                (self.internal_root / ("link" + str(i))).symlink_to(self.tmp / "outside")
            result = instance.root.list(())
        self.assertTrue(result["truncated"])
        self.assertNotIn(".git", [e["name"] for e in result["entries"]])
        for endpoint in ("read", "write", "list"):
            body = {"path": ".git/config"}
            if endpoint == "write":
                body["content_base64"] = "eA=="
            self.assert_denied(self.post(instance, "/v1/files/" + endpoint, body), 403)

    def test_socket_group_mode_and_identity_cleanup(self):
        os.chown(self.tmp, -1, os.getegid())
        os.chmod(self.tmp, 0o710)
        instance = broker.Broker(policy=self.default_policy(), zone="internal", socket_path=str(self.tmp / "group.sock"),
            socket_group=os.getegid(), log=self.logs.append)
        instance.serve_in_thread()
        self.brokers.append(instance)
        info = os.lstat(instance.socket_path)
        self.assertEqual((stat.S_IMODE(info.st_mode), info.st_gid), (0o660, os.getegid()))
        self.assert_denied(self.post(instance, "/v1/files/read", {"path": "absent"}), 404)
        os.unlink(instance.socket_path)
        replacement = Path(instance.socket_path)
        replacement.write_text("keep")
        instance.stop()
        self.assertEqual(replacement.read_text(), "keep")

    def test_absolute_header_body_deadlines_and_connection_cap(self):
        instance = broker.Broker(policy=self.default_policy(), zone="internal", socket_path=str(self.tmp / "limits.sock"),
            header_timeout=0.18, body_timeout=0.18, max_connections=1, log=self.logs.append)
        instance.serve_in_thread()
        self.brokers.append(instance)
        first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        first.connect(instance.socket_path)
        first.sendall(b"P")
        time.sleep(0.04)
        self.assert_denied(exchange(instance.socket_path, request_bytes(body=b"{}")), 503)
        started = time.monotonic()
        for _ in range(4):
            time.sleep(0.06)
            try:
                first.sendall(b"O")
            except OSError:
                break
        first.settimeout(1)
        self.assertIn(b"408", first.recv(4096))
        first.close()
        self.assertLess(time.monotonic() - started, 0.6)
        second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        second.connect(instance.socket_path)
        raw = request_bytes(body=b"{" * 20)
        head, _, body = raw.partition(b"\r\n\r\n")
        second.sendall(head + b"\r\n\r\n" + body[:1])
        for _ in range(4):
            time.sleep(0.06)
            try:
                second.sendall(b"{")
            except OSError:
                break
        second.settimeout(1)
        self.assertIn(b"408", second.recv(4096))
        second.close()
        self.assert_denied(self.post(instance, "/v1/files/read", {"path": "absent"}), 404)

    def test_absolute_upstream_deadline_includes_dripped_headers_and_body(self):
        instance = broker.Broker(policy=make_policy(internal_model_url=self.upstream.url), zone="internal",
            socket_path=str(self.tmp / "upstream-deadline.sock"), upstream_timeout=0.19, log=self.logs.append)
        instance.serve_in_thread()
        self.brokers.append(instance)
        for behaviour in ("drip_headers", "drip_body"):
            self.upstream.behaviour = behaviour
            started = time.monotonic()
            with mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden")):
                self.assert_denied(self.post(instance, "/v1/chat/completions",
                    {"messages": [{"role": "user", "content": "x"}]}), 504)
            self.assertLess(time.monotonic() - started, 0.7)

    def pi_bridge(self, instance):
        class Bridge(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.relay()
            def do_GET(self):
                self.relay()
            def relay(self):
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.settimeout(5)
                connection.connect(instance.socket_path)
                response = None
                try:
                    body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    connection.sendall(request_bytes(method=self.command, path=self.path, body=body,
                        extra={"Authorization": self.headers.get("Authorization", ""), "X-Api-Key": "guest-dummy"}))
                    response = http.client.HTTPResponse(connection)
                    response.begin()
                    self.send_response(response.status)
                    self.send_header("Content-Type", response.getheader("Content-Type"))
                    for name, value in broker._rate_headers({name.lower(): value for name, value in response.getheaders()
                            if name.lower() in broker.RATE_HEADERS or name.lower().startswith("anthropic-ratelimit-unified-")}).items():
                        self.send_header(name, value)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    while True:
                        part = response.read1(4096)
                        if not part:
                            break
                        self.wfile.write(b"%x\r\n" % len(part) + part + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (http.client.HTTPException, OSError):
                    pass
                finally:
                    self.close_connection = True
                    if response is not None:
                        response.close()
                    connection.close()
        bridge = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
        thread = threading.Thread(target=bridge.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        thread.start()
        self.addCleanup(bridge.server_close)
        self.addCleanup(bridge.shutdown)
        return "http://127.0.0.1:%d" % bridge.server_port

    def run_pi(self, instance, script):
        path = self.tmp / "pi-drive.mjs"
        path.write_text(script)
        proc = subprocess.run([shutil.which("node"), str(path), self.pi_bridge(instance), PI_API.resolve().as_uri()],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    @unittest.skipUnless(shutil.which("node") and PI_API.is_file(), "installed Pi SDK required")
    def test_actual_pi_tool_result_continuation_through_unix_broker(self):
        self.upstream.behaviour = "pi_tools"
        instance = self.start()
        result = self.run_pi(instance, PI_CYCLE)
        self.assertEqual(result["firstStop"], "toolUse")
        self.assertEqual(result["call"]["arguments"], {"text": "ping"})
        self.assertEqual(result["final"]["stopReason"], "stop")
        self.assertEqual("".join(c.get("text", "") for c in result["final"]["content"]), "Done: ping")
        self.assertEqual(result["final"]["usage"]["totalTokens"], 16)
        self.assertEqual(self.upstream.hit(), 2)
        first, second = [json.loads(r["body"]) for r in self.upstream.requests]
        self.assertEqual(first["model"], "fixture-model")
        self.assertTrue(first["stream"])
        self.assertEqual(first["tools"][0]["function"]["name"], "echo")
        roles = [m["role"] for m in second["messages"]]
        self.assertEqual(roles.count("tool"), 1)
        self.assertEqual(second["messages"][roles.index("tool")]["content"], "echoed:ping")
        for request in self.upstream.requests:
            self.assertNotIn("authorization", request["headers"])
            self.assertEqual(request["path"], "/v1/chat/completions")

    @unittest.skipUnless(shutil.which("node") and PI_API.is_file(), "installed Pi SDK required")
    def test_actual_pi_truncated_stream_cannot_finish_successfully(self):
        for behaviour in ("pi_truncated", "pi_partial_truncated"):
            self.upstream.behaviour = behaviour
            instance = self.start(socket_name=behaviour + ".sock")
            result = self.run_pi(instance, PI_SETUP + "console.log(JSON.stringify(await turn([user])));")
            self.assertEqual(result["stopReason"], "error", result)
            self.assertNotEqual(result["stopReason"], "stop")

    @unittest.skipUnless(shutil.which("node") and PI_API.is_file(), "installed Pi SDK required")
    def test_actual_pi_partial_stream_total_deadline_is_error(self):
        self.upstream.behaviour = "pi_slow"
        instance = broker.Broker(policy=self.default_policy(), zone="internal", socket_path=str(self.tmp / "stream-deadline.sock"),
            upstream_timeout=1, request_deadline=.25, log=self.logs.append)
        instance.serve_in_thread()
        self.brokers.append(instance)
        result = self.run_pi(instance, PI_SETUP + "console.log(JSON.stringify(await turn([user])));")
        self.assertEqual(result["stopReason"], "error", result)
        self.assertIn("partial", "".join(c.get("text", "") for c in result["content"]))
        self.assertTrue(any("deadline_exceeded" in entry for entry in self.logs), self.logs)

    def test_client_disconnect_closes_idle_upstream_and_releases_slot(self):
        self.upstream.behaviour = "pi_slow"
        instance = broker.Broker(policy=self.default_policy(), zone="internal", socket_path=str(self.tmp / "disconnect.sock"),
            upstream_timeout=2, max_connections=1, log=self.logs.append)
        instance.serve_in_thread()
        self.brokers.append(instance)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(1)
        connection.connect(instance.socket_path)
        body = json.dumps({"messages": [{"role": "user", "content": "x"}], "stream": True}).encode()
        connection.sendall(request_bytes(path="/v1/chat/completions", body=body))
        response = http.client.HTTPResponse(connection)
        response.begin()
        self.assertIn(b"partial", response.read1(4096))
        response.close()
        connection.close()
        time.sleep(.2)
        self.assert_denied(self.post(instance, "/v1/files/read", {"path": "absent"}), 404)

    def test_stalled_stream_reader_is_bounded_by_total_deadline(self):
        self.upstream.behaviour = "pi_blocked"
        instance = broker.Broker(policy=self.default_policy(), zone="internal", socket_path=str(self.tmp / "blocked.sock"),
            upstream_timeout=2, request_deadline=.25, max_connections=1, log=self.logs.append)
        instance.serve_in_thread()
        self.brokers.append(instance)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.connect(instance.socket_path)
        self.addCleanup(connection.close)
        body = json.dumps({"messages": [{"role": "user", "content": "x"}], "stream": True}).encode()
        connection.sendall(request_bytes(path="/v1/chat/completions", body=body))
        time.sleep(.6)
        self.assert_denied(self.post(instance, "/v1/files/read", {"path": "absent"}), 404)

    def test_nonstream_function_calls_are_preserved(self):
        tool = {"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}
        payload = {"id": "function-response", "object": "chat.completion", "model": "fixture-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}
        instance = self.start()
        real = json.dumps
        def encode(value, *args, **kwargs):
            if isinstance(value, dict) and value.get("id") == "fixture-completion":
                value = payload
            return real(value, *args, **kwargs)
        with mock.patch.object(json, "dumps", side_effect=encode):
            status, _, body = self.post(instance, "/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}], "tools": [tool]})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["tool_calls"], payload["choices"][0]["message"]["tool_calls"])

    def test_token_running_bound_and_open_identity(self):
        token = self.token_file()
        named = os.lstat(token)
        # Protection is independently exercised above; these simulate the opened-object race.
        with mock.patch.object(broker, "_assert_protected_stat", return_value=(token, named)):
            with mock.patch.object(broker.os, "lstat", return_value=named):
                Path(token).write_bytes(b"x" * (broker.MAX_TOKEN_FILE_BYTES + 1))
                with mock.patch.object(broker, "assert_no_mutating_acl", wraps=shared_policy.assert_no_mutating_acl):
                    with self.assertRaises(broker.BrokerError):
                        broker.read_token(token)
            other = self.tmp / "replacement"
            other.write_text("new")
            real_open = os.open
            def swap_then_open(*args, **kwargs):
                os.replace(other, token)
                return real_open(*args, **kwargs)
            with mock.patch.object(broker.os, "open", side_effect=swap_then_open):
                with self.assertRaises(broker.BrokerError):
                    broker.read_token(token)


def provider_stream(provider):
    def event(kind, **fields):
        return ("event: " + kind + "\ndata: " + json.dumps(dict(type=kind, **fields)) + "\n\n").encode()
    if provider == "codex":
        item = {"type": "message", "id": "msg_fixture", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "synthetic final", "annotations": []}]}
        def response(status, items):
            return {"id": "resp_fixture", "object": "response", "status": status, "model": "fixture-model",
                "store": False, "output": items, "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13,
                "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
        part = item["content"][0]
        return b"".join([event("response.created", response=response("in_progress", [])),
            event("response.output_item.added", output_index=0, item=dict(item, status="in_progress", content=[])),
            event("response.content_part.added", item_id=item["id"], output_index=0, content_index=0,
                part={"type": "output_text", "text": "", "annotations": []}),
            event("response.output_text.delta", item_id=item["id"], output_index=0, content_index=0, delta=part["text"]),
            event("response.output_text.done", item_id=item["id"], output_index=0, content_index=0, text=part["text"]),
            event("response.content_part.done", item_id=item["id"], output_index=0, content_index=0, part=part),
            event("response.output_item.done", output_index=0, item=item),
            event("response.completed", response=response("completed", [item]))])
    return b"".join([event("message_start", message={"id": "msg_fixture", "type": "message", "role": "assistant",
        "model": "claude-sonnet-4-6", "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 0}}),
        event("content_block_start", index=0, content_block={"type": "text", "text": ""}),
        event("content_block_delta", index=0, delta={"type": "text_delta", "text": "synthetic final"}),
        event("content_block_stop", index=0),
        event("message_delta", delta={"stop_reason": "end_turn", "stop_sequence": None}, usage={"output_tokens": 3}),
        event("message_stop")])


class CloudBrokerTest(unittest.TestCase):
    post = BrokerTest.post
    start = BrokerTest.start
    default_policy = BrokerTest.default_policy
    assert_denied = BrokerTest.assert_denied
    pi_bridge = BrokerTest.pi_bridge

    def setUp(self):
        BrokerTest.setUp(self)
        self.prefix = self.tmp / "prefix"
        self.prefix.mkdir()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(broker, "PREFIX", str(self.prefix)).start()
        # Only the root-managed ancestor gate is substituted for disposable data;
        # service inode modes and real kernel peer credentials remain checked.
        self.original_protection = broker.assert_protected
        mock.patch.object(broker, "assert_protected", side_effect=lambda path, directory=False:
            path if str(path).startswith(str(self.prefix)) else self.original_protection(path, directory=directory)).start()
        self.cloud_servers = []
        self.cloud_upstreams = []

    def tearDown(self):
        for server in self.cloud_servers:
            server.shutdown(); server.server_close()
        for server in self.cloud_upstreams:
            server.shutdown(); server.server_close()
        BrokerTest.tearDown(self)

    def cloud(self, provider, *, mode="normal", cancelled=None, response_status=200, response_headers=()):
        calls = []
        complete = provider_stream(provider)
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                calls.append((self.path, dict(self.headers), json.loads(raw)))
                self.send_response(response_status)
                self.send_header("Content-Type", "text/event-stream" if response_status == 200 else "application/json")
                for name, value in response_headers:
                    self.send_header(name, value)
                self.end_headers()
                try:
                    if response_status != 200:
                        self.wfile.write(b'{"error":"synthetic-provider-secret"}'); self.wfile.flush()
                    elif mode == "observe_cancel":
                        self.wfile.write(complete.split(b"\n\n", 1)[0] + b"\n\n"); self.wfile.flush()
                        self.connection.settimeout(1)
                        if self.connection.recv(1) == b"" and cancelled is not None: cancelled.set()
                    elif mode == "slow":
                        self.wfile.write(complete.split(b"\n\n", 1)[0] + b"\n\n"); self.wfile.flush()
                        time.sleep(1)
                    elif mode == "truncated":
                        self.wfile.write(complete.rsplit(b"event:", 1)[0]); self.wfile.flush()
                    else:
                        self.wfile.write(complete); self.wfile.flush()
                except OSError:
                    pass
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        upstream.daemon_threads = True
        threading.Thread(target=upstream.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.cloud_upstreams.append(upstream)
        parent = self.prefix / "run" / "internal" / provider
        parent.mkdir(parents=True, mode=0o710)
        os.chown(parent, -1, os.getegid()); parent.chmod(0o710)
        config = cloud_service.validate_config({"zone": "internal", "provider": provider, "model": "claude-sonnet-4-6" if provider == "anthropic" else "fixture-model",
            "origin": "http://127.0.0.1:%d" % upstream.server_port, "service_group": os.getegid() + 10000,
            "socket_group": os.getegid()})
        service = cloud_service.CloudService(config, cloud_service._adapter(config, provider + "-root-fixture"),
            str(parent / "cloud.sock"), preflight=lambda path, group: None)
        threading.Thread(target=service.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.cloud_servers.append(service)
        return calls

    def cloud_broker(self, *, zone="internal", identity=None, **options):
        identity = identity or broker.ServiceIdentity(os.geteuid(), os.getegid())
        instance = broker.Broker(policy=self.default_policy(), zone=zone, socket_path=str(self.tmp / options.pop("socket_name", zone + "-cloud.sock")),
            services=broker.Services(codex=identity, anthropic=identity), log=self.logs.append, **options)
        instance.serve_in_thread(); self.brokers.append(instance)
        return instance

    def test_both_typed_cloud_services_through_tcp_bridge_and_unix_broker(self):
        codex_calls, claude_calls = self.cloud("codex"), self.cloud("anthropic")
        instance = self.cloud_broker()
        base = self.pi_bridge(instance)
        host, port = base.removeprefix("http://").split(":")
        for path, document, marker in (("/codex/v1/responses", {"input": "synthetic"}, b"response.completed"),
                ("/claude/v1/messages?beta=true", {"model": "claude-sonnet-4-6", "stream": True, "max_tokens": 10,
                 "messages": [{"role": "user", "content": "synthetic"}]}, b"message_stop")):
            client = http.client.HTTPConnection(host, int(port), timeout=3)
            client.request("POST", path, json.dumps(document), {"Content-Type": "application/json", "Authorization": "guest-dummy"})
            reply = client.getresponse(); body = reply.read(); client.close()
            self.assertEqual(reply.status, 200, body); self.assertIn(marker, body)
        status, _, body = exchange(instance.socket_path, request_bytes(method="GET", path="/codex/v1/models", omit=("content-length",)))
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["data"][0]["id"], "fixture-model")
        for calls, path, token in ((codex_calls, "/v1/responses", "codex-root-fixture"),
                                   (claude_calls, "/v1/messages?beta=true", "anthropic-root-fixture")):
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], path)
            headers = {k.lower(): v for k, v in calls[0][1].items()}
            self.assertNotIn("guest-dummy", json.dumps(headers))
            self.assertIn(token, json.dumps(headers))
            self.assertNotIn("anthropic-root-fixture" if token.startswith("codex") else "codex-root-fixture", json.dumps(headers))
            self.assertEqual(calls[0][2]["model"], "claude-sonnet-4-6" if calls is claude_calls else "fixture-model")

    def test_cloud_missing_config_confidential_and_namespace_refusals(self):
        with mock.patch.object(broker, "_read_services", side_effect=AssertionError("confidential must not read service config")):
            self.assertEqual(broker.load_services("confidential"), broker.Services())
            self.assertEqual(broker.load_services("internal"), broker.Services())
        instance = self.start()
        self.assert_denied(self.post(instance, "/codex/v1/responses", {"input": "x"}), 403, "cloud_disabled")
        self.cloud("codex"); self.cloud("anthropic")
        confidential = self.cloud_broker(zone="confidential")
        self.assert_denied(self.post(confidential, "/codex/v1/responses", {"input": "x"}), 403)
        self.assert_denied(self.post(confidential, "/claude/v1/messages?beta=true", {}), 403)
        for path in ("/codex/v1/messages", "/claude/v1/responses", "/codex/v1/responses/compact", "/codex/v1/models?x=1",
                     "/claude/v1/messages", "/claude/v1/messages?beta=false", "/mail/mcp"):
            self.assert_denied(self.post(instance, path, {}), (400, 404))

    def check_cloud_metadata(self, response_status):
        cases = (("codex", "/codex/v1/responses", {"input": "synthetic"}, "x-ratelimit-remaining-requests"),
                 ("anthropic", "/claude/v1/messages?beta=true", {"model": "claude-sonnet-4-6", "stream": True,
                  "max_tokens": 10, "messages": [{"role": "user", "content": "synthetic"}]},
                  "anthropic-ratelimit-unified-remaining"))
        forbidden = (("Set-Cookie", "synthetic-cookie"), ("Authorization", "synthetic-secret"),
                     ("Location", "http://invalid.example/"), ("X-Ratelimit-Secret", "synthetic-secret"))
        for provider, _, _, rate in cases:
            self.cloud(provider, response_status=response_status, response_headers=(
                ("Retry-After", "19"), (rate, "7"), ("X-Should-Retry", "true")) + forbidden)
        instance = self.cloud_broker()
        host, port = self.pi_bridge(instance).removeprefix("http://").split(":")
        for _, path, document, rate in cases:
            with self.subTest(status=response_status, path=path):
                client = http.client.HTTPConnection(host, int(port), timeout=3)
                try:
                    client.request("POST", path, json.dumps(document), {"Content-Type": "application/json"})
                    reply = client.getresponse(); body = reply.read()
                    headers = {name.lower(): value for name, value in reply.getheaders()}
                    self.assertEqual(reply.status, response_status, body)
                    self.assertEqual(headers["retry-after"], "19")
                    self.assertEqual(headers[rate], "7")
                    if rate.startswith("anthropic-"):
                        self.assertEqual(headers["x-should-retry"], "true")
                    for name, value in forbidden:
                        self.assertNotIn(name.lower(), headers)
                        self.assertNotIn(value.encode(), body)
                    if response_status == 429:
                        self.assertNotIn(b"synthetic-provider-secret", body)
                        self.assertEqual(json.loads(body)["error"]["code"], "cloud_request_failed")
                    else:
                        self.assertIn(b"synthetic final", body)
                finally:
                    client.close()

    def test_actual_typed_cloud_429_retry_metadata_reaches_tcp_consumer(self):
        self.check_cloud_metadata(429)

    def test_actual_typed_cloud_success_rate_metadata_reaches_tcp_consumer(self):
        self.check_cloud_metadata(200)

    def test_cloud_child_metadata_is_strict_and_bounded(self):
        prefix = b"anthropic-ratelimit-unified-"
        cases = (
            b"Retry-After: 19\r\nretry-after: 20\r\n",
            b"Retry-After: 19\x00\r\n", b"Retry-After: \xff\r\n",
            b"Retry-After: " + b"a" * 513 + b"\r\n",
            b"Retry-After: " + b" " * 513 + b"\r\n",
            b"Retry-\xff: 19\r\n",
            prefix + b"a" * (129 - len(prefix)) + b": 1\r\n",
            b"X-Ratelimit-Secret: synthetic-secret\r\n", b"Authorization: synthetic-secret\r\n",
            b"Set-Cookie: synthetic-secret\r\n", b"Retry-After : 19\r\n",
            b"".join(prefix + str(i).encode() + b": 1\r\n" for i in range(33)),
            b"".join(prefix + str(i).encode() + b": " + b"a" * 512 + b"\r\n" for i in range(16)),
        )
        pending = []
        def respond(peer):
            peer.recv(65536)
            status, metadata = pending.pop(0)
            head = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n" if status == 200 else b"HTTP/1.1 429 Refused\r\nContent-Length: 2\r\n"
            body = b"2\r\n{}\r\n0\r\n\r\n" if status == 200 else b"{}"
            peer.sendall(head + b"Content-Type: application/json\r\nConnection: close\r\n" + metadata + b"\r\n" + body)
        self.raw_cloud(respond)
        instance = self.cloud_broker()
        for child_status in (200, 429):
            for index, headers in enumerate(cases):
                with self.subTest(status=child_status, case=index):
                    pending.append((child_status, headers))
                    status, guest_headers, body = self.post(instance, "/codex/v1/responses", {"input": "x"})
                    self.assertEqual(status, 502, body)
                    self.assertEqual(json.loads(body)["error"]["code"], "cloud_response_invalid")
                    self.assertNotIn(b"synthetic-secret", body)
                    self.assertEqual(set(guest_headers), {"content-type", "content-length", "connection"})

    def test_cloud_child_accepts_32_valid_metadata_fields(self):
        metadata = {"anthropic-ratelimit-unified-%d" % i: "7" for i in range(32)}
        def respond(peer):
            peer.recv(65536)
            headers = b"".join((name + ": " + value + "\r\n").encode() for name, value in metadata.items())
            peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n"
                         b"Connection: close\r\n" + headers + b"\r\n2\r\n{}\r\n0\r\n\r\n")
        self.raw_cloud(respond)
        status, headers, body = self.post(self.cloud_broker(), "/codex/v1/responses", {"input": "x"})
        self.assertEqual((status, body), (200, b"{}"))
        for name, value in metadata.items():
            self.assertEqual(headers[name], value)

    def test_services_config_is_fixed_protected_strict_and_role_bounded(self):
        document = {"codex": {"enabled": True, "uid": 610, "gid": 610},
                    "anthropic": {"enabled": False, "uid": 611, "gid": 611}}
        parsed = broker.validate_services(document)
        self.assertEqual(parsed.codex.uid, 610); self.assertIsNone(parsed.anthropic)
        for change in ({"socket": "/tmp/x"}, {"uid": 0, "gid": 0}, {"uid": 599, "gid": 599},
                       {"uid": 1000, "gid": 1000}, {"uid": 610, "gid": 611}, {"enabled": "yes"}):
            bad = json.loads(json.dumps(document)); bad["codex"].update(change)
            with self.assertRaises(broker.PolicyError): broker.validate_services(bad)
        bad = json.loads(json.dumps(document)); bad["anthropic"].update(uid=610, gid=610)
        with self.assertRaises(broker.PolicyError): broker.validate_services(bad)
        with self.assertRaises(broker.PolicyError): broker.validate_services(document, socket_group=610)
        config = self.prefix / "config" / "internal" / "services.json"
        config.parent.mkdir(parents=True); config.write_text(json.dumps(document)); config.chmod(0o640)
        with self.assertRaises(broker.PolicyError): broker.load_services("internal")
        with mock.patch.object(broker, "_read_services", return_value=document) as read:
            self.assertEqual(broker.load_services("internal"), parsed)
            read.assert_called_once_with(str(config))
        with self.assertRaises(broker.BrokerError): broker.strict_json_loads(b'{"codex":null,"codex":null}')
        self.assertNotIn("--services", broker.build_parser().format_help())

    def test_actual_kernel_peer_mismatch_sends_no_application_bytes(self):
        calls = self.cloud("codex")
        for index, identity in enumerate((broker.ServiceIdentity(os.geteuid() + 1, os.getegid()),
                broker.ServiceIdentity(os.geteuid(), os.getegid() + 1))):
            instance = self.cloud_broker(identity=identity, socket_name="peer-%d.sock" % index)
            with mock.patch.object(broker, "_check_service_socket"):
                self.assert_denied(self.post(instance, "/codex/v1/responses", {"input": "x"}), 502, "cloud_peer_denied")
            time.sleep(.05)
            self.assertEqual(calls, [])
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(self.prefix / "run" / "internal" / "codex" / "cloud.sock"))
            self.assertEqual(broker._peer_identity(connection), (os.geteuid(), os.getegid()))

    def test_cloud_truncation_and_total_deadline_never_terminate_success(self):
        for provider, path, doc in (("codex", "/codex/v1/responses", {"input": "x"}),
                ("anthropic", "/claude/v1/messages?beta=true", {"model": "claude-sonnet-4-6", "stream": True,
                 "max_tokens": 10, "messages": [{"role": "user", "content": "x"}]})):
            self.cloud(provider, mode="truncated")
            # Each typed child is separately reached through the fixed broker path.
            instance = broker.Broker(policy=self.default_policy(), zone="internal", socket_path=str(self.tmp / (provider + "-truncated.sock")),
                services=broker.Services(codex=broker.ServiceIdentity(os.geteuid(), os.getegid()),
                    anthropic=broker.ServiceIdentity(os.geteuid(), os.getegid())), log=self.logs.append)
            instance.serve_in_thread(); self.brokers.append(instance)
            try:
                status, _, body = self.post(instance, path, doc)
            except ValueError:
                pass  # Incomplete chunked HTTP is the required failure after any text.
            else:
                self.assertGreaterEqual(status, 400, body)


    def raw_cloud(self, callback):
        parent = self.prefix / "run" / "internal" / "codex"
        parent.mkdir(parents=True, mode=0o710)
        os.chown(parent, -1, os.getegid()); parent.chmod(0o710)
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(2)
                try:
                    callback(self.request)
                except OSError:
                    pass
        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True
        server = Server(str(parent / "cloud.sock"), Handler)
        os.chmod(parent / "cloud.sock", 0o660)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.cloud_servers.append(server)
        return server

    def test_cloud_overflowing_json_number_is_refused_before_child(self):
        calls = self.cloud("codex")
        instance = self.cloud_broker()
        self.assert_denied(self.post(instance, "/codex/v1/responses", b'{"input":"x","temperature":1e9999}'), 400, "invalid_number")
        self.assertEqual(calls, [])

    def test_cloud_slow_headers_have_absolute_deadline(self):
        def slow(peer):
            peer.recv(65536)
            for byte in b"HTTP/1.1 200 OK\r\n":
                peer.sendall(bytes([byte])); time.sleep(.03)
        self.raw_cloud(slow)
        instance = self.cloud_broker(header_timeout=.16)
        started = time.monotonic()
        self.assert_denied(self.post(instance, "/codex/v1/responses", {"input": "x"}), 504, "deadline_exceeded")
        self.assertLess(time.monotonic() - started, .6)

    def test_cloud_disconnect_before_headers_cancels_child(self):
        accepted, cancelled = threading.Event(), threading.Event()
        def waiting(peer):
            peer.recv(65536); accepted.set()
            if peer.recv(1) == b"": cancelled.set()
        self.raw_cloud(waiting)
        instance = self.cloud_broker(max_connections=1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(instance.socket_path)
            client.sendall(request_bytes(path="/codex/v1/responses", body=b'{"input":"x"}'))
            self.assertTrue(accepted.wait(1))
        self.assertTrue(cancelled.wait(.5))
        time.sleep(.1)
        self.assert_denied(self.post(instance, "/v1/files/read", {"path": "absent"}), 404)

    def test_cloud_stream_total_deadline_and_slow_reader_release_slot(self):
        def blocked(peer):
            peer.recv(65536)
            peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
            for _ in range(100):
                part = b"x" * 65536
                peer.sendall(b"10000\r\n" + part + b"\r\n")
        self.raw_cloud(blocked)
        instance = self.cloud_broker(max_connections=1, request_deadline=.2)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(instance.socket_path)
            client.sendall(request_bytes(path="/codex/v1/responses", body=b'{"input":"x"}'))
            time.sleep(.5)
            self.assert_denied(self.post(instance, "/v1/files/read", {"path": "absent"}), 404)

    def test_client_disconnect_reaches_both_typed_provider_adapters(self):
        for provider, path, document in (("codex", "/codex/v1/responses", {"input": "x"}),
                ("anthropic", "/claude/v1/messages?beta=true", {"model": "claude-sonnet-4-6", "stream": True,
                 "max_tokens": 10, "messages": [{"role": "user", "content": "x"}]})):
            cancelled = threading.Event()
            self.cloud(provider, mode="observe_cancel", cancelled=cancelled)
            instance = self.cloud_broker(socket_name=provider + "-cancel.sock")
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); client.settimeout(1); client.connect(instance.socket_path)
            client.sendall(request_bytes(path=path, body=json.dumps(document).encode()))
            reply = http.client.HTTPResponse(client); reply.begin()
            self.assertEqual(reply.status, 200)
            self.assertTrue(reply.read1(65536))
            reply.close(); client.close()
            self.assertTrue(cancelled.wait(.75), provider + " provider socket was not cancelled")

    def test_services_exact_opened_bytes_identity_and_running_limit(self):
        document = {"codex": {"enabled": True, "uid": 610, "gid": 610},
                    "anthropic": {"enabled": True, "uid": 611, "gid": 611}}
        path = self.tmp / "services-fixture.json"
        path.write_text(json.dumps(document)); path.chmod(0o640); os.chown(path, -1, os.getegid())
        real_fstat = os.fstat
        def synthetic_root(info):
            names = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            values = {key: getattr(info, key) for key in names}; values["st_uid"] = 0
            return types.SimpleNamespace(**values)
        def opened(fd): return synthetic_root(real_fstat(fd))
        # Root metadata is simulated only here; bytes, descriptors and inode swaps are real.
        with mock.patch.object(broker, "_assert_protected_stat", side_effect=lambda name: (name, synthetic_root(os.lstat(name)))), mock.patch.object(os, "fstat", side_effect=opened):
            self.assertEqual(broker._read_services(str(path)), document)
            path.write_bytes(b'{"codex":null,"codex":null}')
            with self.assertRaises(broker.PolicyError): broker._read_services(str(path))
            path.write_bytes(b"x")
            real_read = os.read
            grown = [False]
            def growing(fd, amount):
                if not grown[0]:
                    grown[0] = True
                    with path.open("ab") as handle: handle.write(b"x" * (broker.MAX_SERVICES_BYTES + 1))
                return real_read(fd, amount)
            with mock.patch.object(os, "read", side_effect=growing):
                with self.assertRaises(broker.PolicyError) as refusal: broker._read_services(str(path))
                self.assertIn("byte limit", str(refusal.exception))
            path.write_text(json.dumps(document))
            real_open = os.open
            def substitute(name, *args, **kwargs):
                replacement = self.tmp / "replacement.json"; replacement.write_text("{}")
                replacement.chmod(0o640); os.chown(replacement, -1, os.getegid()); os.replace(replacement, path)
                return real_open(name, *args, **kwargs)
            with mock.patch.object(os, "open", side_effect=substitute), mock.patch.object(os, "read", wraps=real_read) as read:
                with self.assertRaises(broker.PolicyError): broker._read_services(str(path))
                read.assert_not_called()

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("claude") and Path("/usr/bin/sandbox-exec").is_file(), "native Claude and Seatbelt required")
    def test_actual_claude_client_turn_through_broker_and_typed_child(self):
        calls = self.cloud("anthropic")
        instance = self.cloud_broker()
        frontend = self.pi_bridge(instance)
        cli = str(Path(shutil.which("claude")).resolve())
        home, project = self.tmp / "cli-home", self.tmp / "cli-project"
        home.mkdir(); project.mkdir()
        profile = ('(version 1)(deny default)(allow process*)(allow sysctl-read)(allow file-read-metadata)'
            '(allow file-read* (require-not (subpath "/Users")))(allow file-read* (literal %s))'
            '(allow file-write* (subpath %s) (literal "/dev/null"))(allow network-outbound (remote tcp "localhost:%s"))') % (
                json.dumps(cli), json.dumps(str(self.tmp)), frontend.rsplit(":", 1)[1])
        profile_path = self.tmp / "claude.sb"; profile_path.write_text(profile)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(self.tmp), "SHELL": "/bin/bash",
            "CLAUDE_CONFIG_DIR": str(home / "config"), "CLAUDE_CODE_TMPDIR": str(self.tmp / "cli-tmp"),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
            "ANTHROPIC_AUTH_TOKEN": "guest-dummy", "ANTHROPIC_BASE_URL": frontend + "/claude",
            "ANTHROPIC_SMALL_FAST_MODEL": "claude-sonnet-4-6", "CLAUDE_CODE_SUBAGENT_MODEL": "claude-sonnet-4-6", "MPLBACKEND": "Agg"}
        cmd = ["/usr/bin/sandbox-exec", "-f", str(profile_path), cli, "--safe-mode", "--setting-sources", "",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-chrome", "--disable-slash-commands",
            "--no-session-persistence", "--model", "claude-sonnet-4-6", "--tools", "Read,Bash", "--allowedTools", "Read,Bash(printf *)", "--system-prompt", "Synthetic protocol test only.",
            "--output-format", "json", "-p", "Return synthetic final."]
        result = subprocess.run(cmd, cwd=project, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertEqual(json.loads(result.stdout)["result"], "synthetic final")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "/v1/messages?beta=true")
        self.assertNotIn("guest-dummy", json.dumps(calls[0][1]))

    @unittest.skipUnless(shutil.which("codex"), "installed Codex required")
    def test_actual_codex_client_turn_through_broker_and_typed_child(self):
        calls = self.cloud("codex")
        instance = self.cloud_broker()
        frontend = self.pi_bridge(instance)
        vendor = Path("/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai")
        binaries = list(vendor.glob("codex-*/vendor/*/bin/codex"))
        cli = str(binaries[0]) if binaries else shutil.which("codex")
        home, codex_home, project = [self.tmp / name for name in ("codex-home", "codex-config", "codex-project")]
        for directory in (home, codex_home, project): directory.mkdir()
        catalog = {"models": [{"slug": "fixture-model", "display_name": "Synthetic fixture", "description": "Local protocol test",
            "base_instructions": "Synthetic test only.", "shell_type": "shell_command", "visibility": "list", "supported_in_api": True,
            "priority": 0, "supported_reasoning_levels": [], "support_verbosity": False, "supports_reasoning_summaries": True,
            "supports_parallel_tool_calls": True, "apply_patch_tool_type": "freeform", "context_window": 8192,
            "truncation_policy": {"mode": "tokens", "limit": 1000}, "input_modalities": ["text"], "experimental_supported_tools": []}]}
        catalog_path = self.tmp / "catalog.json"; catalog_path.write_text(json.dumps(catalog))
        config = ('model = "fixture-model"\nmodel_provider = "sandbox-broker"\nmodel_catalog_json = %s\n'
            'web_search = "disabled"\ncheck_for_update_on_startup = false\n[analytics]\nenabled = false\n'
            '[features]\nmulti_agent = false\nmulti_agent_v2 = false\ngoals = false\nplugins = false\napps = false\nhooks = false\nview_image = false\nshell_snapshot = false\n'
            '[model_providers.sandbox-broker]\nname = "Synthetic broker"\nbase_url = "%s/codex/v1"\nwire_api = "responses"\n'
            'env_key = "SANDBOX_GATEWAY_TOKEN"\nrequires_openai_auth = false\nsupports_websockets = false\nrequest_max_retries = 0\nstream_max_retries = 0\n'
            '[model_providers.sandbox-broker.capabilities]\nremote_compaction = "unsupported"\nexternal_web_access = false\n') % (json.dumps(str(catalog_path)), frontend)
        (codex_home / "config.toml").write_text(config)
        env = {"HOME": str(home), "CODEX_HOME": str(codex_home), "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"), "PATH": "/usr/bin:/bin", "TMPDIR": str(self.tmp),
            "RUST_LOG": "off", "SANDBOX_GATEWAY_TOKEN": "guest-dummy", "MPLBACKEND": "Agg"}
        cmd = [cli, "--no-daemon", "-a", "never", "exec", "--strict-config", "--ephemeral", "--ignore-rules",
            "--skip-git-repo-check", "-s", "danger-full-access", "-C", str(project), "--json", "Return synthetic final."]
        if sys.platform == "darwin":
            profile = '(version 1)(allow default)(deny network*)(allow network-outbound (remote tcp "localhost:%s"))' % frontend.rsplit(":", 1)[1]
            cmd = ["/usr/bin/sandbox-exec", "-p", profile] + cmd
        result = subprocess.run(cmd, cwd=project, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        records = [json.loads(line) for line in result.stdout.splitlines() if line]
        self.assertTrue(any(record.get("type") == "turn.completed" for record in records), records)
        self.assertTrue(any(record.get("item", {}).get("text") == "synthetic final" for record in records), records)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "/v1/responses")
        self.assertNotIn("guest-dummy", json.dumps(calls[0][1]))


if __name__ == "__main__":
    unittest.main()
