"""Hypervisor command builders for the two fixed AI sandbox zones.

This module turns a validated policy and a trusted state directory into the
exact ``argv`` arrays that start one isolated guest on macOS (``vfkit``) or on
Linux (``vhost-device-vsock`` plus ``qemu-system-x86_64``).  It is an offline
builder: it never runs anything, never reads the environment, never searches
for configuration and never accepts an operator-supplied hypervisor flag.

Security properties, and their limits:

* Every device is a fixed literal chosen here.  A caller can only supply the
  zone name, the policy, the state directory and the executable paths, so no
  network interface, filesystem share, Rosetta, GPU, input, USB, ignition or
  management REST endpoint can be requested.
* Paths are rejected when they contain ``,`` ``:`` newline or NUL because both
  vfkit and QEMU parse those characters structurally inside their
  ``key=value`` device specifications, and a comma in a path would therefore
  inject an extra device option.
* Resource limits come from the shared immutable policy model. Callers must
  separately establish root-protected policy and executable provenance.

These builders prove that the *command text* is free of forbidden devices.
They are not evidence that a guest booted without a NIC, that the Linux
vhost backend group isolation works, or that the executables are the reviewed
ones -- ownership, hashes, boot behaviour and protected policy loading belong to
the deployment layer.
"""

from __future__ import annotations

from dataclasses import asdict
import os
import re
import socket

from .policy import Policy, PolicyError, validate_policy

__all__ = [
    "VMBuildError",
    "ZONES",
    "DEFAULT_RECOMMENDATION",
    "build_linux_argv",
    "build_vfkit_argv",
    "vfkit_ssh_connect",
    "linux_vsock_ssh_connect",
    "state_paths",
    "vsock_ssh_connect",
    "zone_resources",
]

#: The only zones this build understands.  Names are fixed, never discovered.
ZONES = ("internal", "confidential")

#: Example recommendation used by the deployment layer when writing a policy.
#: It is a suggestion, not a limit; the limits live in policy.validate_policy.
DEFAULT_RECOMMENDATION = {"cpus": 4, "memory_mib": 4096, "disk_gib": 64}

# Fixed per-zone vsock identity.  A distinct CID per zone keeps the two guests
# distinguishable to the vhost backend, and the group name ties the backend
# instance to one zone so it cannot be shared.
ZONE_GUEST_CID = {"internal": 4, "confidential": 5}
ZONE_GROUP = {"internal": "internal", "confidential": "confidential"}

# Fixed vsock ports: guest SSH (host -> guest) and the broker channel.
SSH_VSOCK_PORT = 2222
BROKER_VSOCK_PORT = 5001

# Fixed file names inside the state directory, so no path fragment of a guest
# device comes from a request at all.
STATE_FILENAMES = (
    "root.raw",
    "efi-vars",
    "console.log",
    "ssh.sock",
    "broker.sock",
    "vsock",
    "vhost.socket",
)

# Characters the vfkit/QEMU key-value parsers treat as structure.
_PATH_FORBIDDEN = (",", ":", "\n", "\r", "\0")
_ZONE_RE = re.compile(r"\A[a-z][a-z0-9_]*\Z")

# Substrings that must never appear in a generated command line.
_VFKIT_FORBIDDEN = (
    "virtio-net",
    "virtio-fs",
    "virtiofs",
    "rosetta",
    "virtio-gpu",
    "virtio-input",
    "virtio-usb",
    "virtio-balloon-device",
    "ignition",
    "cloud-init",
    "sharedDir",
    "mountTag",
    "--unix-socket",
    "--gpu",
    "--input",
    "--clipboard",
    "--usb",
    "--net",
    "--kernel",
    "--initrd",
    "tcp://",
    "unix://",
)
_QEMU_FORBIDDEN = (
    "kernel",
    "initrd",
    "bios",
    "pflash",
    "netdev",
    "virtio-net",
    "fsdev",
    "virtfs",
    "9p",
    "qmp",
    "usb",
    "vga",
    "soundhw",
    "serial",
    "guest-agent",
    "vhost-vsock-pci",
)
_BACKEND_FORBIDDEN = ("forward-cid", "forward-listen")


class VMBuildError(ValueError):
    """A requested VM command was refused by the builder."""


def _reject_forbidden(argv, forbidden, what, redact=()):
    """Refuse a command line that mentions a forbidden device or channel.

    The state directory and the executable are blanked out first so a
    legitimate path such as ``/opt/usb/qemu-system-x86_64`` cannot trip a check
    aimed at the hypervisor options themselves.
    """
    for token in argv:
        probe = token.lower()
        for keep in redact:
            probe = probe.replace(keep.lower(), "<state>")
        for needle in forbidden:
            if needle in probe:
                raise VMBuildError(
                    "%s: forbidden token %r in %r" % (what, needle, token)
                )


def _checked_text(value, what, *, maxlen=4096):
    if not isinstance(value, str):
        raise VMBuildError("%s must be a string" % what)
    if not value or len(value) > maxlen:
        raise VMBuildError("%s must be non-empty and at most %d characters" % (what, maxlen))
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise VMBuildError("%s must not contain control characters" % what)
    return value


def _checked_path(value, what):
    """Absolute, normalized, delimiter-free path.

    Commas, colons, newlines and NUL are refused because vfkit and QEMU split
    their device specifications on them: a permitted comma would let a path
    carry an extra device option.
    """
    value = _checked_text(value, what)
    for char in _PATH_FORBIDDEN:
        if char in value:
            raise VMBuildError(
                "%s must not contain %r (hypervisor key-value delimiter)"
                % (what, "," if char == "," else repr(char))
            )
    if not os.path.isabs(value):
        raise VMBuildError("%s must be an absolute path" % what)
    if value.startswith("//") or os.path.normpath(value) != value:
        raise VMBuildError("%s must be a normalized path (no .. . // or trailing /)" % what)
    return value


def _checked_executable(value, what):
    return _checked_path(value, what)


def _checked_zone(zone):
    if not isinstance(zone, str) or not _ZONE_RE.match(zone):
        raise VMBuildError("zone must be one of %s" % (", ".join(ZONES),))
    if zone not in ZONES:
        raise VMBuildError("unknown zone %r" % (zone,))
    return zone


def zone_resources(zone, policy):
    """Validate shared policy input; this pure builder establishes no authority."""
    zone = _checked_zone(zone)
    try:
        if isinstance(policy, Policy):
            policy = {"version": policy.version,
                      "zones": {name: asdict(entry) for name, entry in policy.zones.items()}}
        checked = validate_policy(policy)
        entry = checked.zones[zone]
    except (PolicyError, TypeError, ValueError, AttributeError, KeyError) as exc:
        raise VMBuildError("policy rejected: %s" % exc) from exc
    return zone, {"cpus": entry.cpus, "memory_mib": entry.memory_mib,
                  "disk_gib": entry.disk_gib}


def _socket_path(path, what):
    path = _checked_path(path, what)
    # vfkit 0.6.4 uses URL.EscapedPath in connect mode, so escapes change names.
    if not re.fullmatch(r"/[A-Za-z0-9_./~-]+", path):
        raise VMBuildError("%s must contain only URL-unescaped ASCII path characters" % what)
    if len(os.fsencode(path)) > 103:
        raise VMBuildError("%s exceeds portable Unix socket path length" % what)
    return path


def _runtime_paths(state_dir, runtime_dir, broker_socket=None):
    state = state_paths(state_dir)
    runtime = _checked_path(runtime_dir if runtime_dir is not None else state_dir, "runtime_dir")
    for name in ("ssh.sock", "broker.sock", "vsock", "vhost.socket"):
        state[name] = _socket_path(runtime.rstrip("/") + "/" + name, name)
    if broker_socket is not None:
        state["broker.sock"] = _socket_path(broker_socket, "broker_socket")
    if state["broker.sock"] in (state["ssh.sock"], state["vsock"], state["vhost.socket"]):
        raise VMBuildError("broker socket must be distinct from VM runtime sockets")
    return state


def state_paths(state_dir):
    """Return the fixed per-VM file paths under a trusted state directory."""
    base = _checked_path(state_dir, "state_dir")
    return {name: base.rstrip("/") + "/" + name for name in STATE_FILENAMES}


def build_vfkit_argv(zone, policy, state_dir, vfkit_path, *, runtime_dir=None, broker_socket=None):
    """Build the macOS ``vfkit`` argv for one zone guest.

    ``runtime_dir`` and ``broker_socket`` are trusted deployment inputs: VM
    state stays private (0700); broker IPC may use a separate 0710 directory
    and fixed-group 0660 socket. This builder changes no permissions.

    No NIC, no shared filesystem, no Rosetta, no GPU, no input, no USB, no
    clipboard, no ignition and no management REST socket (``--restful-uri
    none://``).  EFI boot is used so the native arm64 Debian image needs no
    external kernel or initrd.  ``virtio-balloon`` exposes a balloon device but
    does not by itself prove that host RAM is reclaimed; console log bounding
    and rotation are deployment prerequisites.
    """
    zone, resources = zone_resources(zone, policy)
    state = _runtime_paths(state_dir, runtime_dir, broker_socket)
    executable = _checked_executable(vfkit_path, "vfkit_path")
    argv = [
        executable,
        "--cpus", str(resources["cpus"]),
        "--memory", str(resources["memory_mib"]),
        "--bootloader", "efi,variable-store=%s,create" % state["efi-vars"],
        "--device", "virtio-blk,path=%s" % state["root.raw"],
        "--device", "virtio-rng",
        "--device", "virtio-balloon",
        "--device", "virtio-serial,logFilePath=%s" % state["console.log"],
        "--device",
        "virtio-vsock,port=%d,socketURL=%s,connect" % (SSH_VSOCK_PORT, state["ssh.sock"]),
        "--device",
        "virtio-vsock,port=%d,socketURL=%s,listen" % (BROKER_VSOCK_PORT, state["broker.sock"]),
        "--restful-uri", "none://",
    ]
    _reject_forbidden(argv, _VFKIT_FORBIDDEN, "vfkit argv", redact=(state_dir, os.path.dirname(state["ssh.sock"]), state["broker.sock"], executable))
    return argv


def build_linux_argv(zone, policy, state_dir, qemu_path, vsock_path, *, runtime_dir=None):
    """Build ``(backend_argv, qemu_argv)`` for one zone guest on Linux.

    The vhost-user vsock backend runs as its own process, under its own
    account, one instance per VM, with a zone-specific CID and group.  The
    forbidden ``--forward-cid`` backend is never emitted: it would forward guest
    connections straight to another AF_VSOCK endpoint on the host instead of the
    broker unix socket.  QEMU gets no firmware, kernel or initrd path (Debian
    amd64 cloud images boot from their own BIOS/GRUB), no user config, no
    default network, no monitor and no guest agent, and it uses the shared
    memfd backend the vhost device requires.  No ``-serial`` output is
    configured here: serial logging is deliberately absent rather than unbounded,
    and the deployment layer adds a bounded sink if it wants one.
    """
    zone, resources = zone_resources(zone, policy)
    state = _runtime_paths(state_dir, runtime_dir)
    qemu = _checked_executable(qemu_path, "qemu_path")
    backend = _checked_executable(vsock_path, "vsock_path")
    cid = ZONE_GUEST_CID[zone]
    group = ZONE_GROUP[zone]

    backend_argv = [
        backend,
        "--vm",
        "guest-cid=%d,uds-path=%s,socket=%s,groups=%s"
        % (cid, state["vsock"], state["vhost.socket"], group),
    ]
    qemu_argv = [
        qemu,
        "-nodefaults",
        "-no-user-config",
        "-nic", "none",
        "-display", "none",
        "-monitor", "none",
        "-machine", "q35,accel=kvm,memory-backend=mem0",
        "-object",
        "memory-backend-memfd,id=mem0,size=%d,share=on" % (resources["memory_mib"] * 1024 * 1024),
        "-m", str(resources["memory_mib"]),
        "-smp", str(resources["cpus"]),
        "-drive", "file=%s,format=raw,if=virtio" % state["root.raw"],
        "-device", "virtio-rng-pci",
        "-device", "virtio-balloon-pci",
        "-chardev", "socket,id=vsock,reconnect=0,path=%s" % state["vhost.socket"],
        "-device", "vhost-user-vsock-pci,chardev=vsock",
    ]
    _reject_forbidden(
        backend_argv, _BACKEND_FORBIDDEN, "vhost backend argv", redact=(state_dir, os.path.dirname(state["vsock"]), backend)
    )
    _reject_forbidden(qemu_argv, _QEMU_FORBIDDEN, "qemu argv", redact=(state_dir, os.path.dirname(state["vsock"]), qemu))
    if "groups=%s" % group not in backend_argv[2]:
        raise VMBuildError("vhost backend must carry the zone group")
    return backend_argv, qemu_argv


def linux_vsock_ssh_connect(uds_path, port=SSH_VSOCK_PORT, timeout=10.0):
    """Open Linux vhost-device-vsock's CONNECT 2222 / OK 2222 stream.

    This framing belongs only to the userspace Linux backend. vfkit's fixed
    connect socket carries raw SSH and uses :func:`vfkit_ssh_connect` instead.
    """
    path = _socket_path(uds_path, "uds_path")
    if port != SSH_VSOCK_PORT:
        raise VMBuildError("only vsock port %d is reachable here" % SSH_VSOCK_PORT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise VMBuildError("timeout must be a positive number of seconds at most 300")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(path)
        sock.sendall(b"CONNECT %d\n" % port)
        line = _read_line(sock, 64, timeout)
        if line != b"OK %d\n" % port:
            raise VMBuildError("unexpected vsock handshake response %r" % (line,))
    except BaseException:
        sock.close()
        raise
    return sock


def _read_line(sock, max_bytes, timeout):
    """Read one newline-terminated line of at most ``max_bytes`` bytes."""
    chunks = []
    total = 0
    while True:
        try:
            chunk = sock.recv(1)
        except socket.timeout as exc:
            raise VMBuildError("vsock handshake timed out") from exc
        if not chunk:
            raise VMBuildError("vsock handshake closed before a full line")
        if chunk == b"\n":
            return b"".join(chunks) + b"\n"
        total += 1
        if total > max_bytes:
            raise VMBuildError("vsock handshake line longer than %d bytes" % max_bytes)
        chunks.append(chunk)


# Kept for callers of the original Linux helper; never use it for vfkit.
vsock_ssh_connect = linux_vsock_ssh_connect


def vfkit_ssh_connect(uds_path, timeout=10.0):
    """Connect to vfkit's fixed raw Unix SSH stream, with no handshake."""
    path = _socket_path(uds_path, "uds_path")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise VMBuildError("timeout must be a positive number of seconds at most 300")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(path)
    except BaseException:
        sock.close()
        raise
    return sock
