"""Bounded runtime manifests and root-only installation without executing payloads.

This module and its shared release/policy dependencies must be frozen in the
independently approved core. The ordinary tool updater must not import it.
Bundles contain manifest.json, manifest.sig and a complete payload/ tree.
Only the explicit frozen-core API can install; the CLI builds or verifies.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import posixpath
import re
import secrets
import shutil
import stat
import sys

from ai_zones import policy, release

MAX_ENTRIES = 12000
MAX_TOTAL_BYTES = 1 << 30
MAX_FILE_BYTES = 128 << 20
MAX_MANIFEST_BYTES = 4 << 20
CHUNK = 64 << 10
RUNTIMES = "runtimes"
CURRENT = "runtime-current"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
_SHA = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"3\.(13|14)\.[0-9]{1,3}")
# The test prefix is deliberately exact, never a pattern or an environment input.
_INSTALL_PREFIXES = {"darwin": frozenset({"/Library/AgentSandbox",
    "/Library/AgentSandboxTest-RUNTIME-20261028"}),
    "linux": frozenset({"/opt/agent-sandbox"})}


class RuntimeError(ValueError):
    """Invalid runtime or failed protection/integrity check."""


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 1024 or
            value.startswith("/") or posixpath.normpath(value) != value or
            any(p in ("", ".", "..") for p in value.split("/")) or
            any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF
                for c in value)):
        raise RuntimeError("invalid relative runtime path")
    return value


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value) or value in (".", ".."):
        raise RuntimeError("invalid runtime id")
    return value


def canonical_manifest(manifest):
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("ascii")


def _symlink_target(name, target):
    if (not isinstance(target, str) or not target or len(target) > 1024 or
            target.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in target)):
        raise RuntimeError("symlink target must be relative")
    # Reject dot components before joining: normalizing them before following
    # an intermediate link disagrees with the kernel and can escape the tree.
    _relative(target)
    resolved = posixpath.join(posixpath.dirname(name), target)
    _relative(resolved)
    return resolved


def _validate(manifest):
    keys = {"schema", "runtime_id", "runtime_root", "python_version", "python",
            "platform", "executables", "provenance", "entries"}
    if not isinstance(manifest, dict) or set(manifest) != keys:
        raise RuntimeError("manifest keys differ from runtime schema")
    if type(manifest["schema"]) is not int or manifest["schema"] != 1:
        raise RuntimeError("unsupported runtime schema")
    runtime_id = _id(manifest["runtime_id"])
    if manifest["runtime_root"] != RUNTIMES + "/" + runtime_id:
        raise RuntimeError("runtime root does not match id")
    version = manifest["python_version"]
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise RuntimeError("runtime needs supported Python 3.13 or 3.14")
    expected = "bin/python" + ".".join(version.split(".")[:2])
    if manifest["python"] != expected or manifest["executables"] != [expected]:
        raise RuntimeError("only the canonical Python binary may be executable")
    if manifest["platform"] != "aarch64-apple-darwin":
        raise RuntimeError("only the measured macOS ARM64 bundle is supported")
    provenance = manifest["provenance"]
    if (not isinstance(provenance, dict) or set(provenance) !=
            {"url", "release", "commit", "sha256", "attestation_sha256"}):
        raise RuntimeError("provenance fields are required")
    if (not isinstance(provenance["url"], str) or
            not provenance["url"].startswith("https://github.com/astral-sh/python-build-standalone/releases/download/") or
            len(provenance["url"]) > 1024 or
            not isinstance(provenance["release"], str) or
            not re.fullmatch(r"[0-9]{8}", provenance["release"]) or
            not isinstance(provenance["commit"], str) or
            not re.fullmatch(r"[0-9a-f]{40}", provenance["commit"]) or
            any(not isinstance(provenance[k], str) or not _SHA.fullmatch(provenance[k])
                for k in ("sha256", "attestation_sha256"))):
        raise RuntimeError("invalid pinned provenance")
    source = ("https://github.com/astral-sh/python-build-standalone/releases/download/" +
              provenance["release"] + "/cpython-" + version + "%2B" +
              provenance["release"] + "-aarch64-apple-darwin-install_only")
    if provenance["url"] not in (source + ".tar.gz", source + "_stripped.tar.gz"):
        raise RuntimeError("provenance asset must match the runtime version and release")
    entries = manifest["entries"]
    if not isinstance(entries, dict) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise RuntimeError("runtime entry count exceeds limit")
    total = 0
    for name, item in entries.items():
        _relative(name)
        if not isinstance(item, dict):
            raise RuntimeError("invalid runtime entry")
        kind = item.get("type")
        if kind == "file":
            if set(item) != {"type", "size", "sha256", "mode"}:
                raise RuntimeError("invalid file fields")
            if type(item["size"]) is not int or not 0 <= item["size"] <= MAX_FILE_BYTES:
                raise RuntimeError("runtime file exceeds byte limit")
            if not isinstance(item["sha256"], str) or not _SHA.fullmatch(item["sha256"]):
                raise RuntimeError("invalid file hash")
            mode = 0o555 if name == expected else 0o444
            if type(item["mode"]) is not int or item["mode"] != mode:
                raise RuntimeError("invalid immutable file mode")
            total += item["size"]
        elif kind == "directory":
            if (item != {"type": "directory", "mode": 0o555} or
                    type(item["mode"]) is not int):
                raise RuntimeError("invalid immutable directory mode")
        elif kind == "symlink":
            if set(item) != {"type", "target"}:
                raise RuntimeError("invalid symlink fields")
            _symlink_target(name, item["target"])
        else:
            raise RuntimeError("unsupported runtime object type")
        parent = posixpath.dirname(name)
        if parent and (not isinstance(entries.get(parent), dict) or
                       entries[parent].get("type") != "directory"):
            raise RuntimeError("runtime parent must be a listed directory")
    if total > MAX_TOTAL_BYTES:
        raise RuntimeError("runtime total exceeds byte limit")
    if entries.get(expected, {}).get("type") != "file":
        raise RuntimeError("canonical Python must be a regular file")
    # Resolve every link component through the signed graph, never the host.
    for name, item in entries.items():
        if item["type"] != "symlink":
            continue
        pending = _symlink_target(name, item["target"]).split("/")
        resolved, visited = [], set()
        while pending:
            resolved.append(pending.pop(0))
            current = "/".join(resolved)
            node = entries.get(current)
            if node is None:
                raise RuntimeError("dangling runtime symlink")
            if node["type"] == "symlink":
                if current in visited:
                    raise RuntimeError("cyclic runtime symlink")
                visited.add(current)
                pending = _symlink_target(current, node["target"]).split("/") + pending
                resolved = []
            elif pending and node["type"] != "directory":
                raise RuntimeError("symlink passes through a non-directory")
    return manifest


def _stable(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
            st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _file(parent, name, *, expected=None, destination=None):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size > MAX_FILE_BYTES or before.st_mode & 0o7000):
            raise RuntimeError("runtime file must be bounded regular single-link without special modes")
        digest, size = hashlib.sha256(), 0
        while True:
            data = os.read(fd, min(CHUNK, MAX_FILE_BYTES - size + 1))
            if not data:
                break
            size += len(data)
            if size > MAX_FILE_BYTES or expected is not None and size > expected["size"]:
                raise RuntimeError("runtime file exceeds running byte limit")
            digest.update(data)
            if destination is not None:
                release._write_all(destination, data, "runtime staging")
        if _stable(before) != _stable(os.fstat(fd)) or size != before.st_size:
            raise RuntimeError("runtime file changed while reading")
        result = {"type": "file", "size": size, "sha256": digest.hexdigest()}
        if expected is not None and any(result[k] != expected[k] for k in result):
            raise RuntimeError("runtime file hash or size mismatch")
        return result
    finally:
        os.close(fd)


def _walk(fd, python, *, expected=None, destination=None):
    entries, total = {}, 0
    def visit(parent, relative, dest):
        nonlocal total
        with os.scandir(parent) as children:
            for child in children:
                name = relative + child.name
                _relative(name)
                if len(entries) >= MAX_ENTRIES:
                    raise RuntimeError("runtime entry count exceeds limit")
                before = child.stat(follow_symlinks=False)
                item = expected.get(name) if expected is not None else None
                if expected is not None and item is None:
                    raise RuntimeError("unexpected runtime entry")
                if item is not None:
                    kind = ("directory" if stat.S_ISDIR(before.st_mode) else
                            "file" if stat.S_ISREG(before.st_mode) else
                            "symlink" if stat.S_ISLNK(before.st_mode) else "special")
                    if kind != item["type"]:
                        raise RuntimeError("runtime source type differs from signed manifest")
                target_fd = None
                if stat.S_ISDIR(before.st_mode):
                    found = {"type": "directory", "mode": 0o555}
                    if dest is not None:
                        os.mkdir(child.name, 0o700, dir_fd=dest)
                        target_fd = release._open_dir(child.name, dest, "runtime staging")
                    source_fd = None
                    try:
                        source_fd = release._open_dir(child.name, parent, "runtime source")
                        if _stable(before) != _stable(os.fstat(source_fd)):
                            raise RuntimeError("runtime directory changed")
                        entries[name] = found
                        visit(source_fd, name + "/", target_fd)
                        if _stable(before) != _stable(os.fstat(source_fd)):
                            raise RuntimeError("runtime directory changed while reading")
                        if target_fd is not None:
                            os.fchmod(target_fd, 0o555)
                            os.fsync(target_fd)
                    finally:
                        if source_fd is not None:
                            os.close(source_fd)
                        if target_fd is not None:
                            os.close(target_fd)
                elif stat.S_ISREG(before.st_mode):
                    mode = 0o555 if name == python else 0o444
                    if dest is not None:
                        target_fd = os.open(child.name, os.O_WRONLY | os.O_CREAT |
                            os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dest)
                    try:
                        found = _file(parent, child.name, expected=item, destination=target_fd)
                        found["mode"] = mode
                        total += found["size"]
                        if total > MAX_TOTAL_BYTES:
                            raise RuntimeError("runtime total exceeds byte limit")
                        if target_fd is not None:
                            os.fchmod(target_fd, mode)
                            os.fsync(target_fd)
                    finally:
                        if target_fd is not None:
                            os.close(target_fd)
                    entries[name] = found
                elif stat.S_ISLNK(before.st_mode):
                    target = os.readlink(child.name, dir_fd=parent)
                    _symlink_target(name, target)
                    if _stable(before) != _stable(os.stat(child.name, dir_fd=parent, follow_symlinks=False)):
                        raise RuntimeError("runtime link changed")
                    found = {"type": "symlink", "target": target}
                    entries[name] = found
                    if dest is not None:
                        os.symlink(target, child.name, dir_fd=dest)
                else:
                    raise RuntimeError("runtime contains a special object")
                if expected is not None and found != item:
                    raise RuntimeError("runtime entry differs from signed manifest")
    before = os.fstat(fd)
    visit(fd, "", destination)
    if _stable(before) != _stable(os.fstat(fd)):
        raise RuntimeError("runtime root changed while reading")
    if expected is not None and entries != expected:
        raise RuntimeError("runtime manifest entry missing")
    return entries


def build_manifest(source_tree, runtime_id, *, python_version, provenance):
    """Hash an unprivileged, complete tree. Never execute its interpreter/hooks.

    python_version is an asserted measurement made separately in isolation;
    signing that assertion is the human release authority's responsibility.
    """
    if os.geteuid() == 0:
        raise RuntimeError("build must run unprivileged")
    python = "bin/python" + ".".join(str(python_version).split(".")[:2])
    manifest = {"schema": 1, "runtime_id": _id(runtime_id),
        "runtime_root": RUNTIMES + "/" + runtime_id, "python_version": python_version,
        "python": python, "platform": "aarch64-apple-darwin", "executables": [python],
        "provenance": provenance, "entries": {}}
    fd = release._open_absolute_dir(os.fspath(source_tree))
    try:
        manifest["entries"] = _walk(fd, python)
    finally:
        os.close(fd)
    _validate(manifest)
    if len(canonical_manifest(manifest)) > MAX_MANIFEST_BYTES:
        raise RuntimeError("runtime manifest exceeds byte limit")
    return manifest


def _decode_signed(raw, signature, key):
    release._verify_signature(raw, signature, key)
    try:
        doc = json.loads(raw, object_pairs_hook=policy._reject_duplicate_pairs)
    except (ValueError, UnicodeError, RecursionError, policy.PolicyError) as exc:
        raise RuntimeError("invalid runtime JSON") from exc
    _validate(doc)
    if raw != canonical_manifest(doc):
        raise RuntimeError("runtime manifest is not canonical")
    return doc


def _load(bundle_fd, key):
    if set(release._names(bundle_fd, 3)) != {"manifest.json", "manifest.sig", "payload"}:
        raise RuntimeError("unexpected runtime bundle entry")
    raw = release._read_exact("manifest.json", MAX_MANIFEST_BYTES, "runtime manifest", bundle_fd)
    signature = release._read_exact("manifest.sig", release.SIGNATURE_MAX_BYTES,
                                    "runtime signature", bundle_fd)
    return _decode_signed(raw, signature, key), raw, signature

def verify_runtime_bundle(bundle, public_key):
    """Verify signed metadata and stream-hash all payload bytes without executing.

    Return metadata only: a mutable source is NOT a retained trusted runtime.
    Installation reopens, verifies and hashes into private root staging, then
    rehashes that retained staging before publishing it.
    """
    key = release._read_exact(os.fspath(public_key), release.PUBLIC_KEY_MAX_BYTES, "runtime key")
    fd = release._open_absolute_dir(os.fspath(bundle))
    try:
        doc, _, _ = _load(fd, key)
        payload = release._open_dir("payload", fd, "runtime payload")
        try:
            _walk(payload, doc["python"], expected=doc["entries"])
        finally:
            os.close(payload)
        return doc
    except (OSError, policy.PolicyError, release.ReleaseError) as exc:
        raise RuntimeError("runtime verification failed") from exc
    finally:
        os.close(fd)


def _root_owned(st):
    if st.st_uid != 0:
        raise RuntimeError("runtime object is not root-owned")


def _check_current(prefix, fd):
    try:
        st = os.stat(CURRENT, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISLNK(st.st_mode):
        raise RuntimeError("runtime-current must be a root symlink")
    _root_owned(st)
    policy.assert_no_mutating_acl(prefix + "/" + CURRENT)
    target = os.readlink(CURRENT, dir_fd=fd)
    if target != RUNTIMES + "/" + _id(target.removeprefix(RUNTIMES + "/")):
        raise RuntimeError("invalid runtime-current target")
    policy.assert_protected(prefix + "/" + target, directory=True)
    return target


def _protect_tree(root, entries=None):
    # Root-created entries inherit no caller modes/owners/ACLs or xattrs.
    policy.assert_protected(root, directory=True)
    if entries is not None and stat.S_IMODE(os.lstat(root).st_mode) != 0o555:
        raise RuntimeError("installed runtime root is not immutable")
    def fail(error):
        raise error
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        for name in dirs + files:
            path = os.path.join(parent, name)
            st = os.lstat(path)
            _root_owned(st)
            if not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o022:
                raise RuntimeError("runtime staging modes are unsafe")
            policy.assert_no_mutating_acl(path)
            if entries is not None:
                item = entries.get(os.path.relpath(path, root))
                if item is None:
                    raise RuntimeError("unexpected installed runtime object")
                if item["type"] != "symlink" and stat.S_IMODE(st.st_mode) != item["mode"]:
                    raise RuntimeError("installed runtime mode differs from signed manifest")


def _protected_core(prefix):
    if os.geteuid() != 0:
        raise RuntimeError("installed runtime verification requires root")
    prefix = release._abs(os.fspath(prefix))
    if prefix not in _INSTALL_PREFIXES.get(sys.platform, frozenset()):
        raise RuntimeError("runtime prefix is not approved in the core")
    for module in (__file__, policy.__file__, release.__file__):
        policy.assert_protected(module)
    package = sys.modules.get(__package__)
    if package is None or not getattr(package, "__file__", None):
        raise RuntimeError("installed verifier requires the frozen core package")
    policy.assert_protected(package.__file__)
    policy.assert_protected(release.OPENSSL)
    policy.assert_protected(prefix, directory=True)
    return prefix


def _protected_bytes(path, limit, *, metadata=False):
    _, before = policy._assert_protected_stat(path)
    if metadata and stat.S_IMODE(before.st_mode) != 0o444:
        raise RuntimeError("installed runtime metadata is not immutable")
    return release._read_exact(path, limit, "protected runtime metadata", expected=before)


def verify_installed_runtime(prefix):
    """Verify the selected retained runtime without executing its contents.

    Returns manifest, runtime_root and python (the two paths are canonical).
    Frozen-core startup/import hash approval is a separate bootstrap gate.
    Provenance fields are signed assertions, not publisher verification here.
    """
    descriptors = []
    try:
        prefix = _protected_core(prefix)
        _, before = policy._assert_protected_stat(prefix, directory=True)
        prefix_fd = release._open_absolute_dir(prefix); descriptors.append(prefix_fd)
        if release._identity(os.fstat(prefix_fd)) != release._identity(before):
            raise RuntimeError("runtime prefix changed")
        current_before = os.stat(CURRENT, dir_fd=prefix_fd, follow_symlinks=False)
        target = _check_current(prefix, prefix_fd)
        if target is None:
            raise RuntimeError("no installed runtime is selected")
        runtime_id = target.split("/")[1]
        key = _protected_bytes(prefix + "/trust/runtime.pub", release.PUBLIC_KEY_MAX_BYTES)
        metadata = prefix + "/runtime-manifests/" + runtime_id
        raw = _protected_bytes(metadata + ".json", MAX_MANIFEST_BYTES, metadata=True)
        signature = _protected_bytes(metadata + ".sig", release.SIGNATURE_MAX_BYTES, metadata=True)
        doc = _decode_signed(raw, signature, key)
        if doc["runtime_id"] != runtime_id or doc["runtime_root"] != target:
            raise RuntimeError("selected runtime differs from signed manifest")
        root = prefix + "/" + target
        _, tree_before = policy._assert_protected_stat(root, directory=True)
        tree_fd = release._open_absolute_dir(root); descriptors.append(tree_fd)
        if release._identity(os.fstat(tree_fd)) != release._identity(tree_before):
            raise RuntimeError("installed runtime changed")
        _walk(tree_fd, doc["python"], expected=doc["entries"])
        _protect_tree(root, doc["entries"])
        if (_stable(tree_before) != _stable(os.fstat(tree_fd)) or
                _stable(current_before) != _stable(os.stat(CURRENT, dir_fd=prefix_fd, follow_symlinks=False)) or
                os.readlink(CURRENT, dir_fd=prefix_fd) != target):
            raise RuntimeError("installed runtime selection changed during verification")
        return {"manifest": doc, "runtime_root": root, "python": root + "/" + doc["python"]}
    except (OSError, policy.PolicyError, release.ReleaseError) as exc:
        raise RuntimeError("installed runtime protection or integrity check failed") from exc
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def install_runtime(bundle, prefix, root_public_key, *, expected_manifest_sha256=None):
    """Root frozen-core API. No archive extraction, payload execution or hooks.

    The exact fixed prefix and key location are part of the approved core.
    No existing runtime is overwritten. Selection changes only after every
    staged byte and link has matched the signed manifest twice. An optional
    independently approved manifest hash binds the exact retained signed bytes
    before any installation write.
    """
    if os.geteuid() != 0:
        raise RuntimeError("runtime installation requires root")
    if expected_manifest_sha256 is not None and (
            not isinstance(expected_manifest_sha256, str) or
            not re.fullmatch(r"[0-9a-fA-F]{64}", expected_manifest_sha256)):
        raise RuntimeError("expected manifest SHA256 must be 64 hexadecimal characters")
    prefix = release._abs(os.fspath(prefix))
    if prefix not in _INSTALL_PREFIXES.get(sys.platform, frozenset()):
        raise RuntimeError("runtime prefix is not approved in the core")
    if os.fspath(root_public_key) != prefix + "/trust/runtime.pub":
        raise RuntimeError("runtime key must be the fixed protected trust/runtime.pub")
    descriptors, staging, published, link = [], None, False, None
    created_metadata, meta_fd = [], None
    try:
        _protected_core(prefix)
        _, key_st = policy._assert_protected_stat(root_public_key)
        key = release._read_exact(root_public_key, release.PUBLIC_KEY_MAX_BYTES,
                                  "protected runtime key", expected=key_st)
        _, prefix_st = policy._assert_protected_stat(prefix, directory=True)
        prefix_fd = release._open_absolute_dir(prefix); descriptors.append(prefix_fd)
        if release._identity(os.fstat(prefix_fd)) != release._identity(prefix_st):
            raise RuntimeError("runtime prefix changed")
        _check_current(prefix, prefix_fd)
        bundle_fd = release._open_absolute_dir(os.fspath(bundle)); descriptors.append(bundle_fd)
        doc, raw, signature = _load(bundle_fd, key)
        if (expected_manifest_sha256 is not None and
                hashlib.sha256(raw).hexdigest() != expected_manifest_sha256.lower()):
            raise RuntimeError("runtime differs from the independently approved manifest")
        try:
            os.mkdir(RUNTIMES, 0o755, dir_fd=prefix_fd)
        except FileExistsError:
            pass
        policy.assert_protected(prefix + "/" + RUNTIMES, directory=True)
        runtimes_fd = release._open_dir(RUNTIMES, prefix_fd, "runtimes"); descriptors.append(runtimes_fd)
        runtime_id = doc["runtime_id"]
        if os.path.lexists(prefix + "/" + RUNTIMES + "/" + runtime_id):
            raise RuntimeError("runtime version already exists")
        stage_name = ".staging-" + secrets.token_hex(16)
        os.mkdir(stage_name, 0o700, dir_fd=runtimes_fd)
        staging = prefix + "/" + RUNTIMES + "/" + stage_name
        stage_fd = release._open_dir(stage_name, runtimes_fd, "runtime staging"); descriptors.append(stage_fd)
        source_fd = release._open_dir("payload", bundle_fd, "runtime source"); descriptors.append(source_fd)
        _walk(source_fd, doc["python"], expected=doc["entries"], destination=stage_fd)
        _walk(stage_fd, doc["python"], expected=doc["entries"])
        _protect_tree(staging)
        os.fchmod(stage_fd, 0o555)
        os.fsync(stage_fd)
        # Metadata lives outside the exact signed payload tree.
        metadata = prefix + "/runtime-manifests"
        try:
            os.mkdir(metadata, 0o755)
        except FileExistsError:
            pass
        policy.assert_protected(metadata, directory=True)
        meta_fd = release._open_absolute_dir(metadata); descriptors.append(meta_fd)
        for suffix, data in ((".json", raw), (".sig", signature)):
            out = os.open(runtime_id + suffix, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                          os.O_NOFOLLOW, 0o400, dir_fd=meta_fd)
            created_metadata.append(runtime_id + suffix)
            try:
                release._write_all(out, data, "runtime metadata")
                os.fchmod(out, 0o444)
                os.fsync(out)
            finally:
                os.close(out)
        os.fsync(meta_fd)
        # Root is the only directory writer; reject pre-existing versions above.
        os.rename(stage_name, runtime_id, src_dir_fd=runtimes_fd, dst_dir_fd=runtimes_fd)
        staging, published = None, True
        os.fsync(runtimes_fd)
        link = ".runtime-current-" + secrets.token_hex(16)
        os.symlink(RUNTIMES + "/" + runtime_id, link, dir_fd=prefix_fd)
        os.replace(link, CURRENT, src_dir_fd=prefix_fd, dst_dir_fd=prefix_fd)
        link = None
        try:
            os.fsync(prefix_fd)
        except OSError as exc:
            raise RuntimeError("runtime selected; directory durability is uncertain") from exc
        return prefix + "/" + doc["runtime_root"]
    except (OSError, policy.PolicyError, release.ReleaseError) as exc:
        raise RuntimeError("runtime installation protection or I/O check failed") from exc
    finally:
        if link is not None:
            with contextlib.suppress(OSError):
                os.unlink(link, dir_fd=prefix_fd)
        if not published and meta_fd is not None:
            for name in created_metadata:
                with contextlib.suppress(OSError):
                    os.unlink(name, dir_fd=meta_fd)
        for fd in reversed(descriptors):
            os.close(fd)
        if staging is not None and not published and os.path.isdir(staging):
            # Only the unpredictable root-created staging path is removed.
            for parent, dirs, _ in os.walk(staging, topdown=True, followlinks=False):
                os.chmod(parent, 0o700)
            shutil.rmtree(staging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("source_tree")
    build.add_argument("runtime_id")
    build.add_argument("--python-version", required=True)
    build.add_argument("--provenance", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("bundle")
    verify.add_argument("--public-key", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            provenance = json.loads(release._read_exact(args.provenance, 16384, "provenance"))
            doc = build_manifest(args.source_tree, args.runtime_id,
                python_version=args.python_version, provenance=provenance)
            sys.stdout.buffer.write(canonical_manifest(doc))
        else:
            doc = verify_runtime_bundle(args.bundle, args.public_key)
            print(doc["runtime_id"] + " " + doc["python_version"])
    except (RuntimeError, OSError, ValueError, release.ReleaseError) as exc:
        parser.exit(1, f"runtime: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
