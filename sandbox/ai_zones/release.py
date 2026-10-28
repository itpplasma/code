"""Verify signed tool bundles and install retained bytes without root hooks.

This module belongs to the independently approved core. Installed tool code is
never imported here. Policy, credentials and revocations are outside releases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile

from ai_zones.policy import (
    PolicyError, _assert_protected_stat, assert_no_mutating_acl, assert_protected,
)

OPENSSL = "/usr/bin/openssl"
MANIFEST_NAME, SIGNATURE_NAME, PAYLOAD_DIR = "manifest.json", "manifest.sig", "payload"
CURRENT_LINK, RELEASES_DIR = "current", "releases"
MANIFEST_MAX_BYTES = 1 << 20
PAYLOAD_FILE_MAX_BYTES = 64 << 20
SIGNATURE_MAX_BYTES = 64 << 10
PUBLIC_KEY_MAX_BYTES = 64 << 10
PAYLOAD_FILE_MAX_COUNT = 128
BUNDLE_TOTAL_MAX_BYTES = 128 << 20
OPENSSL_TIMEOUT_S = 30
_READ_CHUNK = 64 << 10
_SAFE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
             "OPENSSL_CONF": "/dev/null"}
_RELEASE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PY_NAME_RE = re.compile(r"ai_zones/[A-Za-z0-9_]+\.py")
_SERVICE_BINARIES = frozenset({"bin/sloptools-agent", "bin/helpy-agent"})


class ReleaseError(Exception):
    """A verification or installation refusal."""


def _abs(path: str) -> str:
    p = os.fspath(path)
    if (not isinstance(p, str) or not p.startswith("/") or p.startswith("//") or
            os.path.normpath(p) != p or len(p) > 4096 or
            any(ord(c) < 32 or ord(c) == 127 for c in p)):
        raise ReleaseError("expected a normalized absolute path without controls")
    return p


def _open_dir(name: str, dir_fd: int, where: str) -> int:
    if not name or "/" in name or name in (".", ".."):
        raise ReleaseError(f"{where}: invalid directory name")
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                       os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError as exc:
        raise ReleaseError(f"{where}: cannot open directory") from exc


def _open_absolute_dir(path: str) -> int:
    path = _abs(path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.split("/")[1:]:
            if name:
                child = _open_dir(name, fd, "directory ancestry")
                os.close(fd)
                fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(st):
    return st.st_dev, st.st_ino, st.st_uid, st.st_mode


def _read_exact(path: str, max_bytes: int, where: str, dir_fd=None,
                expected=None) -> bytes:
    parent = None
    if dir_fd is None:
        path = _abs(path)
        parent = _open_absolute_dir(os.path.dirname(path))
        dir_fd, path = parent, os.path.basename(path)
    if not path or "/" in path or path in (".", ".."):
        if parent is not None:
            os.close(parent)
        raise ReleaseError(f"{where}: invalid file name")
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=dir_fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ReleaseError(f"{where}: expected a single-link regular file")
        if expected is not None and _identity(before) != _identity(expected):
            raise ReleaseError(f"{where}: checked object changed")
        if before.st_size > max_bytes:
            raise ReleaseError(f"{where}: file exceeds byte limit")
        chunks, size = [], 0
        while True:
            data = os.read(fd, min(_READ_CHUNK, max_bytes - size + 1))
            if not data:
                break
            size += len(data)
            if size > max_bytes:
                raise ReleaseError(f"{where}: file exceeds running byte limit")
            chunks.append(data)
        after = os.fstat(fd)
        if (size != before.st_size or _identity(before) != _identity(after) or
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ReleaseError(f"{where}: file changed while reading")
        return b"".join(chunks)
    except OSError as exc:
        raise ReleaseError(f"{where}: cannot read file") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if parent is not None:
            os.close(parent)


def _reject_duplicate_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ReleaseError("manifest: duplicate JSON key")
        result[name] = value
    return result


def _reject_constant(value):
    raise ReleaseError("manifest: nonfinite number")


def _release_id(value):
    return (isinstance(value, str) and _RELEASE_RE.fullmatch(value) is not None
            and ".." not in value)


def validate_manifest(raw: bytes) -> tuple[str, dict[str, str]]:
    if len(raw) > MANIFEST_MAX_BYTES:
        raise ReleaseError("manifest: byte limit exceeded")
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs,
                         parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ReleaseError("manifest: invalid JSON") from exc
    if not isinstance(doc, dict) or set(doc) != {"version", "release", "files"}:
        raise ReleaseError("manifest: unexpected fields")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise ReleaseError("manifest: unsupported version")
    if not _release_id(doc["release"]):
        raise ReleaseError("manifest: unsafe release identifier")
    files = doc["files"]
    if not isinstance(files, dict) or not 1 <= len(files) <= PAYLOAD_FILE_MAX_COUNT:
        raise ReleaseError("manifest: file count limit")
    for name, digest in files.items():
        if (not isinstance(name, str) or
                not (_PY_NAME_RE.fullmatch(name) or name == "bin/ai" or name in _SERVICE_BINARIES)):
            raise ReleaseError("manifest: file outside allowed payload layout")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ReleaseError("manifest: invalid sha256")
    return doc["release"], files


def _names(fd: int, limit: int):
    # Do not materialize attacker-sized directories before applying the limit.
    result = []
    with os.scandir(fd) as entries:
        for count, entry in enumerate(entries, 1):
            if count > limit:
                raise ReleaseError("bundle: directory entry limit exceeded")
            result.append(entry.name)
    return result


def _write_all(fd: int, data: bytes, where: str) -> None:
    remaining = memoryview(data)
    while remaining:
        count = os.write(fd, remaining)
        if count <= 0:
            raise ReleaseError(f"{where}: write made no progress")
        remaining = remaining[count:]


def _verify_signature(manifest: bytes, signature: bytes, public_key: bytes) -> None:
    # Ignore TMPDIR: root verification must not depend on a caller's directory.
    temp_parent = "/private/tmp" if sys.platform == "darwin" else "/tmp"
    with tempfile.TemporaryDirectory(prefix="ai-zones-signature-", dir=temp_parent) as tmp:
        os.chmod(tmp, 0o700)
        assert_no_mutating_acl(tmp)
        paths = []
        for name, data in (("key.pem", public_key), ("manifest.sig", signature)):
            path = os.path.join(tmp, name)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                _write_all(fd, data, "signature input")
            finally:
                os.close(fd)
            paths.append(path)
        try:
            rsa = subprocess.run([OPENSSL, "rsa", "-pubin", "-in", paths[0], "-noout"],
                                 capture_output=True, timeout=OPENSSL_TIMEOUT_S, env=_SAFE_ENV)
            if rsa.returncode != 0:
                raise ReleaseError("signature: expected an RSA public key")
            result = subprocess.run(
                [OPENSSL, "dgst", "-sha256", "-verify", paths[0],
                 "-signature", paths[1]], input=manifest, capture_output=True,
                timeout=OPENSSL_TIMEOUT_S, env=_SAFE_ENV)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ReleaseError("signature: verifier unavailable") from exc
        if result.returncode != 0:
            raise ReleaseError("signature: verification failed")


def _verified_bundle(bundle: str, public_key: bytes) -> tuple[str, dict[str, bytes]]:
    bundle_fd = _open_absolute_dir(bundle)
    try:
        if set(_names(bundle_fd, 3)) != {MANIFEST_NAME, SIGNATURE_NAME, PAYLOAD_DIR}:
            raise ReleaseError("bundle: unexpected or missing top-level entry")
        manifest = _read_exact(MANIFEST_NAME, MANIFEST_MAX_BYTES, "manifest", bundle_fd)
        release, expected = validate_manifest(manifest)
        signature = _read_exact(SIGNATURE_NAME, SIGNATURE_MAX_BYTES, "signature", bundle_fd)
        _verify_signature(manifest, signature, public_key)
        payload_fd = _open_dir(PAYLOAD_DIR, bundle_fd, "payload")
        try:
            verified, total = {}, 0
            for sub in _names(payload_fd, 2):
                if sub not in ("ai_zones", "bin"):
                    raise ReleaseError("payload: unexpected directory")
                sub_fd = _open_dir(sub, payload_fd, "payload directory")
                try:
                    for base in _names(sub_fd, PAYLOAD_FILE_MAX_COUNT):
                        name = sub + "/" + base
                        if name not in expected:
                            raise ReleaseError("payload: unsigned extra entry")
                        if len(verified) >= PAYLOAD_FILE_MAX_COUNT:
                            raise ReleaseError("payload: file count limit exceeded")
                        data = _read_exact(name.split("/")[1],
                                           min(PAYLOAD_FILE_MAX_BYTES,
                                               BUNDLE_TOTAL_MAX_BYTES - total),
                                           "payload file", sub_fd)
                        if hashlib.sha256(data).hexdigest() != expected[name]:
                            raise ReleaseError("payload: sha256 mismatch")
                        total += len(data)
                        verified[name] = data
                finally:
                    os.close(sub_fd)
            if set(verified) != set(expected):
                raise ReleaseError("payload: manifest files missing")
            return release, verified
        finally:
            os.close(payload_fd)
    finally:
        os.close(bundle_fd)


def verify_bundle(bundle: str, public_key: str) -> tuple[str, dict[str, bytes]]:
    """Verify without changing installed state; the key is caller-supplied trust."""
    key = _read_exact(public_key, PUBLIC_KEY_MAX_BYTES, "public key")
    return _verified_bundle(_abs(bundle), key)


def _check_install_protections(prefix: str, public_key: str) -> None:
    assert_protected(__file__)
    assert_protected(public_key)
    assert_protected(prefix, directory=True)
    releases = os.path.join(prefix, RELEASES_DIR)
    if os.path.lexists(releases):
        assert_protected(releases, directory=True)


def _verify_current_link(prefix: str, prefix_fd: int) -> None:
    try:
        st = os.stat(CURRENT_LINK, dir_fd=prefix_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    # Symlink permission bits do not govern replacement; its protected parent does.
    if not stat.S_ISLNK(st.st_mode) or st.st_uid != 0:
        raise ReleaseError("current: expected a root-owned symlink")
    assert_no_mutating_acl(os.path.join(prefix, CURRENT_LINK))
    target = os.readlink(CURRENT_LINK, dir_fd=prefix_fd)
    parts = target.split("/")
    if len(parts) != 2 or parts[0] != RELEASES_DIR or not _release_id(parts[1]):
        raise ReleaseError("current: target must be exactly releases/<release-id>")
    assert_protected(os.path.join(prefix, target), directory=True)


def _cleanup_target(releases_fd, release, target_fd, sub_fds, created, subdirs):
    for sub, base in reversed(created):
        try:
            os.unlink(base, dir_fd=sub_fds[sub])
        except OSError:
            pass
    for fd in sub_fds.values():
        os.close(fd)
    for sub in subdirs:
        try:
            os.rmdir(sub, dir_fd=target_fd)
        except OSError:
            pass
    try:
        if _identity(os.stat(release, dir_fd=releases_fd, follow_symlinks=False)) == \
                _identity(os.fstat(target_fd)):
            os.rmdir(release, dir_fd=releases_fd)
    except OSError:
        pass


def _install_verified(prefix_fd, releases_fd, release, verified):
    """Copy retained bytes; callers establish authority before entering here."""
    target_fd, sub_fds, created, subdirs, link = None, {}, [], [], None
    committed = False
    try:
        os.mkdir(release, 0o700, dir_fd=releases_fd)
        target_fd = _open_dir(release, releases_fd, "new release")
        for name, data in sorted(verified.items()):
            sub, base = name.split("/")
            if sub not in sub_fds:
                os.mkdir(sub, 0o700, dir_fd=target_fd)
                subdirs.append(sub)
                sub_fds[sub] = _open_dir(sub, target_fd, "new payload directory")
            fd = os.open(base, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=sub_fds[sub])
            created.append((sub, base))
            try:
                _write_all(fd, data, "payload install")
                os.fchmod(fd, 0o555 if name in _SERVICE_BINARIES else 0o444)
                os.fsync(fd)
            finally:
                os.close(fd)
            installed = _read_exact(base, PAYLOAD_FILE_MAX_BYTES, "installed payload", sub_fds[sub])
            if len(installed) != len(data) or hashlib.sha256(installed).digest() != hashlib.sha256(data).digest():
                raise ReleaseError("installed payload differs from verified bytes")
        for fd in sub_fds.values():
            os.fchmod(fd, 0o755)
            os.fsync(fd)
        os.fchmod(target_fd, 0o755)
        os.fsync(target_fd)
        os.fsync(releases_fd)
        link = f".current.{os.getpid()}.{secrets.token_hex(8)}"
        os.symlink(RELEASES_DIR + "/" + release, link, dir_fd=prefix_fd)
        os.replace(link, CURRENT_LINK, src_dir_fd=prefix_fd, dst_dir_fd=prefix_fd)
        link, committed = None, True
        try:
            os.fsync(prefix_fd)
        except OSError as exc:
            raise ReleaseError("current committed; directory durability is uncertain") from exc
    except OSError as exc:
        raise ReleaseError("release installation failed") from exc
    finally:
        # A signal can arrive after replace committed but before its return is recorded.
        if not committed and target_fd is not None:
            try:
                committed = os.readlink(CURRENT_LINK, dir_fd=prefix_fd) == RELEASES_DIR + "/" + release
            except OSError:
                pass
        if link is not None:
            try:
                os.unlink(link, dir_fd=prefix_fd)
            except OSError:
                pass
        if target_fd is not None:
            if not committed:
                _cleanup_target(releases_fd, release, target_fd, sub_fds, created, subdirs)
                sub_fds = {}
            for fd in sub_fds.values():
                os.close(fd)
            os.close(target_fd)


def install_bundle(bundle: str, prefix: str, public_key: str) -> str:
    """Install as root. All failures before atomic current replacement preserve it.

    A post-replacement fsync failure reports uncertain durability explicitly;
    it cannot promise to undo a filesystem commit during an I/O failure.
    """
    if os.geteuid() != 0:
        raise ReleaseError("install requires root")
    bundle, prefix, public_key = _abs(bundle), _abs(prefix), _abs(public_key)
    prefix_fd = releases_fd = None
    try:
        _check_install_protections(prefix, public_key)
        _, key_st = _assert_protected_stat(public_key)
        key = _read_exact(public_key, PUBLIC_KEY_MAX_BYTES, "protected public key", expected=key_st)
        _, prefix_st = _assert_protected_stat(prefix, directory=True)
        prefix_fd = _open_absolute_dir(prefix)
        if _identity(os.fstat(prefix_fd)) != _identity(prefix_st):
            raise ReleaseError("prefix changed during verification")
        _verify_current_link(prefix, prefix_fd)
        release, verified = _verified_bundle(bundle, key)
        created_releases = False
        try:
            os.mkdir(RELEASES_DIR, 0o700, dir_fd=prefix_fd)
            created_releases = True
        except FileExistsError:
            pass
        if created_releases:
            new_fd = _open_dir(RELEASES_DIR, prefix_fd, "new releases")
            try:
                os.fchmod(new_fd, 0o755)
            finally:
                os.close(new_fd)
        assert_protected(os.path.join(prefix, RELEASES_DIR), directory=True)
        releases_fd = _open_dir(RELEASES_DIR, prefix_fd, "releases")
        _install_verified(prefix_fd, releases_fd, release, verified)
        return os.path.join(prefix, RELEASES_DIR, release)
    except (PolicyError, OSError) as exc:
        raise ReleaseError("installation protection or I/O check failed") from exc
    finally:
        if releases_fd is not None:
            os.close(releases_fd)
        if prefix_fd is not None:
            os.close(prefix_fd)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "install"))
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--public-key", required=True)
    args = parser.parse_args(argv)
    try:
        _abs(args.prefix)
        if args.command == "verify":
            release, files = verify_bundle(args.bundle, args.public_key)
            print(f"OK: verified release {release}, {len(files)} files; no installed state changed")
        else:
            target = install_bundle(args.bundle, args.prefix, args.public_key)
            print(f"OK: installed {target}")
        return 0
    except (ReleaseError, PolicyError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
