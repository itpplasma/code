#!/usr/bin/env python3
"""Minimal restricted two-zone host broker for guest agents.

One process serves exactly one zone over exactly one Unix-domain socket.  The
zone, its resource ceilings, its approved file root and its single model
upstream come from a protected policy document and the launch arguments.  No
request can select a zone, name an upstream, add a header, or reach a path
outside the approved root of the zone this process was launched for.

Base surface:

    POST /v1/files/read          {"path": "<relative>"}
    POST /v1/files/write         {"path": "<relative>", "content_base64": "..."}
    POST /v1/files/list          {"path": "<relative or empty>"}
    POST /v1/chat/completions    {"messages": [{"role": ..., "content": ...}], ...}

Optional internal-only service routes, enabled by protected services.json:

    POST /codex/v1/responses
    GET  /codex/v1/models
    POST /claude/v1/messages?beta=true

The chat route delegates request and response validation to ModelGateway.
It supports bounded text and guest-executed function tools, including validated
Server-Sent Events. Every upstream origin and model remains fixed by policy.

Policy comes from :mod:`ai_zones.policy`, the shared model: the broker consumes
the immutable :class:`~ai_zones.policy.Policy` returned by
``policy.validate_policy`` / ``policy.load_policy`` and never re-validates a
second, incompatible shape.  Production always loads with ``protected=True``.

Containment is done with pinned directory descriptors, not with strings.  The
approved root is opened once at launch, component by component with
``O_NOFOLLOW`` from ``/`` upwards, so no symlink ancestor can be followed and
no later rename below a mutable ancestor can move the root under the broker.
Everything below that root is opened relative to it with ``O_NOFOLLOW``, and
only single-link regular files are handed to a guest.

Scope, stated plainly: this process is **not** an OS egress sandbox.  It
confines what a guest may ask *this broker* to do.  It does not confine the
guest process itself, installs no network policy, performs no privileged
actions, and integrates with no mail, file-sharing or other owner service.
``cpus``, ``memory_mib`` and ``disk_gib`` are validated; they are
enforced outside this process by the OS (cgroups, quotas, the launcher).
Anything not implemented here fails closed.

Launch (as the dedicated broker identity; the socket group is a root-fixed
launch decision, never a guest field)::

    python3 -m ai_zones.broker --policy /etc/ai-zones/policy.json \
        --zone internal --socket /run/ai-zones/internal.sock \
        --socket-group 4010
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import json
import math
import os
import re
import secrets
import select
import signal
import socket
import socketserver
import stat
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Sequence, Tuple

from .model_gateway import GatewayError, ModelGateway

from .policy import (
    ModelEndpoint,
    Policy,
    PolicyError,
    ZONE_NAMES,
    assert_no_mutating_acl,
    _assert_protected_stat,
    assert_protected,
    load_policy,
)

PROTOCOL = "ai-zones-broker/1"

#: Zones come from the shared policy model, not from a private list.
ZONES: Tuple[str, ...] = tuple(ZONE_NAMES)
POLICY_VERSION = 1

ENDPOINTS: frozenset = frozenset(
    {
        "/v1/files/read",
        "/v1/files/write",
        "/v1/files/list",
        "/v1/chat/completions",
    }
)

PREFIX = "/Library/AgentSandbox" if sys.platform == "darwin" else "/opt/agent-sandbox"
CLOUD_ROUTES = {
    ("POST", "/codex/v1/responses"): ("codex", "/v1/responses"),
    ("GET", "/codex/v1/models"): ("codex", "/v1/models"),
    ("POST", "/claude/v1/messages?beta=true"): ("anthropic", "/v1/messages?beta=true"),
}
MAX_SERVICES_BYTES = 64 * 1024
MAX_CLOUD_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_RATE_HEADERS = 32
MAX_RATE_HEADER_BYTES = 8192
RATE_HEADERS = frozenset({"retry-after", "x-should-retry"} | {
    "x-ratelimit-" + field + "-" + resource
    for field in ("limit", "remaining", "reset") for resource in ("requests", "tokens")
})


# Framing budgets.  Every one of these is a limit that holds *while* the
# bytes arrive, not only after the whole request is buffered.
MAX_REQUEST_LINE_BYTES = 4 * 1024
MAX_HEADER_LINE_BYTES = 8 * 1024
MAX_REQUEST_HEADER_BYTES = 32 * 1024
MAX_REQUEST_HEADERS = 32
MAX_REQUEST_BODY_BYTES = 1 << 20  # 1 MiB per request body
MAX_RESPONSE_BODY_BYTES = 4 << 20  # 4 MiB read from the upstream
MAX_FILE_BYTES = 1 << 20  # 1 MiB per file read or write
MAX_LIST_ENTRIES = 512
MAX_LIST_SCAN_ENTRIES = 4096  # directory entries looked at before stopping
MAX_PATH_CHARS = 4096
MAX_NAME_CHARS = 255
MAX_JSON_DEPTH = 8
MAX_TOKEN_FILE_BYTES = 8 * 1024

#: Absolute deadlines.  A stalled guest is dropped at its deadline instead of
#: holding a thread for one blocking operation at a time.
HEADER_TIMEOUT_SECONDS = 5.0
BODY_TIMEOUT_SECONDS = 15.0
UPSTREAM_TIMEOUT_SECONDS = 30.0
RESPONSE_TIMEOUT_SECONDS = 10.0
#: Hard ceiling for one connection from accept to last byte, whatever phase it
#: is in.  With the defaults above the sum is 60 s, so this never loosens a
#: phase limit; it only bounds the whole.
REQUEST_DEADLINE_SECONDS = 60.0
#: Active connections.  Beyond this a guest is refused before a thread exists.
MAX_ACTIVE_CONNECTIONS = 64
MIN_ACTIVE_CONNECTIONS = 1
MAX_ACTIVE_CONNECTIONS_LIMIT = 256
#: Time given to write a refusal to a guest that is already gone.
REFUSAL_WRITE_TIMEOUT_SECONDS = 1.0

LOOPBACK_HOSTS: frozenset = frozenset({"127.0.0.1", "::1", "[::1]"})
_READ_CHUNK = 64 * 1024

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class BrokerError(Exception):
    """A refusal.  Carries the HTTP status and a stable machine code.

    ``message`` is what the guest sees: it names the rule, never a host path,
    a configuration value or an upstream.  Details stay in the log.
    """

    def __init__(self, status: int, code: str, message: str, *, headers: Optional[dict] = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = {} if headers is None else dict(headers)


class _Handled(Exception):
    """Raised once an answer has been written, to end the request cleanly."""


# --------------------------------------------------------------------------
# Strict JSON
# --------------------------------------------------------------------------


def strict_json_loads(raw: bytes, *, where: str = "request") -> Any:
    """Parse JSON rejecting duplicate keys, NaN/Infinity and non-UTF-8."""

    def pairs(pairs: list) -> dict:
        seen: set = set()
        for key, _ in pairs:
            if key in seen:
                raise BrokerError(400, "duplicate_key", f"{where}: duplicate key {key!r}")
            seen.add(key)
        return dict(pairs)

    def finite_float(raw: str) -> float:
        value = float(raw)
        if not math.isfinite(value):
            raise BrokerError(400, "invalid_number", f"{where}: number is not finite")
        return value

    def constant(name: str) -> Any:
        raise BrokerError(400, "invalid_number", f"{where}: {name} is not allowed")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrokerError(400, "invalid_encoding", f"{where}: not valid UTF-8") from exc
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)
    except BrokerError:
        raise
    except (ValueError, RecursionError) as exc:
        raise BrokerError(400, "invalid_json", f"{where}: malformed JSON") from exc


def check_depth(value: Any, *, where: str, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise BrokerError(400, "too_deep", f"{where}: nested deeper than {MAX_JSON_DEPTH}")
    if isinstance(value, dict):
        for item in value.values():
            check_depth(item, where=where, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            check_depth(item, where=where, depth=depth + 1)


def expect_fields(obj: dict, allowed: Iterable[str], where: str) -> dict:
    """Return obj unchanged if it carries no field outside `allowed`."""
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise BrokerError(400, "unknown_field", f"{where}: unsupported field(s) {unknown}")
    return obj


def field(fields: dict, name: str, where: str) -> Any:
    if name not in fields:
        raise BrokerError(400, "missing_field", f"{where}: {name} is required")
    return fields[name]


# --------------------------------------------------------------------------
# Policy: the shared model, projected onto this zone
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelTarget:
    """One fixed upstream origin plus its protected credential, if any."""

    scheme: str
    host: str
    port: int
    name: str
    token_file: Optional[str]

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


@dataclass(frozen=True)
class ZoneConfig:
    zone: str
    cpus: int
    memory_mib: int
    disk_gib: int
    files_root: Optional[str]
    files_writable: bool
    model: Optional[ModelTarget]


def _origin(endpoint: ModelEndpoint, *, zone: str) -> Tuple[str, str, int]:
    """Split the policy origin into (scheme, host, port), fail closed.

    The shared model already checked the URL's shape.  What is added here is
    the broker's own transport rule: plaintext only on loopback.
    """
    scheme, host, port = split_origin(endpoint.url)
    if zone == "confidential" and host not in LOOPBACK_HOSTS:
        raise PolicyError("confidential zone is loopback-only")
    if host not in LOOPBACK_HOSTS:
        raise PolicyError("this completion surface requires a literal loopback origin")
    return scheme, host, port


def split_origin(url: str) -> Tuple[str, str, int]:
    """Split an already-validated origin; no DNS, no network, ever."""
    scheme, _, rest = url.partition("://")
    if scheme not in ("http", "https"):
        raise PolicyError("model.url: only http or https origins")
    netloc = rest.split("/", 1)[0]
    if netloc.startswith("["):
        end = netloc.find("]")
        host, remainder = netloc[: end + 1], netloc[end + 1 :]
    elif ":" in netloc:
        host, _, port_text = netloc.rpartition(":")
        remainder = ":" + port_text if port_text.isdigit() else ""
    else:
        host, remainder = netloc, ""
    if not host:
        raise PolicyError("model.url: missing host")
    port = None
    if remainder.startswith(":"):
        digits = remainder[1:]
        if not digits or any(byte not in _DIGITS for byte in digits.encode("ascii")):
            raise PolicyError("model.url: malformed port")
        port = int(digits)
        if not 1 <= port <= 65535:
            raise PolicyError("model.url: port out of range")
    elif remainder:
        raise PolicyError("model.url: malformed port")
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def project_zone(policy: Any, zone: str) -> ZoneConfig:
    """Project one zone of a validated shared ``Policy``.

    Only the shared immutable model is accepted: a bare dictionary is not a
    policy here, so the broker cannot be handed a shape the loader rejected.
    """
    if zone not in ZONES:
        raise PolicyError(f"unknown zone {zone!r}: choose one of {list(ZONES)}")
    if not isinstance(policy, Policy):
        raise PolicyError("policy: must be a validated ai_zones.policy.Policy, not raw input")
    if policy.version != POLICY_VERSION:
        raise PolicyError(f"policy.version: must be {POLICY_VERSION}")
    entry = policy.zones.get(zone)
    if entry is None:
        raise PolicyError(f"policy.zones.{zone}: missing")
    files = entry.files
    model: Optional[ModelTarget] = None
    if entry.model is not None:
        scheme, host, port = _origin(entry.model, zone=zone)
        model = ModelTarget(
            scheme=scheme,
            host=host,
            port=port,
            name=entry.model.name,
            token_file=entry.model.token_file,
        )
    return ZoneConfig(
        zone=zone,
        cpus=entry.cpus,
        memory_mib=entry.memory_mib,
        disk_gib=entry.disk_gib,
        files_root=None if files is None else files.root,
        files_writable=False if files is None else files.writable,
        model=model,
    )


def load_policy_file(path: str) -> Policy:
    """Production policy load through the shared loader.  Always protected."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise PolicyError("policy path: must be absolute")
    return load_policy(path, protected=True)


@dataclass(frozen=True)
class ServiceIdentity:
    uid: int
    gid: int


@dataclass(frozen=True)
class Services:
    codex: Optional[ServiceIdentity] = None
    anthropic: Optional[ServiceIdentity] = None

    def identity(self, provider: str) -> Optional[ServiceIdentity]:
        if provider not in ("codex", "anthropic"):
            raise PolicyError("unknown cloud service role")
        return getattr(self, provider)


def validate_services(document: Any, *, socket_group: Optional[int] = None) -> Services:
    if type(document) is not dict or set(document) != {"codex", "anthropic"}:
        raise PolicyError("services: expected exactly the codex and anthropic roles")
    roles = {}
    used = set()
    for provider in ("codex", "anthropic"):
        entry = document[provider]
        if type(entry) is not dict or set(entry) != {"enabled", "uid", "gid"}:
            raise PolicyError("services: each role requires enabled, uid and gid")
        if type(entry["enabled"]) is not bool:
            raise PolicyError("services: enabled must be boolean")
        uid, gid = entry["uid"], entry["gid"]
        if type(uid) is not int or type(gid) is not int or uid != gid or not 600 <= uid <= 999:
            raise PolicyError("services: roles require matching non-root UID/GID in 600 through 999")
        if uid in used or uid in (os.geteuid(), os.getegid(), socket_group):
            raise PolicyError("services: provider roles and broker identities must be distinct")
        used.add(uid)
        roles[provider] = ServiceIdentity(uid, gid) if entry["enabled"] else None
    return Services(**roles)


def _read_services(path: str) -> Any:
    _, named = _assert_protected_stat(path)
    fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC | _O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        identity = lambda st: (st.st_dev, st.st_ino, st.st_uid, st.st_gid, st.st_mode, st.st_nlink)
        if identity(opened) != identity(named) or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise PolicyError("services: opened object differs from the checked file")
        if opened.st_uid != 0 or opened.st_gid != os.getegid() or stat.S_IMODE(opened.st_mode) != 0o640:
            raise PolicyError("services: expected root-owned mode 0640 in the broker private group")
        assert_no_mutating_acl(path, fd=fd)
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(4096, MAX_SERVICES_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SERVICES_BYTES:
                raise PolicyError("services: configuration exceeds the byte limit")
            chunks.append(chunk)
        after = os.fstat(fd)
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (total, after.st_mtime_ns, after.st_ctime_ns):
            raise PolicyError("services: configuration changed while reading")
        try:
            return strict_json_loads(b"".join(chunks), where="services")
        except BrokerError:
            raise PolicyError("services: invalid JSON") from None
    finally:
        os.close(fd)


def load_services(zone: str, *, socket_group: Optional[int] = None) -> Services:
    if zone == "confidential":
        return Services()
    if zone != "internal":
        raise PolicyError("services: unknown fixed zone")
    path = PREFIX + "/config/internal/services.json"
    try:
        os.lstat(path)
    except FileNotFoundError:
        return Services()
    except OSError:
        raise PolicyError("services: protected configuration is unavailable") from None
    if os.getegid() == 0 or os.getegid() == socket_group:
        raise PolicyError("services: broker private group must differ from its socket group")
    try:
        return validate_services(_read_services(path), socket_group=socket_group)
    except OSError:
        raise PolicyError("services: protected configuration is unavailable") from None


def _cloud_socket(provider: str) -> str:
    if provider not in ("codex", "anthropic"):
        raise PolicyError("unknown cloud service role")
    return PREFIX + "/run/internal/" + provider + "/cloud.sock"


def _check_service_socket(path: str, expected: ServiceIdentity) -> None:
    parent = os.path.dirname(path)
    assert_protected(os.path.dirname(parent), directory=True)
    named = os.lstat(parent)
    fd = os.open(parent, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino) or not stat.S_ISDIR(info.st_mode):
            raise PolicyError("cloud service directory changed")
        if info.st_uid != expected.uid or stat.S_IMODE(info.st_mode) != 0o710:
            raise PolicyError("cloud service directory has the wrong authority")
        assert_no_mutating_acl(parent, fd=fd)
        node = os.stat("cloud.sock", dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISSOCK(node.st_mode) or node.st_uid != expected.uid or node.st_gid != info.st_gid or stat.S_IMODE(node.st_mode) != 0o660:
            raise PolicyError("cloud service socket has the wrong authority")
    finally:
        os.close(fd)


def _peer_identity(connection: socket.socket) -> Tuple[int, int]:
    if sys.platform == "darwin":
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        function = ctypes.CDLL(None, use_errno=True).getpeereid
        function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        function.restype = ctypes.c_int
        if function(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise OSError(ctypes.get_errno(), "cannot verify cloud service peer")
        return uid.value, gid.value
    if sys.platform.startswith("linux") and hasattr(socket, "SO_PEERCRED"):
        _, uid, gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return uid, gid
    raise OSError("cloud service peer credentials are unsupported")


# --------------------------------------------------------------------------
# Pinned directories
# --------------------------------------------------------------------------


def _check_dir_ownership(where: str, info: os.stat_result, *, final: bool) -> None:
    """Owner must be root or this service identity; no group/other write."""
    if info.st_uid not in (0, os.geteuid()):
        raise PolicyError(f"{where}: owned by uid {info.st_uid}, not root or the service identity")
    if info.st_mode & 0o022:
        if not final and info.st_uid == 0 and info.st_mode & stat.S_ISVTX:
            return  # a sticky root-owned ancestor cannot have its entries renamed by others
        raise PolicyError(f"{where}: writable by group or other")


def pin_directory(path: str, *, where: str, protected_ancestors: bool = False) -> int:
    """Open ``path`` as a directory descriptor, refusing symlink ancestors.

    Each component is opened with ``O_NOFOLLOW`` relative to the descriptor of
    its already-pinned parent, and the opened object is compared with the
    object the path names, so the descriptor and the string are the same
    directory.  Ownership and mutability of every ancestor are checked here,
    once, at launch.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        raise PolicyError(f"{where}: must be an absolute path")
    if path.startswith("//") or "\x00" in path:
        raise PolicyError(f"{where}: forbidden character")
    if os.path.normpath(path) != path:
        raise PolicyError(f"{where}: must be a normalized path")
    components = [c for c in path.split("/") if c]
    try:
        fd = os.open("/", os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC)
    except OSError as exc:
        raise PolicyError(f"{where}: cannot open the filesystem root ({exc.strerror})") from exc
    checked = "/"
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise PolicyError(f"{where}: the filesystem root is not a directory")
        _check_dir_ownership("/", info, final=False)
        assert_no_mutating_acl("/")
        for index, component in enumerate(components):
            final = index == len(components) - 1
            try:
                child = os.open(
                    component,
                    os.O_RDONLY | _O_NOFOLLOW | _O_DIRECTORY | _O_CLOEXEC,
                    dir_fd=fd,
                )
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise PolicyError(f"{where}: symlinked ancestor {checked}/{component}") from exc
                raise PolicyError(f"{where}: {checked}/{component} ({exc.strerror})") from exc
            checked = checked.rstrip("/") + "/" + component
            try:
                named = os.lstat(checked)
                opened = os.fstat(child)
            except OSError as exc:
                os.close(child)
                raise PolicyError(f"{where}: {checked} ({exc.strerror})") from exc
            if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino) or stat.S_ISLNK(named.st_mode):
                os.close(child)
                raise PolicyError(f"{where}: {checked} changed while it was being opened")
            try:
                _check_dir_ownership(checked, opened, final=final)
                if protected_ancestors and not final and opened.st_uid != 0:
                    raise PolicyError(f"{where}: runtime ancestors must be root-owned")
                assert_no_mutating_acl(checked, fd=child)
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


class _Fd:
    """Context manager for a bare file descriptor."""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def __enter__(self) -> int:
        return self.fd

    def __exit__(self, *exc: Any) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


def _fsync_dir_fd(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError:
        pass  # durability hint only; the rename already happened


def _refuse_reason(dir_fd: int, name: str, exc: OSError) -> BrokerError:
    """Name the rule that refused a component, without naming the path."""
    if exc.errno == errno.ELOOP:
        return BrokerError(403, "symlink_denied", "symlinks are not followed")
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return BrokerError(404, "not_found", "path does not exist")
    if stat.S_ISLNK(info.st_mode):
        return BrokerError(403, "symlink_denied", "symlinks are not followed")
    if exc.errno in (errno.ENOENT, errno.ENOTDIR):
        return BrokerError(404, "not_found", "path does not exist")
    return BrokerError(403, "path_denied", "path refused")


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def split_relpath(value: Any, *, where: str = "path", allow_empty: bool = False) -> Tuple[str, ...]:
    """Validate a guest path and split it into safe components."""
    if not isinstance(value, str):
        raise BrokerError(400, "invalid_path", f"{where}: must be a string")
    if len(value) > MAX_PATH_CHARS:
        raise BrokerError(413, "invalid_path", f"{where}: too long")
    if value == "":
        if allow_empty:
            return ()
        raise BrokerError(400, "invalid_path", f"{where}: must not be empty")
    if value.startswith("/") or value.startswith("~"):
        raise BrokerError(400, "absolute_path", f"{where}: host paths are not accepted")
    if "\\" in value or "\x00" in value:
        raise BrokerError(400, "invalid_path", f"{where}: forbidden character")
    components = value.split("/")
    for component in components:
        if component.casefold() in {".git", ".hg", ".svn", ".lfsconfig", ".gitattributes", ".gitmodules"}:
            raise BrokerError(403, "control_metadata_denied", "repository control metadata is unavailable")
        if component in ("", ".", ".."):
            raise BrokerError(400, "traversal", f"{where}: only plain relative components are allowed")
        if len(component.encode("utf-8")) > MAX_NAME_CHARS:
            raise BrokerError(400, "invalid_path", f"{where}: component too long")
    return tuple(components)


# --------------------------------------------------------------------------
# Filesystem access below one pinned root
# --------------------------------------------------------------------------


class RootAccess:
    """Opens guest paths below one approved root, never following symlinks.

    The root descriptor is pinned once, at launch, and every operation is
    relative to it.  Only single-link regular files are readable or writable:
    a hardlink is a second name for content that lives somewhere else, and the
    broker cannot tell from inside who else holds it.
    """

    def __init__(self, root: str, *, writable: bool) -> None:
        if root == "/" or root == os.path.expanduser("~"):
            raise PolicyError("approved root must be a dedicated data directory")
        self._fd = pin_directory(root, where="approved root")
        info = os.fstat(self._fd)
        self._identity = (info.st_dev, info.st_ino)
        self.writable = writable
        self.root = root

    @property
    def identity(self) -> Tuple[int, int]:
        return self._identity

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = -1

    def _still_pinned(self) -> None:
        info = os.fstat(self._fd)
        if (info.st_dev, info.st_ino) != self._identity:
            raise BrokerError(503, "root_unavailable", "approved root is no longer the pinned directory")

    def _descend(self, components: Sequence[str]) -> int:
        """Fresh descriptor for the deepest directory below the pinned root."""
        fd = os.dup(self._fd)
        try:
            for component in components:
                try:
                    nxt = os.open(
                        component, os.O_RDONLY | _O_NOFOLLOW | _O_DIRECTORY | _O_CLOEXEC,
                        dir_fd=fd,
                    )
                except OSError as exc:
                    raise _refuse_reason(fd, component, exc) from exc
                os.close(fd)
                fd = nxt
            return fd
        except BaseException:
            os.close(fd)
            raise

    def read(self, components: Tuple[str, ...]) -> dict:
        if not components:
            raise BrokerError(400, "invalid_path", "path must name a file")
        self._still_pinned()
        with _Fd(self._descend(components[:-1])) as parent_fd:
            try:
                # O_NONBLOCK: a fifo or device node is refused below by fstat,
                # and must not block the broker while it is being opened.
                fd = os.open(
                    components[-1],
                    os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC | _O_NONBLOCK,
                    dir_fd=parent_fd,
                )
            except OSError as exc:
                raise _refuse_reason(parent_fd, components[-1], exc) from exc
            with _Fd(fd) as file_fd:
                info = os.fstat(file_fd)
                if not stat.S_ISREG(info.st_mode):
                    raise BrokerError(403, "not_regular_file", "only regular files are readable")
                if info.st_nlink != 1:
                    raise BrokerError(403, "hardlink_denied", "hardlinks are not readable here")
                if info.st_size > MAX_FILE_BYTES:
                    raise BrokerError(413, "file_too_large", "file exceeds the read limit")
                data = _read_bounded_fd(file_fd, MAX_FILE_BYTES)
        return {
            "path": "/".join(components),
            "size_bytes": len(data),
            "content_base64": base64.b64encode(data).decode("ascii"),
        }

    def list(self, components: Tuple[str, ...]) -> dict:
        self._still_pinned()
        with _Fd(self._descend(components)) as dir_fd:
            entries: list = []
            scanned = 0
            truncated = False
            iterator = os.scandir(dir_fd)
            try:
                for entry in iterator:
                    scanned += 1
                    if scanned > MAX_LIST_SCAN_ENTRIES:
                        truncated = True
                        break
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if entry.name.casefold() in {".git", ".hg", ".svn", ".lfsconfig", ".gitattributes", ".gitmodules"}:
                        continue
                    if stat.S_ISLNK(info.st_mode):
                        continue  # never reported, never followed
                    if stat.S_ISDIR(info.st_mode):
                        entries.append({"name": entry.name, "type": "dir"})
                    elif stat.S_ISREG(info.st_mode):
                        if info.st_nlink != 1:
                            continue  # a second name for content held elsewhere
                        entries.append({"name": entry.name, "type": "file", "size_bytes": info.st_size})
                    if len(entries) >= MAX_LIST_ENTRIES:
                        truncated = True
                        break
            finally:
                iterator.close()
        entries.sort(key=lambda item: item["name"])
        return {"path": "/".join(components), "entries": entries, "truncated": truncated}

    def write(self, components: Tuple[str, ...], data: bytes) -> dict:
        if not self.writable:
            raise BrokerError(403, "write_denied", "this zone's root is read-only")
        if not components:
            raise BrokerError(400, "invalid_path", "path must name a file")
        if len(data) > MAX_FILE_BYTES:
            raise BrokerError(413, "file_too_large", "write exceeds the file limit")
        self._still_pinned()
        with _Fd(self._descend(components[:-1])) as parent_fd:
            name = components[-1]
            try:
                existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            except OSError:
                raise BrokerError(403, "path_denied", "target refused") from None
            if existing is not None:
                if stat.S_ISLNK(existing.st_mode):
                    raise BrokerError(403, "symlink_denied", "will not write through a symlink")
                if not stat.S_ISREG(existing.st_mode):
                    raise BrokerError(403, "not_regular_file", "will not replace a non-file")
                if existing.st_nlink != 1:
                    raise BrokerError(403, "hardlink_denied", "will not replace a hardlink")
            tmp = f".{name}.{os.getpid()}.{secrets.token_hex(8)}.partial"
            published = False
            fd = None
            try:
                fd = os.open(
                    tmp,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC,
                    0o600,
                    dir_fd=parent_fd,
                )
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError(errno.EIO, "staging write made no progress")
                    view = view[written:]
                os.fsync(fd)
                os.close(fd)
                fd = None
                os.rename(tmp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                published = True
            except OSError as exc:
                # Cleanup stays relative to the already opened parent, on every
                # error, so a different working directory can never be touched
                # and no staging file is left behind.
                _unlink_in(parent_fd, tmp)
                if exc.errno == errno.ENOSPC:
                    raise BrokerError(507, "storage_full", "the storage limit was reached") from exc
                stage = "publish" if published else "stage"
                raise BrokerError(500, "write_failed", f"could not {stage} the file") from exc
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if not published:
                    _unlink_in(parent_fd, tmp)
            _fsync_dir_fd(parent_fd)
        return {"path": "/".join(components), "written_bytes": len(data)}


def _unlink_in(dir_fd: int, name: str) -> None:
    """Remove one name relative to an opened directory, never the cwd."""
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


def _read_bounded_fd(fd: int, limit: int) -> bytes:
    """Read at most ``limit`` bytes, enforcing the bound while reading."""
    chunks: list = []
    total = 0
    while True:
        chunk = os.read(fd, min(_READ_CHUNK, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise BrokerError(413, "file_too_large", "file exceeds the read limit")
        chunks.append(chunk)
    return b"".join(chunks)


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


def read_token(path: Optional[str]) -> Optional[str]:
    """Read the protected credential, or nothing when none is configured.

    The whole path is checked by the shared protected-path rule: root-owned
    every component, no symlink, no mutating access control list.  The file
    itself must be a single-link regular file that no identity outside root
    and its dedicated group can read.  A failure here is a refusal: the broker
    never drops the credential and carries on unauthenticated.
    """
    if path is None:
        return None
    _, named = _assert_protected_stat(path, directory=False)
    try:
        named_now = os.lstat(path)
        if (named_now.st_dev, named_now.st_ino) != (named.st_dev, named.st_ino):
            raise BrokerError(503, "token_unprotected", "the credential changed")
    except OSError as exc:
        raise BrokerError(503, "token_unreadable", "the credential is unavailable") from exc
    if not stat.S_ISREG(named.st_mode) or named.st_nlink != 1:
        raise BrokerError(503, "token_unprotected", "the credential is not a private regular file")
    if named.st_mode & 0o040 and named.st_gid != os.getegid():
        raise BrokerError(503, "token_unprotected", "the credential group is not the private service group")
    if named.st_mode & 0o007:
        raise BrokerError(503, "token_unprotected", "the credential is readable by other")
    try:
        fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC | _O_NONBLOCK)
    except OSError as exc:
        raise BrokerError(503, "token_unreadable", "the credential is unavailable") from exc
    with _Fd(fd) as file_fd:
        opened = os.fstat(file_fd)
        if (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_mode) != (named.st_dev, named.st_ino, named.st_uid, named.st_mode):
            raise BrokerError(503, "token_unprotected", "the credential changed while it was opened")
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise BrokerError(503, "token_unprotected", "the credential is not a private regular file")
        if opened.st_mode & 0o007:
            raise BrokerError(503, "token_unprotected", "the credential is readable by other")
        assert_no_mutating_acl(path, fd=file_fd)
        try:
            raw = _read_bounded_token(file_fd, MAX_TOKEN_FILE_BYTES)
            after = os.fstat(file_fd)
            if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise BrokerError(503, "token_unprotected", "the credential changed during reading")
        except OSError:
            raise BrokerError(503, "token_unreadable", "the credential is unavailable") from None
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise BrokerError(503, "token_unprotected", "the credential is not text") from None
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise BrokerError(503, "token_unprotected", "the credential is empty")
    return token


def _read_bounded_token(fd: int, limit: int) -> bytes:
    chunks: list = []
    total = 0
    while True:
        chunk = os.read(fd, min(4096, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise BrokerError(503, "token_unprotected", "the credential file is too large")
        chunks.append(chunk)
    return b"".join(chunks)


# --------------------------------------------------------------------------
# Framed HTTP/1.1 over the socket
# --------------------------------------------------------------------------


class _Framer:
    """Reads a request line, headers and a fixed-size body with byte caps.

    Every read is bounded twice: by the bytes still allowed and by the time
    still allowed.  Nothing here buffers a request without a limit, and no
    read waits for a single blocking operation past the connection deadline.
    """

    def __init__(self, connection: socket.socket, deadline: float) -> None:
        self._conn = connection
        self._deadline = deadline
        self._buffer = b""

    def _time_left(self) -> float:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise BrokerError(408, "deadline_exceeded", "the request took too long")
        self._conn.settimeout(left)
        return left

    def _feed(self) -> None:
        self._time_left()
        try:
            chunk = self._conn.recv(4096)
        except socket.timeout:
            raise BrokerError(408, "deadline_exceeded", "the request took too long") from None
        except (ConnectionResetError, BrokenPipeError, OSError):
            raise BrokerError(400, "connection_closed", "the request ended early") from None
        if not chunk:
            raise BrokerError(400, "connection_closed", "the request ended early")
        self._buffer += chunk

    def readline(self, cap: int) -> bytes:
        """One CRLF-terminated line of at most ``cap`` bytes, without the CRLF."""
        while True:
            newline = self._buffer.find(b"\n")
            if newline != -1:
                if newline == 0 or self._buffer[newline - 1 : newline] != b"\r":
                    raise BrokerError(400, "invalid_line", "lines must end with CRLF")
                if newline - 1 > cap:
                    raise BrokerError(431, "header_too_large", "line is too long")
                line = self._buffer[: newline - 1]
                self._buffer = self._buffer[newline + 1 :]
                return line
            if len(self._buffer) > cap + 2:
                raise BrokerError(431, "header_too_large", "line is too long")
            self._feed()

    def read_body(self, size: int, deadline: float) -> bytes:
        """Exactly ``size`` bytes, bounded by ``deadline`` and by ``size``."""
        self._deadline = deadline
        taken = min(size, len(self._buffer))
        chunks = [self._buffer[:taken]]
        self._buffer = self._buffer[taken:]
        remaining = size - taken
        while remaining > 0:
            self._time_left()
            try:
                chunk = self._conn.recv(min(remaining, _READ_CHUNK))
            except socket.timeout:
                raise BrokerError(408, "deadline_exceeded", "the request body took too long") from None
            except (ConnectionResetError, BrokenPipeError, OSError):
                raise BrokerError(400, "connection_closed", "the request body ended early") from None
            if not chunk:
                raise BrokerError(400, "truncated_body", "request body ended early")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


def _interrupt_socket(connection: socket.socket) -> None:
    # Keep the descriptor alive until its reader wakes; cross-thread close can
    # strand Darwin's timeout select even after shutdown made EOF available.
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _connect_service(connection: socket.socket, path: str, cancelled: threading.Event, deadline: float) -> None:
    if cancelled.is_set():
        raise BrokerError(400, "request_cancelled", "request cancelled")
    connection.setblocking(False)
    try:
        connection.connect(path)
    except (BlockingIOError, InterruptedError):
        while True:
            if cancelled.is_set():
                raise BrokerError(400, "request_cancelled", "request cancelled")
            left = deadline - time.monotonic()
            if left <= 0:
                raise BrokerError(504, "deadline_exceeded", "cloud connection took too long")
            _, ready, failed = select.select([], [connection], [connection], min(.05, left))
            if ready or failed:
                error = connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    raise OSError(error, "cloud connection failed")
                break


def _close_socket(connection: socket.socket) -> None:
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    connection.close()


class _ClientWatcher:
    def __init__(self, connection: socket.socket, cancel: Callable[[], None], deadline: float) -> None:
        self.stop = threading.Event()
        self.peer = connection.dup()
        self.peer.setblocking(False)

        def watch() -> None:
            try:
                while not self.stop.wait(.05):
                    if time.monotonic() >= deadline:
                        cancel()
                        return
                    try:
                        self.peer.send(b"", socket.MSG_DONTWAIT)
                    except BlockingIOError:
                        continue
                    except OSError:
                        cancel()
                        return
            finally:
                self.peer.close()
        self.thread = threading.Thread(target=watch, daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.peer.close()
            raise

    def close(self) -> None:
        self.stop.set()
        self.thread.join(.2)


def _rate_headers(headers: Any) -> dict:
    if type(headers) is not dict or len(headers) > MAX_RATE_HEADERS:
        raise BrokerError(502, "cloud_response_invalid", "cloud metadata exceeds the limit")
    result, total = {}, 0
    for name, value in headers.items():
        if type(name) is not str or not name.isascii() or len(name) > 128:
            raise BrokerError(502, "cloud_response_invalid", "cloud metadata refused")
        name = name.lower()
        allowed = name in RATE_HEADERS or re.fullmatch(r"anthropic-ratelimit-unified-[a-z0-9]+(?:-[a-z0-9]+)*", name)
        if not allowed or name in result or type(value) is not str or len(value) > 512 or any(ord(ch) < 32 or ord(ch) > 126 for ch in value):
            raise BrokerError(502, "cloud_response_invalid", "cloud metadata refused")
        total += len(name) + len(value) + 4
        if total > MAX_RATE_HEADER_BYTES:
            raise BrokerError(502, "cloud_response_invalid", "cloud metadata exceeds the limit")
        result[name] = value
    return result


def _rate_header_bytes(headers: Any) -> bytes:
    return b"".join((name + ": " + value + "\r\n").encode("ascii") for name, value in _rate_headers(headers).items())


class _CloudResponse:
    def __init__(self, connection: socket.socket, deadline: float, header_timeout: float) -> None:
        self.connection, self.deadline = connection, deadline
        self.reader = _Framer(connection, min(deadline, time.monotonic() + header_timeout))
        try:
            status_line = self.reader.readline(MAX_REQUEST_LINE_BYTES)
            parts = status_line.split(b" ", 2)
            if len(parts) != 3 or parts[0] != b"HTTP/1.1" or len(parts[1]) != 3 or not parts[1].isdigit():
                raise BrokerError(502, "cloud_response_invalid", "cloud service response refused")
            self.status = int(parts[1])
            headers, total = {}, len(status_line) + 2
            for _ in range(MAX_RATE_HEADERS + 4):
                line = self.reader.readline(MAX_HEADER_LINE_BYTES)
                total += len(line) + 2
                if total > MAX_REQUEST_HEADER_BYTES:
                    raise BrokerError(502, "cloud_response_invalid", "cloud service headers exceed the limit")
                if not line:
                    break
                name, separator, value = line.partition(b":")
                if not separator or not name or any(byte not in _HEADER_TOKEN for byte in name) or any(byte < 32 or byte > 126 for byte in value):
                    raise BrokerError(502, "cloud_response_invalid", "cloud service headers refused")
                name = name.lower()
                if name in headers:
                    raise BrokerError(502, "cloud_response_invalid", "duplicate cloud service header")
                headers[name] = value[1:] if value.startswith(b" ") else value
            else:
                raise BrokerError(502, "cloud_response_invalid", "cloud service headers exceed the limit")
            framing = {b"content-type", b"transfer-encoding", b"content-length", b"connection"}
            self.headers = _rate_headers({name.decode("ascii"): value.decode("ascii")
                for name, value in headers.items() if name not in framing})
            base = set(headers) & framing
            if self.status != 200:
                if base != {b"content-type", b"content-length", b"connection"} or headers[b"content-type"] != b"application/json" or headers[b"connection"].lower() != b"close":
                    raise BrokerError(502, "cloud_response_invalid", "cloud error framing refused")
                status = self.status if self.status in (400, 401, 403, 404, 408, 413, 429, 500, 502, 503, 504, 529) else 502
                raise BrokerError(status, "cloud_request_failed", "cloud request refused", headers=self.headers)
            if base != {b"content-type", b"transfer-encoding", b"connection"} or headers[b"transfer-encoding"].lower() != b"chunked" or headers[b"connection"].lower() != b"close":
                raise BrokerError(502, "cloud_response_invalid", "cloud service framing refused")
            self.content_type = headers[b"content-type"].decode("ascii")
            if self.content_type not in ("application/json", "text/event-stream"):
                raise BrokerError(502, "cloud_response_invalid", "cloud service content refused")
            self.reader._deadline = deadline
        except BrokerError as exc:
            if exc.code == "deadline_exceeded":
                raise BrokerError(504, exc.code, "cloud response took too long") from None
            raise

    def iter_bytes(self) -> Iterable[bytes]:
        total = 0
        try:
            while True:
                raw = self.reader.readline(32)
                if not raw or any(byte not in b"0123456789abcdefABCDEF" for byte in raw) or len(raw) > 8:
                    raise BrokerError(502, "cloud_response_invalid", "cloud chunk refused")
                size = int(raw, 16)
                if size == 0:
                    if self.reader.readline(2) != b"" or self.reader._buffer:
                        raise BrokerError(502, "cloud_response_invalid", "cloud terminator refused")
                    return
                total += size
                if total > MAX_CLOUD_RESPONSE_BYTES:
                    raise BrokerError(502, "cloud_response_invalid", "cloud response exceeds the byte limit")
                while size:
                    chunk = self.reader.read_body(min(size, _READ_CHUNK), self.deadline)
                    size -= len(chunk)
                    yield chunk
                if self.reader.read_body(2, self.deadline) != b"\r\n":
                    raise BrokerError(502, "cloud_response_invalid", "cloud chunk terminator refused")
        except BrokerError as exc:
            status = 504 if exc.code == "deadline_exceeded" else 502
            raise BrokerError(status, "deadline_exceeded" if status == 504 else "cloud_response_invalid", "cloud response refused") from None

    def close(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()


_DIGITS = frozenset(b"0123456789")
_HEADER_TOKEN = frozenset(
    b"!#$%&'*+-.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ^_`abcdefghijklmnopqrstuvwxyz|~"
)


def parse_content_length(values: Sequence[bytes]) -> int:
    """Exactly one ASCII decimal, positive, within the body limit.

    A negative, empty, signed, spaced, non-ASCII or duplicated length is a
    framing error: none of them is allowed to reach a read call.
    """
    if len(values) == 0:
        raise BrokerError(411, "length_required", "Content-Length is required")
    if len(values) > 1:
        raise BrokerError(400, "duplicate_length", "Content-Length appears more than once")
    value = values[0]
    if not value or any(byte not in _DIGITS for byte in value):
        raise BrokerError(400, "invalid_length", "Content-Length must be plain ASCII decimal digits")
    if value[0:1] == b"0":
        raise BrokerError(400, "invalid_length", "Content-Length must not have leading zeroes")
    if len(value) > len(str(MAX_REQUEST_BODY_BYTES)):
        raise BrokerError(413, "body_too_large", "request body exceeds the body limit")
    size = int(value.decode("ascii"))
    if size <= 0:
        raise BrokerError(400, "invalid_length", "Content-Length must be positive")
    if size > MAX_REQUEST_BODY_BYTES:
        raise BrokerError(413, "body_too_large", "request body must be at most the body limit")
    return size


# --------------------------------------------------------------------------
# Chat upstream
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------


class ZoneRequestHandler(socketserver.BaseRequestHandler):
    """Four POST endpoints, one zone, no generic dispatch.

    The request is framed by :class:`_Framer`, not by the standard library
    handler, so the byte caps hold while the bytes arrive and every refusal is
    a short JSON body that names only the rule it applied.
    """

    def handle(self) -> None:
        broker = self.server.broker
        connection = self.request
        try:
            connection.settimeout(HEADER_TIMEOUT_SECONDS)
        except OSError:
            return
        try:
            self._one_request(broker, connection)
        except BrokerError as error:
            if not getattr(self, "_response_started", False):
                self._reply(broker, connection, error.status, error.code, error.message, headers=error.headers)
            else:
                broker.log(f"{broker.config.zone} incomplete response {error.code}")
        except _Handled:
            return
        except (socket.timeout, TimeoutError, ConnectionResetError, BrokenPipeError, OSError):
            broker.log(f"{broker.config.zone} connection closed in {getattr(self, '_phase', 'read')}")
        except Exception:
            # Fail closed: name the stage, keep the number of details at zero.
            broker.log(f"internal error in {getattr(self, '_phase', 'request')}")
            try:
                if not getattr(self, "_response_started", False):
                    self._reply(broker, connection, 500, "internal_error", "request refused")
            except OSError:
                pass

    # -- framing --------------------------------------------------------
    def _one_request(self, broker: "Broker", connection: socket.socket) -> None:
        self._phase = "headers"
        started = time.monotonic()
        deadline = started + broker.request_deadline
        self._deadline = deadline
        framer = _Framer(connection, min(deadline, started + broker.header_timeout))
        request_line = framer.readline(MAX_REQUEST_LINE_BYTES)
        try:
            method, path, version = request_line.decode("ascii").split(" ")
        except (UnicodeDecodeError, ValueError):
            raise BrokerError(400, "invalid_request_line", "malformed request line") from None
        if version != "HTTP/1.1":
            raise BrokerError(400, "invalid_version", "only HTTP/1.1 is served")
        if path.startswith(("http://", "https://")) or path.startswith("//"):
            raise BrokerError(400, "absolute_uri", "absolute request URIs are not accepted")
        cloud_route = CLOUD_ROUTES.get((method, path))
        if ("?" in path or "#" in path) and cloud_route is None:
            raise BrokerError(400, "query_denied", "query strings are not accepted")
        if method != "POST" and cloud_route is None:
            raise BrokerError(405, "method_not_allowed", "only POST is served")
        if path not in ENDPOINTS and cloud_route is None:
            raise BrokerError(404, "unknown_endpoint", "no such endpoint")

        names: dict = {}
        total = len(request_line) + 2
        count = 0
        while True:
            line = framer.readline(MAX_HEADER_LINE_BYTES)
            total += len(line) + 2
            if total > MAX_REQUEST_HEADER_BYTES:
                raise BrokerError(431, "header_too_large", "header block is too large")
            if not line:
                break
            count += 1
            if count > MAX_REQUEST_HEADERS:
                raise BrokerError(431, "too_many_headers", f"at most {MAX_REQUEST_HEADERS} headers")
            if total > MAX_REQUEST_HEADER_BYTES:
                raise BrokerError(431, "header_too_large", "header block is too large")
            name, separator, value = line.partition(b":")
            if not separator or not name or any(byte not in _HEADER_TOKEN for byte in name):
                raise BrokerError(400, "invalid_header", "malformed header")
            key = name.decode("ascii").lower()
            if any(byte < 32 and byte != 9 or byte > 126 for byte in value):
                raise BrokerError(400, "invalid_header", "malformed header value")
            names.setdefault(key, []).append(value.strip(b" \t"))
        if count == 0:
            raise BrokerError(400, "invalid_header", "at least one header is required")

        if "upgrade" in names or any(token.strip().lower() == b"upgrade" for v in names.get("connection", []) for token in v.split(b",")):
            raise BrokerError(400, "upgrade_denied", "protocol upgrades are unavailable")
        # Framing: no transfer coding at all, then exactly one positive length.
        for key in ("transfer-encoding", "te"):
            if key in names:
                raise BrokerError(411, "chunked_denied", "chunked request bodies are not accepted")
        if "content-encoding" in names:
            raise BrokerError(415, "unsupported_encoding", "encoded request bodies are not accepted")
        if cloud_route is not None and method == "GET":
            lengths = names.get("content-length", [])
            if lengths not in ([], [b"0"]) or framer._buffer:
                raise BrokerError(400, "invalid_length", "catalog requests carry no body")
            self._cloud(broker, connection, cloud_route, method, None, deadline)
            return
        types = names.get("content-type", [b""])
        if len(types) != 1 or types[0].split(b";")[0].strip().lower() != b"application/json":
            raise BrokerError(415, "unsupported_media_type", "Content-Type must be application/json")
        size = parse_content_length(names.get("content-length", []))

        self._phase = "body"
        body = framer.read_body(size, min(deadline, time.monotonic() + broker.body_timeout))
        self._phase = "dispatch"
        document = strict_json_loads(body)
        if path != "/v1/chat/completions" and cloud_route is None:
            check_depth(document, where="request")
        if not isinstance(document, dict):
            raise BrokerError(400, "invalid_json", "request body must be a JSON object")
        if cloud_route is not None:
            self._cloud(broker, connection, cloud_route, method, document, deadline)
        elif path == "/v1/chat/completions":
            self._chat(broker, connection, document, deadline)
        else:
            self._files(broker, connection, path, document)

    # -- endpoints ----------------------------------------------------
    def _files(self, broker: "Broker", connection: socket.socket, path: str, document: dict) -> None:
        if broker.config.files_root is None:
            raise BrokerError(403, "files_disabled", "this zone has no approved file root")
        access = broker.root
        if path == "/v1/files/read":
            fields = expect_fields(document, {"path"}, "request")
            self._reply_json(broker, connection, 200, access.read(split_relpath(field(fields, "path", "request"))))
        elif path == "/v1/files/list":
            fields = expect_fields(document, {"path"}, "request")
            self._reply_json(
                broker, connection, 200, access.list(split_relpath(field(fields, "path", "request"), allow_empty=True))
            )
        else:  # /v1/files/write
            fields = expect_fields(document, {"path", "content_base64"}, "request")
            encoded = field(fields, "content_base64", "request")
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3):
                raise BrokerError(413, "body_too_large", "content_base64 is too large")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                raise BrokerError(400, "invalid_base64", "content_base64 is not valid base64") from None
            self._reply_json(broker, connection, 200, access.write(split_relpath(field(fields, "path", "request")), data))

    def _cloud(self, broker: "Broker", connection: socket.socket, route: Tuple[str, str], method: str, document: Any, deadline: float) -> None:
        provider, child_path = route
        expected = broker.services.identity(provider)
        if broker.config.zone != "internal" or expected is None:
            raise BrokerError(403, "cloud_disabled", "this fixed cloud route is disabled")
        self._phase = "cloud service"
        child = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        response = None
        cancelled = threading.Event()
        def cancel() -> None:
            cancelled.set()
            _interrupt_socket(child)
        watcher = _ClientWatcher(connection, cancel, deadline)
        try:
            path = _cloud_socket(provider)
            _check_service_socket(path, expected)
            left = deadline - time.monotonic()
            if left <= 0:
                raise BrokerError(504, "deadline_exceeded", "the request took too long")
            _connect_service(child, path, cancelled, deadline)
            if _peer_identity(child) != (expected.uid, expected.gid):
                raise BrokerError(502, "cloud_peer_denied", "cloud service identity refused")
            body = b"" if document is None else json.dumps(document, separators=(",", ":"), allow_nan=False).encode("utf-8")
            headers = (method + " " + child_path + " HTTP/1.1\r\nHost: cloud.invalid\r\nConnection: close\r\n")
            if document is not None:
                headers += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(body)
            if cancelled.is_set():
                raise BrokerError(400, "request_cancelled", "request cancelled")
            self._model_send(child, headers.encode("ascii") + b"\r\n" + body, deadline)
            response = _CloudResponse(child, deadline, broker.header_timeout)
            self._relay_model(broker, connection, response, deadline, limit=MAX_CLOUD_RESPONSE_BYTES, watch_client=False)
        except BrokerError as exc:
            if time.monotonic() >= deadline and exc.status in (400, 408, 502, 504):
                raise BrokerError(504, "deadline_exceeded", "the request took too long") from None
            raise
        except PolicyError:
            raise BrokerError(502, "cloud_unavailable", "fixed cloud service unavailable") from None
        except socket.timeout:
            raise BrokerError(504, "deadline_exceeded", "the request took too long") from None
        except OSError:
            if time.monotonic() >= deadline:
                raise BrokerError(504, "deadline_exceeded", "the request took too long") from None
            raise BrokerError(502, "cloud_unavailable", "fixed cloud service unavailable") from None
        finally:
            watcher.close()
            if response is not None:
                response.close()
            else:
                child.close()
        raise _Handled()

    def _chat(self, broker: "Broker", connection: socket.socket, document: dict, deadline: float) -> None:
        model = broker.config.model
        if model is None:
            raise BrokerError(403, "chat_disabled", "this zone has no configured model")
        self._phase = "credential"
        try:
            token = read_token(model.token_file)
        except PolicyError:
            raise BrokerError(503, "token_unavailable", "the credential is unavailable") from None
        remaining = min(broker.upstream_timeout, deadline - time.monotonic())
        if remaining <= 0:
            raise BrokerError(408, "deadline_exceeded", "the request took too long")
        self._phase = "upstream"
        try:
            gateway = ModelGateway(model.origin, model.name, token,
                deadline_seconds=remaining, connect_timeout=remaining,
                max_response_bytes=MAX_RESPONSE_BODY_BYTES)
            with gateway.request(document) as response:
                self._relay_model(broker, connection, response, deadline)
        except GatewayError as exc:
            # Gateway details can contain caller or upstream values; expose only its stable code.
            raise BrokerError(exc.status, exc.code, "model request refused") from None
        raise _Handled()

    def _relay_model(self, broker: "Broker", connection: socket.socket, response: Any, deadline: float,
                     *, limit: int = MAX_RESPONSE_BODY_BYTES, watch_client: bool = True) -> None:
        watcher = _ClientWatcher(connection, response.close, deadline) if watch_client else None
        try:
            iterator = iter(response.iter_bytes())
            first = next(iterator)
            content_type = response.content_type.split(";", 1)[0].strip().lower()
            if content_type not in ("application/json", "text/event-stream"):
                raise BrokerError(502, "upstream_response", "model response refused")
            head = ("HTTP/1.1 200 OK\r\nContent-Type: " + content_type
                + "\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n").encode("ascii")
            head += _rate_header_bytes(getattr(response, "headers", {})) + b"\r\n"
            self._response_started = True
            self._model_send(connection, head, deadline)
            total = 0
            for chunk in _prepend(first, iterator):
                total += len(chunk)
                if total > limit:
                    raise BrokerError(502, "upstream_response", "model response exceeds the byte limit")
                self._model_send(connection, ("%x\r\n" % len(chunk)).encode("ascii") + chunk + b"\r\n", deadline)
            # Only a fully validated upstream may terminate the HTTP response successfully.
            self._model_send(connection, b"0\r\n\r\n", deadline)
            broker.log(f"{broker.config.zone} 200")
        except StopIteration:
            raise BrokerError(502, "upstream_response", "model returned no response") from None
        finally:
            if watcher is not None:
                watcher.close()
            response.close()

    def _model_send(self, connection: socket.socket, data: bytes, deadline: float) -> None:
        left = deadline - time.monotonic()
        if left <= 0:
            raise BrokerError(504, "deadline_exceeded", "the request took too long")
        connection.settimeout(min(RESPONSE_TIMEOUT_SECONDS, left))
        connection.sendall(data)

    # -- answers ------------------------------------------------------
    def _reply_json(self, broker: "Broker", connection: socket.socket, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._send(broker, connection, status, body)
        broker.log(f"{broker.config.zone} {status}")
        raise _Handled()

    def _reply(self, broker: "Broker", connection: socket.socket, status: int, code: str, message: str, *, headers: Optional[dict] = None) -> None:
        body = json.dumps({"error": {"code": code, "message": message}}, separators=(",", ":")).encode("utf-8")
        self._send(broker, connection, status, body, headers=headers)
        broker.log(f"{broker.config.zone} {status} {code}")

    def _send(self, broker: "Broker", connection: socket.socket, status: int, body: bytes, *, headers: Optional[dict] = None) -> None:
        """One short JSON answer: the rule, never the path behind it."""
        self._phase = "response"
        head = (
            f"HTTP/1.1 {status} {_reason(status)}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
        ).encode("ascii")
        head += _rate_header_bytes({} if headers is None else headers) + b"\r\n"
        try:
            connection.settimeout(max(0.001, min(RESPONSE_TIMEOUT_SECONDS, getattr(self, "_deadline", time.monotonic() + 1) - time.monotonic())))
            connection.sendall(head + body)
        except OSError:
            broker.log(f"{broker.config.zone} response undeliverable")


def _prepend(first: bytes, rest: Iterable[bytes]) -> Iterable[bytes]:
    yield first
    yield from rest


def _reason(status: int) -> str:
    return {
        200: "OK",
        400: "Bad Request",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        408: "Request Timeout",
        411: "Length Required",
        413: "Payload Too Large",
        415: "Unsupported Media Type",
        429: "Too Many Requests",
        431: "Request Header Fields Too Large",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout",
        507: "Insufficient Storage",
    }.get(status, "Bad Request")


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------


class _UnixHTTPServer(socketserver.ThreadingUnixStreamServer):
    """One Unix socket, a fixed number of active connections, 0600 or 0660.

    ``request_queue_size`` is the kernel backlog, not a thread cap: the cap is
    a semaphore taken in the accept loop, so a guest that opens connections and
    says nothing cannot buy threads.
    """

    daemon_threads = True
    request_queue_size = 16

    def __init__(self, socket_path: str, handler: type, *, socket_group: Optional[int]) -> None:
        self.socket_path = socket_path
        self.socket_group = socket_group
        self.owned_socket = False
        self._identity: Optional[Tuple[int, int]] = None
        self._slots = threading.BoundedSemaphore(MAX_ACTIVE_CONNECTIONS)
        # Sockets are created with 0777 & ~umask; narrow the mask before bind
        # so the path is never world-accessible, then pin and verify it.
        umask = os.umask(0o177 if socket_group is None else 0o077)
        try:
            super().__init__(socket_path, handler)
        finally:
            os.umask(umask)
        try:
            self._harden()
        except BaseException:
            self._remove_own_socket()
            super().server_close()
            raise
        self.owned_socket = True

    # -- permissions --------------------------------------------------
    def _harden(self) -> None:
        """Set and path-identity-verify the mode (and group) of our socket."""
        before = os.lstat(self.socket_path)
        if not stat.S_ISSOCK(before.st_mode):
            raise OSError(errno.ENOTSOCK, "bound path is not a socket", self.socket_path)
        self._identity = (before.st_dev, before.st_ino)
        wanted = 0o660 if self.socket_group is not None else 0o600
        if self.socket_group is not None:
            _set_group(self.socket_path, self.socket_group, self._identity)
        _set_mode(self.socket_path, self.socket.fileno(), wanted, self._identity)
        after = os.lstat(self.socket_path)
        if (after.st_dev, after.st_ino) != self._identity:
            raise OSError(errno.EXDEV, "socket path changed under us", self.socket_path)
        if after.st_uid != os.geteuid():
            raise OSError(errno.EPERM, "socket has the wrong owner", self.socket_path)
        if stat.S_IMODE(after.st_mode) != wanted:
            raise OSError(errno.EPERM, "socket permissions are not the intended ones", self.socket_path)
        if stat.S_IMODE(after.st_mode) & 0o007:
            raise OSError(errno.EPERM, "socket is accessible to other", self.socket_path)
        if self.socket_group is not None and after.st_gid != self.socket_group:
            raise OSError(errno.EPERM, "socket group is not the launch group", self.socket_path)

    def _remove_own_socket(self) -> None:
        """Unlink only the socket this process just bound, verified by identity."""
        if self._identity is None:
            return
        try:
            info = os.lstat(self.socket_path)
        except OSError:
            return
        if (info.st_dev, info.st_ino) == self._identity and stat.S_ISSOCK(info.st_mode):
            try:
                os.unlink(self.socket_path)
            except FileNotFoundError:
                pass

    # -- connection budget ----------------------------------------------
    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            self._refuse_overload(request)
            self.shutdown_request(request)
            self.broker.log("connection refused over the active limit")
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def _refuse_overload(self, request: Any) -> None:
        body = json.dumps(
            {"error": {"code": "overloaded", "message": "too many active requests"}}, separators=(",", ":")
        ).encode("utf-8")
        head = (
            f"HTTP/1.1 503 {_reason(503)}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        try:
            request.settimeout(REFUSAL_WRITE_TIMEOUT_SECONDS)
            request.sendall(head + body)
        except OSError:
            pass

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (socket.timeout, TimeoutError, ConnectionResetError, BrokenPipeError)):
            return
        self.broker.log(f"handler error: {type(exc).__name__}")

    # -- lifecycle ----------------------------------------------------
    def still_ours(self) -> bool:
        if not self.owned_socket or self._identity is None:
            return False
        try:
            info = os.lstat(self.socket_path)
        except OSError:
            return False
        return (info.st_dev, info.st_ino) == self._identity

    def server_close(self) -> None:
        ours = self.still_ours()
        super().server_close()
        if ours:
            self._remove_own_socket()


def _set_mode(path: str, fileno: int, mode: int, identity: Tuple[int, int]) -> None:
    """Set the mode of the socket we just bound and prove it is that path.

    ``fchmod`` on a bound socket is refused on macOS/BSD, so the fallback sets
    it on the path with ``lchmod`` -- which is only safe because the identity
    of the path is checked before and after against the descriptor we hold.
    """
    try:
        os.fchmod(fileno, mode)
    except OSError:
        named = os.lstat(path)
        if (named.st_dev, named.st_ino) != identity or not stat.S_ISSOCK(named.st_mode):
            raise OSError(errno.EXDEV, "socket path changed before its mode could be set", path)
        os.chmod(path, mode, follow_symlinks=False)


def _set_group(path: str, group: int, identity: Tuple[int, int]) -> None:
    """Give the socket to the launch group, on the verified path only."""
    named = os.lstat(path)
    if (named.st_dev, named.st_ino) != identity or not stat.S_ISSOCK(named.st_mode):
        raise OSError(errno.EXDEV, "socket path changed before its group could be set", path)
    os.chown(path, -1, group, follow_symlinks=False)


def parse_socket_group(value: Any) -> int:
    """The launch group is a numeric group id, nothing else."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise PolicyError("--socket-group: must be a numeric group id")
    text = str(value)
    if not text or any(byte not in _DIGITS for byte in text.encode("utf-8")):
        raise PolicyError("--socket-group: must be a numeric group id, not a name")
    group = int(text)
    if not 0 <= group <= 2147483647:
        raise PolicyError("--socket-group: group id out of range")
    return group


def verify_socket_group(group: int) -> None:
    """This process must actually hold the group it is asked to grant."""
    if group == os.getegid():
        return
    try:
        supplementary = set(os.getgroups())
    except OSError:
        supplementary = set()
    if group not in supplementary:
        raise PolicyError("--socket-group: this process does not hold that group")


def preflight_socket(socket_path: str, *, socket_group: Optional[int] = None) -> None:
    """Check the socket's own path chain; refuse to touch what we did not make.

    With a launch group the parent directory is the fixed broker-owned
    ``0710`` directory of that group: the group can reach the socket, nobody
    else can list or add to the directory.
    """
    if not isinstance(socket_path, str) or not socket_path.startswith("/"):
        raise PolicyError("--socket: must be an absolute path")
    if os.path.normpath(socket_path) != socket_path:
        raise PolicyError("--socket: must be a normalized path")
    parent = os.path.dirname(socket_path) or "/"
    try:
        parent_fd = pin_directory(parent, where="--socket parent", protected_ancestors=True)
    except PolicyError as exc:
        raise PolicyError(f"--socket parent: {exc}") from exc
    try:
        info = os.fstat(parent_fd)
    finally:
        os.close(parent_fd)
    if socket_group is None:
        if info.st_uid not in (0, os.geteuid()):
            raise PolicyError("--socket parent is not owned by root or the service identity")
        if info.st_mode & 0o022:
            raise PolicyError("--socket parent is writable by group or other")
        if stat.S_IMODE(info.st_mode) & 0o007:
            raise PolicyError("--socket parent is accessible to other")
    else:
        if info.st_uid != os.geteuid():
            raise PolicyError("--socket parent must be owned by the broker identity")
        if info.st_gid != socket_group:
            raise PolicyError("--socket parent must belong to the launch group")
        if stat.S_IMODE(info.st_mode) != 0o710:
            raise PolicyError("--socket parent must be mode 0710")
    try:
        os.lstat(socket_path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PolicyError(f"--socket: path is not inspectable ({exc.strerror})") from exc
    raise PolicyError("--socket: path already exists, refusing to unlink it")


def _stderr_log(line: str) -> None:
    sys.stderr.write(f"[ai-zones] {line}\n")


def _interpreter_is_private() -> Optional[str]:
    """Name why this interpreter is not a fixed production binary, if it is not."""
    executable = sys.executable
    try:
        resolved = os.path.realpath(executable)
        info = os.stat(resolved)
    except OSError:
        return "the interpreter cannot be inspected"
    if info.st_mode & 0o022:
        return "the interpreter binary is writable by group or other"
    return None


class _Shutdown(Exception):
    """Raised in the serving thread when the operator asks the broker to stop."""


class Broker:
    """A running broker for one fixed zone.

    ``policy`` is the immutable :class:`~ai_zones.policy.Policy` produced by
    ``policy.validate_policy`` or ``policy.load_policy``; raw dictionaries are
    refused.  ``socket_group`` is a launch decision that only changes the
    socket's group and mode: it selects no zone, no policy and no capability.
    """

    def __init__(
        self,
        *,
        policy: Any,
        zone: str,
        socket_path: str,
        socket_group: Optional[int] = None,
        header_timeout: float = HEADER_TIMEOUT_SECONDS,
        body_timeout: float = BODY_TIMEOUT_SECONDS,
        upstream_timeout: float = UPSTREAM_TIMEOUT_SECONDS,
        request_deadline: float = REQUEST_DEADLINE_SECONDS,
        max_connections: int = MAX_ACTIVE_CONNECTIONS,
        services: Optional[Services] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.config = project_zone(policy, zone)
        if services is not None and not isinstance(services, Services):
            raise PolicyError("services must be the fixed immutable service view")
        self.services = Services() if zone == "confidential" else services or Services()
        self.socket_path = socket_path
        if self.config.files_root is not None:
            excluded = [socket_path]
            excluded.extend("/Library/AgentSandbox/" + p for p in ("core", "runtime", "config", "state", "run", "releases", "management"))
            excluded.extend("/opt/agent-sandbox/" + p for p in ("core", "runtime", "config", "state", "run", "releases", "management"))
            if self.config.model is not None and self.config.model.token_file is not None:
                excluded.append(self.config.model.token_file)
            _exclude_control_overlap(self.config.files_root, excluded)
        self.socket_group = None if socket_group is None else parse_socket_group(socket_group)
        self.header_timeout = _positive_timeout(header_timeout, "header_timeout")
        self.body_timeout = _positive_timeout(body_timeout, "body_timeout")
        self.upstream_timeout = _positive_timeout(upstream_timeout, "upstream_timeout")
        self.request_deadline = _positive_timeout(request_deadline, "request_deadline")
        if type(max_connections) is not int or not MIN_ACTIVE_CONNECTIONS <= max_connections <= MAX_ACTIVE_CONNECTIONS_LIMIT:
            raise PolicyError(f"max_connections: must be within {MIN_ACTIVE_CONNECTIONS}..{MAX_ACTIVE_CONNECTIONS_LIMIT}")
        self.max_connections = max_connections
        self.log = log if log is not None else _stderr_log
        self.root: Optional[RootAccess] = None
        if self.config.files_root is not None:
            try:
                self.root = RootAccess(self.config.files_root, writable=self.config.files_writable)
            except PolicyError:
                raise
        self._server: Optional[_UnixHTTPServer] = None
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------
    def start(self) -> "_UnixHTTPServer":
        with self._lock:
            if self._server is not None:
                return self._server
            if self.socket_group is not None:
                verify_socket_group(self.socket_group)
            preflight_socket(self.socket_path, socket_group=self.socket_group)
            handler = type("BoundZoneRequestHandler", (ZoneRequestHandler,), {"broker": self})
            self._server = _UnixHTTPServer(self.socket_path, handler, socket_group=self.socket_group)
            # One bound reference, on the server, used by every handler.
            self._server.broker = self
            self._server._slots = threading.BoundedSemaphore(self.max_connections)
            return self._server

    def run(self) -> int:
        warning = _interpreter_is_private()
        if warning is not None:
            self.log(f"warning: {warning}")
        server = self.start()
        if os.geteuid() == 0:
            self.log("running as root: a dedicated unprivileged service identity is expected")
        self.log(
            f"serving (protocol {PROTOCOL}, zone={self.config.zone}, "
            f"files={'rw' if self.config.files_writable else 'ro' if self.config.files_root else 'off'}, "
            f"model={'on' if self.config.model else 'off'}, "
            f"socket={'group' if self.socket_group is not None else 'owner-only'})"
        )
        installed = self._install_stop_handlers()
        try:
            server.serve_forever(poll_interval=0.2)
        except (_Shutdown, KeyboardInterrupt):
            self.log("stop requested")
        finally:
            self._restore_stop_handlers(installed)
            self.stop()
        return 0

    def _install_stop_handlers(self) -> dict:
        """Make SIGTERM and SIGINT end the loop, so the socket is closed cleanly."""
        installed: dict = {}

        def handler(signum: int, frame: Any) -> None:
            raise _Shutdown()

        for number in (signal.SIGINT, signal.SIGTERM):
            try:
                installed[number] = signal.signal(number, handler)
            except (ValueError, OSError):
                pass  # not the main thread; the caller owns shutdown
        return installed

    def _restore_stop_handlers(self, installed: dict) -> None:
        for number, previous in installed.items():
            try:
                signal.signal(number, previous)
            except (ValueError, OSError):
                pass

    def stop(self) -> None:
        with self._lock:
            server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self.root is not None:
            self.root.close()
            self.root = None

    def serve_in_thread(self) -> threading.Thread:
        server = self.start()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        return thread


def _exclude_control_overlap(root: str, controls: Iterable[str]) -> None:
    for control in controls:
        common = os.path.commonpath((root, control))
        if common in (root, control):
            raise PolicyError("approved data root overlaps protected control or runtime paths")


def _positive_timeout(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < float(value) <= 3600.0):
        raise PolicyError(f"{where}: must be a positive number of seconds up to 3600")
    return float(value)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_zones.broker",
        description=(
            "Restricted two-zone file and chat broker. The zone is fixed here and cannot be chosen by a guest. "
            "--socket-group is a numeric group id decided by root at launch; it grants reach to the socket and "
            "selects no policy, zone or capability."
        ),
    )
    parser.add_argument("--policy", required=True, help="absolute path to the root-owned policy document")
    parser.add_argument("--zone", required=True, choices=ZONES, help="zone this process serves")
    parser.add_argument("--socket", required=True, help="absolute path of the Unix socket to create")
    parser.add_argument(
        "--socket-group",
        default=None,
        help="numeric group id allowed to reach the socket (mode 0660); without it the socket is 0600",
    )
    return parser


def main(argv: Optional[list] = None, *, broker_factory: Optional[Callable[..., Broker]] = None) -> int:
    args = build_parser().parse_args(argv)
    if os.geteuid() == 0:
        sys.stderr.write("broker: launch requires a dedicated unprivileged service identity\n")
        return 2
    if not args.policy.startswith("/"):
        sys.stderr.write("broker: --policy must be an absolute path\n")
        return 2
    try:
        group = None if args.socket_group is None else parse_socket_group(args.socket_group)
        policy = load_policy_file(args.policy)
        services = load_services(args.zone, socket_group=group)
        selected = policy.zones[args.zone]
        if selected.files is not None:
            _exclude_control_overlap(selected.files.root, [args.policy])
    except PolicyError as exc:
        sys.stderr.write(f"broker: {exc}\n")
        return 2
    try:
        broker = (broker_factory or Broker)(
            policy=policy, zone=args.zone, socket_path=args.socket, socket_group=group, services=services
        )
    except (PolicyError, OSError) as exc:
        sys.stderr.write(f"broker: {exc}\n")
        return 2
    return broker.run()


if __name__ == "__main__":
    sys.exit(main())
