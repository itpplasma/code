"""Offline guest-seed, guest-relay and image-pin tooling for the AI sandbox.

Three offline jobs, all standard library only:

``seed``
    Write a NoCloud seed directory (``user-data`` and ``meta-data``, nothing
    else) for one fixed zone.  The seed pins the guest hostname, creates the
    single ``agent`` user with the operator's ed25519 key and a locked
    password, pins the pre-generated guest host key, turns off password and
    root login and forwarding, and binds SSH to ``127.0.0.1:22``.  It installs
    no packages, downloads nothing, reboots nothing and contains no cloud
    credentials: the guest has no network path to fetch anything.

``relay``
    Emit the guest-side relay program and its systemd units.  The relay bridges
    the two fixed channels -- AF_VSOCK port 2222 to guest-local TCP 22, and
    guest-local TCP ``127.0.0.1:5001`` to the host broker on vsock CID 2 port
    5001 -- and nothing else.  There is no bind address, target host or URL a
    caller can choose.

``verify-image``
    Stream a local raw image through SHA256 and compare it with a reviewed
    image descriptor.  This proves the file matches the pin it was given; it
    cannot make a self-chosen pin trustworthy, so the descriptor itself stays a
    reviewed input.

Known limits, stated rather than papered over: no cryptographic validation of
key pairs happens here.  Key files are checked for format, size, type and
internal consistency (the embedded public blob of the host key must equal the
host public key that was passed alongside it), and ``ssh-keygen`` plus a real
boot are the staging gate that proves the pair works.  Debian genericcloud
ships the Python 3 that cloud-init needs, but the required modules, openssh
and a successful boot must be proven in staging before an image is accepted.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import struct
import stat

from .policy import PolicyError, assert_no_mutating_acl
import sys

__all__ = [
    "GuestSeedError",
    "ZONES",
    "build_seed",
    "relay_units",
    "relay_source",
    "verify_image",
    "write_relay",
    "write_seed",
]

ZONES = ("internal", "confidential")
ZONE_HOSTNAME = {"internal": "agent-internal", "confidential": "agent-confidential"}
AGENT_USER = "agent"
SEED_VERSION = 1

MAX_KEY_BYTES = 64 * 1024
MAX_SEED_BYTES = 64 * 1024
MAX_DESCRIPTOR_BYTES = 16 * 1024
HASH_CHUNK_BYTES = 256 * 1024
MAX_PUBKEY_CHARS = 4096
MAX_CLIENT_MODELS_BYTES = 4096
_CLIENT_MODEL_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")

ED25519_TYPE = b"ssh-ed25519"
OPENSSH_PRIVATE_MAGIC = b"openssh-key-v1\0"
DEBIAN_CLOUD_ORIGIN = "cloud.debian.org"
RELEASE_SUITE = {"13": "trixie"}
ARCHITECTURES = ("arm64", "amd64")

_HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_PUBKEY_RE = re.compile(r"\Assh-ed25519 ([A-Za-z0-9+/=]{68,1024})( [ -~]{0,255})?\Z")
_DESCRIPTOR_KEYS = (
    "version",
    "distribution",
    "release",
    "architecture",
    "format",
    "sha256",
    "source_url",
)
_SNAPSHOT_RE = re.compile(r"\A(20[0-9]{6})-([0-9]+)\Z")

_FILE_MODE = 0o600
_DIR_MODE = 0o700


class GuestSeedError(ValueError):
    """A seed, relay or image-pin request was refused."""


# --------------------------------------------------------------------------- #
# input guards
# --------------------------------------------------------------------------- #

def _checked_path(value, what):
    if not isinstance(value, str) or not value:
        raise GuestSeedError("%s must be a non-empty path" % what)
    if any(ord(char) < 32 or ord(char) == 127 or char in ",:" for char in value):
        raise GuestSeedError("%s must not contain a delimiter or control character" % what)
    if not os.path.isabs(value):
        raise GuestSeedError("%s must be an absolute path" % what)
    if value.startswith("//") or os.path.normpath(value) != value:
        raise GuestSeedError("%s must be normalized (no .. . // or trailing /)" % what)
    return value


def _checked_zone(zone):
    if zone not in ZONES:
        raise GuestSeedError("zone must be one of %s" % ", ".join(ZONES))
    return zone


def _read_regular_file(path, what, max_bytes):
    """Read a regular, non-symlink file of at most ``max_bytes`` bytes.

    Only the final component is required to be free of symlinks; parent
    directories may legitimately be symlinks (``/tmp`` on macOS is one).
    """
    path = _checked_path(path, what)
    fd = _open_regular(path, what)
    try:
        size = os.fstat(fd).st_size
        if size == 0 or size > max_bytes:
            raise GuestSeedError("%s must contain 1..%d bytes" % (what, max_bytes))
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(16384, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise GuestSeedError("%s exceeded its running byte limit" % what)
        if not total:
            raise GuestSeedError("%s became empty while reading" % what)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _open_regular(path, what):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise GuestSeedError("%s must be a regular non-symlink file" % what)
        return fd
    except OSError as exc:
        raise GuestSeedError("cannot open %s: %s" % (what, exc)) from exc


def _private_directory(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise GuestSeedError("output directory must be privately owned with mode 0700")
        assert_no_mutating_acl(path, fd=fd)
        return fd
    except (OSError, PolicyError, GuestSeedError) as exc:
        if 'fd' in locals():
            os.close(fd)
        raise GuestSeedError("unsafe output directory: %s" % exc) from exc


def _write_new_file(path, payload, mode):
    """Create exclusively relative to a pinned private output directory."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    if len(data) > MAX_SEED_BYTES:
        raise GuestSeedError("output exceeds %d bytes" % MAX_SEED_BYTES)
    directory = _private_directory(os.path.dirname(path))
    try:
        descriptor = os.open(os.path.basename(path),
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                             mode, dir_fd=directory)
        try:
            os.fchmod(descriptor, mode)
            remaining = memoryview(data)
            while remaining:
                count = os.write(descriptor, remaining)
                if count <= 0:
                    raise GuestSeedError("output write made no progress")
                remaining = remaining[count:]
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)
    return len(data)


def _prepare_output_dir(path):
    path = _checked_path(path, "output")
    try:
        os.makedirs(path, _DIR_MODE, exist_ok=True)
    except OSError as exc:
        raise GuestSeedError("could not create output directory: %s" % exc) from exc
    fd = _private_directory(path)
    os.close(fd)
    return path


# --------------------------------------------------------------------------- #
# key guards
# --------------------------------------------------------------------------- #

def _parse_ssh_string(blob, offset, what, *, allow_empty=False):
    if offset + 4 > len(blob):
        raise GuestSeedError("%s: truncated length prefix" % what)
    (length,) = struct.unpack_from(">I", blob, offset)
    offset += 4
    if (length == 0 and not allow_empty) or offset + length > len(blob) or length > MAX_KEY_BYTES:
        raise GuestSeedError("%s: bad string length" % what)
    return blob[offset:offset + length], offset + length


def _check_ed25519_public_line(text, what):
    """Validate one bounded OpenSSH ed25519 public key line.

    This checks the wire format and the 32-byte ed25519 key length.  It does
    not prove anything about which private key belongs to it.
    """
    if not isinstance(text, str):
        raise GuestSeedError("%s must be text" % what)
    lines = text.splitlines()
    if len(lines) != 1:
        raise GuestSeedError("%s must be exactly one line" % what)
    line = lines[0]
    if len(line) > MAX_PUBKEY_CHARS:
        raise GuestSeedError("%s must be at most %d characters" % (what, MAX_PUBKEY_CHARS))
    match = _PUBKEY_RE.match(line)
    if not match:
        raise GuestSeedError("%s must be a single 'ssh-ed25519 <base64> [comment]' line" % what)
    try:
        blob = base64.b64decode(match.group(1), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GuestSeedError("%s: public key blob is not valid base64" % what) from exc
    key_type, offset = _parse_ssh_string(blob, 0, what)
    if key_type != ED25519_TYPE:
        raise GuestSeedError("%s: key type must be ssh-ed25519" % what)
    key, offset = _parse_ssh_string(blob, offset, what)
    if len(key) != 32 or offset != len(blob):
        raise GuestSeedError("%s: ed25519 public key must be 32 bytes" % what)
    return line, blob


def _check_ed25519_private_text(text, what):
    """Validate an unencrypted OpenSSH private key and return its public blob.

    An unencrypted key is required because cloud-init writes it to disk for
    sshd; a passphrase-protected key could not be used unattended.  The
    returned public blob allows a byte-for-byte consistency check against the
    matching public key file -- a comparison, not a signature check, so
    ``ssh-keygen`` remains the staging gate for the pair working in practice.
    """
    if not isinstance(text, str):
        raise GuestSeedError("%s must be text" % what)
    if len(text.encode("utf-8")) > MAX_KEY_BYTES:
        raise GuestSeedError("%s must be at most %d bytes" % (what, MAX_KEY_BYTES))
    lines = text.splitlines()
    if len(lines) < 3:
        raise GuestSeedError("%s is not an OpenSSH private key" % what)
    if lines[0] != "-----BEGIN OPENSSH PRIVATE KEY-----" or lines[-1] != "-----END OPENSSH PRIVATE KEY-----":
        raise GuestSeedError("%s must be a framed OpenSSH private key" % what)
    body = "".join(line.strip() for line in lines[1:-1])
    if not body:
        raise GuestSeedError("%s has no key body" % what)
    try:
        blob = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GuestSeedError("%s: key body is not valid base64" % what) from exc
    if not blob.startswith(OPENSSH_PRIVATE_MAGIC):
        raise GuestSeedError("%s must be an OpenSSH format private key" % what)
    offset = len(OPENSSH_PRIVATE_MAGIC)
    cipher, offset = _parse_ssh_string(blob, offset, what)
    kdf, offset = _parse_ssh_string(blob, offset, what)
    if cipher != b"none" or kdf != b"none":
        raise GuestSeedError("%s must be unencrypted (cipher and kdf must be 'none')" % what)
    _options, offset = _parse_ssh_string(blob, offset, what, allow_empty=True)
    if _options:
        raise GuestSeedError("%s: unencrypted KDF options must be empty" % what)
    if offset + 4 > len(blob):
        raise GuestSeedError("%s: truncated key count" % what)
    (count,) = struct.unpack_from(">I", blob, offset)
    offset += 4
    if count != 1:
        raise GuestSeedError("%s must contain exactly one key" % what)
    pub, _ = _parse_ssh_string(blob, offset, what)
    pub_type, pub_offset = _parse_ssh_string(pub, 0, what)
    if pub_type != ED25519_TYPE:
        raise GuestSeedError("%s: key type must be ssh-ed25519" % what)
    pub_key, pub_offset = _parse_ssh_string(pub, pub_offset, what)
    if len(pub_key) != 32 or pub_offset != len(pub):
        raise GuestSeedError("%s: embedded public key is not a bare ed25519 key" % what)
    return pub


# --------------------------------------------------------------------------- #
# seed
# --------------------------------------------------------------------------- #

def build_seed(zone, operator_public_key, ssh_host_private_key, ssh_host_public_key, *, client_models=None):
    """Return the seed as ``{"user-data": ..., "meta-data": ...}`` text.

    The seed is JSON, which is valid YAML, so no YAML library is needed.  It
    is deterministic: the same inputs always produce the same bytes, including
    the instance id, which is derived from the pinned host key.
    """
    zone = _checked_zone(zone)
    hostname = ZONE_HOSTNAME[zone]
    operator_line, _ = _check_ed25519_public_line(operator_public_key, "operator-public-key")
    host_public_line, host_public_blob = _check_ed25519_public_line(
        ssh_host_public_key, "ssh-host-public-key"
    )
    embedded_public_blob = _check_ed25519_private_text(ssh_host_private_key, "ssh-host-private-key")
    if embedded_public_blob != host_public_blob:
        raise GuestSeedError("ssh-host-private-key does not match ssh-host-public-key")

    host_private = ssh_host_private_key if ssh_host_private_key.endswith("\n") else ssh_host_private_key + "\n"
    instance_id = "%s-%s" % (hostname, hashlib.sha256(host_public_blob).hexdigest()[:12])

    cloud_config = {
        "hostname": hostname,
        "manage_etc_hosts": "localhost",
        "users": [
            {
                "name": AGENT_USER,
                "lock_passwd": True,
                "ssh_authorized_keys": [operator_line],
                "shell": "/bin/bash",
                "sudo": ["ALL=(ALL) NOPASSWD:ALL"],
            }
        ],
        "ssh_pwauth": False,
        "disable_root": True,
        "ssh_keys": {
            "ed25519_private": host_private,
            "ed25519_public": host_public_line,
        },
        "write_files": _seed_files(),
        "runcmd": [
            ["/usr/sbin/sshd", "-t"],
            ["systemctl", "daemon-reload"],
            ["systemctl", "restart", "ssh.service"],
            ["systemctl", "enable", "--now", *_UNIT_FILENAMES.values()],
        ],
        "package_update": False,
        "package_upgrade": False,
        "package_reboot_if_required": False,
        "final_message": "agent-zone %s seed applied" % zone,
    }
    if client_models is not None:
        client_files, directories = _client_files(zone, client_models)
        cloud_config["write_files"].extend(client_files)
        cloud_config["runcmd"].insert(0, ["install", "-d", "-m", "0700", "-o", AGENT_USER,
                                          "-g", AGENT_USER, *directories])
    user_data = "#cloud-config\n" + json.dumps(cloud_config, indent=2, sort_keys=True) + "\n"
    meta_data = json.dumps(
        {
            "instance-id": instance_id,
            "local-hostname": hostname,
            "zone": zone,
            "seed-version": SEED_VERSION,
        },
        indent=2,
        sort_keys=True,
    ) + "\n"

    for name, payload in (("user-data", user_data), ("meta-data", meta_data)):
        if len(payload.encode("utf-8")) > MAX_SEED_BYTES:
            raise GuestSeedError("seed %s exceeds %d bytes" % (name, MAX_SEED_BYTES))
    return {"user-data": user_data, "meta-data": meta_data}


def write_seed(zone, output, operator_public_key, ssh_host_private_key, ssh_host_public_key, *, client_models=None):
    """Read the key inputs, build the seed and write it into a fresh directory."""
    operator_text = _read_regular_file(operator_public_key, "operator-public-key", MAX_KEY_BYTES)
    private_text = _read_regular_file(ssh_host_private_key, "ssh-host-private-key", MAX_KEY_BYTES)
    host_public_text = _read_regular_file(ssh_host_public_key, "ssh-host-public-key", MAX_KEY_BYTES)
    try:
        seed = build_seed(
            zone,
            operator_text.decode("utf-8"),
            private_text.decode("utf-8"),
            host_public_text.decode("utf-8"),
            client_models=client_models,
        )
    except UnicodeDecodeError as exc:
        raise GuestSeedError("key inputs must be UTF-8 text") from exc

    directory = _prepare_output_dir(output)
    written = {}
    for name in ("user-data", "meta-data"):
        target = os.path.join(directory, name)
        if os.path.lexists(target):
            raise GuestSeedError("refusing to overwrite existing %s" % target)
        written[name] = _write_new_file(target, seed[name], _FILE_MODE)
    return directory, written


# --------------------------------------------------------------------------- #
# guest relay
# --------------------------------------------------------------------------- #

_RELAY_SOURCE = r"""#!/usr/bin/env python3
'''Bridge the two fixed agent-zone channels inside a guest.

Channel 1 (ssh): AF_VSOCK port 2222 -> TCP 127.0.0.1:22, so the host can
reach the guest's SSH daemon without the guest having any network interface.
Channel 2 (broker): TCP 127.0.0.1:5001 -> AF_VSOCK host CID 2 port 5001, so
a guest-local client can reach the host broker.

Addresses and ports are constants. There is no bind address, target host or URL
that a caller can supply, no shell, no subprocess and no environment lookup.
The relay fails closed when AF_VSOCK is unavailable.
'''
import argparse
import selectors
import signal
import socket
import sys
import threading

AF_VSOCK = getattr(socket, 'AF_VSOCK', None)
VMADDR_ANY = getattr(socket, 'VMADDR_CID_ANY', 0xffffffff)
VMADDR_CID_HOST = getattr(socket, 'VMADDR_CID_HOST', 2)

BUFFER_SIZE = 65536
MAX_CONNECTIONS = 32
CONNECT_TIMEOUT = 10.0
IDLE_TIMEOUT = 300.0
ACCEPT_POLL_SECONDS = 0.5
LISTEN_BACKLOG = 16

SSH_VSOCK_PORT = 2222
BROKER_VSOCK_PORT = 5001
SSH_TCP_ADDRESS = ('127.0.0.1', 22)
BROKER_TCP_ADDRESS = ('127.0.0.1', 5001)
BROKER_VSOCK_TARGET = (VMADDR_CID_HOST, BROKER_VSOCK_PORT)


def log(message):
    sys.stderr.write('[agent-zone-relay] %s\n' % (message,))
    sys.stderr.flush()


def pump(left, right, buffer_size=BUFFER_SIZE, idle_timeout=IDLE_TIMEOUT):
    '''Bounded full-duplex forwarding, preserving FIN after queued bytes drain.'''
    endpoints = {'left': left, 'right': right}
    opposite = {'left': 'right', 'right': 'left'}
    readable = set(endpoints)
    pending = {'left': bytearray(), 'right': bytearray()}
    shutdown = set()
    selector = selectors.DefaultSelector()
    for endpoint in endpoints.values():
        endpoint.setblocking(False)
    try:
        while readable or any(pending.values()):
            for name, endpoint in endpoints.items():
                target = opposite[name]
                if target not in readable and not pending[name] and name not in shutdown:
                    try:
                        endpoint.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    shutdown.add(name)
                events = 0
                if name in readable and len(pending[target]) < buffer_size:
                    events |= selectors.EVENT_READ
                if pending[name]:
                    events |= selectors.EVENT_WRITE
                try:
                    selector.get_key(endpoint)
                except KeyError:
                    if events:
                        selector.register(endpoint, events, name)
                else:
                    if events:
                        selector.modify(endpoint, events, name)
                    else:
                        selector.unregister(endpoint)
            events = selector.select(idle_timeout)
            if not events:
                raise TimeoutError('relay idle timeout')
            for key, mask in events:
                name = key.data
                endpoint = endpoints[name]
                target = opposite[name]
                if mask & selectors.EVENT_WRITE:
                    try:
                        sent = endpoint.send(pending[name])
                        if sent <= 0:
                            raise OSError('socket send made no progress')
                        del pending[name][:sent]
                    except BlockingIOError:
                        pass
                if mask & selectors.EVENT_READ:
                    try:
                        data = endpoint.recv(buffer_size - len(pending[target]))
                    except BlockingIOError:
                        continue
                    if data:
                        pending[target].extend(data)
                    else:
                        readable.discard(name)
        for endpoint in endpoints.values():
            try:
                endpoint.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    finally:
        selector.close()
        left.close()
        right.close()


def connect_tcp(address, timeout=CONNECT_TIMEOUT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(address)
    except BaseException:
        sock.close()
        raise
    return sock


def connect_vsock(cid, port, timeout=CONNECT_TIMEOUT):
    if AF_VSOCK is None:
        raise OSError('AF_VSOCK is unavailable')
    sock = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((cid, port))
    except BaseException:
        sock.close()
        raise
    return sock


def bind_vsock(port, backlog=LISTEN_BACKLOG):
    if AF_VSOCK is None:
        raise OSError('AF_VSOCK is unavailable')
    sock = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
    try:
        sock.bind((VMADDR_ANY, port))
        sock.listen(backlog)
    except BaseException:
        sock.close()
        raise
    return sock


def bind_tcp(address, port, backlog=LISTEN_BACKLOG):
    if address != '127.0.0.1':
        raise ValueError('only loopback may be bound')
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((address, port))
        sock.listen(backlog)
    except BaseException:
        sock.close()
        raise
    return sock


def serve(listener, connector, max_connections=MAX_CONNECTIONS,
          connect_timeout=CONNECT_TIMEOUT, idle_timeout=IDLE_TIMEOUT,
          buffer_size=BUFFER_SIZE, stop=None):
    '''Accept connections and hand each to its own pump until told to stop.

    Connections are independent: one slow or broken connection cannot stop
    another, and the number of live connections is bounded.
    '''
    listener.settimeout(ACCEPT_POLL_SECONDS)
    threads = set()
    lock = threading.Lock()
    while stop is None or not stop.is_set():
        try:
            accepted, _addr = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            if stop is not None and stop.is_set():
                break
            raise
        with lock:
            for finished in [thread for thread in threads if not thread.is_alive()]:
                threads.discard(finished)
            if len(threads) >= max_connections:
                log('connection limit reached, refusing one')
                try:
                    accepted.close()
                except OSError:
                    pass
                continue

            def handle(accepted=accepted, connector=connector):
                try:
                    peer = connector()
                except (OSError, ValueError) as exc:
                    log('connect failed: %s' % (exc,))
                    try:
                        accepted.close()
                    except OSError:
                        pass
                    return
                try:
                    pump(accepted, peer, buffer_size=buffer_size, idle_timeout=idle_timeout)
                except Exception as exc:  # noqa: BLE001 - one connection must not die loudly
                    log('connection closed: %s' % (exc,))

            thread = threading.Thread(target=handle, daemon=True)
            threads.add(thread)
            thread.start()
    with lock:
        for thread in threads:
            thread.join(timeout=1.0)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='agent-zone-relay', description=__doc__)
    parser.add_argument('mode', choices=('ssh', 'broker'))
    arguments = parser.parse_args(argv)
    if AF_VSOCK is None:
        log('AF_VSOCK is unavailable, refusing to start')
        return 3

    stop = threading.Event()

    def request_stop(signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    if arguments.mode == 'ssh':
        listener = bind_vsock(SSH_VSOCK_PORT)
        connector = lambda: connect_tcp(SSH_TCP_ADDRESS, timeout=CONNECT_TIMEOUT)
    else:
        listener = bind_tcp(*BROKER_TCP_ADDRESS)
        connector = lambda: connect_vsock(*BROKER_VSOCK_TARGET, timeout=CONNECT_TIMEOUT)
    try:
        serve(listener, connector, stop=stop)
    finally:
        try:
            listener.close()
        except OSError:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
"""

_RELAY_FILENAME = "agent-zone-relay.py"
_UNIT_FILENAMES = {
    "ssh": "agent-zone-ssh-relay.service",
    "broker": "agent-zone-broker-relay.service",
}


def relay_source():
    """Return the exact guest relay program that :func:`write_relay` installs."""
    return _RELAY_SOURCE


def relay_units():
    """Return units; the demonstrated Debian image autoloads vsock devices."""
    common = """[Unit]
Description=Agent zone %s relay (%s)
Documentation=https://github.com/rust-vmm/vhost-device/tree/main/vhost-device-vsock
After=ssh.service
Wants=ssh.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/lib/agent-zone-relay.py %s
Restart=always
RestartSec=2
# Fixed constants only: no environment, no operator-supplied addresses.
Environment=
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_VSOCK
RestrictNamespaces=yes
RestrictRealtime=yes
LockPersonality=yes
MemoryDenyWriteExecute=no
CapabilityBoundingSet=
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
"""
    return {
        _UNIT_FILENAMES["ssh"]: common % ("ssh", "AF_VSOCK 2222 to 127.0.0.1:22", "ssh"),
        _UNIT_FILENAMES["broker"]: common % ("broker", "127.0.0.1:5001 to vsock CID 2 port 5001", "broker"),
    }


_SSHD_CONFIG = """ListenAddress 127.0.0.1
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowTcpForwarding no
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
DisableForwarding yes
"""


def _seed_files():
    files = {"/etc/ssh/sshd_config.d/00-agent-zone.conf": (_SSHD_CONFIG, "0644"),
             "/usr/local/lib/agent-zone-relay.py": (relay_source(), "0755")}
    files.update({"/etc/systemd/system/" + name: (text, "0644")
                  for name, text in relay_units().items()})
    return [{"path": path, "owner": "root:root", "permissions": mode,
             "content": text} for path, (text, mode) in files.items()]


def _client_files(zone, models):
    """Guest defaults express a host-approved mapping; they grant no authority."""
    keys = {"local"} if zone == "confidential" else {"local", "codex", "anthropic"}
    if not isinstance(models, dict) or set(models) != keys:
        raise GuestSeedError("client_models must have exactly %s for %s" % (sorted(keys), zone))
    if any(not isinstance(value, str) or not _CLIENT_MODEL_ID.fullmatch(value)
           for value in models.values()):
        raise GuestSeedError("client model IDs must be 1..256 inert ASCII model-ID characters")

    files = []
    directories = ["/home/agent/.pi", "/home/agent/.pi/agent", "/etc/agent-zone"]

    def add(path, content):
        if not isinstance(content, str):
            content = json.dumps(content, indent=2, sort_keys=True) + "\n"
        files.append({"path": path, "owner": "agent:agent", "permissions": "0600",
                      "defer": True, "content": content})

    add("/etc/agent-zone/client-models.json", models)
    add("/home/agent/.pi/agent/settings.json", {"defaultProvider": "sandbox-broker",
                                              "defaultModel": models["local"]})
    add("/home/agent/.pi/agent/models.json", {"providers": {"sandbox-broker": {
        "baseUrl": "http://127.0.0.1:5001/v1", "api": "openai-completions",
        "apiKey": "dummy-local-capability", "authHeader": True,
        "models": [{"id": models["local"], "name": "Approved local model", "input": ["text"],
                    "reasoning": False, "contextWindow": 16384, "maxTokens": 2048,
                    "compat": {"supportsStore": True, "supportsUsageInStreaming": True,
                               "maxTokensField": "max_tokens", "supportsStrictMode": False}}]}}})
    environment = {"PI_OFFLINE": "1"}
    if zone == "internal":
        directories.extend(["/home/agent/.codex", "/home/agent/.claude"])
        model = models["codex"]
        catalog = {"models": [{"slug": model, "display_name": "Approved Codex model",
            "description": "Host broker model", "base_instructions": "You are a coding assistant.",
            "shell_type": "shell_command", "visibility": "list", "supported_in_api": True,
            "priority": 0, "supported_reasoning_levels": [], "support_verbosity": False,
            "supports_reasoning_summaries": True, "supports_parallel_tool_calls": True,
            "apply_patch_tool_type": "freeform", "context_window": 8192,
            "truncation_policy": {"mode": "tokens", "limit": 1000},
            "input_modalities": ["text"], "experimental_supported_tools": []}]}
        add("/home/agent/.codex/models.json", catalog)
        # The pinned custom Responses provider already sends store=false.
        add("/home/agent/.codex/config.toml", 'model = %s\n' % json.dumps(model) + '''model_provider = "sandbox-broker"
model_catalog_json = "/home/agent/.codex/models.json"
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
name = "Guest sandbox broker"
base_url = "http://127.0.0.1:5001/codex/v1"
wire_api = "responses"
experimental_bearer_token = "dummy-local-capability"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
[model_providers.sandbox-broker.capabilities]
remote_compaction = "unsupported"
external_web_access = false
''')
        anthropic = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:5001/claude",
            "ANTHROPIC_AUTH_TOKEN": "dummy-local-capability",
            "ANTHROPIC_MODEL": models["anthropic"],
            "ANTHROPIC_SMALL_FAST_MODEL": models["anthropic"],
            "ANTHROPIC_DEFAULT_OPUS_MODEL": models["anthropic"],
            "ANTHROPIC_DEFAULT_SONNET_MODEL": models["anthropic"],
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": models["anthropic"],
            "CLAUDE_CODE_SUBAGENT_MODEL": models["anthropic"],
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1"}
        add("/home/agent/.claude/settings.json", {"model": models["anthropic"], "env": anthropic,
            "permissions": {"deny": ["WebFetch", "WebSearch"]}, "enableAllProjectMcpServers": False})
        environment.update(anthropic)
    # Fixed literals and restricted ASCII IDs are safe as shell single quotes.
    files.append({"path": "/etc/profile.d/agent-zone-clients.sh", "owner": "root:root",
                  "permissions": "0644", "content": "".join(
                      "export %s='%s'\n" % (name, value) for name, value in sorted(environment.items()))})
    return files, directories


def write_relay(output):
    """Write the relay program and its units into a private staging directory."""
    directory = _prepare_output_dir(output)
    written = {}
    payload = {_RELAY_FILENAME: relay_source()}
    payload.update(relay_units())
    for name, text in payload.items():
        target = os.path.join(directory, name)
        if os.path.lexists(target):
            raise GuestSeedError("refusing to overwrite existing %s" % target)
        mode = 0o755 if name.endswith(".py") else 0o644
        written[name] = _write_new_file(target, text, mode)
    return directory, written


# --------------------------------------------------------------------------- #
# image pin
# --------------------------------------------------------------------------- #

def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GuestSeedError("descriptor contains a duplicate key %r" % (key,))
        result[key] = value
    return result


def _check_descriptor(text):
    if len(text.encode("utf-8")) > MAX_DESCRIPTOR_BYTES:
        raise GuestSeedError("descriptor exceeds %d bytes" % MAX_DESCRIPTOR_BYTES)
    try:
        descriptor = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise GuestSeedError("descriptor is not valid JSON: %s" % (exc,)) from exc
    if not isinstance(descriptor, dict):
        raise GuestSeedError("descriptor must be a JSON object")
    if tuple(sorted(descriptor)) != tuple(sorted(_DESCRIPTOR_KEYS)):
        raise GuestSeedError(
            "descriptor must have exactly the keys %s" % ", ".join(sorted(_DESCRIPTOR_KEYS))
        )
    if descriptor["version"] != 1 or isinstance(descriptor["version"], bool):
        raise GuestSeedError("descriptor version must be 1")
    if descriptor["distribution"] != "debian":
        raise GuestSeedError("descriptor distribution must be debian")
    release = descriptor["release"]
    if not isinstance(release, str) or release not in RELEASE_SUITE:
        raise GuestSeedError("descriptor release must be one of %s" % ", ".join(sorted(RELEASE_SUITE)))
    architecture = descriptor["architecture"]
    if architecture not in ARCHITECTURES:
        raise GuestSeedError("descriptor architecture must be one of %s" % ", ".join(ARCHITECTURES))
    if descriptor["format"] != "raw":
        raise GuestSeedError("descriptor format must be raw")
    digest = descriptor["sha256"]
    if not isinstance(digest, str) or not _HEX64_RE.match(digest):
        raise GuestSeedError("descriptor sha256 must be 64 lowercase hexadecimal characters")
    _check_source_url(descriptor["source_url"], release, architecture)
    return descriptor


def _check_source_url(url, release, architecture):
    if not isinstance(url, str) or len(url) > 2048:
        raise GuestSeedError("descriptor source_url must be a short string")
    prefix = "https://%s/images/cloud/" % DEBIAN_CLOUD_ORIGIN
    if not url.startswith(prefix):
        raise GuestSeedError("descriptor source_url must be an official %s cloud build URL" % DEBIAN_CLOUD_ORIGIN)
    if "?" in url or "#" in url or "@" in url or ".." in url:
        raise GuestSeedError("descriptor source_url must be a plain path without query, fragment or traversal")
    rest = url[len(prefix):]
    parts = rest.split("/")
    if len(parts) != 3 or not all(parts):
        raise GuestSeedError("descriptor source_url must name suite, snapshot and file")
    suite, snapshot, filename = parts
    if suite != RELEASE_SUITE[release]:
        raise GuestSeedError("descriptor source_url suite must be %s for release %s" % (RELEASE_SUITE[release], release))
    if snapshot in ("latest", "daily") or not _SNAPSHOT_RE.match(snapshot):
        raise GuestSeedError("descriptor source_url must name an immutable dated snapshot, not latest or daily")
    if architecture not in filename:
        raise GuestSeedError("descriptor source_url file must match architecture %s" % architecture)
    if not filename.endswith(".raw"):
        raise GuestSeedError("descriptor source_url file must be a raw image")
    return url


def verify_image(image, descriptor):
    """Hash ``image`` in bounded chunks and compare it with the pinned digest.

    Returns a bounded summary dict.  The descriptor is a reviewed input: this
    proves the local file matches it, not that the descriptor is authentic.
    """
    image = _checked_path(image, "image")
    descriptor_path = _checked_path(descriptor, "descriptor")
    descriptor_text = _read_regular_file(descriptor, "descriptor", MAX_DESCRIPTOR_BYTES).decode("utf-8")
    pin = _check_descriptor(descriptor_text)

    descriptor_fd = _open_regular(image, "image")
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor_fd)
        while True:
            chunk = os.read(descriptor_fd, min(HASH_CHUNK_BYTES, before.st_size + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > before.st_size:
                raise GuestSeedError("image grew during verification")
            digest.update(chunk)
        after = os.fstat(descriptor_fd)
        if (size != before.st_size or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns):
            raise GuestSeedError("image changed during verification")
    finally:
        os.close(descriptor_fd)
    if size == 0:
        raise GuestSeedError("image is empty")
    computed = digest.hexdigest()
    matches = computed == pin["sha256"]
    summary = {
        "verified": matches,
        "distribution": pin["distribution"],
        "release": pin["release"],
        "architecture": pin["architecture"],
        "format": pin["format"],
        "size_bytes": size,
        "sha256": computed,
        "expected_sha256": pin["sha256"],
        "source_url": pin["source_url"],
        "image": image,
    }
    return summary, 0 if matches else 1


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #

def _parser():
    parser = argparse.ArgumentParser(
        prog="guest",
        description="Offline guest seed, relay and image pin tools for the AI sandbox zones.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    seed = subcommands.add_parser("seed", help="write a NoCloud seed directory")
    seed.add_argument("--zone", required=True, choices=ZONES)
    seed.add_argument("--output", required=True)
    seed.add_argument("--operator-public-key", required=True)
    seed.add_argument("--ssh-host-private-key", required=True)
    seed.add_argument("--ssh-host-public-key", required=True)
    seed.add_argument("--client-models", help="bounded JSON model-ID mapping for the selected zone")

    relay = subcommands.add_parser("relay", help="write the guest relay program and units")
    relay.add_argument("--output", required=True)

    verify = subcommands.add_parser("verify-image", help="verify a local image against a pin")
    verify.add_argument("--image", required=True)
    verify.add_argument("--descriptor", required=True)
    return parser


def main(argv=None):
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "seed":
            client_models = None
            if arguments.client_models is not None:
                try:
                    text = _read_regular_file(arguments.client_models, "client-models", MAX_CLIENT_MODELS_BYTES)
                    client_models = json.loads(text.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise GuestSeedError("client-models must be UTF-8 JSON") from exc
                if client_models is None:
                    raise GuestSeedError("client-models file must contain a model-ID mapping")
            directory, written = write_seed(
                arguments.zone,
                arguments.output,
                arguments.operator_public_key,
                arguments.ssh_host_private_key,
                arguments.ssh_host_public_key,
                client_models=client_models,
            )
            print(json.dumps({"seed_dir": directory, "written": written}, sort_keys=True))
            return 0
        if arguments.command == "relay":
            directory, written = write_relay(arguments.output)
            print(json.dumps({"relay_dir": directory, "written": written}, sort_keys=True))
            return 0
        summary, code = verify_image(arguments.image, arguments.descriptor)
        print(json.dumps(summary, sort_keys=True))
        return code
    except GuestSeedError as exc:
        sys.stderr.write("guest: %s\n" % (exc,))
        return 1


if __name__ == "__main__":
    sys.exit(main())
