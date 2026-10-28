"""Synthetic RSA-signed runtimes; no payload runs and no root changes."""
import copy
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))
from ai_zones import policy, release, runtime

PROVENANCE = {"url": "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.14.10%2B20261026-aarch64-apple-darwin-install_only_stripped.tar.gz",
    "release": "20261026", "commit": "a" * 40, "sha256": "b" * 64,
    "attestation_sha256": "c" * 64}


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.private = Path(cls.keys.name).resolve() / "synthetic.pem"
        cls.public = Path(cls.keys.name).resolve() / "synthetic.pub"
        for argv in (["genrsa", "-out", str(cls.private), "2048"],
                     ["rsa", "-in", str(cls.private), "-pubout", "-out", str(cls.public)]):
            subprocess.run(["/usr/bin/openssl"] + argv, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bundle = Path(self.tmp.name).resolve() / "bundle"
        self.payload = self.bundle / "payload"
        (self.payload / "bin").mkdir(parents=True)
        (self.payload / "lib").mkdir()
        self.marker = Path(self.tmp.name).resolve() / "MUST-NOT-EXECUTE"
        (self.payload / "bin/python3.14").write_text("#!/bin/sh\ntouch " + str(self.marker) + "\n")
        (self.payload / "bin/python3").symlink_to("python3.14")
        (self.payload / "lib/stdlib.py").write_text("synthetic = True\n")

    def manifest(self):
        return runtime.build_manifest(str(self.payload), "test-runtime",
            python_version="3.14.10", provenance=PROVENANCE)

    def sign(self, doc=None):
        doc = self.manifest() if doc is None else doc
        (self.bundle / "manifest.json").write_bytes(runtime.canonical_manifest(doc))
        subprocess.run(["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(self.private),
            "-out", str(self.bundle / "manifest.sig"), str(self.bundle / "manifest.json")],
            capture_output=True, check=True)
        return doc

    def verify(self):
        return runtime.verify_runtime_bundle(str(self.bundle), str(self.public))

    def test_signed_tree_verifies_without_execution(self):
        doc = self.sign()
        self.assertEqual(self.verify(), doc)
        self.assertFalse(self.marker.exists())
        self.assertEqual(doc["entries"]["bin/python3.14"]["mode"], 0o555)
        self.assertEqual(doc["entries"]["lib/stdlib.py"]["mode"], 0o444)
        self.assertEqual(doc["runtime_root"], "runtimes/test-runtime")

    def test_changed_payload_refused(self):
        self.sign()
        (self.payload / "lib/stdlib.py").write_text("tampered = True\n")
        with self.assertRaises(runtime.RuntimeError): self.verify()

    def test_source_is_not_retained_trust(self):
        self.sign(); self.verify()
        (self.payload / "lib/stdlib.py").write_text("after = True\n")
        with self.assertRaises(runtime.RuntimeError): self.verify()

    def test_signature_tampering_refused(self):
        self.sign()
        (self.bundle / "manifest.sig").write_bytes(b"bad signature")
        with self.assertRaises(runtime.RuntimeError): self.verify()

    def test_manifest_tampering_refused(self):
        self.sign()
        (self.bundle / "manifest.json").write_bytes(runtime.canonical_manifest(self.manifest()) + b" ")
        with self.assertRaises(runtime.RuntimeError): self.verify()

    def test_unknown_and_missing_entries_refused(self):
        self.sign()
        (self.payload / "extra").write_text("extra")
        with self.assertRaises(runtime.RuntimeError): self.verify()
        (self.payload / "extra").unlink()
        (self.payload / "lib/stdlib.py").unlink()
        with self.assertRaises(runtime.RuntimeError): self.verify()

    def test_symlink_escape_absolute_and_cycle_refused(self):
        link = self.payload / "bin/bad"
        for target in ("/etc/passwd", "../../escape", "bad", "missing"):
            with self.subTest(target=target):
                link.symlink_to(target)
                with self.assertRaises(runtime.RuntimeError): self.manifest()
                link.unlink()

    def test_symlink_component_escape_refused(self):
        (self.payload / "lib/out").symlink_to("../bin/python3.14")
        (self.payload / "bin/bad").symlink_to("../lib/out/child")
        with self.assertRaises(runtime.RuntimeError): self.manifest()

    def test_intermediate_symlink_dotdot_escape_refused(self):
        inside = self.payload / "manifest.json"
        inside.write_text("inside signed payload sentinel")
        doc = self.manifest()
        (self.payload / "lib/dir").symlink_to("../bin")
        bad = self.payload / "bin/bad"
        bad.symlink_to("../lib/dir/../../manifest.json")
        doc["entries"]["lib/dir"] = {"type": "symlink", "target": "../bin"}
        doc["entries"]["bin/bad"] = {"type": "symlink", "target": "../lib/dir/../../manifest.json"}
        self.sign(doc)
        # Read through the OS resolver to establish an actual escape, not
        # merely a disagreement between two path-normalization functions.
        self.assertEqual(bad.read_bytes(), (self.bundle / "manifest.json").read_bytes())
        self.assertNotEqual(bad.read_bytes(), inside.read_bytes())
        with self.subTest(consumer="builder"):
            with self.assertRaises(runtime.RuntimeError): self.manifest()
        with self.subTest(consumer="signed verifier"):
            with self.assertRaises(runtime.RuntimeError): self.verify()
        stage_path = Path(self.tmp.name).resolve() / "staging"
        stage_path.mkdir(mode=0o700)
        (stage_path.parent / "manifest.json").write_text("outside staging sentinel")
        source = release._open_absolute_dir(str(self.payload))
        target = release._open_absolute_dir(str(stage_path))
        try:
            with self.subTest(consumer="staging copier"):
                with self.assertRaises(runtime.RuntimeError):
                    runtime._walk(source, doc["python"], expected=doc["entries"], destination=target)
            staged_link = stage_path / "bin/bad"
            if staged_link.is_symlink():
                self.assertEqual(staged_link.read_text(), "outside staging sentinel")
        finally:
            os.close(source); os.close(target)
            for parent, dirs, files in os.walk(stage_path, followlinks=False):
                os.chmod(parent, 0o700)
                for name in dirs + files:
                    link = Path(parent) / name
                    if link.is_symlink(): link.unlink()

    def test_symlink_tampering_refused(self):
        self.sign()
        (self.payload / "bin/python3").unlink()
        (self.payload / "bin/python3").symlink_to("../lib/stdlib.py")
        with self.assertRaises(runtime.RuntimeError): self.verify()

    def test_hardlink_fifo_and_special_mode_refused(self):
        other = self.payload / "lib/other"
        os.link(self.payload / "lib/stdlib.py", other)
        with self.assertRaises(runtime.RuntimeError): self.manifest()
        other.unlink()
        os.mkfifo(other)
        with self.assertRaises(runtime.RuntimeError): self.manifest()
        other.unlink()
        (self.payload / "lib/stdlib.py").chmod(0o4644)
        with self.assertRaises(runtime.RuntimeError): self.manifest()

    def test_parent_symlink_refused(self):
        alias = Path(self.tmp.name).resolve() / "alias"
        alias.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(release.ReleaseError):
            runtime.build_manifest(str(alias / "payload"), "test", python_version="3.14.10", provenance=PROVENANCE)

    def test_invalid_manifest_and_unsupported_version_refused(self):
        doc = self.manifest()
        for key, value in (("python", "bin/evil"), ("executables", ["bin/python3.14", "bin/evil"]),
            ("runtime_root", "../policy"), ("runtime_id", ".."), ("python_version", "3.9.6"),
            ("platform", "linux")):
            with self.subTest(key=key):
                bad = copy.deepcopy(doc); bad[key] = value
                self.sign(bad)
                with self.assertRaises(runtime.RuntimeError): self.verify()

    def test_malformed_signed_field_types_fail_closed(self):
        doc = self.manifest()
        bad_release = copy.deepcopy(doc)
        bad_release["provenance"]["release"] = 20261026
        bad_mode = copy.deepcopy(doc)
        bad_mode["entries"]["lib"]["mode"] = float(0o555)
        bad_parent = copy.deepcopy(doc)
        bad_parent["entries"] = {"bin/python3.14": doc["entries"]["bin/python3.14"], "bin": "not an object"}
        for bad in (bad_release, bad_mode, bad_parent):
            with self.subTest(manifest=bad):
                self.sign(bad)
                with self.assertRaises(runtime.RuntimeError): self.verify()

    def test_source_type_swap_refused_before_copy(self):
        self.sign()
        (self.payload / "lib/stdlib.py").unlink()
        (self.payload / "lib/stdlib.py").mkdir()
        with self.assertRaises(runtime.RuntimeError): self.verify()

    def test_running_file_and_total_limits(self):
        for setting, value in (("MAX_FILE_BYTES", 5), ("MAX_TOTAL_BYTES", 5), ("MAX_ENTRIES", 2)):
            with self.subTest(setting=setting), mock.patch.object(runtime, setting, value):
                with self.assertRaises(runtime.RuntimeError): self.manifest()

    def test_growth_during_read_refused(self):
        parent = release._open_absolute_dir(str(self.payload / "lib"))
        self.addCleanup(os.close, parent)
        original = os.read
        changed = [False]
        def growing(fd, size):
            data = original(fd, size)
            if data and not changed[0]:
                changed[0] = True
                with (self.payload / "lib/stdlib.py").open("ab") as out: out.write(b"growth")
            return data
        with mock.patch.object(runtime.os, "read", side_effect=growing):
            with self.assertRaises(runtime.RuntimeError): runtime._file(parent, "stdlib.py")

    def test_short_writes_are_completed(self):
        out = Path(self.tmp.name).resolve() / "copy"
        fd = os.open(out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        parent = release._open_absolute_dir(str(self.payload / "lib"))
        original = os.write
        try:
            with mock.patch.object(release.os, "write", side_effect=lambda f, data: original(f, data[:2])):
                runtime._file(parent, "stdlib.py", destination=fd)
        finally:
            os.close(parent); os.close(fd)
        self.assertEqual(out.read_bytes(), (self.payload / "lib/stdlib.py").read_bytes())

    def test_streamed_copy_rehashes_and_freezes_complete_tree(self):
        doc = self.sign()
        source = release._open_absolute_dir(str(self.payload))
        target_path = Path(self.tmp.name).resolve() / "staging"
        target_path.mkdir(mode=0o700)
        target = release._open_absolute_dir(str(target_path))
        try:
            copied = runtime._walk(source, doc["python"], expected=doc["entries"], destination=target)
            self.assertEqual(copied, doc["entries"])
            self.assertEqual(runtime._walk(target, doc["python"], expected=doc["entries"]), copied)
            self.assertEqual((target_path / "bin/python3.14").stat().st_mode & 0o777, 0o555)
            self.assertEqual((target_path / "lib/stdlib.py").stat().st_mode & 0o777, 0o444)
            self.assertEqual((target_path / "lib").stat().st_mode & 0o777, 0o555)
            self.assertFalse(self.marker.exists())
            (target_path / "lib/stdlib.py").chmod(0o644)
            (target_path / "lib/stdlib.py").write_text("tampered staged bytes")
            with self.assertRaises(runtime.RuntimeError):
                runtime._walk(target, doc["python"], expected=doc["entries"])
        finally:
            os.close(source); os.close(target)

    def test_zero_write_refused(self):
        parent = release._open_absolute_dir(str(self.payload / "lib"))
        self.addCleanup(os.close, parent)
        fd = os.open(Path(self.tmp.name).resolve() / "copy", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        self.addCleanup(os.close, fd)
        with mock.patch.object(release.os, "write", return_value=0):
            with self.assertRaises(release.ReleaseError): runtime._file(parent, "stdlib.py", destination=fd)

    def test_build_and_install_privilege_boundaries(self):
        with mock.patch.object(runtime.os, "geteuid", return_value=0):
            with self.assertRaises(runtime.RuntimeError): self.manifest()
        with mock.patch.object(runtime.os, "geteuid", return_value=501):
            with self.assertRaises(runtime.RuntimeError):
                runtime.install_runtime(str(self.bundle), "/Library/AgentSandbox", "/Library/AgentSandbox/trust/runtime.pub")
        with mock.patch.object(runtime.os, "geteuid", return_value=0):
            with self.assertRaises(runtime.RuntimeError):
                runtime.install_runtime(str(self.bundle), "/arbitrary", "/arbitrary/trust/runtime.pub")
            with self.assertRaises(runtime.RuntimeError):
                runtime.install_runtime(str(self.bundle), "/Library/AgentSandbox", str(self.public))

    def test_root_import_or_key_protection_failure_stops_before_copy(self):
        self.sign()
        with mock.patch.object(runtime.os, "geteuid", return_value=0), \
             mock.patch.object(policy, "assert_protected", side_effect=policy.PolicyError("unprotected core")), \
             mock.patch.object(runtime, "_walk") as walk:
            with self.assertRaises(runtime.RuntimeError):
                runtime.install_runtime(str(self.bundle), "/Library/AgentSandbox", "/Library/AgentSandbox/trust/runtime.pub")
            walk.assert_not_called()
        with self.assertRaises(policy.PolicyError): policy.assert_protected(str(self.public))

    @contextlib.contextmanager
    def install_authority(self):
        """Only root authority is mocked; no privileged copy is performed."""
        prefix = Path(self.tmp.name).resolve() / "install-prefix"
        (prefix / "trust").mkdir(parents=True)
        key = prefix / "trust/runtime.pub"
        shutil.copyfile(self.public, key)
        with mock.patch.object(runtime.os, "geteuid", return_value=0), \
             mock.patch.object(runtime, "_INSTALL_PREFIXES", {sys.platform: {str(prefix)}}), \
             mock.patch.object(runtime, "_protected_core", return_value=str(prefix)), \
             mock.patch.object(policy, "_assert_protected_stat", side_effect=lambda p, directory=False: (os.fspath(p), os.lstat(p))):
            yield prefix, key

    def test_approved_manifest_denies_valid_signed_bundle_substitution_before_writes(self):
        self.sign()
        bundle_a = Path(self.tmp.name).resolve() / "bundle-a"
        shutil.copytree(self.bundle, bundle_a, symlinks=True)
        approved = hashlib.sha256((bundle_a / "manifest.json").read_bytes()).hexdigest()
        (self.payload / "lib/stdlib.py").write_text("different valid bundle B")
        self.sign()
        runtime.verify_runtime_bundle(str(bundle_a), str(self.public))
        self.verify()
        with self.install_authority() as (prefix, key):
            original_mkdir = os.mkdir
            def no_install_write(name, *args, **kwargs):
                if name == runtime.RUNTIMES or os.fspath(name).startswith(str(prefix) + "/"):
                    self.fail("installation wrote before checking the approved manifest")
                return original_mkdir(name, *args, **kwargs)
            with mock.patch.object(runtime.os, "mkdir", side_effect=no_install_write), \
                 mock.patch.object(runtime.os, "symlink") as symlink, \
                 mock.patch.object(runtime.os, "replace") as replace, \
                 mock.patch.object(runtime, "_walk") as walk:
                with self.assertRaisesRegex(runtime.RuntimeError, "approved manifest"):
                    runtime.install_runtime(str(self.bundle), str(prefix), str(key),
                        expected_manifest_sha256=approved)
                symlink.assert_not_called(); replace.assert_not_called(); walk.assert_not_called()
            self.assertEqual(sorted(str(p.relative_to(prefix)) for p in prefix.rglob("*")),
                ["trust", "trust/runtime.pub"])

    def test_matching_approved_manifest_and_default_reach_first_write(self):
        self.sign()
        digest = hashlib.sha256((self.bundle / "manifest.json").read_bytes()).hexdigest()
        class FirstInstallWrite(Exception): pass
        with self.install_authority() as (prefix, key):
            original_mkdir = os.mkdir
            def stop_at_install_write(name, *args, **kwargs):
                if name == runtime.RUNTIMES: raise FirstInstallWrite()
                return original_mkdir(name, *args, **kwargs)
            for expected in (digest, digest.upper(), None):
                with self.subTest(expected=expected), \
                     mock.patch.object(runtime.os, "mkdir", side_effect=stop_at_install_write):
                    with self.assertRaises(FirstInstallWrite):
                        runtime.install_runtime(str(self.bundle), str(prefix), str(key),
                            expected_manifest_sha256=expected)
            self.assertFalse((prefix / runtime.RUNTIMES).exists())

    def test_malformed_approved_manifest_hash_refused_before_loading(self):
        for expected in ("", "f" * 63, "g" * 64, "f" * 64 + "\n", 123, b"f" * 64):
            with self.subTest(expected=expected), \
                 mock.patch.object(runtime.os, "geteuid", return_value=0), \
                 mock.patch.object(runtime, "_load") as load, \
                 mock.patch.object(runtime.os, "mkdir") as mkdir:
                with self.assertRaisesRegex(runtime.RuntimeError, "manifest SHA256"):
                    runtime.install_runtime(str(self.bundle), "/Library/AgentSandbox",
                        "/Library/AgentSandbox/trust/runtime.pub", expected_manifest_sha256=expected)
                load.assert_not_called(); mkdir.assert_not_called()

    @contextlib.contextmanager
    def installed_fixture(self):
        """Real signed immutable bytes; only root authority is mocked."""
        doc = self.sign()
        prefix = Path(self.tmp.name).resolve() / "protected-prefix"
        root = prefix / doc["runtime_root"]
        shutil.copytree(self.payload, root, symlinks=True)
        for name, item in doc["entries"].items():
            if item["type"] != "symlink": (root / name).chmod(item["mode"])
        root.chmod(0o555)
        (prefix / "trust").mkdir()
        shutil.copyfile(self.public, prefix / "trust/runtime.pub")
        metadata = prefix / "runtime-manifests"
        metadata.mkdir()
        for source, suffix in (("manifest.json", ".json"), ("manifest.sig", ".sig")):
            path = metadata / (doc["runtime_id"] + suffix)
            shutil.copyfile(self.bundle / source, path); path.chmod(0o444)
        (prefix / runtime.CURRENT).symlink_to(doc["runtime_root"])
        with mock.patch.object(runtime, "_protected_core", return_value=str(prefix)), \
             mock.patch.object(runtime, "_root_owned"), \
             mock.patch.object(policy, "assert_protected", side_effect=lambda p, directory=False: os.fspath(p)), \
             mock.patch.object(policy, "_assert_protected_stat", side_effect=lambda p, directory=False: (os.fspath(p), os.lstat(p))):
            try:
                yield prefix, root, doc
            finally:
                for parent, dirs, files in os.walk(root, followlinks=False):
                    os.chmod(parent, 0o700)
                    for name in dirs + files:
                        path = Path(parent) / name
                        if path.is_symlink(): path.unlink()

    def test_installed_runtime_verifies_retained_signed_tree(self):
        with self.installed_fixture() as (prefix, root, doc):
            verified = runtime.verify_installed_runtime(str(prefix))
            self.assertEqual(verified, {"manifest": doc, "runtime_root": str(root),
                "python": str(root / doc["python"])})
            self.assertFalse(self.marker.exists())

    def test_installed_runtime_changed_bytes_refused(self):
        with self.installed_fixture() as (prefix, root, doc):
            path = root / "lib/stdlib.py"
            path.chmod(0o644); path.write_text("changed after install"); path.chmod(0o444)
            with self.assertRaises(runtime.RuntimeError): runtime.verify_installed_runtime(str(prefix))

    def test_installed_runtime_changed_link_refused(self):
        with self.installed_fixture() as (prefix, root, doc):
            (root / "bin").chmod(0o755)
            link = root / "bin/python3"
            link.unlink(); link.symlink_to("python3.13")
            (root / "bin").chmod(0o555)
            with self.assertRaises(runtime.RuntimeError): runtime.verify_installed_runtime(str(prefix))

    def test_installed_runtime_current_escape_refused(self):
        with self.installed_fixture() as (prefix, root, doc):
            link = prefix / runtime.CURRENT
            link.unlink(); link.symlink_to("runtimes/../untrusted")
            with self.assertRaises(runtime.RuntimeError): runtime.verify_installed_runtime(str(prefix))

    def corrupt_installed_metadata(self, suffix):
        with self.installed_fixture() as (prefix, root, doc):
            path = prefix / "runtime-manifests" / (doc["runtime_id"] + suffix)
            path.chmod(0o644); path.write_bytes(b"tampered"); path.chmod(0o444)
            with self.assertRaises(runtime.RuntimeError): runtime.verify_installed_runtime(str(prefix))

    def test_installed_runtime_changed_manifest_refused(self):
        self.corrupt_installed_metadata(".json")

    def test_installed_runtime_changed_signature_refused(self):
        self.corrupt_installed_metadata(".sig")

    def test_installed_runtime_signed_id_mismatch_refused(self):
        with self.installed_fixture() as (prefix, root, doc):
            bad = copy.deepcopy(doc)
            bad["runtime_id"] = "other-version"
            bad["runtime_root"] = "runtimes/other-version"
            self.sign(bad)
            for source, suffix in (("manifest.json", ".json"), ("manifest.sig", ".sig")):
                path = prefix / "runtime-manifests" / (doc["runtime_id"] + suffix)
                path.chmod(0o644)
                shutil.copyfile(self.bundle / source, path); path.chmod(0o444)
            with self.assertRaises(runtime.RuntimeError): runtime.verify_installed_runtime(str(prefix))

    def test_installed_runtime_current_swap_during_verification_refused(self):
        with self.installed_fixture() as (prefix, root, doc):
            original = runtime._walk
            def swap(*args, **kwargs):
                result = original(*args, **kwargs)
                replacement = prefix / ".replacement"
                replacement.symlink_to(doc["runtime_root"])
                os.replace(replacement, prefix / runtime.CURRENT)
                return result
            with mock.patch.object(runtime, "_walk", side_effect=swap):
                with self.assertRaises(runtime.RuntimeError): runtime.verify_installed_runtime(str(prefix))

    def test_installed_runtime_unsigned_extra_file_refused(self):
        with self.installed_fixture() as (prefix, root, doc):
            (root / "lib").chmod(0o755)
            (root / "lib/unlisted").write_text("extra")
            (root / "lib").chmod(0o555)
            with self.assertRaises(runtime.RuntimeError): runtime.verify_installed_runtime(str(prefix))

    def test_installed_runtime_mutable_file_mode_refused(self):
        with self.installed_fixture() as (prefix, root, doc):
            (root / "lib/stdlib.py").chmod(0o644)
            with self.assertRaises(runtime.RuntimeError): runtime.verify_installed_runtime(str(prefix))

    def test_installed_runtime_protection_failure_stops_before_hashing(self):
        with self.installed_fixture() as (prefix, root, doc):
            with mock.patch.object(policy, "assert_protected", side_effect=policy.PolicyError("unprotected")), \
                 mock.patch.object(runtime, "_walk") as walk:
                with self.assertRaises(runtime.RuntimeError): runtime.verify_installed_runtime(str(prefix))
                walk.assert_not_called()

    def test_installed_runtime_root_boundary(self):
        with mock.patch.object(runtime.os, "geteuid", return_value=501):
            with self.assertRaises(runtime.RuntimeError): runtime.verify_installed_runtime("/Library/AgentSandbox")
        with self.assertRaises(runtime.RuntimeError): runtime._root_owned(os.lstat(self.payload))

    def test_no_cli_install_command(self):
        with self.assertRaises(SystemExit) as result:
            runtime.main(["install", str(self.bundle)])
        self.assertEqual(result.exception.code, 2)


if __name__ == "__main__": unittest.main()
