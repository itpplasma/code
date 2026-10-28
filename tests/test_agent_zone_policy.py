"""Tests for ai_zones.policy: fail-closed two-zone policy validation."""

import json
import os
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

from ai_zones import policy as policy_module
from ai_zones.policy import (  # noqa: E402
    PolicyError,
    assert_protected,
    assert_no_mutating_acl,
    load_policy,
    validate_policy,
)


def valid_policy_dict():
    return {
        "version": 1,
        "zones": {
            "internal": {
                "cpus": 8,
                "memory_mib": 16384,
                "disk_gib": 200,
                "files": {"root": "/srv/sandbox-example/internal", "writable": True},
                "model": {
                    "url": "https://llm.example.internal",
                    "name": "example-internal-model",
                    "token_file": "/etc/sandbox-example/internal-token",
                },
            },
            "confidential": {
                "cpus": 4,
                "memory_mib": 8192,
                "disk_gib": 100,
                "files": None,
                "model": {
                    "url": "http://127.0.0.1:8080",
                    "name": "example-local-model",
                    "token_file": None,
                },
            },
        },
    }


def fake_stat(mode, uid):
    return os.stat_result(
        (mode, 1, 1, 1, uid, 0, 0, 0, 0, 0)
    )


class ValidationTests(unittest.TestCase):
    def test_valid_document(self):
        policy = validate_policy(valid_policy_dict())
        self.assertEqual(policy.version, 1)
        self.assertEqual(set(policy.zones), {"internal", "confidential"})
        self.assertEqual(policy.zones["internal"].cpus, 8)
        self.assertIsNone(policy.zones["confidential"].files)

    def test_example_file_is_valid_and_synthetic(self):
        example = _SANDBOX / "policy.example.json"
        data = json.loads(example.read_text())
        policy = validate_policy(data)
        self.assertEqual(policy.zones["confidential"].model.url,
                         "http://127.0.0.1:8080")

    def test_policy_is_immutable(self):
        policy = validate_policy(valid_policy_dict())
        with self.assertRaises(Exception):
            policy.version = 2
        with self.assertRaises(TypeError):
            policy.zones["internal"] = None

    def test_unknown_and_missing_top_keys(self):
        for mutate in (
            lambda d: d.update(extra=1),
            lambda d: d.pop("zones"),
            lambda d: d.update(version="1"),
            lambda d: d.update(version=2),
        ):
            doc = valid_policy_dict()
            mutate(doc)
            with self.assertRaises(PolicyError):
                validate_policy(doc)

    def test_zone_names_are_fixed(self):
        doc = valid_policy_dict()
        doc["zones"]["dmz"] = doc["zones"]["internal"]
        with self.assertRaises(PolicyError):
            validate_policy(doc)
        doc = valid_policy_dict()
        del doc["zones"]["confidential"]
        with self.assertRaises(PolicyError):
            validate_policy(doc)

    def test_unknown_zone_keys_rejected(self):
        doc = valid_policy_dict()
        doc["zones"]["internal"]["gpus"] = 2
        with self.assertRaises(PolicyError):
            validate_policy(doc)

    def test_bool_and_int_are_distinct_types(self):
        for path, bad in (
            (("zones", "internal", "cpus"), True),
            (("zones", "internal", "cpus"), 8.0),
            (("zones", "internal", "memory_mib"), False),
            (("zones", "internal", "disk_gib"), "200"),
            (("zones", "internal", "files", "writable"), 1),
            (("zones", "internal", "files", "writable"), 0),
            (("zones", "internal", "files", "writable"), "true"),
        ):
            doc = valid_policy_dict()
            target = doc
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = bad
            with self.assertRaises(PolicyError, msg=str(path) + str(bad)):
                validate_policy(doc)

    def test_sizes_are_bounded(self):
        for path, bad in (
            (("zones", "internal", "cpus"), 0),
            (("zones", "internal", "cpus"), 10**6),
            (("zones", "internal", "memory_mib"), 1),
            (("zones", "internal", "memory_mib"), 10**9),
            (("zones", "internal", "disk_gib"), 0),
            (("zones", "internal", "disk_gib"), -5),
        ):
            doc = valid_policy_dict()
            target = doc
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = bad
            with self.assertRaises(PolicyError, msg=str(path) + str(bad)):
                validate_policy(doc)

    def test_files_shape(self):
        for files in (
            {"root": "relative/path", "writable": True},
            {"root": "/srv/sandbox-example/x/../y", "writable": True},
            {"root": "/srv/sandbox-example/x"},
            {"root": "/srv/sandbox-example/x", "writable": True, "mode": 0o700},
            "/srv/sandbox-example/x",
            {},
        ):
            doc = valid_policy_dict()
            doc["zones"]["internal"]["files"] = files
            with self.assertRaises(PolicyError, msg=repr(files)):
                validate_policy(doc)

    def test_model_url_must_be_fixed_origin(self):
        for url in (
            "https://user:pw@llm.example.internal",
            "https://llm.example.internal/v1/chat",
            "https://llm.example.internal?key=1",
            "https://llm.example.internal#frag",
            "ftp://llm.example.internal",
            "http://*",
            "http://",
            "llm.example.internal",
        ):
            doc = valid_policy_dict()
            doc["zones"]["internal"]["model"]["url"] = url
            with self.assertRaises(PolicyError, msg=url):
                validate_policy(doc)

    def test_confidential_needs_literal_loopback_no_dns(self):
        for url in (
            "http://localhost:8080",
            "http://10.0.0.1:8080",
            "http://0x7f000001:8080",
            "http://127.1:8080",
            "http://[::2]:8080",
            "http://llm.example.internal",
        ):
            doc = valid_policy_dict()
            doc["zones"]["confidential"]["model"]["url"] = url
            with self.assertRaises(PolicyError, msg=url):
                validate_policy(doc)
        # no DNS is ever consulted: socket.getaddrinfo must not be called
        with mock.patch("socket.getaddrinfo",
                       side_effect=AssertionError("DNS was used")):
            for url in ("http://127.0.0.1:8080", "https://[::1]:8443"):
                doc = valid_policy_dict()
                doc["zones"]["confidential"]["model"]["url"] = url
                validate_policy(doc)

    def test_internal_allows_fixed_origin(self):
        doc = valid_policy_dict()
        doc["zones"]["internal"]["model"]["url"] = "https://fixed.internal:8443"
        validate_policy(doc)

    def test_model_shape(self):
        for model in (
            {"url": "https://llm.example.internal", "name": "m"},
            {"url": "https://llm.example.internal", "name": "",
             "token_file": None},
            {"url": "https://llm.example.internal", "name": "m",
             "token_file": "relative/token"},
            {"url": "https://llm.example.internal", "name": "m",
             "token_file": None, "extra": 1},
        ):
            doc = valid_policy_dict()
            doc["zones"]["internal"]["model"] = model
            with self.assertRaises(PolicyError, msg=repr(model)):
                validate_policy(doc)
        doc = valid_policy_dict()
        doc["zones"]["internal"]["model"] = None
        validate_policy(doc)


class DuplicateKeyTests(unittest.TestCase):
    def test_duplicate_keys_rejected_at_every_nesting(self):
        cases = [
            '{"version": 1, "version": 1, "zones": {}}',
            json.dumps(valid_policy_dict()).replace(
                '"cpus": 8', '"cpus": 8, "cpus": 4', 1),
            json.dumps(valid_policy_dict()).replace(
                '"writable": true', '"writable": true, "writable": false', 1),
            json.dumps(valid_policy_dict()).replace(
                '"name": "example-local-model"',
                '"name": "a", "name": "b"', 1),
        ]
        for text in cases:
            with self.assertRaises(PolicyError):
                with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as tmp:
                    p = os.path.join(tmp, "policy.json")
                    with open(p, "w") as fh:
                        fh.write(text)
                    load_policy(p, protected=False)


class AssertProtectedTests(unittest.TestCase):
    def test_relative_path_rejected(self):
        with self.assertRaises(PolicyError):
            assert_protected("etc/policy.json")

    def test_unprotected_tmp_path_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as tmp:
            p = os.path.join(tmp, "policy.json")
            Path(p).write_text("{}")
            with self.assertRaises(PolicyError):  # not root-owned
                assert_protected(p)
            os.chmod(tmp, 0o777)
            with self.assertRaises(PolicyError):  # world-writable ancestor
                assert_protected(tmp, directory=True)

    def test_symlink_component_rejected(self):
        path = next((p for p in ("/var", "/var/run", "/etc", "/bin") if os.path.islink(p)), None)
        if path is None:
            self.skipTest("no root-owned system symlink fixture")
        with self.assertRaisesRegex(PolicyError, "symlink"):
            assert_protected(path, directory=True)

    def test_type_mismatch_rejected(self):
        def lstat_all(path):
            if path.endswith("policy.json"):
                return fake_stat(stat.S_IFREG | 0o644, 0)
            return fake_stat(stat.S_IFDIR | 0o755, 0)

        with mock.patch("ai_zones.policy.os.lstat", side_effect=lstat_all), \
                mock.patch("ai_zones.policy.assert_no_mutating_acl") :
            self.assertEqual(
                assert_protected("/srv/zone/policy.json"),
                "/srv/zone/policy.json",
            )
            self.assertEqual(
                assert_protected("/srv/zone", directory=True), "/srv/zone"
            )
            with self.assertRaises(PolicyError):  # file, not directory
                assert_protected("/srv/zone/policy.json", directory=True)
            with self.assertRaises(PolicyError):  # directory, not file
                assert_protected("/srv/zone")

    def test_group_or_other_write_rejected(self):
        def lstat_all(path):
            if path == "/srv":
                return fake_stat(stat.S_IFDIR | 0o775, 0)
            if path.endswith("policy.json"):
                return fake_stat(stat.S_IFREG | 0o604, 0)
            return fake_stat(stat.S_IFDIR | 0o755, 0)

        with mock.patch("ai_zones.policy.os.lstat", side_effect=lstat_all), \
                mock.patch("ai_zones.policy.assert_no_mutating_acl") :
            with self.assertRaises(PolicyError):  # group-writable ancestor
                assert_protected("/srv/zone/policy.json")
        with mock.patch("ai_zones.policy.os.lstat", side_effect=lstat_all), \
                mock.patch("ai_zones.policy.assert_no_mutating_acl") :
            with self.assertRaises(PolicyError):  # other-writable file
                assert_protected("/srv/zone/policy.json")


class LoadPolicyTests(unittest.TestCase):
    def test_requires_absolute_path(self):
        with self.assertRaises(PolicyError):
            load_policy("policy.json", protected=False)

    def test_no_env_or_cwd_search(self):
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as tmp:
            good = os.path.join(tmp, "policy.json")
            Path(good).write_text(json.dumps(valid_policy_dict()))
            env_var = tmp.upper().replace("/", "_")
            with mock.patch.dict(os.environ, {env_var: tmp, "POLICY": good}):
                with mock.patch("os.getcwd", return_value=tmp):
                    with self.assertRaises(PolicyError):
                        load_policy("policy.json")

    def test_default_is_protected_and_tmp_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as tmp:
            p = os.path.join(tmp, "policy.json")
            Path(p).write_text(json.dumps(valid_policy_dict()))
            with self.assertRaises(PolicyError):
                load_policy(p)

    def test_opened_object_must_match_checked_policy(self):
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as tmp:
            p = Path(tmp) / "policy.json"
            p.write_text(json.dumps(valid_policy_dict()))
            checked = os.stat(p)
            with mock.patch("ai_zones.policy._assert_protected_stat",
                            return_value=(str(p), checked)):
                self.assertEqual(load_policy(str(p)).zones["internal"].cpus, 8)
                replacement = Path(tmp) / "replacement"
                replacement.write_text(p.read_text())
                os.replace(replacement, p)
                with self.assertRaisesRegex(PolicyError, "opened object differs"):
                    load_policy(str(p))

    def test_invalid_json_and_encoding(self):
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as tmp:
            p = os.path.join(tmp, "policy.json")
            Path(p).write_bytes(b"\xff\xfe not json")
            with self.assertRaises(PolicyError):
                load_policy(p, protected=False)
            Path(p).write_text('{"version": 1')
            with self.assertRaises(PolicyError):
                load_policy(p, protected=False)
            with self.assertRaises(PolicyError):
                load_policy(os.path.join(tmp, "missing.json"), protected=False)


class ProtectionRegressions(unittest.TestCase):
    def test_noncanonical_path_cannot_change_kernel_resolution(self):
        parent = "/private/tmp" if sys.platform == "darwin" else "/tmp"
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            base = Path(tmp)
            (base / "a" / "b").mkdir(parents=True)
            (base / "etc").mkdir()
            secret = base / "etc" / "hosts"
            secret.write_text(json.dumps(valid_policy_dict()))
            link = Path(parent) / (base.name + "-link")
            os.symlink(base / "a" / "b", link)
            self.addCleanup(link.unlink)
            attack = str(link) + "/../../etc/hosts"
            self.assertEqual(Path(attack).read_text(), secret.read_text())
            with self.assertRaisesRegex(PolicyError, "normalized"):
                load_policy(attack)

    def test_noncanonical_paths_are_refused_before_stat(self):
        for path in ("//etc/hosts", "/etc/./hosts", "/etc/../etc/hosts", "/etc//hosts"):
            with self.subTest(path=path), self.assertRaises(PolicyError):
                assert_protected(path)

    def test_fifo_does_not_block_even_without_authority_check(self):
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as tmp:
            fifo = Path(tmp) / "policy"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(PolicyError, "regular file"):
                load_policy(str(fifo), protected=False)

    def test_running_read_limit_includes_concurrent_growth(self):
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as tmp:
            p = Path(tmp) / "policy"
            p.write_bytes(b"{")
            real_read, grown = os.read, False
            def read(fd, count):
                nonlocal grown
                if not grown:
                    grown = True
                    with p.open("ab") as f:
                        f.write(b"x" * 200)
                return real_read(fd, count)
            with mock.patch.object(policy_module, "POLICY_MAX_BYTES", 128), \
                    mock.patch.object(policy_module.os, "read", side_effect=read):
                with self.assertRaisesRegex(PolicyError, "limit"):
                    load_policy(str(p), protected=False)

    def test_invalid_ports_and_brackets_are_policy_errors(self):
        for url in ("http://127.0.0.1:0", "http://127.0.0.1:99999",
                    "http://[::1]:65536", "http://[::1", "http://127.0.0.1:x"):
            doc = valid_policy_dict()
            doc["zones"]["confidential"]["model"]["url"] = url
            with self.subTest(url=url), self.assertRaises(PolicyError):
                validate_policy(doc)

    @unittest.skipUnless(sys.platform == "darwin", "Darwin extended ACLs")
    def test_actual_acl_mutation_grants_are_refused(self):
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as tmp:
            f = Path(tmp) / "policy"
            f.write_text("synthetic")
            for right in ("write", "append", "delete", "writeattr", "writeextattr",
                          "writesecurity", "chown"):
                subprocess.run(["/bin/chmod", "-N", str(f)], check=True)
                subprocess.run(["/bin/chmod", "+a", "everyone allow " + right, str(f)], check=True)
                with self.subTest(right=right), self.assertRaises(PolicyError):
                    assert_no_mutating_acl(str(f))
            subprocess.run(["/bin/chmod", "-N", str(f)], check=True)
            subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(f)], check=True)
            assert_no_mutating_acl(str(f))
            subprocess.run(["/bin/chmod", "-N", str(f)], check=True)
            subprocess.run(["/bin/chmod", "+a", "everyone deny delete", str(f)], check=True)
            try:
                assert_no_mutating_acl(str(f))
            finally:
                subprocess.run(["/bin/chmod", "-N", str(f)], check=True)

    @unittest.skipUnless(sys.platform == "darwin", "Darwin extended ACLs")
    def test_directory_inherited_mutation_is_also_refused(self):
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as tmp:
            subprocess.run(["/bin/chmod", "+a", "everyone allow add_file,file_inherit,directory_inherit", tmp], check=True)
            with self.assertRaises(PolicyError):
                assert_no_mutating_acl(tmp)

    def test_existing_system_root_chain_can_be_checked(self):
        path = "/private/etc/hosts" if sys.platform == "darwin" else "/etc/hosts"
        assert_protected(path)


if __name__ == "__main__":
    unittest.main()
