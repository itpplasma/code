"""Unprivileged fixed-zone SSH client; no caller-selected host configuration."""
from __future__ import annotations

import json
import os
import pwd
import shlex
import stat
import subprocess
import sys

from ai_zones.policy import PolicyError, assert_protected, assert_no_mutating_acl

ZONES = ("internal", "confidential")
PREFIXES = {"darwin": "/Library/AgentSandbox", "linux": "/opt/agent-sandbox"}
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}


class ClientError(Exception):
    """Operator input or protected installation record was refused."""


def parse_args(argv):
    if not argv or argv[0] not in ZONES:
        raise ClientError("usage: ai internal|confidential [-- COMMAND [ARGS...]]")
    if len(argv) == 1:
        return argv[0], []
    if len(argv) < 3 or argv[1] != "--" or not argv[2]:
        raise ClientError("remote commands require -- COMMAND [ARGS...]")
    if len(argv) > 258 or sum(len(item) for item in argv) > 65536:
        raise ClientError("remote command exceeds bound")
    if any("\x00" in item for item in argv):
        raise ClientError("NUL in remote command")
    return argv[0], argv[2:]


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ClientError("duplicate operator record key")
        result[key] = value
    return result


def operator_record(prefix, uid=None):
    uid = os.getuid() if uid is None else uid
    path = prefix + "/config/operator.json"
    assert_protected(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ClientError("operator record is not a single-link regular file")
        raw = os.read(fd, 8193)
        after = os.fstat(fd)
        if len(raw) > 8192 or before.st_size != len(raw) or (
                before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns):
            raise ClientError("operator record exceeds bound or changed")
    finally:
        os.close(fd)
    record = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    if (not isinstance(record, dict) or set(record) != {"version", "operator", "zones"} or
            type(record["version"]) is not int or record["version"] != 1 or
            record["zones"] != list(ZONES)):
        raise ClientError("unexpected operator record schema")
    operator = record["operator"]
    if not isinstance(operator, dict) or set(operator) != {"name", "uid", "private_dir"}:
        raise ClientError("unexpected operator identity schema")
    actual = pwd.getpwuid(uid)
    if (uid == 0 or type(operator["uid"]) is not int or operator["uid"] != uid or
            operator["name"] != actual.pw_name or
            operator["private_dir"] != actual.pw_dir + "/.ssh/agent-sandbox"):
        raise ClientError("real user differs from protected operator identity")
    return operator


def private_key(operator, zone):
    if zone not in ZONES:
        raise ClientError("unknown zone")
    directory = operator["private_dir"]
    if (not isinstance(directory, str) or not directory.startswith("/") or
            os.path.normpath(directory) != directory or directory.startswith("//") or
            any(ord(char) < 32 or ord(char) == 127 for char in directory)):
        raise ClientError("invalid recorded private key directory")
    # Home is user-owned by design; no component may redirect through a symlink.
    current = "/"
    for component in directory.strip("/").split("/"):
        current = current.rstrip("/") + "/" + component
        entry = os.lstat(current)
        if not stat.S_ISDIR(entry.st_mode) or entry.st_mode & 0o022:
            raise ClientError("private key ancestor is a symlink or writable by others")
        assert_no_mutating_acl(current)
    st = os.lstat(directory)
    if st.st_uid != operator["uid"] or stat.S_IMODE(st.st_mode) != 0o700:
        raise ClientError("private key directory must be operator-owned 0700")
    path = directory + "/" + zone + "_ed25519"
    st = os.lstat(path)
    if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or
            st.st_uid != operator["uid"] or stat.S_IMODE(st.st_mode) != 0o600):
        raise ClientError("private key must be operator-owned single-link 0600")
    assert_no_mutating_acl(path)
    return path


def ssh_argv(prefix, zone, key, remote=()):
    """Pure argv builder; prefix/key come only from the protected record."""
    if prefix not in PREFIXES.values() or zone not in ZONES:
        raise ClientError("unknown installation or zone")
    pin = prefix + "/pins/" + zone + ".known_hosts"
    proxy = "/usr/bin/sudo -n " + prefix + "/bin/admin proxy " + zone
    argv = ["/usr/bin/ssh", "-F", "/dev/null", "-T" if remote else "-t",
            "-o", "IdentityAgent=none", "-o", "IdentitiesOnly=yes",
            "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
            "-o", "ClearAllForwardings=yes", "-o", "PermitLocalCommand=no",
            "-o", "StrictHostKeyChecking=yes", "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "UserKnownHostsFile=" + pin, "-o", "HostKeyAlias=" + zone,
            "-o", "UpdateHostKeys=no", "-o", "ControlMaster=no", "-o", "ControlPath=none",
            "-o", "ProxyCommand=" + proxy, "-o", "RequestTTY=no" if remote else "RequestTTY=auto",
            "-i", key, "agent@" + zone]
    if remote:
        argv.append(shlex.join(remote))
    return argv


def client(zone, remote, *, runner=subprocess.run, uid=None):
    platform_name = sys.platform
    if platform_name not in PREFIXES:
        raise ClientError("unsupported platform")
    prefix = PREFIXES[platform_name]
    operator = operator_record(prefix, uid)
    key = private_key(operator, zone)
    assert_protected(prefix + "/pins/" + zone + ".known_hosts")
    # No HOME, SSH_AUTH_SOCK, Python paths or user-selected SSH configuration.
    start = runner(["/usr/bin/sudo", "-n", prefix + "/bin/admin", "start", zone],
                   env=ENV, cwd="/", check=False)
    if start.returncode:
        raise ClientError("zone start refused by the protected host administrator")
    result = runner(ssh_argv(prefix, zone, key, remote), env=ENV, cwd="/", check=False)
    return result.returncode


def main(argv=None):
    try:
        zone, remote = parse_args(list(sys.argv[1:] if argv is None else argv))
        return client(zone, remote)
    except (ClientError, PolicyError, OSError, ValueError, KeyError) as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
