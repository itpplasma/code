"""Offline disposable Debian tool-image recipe, never a VM launcher.

The checked-in lock is a review proposal, not an approval or signed image.
``recipe`` emits a one-use NoCloud seed which installs only inside the disposable
guest. ``build-argv`` is a separate networked adapter; production vm.py remains
NIC-free. Neither command executes downloaded code or guest commands on the host.
An actual build, NIC-free first boot on each platform, and signed promotion are
still required. Exact artifact bytes and package inputs are reproducible; disk
bytes (filesystem UUIDs, timestamps and logs) are not claimed reproducible.

Run with PYTHONPATH=sandbox, e.g. python3 -m ai_zones.image_builder validate-lock
--lock sandbox/guest-tools.lock.json. The other commands expose --help. A build
requires a fresh private disposable-image-build directory, a verified copy of
the locked raw image expanded to 64 GiB, a new temporary ed25519 public key and
a cidata ISO containing recipe's two files. build-argv emits the native
hypervisor command, without executing it. After shutdown, discard the seed,
temporary private key, EFI variables and adapter logs; inspect
the disk and its manifest offline before copying root.raw to its matching zone.
Recipe and manifest inspection require --zone internal or --zone confidential.
Internal images contain Pi, Codex and Claude; confidential images contain Pi only.
An unsigned manifest establishes consistency, never tool or image provenance.
Production identities come only from guest.build_seed, separately per instance.
The native Claude artifact is tied to an upstream release-repository commit;
that public repository is not the source of its proprietary binary.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .guest import _check_ed25519_public_line, _seed_files
from .vm import _checked_path


class ImageBuildError(ValueError):
    """The proposed image input is incomplete or unsafe."""


VERSIONS = {"pi": "1.4.0", "codex": "0.165.0", "claude": "2.1.310"}
BASE_PACKAGES = {"cloud-init", "openssh-server", "sudo", "python3",
                 "ca-certificates", "curl", "xz-utils"}
INSTALL_PACKAGES = {"git", "ripgrep"}
ARCHES = {"arm64": ("arm64", "aarch64"), "amd64": ("x64", "x86_64")}
ZONE_TOOLS = {"internal": {"pi", "codex", "claude"}, "confidential": {"pi"}}


def _baked_files():
    return {item["path"].lstrip("/"): item["content"] for item in _seed_files()}


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ImageBuildError(label + " has missing or unknown fields")


def _integrity(value):
    if not isinstance(value, str):
        raise ImageBuildError("integrity must be text")
    if re.fullmatch(r"sha(?:256:[0-9a-f]{64}|512:[0-9a-f]{128})", value):
        return
    if value.startswith("sha512-"):
        try:
            blob = base64.b64decode(value[7:], validate=True)
            if len(blob) == 64 and base64.b64encode(blob).decode() == value[7:]:
                return
        except ValueError:
            pass
    raise ImageBuildError("a complete SHA256 or SHA512 integrity pin is required")


def _package_map(value, expected=None):
    if not isinstance(value, dict) or not value or (expected and set(value) != expected):
        raise ImageBuildError("package set differs from the fixed recipe")
    for name, version in value.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.:~-]*", name)
                or not isinstance(version, str)
                or not re.fullmatch(r"[0-9][A-Za-z0-9+.~:~-]*", version)):
            raise ImageBuildError("invalid exact Debian package/version")


def validate_lock(lock):
    """Strict shape, official origins, architecture, version and integrity checks.

    This validates a proposed input, not its author, approval or signatures.
    No field can inject a command, repository, archive path or alternate tool.
    """
    _keys(lock, ("version", "status", "distribution", "release", "snapshot", "architectures"), "lock")
    if (type(lock["version"]) is not int or lock["version"] != 1
            or lock["status"] != "proposal" or lock["distribution"] != "debian"
            or lock["release"] != "13"):
        raise ImageBuildError("only Debian 13 proposal lock version 1 is accepted")
    if not isinstance(lock["snapshot"], str) or not re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", lock["snapshot"]):
        raise ImageBuildError("immutable Debian snapshot timestamp required")
    _keys(lock["architectures"], ARCHES, "architectures")
    for arch, (cpu, rust) in ARCHES.items():
        entry = lock["architectures"][arch]
        _keys(entry, ("base", "package_index", "packages", "tools"), arch)
        base = entry["base"]
        _keys(base, ("url", "integrity", "package_manifest_url", "package_manifest_integrity"), "base")
        pattern = (r"https://cloud\.debian\.org/images/cloud/trixie/"
                   r"(20[0-9]{6}-[0-9]+)/debian-13-genericcloud-" + arch + r"-\1\.raw")
        if not isinstance(base["url"], str) or not re.fullmatch(pattern, base["url"]):
            raise ImageBuildError("immutable official Debian image for the correct architecture required")
        if base["package_manifest_url"] != base["url"][:-4] + ".json":
            raise ImageBuildError("package manifest must belong to the pinned base")
        _integrity(base["integrity"])
        _integrity(base["package_manifest_integrity"])
        if not base["integrity"].startswith("sha512:") or not base["package_manifest_integrity"].startswith("sha512:"):
            raise ImageBuildError("official Debian SHA512 image/manifest checksums required")
        index = entry["package_index"]
        _keys(index, ("url", "integrity"), "package index")
        if index["url"] != ("https://snapshot.debian.org/archive/debian/" + lock["snapshot"]
                            + "/dists/trixie/main/binary-" + arch + "/Packages.xz"):
            raise ImageBuildError("package index must use the same snapshot and architecture")
        _integrity(index["integrity"])
        _keys(entry["packages"], ("base", "install"), "packages")
        _package_map(entry["packages"]["base"], BASE_PACKAGES)
        _package_map(entry["packages"]["install"], INSTALL_PACKAGES)
        _keys(entry["tools"], VERSIONS, "tools")
        for name, version in VERSIONS.items():
            tool = entry["tools"][name]
            _keys(tool, ("version", "release_commit", "url", "integrity", "member"), name)
            if tool["version"] != version:
                raise ImageBuildError(name + ": exact requested version is unavailable; do not substitute")
            if not isinstance(tool["release_commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", tool["release_commit"]):
                raise ImageBuildError("exact upstream release commit required")
            if name == "pi":
                url = f"https://github.com/earendil-works/pi/releases/download/v{version}/pi-linux-{cpu}.tar.gz"
                member = "pi/pi"
            elif name == "codex":
                member = f"codex-{rust}-unknown-linux-musl"
                url = f"https://github.com/openai/codex/releases/download/rust-v{version}/{member}.tar.gz"
            else:
                url = (f"https://registry.npmjs.org/@anthropic-ai/claude-code-linux-{cpu}/-/"
                       f"claude-code-linux-{cpu}-{version}.tgz")
                member = "package/claude"
            if tool["url"] != url or tool["member"] != member:
                raise ImageBuildError(name + ": incorrect official artifact or architecture")
            _integrity(tool["integrity"])
    return lock


def load_lock(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ImageBuildError("duplicate JSON field: " + key)
            result[key] = value
        return result
    try:
        blob = Path(path).read_bytes()
        if not 0 < len(blob) <= 128 * 1024:
            raise ImageBuildError("lock must contain at most 128 KiB")
        return validate_lock(json.loads(blob, object_pairs_hook=unique))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImageBuildError(str(exc)) from exc


def lock_digest(lock):
    return hashlib.sha256(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# Only the disposable guest invokes build(); offline inspection uses its data checks.
GUEST_PROGRAM = r'''#!/usr/bin/python3
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import traceback
import tarfile
import urllib.request

ZONE_TOOLS = {"internal": {"pi", "codex", "claude"}, "confidential": {"pi"}}
ABSENT_PATHS = (
    "var/lib/cloud", "var/lib/agent-image-build", "var/lib/dhcp", "var/lib/NetworkManager",
    "var/cache/apt/archives", "var/lib/apt/lists", "etc/cloud/cloud.cfg.d/90-builder.cfg",
    "etc/sudoers.d/90-cloud-init-users", "etc/ssh/sshd_config.d/00-builder.conf",
    "etc/agent-zone-disposable-builder", "var/lib/systemd/random-seed", "var/lib/dbus/machine-id",
    "etc/passwd-", "etc/shadow-", "etc/group-", "etc/gshadow-", "etc/subuid-", "etc/subgid-",
    "var/backups/passwd.bak", "var/backups/shadow.bak", "var/backups/group.bak", "var/backups/gshadow.bak",
    "var/lib/agent-image-build.log",
)
EMPTY_PATHS = ("root", "home", "var/log", "tmp", "var/tmp")
EMPTY_MODES = {"root": 0o700, "home": 0o755, "var/log": 0o755, "tmp": 0o1777, "var/tmp": 0o1777}
DIRECTORY_TARGETS = EMPTY_PATHS + ("etc/ssh", "etc/netplan", "etc/systemd/network")
INSPECTED_FILES = ("etc/passwd", "etc/shadow", "etc/group", "etc/gshadow", "etc/subuid", "etc/subgid",
                   "etc/machine-id")
SCRUB_TARGETS = ABSENT_PATHS + DIRECTORY_TARGETS + INSPECTED_FILES
FREEZE_WRITERS = (
    "systemd-random-seed.service", "systemd-journald.service", "systemd-journald.socket",
    "systemd-journald-dev-log.socket", "systemd-journald-audit.socket",
)


def digest_file(path, algorithm="sha256"):
    digest = hashlib.new(algorithm)
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest


def verify_artifact(path, integrity):
    if integrity.startswith("sha512-"):
        expected = base64.b64decode(integrity[7:], validate=True)
        actual = digest_file(path, "sha512").digest()
    else:
        algorithm, expected = integrity.split(":", 1)
        actual = digest_file(path, algorithm).hexdigest()
    if actual != expected:
        raise ValueError("artifact integrity mismatch")


def download(spec, target):
    request = urllib.request.Request(spec["url"], headers={"User-Agent": "agent-image-builder/1"})
    with urllib.request.urlopen(request, timeout=120) as response, open(target, "xb") as sink:
        shutil.copyfileobj(response, sink, 1024 * 1024)
    verify_artifact(target, spec["integrity"])


def unpack(archive, destination):
    """Extract data only, never archive hooks, links, owners or special files."""
    destination = Path(destination)
    destination.mkdir(mode=0o755)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if len(members) > 50000 or sum(m.size for m in members) > 2 * 1024**3:
            raise ValueError("archive exceeds fixed limits")
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (not member.name or path.is_absolute() or ".." in path.parts
                    or member.name in names or not (member.isfile() or member.isdir())):
                raise ValueError("unsafe archive member")
            names.add(member.name)
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as content, open(target, "xb") as sink:
                    shutil.copyfileobj(content, sink)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)


def packages():
    result = subprocess.check_output(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"], text=True)
    return dict(line.split("\t", 1) for line in result.splitlines())


def check_packages(config, actual, installed):
    for group in ("base", "install") if installed else ("base",):
        for name, version in config["packages"][group].items():
            if actual.get(name) != version:
                raise ValueError("unexpected package version: " + name)


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def mounts():
    path = Path("/proc/self/mountinfo")
    if not path.exists() and os.uname().sysname != "Linux":
        return []
    result = []
    for line in path.read_text().splitlines():
        left, right = line.split(" - ", 1)
        fields = left.split()
        # Kernel mountinfo escapes whitespace and backslashes with octal digits.
        decode = lambda text: __import__("re").sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), text)
        result.append((decode(fields[4]), decode(fields[3]), right.split()[0]))
    return result


def expose_persistent_tmp(root):
    require_scrub_ancestors(root)
    target = str(Path(root) / "tmp")
    entries = mounts()
    selected = [entry for entry in entries if entry[0] == target]
    if selected:
        if (len(selected) != 1 or selected[0][1:] != ("/", "tmpfs")
                or any(entry[0].startswith(target + "/") for entry in entries)):
            raise ValueError("unexpected or nested temporary mount")
        # Do not use lazy unmount: the persistent directory must be visible now.
        subprocess.run(["systemctl", "mask", "--runtime", "tmp.mount"], check=True)
        subprocess.run(["umount", "--", target], check=True)
    if any(entry[0] == target or entry[0].startswith(target + "/") for entry in mounts()):
        raise ValueError("temporary mount still hides the persistent directory")


def require_scrub_ancestors(root):
    root = Path(root)
    if not root.is_absolute() or os.path.normpath(str(root)) != str(root) or not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError("supplied image root must be a real normalized directory")
    for relative in SCRUB_TARGETS:
        parts = Path(relative).parts
        if relative not in DIRECTORY_TARGETS:
            parts = parts[:-1]
        path = root
        for part in parts:
            path = path / part
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                break
            if not stat.S_ISDIR(mode):
                raise ValueError("scrub ancestor must be a real directory: " + relative)


def require_unmounted_scrub_paths(root):
    base = str(Path(root))
    targets = [str(Path(root) / relative) for relative in SCRUB_TARGETS]
    for mounted, unused_root, unused_type in mounts():
        # The supplied root's backing mount is expected; only inspect its children.
        if mounted == base or not mounted.startswith(base.rstrip("/") + "/"):
            continue
        if any(mounted == target or mounted.startswith(target + "/")
               or target.startswith(mounted.rstrip("/") + "/") for target in targets):
            raise ValueError("mount hides a persistent scrub target")


def clear_directory(path, mode):
    """Keep the directory inode; unlink children without following their links."""
    path.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)

    def clear(parent):
        parent_stat = os.fstat(parent)
        for name in os.listdir(parent):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                dir_fd=parent)
                try:
                    opened = os.fstat(child)
                    if opened.st_dev != parent_stat.st_dev or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError("nested mount or changed directory during scrub")
                    clear(child)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=parent)
            else:
                os.unlink(name, dir_fd=parent)

    try:
        clear(descriptor)
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def build_event(message, persist=True):
    data = (message.rstrip() + "\n").encode()
    if persist:
        descriptor = os.open("/var/lib/agent-image-build.log",
                             os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise ValueError("build log must be a privately owned regular file")
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    try:
        print(message, flush=True)
    except OSError:
        pass
    for device in ("/dev/hvc0", "/dev/ttyS0"):
        try:
            descriptor = os.open(device, os.O_WRONLY | os.O_NOCTTY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if stat.S_ISCHR(os.fstat(descriptor).st_mode):
                    os.write(descriptor, data)
            finally:
                os.close(descriptor)
        except OSError:
            pass


def scrub(root):
    """Remove disposable identity before any image is frozen or copied."""
    root = Path(root)
    require_scrub_ancestors(root)
    require_unmounted_scrub_paths(root)
    for relative in ABSENT_PATHS:
        remove(root / relative)
    for relative in EMPTY_PATHS:
        clear_directory(root / relative, EMPTY_MODES[relative])
    for path in (root / "etc/ssh").glob("ssh_host_*"):
        remove(path)
    for path in (root / "etc/netplan").glob("*.yaml"):
        remove(path)
    for path in (root / "etc/systemd/network").glob("*"):
        remove(path)
    remove(root / "etc/machine-id")
    (root / "etc/machine-id").write_text("")


def frozen_state(root):
    root = Path(root)
    require_scrub_ancestors(root)
    require_unmounted_scrub_paths(root)
    if any((root / p).exists() or (root / p).is_symlink() for p in ABSENT_PATHS):
        raise ValueError("disposable identity/cache remains")
    for relative in ("etc/passwd", "etc/shadow", "etc/group", "etc/gshadow", "etc/subuid", "etc/subgid"):
        path = root / relative
        if path.is_symlink() or (path.exists() and any(
                line.split(":", 1)[0] == "builder" for line in path.read_text().splitlines())):
            raise ValueError("build account remains")
    if ((root / "etc/machine-id").is_symlink()
            or (root / "etc/machine-id").read_bytes() != b""
            or any((root / p).is_symlink() or not (root / p).is_dir()
                   or stat.S_IMODE((root / p).stat().st_mode) != EMPTY_MODES[p]
                   or any((root / p).iterdir()) for p in EMPTY_PATHS)
            or any((root / "etc/ssh").glob("ssh_host_*"))
            or any((root / "etc/netplan").glob("*.yaml"))
            or any((root / "etc/systemd/network").glob("*"))):
        raise ValueError("build identity or network configuration remains")
    return "scrubbed"


def prepare_freeze(root):
    require_scrub_ancestors(root)
    # v257 random-seed ExecStop saves state; complete it before deleting the seed.
    # Runtime masks prevent socket activation from reopening persistent logs.
    subprocess.run(["systemctl", "stop", "ssh.service", "ssh.socket", "systemd-networkd.service",
                    "systemd-resolved.service", "systemd-timesyncd.service", "cron.service",
                    "apt-daily.timer", "apt-daily-upgrade.timer"], check=False)
    subprocess.run(["systemctl", "mask", "--runtime", "--now", *FREEZE_WRITERS], check=True)
    for unit in FREEZE_WRITERS:
        state = subprocess.check_output(["systemctl", "show", "--property=ActiveState", "--value", unit],
                                        text=True).strip()
        if state != "inactive":
            raise ValueError("freeze writer is still active: " + unit)
    expose_persistent_tmp(root)
    require_unmounted_scrub_paths(root)
    subprocess.run(["userdel", "builder"], check=True)
    scrub(root)
    return frozen_state(root)


def install_tools(config, zone, work, prefix, launchers):
    if zone not in ZONE_TOOLS:
        raise ValueError("explicit build zone required")
    prefix.mkdir(mode=0o755)
    versions = {}
    for name in sorted(ZONE_TOOLS[zone]):
        spec = config["tools"][name]
        archive = work / (name + ".tar.gz")
        download(spec, archive)
        directory = prefix / name
        unpack(archive, directory)
        binary = directory / spec["member"]
        if not binary.is_file() or not binary.stat().st_mode & 0o111:
            raise ValueError("missing executable: " + name)
        os.symlink(str(binary), str(launchers / name))
        # Even --version executes upstream code only as an unprivileged guest user.
        result = subprocess.check_output(["runuser", "-u", "builder", "--", "/usr/bin/env", "-i",
                    "HOME=/home/builder", "PATH=/usr/bin:/bin", "DISABLE_AUTOUPDATER=1",
                    "DISABLE_TELEMETRY=1", "DISABLE_ERROR_REPORTING=1", str(binary), "--version"],
                    text=True, timeout=60).strip()
        if not __import__("re").search(r"(?<![0-9.])" + __import__("re").escape(spec["version"])
                                      + r"(?![0-9.])", result):
            raise ValueError("installed version differs: " + name)
        versions[name] = spec["version"]
    return versions


def build():
    work = Path("/var/lib/agent-image-build")
    lock = json.loads((work / "lock.json").read_text())
    canonical = json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()
    pin = hashlib.sha256(canonical).hexdigest()
    arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    if (os.geteuid() != 0 or os.uname().sysname != "Linux"
            or Path("/etc/agent-zone-disposable-builder").read_text() != pin
            or arch != (work / "architecture").read_text()):
        raise ValueError("only the separately seeded disposable Linux guest may build")
    build_event("AGENT_IMAGE_BUILD start " + pin)
    config = lock["architectures"][arch]
    zone = (work / "zone").read_text()
    if zone not in ZONE_TOOLS:
        raise ValueError("explicit build zone required")
    check_packages(config, packages(), False)
    # Apt validates signed metadata with the base image's Debian archive keyring.
    index = work / "Packages.xz"
    download(config["package_index"], index)
    sources = work / "sources.list"
    sources.write_text("deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/"
                       + lock["snapshot"] + "/ trixie main\n")
    apt = ["apt-get", "-o", "Dir::Etc::sourcelist=" + str(sources),
           "-o", "Dir::Etc::sourceparts=-", "-o", "APT::Get::List-Cleanup=0"]
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "DEBIAN_FRONTEND": "noninteractive"}
    subprocess.run(apt + ["update"], env=environment, check=True)
    subprocess.run(apt + ["install", "--yes", "--no-install-recommends", "--no-upgrade"]
                   + [name + "=" + value for name, value in sorted(config["packages"]["install"].items())],
                   env=environment, check=True)
    check_packages(config, packages(), True)
    build_event("AGENT_IMAGE_BUILD packages complete")
    prefix = Path("/opt/agent-tools")
    versions = install_tools(config, zone, work, prefix, Path("/usr/local/bin"))
    actual_packages = packages()
    hashes = {str(path.relative_to("/")): digest_file(path).hexdigest()
              for path in sorted(prefix.rglob("*")) if path.is_file() and not path.is_symlink()}
    for relative in json.loads((work / "baked-files.json").read_text()):
        hashes[relative] = digest_file("/" + relative).hexdigest()
    build_event("AGENT_IMAGE_BUILD tools complete; stopping writers and scrubbing")
    prepare_freeze("/")
    manifest = {"version": 1, "architecture": arch, "zone": zone, "lock_sha256": pin,
                "tools": versions, "packages": actual_packages, "files": hashes,
                "lifecycle": "scrubbed"}
    Path("/usr/local/share/agent-image-manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    os.sync()
    build_event("AGENT_IMAGE_FROZEN " + pin, persist=False)
    subprocess.run(["systemctl", "poweroff"], check=True)


if __name__ == "__main__":
    try:
        build()
    except Exception:
        message = "AGENT_IMAGE_BUILD failed\n" + traceback.format_exc()
        try:
            build_event(message)
        except OSError:
            build_event(message, persist=False)
        raise
'''


def recipe(lock, architecture, zone, temporary_public_key):
    """Return one disposable seed; no credentials or production seed accepted."""
    validate_lock(lock)
    if architecture not in ARCHES:
        raise ImageBuildError("architecture must be arm64 or amd64")
    if zone not in ZONE_TOOLS:
        raise ImageBuildError("explicit internal or confidential zone required")
    public, _ = _check_ed25519_public_line(temporary_public_key, "temporary build key")
    pin = lock_digest(lock)
    work = "/var/lib/agent-image-build/"
    files = [
        {"path": work + "build.py", "permissions": "0700", "content": GUEST_PROGRAM},
        {"path": work + "lock.json", "permissions": "0600", "content": json.dumps(lock, sort_keys=True)},
        {"path": work + "architecture", "permissions": "0600", "content": architecture},
        {"path": work + "zone", "permissions": "0600", "content": zone},
        {"path": work + "baked-files.json", "permissions": "0600", "content": json.dumps(sorted(_baked_files()))},
        {"path": "/etc/agent-zone-disposable-builder", "permissions": "0600", "content": pin},
    ]
    files.extend(_seed_files())
    config = {"hostname": "agent-image-disposable-builder", "users": [
        {"name": "builder", "lock_passwd": True, "shell": "/bin/bash",
         "ssh_authorized_keys": [public]}], "disable_root": True, "ssh_pwauth": False,
        "package_update": False, "package_upgrade": False, "write_files": files,
        "runcmd": [["systemd-run", "--no-block", "--unit=agent-image-build",
                    "--property=After=cloud-final.service", "--property=StandardOutput=tty",
                    "--property=StandardError=tty", "--property=TTYPath=/dev/console",
                    "/usr/bin/python3", work + "build.py"]]}
    return {"user-data": "#cloud-config\n" + json.dumps(config, sort_keys=True) + "\n",
            "meta-data": json.dumps({"instance-id": "disposable-" + zone + "-" + pin[:16],
                                     "local-hostname": "agent-image-disposable-builder"}) + "\n"}


def build_argv(architecture, state_dir, seed_iso, executable):
    """Pure temporary-NAT adapter: fresh build copy only, never zone state.

    state_dir contains root.raw, console.log and (arm64) efi-vars. Prepare an
    empty private directory, verify/copy the locked base image there and create
    a cidata ISO from recipe's seed before using these commands. Those are
    offline file operations, not permission to launch this VM or install tools.
    No host mounts, agent socket, management endpoint or forwarded TCP port.
    """
    if architecture not in ARCHES:
        raise ImageBuildError("architecture must be arm64 or amd64")
    state_dir = _checked_path(state_dir, "disposable state")
    seed_iso = _checked_path(seed_iso, "disposable seed ISO")
    executable = _checked_path(executable, "hypervisor")
    # Fixed basename separates this adapter from either production zone.
    if Path(state_dir).name != "disposable-image-build":
        raise ImageBuildError("state directory must be named disposable-image-build")
    disk = state_dir + "/root.raw"
    if architecture == "arm64":
        return [executable, "--cpus", "4", "--memory", "4096", "--bootloader",
                "efi,variable-store=" + state_dir + "/efi-vars,create", "--device",
                "virtio-blk,path=" + disk, "--device", "virtio-blk,path=" + seed_iso + ",readonly",
                "--device", "virtio-net,nat", "--device", "virtio-rng", "--device",
                "virtio-serial,logFilePath=" + state_dir + "/console.log", "--restful-uri", "none://"]
    return [executable, "-nodefaults", "-no-user-config", "-machine", "q35,accel=kvm",
            "-m", "4096", "-smp", "4", "-display", "none", "-monitor", "none",
            "-drive", "file=" + disk + ",format=raw,if=virtio",
            "-drive", "file=" + seed_iso + ",format=raw,media=cdrom,readonly=on",
            "-netdev", "user,id=build", "-device", "virtio-net-pci,netdev=build",
            "-device", "virtio-rng-pci", "-serial", "file:" + state_dir + "/console.log"]


def verify_manifest(lock, architecture, zone, manifest, root=None):
    """Validate installed evidence offline, optionally against an extracted root.

    The unsigned manifest and filesystem can be forged together. These checks
    establish consistency only, not provenance or approval. Authenticity needs
    an externally approved image hash or independent locked-artifact comparison.
    """
    validate_lock(lock)
    if architecture not in ARCHES:
        raise ImageBuildError("unsupported architecture")
    if zone not in ZONE_TOOLS:
        raise ImageBuildError("explicit internal or confidential zone required")
    expected_tools = {name: VERSIONS[name] for name in ZONE_TOOLS[zone]}
    _keys(manifest, ("version", "architecture", "zone", "lock_sha256", "tools", "packages", "files", "lifecycle"), "manifest")
    if (type(manifest["version"]) is not int or manifest["version"] != 1
            or manifest["architecture"] != architecture or manifest["lock_sha256"] != lock_digest(lock)
            or manifest["zone"] != zone or manifest["tools"] != expected_tools or manifest["lifecycle"] != "scrubbed"):
        raise ImageBuildError("manifest differs from the requested recipe or freeze state")
    _package_map(manifest["packages"])
    config = lock["architectures"][architecture]
    for group in config["packages"].values():
        if any(manifest["packages"].get(k) != v for k, v in group.items()):
            raise ImageBuildError("manifest package versions differ")
    files = manifest["files"]
    if not isinstance(files, dict) or not files or len(files) > 50000:
        raise ImageBuildError("installed file hashes required")
    selected = {name: config["tools"][name] for name in ZONE_TOOLS[zone]}
    required = {"opt/agent-tools/" + name + "/" + spec["member"] for name, spec in selected.items()}
    allowed_prefixes = tuple("opt/agent-tools/" + name + "/" for name in selected)
    baked = _baked_files()
    if not required | baked.keys() <= files.keys():
        raise ImageBuildError("installed tool executable missing")
    for relative, content in baked.items():
        if files[relative] != hashlib.sha256(content.encode()).hexdigest():
            raise ImageBuildError("baked relay or SSH configuration differs")
    for relative, digest in files.items():
        if (not isinstance(relative, str) or not (relative.startswith(allowed_prefixes) or relative in baked)
                or str(Path(relative)) != relative or ".." in Path(relative).parts
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ImageBuildError("unsafe installed file/hash")
        if root is not None:
            path = Path(root) / relative
            ancestors = [path]
            while ancestors[-1] != Path(root):
                ancestors.append(ancestors[-1].parent)
            if any(p.is_symlink() for p in ancestors):
                raise ImageBuildError("symlink in inspected image")
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ImageBuildError("installed file integrity mismatch")
            if relative in required and not path.stat().st_mode & 0o111:
                raise ImageBuildError("installed tool is not executable")
    if root is not None:
        namespace = {"__name__": "offline_inspection"}
        exec(compile(GUEST_PROGRAM, "<guest-freeze-inspection>", "exec"), namespace)
        try:
            namespace["frozen_state"](root)
        except (ValueError, OSError) as exc:
            raise ImageBuildError("image is not scrubbed: " + str(exc)) from exc
        prefix = Path(root) / "opt/agent-tools"
        if {path.name for path in prefix.iterdir()} != set(selected):
            raise ImageBuildError("installed tool directories differ from zone")
        actual_files = set()
        for path in prefix.rglob("*"):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ImageBuildError("unexpected installed link or special file")
            if path.is_file():
                actual_files.add(str(path.relative_to(root)))
        if actual_files != set(files) - baked.keys():
            raise ImageBuildError("installed files differ from manifest inventory")
        for name, spec in selected.items():
            link = Path(root) / "usr/local/bin" / name
            if not link.is_symlink() or os.readlink(link) != "/opt/agent-tools/" + name + "/" + spec["member"]:
                raise ImageBuildError("installed launcher differs")
        for name in VERSIONS.keys() - selected.keys():
            if (Path(root) / "usr/local/bin" / name).exists() or (Path(root) / "usr/local/bin" / name).is_symlink():
                raise ImageBuildError("unexpected tool launcher for zone")
    return {"consistent": True, "provenance_verified": False, "zone": zone,
            "architecture": architecture, "lock_sha256": lock_digest(lock),
            "files_checked": len(files) if root is not None else 0,
            "approval": False, "runtime_boot_verified": False}


def verify_base(lock, architecture, image):
    """Stream and compare the base before any disposable VM consumes it."""
    validate_lock(lock)
    if architecture not in ARCHES:
        raise ImageBuildError("unsupported architecture")
    pin = lock["architectures"][architecture]["base"]["integrity"]
    algorithm, expected = pin.split(":", 1)
    computed = hashlib.new(algorithm)
    fd = os.open(image, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        import stat
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            raise ImageBuildError("base image must be a nonempty regular file")
        total = 0
        while total <= before.st_size:
            block = os.read(fd, min(1024 * 1024, before.st_size + 1 - total))
            if not block:
                break
            computed.update(block)
            total += len(block)
        after = os.fstat(fd)
        if (total != before.st_size or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns or computed.hexdigest() != expected):
            raise ImageBuildError("base image integrity mismatch or concurrent change")
    finally:
        os.close(fd)
    return {"verified": True, "architecture": architecture, "integrity": pin, "approval": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("validate-lock")
    check.add_argument("--lock", required=True)
    base = commands.add_parser("verify-base")
    base.add_argument("--lock", required=True)
    base.add_argument("--architecture", choices=ARCHES, required=True)
    base.add_argument("--image", required=True)
    seed = commands.add_parser("recipe")
    seed.add_argument("--lock", required=True)
    seed.add_argument("--architecture", choices=ARCHES, required=True)
    seed.add_argument("--zone", choices=ZONE_TOOLS, required=True)
    seed.add_argument("--temporary-public-key", required=True)
    seed.add_argument("--output", required=True)
    adapter = commands.add_parser("build-argv")
    adapter.add_argument("--architecture", choices=ARCHES, required=True)
    adapter.add_argument("--state-dir", required=True)
    adapter.add_argument("--seed-iso", required=True)
    adapter.add_argument("--executable", required=True)
    verify = commands.add_parser("verify-manifest")
    verify.add_argument("--lock", required=True)
    verify.add_argument("--architecture", choices=ARCHES, required=True)
    verify.add_argument("--zone", choices=ZONE_TOOLS, required=True)
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--root")
    args = parser.parse_args(argv)
    try:
        if args.command == "build-argv":
            result = build_argv(args.architecture, args.state_dir, args.seed_iso, args.executable)
        else:
            lock = load_lock(args.lock)
            if args.command == "validate-lock":
                result = {"valid": True, "status": "proposal", "approval": False, "lock_sha256": lock_digest(lock)}
            elif args.command == "verify-base":
                result = verify_base(lock, args.architecture, args.image)
            elif args.command == "recipe":
                output = Path(_checked_path(args.output, "output"))
                output.mkdir(mode=0o700)
                seed_data = recipe(lock, args.architecture, args.zone, Path(args.temporary_public_key).read_text())
                for name, content in seed_data.items():
                    fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w") as sink:
                        sink.write(content)
                result = {"seed": str(output), "disposable_only": True, "approval": False}
            else:
                result = verify_manifest(lock, args.architecture, args.zone, json.loads(Path(args.manifest).read_text()), args.root)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        sys.stderr.write("image_builder: " + str(exc) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
