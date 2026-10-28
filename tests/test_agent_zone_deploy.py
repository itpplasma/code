"""Synthetic bootstrap verification; never creates host accounts or services."""
import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = _ROOT / "sandbox/ai_zones"
sys.path.insert(0, str(_SOURCE.parent))
from ai_zones import deploy, policy, release


def sha(data):
    return hashlib.sha256(data).hexdigest()


def public(seed):
    blob = b"\0\0\0\x0bssh-ed25519\0\0\0 " + bytes([seed]) * 32
    return b"ssh-ed25519 " + base64.b64encode(blob) + b"\n"


class DeployTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keydir = tempfile.TemporaryDirectory(prefix="ai-test-rsa-")
        cls.private = Path(cls.keydir.name).resolve() / "test-only.pem"
        cls.public = Path(cls.keydir.name).resolve() / "test-only.pub"
        subprocess.run(["/usr/bin/openssl", "genrsa", "-out", str(cls.private), "2048"],
                       check=True, capture_output=True)
        subprocess.run(["/usr/bin/openssl", "rsa", "-in", str(cls.private), "-pubout",
                        "-out", str(cls.public)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.keydir.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ai-deploy-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.core = self.root / "core.json"
        core = {"version": 1, "core_id": "reviewed-test-core", "files": {},
                "runtime": {"runtime_id": deploy.RUNTIME_ID, "python_version": deploy.RUNTIME_VERSION,
                            "manifest_sha256": "a" * 64, "provenance": deploy.RUNTIME_PROVENANCE}}
        for name in deploy.CORE_FILES:
            path = _SOURCE.parent / name
            core["files"][name] = {"path": str(path), "sha256": sha(path.read_bytes())}
        self.core.write_bytes(deploy.encoded(core))
        self.bundle = self.root / "bundle"
        (self.bundle / "payload/ai_zones").mkdir(parents=True)
        self.sentinel = self.root / "MUST_NOT_EXIST"
        malicious = ("open(" + repr(str(self.sentinel)) + ",'w').write('root hook')\n").encode()
        (self.bundle / "payload/ai_zones/deploy.py").write_bytes(malicious)
        manifest = {"version": 1, "release": "test-release", "files": {"ai_zones/deploy.py": sha(malicious)}}
        (self.bundle / "manifest.json").write_bytes(deploy.encoded(manifest))
        subprocess.run(["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(self.private),
                        "-out", str(self.bundle / "manifest.sig"), str(self.bundle / "manifest.json")],
                       check=True, capture_output=True)
        image = self.root / "image.raw"
        image.write_bytes(b"synthetic raw disk")
        descriptor = self.root / "image.json"
        descriptor.write_bytes(deploy.encoded({"version": 1, "distribution": "debian", "release": "13",
            "architecture": "arm64", "format": "raw", "sha256": sha(image.read_bytes()),
            "source_url": "https://cloud.debian.org/images/cloud/trixie/20261001-1234/debian-13-arm64.raw"}))
        vfkit = self.root / "vfkit"
        vfkit.write_bytes(b"synthetic signed hypervisor")
        staged = {"version": 1, "zones": {}}
        for index, zone in enumerate(deploy.ZONES):
            row = {}
            for name, data in (("seed", b"synthetic seed"), ("host_private_key", b"not-a-real-secret"),
                               ("host_public_key", public(index + 1)), ("operator_public_key", public(index + 3))):
                path = self.root / (zone + "-" + name)
                path.write_bytes(data)
                row[name] = {"path": str(path), "sha256": sha(data)}
            row["acceptance"] = None
            staged["zones"][zone] = row
        self.staging = self.root / "staging.json"
        self.staging.write_bytes(deploy.encoded(staged))
        self.inputs = argparse.Namespace(core_manifest=str(self.core), expect_core_sha256=sha(self.core.read_bytes()),
            bundle=str(self.bundle), public_key=str(self.public), image=str(image), descriptor=str(descriptor),
            vfkit=str(vfkit), vfkit_sha256=sha(vfkit.read_bytes()), operator="operator", staging_manifest=str(self.staging))
        self.facts = {"platform": "darwin", "machine": "arm64", "os_build": "25A123",
            "operator": {"name": "operator", "uid": 501, "private_dir": "/Users/operator/.ssh/agent-sandbox"},
            "inventory_sha256": "a" * 64, "accounts": deploy.account_plan({"users": [], "groups": []})}

    def runner(self, argv):
        if argv[:2] == ["/usr/bin/ssh-keygen", "-y"]:
            return public(1 if "internal-" in argv[-1] else 2)
        if argv[:3] == ["/usr/bin/codesign", "-d", "--entitlements"]:
            import plistlib
            return plistlib.dumps({"com.apple.security.virtualization": True})
        if argv[:3] == ["/usr/bin/codesign", "--verify", "--strict"]:
            return b""
        self.fail("unexpected host command " + repr(argv))

    @contextlib.contextmanager
    def target(self):
        # The real protected system parent is checked. Never create the target.
        exists = os.path.lexists
        with mock.patch.object(deploy.os.path, "lexists", side_effect=lambda path:
                               False if path == "/Library/AgentSandbox" else exists(path)):
            yield

    def plan(self):
        with self.target():
            return deploy.build_plan(self.inputs, facts=self.facts, runner=self.runner)

    def test_plan_is_deterministic_bounded_and_offline(self):
        first, _ = self.plan()
        second, _ = self.plan()
        self.assertEqual(first, second)
        self.assertLess(len(deploy.encoded(first)), 32768)
        self.assertFalse(self.sentinel.exists())
        self.assertIn("trusted_runtime_missing", first["activation_gates"])
        self.assertEqual(first["status"], "offline_foundation_only")
        self.assertTrue(all(z["files"] is None and z["model"] is None for z in first["policy"]["zones"].values()))
        for zone in deploy.ZONES:
            a = first["host"]["accounts"][zone]
            self.assertEqual(set(a), {"vm", "broker", "ipc"})
            self.assertNotEqual(a["ipc"]["gid"], a["broker"]["gid"])
            self.assertEqual(a["ipc"]["members"], [a["vm"]["name"], a["broker"]["name"]])

    def test_valid_signed_malicious_release_only_copies_retained_bytes(self):
        plan, retained = self.plan()
        installation = self.root / "installation"
        installation.mkdir()
        (installation / "releases").mkdir()
        immutable = installation / "core"
        immutable.write_bytes(b"independent CORE")
        pfd = release._open_absolute_dir(str(installation))
        rfd = release._open_absolute_dir(str(installation / "releases"))
        try:
            release._install_verified(pfd, rfd, plan["release"]["id"], retained["payload"])
        finally:
            os.close(pfd)
            os.close(rfd)
        self.assertEqual(immutable.read_bytes(), b"independent CORE")
        self.assertFalse(self.sentinel.exists())
        self.assertEqual(os.readlink(installation / "current"), "releases/test-release")

    def test_core_manifest_extra_file_wrong_hash_and_changed_source_refused(self):
        original = self.core.read_bytes()
        doc = json.loads(original)
        doc["files"]["hooks/root.py"] = doc["files"]["ai_zones/deploy.py"]
        self.core.write_bytes(deploy.encoded(doc))
        self.inputs.expect_core_sha256 = sha(self.core.read_bytes())
        with self.assertRaisesRegex(deploy.DeployError, "CORE import closure"):
            self.plan()
        self.core.write_bytes(original)
        self.inputs.expect_core_sha256 = "0" * 64
        with self.assertRaisesRegex(deploy.DeployError, "independently reviewed"):
            self.plan()

    def test_artifact_change_symlink_and_control_path_refused(self):
        Path(self.inputs.image).write_bytes(b"changed")
        with self.assertRaisesRegex(deploy.DeployError, "descriptor"):
            self.plan()
        link = self.root / "link"
        link.symlink_to(self.inputs.image)
        with self.assertRaises((deploy.DeployError, OSError, release.ReleaseError)):
            deploy._file_digest(str(link))
        for path in ("/tmp/../etc/x", "/tmp//x", "/tmp/x\n", "relative"):
            with self.assertRaises(deploy.DeployError):
                deploy._path(path)

    def test_staging_strict_schema_and_host_key_pair(self):
        staged = json.loads(self.staging.read_bytes())
        staged["hooks"] = []
        self.staging.write_bytes(deploy.encoded(staged))
        with self.assertRaisesRegex(deploy.DeployError, "staging manifest"):
            self.plan()
        staged.pop("hooks")
        self.staging.write_bytes(deploy.encoded(staged))
        with self.target(), self.assertRaisesRegex(deploy.DeployError, "key pair mismatch"):
            deploy.build_plan(self.inputs, facts=self.facts, runner=lambda argv: public(9))

    def test_hypervisor_signature_and_virtualization_entitlement_fail_closed(self):
        import plistlib
        def bad_signature(argv):
            raise deploy.DeployError("synthetic invalid code signature")
        with self.assertRaisesRegex(deploy.DeployError, "invalid code signature"):
            deploy._vfkit(self.inputs.vfkit, self.inputs.vfkit_sha256, "darwin", bad_signature)
        def no_entitlement(argv):
            return plistlib.dumps({}) if "--entitlements" in argv else b""
        with self.assertRaisesRegex(deploy.DeployError, "virtualization entitlement"):
            deploy._vfkit(self.inputs.vfkit, self.inputs.vfkit_sha256, "darwin", no_entitlement)
        with self.assertRaisesRegex(deploy.DeployError, "vfkit digest mismatch"):
            deploy._vfkit(self.inputs.vfkit, "0" * 64, "darwin", self.runner)

    def test_duplicate_json_and_options_and_unknown_flags(self):
        with self.assertRaises(deploy.DeployError):
            deploy._json(b'{"version":1,"version":1}')
        with contextlib.redirect_stderr(io.StringIO()):
            for args in (["--plan", "--plan"], ["--plan", "--unknown"], ["--pl"]):
                with self.assertRaises(SystemExit):
                    deploy.parser().parse_args(args)

    def test_cross_zone_metadata_is_redacted_with_real_policy_model(self):
        full = deploy.null_policy()
        full["zones"]["confidential"]["files"] = {"root": "/private/classified-data", "writable": False}
        full["zones"]["confidential"]["model"] = {"url": "http://127.0.0.1:8000", "name": "classified-model", "token_file": "/private/token"}
        typed = policy.validate_policy(full)
        self.assertIsInstance(typed, policy.Policy)
        view = deploy.service_policy(full, "internal")
        self.assertNotIn(b"classified", deploy.encoded(view))
        self.assertNotIn(b"/private/token", deploy.encoded(view))

    def test_exact_root_api_and_sudoers(self):
        text = deploy.sudoers_text("/Library/AgentSandbox", "operator")
        self.assertNotIn("*", text)
        self.assertIn("NOSETENV", text)
        for zone in deploy.ZONES:
            for action in ("start", "status", "proxy"):
                self.assertEqual(deploy.admin_args([action, zone]), (action, zone))
        for bad in (["start", "internal", "--config=/tmp/x"], ["update", "internal"],
                    ["proxy", "/tmp/socket"], ["start", "--internal"], ["prepare-host"]):
            with self.assertRaises(deploy.DeployError):
                deploy.admin_args(bad)
        with self.assertRaises(deploy.DeployError):
            deploy.sudoers_text("/tmp/prefix", "operator")

    def test_account_ids_commands_and_private_socket_layout(self):
        accounts = deploy.account_plan({"users": [("someone", 600)], "groups": [("something", 601)]})
        commands = deploy.account_commands("/Library/AgentSandbox", accounts)
        self.assertTrue(all(argv[0] == "/usr/bin/dscl" for argv in commands))
        self.assertTrue(any(argv[-2:] == ["UserShell", "/usr/bin/false"] for argv in commands))
        for zone in deploy.ZONES:
            self.assertGreater(accounts[zone]["vm"]["uid"], 601)
            paths = deploy.socket_paths("/Library/AgentSandbox", zone)
            self.assertNotEqual(os.path.dirname(paths["broker"]), os.path.dirname(paths["ssh"]))
        with self.assertRaisesRegex(deploy.DeployError, "not adopted"):
            deploy.account_plan({"users": [("_aivmi", 600)], "groups": []})

    def test_native_existing_account_and_alias_lookup_refuses_overwrite(self):
        with self.assertRaisesRegex(deploy.DeployError, "not overwritten"):
            deploy._require_unused_record("/Users/" + deploy.pwd.getpwuid(0).pw_name)
        with self.assertRaisesRegex(deploy.DeployError, "not overwritten"):
            deploy._require_unused_record("/Groups/" + deploy.grp.getgrgid(0).gr_name)

    def test_modern_runtime_contract_rejects_old_version_and_binds_manifest(self):
        import copy
        document = json.loads(self.core.read_bytes())
        document["runtime"]["python_version"] = "3.9.6"
        self.core.write_bytes(deploy.encoded(document))
        self.inputs.expect_core_sha256 = sha(self.core.read_bytes())
        with self.assertRaisesRegex(deploy.DeployError, "reviewed modern runtime"):
            self.plan()
        source = self.root / "runtime-tree"
        (source / "bin").mkdir(parents=True)
        (source / "bin/python3.14").write_bytes(b"synthetic interpreter; never execute")
        manifest = deploy.runtime.build_manifest(str(source), deploy.RUNTIME_ID,
            python_version=deploy.RUNTIME_VERSION, provenance=deploy.RUNTIME_PROVENANCE)
        contract = {"runtime_id": deploy.RUNTIME_ID, "python_version": deploy.RUNTIME_VERSION,
                    "manifest_sha256": sha(deploy.runtime.canonical_manifest(manifest)),
                    "provenance": deploy.RUNTIME_PROVENANCE}
        prefix = "/Library/AgentSandbox"
        selected = {"manifest": manifest, "runtime_root": prefix + "/runtimes/" + deploy.RUNTIME_ID,
                    "python": deploy.runtime_python(prefix)}
        with mock.patch.object(deploy.runtime, "verify_installed_runtime", return_value=selected, create=True) as verifier:
            self.assertEqual(deploy._verify_selected_runtime(prefix, contract), selected)
            verifier.assert_called_once_with(prefix)
            changed = copy.deepcopy(selected)
            changed["manifest"]["entries"]["bin/python3.14"]["sha256"] = "0" * 64
            verifier.return_value = changed
            with self.assertRaisesRegex(deploy.DeployError, "independently approved CORE contract"):
                deploy._verify_selected_runtime(prefix, contract)
            changed = copy.deepcopy(selected)
            changed["python"] = prefix + "/runtime-current/bin/python3.14"
            verifier.return_value = changed
            with self.assertRaisesRegex(deploy.DeployError, "canonical paths"):
                deploy._verify_selected_runtime(prefix, contract)

    def test_runtime_authority_is_separate_and_root_installation_remains_pending(self):
        contract = json.loads(self.core.read_bytes())["runtime"]
        candidate, key = deploy._runtime_plan(self.inputs, "/Library/AgentSandbox", contract, self.public.read_bytes())
        self.assertIsNone(key)
        self.assertEqual(candidate["status"], "missing_signed_runtime_bundle")
        disguised = self.root / "same-rsa-key-different-pem.pub"
        disguised.write_bytes(b"\n" + self.public.read_bytes() + b"\n")
        self.inputs.runtime_public_key = str(disguised)
        with self.assertRaisesRegex(deploy.DeployError, "authorities must differ"):
            deploy._runtime_plan(self.inputs, "/Library/AgentSandbox", contract, self.public.read_bytes())

    def runtime_fixture(self):
        bundle = self.root / "runtime-bundle"
        (bundle / "payload/bin").mkdir(parents=True)
        python = bundle / "payload/bin/python3.14"
        python.write_bytes(b"synthetic runtime payload, never execute")
        manifest = deploy.runtime.build_manifest(str(bundle / "payload"), deploy.RUNTIME_ID,
            python_version=deploy.RUNTIME_VERSION, provenance=deploy.RUNTIME_PROVENANCE)
        raw = deploy.runtime.canonical_manifest(manifest)
        (bundle / "manifest.json").write_bytes(raw)
        private = self.root / "runtime-test-only.pem"
        public_key = self.root / "runtime-test-only.pub"
        subprocess.run(["/usr/bin/openssl", "genrsa", "-out", str(private), "2048"],
                       check=True, capture_output=True)
        subprocess.run(["/usr/bin/openssl", "rsa", "-in", str(private), "-pubout", "-out", str(public_key)],
                       check=True, capture_output=True)
        subprocess.run(["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(private),
                        "-out", str(bundle / "manifest.sig"), str(bundle / "manifest.json")],
                       check=True, capture_output=True)
        contract = {"runtime_id": deploy.RUNTIME_ID, "python_version": deploy.RUNTIME_VERSION,
                    "manifest_sha256": sha(raw), "provenance": deploy.RUNTIME_PROVENANCE}
        return bundle, public_key, manifest, contract

    def test_real_signed_modern_runtime_candidate_binding(self):
        bundle, public_key, manifest, contract = self.runtime_fixture()
        python = bundle / "payload/bin/python3.14"
        self.inputs.runtime_bundle = str(bundle)
        self.inputs.runtime_public_key = str(public_key)
        candidate, retained_key = deploy._runtime_plan(self.inputs, "/Library/AgentSandbox", contract, self.public.read_bytes())
        self.assertEqual(candidate["status"], "candidate_verified_root_installation_pending")
        self.assertEqual(retained_key, public_key.read_bytes())
        python.write_bytes(b"tampered runtime")
        with self.assertRaises(deploy.runtime.RuntimeError):
            deploy._runtime_plan(self.inputs, "/Library/AgentSandbox", contract, self.public.read_bytes())

    @contextlib.contextmanager
    def initial_runtime_context(self, prefix, plan, manifest, contract):
        selected = {"manifest": manifest, "runtime_root": str(prefix) + "/runtimes/" + deploy.RUNTIME_ID,
                    "python": deploy.runtime_python(str(prefix))}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(deploy.os, "geteuid", return_value=0))
            stack.enter_context(mock.patch.object(deploy, "prefix_for", return_value=str(prefix)))
            stack.enter_context(mock.patch.object(deploy, "_load_plan", return_value=plan))
            stack.enter_context(mock.patch.object(deploy, "_assert_core_sources", return_value=contract))
            stack.enter_context(mock.patch.object(deploy.policy, "assert_protected"))
            install = stack.enter_context(mock.patch.object(deploy.runtime, "install_runtime",
                return_value=selected["runtime_root"]))
            verify = stack.enter_context(mock.patch.object(deploy, "_verify_selected_runtime", return_value=selected))
            yield install, verify

    def test_initial_runtime_bootstrap_contract_key_and_tamper_before_install(self):
        import copy
        bundle, public_key, manifest, contract = self.runtime_fixture()
        prefix = self.root / "foundation"
        (prefix / "trust").mkdir(parents=True)
        (prefix / "trust/runtime.pub").write_bytes(public_key.read_bytes())
        (prefix / "trust/release.pub").write_bytes(self.public.read_bytes())
        plan = {"runtime": {"contract": contract, "public_key_sha256": sha(public_key.read_bytes())},
                "activation_gates": list(deploy.GATES)}
        with self.initial_runtime_context(prefix, plan, manifest, contract) as (install, verify):
            result = deploy.install_initial_runtime(str(bundle))
            self.assertEqual(result["status"], "runtime_installed_activation_pending")
            install.assert_called_once_with(str(bundle), str(prefix), str(prefix / "trust/runtime.pub"),
                                           expected_manifest_sha256=contract["manifest_sha256"])
            verify.assert_called_once_with(str(prefix), contract)
        wrong = copy.deepcopy(contract)
        wrong["manifest_sha256"] = "0" * 64
        with self.initial_runtime_context(prefix, plan, manifest, wrong) as (install, verify):
            with self.assertRaisesRegex(deploy.DeployError, "approved CORE contract"):
                deploy.install_initial_runtime(str(bundle))
            install.assert_not_called()
            verify.assert_not_called()
        (prefix / "trust/runtime.pub").write_bytes(self.public.read_bytes())
        with self.initial_runtime_context(prefix, plan, manifest, contract) as (install, _):
            with self.assertRaisesRegex(deploy.DeployError, "trust key differs"):
                deploy.install_initial_runtime(str(bundle))
            install.assert_not_called()
        same_key_plan = copy.deepcopy(plan)
        same_key_plan["runtime"]["public_key_sha256"] = sha(self.public.read_bytes())
        with self.initial_runtime_context(prefix, same_key_plan, manifest, contract) as (install, _):
            with self.assertRaisesRegex(deploy.DeployError, "authorities must differ"):
                deploy.install_initial_runtime(str(bundle))
            install.assert_not_called()
        (prefix / "trust/runtime.pub").write_bytes(public_key.read_bytes())
        (bundle / "payload/bin/python3.14").write_bytes(b"tampered, never execute")
        with self.initial_runtime_context(prefix, plan, manifest, contract) as (install, _):
            with self.assertRaises(deploy.runtime.RuntimeError):
                deploy.install_initial_runtime(str(bundle))
            install.assert_not_called()
        with mock.patch.object(deploy.os, "geteuid", return_value=501), \
             mock.patch.object(deploy.runtime, "install_runtime") as install:
            with self.assertRaisesRegex(deploy.DeployError, "requires root"):
                deploy.install_initial_runtime(str(bundle))
            install.assert_not_called()

    def test_initial_runtime_argv_and_one_time_guard(self):
        bundle, public_key, manifest, contract = self.runtime_fixture()
        self.inputs.runtime_bundle, self.inputs.runtime_public_key = str(bundle), str(public_key)
        core_doc = json.loads(self.core.read_bytes())
        core_doc["runtime"] = contract
        self.core.write_bytes(deploy.encoded(core_doc))
        self.inputs.expect_core_sha256 = sha(self.core.read_bytes())
        plan, _ = self.plan()
        self.assertEqual(plan["runtime"]["bootstrap_argv"], ["/usr/bin/python3", "-I", "-S", "-B",
            "/Library/AgentSandbox/core/reviewed-test-core/bootstrap.py", "install-runtime", str(bundle)])
        self.assertIn("human-reviewed", plan["runtime"]["bootstrap_approval"])
        with self.assertRaises(deploy.DeployError):
            deploy.initial_runtime_argv("/tmp/attacker", "core", str(bundle))
        prefix = self.root / "existing-foundation"
        prefix.mkdir()
        (prefix / "runtime-current").symlink_to("runtimes/existing")
        with self.initial_runtime_context(prefix, plan, manifest, contract) as (install, _):
            with self.assertRaisesRegex(deploy.DeployError, "existing runtime"):
                deploy.install_initial_runtime(str(bundle))
            install.assert_not_called()
        with self.assertRaises(deploy.DeployError):
            deploy.admin_args(["install-runtime", str(bundle)])

    def test_installed_dispatch_initial_runtime_exact_action(self):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ), mock.patch.object(deploy.os, "chdir"), \
             mock.patch.object(deploy, "install_initial_runtime", return_value={"status": "checked"}) as install, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(deploy.installed_main(["install-runtime", "/reviewed/bundle"]), 0)
            install.assert_called_once_with("/reviewed/bundle")
            self.assertEqual(json.loads(output.getvalue()), {"status": "checked"})
            install.reset_mock()
            for args in (["install-runtime"], ["install-runtime", "/reviewed/bundle", "--extra"]):
                self.assertEqual(deploy.installed_main(args), 1)
            install.assert_not_called()
        self.assertIn("unknown installed CORE action", errors.getvalue())

    def test_frozen_source_guard_and_normal_old_interpreter_refusal(self):
        prefix = self.root / "frozen"
        core = prefix / "core/test-core"
        (core / "ai_zones").mkdir(parents=True)
        (prefix / "management").mkdir()
        (core / "ai_zones/__init__.py").write_bytes(b"")
        hashes = {}
        for name in deploy.CORE_FILES:
            data = (_SOURCE.parent / name).read_bytes()
            (core / name).write_bytes(data)
            hashes[name] = sha(data)
        raw = self.core.read_bytes()
        (prefix / "management/core-manifest.json").write_bytes(raw)
        (core / "bootstrap.py").write_bytes(deploy._bootstrap_text(str(prefix), "test-core"))
        plan = {"core_id": "test-core", "core_files": hashes, "core_manifest_sha256": sha(raw),
                "runtime": {"contract": json.loads(raw)["runtime"]}}
        plan_path = self.root / "source-guard-plan.json"
        plan_path.write_bytes(deploy.encoded(plan))
        probe = """import sys,json
from unittest import mock
sys.path.insert(0,sys.argv[1])
from ai_zones import deploy
with open(sys.argv[3]) as handle: plan=json.load(handle)
with mock.patch.object(deploy.policy,'assert_protected'):
    deploy._assert_core_sources(sys.argv[2],plan)
    if sys.argv[4]=='normal':
        with mock.patch.object(deploy,'_verify_selected_runtime',return_value={}):
            deploy._assert_core(sys.argv[2],plan)
print('FROZEN SOURCE APPROVAL CHECKED')
"""
        argv = [sys.executable, "-I", "-S", "-B", "-c", probe, str(core), str(prefix), str(plan_path)]
        result = subprocess.run(argv + ["source"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FROZEN SOURCE APPROVAL CHECKED", result.stdout)
        result = subprocess.run(argv + ["normal"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires protected dedicated runtime", result.stderr)
        (core / "ai_zones/release.py").write_bytes((core / "ai_zones/release.py").read_bytes() + b"\n# tamper\n")
        result = subprocess.run(argv + ["source"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CORE source digest mismatch", result.stderr)

    def test_encryption_and_remote_recovery_are_activation_gates(self):
        plan, _ = self.plan()
        self.assertIn("filevault_encryption_acceptance_pending", plan["activation_gates"])
        self.assertIn("remote_recovery_acceptance_pending", plan["activation_gates"])
        self.assertFalse(any("inference" in row["path"] for row in plan["directories"]))
        linux, _ = deploy._runtime_plan(self.inputs, "/opt/agent-sandbox", plan["runtime"]["contract"], self.public.read_bytes())
        self.assertIsNone(linux["python"])
        self.assertEqual(linux["status"], "linux_system_runtime_and_confinement_pending")

    def test_generated_dispatch_never_uses_current_or_release_imports(self):
        code = deploy._bootstrap_text("/Library/AgentSandbox", "reviewed-core").decode()
        self.assertIn("/Library/AgentSandbox/core/reviewed-core", code)
        self.assertNotIn("current", code)
        self.assertNotIn("releases", code)
        launcher = deploy._admin_launcher("/Library/AgentSandbox", "reviewed-core").decode()
        self.assertIn(" -I -S -B ", launcher)
        self.assertNotIn("/usr/bin/python3", launcher)
        self.assertIn("/runtimes/" + deploy.RUNTIME_ID + "/bin/python3.14", launcher)

    def test_apply_fails_before_writes_and_stage_stale_plan(self):
        plan, retained = self.plan()
        with mock.patch.object(deploy.os, "geteuid", return_value=0), \
             mock.patch.object(deploy, "build_plan", return_value=(plan, retained)), \
             mock.patch.object(deploy, "_mkdir") as mkdir:
            with self.assertRaisesRegex(deploy.DeployError, "stale plan"):
                deploy.stage_foundation(self.inputs, "0" * 64)
            mkdir.assert_not_called()
        with mock.patch.object(deploy.os, "geteuid", return_value=501):
            with self.assertRaisesRegex(deploy.DeployError, "root"):
                deploy.stage_foundation(self.inputs, plan["plan_sha256"])

    def test_native_ssh_host_key_staging_and_sudoers_consumer(self):
        native = json.loads(self.staging.read_bytes())
        for zone in deploy.ZONES:
            private = self.root / (zone + "-native-key")
            subprocess.run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private)],
                           check=True, capture_output=True)
            for name, path in (("host_private_key", private), ("host_public_key", Path(str(private) + ".pub"))):
                native["zones"][zone][name] = {"path": str(path), "sha256": sha(path.read_bytes())}
        self.staging.write_bytes(deploy.encoded(native))
        staged, retained, _ = deploy.staging_inputs(str(self.staging))
        self.assertEqual(set(staged), set(deploy.ZONES))
        sudoers = self.root / "sudoers.candidate"
        sudoers.write_text(deploy.sudoers_text("/Library/AgentSandbox", "operator"))
        if Path("/usr/sbin/visudo").exists():
            result = subprocess.run(["/usr/sbin/visudo", "-cf", str(sudoers)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_dispatch_consumer_ignores_malicious_release_and_environment(self):
        prefix = self.root / "protected-prefix"
        core = prefix / "core/fixed-core"
        (core / "ai_zones").mkdir(parents=True)
        (core / "ai_zones/deploy.py").write_text(
            "def installed_main(argv):\n    print('FIXED CORE ' + ','.join(argv)); return 0\n")
        ordinary = prefix / "releases/attacker"
        (ordinary / "ai_zones").mkdir(parents=True)
        (ordinary / "ai_zones/deploy.py").write_text(
            "raise RuntimeError('ORDINARY RELEASE MUST NOT BE IMPORTED')\n")
        (prefix / "current").symlink_to("releases/attacker")
        dispatcher = core / "bootstrap.py"
        dispatcher.write_bytes(deploy._bootstrap_text(str(prefix), "fixed-core"))
        env = dict(os.environ, PYTHONPATH=str(ordinary), AI_ZONE_PREFIX=str(ordinary))
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(dispatcher), "admin", "status", "internal"],
                                env=env, cwd=str(ordinary), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "FIXED CORE admin,status,internal")

    def test_real_policy_rejects_unprivileged_fixture_chain(self):
        target = self.root / "protected.json"
        target.write_bytes(b"{}")
        with self.assertRaises(policy.PolicyError):
            policy.assert_protected(str(target))

    def test_acceptance_requires_exact_bindings_and_true_result(self):
        receipt = {"version": 1, "platform": "darwin", "os_build": "25A123", "zone": "internal", "passed": True}
        for name in ("release_sha256", "vfkit_sha256", "image_sha256", "seed_sha256", "profile_sha256", "suite_sha256"):
            receipt[name] = "a" * 64
        self.assertEqual(deploy.acceptance_receipt(deploy.encoded(receipt), {"zone": "internal"}), receipt)
        with self.assertRaises(deploy.DeployError):
            deploy.acceptance_receipt(deploy.encoded(receipt), {"os_build": "changed"})
        receipt["passed"] = 1
        with self.assertRaises(deploy.DeployError):
            deploy.acceptance_receipt(deploy.encoded(receipt), {})

    def test_cli_plan_real_entry_no_writes(self):
        argv = ["--plan"]
        for name, value in vars(self.inputs).items():
            argv += ["--" + name.replace("_", "-"), value]
        with self.target(), mock.patch.object(deploy, "host_facts", return_value=self.facts), \
             mock.patch.object(deploy, "_run", side_effect=self.runner):
            # Default callable arguments are captured at definition; use build_plan wrapper.
            actual_build = deploy.build_plan
            with mock.patch.object(deploy, "build_plan", side_effect=lambda args: actual_build(args, facts=self.facts, runner=self.runner)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    result = deploy.main(argv)
                self.assertEqual(result, 0)
                self.assertEqual(json.loads(out.getvalue())["status"], "offline_foundation_only")
        self.assertFalse(self.sentinel.exists())


if __name__ == "__main__":
    unittest.main()
