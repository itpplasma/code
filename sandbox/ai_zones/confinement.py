"""Default-deny broker profiles for a caller-selected, protected Python runtime.

The supervisor must freeze and verify runtime/code contents, policy/token files,
profile and launch configuration, close inherited descriptors, clear its
inherited environment and set a fixed working directory before launching.
This generator does not establish those deployment prerequisites. Seatbelt's
private interface has been probed only on macOS build 25G83; launch refuses
other builds until the probes and this gate have been deliberately updated.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Dict, Iterable, List, Sequence, Tuple

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
PYTHON_ISOLATED_FLAGS = ("-I", "-S", "-B")
TESTED_MACOS_BUILD = "25G83"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class ConfinementError(ValueError):
    """An unsafe input or unmet confinement prerequisite."""


def _validate_path(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ConfinementError(f"{what}: expected an absolute text path")
    if (_CONTROL_CHARS.search(value) or len(value) > 1024 or
            any("\ud800" <= c <= "\udfff" for c in value)):
        raise ConfinementError(f"{what}: invalid path characters or length")
    if value.startswith("//") or os.path.normpath(value) != value:
        raise ConfinementError(f"{what}: path must be normalized")
    return value


def sbpl_quote(value: object, what: str = "path") -> str:
    """Quote an entire SBPL string, including S-expression injection attempts."""
    value = _validate_path(value, what)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _grant_path(value: object, what: str) -> str:
    path = _validate_path(value, what)
    if path == "/":
        raise ConfinementError(f"{what}: refusing a whole-filesystem grant")
    return path


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _overlap(a: str, b: str) -> bool:
    return _within(a, b) or _within(b, a)


def _runtime(python: str, runtime_root: str) -> Tuple[str, str]:
    py = _grant_path(python, "python")
    runtime = _grant_path(runtime_root, "runtime_root")
    if py == runtime or not _within(py, runtime):
        raise ConfinementError("python: canonical executable must be inside runtime_root")
    return py, runtime


def _ancestors(path: str) -> Iterable[str]:
    while path != "/":
        path = os.path.dirname(path)
        yield path


def generate_macos_profile(
    *, unix_socket: str, state_dir: str, model_port: int,
    data_roots: Iterable[Tuple[str, str]], python: str, runtime_root: str,
    code_roots: Iterable[str], read_files: Iterable[str] = (),
    runtime_executables: Iterable[str] = (), runtime_uname: bool = False,
) -> str:
    """Pure profile generator; all paths come from trusted launch configuration.

    ``runtime_root`` is the entire frozen canonical runtime, including imports.
    ``code_roots`` contains only frozen broker code. ``read_files`` grants exact
    policy/token files, not their directories. Data roots are approved with
    ``read`` or ``readwrite`` access. Only the zone's state is otherwise writable.
    The broker uses literal 127.0.0.1; Seatbelt permits IPv4 localhost at
    one fixed TCP port. Its parser cannot encode an exact numeric address.
    Root configuration must reserve distinct ports for distinct service roles;
    loopback aliases at the same port do not isolate them. runtime_executables
    lists any exact, frozen framework helper executed by the chosen launcher.
    The measured dyld exception permits listing public top-level OS names at
    literal /, never descendants. Deployment must reject confidential custom
    root names; confidentiality probes cover user data below that level.
    runtime_uname enables only the five public OS/host identity sysctls used
    by os.uname(), required by CPython 3.14 ctypes on this tested OS build.
    The hostname becomes visible; no other sysctl reads are granted.
    """
    if type(runtime_uname) is not bool:
        raise ConfinementError("runtime_uname: expected bool")
    py, runtime = _runtime(python, runtime_root)
    helpers = [_grant_path(p, "runtime executable") for p in runtime_executables]
    if any(not _within(p, runtime) or p == runtime for p in helpers):
        raise ConfinementError("runtime executable must be inside runtime_root")
    state = _grant_path(state_dir, "state_dir")
    sock = _grant_path(unix_socket, "unix_socket")
    if not _within(sock, state) or sock == state:
        raise ConfinementError("unix_socket: must be inside private state_dir")
    if (isinstance(model_port, bool) or not isinstance(model_port, int) or
            not 1 <= model_port <= 65535 or model_port == 53):
        raise ConfinementError("model_port: expected a non-DNS TCP port")
    code = [_grant_path(p, "code root") for p in code_roots]
    if not code:
        raise ConfinementError("code_roots: frozen entry code is required")
    files = [_grant_path(p, "read file") for p in read_files]
    roots = []
    for item in data_roots:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ConfinementError("data_roots: expected (path, mode)")
        path, mode = item
        if mode not in ("read", "readwrite"):
            raise ConfinementError("data_roots: mode must be read or readwrite")
        roots.append((_grant_path(path, "data root"), mode))
    authorities = [runtime] + code + files
    writable = [state] + [p for p, mode in roots if mode == "readwrite"]
    for write in writable:
        if any(_overlap(write, authority) for authority in authorities):
            raise ConfinementError("writable root overlaps runtime, code or configuration")
    # A broad writable ancestor would silently cancel a narrower read-only grant.
    for path, mode in roots:
        if mode == "read" and any(_overlap(path, write) for write in writable):
            raise ConfinementError("read-only data overlaps a writable root")
    q = sbpl_quote
    lines = ["(version 1)", "(deny default)",
             f"(allow process-exec (literal {q(py)}))",
             '(allow file-read-data (literal "/"))',
             '(allow file-read-data (literal "/dev/urandom"))']
    # dyld aborts before Python without this exact root-directory read on
    # build 25G83. It exposes root names, not descendant file contents.
    if runtime_uname:
        for name in ("kern.osrelease", "kern.ostype", "kern.version", "hw.machine", "kern.hostname"):
            lines.append(f'(allow sysctl-read (sysctl-name "{name}"))')
    for helper in helpers:
        lines.append(f"(allow process-exec (literal {q(helper)}))")
    for root in [runtime] + code:
        lines.append(f"(allow file-read* (subpath {q(root)}))")
    for path in files:
        lines.append(f"(allow file-read* (literal {q(path)}))")
    for ancestor in sorted({a for p in authorities + writable + [p for p, _ in roots]
                            for a in _ancestors(p)}):
        lines.append(f"(allow file-read-metadata (literal {q(ancestor)}))")
    lines.append(f"(allow file-read* file-write* (subpath {q(state)}))")
    for path, mode in roots:
        access = "file-read*" + (" file-write*" if mode == "readwrite" else "")
        lines.append(f"(allow {access} (subpath {q(path)}))")
    lines += [f"(allow network-bind (literal {q(sock)}))",
              f"(allow network-outbound (literal {q(sock)}))",
              f'(allow network-outbound (remote tcp4 "localhost:{model_port}"))']
    return "\n".join(lines) + "\n"


def assert_macos_build() -> str:
    """Refuse launch on an unprobed OS; never retry without confinement."""
    try:
        out = subprocess.run(["/usr/bin/sw_vers", "-buildVersion"], env={},
                             capture_output=True, text=True, check=True,
                             timeout=10, close_fds=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfinementError(f"cannot verify macOS build: {exc}") from exc
    build = out.stdout.strip()
    if build != TESTED_MACOS_BUILD:
        raise ConfinementError(f"macOS build {build!r} requires confinement retesting")
    return build


def macos_launch_argv(profile_path: str, entry_point: str, *, python: str,
                      runtime_root: str) -> List[str]:
    """Fixed absolute canonical interpreter; caller verifies frozen entry code.

    Run with shell=False, env=launch_env(), close_fds=True and a fixed cwd.
    Unknown OS builds, parser errors and execution failures have no fallback.
    """
    prof = _grant_path(profile_path, "profile_path")
    entry = _grant_path(entry_point, "entry_point")
    py, _ = _runtime(python, runtime_root)
    assert_macos_build()
    return [SANDBOX_EXEC, "-f", prof, py, *PYTHON_ISOLATED_FLAGS, entry]


def launch_env() -> Dict[str, str]:
    """No caller environment is inherited; Python may set its own locale."""
    return {}


def assert_protected(path: str, directory: bool = False) -> str:
    """Use the shared policy's root, mode, symlink and native ACL check.

    A missing shared policy fails closed. This path check does not establish
    an integrity manifest or enumerate imported dependencies.
    """
    _validate_path(path, "protected path")
    try:
        from .policy import assert_protected as check, PolicyError
    except ImportError as exc:
        raise ConfinementError("shared policy protection checker is required") from exc
    try:
        return check(path, directory=directory)
    except PolicyError as exc:
        raise ConfinementError(str(exc)) from exc


def verify_production_inputs(profile_path: str, entry_point: str, *, python: str,
                             runtime_root: str, code_roots: Sequence[str],
                             authority_files: Sequence[str] = (),
                             runtime_executables: Sequence[str] = ()) -> None:
    """Check listed authorities, not completeness of the deployment manifest.

    The supervisor must separately verify every runtime/code descendant and
    its integrity manifest, plus fixed cwd, descriptors and launch settings.
    A production relocated runtime has not yet passed deployment probes.
    """
    py, runtime = _runtime(python, runtime_root)
    if any(p == runtime or not _within(_grant_path(p, "runtime executable"), runtime)
           for p in runtime_executables):
        raise ConfinementError("runtime executable must be inside runtime_root")
    assert_macos_build()
    for path in [profile_path, entry_point, py, SANDBOX_EXEC] + list(authority_files) + list(runtime_executables):
        assert_protected(path)
    for root in [runtime] + list(code_roots):
        assert_protected(root, directory=True)


def _systemd_path(value: str, what: str) -> str:
    path = _grant_path(value, what)
    # Refuse separator/specifier syntax instead of emitting an ambiguous mount.
    if any(c.isspace() or c in ':"\\%' for c in path):
        raise ConfinementError(f"{what}: unsupported systemd path syntax")
    return path


def generate_systemd_unit_fragment(
    *, socket_path: str, root_directory: str,
    data_mounts: Iterable[Tuple[str, str, str]],
    model_socket: Tuple[str, str],
) -> str:
    """Untested Linux namespace plan, requiring a Unix model proxy adapter.

    The root tree and approved mounts must expose no other pathname sockets;
    only exact broker and model sockets are bound into it. AF_UNIX alone does not isolate paths.
    PrivateNetwork prevents reaching the macOS adapter's host loopback model.
    Real namespace, root-tree and inherited-descriptor probes are required.
    """
    sock = _systemd_path(socket_path, "socket_path")
    root = _systemd_path(root_directory, "root_directory")
    lines = ["# RUNTIME UNTESTED: requires isolated root and Unix model adapter",
             "[Service]", "PrivateNetwork=yes", "RestrictAddressFamilies=AF_UNIX",
             "CapabilityBoundingSet=", f"RootDirectory={root}",
             f"BindPaths={sock}:{sock}"]
    if not isinstance(model_socket, (tuple, list)) or len(model_socket) != 2:
        raise ConfinementError("model_socket: expected (host, destination)")
    host, dest = [_systemd_path(p, "model socket") for p in model_socket]
    lines.append(f"BindReadOnlyPaths={host}:{dest}")
    for item in data_mounts:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise ConfinementError("data_mounts: expected (host, destination, mode)")
        host, dest, mode = item
        host, dest = _systemd_path(host, "mount host"), _systemd_path(dest, "mount destination")
        if mode not in ("read", "readwrite"):
            raise ConfinementError("mount mode must be read or readwrite")
        key = "BindReadOnlyPaths" if mode == "read" else "BindPaths"
        lines.append(f"{key}={host}:{dest}")
    return "\n".join(lines + ["NoNewPrivileges=yes", "PrivateTmp=yes",
                             "PrivateDevices=yes", "SystemCallArchitectures=native", ""])
