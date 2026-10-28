"""Tests for ai_zones.release: signed bundle verification and root install.

Signatures are real RSA/SHA-256 signatures produced by /usr/bin/openssl in
a temporary directory; tampering tests mutate bytes after signing.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

from ai_zones import release  # noqa: E402
from ai_zones.release import ReleaseError, main, verify_bundle  # noqa: E402

OPENSSL = "/usr/bin/openssl"

GOOD_PAYLOAD = {
    "ai_zones/zone_cli.py": b"print('zone')\n",
    "bin/ai": b"#!/bin/sh\necho ai\n",
}


def make_keys(tmp):
    priv = os.path.join(tmp, "release-priv.pem")
    pub = os.path.join(tmp, "release-pub.pem")
    subprocess.run([OPENSSL, "genrsa", "-out", priv, "2048"],
                   check=True, capture_output=True)
    subprocess.run([OPENSSL, "rsa", "-in", priv, "-pubout", "-out", pub],
                   check=True, capture_output=True)
    return priv, pub


def sign(priv, data_path, sig_path):
    subprocess.run(
        [OPENSSL, "dgst", "-sha256", "-sign", priv, "-out", sig_path, data_path],
        check=True, capture_output=True,
    )


def build_bundle(tmp, payload=None, release_id="1.0.0", files=None,
                 manifest_text=None, sign_key=None):
    """Create bundle/ with manifest.json, manifest.sig and payload/."""
    bundle = os.path.join(tmp, "bundle")
    payload_dir = os.path.join(bundle, "payload")
    os.makedirs(os.path.join(payload_dir, "ai_zones"))
    os.makedirs(os.path.join(payload_dir, "bin"))
    payload = payload if payload is not None else GOOD_PAYLOAD
    for name, data in payload.items():
        path = os.path.join(payload_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    if manifest_text is None:
        digests = files if files is not None else {
            name: hashlib.sha256(data).hexdigest()
            for name, data in payload.items()
        }
        manifest_text = json.dumps(
            {"version": 1, "release": release_id, "files": digests},
            sort_keys=True,
        )
    manifest_path = os.path.join(bundle, "manifest.json")
    with open(manifest_path, "wb") as fh:
        fh.write(manifest_text.encode("utf-8"))
    priv = sign_key
    sig_path = os.path.join(bundle, "manifest.sig")
    sign(priv, manifest_path, sig_path)
    return bundle


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir()))
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.priv, self.pub = make_keys(self.tmp)
        self.bundle = build_bundle(self.tmp, sign_key=self.priv)


class VerifyTests(Fixture):
    def test_good_bundle_verifies_and_returns_verified_bytes(self):
        release_id, verified = verify_bundle(self.bundle, self.pub)
        self.assertEqual(release_id, "1.0.0")
        self.assertEqual(set(verified), set(GOOD_PAYLOAD))
        for name, data in GOOD_PAYLOAD.items():
            self.assertEqual(verified[name], data)

    def test_only_named_boundary_service_binaries_are_allowed(self):
        payload = {"bin/sloptools-agent": b"synthetic sloptools executable",
                   "bin/helpy-agent": b"synthetic helpy executable"}
        bundle = build_bundle(os.path.join(self.tmp, "services"), payload=payload, sign_key=self.priv)
        self.assertEqual(verify_bundle(bundle, self.pub)[1], payload)

    def test_verify_performs_no_mutation(self):
        prefix = os.path.join(self.tmp, "prefix")
        verify_bundle(self.bundle, self.pub)
        self.assertFalse(os.path.exists(prefix))

    def test_wrong_public_key_refused(self):
        _, other_pub = make_keys(self.tmp)
        with self.assertRaises(ReleaseError):
            verify_bundle(self.bundle, other_pub)

    def test_tampered_payload_refused(self):
        target = os.path.join(self.bundle, "payload", "bin", "ai")
        with open(target, "wb") as fh:
            fh.write(b"#!/bin/sh\necho evil\n")
        with self.assertRaises(ReleaseError) as ctx:
            verify_bundle(self.bundle, self.pub)
        self.assertIn("sha256 mismatch", str(ctx.exception))

    def test_tampered_manifest_refused_by_signature(self):
        m = os.path.join(self.bundle, "manifest.json")
        doc = json.loads(Path(m).read_text())
        doc["release"] = "9.9.9-evil"
        with open(m, "wb") as fh:
            fh.write(json.dumps(doc, sort_keys=True).encode("utf-8"))
        with self.assertRaises(ReleaseError) as ctx:
            verify_bundle(self.bundle, self.pub)
        self.assertIn("signature", str(ctx.exception).lower())

    def test_extra_payload_file_refused(self):
        extra = os.path.join(self.bundle, "payload", "ai_zones", "extra.py")
        Path(extra).write_bytes(b"print('extra')\n")
        with self.assertRaises(ReleaseError):
            verify_bundle(self.bundle, self.pub)

    def test_missing_payload_file_refused(self):
        os.remove(os.path.join(self.bundle, "payload", "bin", "ai"))
        with self.assertRaises(ReleaseError):
            verify_bundle(self.bundle, self.pub)

    def test_missing_members_refused(self):
        os.remove(os.path.join(self.bundle, "manifest.sig"))
        with self.assertRaises(ReleaseError):
            verify_bundle(self.bundle, self.pub)

    def test_symlink_in_payload_refused(self):
        os.remove(os.path.join(self.bundle, "payload", "bin", "ai"))
        os.symlink("/bin/sh", os.path.join(self.bundle, "payload", "bin", "ai"))
        with self.assertRaises(ReleaseError):
            verify_bundle(self.bundle, self.pub)

    def test_hardlink_in_payload_refused(self):
        a = os.path.join(self.bundle, "payload", "ai_zones", "zone_cli.py")
        b = os.path.join(self.bundle, "payload", "ai_zones", "zone_cli2.py")
        os.link(a, b)
        with self.assertRaises(ReleaseError):
            verify_bundle(self.bundle, self.pub)

    def test_special_file_in_payload_refused(self):
        os.mkfifo(os.path.join(self.bundle, "payload", "ai_zones", "fifo"))
        with self.assertRaises(ReleaseError):
            verify_bundle(self.bundle, self.pub)

    def test_unexpected_payload_directory_refused(self):
        os.makedirs(os.path.join(self.bundle, "payload", "etc", "cron.d"),
                    exist_ok=True)
        with self.assertRaises(ReleaseError):
            verify_bundle(self.bundle, self.pub)

    def test_manifest_duplicate_keys_refused(self):
        text = json.dumps(
            {"version": 1, "release": "1.0.0", "files": {
                "bin/ai": hashlib.sha256(GOOD_PAYLOAD["bin/ai"]).hexdigest(),
                "ai_zones/zone_cli.py":
                    hashlib.sha256(GOOD_PAYLOAD["ai_zones/zone_cli.py"]).hexdigest(),
            }}, sort_keys=True,
        )
        text = text.replace('"version": 1', '"version": 1, "version": 1')
        bundle = build_bundle(os.path.join(self.tmp, "dup"),
                              manifest_text=text, sign_key=self.priv)
        with self.assertRaises(ReleaseError):
            verify_bundle(bundle, self.pub)

    def test_manifest_wrong_version_refused(self):
        text = json.dumps({"version": 2, "release": "1.0.0",
                          "files": {"bin/ai": "0" * 64}})
        bundle = build_bundle(os.path.join(self.tmp, "v2"),
                              manifest_text=text, sign_key=self.priv)
        with self.assertRaises(ReleaseError):
            verify_bundle(bundle, self.pub)

    def test_manifest_unknown_key_refused(self):
        text = json.dumps({"version": 1, "release": "1.0.0",
                           "files": {"bin/ai": "0" * 64}, "hooks": {}})
        bundle = build_bundle(os.path.join(self.tmp, "hook"),
                              manifest_text=text, sign_key=self.priv)
        with self.assertRaises(ReleaseError):
            verify_bundle(bundle, self.pub)

    def test_manifest_rejects_traversal_and_disallowed_names(self):
        for name in ("../evil.py", "bin/../bin/ai", "/etc/passwd",
                     "bin/evil", "bin/sh", "bin/python3", "bin/vfkit", "bin/helpy",
                     "hooks/x.sh", "ai_zones/x.txt", "README"):
            text = json.dumps({"version": 1, "release": "1.0.0",
                               "files": {name: "0" * 64}})
            with self.assertRaises(ReleaseError, msg=name):
                release.validate_manifest(text.encode("utf-8"))

    def test_manifest_rejects_unsafe_release_ids(self):
        for rid in ("", "a/b", "..", "-lead", ".hidden", "x" * 65, "a b"):
            text = json.dumps({"version": 1, "release": rid,
                               "files": {"bin/ai": "0" * 64}})
            with self.assertRaises(ReleaseError, msg=rid):
                release.validate_manifest(text.encode("utf-8"))

    def test_manifest_rejects_bad_digest_format(self):
        text = json.dumps({"version": 1, "release": "1.0.0",
                           "files": {"bin/ai": "DEADBEEF"}})
        with self.assertRaises(ReleaseError):
            release.validate_manifest(text.encode("utf-8"))

    def test_oversized_payload_file_refused(self):
        with mock.patch.object(release, "PAYLOAD_FILE_MAX_BYTES", 8):
            with self.assertRaises(ReleaseError):
                verify_bundle(self.bundle, self.pub)

    def test_symlinked_manifest_refused(self):
        m = os.path.join(self.bundle, "manifest.json")
        tmp = m + ".real"
        os.rename(m, tmp)
        os.symlink(tmp, m)
        with self.assertRaises(ReleaseError):
            verify_bundle(self.bundle, self.pub)


class InstallTests(Fixture):
    def _prefix(self):
        prefix = os.path.join(self.tmp, "prefix")
        os.makedirs(os.path.join(prefix, "releases"))
        return prefix

    def copy_verified(self, prefix, bundle):
        # Exercise copy/commit as this unprivileged user, without claiming authority.
        rid, verified = verify_bundle(bundle, self.pub)
        prefix_fd = release._open_absolute_dir(prefix)
        try:
            releases_fd = release._open_dir("releases", prefix_fd, "fixture releases")
            try:
                release._install_verified(prefix_fd, releases_fd, rid, verified)
            finally:
                os.close(releases_fd)
        finally:
            os.close(prefix_fd)
        return os.path.join(prefix, "releases", rid)

    def test_public_install_refuses_non_root(self):
        prefix = self._prefix()
        self.assertNotEqual(os.geteuid(), 0, "run unit tests unprivileged")
        with self.assertRaisesRegex(ReleaseError, "root"):
            release.install_bundle(self.bundle, prefix, self.pub)
        self.assertEqual(os.listdir(os.path.join(prefix, "releases")), [])

    def test_public_install_refuses_unprotected_core_and_prefix(self):
        prefix = self._prefix()
        with mock.patch("os.geteuid", return_value=0):
            with self.assertRaises(ReleaseError):
                release.install_bundle(self.bundle, prefix, self.pub)
        self.assertEqual(os.listdir(os.path.join(prefix, "releases")), [])

    def test_retained_bytes_are_installed_under_exact_subdirectories(self):
        prefix = self._prefix()
        target = self.copy_verified(prefix, self.bundle)
        for name, data in GOOD_PAYLOAD.items():
            path = Path(target) / name
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o444)
        self.assertEqual(set(p.name for p in Path(target).iterdir()), {"ai_zones", "bin"})
        self.assertEqual(os.readlink(os.path.join(prefix, "current")), "releases/1.0.0")

    def test_existing_release_refused_and_previous_release_retained(self):
        prefix = self._prefix()
        self.copy_verified(prefix, self.bundle)
        before = os.readlink(os.path.join(prefix, "current"))
        with self.assertRaises(ReleaseError):
            self.copy_verified(prefix, self.bundle)
        self.assertEqual(os.readlink(os.path.join(prefix, "current")), before)
        bundle2 = build_bundle(os.path.join(self.tmp, "second"), release_id="1.0.1",
                               payload={"ai_zones/zone_cli.py": b"print('v2')"}, sign_key=self.priv)
        self.copy_verified(prefix, bundle2)
        self.assertEqual(sorted(os.listdir(os.path.join(prefix, "releases"))), ["1.0.0", "1.0.1"])
        self.assertEqual(os.readlink(os.path.join(prefix, "current")), "releases/1.0.1")

    def test_short_writes_are_completed(self):
        prefix = self._prefix()
        real_write = os.write
        def short_write(fd, data):
            return real_write(fd, data[:3])
        with mock.patch.object(release.os, "write", side_effect=short_write):
            target = self.copy_verified(prefix, self.bundle)
        for name, data in GOOD_PAYLOAD.items():
            self.assertEqual((Path(target) / name).read_bytes(), data)

    def test_write_failure_cleans_only_new_release_and_preserves_current(self):
        prefix = self._prefix()
        self.copy_verified(prefix, self.bundle)
        rid, data = "1.0.1", dict(GOOD_PAYLOAD)
        prefix_fd = release._open_absolute_dir(prefix)
        releases_fd = release._open_dir("releases", prefix_fd, "fixture")
        real_write, calls = os.write, 0
        def fail(fd, value):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic storage failure")
            return real_write(fd, value)
        try:
            with mock.patch.object(release.os, "write", side_effect=fail):
                with self.assertRaises(ReleaseError):
                    release._install_verified(prefix_fd, releases_fd, rid, data)
        finally:
            os.close(releases_fd)
            os.close(prefix_fd)
        self.assertEqual(os.readlink(os.path.join(prefix, "current")), "releases/1.0.0")
        self.assertEqual(os.listdir(os.path.join(prefix, "releases")), ["1.0.0"])
        self.assertEqual(set(os.listdir(prefix)), {"current", "releases"})

    def test_zero_write_and_postcopy_corruption_do_not_select_release(self):
        prefix = self._prefix()
        for fault in ("zero", "corrupt"):
            pfd = release._open_absolute_dir(prefix)
            rfd = release._open_dir("releases", pfd, "fixture")
            patch = (mock.patch.object(release.os, "write", return_value=0) if fault == "zero" else
                     mock.patch.object(release, "_read_exact", return_value=b"corrupted"))
            try:
                with patch, self.assertRaises(ReleaseError):
                    release._install_verified(pfd, rfd, "1.0.0", GOOD_PAYLOAD)
            finally:
                os.close(rfd)
                os.close(pfd)
            self.assertFalse(os.path.lexists(os.path.join(prefix, "current")))
            self.assertEqual(os.listdir(os.path.join(prefix, "releases")), [])

    def test_interrupt_after_current_replacement_keeps_selected_files(self):
        prefix = self._prefix()
        pfd = release._open_absolute_dir(prefix)
        rfd = release._open_dir("releases", pfd, "fixture")
        replace = os.replace
        def interrupted(*args, **kwargs):
            replace(*args, **kwargs)
            raise KeyboardInterrupt("synthetic post-commit interruption")
        try:
            with mock.patch.object(release.os, "replace", side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    release._install_verified(pfd, rfd, "1.0.0", GOOD_PAYLOAD)
        finally:
            os.close(rfd)
            os.close(pfd)
        self.assertEqual(os.readlink(os.path.join(prefix, "current")), "releases/1.0.0")
        for name, data in GOOD_PAYLOAD.items():
            self.assertEqual((Path(prefix) / "current" / name).read_bytes(), data)

    def test_signed_modules_and_service_executables_are_never_run(self):
        prefix = self._prefix()
        marker = Path(self.tmp) / "must-not-exist"
        script = ("#!/bin/sh\nprintf executed > " + repr(str(marker)) + "\n").encode()
        payload = {"ai_zones/evil.py": ("open(" + repr(str(marker)) + ", 'w').write('executed')").encode(),
                   "bin/sloptools-agent": script, "bin/helpy-agent": script}
        bundle = build_bundle(os.path.join(self.tmp, "evil"), payload=payload, sign_key=self.priv)
        old_umask = os.umask(0o077)
        try:
            target = Path(self.copy_verified(prefix, bundle))
        finally:
            os.umask(old_umask)
        self.assertFalse(marker.exists())
        for name, data in payload.items():
            path = target / name
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o555 if name.startswith("bin/") else 0o444)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((target / "bin").stat().st_mode), 0o755)


class VerificationRegressions(Fixture):
    def test_payload_symlink_ancestor_is_refused(self):
        payload = Path(self.bundle) / "payload"
        original = payload / "ai_zones"
        elsewhere = Path(self.tmp) / "elsewhere"
        original.rename(elsewhere)
        original.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(ReleaseError):
            verify_bundle(self.bundle, self.pub)

    def test_bundle_and_key_symlink_ancestors_are_refused(self):
        alias = Path(self.tmp) / "alias"
        alias.symlink_to(self.tmp, target_is_directory=True)
        for bundle, key in ((str(alias / "bundle"), self.pub),
                            (self.bundle, str(alias / Path(self.pub).name))):
            with self.subTest(bundle=bundle), self.assertRaises(ReleaseError):
                verify_bundle(bundle, key)

    def test_key_is_snapshotted_before_verifier_runs(self):
        real_verify = release._verify_signature
        original = Path(self.pub).read_bytes()
        def replace_key(manifest, signature, key):
            self.assertEqual(key, original)
            Path(self.pub).write_bytes(b"invalid replacement")
            return real_verify(manifest, signature, key)
        with mock.patch.object(release, "_verify_signature", side_effect=replace_key):
            self.assertEqual(verify_bundle(self.bundle, self.pub)[1], GOOD_PAYLOAD)

    def test_source_subdirectory_rename_keeps_pinned_files(self):
        original_read = release._read_exact
        changed = False
        def rename(path, limit, where, *args, **kwargs):
            nonlocal changed
            if where == "payload file" and path == "zone_cli.py" and not changed:
                changed = True
                source = Path(self.bundle) / "payload" / "ai_zones"
                source.rename(Path(self.tmp) / "retained-inode")
                source.mkdir()
                (source / "zone_cli.py").write_text("wrong replacement")
            return original_read(path, limit, where, *args, **kwargs)
        with mock.patch.object(release, "_read_exact", side_effect=rename):
            self.assertEqual(verify_bundle(self.bundle, self.pub)[1], GOOD_PAYLOAD)

    def test_aggregate_and_manifest_count_limits_are_enforced(self):
        with mock.patch.object(release, "BUNDLE_TOTAL_MAX_BYTES", 20):
            with self.assertRaises(ReleaseError):
                verify_bundle(self.bundle, self.pub)
        manifest = {"version": 1, "release": "r1", "files": {
            "ai_zones/f%d.py" % i: "0" * 64 for i in range(129)}}
        with self.assertRaises(ReleaseError):
            release.validate_manifest(json.dumps(manifest).encode())

    def test_fifo_member_is_refused_without_waiting_for_writer(self):
        for relative in ("manifest.json", "manifest.sig", "payload/bin/ai"):
            path = Path(self.bundle) / relative
            saved = path.read_bytes()
            path.unlink()
            os.mkfifo(path)
            try:
                with self.subTest(relative=relative), self.assertRaises(ReleaseError):
                    verify_bundle(self.bundle, self.pub)
            finally:
                path.unlink()
                path.write_bytes(saved)

    def test_growing_file_hits_running_limit(self):
        path = Path(self.tmp) / "grow"
        path.write_bytes(b"x")
        real_read, grown = os.read, False
        def read(fd, size):
            nonlocal grown
            if not grown:
                grown = True
                with path.open("ab") as f:
                    f.write(b"x" * 200)
            return real_read(fd, size)
        with mock.patch.object(release.os, "read", side_effect=read):
            with self.assertRaisesRegex(ReleaseError, "running byte limit"):
                release._read_exact(str(path), 128, "growing fixture")

    def test_verification_ignores_caller_openssl_and_temp_environment(self):
        config = Path(self.tmp) / "bad-openssl.cnf"
        config.write_text("[invalid-without-closing-bracket")
        with mock.patch.dict(os.environ, {"OPENSSL_CONF": str(config),
                                         "DYLD_INSERT_LIBRARIES": "/does/not/exist",
                                         "TMPDIR": "/does/not/exist"}):
            self.assertEqual(verify_bundle(self.bundle, self.pub)[1], GOOD_PAYLOAD)


_ROOT_TEST_PARENT = os.environ.get("AI_ZONES_ROOT_TEST_DIR", "")


@unittest.skipUnless(os.geteuid() == 0 and bool(_ROOT_TEST_PARENT) and Path(_ROOT_TEST_PARENT).is_dir(),
                     "explicit protected root fixture required")
class ProtectedInstallTests(unittest.TestCase):
    def setUp(self):
        release.assert_protected(_ROOT_TEST_PARENT, directory=True)
        self.tmp = tempfile.mkdtemp(prefix="case-", dir=_ROOT_TEST_PARENT)
        self.addCleanup(shutil.rmtree, self.tmp)
        self.priv, self.pub = make_keys(self.tmp)
        self.bundle = build_bundle(self.tmp, sign_key=self.priv)
        self.prefix = str(Path(self.tmp) / "prefix")
        Path(self.prefix).mkdir(mode=0o755)

    def test_actual_root_install_update_and_tampering(self):
        first = release.install_bundle(self.bundle, self.prefix, self.pub)
        for name, data in GOOD_PAYLOAD.items():
            self.assertEqual((Path(first) / name).read_bytes(), data)
        second = build_bundle(os.path.join(self.tmp, "second"), release_id="1.0.1",
                              payload={"ai_zones/zone_cli.py": b"print('next')"}, sign_key=self.priv)
        release.install_bundle(second, self.prefix, self.pub)
        current = Path(self.prefix) / "current"
        self.assertEqual(os.readlink(current), "releases/1.0.1")
        self.assertTrue(Path(first).is_dir())
        (Path(second) / "payload" / "ai_zones" / "zone_cli.py").write_bytes(b"tamper")
        before = sorted((Path(self.prefix) / "releases").iterdir())
        with self.assertRaises(ReleaseError):
            release.install_bundle(second, self.prefix, self.pub)
        self.assertEqual(os.readlink(current), "releases/1.0.1")
        self.assertEqual(sorted((Path(self.prefix) / "releases").iterdir()), before)

    def test_symlink_or_writable_release_directory_is_rejected_before_writes(self):
        outside = Path(self.tmp) / "outside"
        outside.mkdir()
        releases = Path(self.prefix) / "releases"
        releases.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ReleaseError):
            release.install_bundle(self.bundle, self.prefix, self.pub)
        self.assertEqual(list(outside.iterdir()), [])
        releases.unlink()
        releases.mkdir()
        releases.chmod(0o777)
        with self.assertRaises(ReleaseError):
            release.install_bundle(self.bundle, self.prefix, self.pub)
        self.assertEqual(list(releases.iterdir()), [])

    @unittest.skipUnless(sys.platform == "darwin", "Darwin extended ACLs")
    def test_mutating_acl_on_root_policy_or_release_directory_is_refused(self):
        from ai_zones.policy import load_policy, PolicyError
        p = Path(self.tmp) / "policy.json"
        zone = {"cpus": 4, "memory_mib": 4096, "disk_gib": 64, "files": None, "model": None}
        p.write_text(json.dumps({"version": 1, "zones": {"internal": zone, "confidential": zone}}))
        self.assertEqual(load_policy(str(p)).zones["internal"].cpus, 4)
        for right in ("writeattr", "writeextattr", "writesecurity", "chown"):
            subprocess.run(["/bin/chmod", "+a", "everyone allow " + right, str(p)], check=True)
            try:
                with self.subTest(right=right), self.assertRaises(PolicyError):
                    load_policy(str(p))
            finally:
                subprocess.run(["/bin/chmod", "-N", str(p)], check=True)
        releases = Path(self.prefix) / "releases"
        releases.mkdir()
        subprocess.run(["/bin/chmod", "+a", "everyone allow writesecurity", str(releases)], check=True)
        try:
            with self.assertRaises(ReleaseError):
                release.install_bundle(self.bundle, self.prefix, self.pub)
            self.assertEqual(list(releases.iterdir()), [])
        finally:
            subprocess.run(["/bin/chmod", "-N", str(releases)], check=True)

    def test_unprivileged_identity_cannot_mutate_core_or_installed_files(self):
        import pwd
        from ai_zones import policy
        identity = pwd.getpwnam("nobody")
        target = release.install_bundle(self.bundle, self.prefix, self.pub)
        os.chmod(self.tmp, 0o755)
        def drop_identity():
            os.setgroups([])
            os.setgid(identity.pw_gid)
            os.setuid(identity.pw_uid)
        probe = ("import os,sys; p=sys.argv[1]; fd=os.open(p,os.O_RDONLY); os.close(fd); "
                 "\ntry: os.open(p,os.O_WRONLY)"
                 "\nexcept PermissionError: raise SystemExit(0)"
                 "\nraise SystemExit('unexpected write authority')")
        for path in (policy.__file__, str(Path(target) / "bin" / "ai")):
            result = subprocess.run([sys.executable, "-I", "-S", "-c", probe, path],
                                    cwd="/", env={"PATH": "/usr/bin:/bin"},
                                    preexec_fn=drop_identity, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_unexpected_current_target_and_signed_code_never_run(self):
        current = Path(self.prefix) / "current"
        current.symlink_to("../outside")
        with self.assertRaises(ReleaseError):
            release.install_bundle(self.bundle, self.prefix, self.pub)
        current.unlink()
        marker = Path(self.tmp) / "must-not-exist"
        bundle = build_bundle(os.path.join(self.tmp, "code"), release_id="1.0.2",
                              payload={"ai_zones/evil.py": ("open(" + repr(str(marker)) + ", 'w').write('executed')").encode(),
                                       "bin/sloptools-agent": b"#!/bin/sh\nexit 99\n",
                                       "bin/helpy-agent": b"#!/bin/sh\nexit 98\n"},
                              sign_key=self.priv)
        release.install_bundle(bundle, self.prefix, self.pub)
        self.assertFalse(marker.exists())


class CliTests(Fixture):
    def test_cli_verify_ok(self):
        prefix = os.path.join(self.tmp, "prefix")
        self.assertEqual(
            main(["verify", "--bundle", self.bundle, "--prefix", prefix,
                 "--public-key", self.pub]),
            0,
        )

    def test_cli_rejects_relative_paths(self):
        self.assertEqual(
            main(["verify", "--bundle", "bundle", "--prefix", "/x",
                 "--public-key", self.pub]),
            1,
        )

    def test_cli_install_refuses_non_root(self):
        self.assertEqual(
            main(["install", "--bundle", self.bundle,
                 "--prefix", os.path.join(self.tmp, "prefix"),
                 "--public-key", self.pub]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
