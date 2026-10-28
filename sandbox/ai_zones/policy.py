"""Fail-closed policy model for the two-zone AI sandbox.

Zones are fixed: ``internal`` and ``confidential``.  A policy is only an
authority when it is loaded from an absolute, caller-independent path that
passes :func:`assert_protected`; a dictionary handed to
:func:`validate_policy` is merely validated input, never authority.  There
is deliberately no environment or current-directory search, and no API here
writes a policy: the policy is immutable outside the release process.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import stat as _stat
import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit

POLICY_SCHEMA_VERSION = 1
ZONE_NAMES = ("confidential", "internal")

CPUS_MIN, CPUS_MAX = 1, 256
MEMORY_MIB_MIN, MEMORY_MIB_MAX = 256, 1_048_576
DISK_GIB_MIN, DISK_GIB_MAX = 1, 16_384
NAME_MAX_LEN = 256
POLICY_MAX_BYTES = 64 * 1024
_READ_CHUNK = 16 * 1024

_TOP_KEYS = frozenset({"version", "zones"})
_ZONE_KEYS = frozenset({"cpus", "memory_mib", "disk_gib", "files", "model"})
_FILES_KEYS = frozenset({"root", "writable"})
_MODEL_KEYS = frozenset({"url", "name", "token_file"})
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class PolicyError(Exception):
    """Any policy validation or protection failure. Fail closed."""


def _exact_keys(obj: Any, allowed: frozenset, where: str) -> Mapping[str, Any]:
    if not isinstance(obj, dict):
        raise PolicyError(f"{where}: expected an object, got {type(obj).__name__}")
    if any(not isinstance(key, str) for key in obj):
        raise PolicyError(f"{where}: expected string keys")
    keys = frozenset(obj)
    if keys != allowed:
        missing = sorted(allowed - keys)
        extra = sorted(keys - allowed)
        raise PolicyError(
            f"{where}: keys must be exactly {sorted(allowed)}"
            + (f"; missing {missing}" if missing else "")
            + (f"; unknown {extra}" if extra else "")
        )
    return obj


def _require_int(value: Any, lo: int, hi: int, where: str) -> int:
    if type(value) is not int:  # bool is deliberately rejected here
        raise PolicyError(f"{where}: expected int, got {type(value).__name__}")
    if not lo <= value <= hi:
        raise PolicyError(f"{where}: {value} outside [{lo}, {hi}]")
    return value


def _require_bool(value: Any, where: str) -> bool:
    if type(value) is not bool:  # int 0/1 is deliberately rejected here
        raise PolicyError(f"{where}: expected bool, got {type(value).__name__}")
    return value


def _require_abs_path(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise PolicyError(f"{where}: expected an absolute path, got {value!r}")
    if value.startswith("//") or os.path.normpath(value) != value:
        raise PolicyError(f"{where}: path must be normalized: {value!r}")
    if _CONTROL_CHARS.search(value):
        raise PolicyError(f"{where}: path contains control characters")
    return value


def _parse_origin(url: Any, where: str) -> tuple[str, str]:
    """Return (scheme, raw_host) for a fixed http(s) origin, else fail.

    The host is kept exactly as written; no DNS is ever consulted, so a
    confidential origin stays a literal string comparison.
    """
    if not isinstance(url, str):
        raise PolicyError(f"{where}: expected a string URL")
    if _CONTROL_CHARS.search(url) or any(c.isspace() for c in url):
        raise PolicyError(f"{where}: URL contains whitespace or control characters")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise PolicyError(f"{where}: malformed URL: {exc}")
    if parts.scheme not in ("http", "https"):
        raise PolicyError(f"{where}: scheme must be http or https")
    if parts.username is not None or parts.password is not None:
        raise PolicyError(f"{where}: URL must not contain credentials")
    if parts.query or parts.fragment:
        raise PolicyError(f"{where}: URL must carry no query or fragment")
    if parts.path not in ("", "/"):
        raise PolicyError(f"{where}: URL must be a bare origin, not a path")
    netloc = parts.netloc
    if netloc.startswith("["):
        end = netloc.find("]")
        if end == -1:
            raise PolicyError(f"{where}: malformed IPv6 origin")
        raw_host = netloc[: end + 1]
        rest = netloc[end + 1 :]
        if rest and not re.fullmatch(r":\d+", rest):
            raise PolicyError(f"{where}: malformed port in origin")
    elif ":" in netloc:
        raw_host, _, port = netloc.rpartition(":")
        if not port.isdigit():
            raise PolicyError(f"{where}: malformed port in origin")
    else:
        raw_host = netloc
    if not raw_host or "*" in raw_host:
        raise PolicyError(f"{where}: origin host must be a fixed host, not a wildcard")
    try:
        port = parts.port
    except ValueError as exc:
        raise PolicyError(f"{where}: malformed port in origin: {exc}")
    if port is not None and not (1 <= port <= 65535):
        raise PolicyError(f"{where}: port {port} outside [1, 65535]")
    return parts.scheme, raw_host


def _validate_model(model: Any, zone: str, where: str) -> "ModelEndpoint | None":
    if model is None:
        return None
    _exact_keys(model, _MODEL_KEYS, where)
    _, raw_host = _parse_origin(model["url"], f"{where}.url")
    if zone == "confidential":
        # Confidential traffic must terminate locally.  The host is checked
        # as a literal loopback string; no DNS lookup is ever performed.
        if raw_host not in ("127.0.0.1", "[::1]"):
            raise PolicyError(
                f"{where}.url: confidential model must be literal loopback "
                "(127.0.0.1 or [::1]), no DNS"
            )
    name = model["name"]
    if not isinstance(name, str) or not (1 <= len(name) <= NAME_MAX_LEN):
        raise PolicyError(f"{where}.name: expected a fixed non-empty string")
    if _CONTROL_CHARS.search(name):
        raise PolicyError(f"{where}.name: contains control characters")
    token_file = model["token_file"]
    if token_file is not None:
        _require_abs_path(token_file, f"{where}.token_file")
    return ModelEndpoint(url=model["url"], name=name, token_file=token_file)


def _validate_files(files: Any, where: str) -> "FileRoot | None":
    if files is None:
        return None
    _exact_keys(files, _FILES_KEYS, where)
    return FileRoot(
        root=_require_abs_path(files["root"], f"{where}.root"),
        writable=_require_bool(files["writable"], f"{where}.writable"),
    )


@dataclass(frozen=True)
class FileRoot:
    root: str
    writable: bool


@dataclass(frozen=True)
class ModelEndpoint:
    url: str
    name: str
    token_file: "str | None"


@dataclass(frozen=True)
class Zone:
    cpus: int
    memory_mib: int
    disk_gib: int
    files: "FileRoot | None"
    model: "ModelEndpoint | None"


@dataclass(frozen=True)
class Policy:
    version: int
    zones: Mapping[str, Zone]


def validate_policy(data: Any) -> Policy:
    """Validate a decoded policy document. The result is immutable.

    The input dict itself is never treated as authority; only
    :func:`load_policy` from a protected path is.
    """
    top = _exact_keys(data, _TOP_KEYS, "policy")
    if top["version"] != POLICY_SCHEMA_VERSION or type(top["version"]) is not int:
        raise PolicyError(
            f"policy.version: must be exactly {POLICY_SCHEMA_VERSION}"
        )
    zones_obj = _exact_keys(top["zones"], frozenset(ZONE_NAMES), "policy.zones")
    zones = {}
    for zone_name in ZONE_NAMES:
        where = f"policy.zones.{zone_name}"
        zone = _exact_keys(zones_obj[zone_name], _ZONE_KEYS, where)
        zones[zone_name] = Zone(
            cpus=_require_int(zone["cpus"], CPUS_MIN, CPUS_MAX, f"{where}.cpus"),
            memory_mib=_require_int(
                zone["memory_mib"], MEMORY_MIB_MIN, MEMORY_MIB_MAX,
                f"{where}.memory_mib",
            ),
            disk_gib=_require_int(
                zone["disk_gib"], DISK_GIB_MIN, DISK_GIB_MAX, f"{where}.disk_gib"
            ),
            files=_validate_files(zone["files"], f"{where}.files"),
            model=_validate_model(zone["model"], zone_name, f"{where}.model"),
        )
    return Policy(version=POLICY_SCHEMA_VERSION, zones=MappingProxyType(zones))


# Values and signatures are from Darwin sys/acl.h, not ls permission names.
_ACL_MUTATING = sum(1 << bit for bit in (2, 4, 5, 6, 8, 10, 12, 13))
_ACL_KNOWN = _ACL_MUTATING | sum(1 << bit for bit in (1, 3, 7, 9, 11, 20))
_DARWIN_ACL = None


def _darwin_acl_library():
    global _DARWIN_ACL
    if _DARWIN_ACL is None:
        try:
            lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            signatures = {
                "acl_get_link_np": ([ctypes.c_char_p, ctypes.c_int], ctypes.c_void_p),
                "acl_get_fd_np": ([ctypes.c_int, ctypes.c_int], ctypes.c_void_p),
                "acl_valid": ([ctypes.c_void_p], ctypes.c_int),
                "acl_get_entry": ([ctypes.c_void_p, ctypes.c_int,
                                   ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int),
                "acl_get_tag_type": ([ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
                "acl_get_permset_mask_np": ([ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_uint64)], ctypes.c_int),
                "acl_free": ([ctypes.c_void_p], ctypes.c_int),
            }
            for name, (args, result) in signatures.items():
                function = getattr(lib, name)
                function.argtypes, function.restype = args, result
            _DARWIN_ACL = lib
        except (AttributeError, OSError) as exc:
            raise PolicyError("native ACL inspection unavailable") from exc
    return _DARWIN_ACL


def assert_no_mutating_acl(path: str, *, fd: "int | None" = None) -> None:
    """Reject mutating allow entries, including inherited grants.

    Darwin inspection uses libSystem directly so protected configuration can
    be loaded inside a sandbox that denies subprocess execution. On Linux,
    the mode's group bits are the POSIX ACL mask and bound every named grant.
    """
    if sys.platform != "darwin":
        return
    try:
        before = os.fstat(fd) if fd is not None else os.lstat(path)
        lib = _darwin_acl_library()
        ctypes.set_errno(0)
        acl = (lib.acl_get_fd_np(fd, 0x100) if fd is not None else
               lib.acl_get_link_np(os.fsencode(path), 0x100))
        if not acl:
            # Darwin reports an absent ACL as ENOENT even for an existing file.
            if ctypes.get_errno() != errno.ENOENT:
                raise PolicyError("cannot read extended ACL")
            return
        try:
            if lib.acl_valid(acl) != 0:
                raise PolicyError("invalid extended ACL")
            entry = ctypes.c_void_p()
            for index in range(129):
                ctypes.set_errno(0)
                result = lib.acl_get_entry(acl, 0 if index == 0 else -1,
                                           ctypes.byref(entry))
                if result != 0:
                    # Darwin uses -1/EINVAL for the end of a validated ACL.
                    if result == -1 and ctypes.get_errno() == errno.EINVAL:
                        break
                    raise PolicyError("cannot enumerate extended ACL")
                if index == 128:
                    raise PolicyError("extended ACL exceeds entry limit")
                tag, mask = ctypes.c_int(), ctypes.c_uint64()
                if (lib.acl_get_tag_type(entry, ctypes.byref(tag)) != 0 or
                        lib.acl_get_permset_mask_np(entry, ctypes.byref(mask)) != 0):
                    raise PolicyError("cannot inspect extended ACL entry")
                if tag.value not in (1, 2) or mask.value & ~_ACL_KNOWN:
                    raise PolicyError("unknown extended ACL entry")
                if tag.value == 1 and mask.value & _ACL_MUTATING:
                    raise PolicyError("extended ACL grants mutation")
        finally:
            lib.acl_free(acl)
    except (OSError, ValueError) as exc:
        raise PolicyError("cannot inspect extended ACL") from exc
    finally:
        # Only root may change a protected ancestor; nevertheless detect swaps.
        if 'before' in locals():
            try:
                after = os.fstat(fd) if fd is not None else os.lstat(path)
            except (OSError, ValueError) as exc:
                raise PolicyError("ACL object changed during inspection") from exc
            if (before.st_dev, before.st_ino, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_ctime_ns):
                raise PolicyError("ACL object changed during inspection")


def _assert_protected_stat(
    path: "str | os.PathLike", directory: bool = False
) -> "tuple[str, os.stat_result]":
    """As :func:`assert_protected`, but also return the lstat of the exact
    final checked object so a caller can confirm it opened *that* object.
    """
    if not isinstance(path, (str, os.PathLike)):
        raise PolicyError(f"protected check: bad path type {type(path).__name__}")
    p = os.fspath(path)
    if not isinstance(p, str) or not p.startswith("/") or _CONTROL_CHARS.search(p):
        raise PolicyError("protected check: expected an absolute text path without controls")
    # Reject ``..``, ``.`` and doubled separators *before* any component is
    # examined: normalizing first would let the string be checked against one
    # path while the kernel resolves the original through a symlink to a
    # different, attacker-owned file.
    norm = os.path.normpath(p)
    if p.startswith("//") or norm != p:
        raise PolicyError(
            f"protected check: path must be normalized (no '..', '.', '//'): {p!r}"
        )
    components = ["/"]
    if norm != "/":
        for part in norm.strip("/").split("/"):
            components.append(components[-1].rstrip("/") + "/" + part)
    final_st = None
    for index, component in enumerate(components):
        try:
            st = os.lstat(component)
        except OSError as exc:
            raise PolicyError(f"protected check: cannot lstat {component}: {exc}")
        if _stat.S_ISLNK(st.st_mode):
            raise PolicyError(f"protected check: symlink not allowed: {component}")
        if st.st_uid != 0:
            raise PolicyError(
                f"protected check: {component} owned by uid {st.st_uid}, not root"
            )
        if st.st_mode & 0o022:
            raise PolicyError(
                f"protected check: {component} writable by group/other"
            )
        # On macOS an extended ACL can grant write/delete/add even with
        # clean mode bits, so mode alone cannot establish immutability.
        assert_no_mutating_acl(component)
        if index < len(components) - 1 and not _stat.S_ISDIR(st.st_mode):
            raise PolicyError("protected check: ancestor is not a directory")
        if index == len(components) - 1:
            if directory and not _stat.S_ISDIR(st.st_mode):
                raise PolicyError(f"protected check: {component} is not a directory")
            if not directory and not _stat.S_ISREG(st.st_mode):
                raise PolicyError(f"protected check: {component} is not a file")
            final_st = st
    return norm, final_st


def assert_protected(path: "str | os.PathLike", directory: bool = False) -> str:
    """Fail unless every component of ``path`` is root-owned and unwritable
    by group/other and by any extended ACL, with no symlink anywhere along
    the way, and unless ``path`` is already normalized.

    Each ancestor is lstat'ed individually: the final path alone is never
    trusted, because an attacker-writable ancestor lets the "final" object
    be swapped regardless of its own mode.  Non-normalized input is refused
    outright, so the checked string and the kernel-resolved object are the
    same path.  The returned string is exactly ``path``.
    """
    norm, _ = _assert_protected_stat(path, directory)
    return norm


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise PolicyError(f"duplicate JSON key: {key!r}")
        seen[key] = value
    return seen


def load_policy(path: str | os.PathLike, protected: bool = True) -> Policy:
    """Load and validate a policy from an explicit absolute path.

    No environment variable or current-directory search is performed.  With
    ``protected=True`` (the default, and the only mode that yields an
    authority) the file and every ancestor must pass :func:`assert_protected`.
    """
    if not isinstance(path, (str, os.PathLike)):
        raise PolicyError("load_policy: path must be given explicitly")
    p = os.fspath(path)
    _require_abs_path(p, "load_policy")
    expect = None
    if protected:
        _, expect = _assert_protected_stat(p, directory=False)
    try:
        # O_NOFOLLOW: never open a symlink, even when the check above passed
        # against the normalized name of a different object.
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (OSError, ValueError) as exc:
        raise PolicyError(f"load_policy: cannot open {p}: {exc}")
    try:
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise PolicyError("load_policy: expected a single-link regular file")
        if expect is not None and (
            st.st_dev, st.st_ino, st.st_uid, st.st_mode
        ) != (
            expect.st_dev, expect.st_ino, expect.st_uid, expect.st_mode
        ):
            raise PolicyError(
                "load_policy: opened object differs from the checked one "
                f"(dev/ino/uid/mode mismatch): {p!r}"
            )
        if protected:
            assert_no_mutating_acl(p, fd=fd)
        # Running byte limit: the bound holds while reading, not only
        # against the size seen in an initial stat.
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, _READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > POLICY_MAX_BYTES:
                raise PolicyError(
                    f"load_policy: {p} exceeds {POLICY_MAX_BYTES}-byte limit"
                )
            chunks.append(chunk)
        after = os.fstat(fd)
        if (st.st_size != total or (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)):
            raise PolicyError("load_policy: file changed during read")
        raw = b"".join(chunks)
    except OSError as exc:
        raise PolicyError(f"load_policy: cannot read {p}: {exc}")
    finally:
        os.close(fd)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError(f"load_policy: {p} is not valid UTF-8: {exc}")
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except PolicyError:
        raise
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise PolicyError(f"load_policy: invalid JSON in {p}: {exc}")
    return validate_policy(data)
