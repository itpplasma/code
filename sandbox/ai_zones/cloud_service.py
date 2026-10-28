"""Internal-zone typed cloud service; OS egress and launch identity are external gates.

The executable reads only the fixed protected configuration and credential files.
No environment credentials, generic proxy, subprocess or guest-selected headers exist.
Launch each immutable service definition with --provider codex or --provider anthropic.
"""
from __future__ import annotations

import json
import os
import re
import socket
import socketserver
import stat
import sys
import threading
import time
from dataclasses import dataclass

from . import policy
from .codex_gateway import CodexGateway, GatewayError as CodexError
from .anthropic_gateway import AnthropicGateway, GatewayError as AnthropicError

PREFIX = "/Library/AgentSandbox" if sys.platform == "darwin" else "/opt/agent-sandbox"
PROVIDERS = ("codex", "anthropic")
MAX_HEADER = 32768
MAX_BODY = 2 * 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024
HEADER_SECONDS = 5.0
BODY_SECONDS = 10.0
TOTAL_SECONDS = 125.0
WRITE_SECONDS = 5.0
MAX_ACTIVE = 16


class ServiceError(Exception):
    def __init__(self, status=400, headers=None):
        self.status = status
        self.headers = headers


def _need(ok):
    if not ok:
        raise ServiceError()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result)
            result[key] = value
        return result
    def constant(_):
        raise ServiceError()
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ServiceError() from None


@dataclass(frozen=True)
class Config:
    provider: str
    model: str
    origin: str
    service_group: int
    socket_group: int
    account_id: str = None
    oauth_beta: str = None


def validate_config(value):
    _need(type(value) is dict and set(value) <= {
        "zone", "provider", "model", "origin", "service_group", "socket_group", "account_id", "oauth_beta"}
        and {"zone", "provider", "model", "origin", "service_group", "socket_group"} <= set(value))
    _need(value["zone"] == "internal" and value["provider"] in ("codex", "anthropic"))
    _need(type(value["service_group"]) is int and 0 < value["service_group"] < 2**31)
    _need(type(value["socket_group"]) is int and 0 < value["socket_group"] < 2**31
          and value["socket_group"] != value["service_group"])
    _need(type(value["model"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value["model"]))
    _need(type(value["origin"]) is str)
    _need(not (value["provider"] == "codex" and "oauth_beta" in value))
    _need(not (value["provider"] == "anthropic" and "account_id" in value))
    config = Config(**{k: v for k, v in value.items() if k != "zone"})
    # Constructors also validate origin and every fixed header value.
    _adapter(config, "configuration-validation-placeholder")
    return config


def _adapter(config, token):
    if config.provider == "codex":
        return CodexGateway(config.origin, config.model, token, config.account_id)
    return AnthropicGateway(config.origin, config.model, token, config.oauth_beta)


def _no_acl(fd):
    if sys.platform == "darwin":
        import ctypes
        import errno
        lib = policy._darwin_acl_library()
        ctypes.set_errno(0)
        acl = lib.acl_get_fd_np(fd, 0x100)
        if not acl:
            _need(ctypes.get_errno() == errno.ENOENT)
            return
        try:
            entry = ctypes.c_void_p()
            ctypes.set_errno(0)
            result = lib.acl_get_entry(acl, 0, ctypes.byref(entry))
            _need(result == -1 and ctypes.get_errno() == errno.EINVAL)
        finally:
            lib.acl_free(acl)
    else:
        _need(not any(name.startswith("system.posix_acl_") for name in os.listxattr(fd)))


def protected_json(path, group):
    """Read the same immutable inode whose ancestry and ACL were checked."""
    checked, before = policy._assert_protected_stat(path)
    fd = os.open(checked, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        _need((before.st_dev, before.st_ino) == (info.st_dev, info.st_ino))
        _need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == group
              and stat.S_IMODE(info.st_mode) == 0o640 and info.st_nlink == 1)
        _no_acl(fd)
        raw = os.read(fd, 65537)
        _need(len(raw) <= 65536)
        after = os.fstat(fd)
        _need((info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
              (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
        return strict_json(raw)
    finally:
        os.close(fd)


def provider_paths(provider):
    _need(type(provider) is str and provider in PROVIDERS)
    return (PREFIX + "/config/internal/" + provider + ".json",
            PREFIX + "/config/internal/" + provider + "-credentials.json",
            PREFIX + "/run/internal/" + provider + "/cloud.sock")


def load_configuration(provider):
    _need(os.geteuid() != 0 and os.getegid() != 0)
    config_path, credential_path, _ = provider_paths(provider)
    config = validate_config(protected_json(config_path, os.getegid()))
    _need(config.provider == provider and config.service_group == os.getegid())
    credentials = protected_json(credential_path, config.service_group)
    _need(type(credentials) is dict and set(credentials) == {"token"})
    token = credentials["token"]
    _need(type(token) is str and 0 < len(token) <= 16384
          and all(32 <= ord(ch) < 127 for ch in token))
    return config, _adapter(config, token)


def socket_preflight(path, group):
    _need(os.geteuid() != 0 and group in {os.getegid(), *os.getgroups()})
    _need(type(path) is str and path.startswith("/") and not path.startswith("//")
          and os.path.normpath(path) == path and "\x00" not in path)
    parent = os.path.dirname(path)
    # The final directory belongs to this service; every earlier component is protected.
    policy.assert_protected(os.path.dirname(parent), directory=True)
    info = os.lstat(parent)
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
          and info.st_gid == group and stat.S_IMODE(info.st_mode) == 0o710)
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _need((info.st_dev, info.st_ino) == (os.fstat(fd).st_dev, os.fstat(fd).st_ino))
        _no_acl(fd)
    finally:
        os.close(fd)
    _need(not os.path.lexists(path))


class _Reader:
    def __init__(self, connection, deadline):
        self.connection, self.buffer = connection, b""
        self.total_deadline = deadline
        self.deadline = min(time.monotonic() + HEADER_SECONDS, deadline)

    def read(self, size):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise ServiceError(408)
        self.connection.settimeout(left)
        try:
            data = self.connection.recv(size)
        except socket.timeout:
            raise ServiceError(408) from None
        if not data:
            raise ServiceError()
        return data

    def line(self, limit):
        while b"\n" not in self.buffer:
            if len(self.buffer) > limit:
                raise ServiceError(431)
            self.buffer += self.read(min(4096, limit + 2 - len(self.buffer)))
        line, self.buffer = self.buffer.split(b"\n", 1)
        _need(line.endswith(b"\r") and len(line) <= limit + 1)
        return line[:-1]

    def body(self, size):
        self.deadline = min(time.monotonic() + BODY_SECONDS, self.total_deadline)
        while len(self.buffer) < size:
            self.buffer += self.read(min(16384, size - len(self.buffer)))
        _need(len(self.buffer) == size)
        return self.buffer


def read_request(connection, provider, deadline):
    reader = _Reader(connection, deadline)
    line = reader.line(4096)
    parts = line.split(b" ")
    _need(len(parts) == 3 and parts[2] == b"HTTP/1.1")
    method, path = parts[:2]
    routes = ({(b"GET", b"/v1/models"), (b"POST", b"/v1/responses")} if provider == "codex"
              else {(b"POST", b"/v1/messages?beta=true")})
    _need((method, path) in routes)
    headers, total = {}, len(line)
    for _ in range(33):
        line = reader.line(8192)
        total += len(line) + 2
        _need(total <= MAX_HEADER)
        if not line:
            break
        _need(b":" in line)
        key, value = line.split(b":", 1)
        _need(re.fullmatch(br"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) is not None)
        key = key.lower()
        value = value.strip(b" \t")
        _need(key not in headers and all(32 <= byte < 127 for byte in value))
        _need(key in (b"host", b"content-type", b"content-length", b"accept", b"connection", b"user-agent"))
        headers[key] = value
    else:
        raise ServiceError(431)
    _need(bool(headers.get(b"host")) and headers.get(b"connection", b"close").lower() in (b"close", b"keep-alive"))
    if method == b"GET":
        _need(headers.get(b"content-length", b"0") == b"0" and not reader.buffer)
        return path.decode(), None
    _need(headers.get(b"content-type", b"").lower() == b"application/json")
    length = headers.get(b"content-length", b"")
    _need(re.fullmatch(br"[1-9][0-9]{0,7}", length) is not None)
    size = int(length)
    if size > MAX_BODY:
        raise ServiceError(413)
    return path.decode(), strict_json(reader.body(size))


def _send(connection, data, deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise ServiceError(504)
    connection.settimeout(min(WRITE_SECONDS, left))
    connection.sendall(data)


_RATE_HEADERS = frozenset(
    "x-ratelimit-" + kind + "-" + unit
    for kind in ("limit", "remaining", "reset") for unit in ("requests", "tokens")
)


def _safe_headers(headers):
    if not isinstance(headers, dict) or len(headers) > 128:
        return {}
    safe, seen, size = {}, set(), 0
    for key, value in headers.items():
        if type(key) is not str:
            continue
        name = key.lower()
        if name in seen:
            previous = safe.pop(name, None)
            if previous is not None:
                size -= len(name) + len(previous) + 4
            continue
        seen.add(name)
        allowed = (name in _RATE_HEADERS or name in ("retry-after", "x-should-retry")
                   or re.fullmatch(r"anthropic-ratelimit-unified-[a-z0-9]+(?:-[a-z0-9]+)*", name))
        if (not allowed or len(name) > 128 or type(value) is not str or len(value) > 512
                or not all(32 <= ord(ch) < 127 for ch in name + value)):
            continue
        length = len(name) + len(value) + 4
        if len(safe) < 32 and size + length <= 8192:
            safe[name] = value
            size += length
    return safe


def _header_block(headers):
    return b"".join((key + ": " + value + "\r\n").encode("ascii")
                    for key, value in _safe_headers(headers).items())


def _error(connection, status, headers=None):
    if status not in (400, 401, 403, 404, 408, 413, 429, 431, 500, 502, 503, 504, 529):
        status = 502
    body = b'{"error":{"type":"gateway_error","code":"request_failed"}}'
    head = (("HTTP/1.1 %d Error\r\nContent-Type: application/json\r\nContent-Length: %d\r\n" % (status, len(body))).encode()
            + _header_block(headers) + b"Connection: close\r\n\r\n")
    try:
        _send(connection, head + body, time.monotonic() + 0.1)
    except (OSError, ServiceError):
        pass


class _Cancellation:
    def __init__(self):
        self._lock = threading.Lock()
        self._callback = None
        self._cancelled = False

    def register(self, callback):
        with self._lock:
            _need(callable(callback) and self._callback is None)
            self._callback = callback
            cancelled = self._cancelled
        if cancelled:
            callback()

    def cancel(self):
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            callback = self._callback
        if callback is not None:
            callback()

    def cancelled(self):
        with self._lock:
            return self._cancelled


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        response, started = None, False
        stop = threading.Event()
        deadline = time.monotonic() + TOTAL_SECONDS
        peer = self.request.dup()
        peer.setblocking(False)
        state = _Cancellation()

        def watch():
            while not stop.wait(.025):
                try:
                    peer.send(b"", socket.MSG_DONTWAIT)
                except BlockingIOError:
                    pass
                except OSError:
                    state.cancel()
                    return
                if time.monotonic() >= deadline:
                    state.cancel()
                    return
        watcher = threading.Thread(target=watch, daemon=True)
        try:
            watcher.start()
            path, document = read_request(self.request, self.server.config.provider, deadline)
            response = self.server.adapter.request(path, document, deadline=deadline,
                                                   register_cancel=state.register)
            if state.cancelled():
                raise ServiceError(504)
            if response.status != 200:
                raise ServiceError(response.status, response.headers)
            iterator = iter(response.iter_bytes())
            first = next(iterator)
            _need(response.content_type in ("application/json", "text/event-stream"))
            started = True
            _send(self.request, ("HTTP/1.1 200 OK\r\nContent-Type: " + response.content_type +
                  "\r\n").encode() + _header_block(response.headers)
                  + b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n", deadline)
            total = 0
            def chunks():
                yield first
                yield from iterator
            for chunk in chunks():
                _need(type(chunk) is bytes and bool(chunk))
                total += len(chunk)
                if total > MAX_OUTPUT:
                    raise ServiceError(502)
                _send(self.request, ("%x\r\n" % len(chunk)).encode() + chunk + b"\r\n", deadline)
            _send(self.request, b"0\r\n\r\n", deadline)
        except StopIteration:
            if not started:
                _error(self.request, 502, getattr(response, "headers", None))
        except (ServiceError, CodexError, AnthropicError) as error:
            if not started:
                _error(self.request, error.status, getattr(error, "headers", None)
                       or getattr(response, "headers", None))
        except (OSError, ValueError, TypeError, RecursionError):
            if not started:
                _error(self.request, 400)
        except Exception:
            if not started:
                _error(self.request, 502)
        finally:
            stop.set()
            state.cancel()
            if response is not None:
                response.close()
            if watcher.ident is not None:
                watcher.join(.2)
            peer.close()


class CloudService(socketserver.ThreadingUnixStreamServer):
    """Construction accepts injected fixtures; the executable never bypasses authority."""
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, config, adapter, socket_path=None, *, preflight=socket_preflight):
        _need(os.geteuid() != 0)
        _need(config.provider in ("codex", "anthropic"))
        if socket_path is None:
            socket_path = provider_paths(config.provider)[2]
        preflight(socket_path, config.socket_group)
        self.config, self.adapter, self.path = config, adapter, socket_path
        self.identity = None
        self.slots = threading.BoundedSemaphore(MAX_ACTIVE)
        mask = os.umask(0o077)
        try:
            super().__init__(socket_path, _Handler)
        finally:
            os.umask(mask)
        try:
            info = os.lstat(socket_path)
            _need(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid())
            self.identity = (info.st_dev, info.st_ino)
            os.chown(socket_path, -1, config.socket_group, follow_symlinks=False)
            os.chmod(socket_path, 0o660, follow_symlinks=False)
            info = os.lstat(socket_path)
            _need((info.st_dev, info.st_ino) == self.identity and info.st_gid == config.socket_group
                  and stat.S_IMODE(info.st_mode) == 0o660)
        except BaseException:
            self.server_close()
            raise

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            _error(request, 503)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, address):
        pass

    def server_close(self):
        super().server_close()
        try:
            info = os.lstat(self.path)
            if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self.identity:
                os.unlink(self.path)
        except FileNotFoundError:
            pass


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        _need(os.geteuid() != 0 and len(argv) == 2 and argv[0] == "--provider"
              and argv[1] in PROVIDERS)
        config, adapter = load_configuration(argv[1])
        with CloudService(config, adapter) as server:
            server.serve_forever(poll_interval=.1)
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("cloud service refused launch", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
