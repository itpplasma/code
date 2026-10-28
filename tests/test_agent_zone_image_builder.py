"""Offline tests of the emitted guest consumer using synthetic public fixtures."""

import base64
import copy
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sandbox"))
from ai_zones import image_builder as builder
from ai_zones import vm, guest

LOCK = REPO_ROOT / "sandbox/guest-tools.lock.json"


def guest_consumer():
    namespace = {"__name__": "test_emitted_guest"}
    exec(compile(builder.GUEST_PROGRAM, "<emitted-guest>", "exec"), namespace)
    return namespace


class TestLock(unittest.TestCase):
    def setUp(self):
        self.lock = builder.load_lock(LOCK)

    def test_primary_proposal_accepts_both_guest_architectures(self):
        self.assertEqual(builder.validate_lock(self.lock), self.lock)
        for arch in builder.ARCHES:
            self.assertEqual(set(self.lock["architectures"][arch]["tools"]), set(builder.VERSIONS))
        result = subprocess.run([sys.executable, "-m", "ai_zones.image_builder",
                                 "validate-lock", "--lock", str(LOCK)], cwd=REPO_ROOT / "sandbox",
                                text=True, capture_output=True, check=True)
        self.assertFalse(json.loads(result.stdout)["approval"])

    def test_unknown_fields_versions_architectures_and_origins_refused(self):
        variants = []
        for field, value in (("version", True), ("status", "approved"), ("snapshot", "latest")):
            lock = copy.deepcopy(self.lock)
            lock[field] = value
            variants.append(lock)
        for field, value in (("url", "https://example.invalid/install.sh"),
                             ("member", "../../root/.ssh/authorized_keys"),
                             ("version", "1.3.0"), ("integrity", "sha256:" + "a" * 63),
                             ("integrity", "sha512-" + base64.b64encode(b"short").decode()),
                             ("postinstall", "id")):
            lock = copy.deepcopy(self.lock)
            lock["architectures"]["arm64"]["tools"]["pi"][field] = value
            variants.append(lock)
        lock = copy.deepcopy(self.lock)
        lock["architectures"]["arm64"]["tools"] = copy.deepcopy(lock["architectures"]["amd64"]["tools"])
        variants.append(lock)
        lock = copy.deepcopy(self.lock)
        lock["architectures"]["arm64"]["packages"]["install"]["git"] = "1;id"
        variants.append(lock)
        lock = copy.deepcopy(self.lock)
        del lock["architectures"]["amd64"]
        variants.append(lock)
        for lock in variants:
            with self.subTest(lock=lock), self.assertRaises(builder.ImageBuildError):
                builder.validate_lock(lock)

    def test_duplicate_json_fields_are_not_last_writer_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lock"
            path.write_text('{"version":1,' + LOCK.read_text().lstrip()[1:])
            with self.assertRaisesRegex(builder.ImageBuildError, "duplicate"):
                builder.load_lock(path)

    def test_base_bytes_checked_before_disposable_boot(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "base.raw"
            path.write_bytes(b"synthetic immutable Debian image fixture")
            self.lock["architectures"]["arm64"]["base"]["integrity"] = "sha512:" + hashlib.sha512(path.read_bytes()).hexdigest()
            self.assertTrue(builder.verify_base(self.lock, "arm64", path)["verified"])
            path.write_bytes(b"tampered")
            with self.assertRaises(builder.ImageBuildError):
                builder.verify_base(self.lock, "arm64", path)
            link = Path(temporary) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                builder.verify_base(self.lock, "arm64", link)


class TestGuestConsumer(unittest.TestCase):
    def setUp(self):
        self.guest = guest_consumer()
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def archive(self, entries):
        path = self.root / "artifact.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, kind in entries:
                info = tarfile.TarInfo(name)
                if kind == "link":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "/etc/passwd"
                    archive.addfile(info)
                else:
                    content = b"synthetic executable fixture\n"
                    info.size = len(content)
                    info.mode = 0o755
                    archive.addfile(info, io.BytesIO(content))
        return path

    def test_integrity_precedes_extracting_data_without_hooks(self):
        archive = self.archive([("pi/pi", "file"), ("pi/postinstall.sh", "file")])
        for integrity in ("sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
                          "sha512-" + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()):
            self.guest["verify_artifact"](archive, integrity)
        destination = self.root / "installed"
        self.guest["unpack"](archive, destination)
        self.assertEqual((destination / "pi/pi").read_bytes(), b"synthetic executable fixture\n")
        self.assertTrue((destination / "pi/pi").stat().st_mode & 0o111)
        archive.write_bytes(b"modified payload")
        with self.assertRaises(ValueError):
            self.guest["verify_artifact"](archive, integrity)

    def test_archive_paths_links_and_duplicates_fail_before_writing_files(self):
        for entries in ([('../escaped', 'file')], [('/absolute', 'file')],
                        [('pi/link', 'link')], [('pi/pi', 'file'), ('pi/pi', 'file')]):
            with self.subTest(entries=entries):
                archive = self.archive(entries)
                destination = self.root / "rejected"
                with self.assertRaises(ValueError):
                    self.guest["unpack"](archive, destination)
                self.assertEqual(list(destination.iterdir()), [])
                destination.rmdir()

    def test_cleanup_removes_build_identity_and_detects_reintroduction(self):
        for relative in ("etc/ssh", "etc/netplan", "etc/systemd/network", "etc/cloud/cloud.cfg.d"):
            (self.root / relative).mkdir(parents=True)
        (self.root / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
        for relative in ("home/builder/.ssh/authorized_keys", "root/.ssh/authorized_keys",
                         "var/lib/cloud/seed/nocloud/user-data", "var/lib/cloud/instance/identity",
                         "var/lib/agent-image-build/lock.json", "var/cache/apt/archives/blob",
                         "var/lib/apt/lists/index", "root/.bash_history", "root/.cache/log",
                         "root/.zsh_history", "root/.local/share/fish/fish_history", "var/log/build.log",
                         "var/lib/dhcp/lease", "var/lib/NetworkManager/lease", "etc/passwd-", "etc/shadow-",
                         "etc/ssh/ssh_host_ed25519_key", "etc/netplan/50-cloud-init.yaml",
                         "etc/machine-id", "var/lib/dbus/machine-id", "var/lib/systemd/random-seed"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("synthetic build identity")
        self.guest["scrub"](self.root)
        self.assertEqual(self.guest["frozen_state"](self.root), "scrubbed")
        self.assertFalse((self.root / "home/builder").exists())
        (self.root / "etc/passwd").write_text("builder:x:1000:1000::/home/builder:/bin/bash\n")
        with self.assertRaises(ValueError):
            self.guest["frozen_state"](self.root)

    def test_scrub_preserves_mountpoint_directory_roots_and_canonical_modes(self):
        (self.root / "etc/ssh").mkdir(parents=True)
        (self.root / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
        identities = {}
        for relative in self.guest["EMPTY_PATHS"]:
            directory = self.root / relative
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o777)
            (directory / "synthetic-build-identity").write_text("discard")
            identities[relative] = (directory.stat().st_dev, directory.stat().st_ino)
        rmdir = os.rmdir

        def mountpoint_semantics(path, *args, **kwargs):
            info = os.stat(path, dir_fd=kwargs.get("dir_fd"), follow_symlinks=False)
            if (info.st_dev, info.st_ino) == identities["tmp"]:
                raise OSError(errno.EBUSY, "Device or resource busy", str(path))
            return rmdir(path, *args, **kwargs)

        with mock.patch.object(self.guest["os"], "rmdir", side_effect=mountpoint_semantics):
            self.guest["scrub"](self.root)
        for relative, identity in identities.items():
            directory = self.root / relative
            self.assertEqual((directory.stat().st_dev, directory.stat().st_ino), identity)
            self.assertEqual(directory.stat().st_mode & 0o7777, self.guest["EMPTY_MODES"][relative])
            self.assertEqual(list(directory.iterdir()), [])
        self.assertEqual(self.guest["frozen_state"](self.root), "scrubbed")
        (self.root / "home").chmod(0o777)
        with self.assertRaises(ValueError):
            self.guest["frozen_state"](self.root)

    def test_scrub_rejects_links_non_directories_and_nested_mounts(self):
        outside = self.root / "outside"
        outside.mkdir()
        secret = outside / "preserve"
        secret.write_text("unrelated fixture")
        target = self.root / "tmp"
        target.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.guest["scrub"](self.root)
        self.assertEqual(secret.read_text(), "unrelated fixture")
        target.unlink()
        target.write_text("not a directory")
        with self.assertRaises(ValueError):
            self.guest["scrub"](self.root)
        target.unlink()
        entries = [(str(target / "nested"), "/", "tmpfs")]
        with mock.patch.dict(self.guest, {"mounts": lambda: entries}):
            with self.assertRaisesRegex(ValueError, "mount hides"):
                self.guest["scrub"](self.root)

    def test_all_scrub_ancestors_refuse_external_links_before_any_deletion(self):
        for relative in ("var/lib", "etc/ssh", "etc/netplan", "etc/systemd/network"):
            with self.subTest(ancestor=relative), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                root, outside = base / "image", base / "outside"
                root.mkdir()
                (outside / "cloud").mkdir(parents=True)
                external = [outside / "cloud/preserve", outside / "ssh_host_fixture",
                            outside / "fixture.yaml", outside / "fixture.network"]
                for path in external:
                    path.write_text("outside data must survive")
                (root / "etc").mkdir()
                (root / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(outside)
                untouched = root / "var/cache/apt/archives/first-write-sentinel"
                untouched.parent.mkdir(parents=True, exist_ok=True)
                untouched.write_text("preflight must refuse before deletion")
                for operation in ("scrub", "frozen_state"):
                    with self.assertRaises(ValueError):
                        self.guest[operation](root)
                    self.assertEqual(untouched.read_text(), "preflight must refuse before deletion")
                    self.assertTrue(all(path.read_text() == "outside data must survive" for path in external))

    def test_scrub_and_inspection_refuse_symlink_root_and_non_directory_ancestors(self):
        root = self.root / "image"
        root.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(root)
        for operation in ("scrub", "frozen_state"):
            with self.assertRaises(ValueError):
                self.guest[operation](alias)
        (root / "var").mkdir()
        (root / "var/lib").write_text("not a directory")
        for operation in ("scrub", "frozen_state"):
            with self.assertRaises(ValueError):
                self.guest[operation](root)

    def test_intervening_mounts_refuse_before_scrub_or_frozen_inspection(self):
        for relative in ("var/lib", "etc", "etc/ssh", "etc/netplan", "etc/systemd/network",
                         "etc/machine-id", "etc/passwd"):
            with self.subTest(mounted=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "etc/ssh").mkdir(parents=True)
                (root / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
                sentinel = root / "var/cache/apt/archives/first-write-sentinel"
                sentinel.parent.mkdir(parents=True)
                sentinel.write_text("preflight must refuse before deletion")
                entries = [(str(root), "/", "ext4"), (str(root / relative), "/", "tmpfs")]
                with mock.patch.dict(self.guest, {"mounts": lambda: entries}):
                    for operation in ("scrub", "frozen_state"):
                        with self.assertRaisesRegex(ValueError, "mount hides"):
                            self.guest[operation](root)
                        self.assertEqual(sentinel.read_text(), "preflight must refuse before deletion")
                entries = [(str(root), "/", "ext4"), (str(root / "unrelated"), "/", "tmpfs")]
                with mock.patch.dict(self.guest, {"mounts": lambda: entries}):
                    self.guest["scrub"](root)
                    self.assertEqual(self.guest["frozen_state"](root), "scrubbed")

    def test_guest_tmp_unmount_exposes_and_scrubs_hidden_persistent_files(self):
        (self.root / "etc/ssh").mkdir(parents=True)
        (self.root / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
        target = self.root / "tmp"
        hidden = self.root / "hidden-under-tmpfs"
        hidden.mkdir()
        (hidden / "persisted-build-secret").write_text("must be removed")
        target.mkdir()
        (target / "volatile-build-secret").write_text("tmpfs contents")
        mounted = [True]
        active = {unit: True for unit in self.guest["FREEZE_WRITERS"]}

        def mounts():
            return [(str(target), "/", "tmpfs")] if mounted[0] else []

        def run(arguments, **unused):
            if arguments[:4] == ["systemctl", "mask", "--runtime", "--now"]:
                for unit in arguments[4:]:
                    active[unit] = False
            elif arguments[:2] == ["umount", "--"]:
                self.assertFalse(any(active.values()))
                self.assertEqual(arguments, ["umount", "--", str(target)])
                self.guest["shutil"].rmtree(target)
                hidden.rename(target)
                mounted[0] = False
            return subprocess.CompletedProcess(arguments, 0)

        with mock.patch.dict(self.guest, {"mounts": mounts}), \
                mock.patch.object(self.guest["subprocess"], "run", side_effect=run), \
                mock.patch.object(self.guest["subprocess"], "check_output", return_value="inactive\n"):
            self.assertEqual(self.guest["prepare_freeze"](self.root), "scrubbed")
        self.assertFalse(mounted[0])
        self.assertFalse(hidden.exists())
        self.assertEqual(list(target.iterdir()), [])

    def test_freeze_refuses_mounted_account_tables_before_deleting_builder(self):
        (self.root / "etc").mkdir()
        passwd = self.root / "etc/passwd"
        initial = "root:x:0:0::/root:/bin/bash\nbuilder:x:1000:1000::/home/builder:/bin/bash\n"
        passwd.write_text(initial)
        calls = []

        def run(arguments, **unused):
            calls.append(arguments)
            if arguments == ["userdel", "builder"]:
                passwd.write_text("root:x:0:0::/root:/bin/bash\n")
            return subprocess.CompletedProcess(arguments, 0)

        entries = [(str(self.root / "etc"), "/", "tmpfs")]
        with mock.patch.dict(self.guest, {"mounts": lambda: entries}), \
                mock.patch.object(self.guest["subprocess"], "run", side_effect=run), \
                mock.patch.object(self.guest["subprocess"], "check_output", return_value="inactive\n"):
            with self.assertRaisesRegex(ValueError, "mount hides"):
                self.guest["prepare_freeze"](self.root)
        self.assertEqual(passwd.read_text(), initial)
        self.assertNotIn(["userdel", "builder"], calls)

    def test_guest_tmp_refuses_other_filesystems_bind_roots_nested_or_failed_unmount(self):
        target = str(self.root / "tmp")
        for entries in ([(target, "/", "ext4")], [(target, "/subdir", "tmpfs")],
                        [(target, "/", "tmpfs"), (target + "/nested", "/", "tmpfs")]):
            with self.subTest(entries=entries), mock.patch.dict(self.guest, {"mounts": lambda: entries}), \
                    mock.patch.object(self.guest["subprocess"], "run") as runner:
                with self.assertRaises(ValueError):
                    self.guest["expose_persistent_tmp"](self.root)
                runner.assert_not_called()
        with mock.patch.dict(self.guest, {"mounts": lambda: [(target, "/", "tmpfs")]}), \
                mock.patch.object(self.guest["subprocess"], "run"):
            with self.assertRaisesRegex(ValueError, "still hides"):
                self.guest["expose_persistent_tmp"](self.root)

    def test_build_diagnostics_persist_after_failure_and_use_both_fixed_consoles(self):
        log = self.root / "var/lib/agent-image-build.log"
        log.parent.mkdir(parents=True)
        opened = []
        real_open = os.open

        def open_path(path, flags, *args, **kwargs):
            opened.append(str(path))
            if str(path) == "/var/lib/agent-image-build.log":
                return real_open(log, flags, *args, **kwargs)
            if str(path) in ("/dev/hvc0", "/dev/ttyS0"):
                raise FileNotFoundError(str(path))
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(self.guest["os"], "open", side_effect=open_path), \
                mock.patch("builtins.print"):
            self.guest["build_event"]("AGENT_IMAGE_BUILD packages complete")
            log.unlink()  # Cleanup may unlink the progress log before a later failure.
            self.guest["build_event"]("AGENT_IMAGE_BUILD failed\nsynthetic OSError EBUSY")
        self.assertIn("synthetic OSError EBUSY", log.read_text())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        self.assertIn("/dev/hvc0", opened)
        self.assertIn("/dev/ttyS0", opened)
        with self.assertRaisesRegex(ValueError, "identity/cache"):
            self.guest["frozen_state"](self.root)

    def test_freeze_finishes_shutdown_writers_before_deleting_their_state(self):
        (self.root / "etc/ssh").mkdir(parents=True)
        (self.root / "etc/passwd").write_text("builder:x:1000:1000::/home/builder:/bin/bash\n")
        active = {unit: True for unit in self.guest["FREEZE_WRITERS"]}
        seed = self.root / "var/lib/systemd/random-seed"

        def run(arguments, **unused):
            if arguments[:4] == ["systemctl", "mask", "--runtime", "--now"]:
                for unit in arguments[4:]:
                    if unit == "systemd-random-seed.service":
                        seed.parent.mkdir(parents=True, exist_ok=True)
                        seed.write_text("ExecStop saved build entropy")
                    active[unit] = False
            elif arguments == ["userdel", "builder"]:
                # Account tools preserve a pre-deletion backup unless scrub removes it.
                (self.root / "etc/passwd-").write_text((self.root / "etc/passwd").read_text())
                (self.root / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
            return subprocess.CompletedProcess(arguments, 0)

        def state(arguments, **unused):
            return "active\n" if active[arguments[-1]] else "inactive\n"

        with mock.patch.object(self.guest["subprocess"], "run", side_effect=run), \
                mock.patch.object(self.guest["subprocess"], "check_output", side_effect=state):
            self.assertEqual(self.guest["prepare_freeze"](self.root), "scrubbed")
        # A subsequent shutdown only saves entropy for a still-active service.
        if active["systemd-random-seed.service"]:
            seed.write_text("late shutdown rewrite")
        self.assertEqual(self.guest["frozen_state"](self.root), "scrubbed")
        self.assertFalse(seed.exists())
        self.assertFalse((self.root / "etc/passwd-").exists())

    def test_freeze_refuses_an_active_writer_before_deleting_evidence(self):
        seed = self.root / "var/lib/systemd/random-seed"
        seed.parent.mkdir(parents=True)
        seed.write_text("build entropy")
        with mock.patch.object(self.guest["subprocess"], "run"), \
                mock.patch.object(self.guest["subprocess"], "check_output", return_value="active\n"):
            with self.assertRaisesRegex(ValueError, "writer is still active"):
                self.guest["prepare_freeze"](self.root)
        self.assertEqual(seed.read_text(), "build entropy")

    def test_package_consumer_rejects_drift(self):
        lock = builder.load_lock(LOCK)["architectures"]["arm64"]
        packages = {**lock["packages"]["base"], **lock["packages"]["install"]}
        self.guest["check_packages"](lock, packages, True)
        packages["git"] = "0.0.0"
        with self.assertRaises(ValueError):
            self.guest["check_packages"](lock, packages, True)

    def test_emitted_installer_fetches_and_extracts_only_the_explicit_zone_tools(self):
        config = builder.load_lock(LOCK)["architectures"]["arm64"]
        for zone, selected in builder.ZONE_TOOLS.items():
            with self.subTest(zone=zone), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                work = root / "work"
                work.mkdir()
                prefix = root / "agent-tools"
                launchers = root / "bin"
                launchers.mkdir()
                fetched = []

                def download(spec, target):
                    name = next(name for name, value in config["tools"].items() if value == spec)
                    fetched.append(name)
                    with tarfile.open(target, "w:gz") as archive:
                        member = tarfile.TarInfo(spec["member"])
                        content = b"synthetic native payload, never executed"
                        member.size = len(content)
                        member.mode = 0o755
                        archive.addfile(member, io.BytesIO(content))

                def version(arguments, **unused):
                    self.assertEqual(arguments[:4], ["runuser", "-u", "builder", "--"])
                    self.assertEqual(arguments[-1], "--version")
                    name = next(name for name, spec in config["tools"].items()
                                if arguments[-2] == str(prefix / name / spec["member"]))
                    return config["tools"][name]["version"] + "\n"

                with mock.patch.dict(self.guest, {"download": download}), \
                        mock.patch.object(self.guest["subprocess"], "check_output", side_effect=version):
                    actual = self.guest["install_tools"](config, zone, work, prefix, launchers)
                self.assertEqual(actual, {name: builder.VERSIONS[name] for name in selected})
                self.assertEqual(set(fetched), selected)
                self.assertEqual({path.name for path in prefix.iterdir()}, selected)
                self.assertEqual({path.name for path in launchers.iterdir()}, selected)


class TestRecipeAndManifest(unittest.TestCase):
    def setUp(self):
        self.lock = builder.load_lock(LOCK)

    def test_seed_is_cloud_init_data_with_a_separate_guest_root_consumer(self):
        blob = (len(b"ssh-ed25519").to_bytes(4, "big") + b"ssh-ed25519"
                + (32).to_bytes(4, "big") + bytes(32))
        key = "ssh-ed25519 " + base64.b64encode(blob).decode() + " disposable-test"
        seed = builder.recipe(self.lock, "arm64", "internal", key)
        config = json.loads(seed["user-data"].split("\n", 1)[1])
        self.assertFalse(config["package_update"])
        self.assertFalse(config["package_upgrade"])
        self.assertEqual(config["users"][0]["ssh_authorized_keys"], [key])
        files = {item["path"]: item["content"] for item in config["write_files"]}
        self.assertEqual(files["/var/lib/agent-image-build/zone"], "internal")
        confidential = builder.recipe(self.lock, "arm64", "confidential", key)
        confidential_files = {item["path"]: item["content"] for item in json.loads(
            confidential["user-data"].split("\n", 1)[1])["write_files"]}
        self.assertEqual(confidential_files["/var/lib/agent-image-build/zone"], "confidential")
        self.assertNotEqual(json.loads(confidential["meta-data"])["instance-id"],
                            json.loads(seed["meta-data"])["instance-id"])
        with self.assertRaises(builder.ImageBuildError):
            builder.recipe(self.lock, "arm64", "all", key)
        emitted = files["/var/lib/agent-image-build/build.py"]
        compile(emitted, "emitted-guest", "exec")
        self.assertEqual(json.loads(files["/var/lib/agent-image-build/lock.json"]), self.lock)
        self.assertEqual(config["runcmd"], [["systemd-run", "--no-block", "--unit=agent-image-build",
                         "--property=After=cloud-final.service", "--property=StandardOutput=tty",
                         "--property=StandardError=tty", "--property=TTYPath=/dev/console",
                         "/usr/bin/python3", "/var/lib/agent-image-build/build.py"]])

    def manifest_fixture(self, root, arch, zone="internal"):
        config = self.lock["architectures"][arch]
        files = {}
        for name in builder.ZONE_TOOLS[zone]:
            spec = config["tools"][name]
            relative = "opt/agent-tools/" + name + "/" + spec["member"]
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("synthetic " + name).encode())
            path.chmod(0o755)
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            link = root / "usr/local/bin" / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to("/" + relative)
        (root / "etc/ssh").mkdir(parents=True)
        (root / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
        guest_consumer()["scrub"](root)
        for item in guest._seed_files():
            relative = item["path"].lstrip("/")
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"])
            target.chmod(int(item["permissions"], 8))
            files[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        return {"version": 1, "architecture": arch, "zone": zone, "lock_sha256": builder.lock_digest(self.lock),
                "tools": {name: builder.VERSIONS[name] for name in builder.ZONE_TOOLS[zone]}, "packages": {**config["packages"]["base"], **config["packages"]["install"]},
                "files": files, "lifecycle": "scrubbed"}

    def test_offline_manifest_checks_actual_files_and_does_not_approve(self):
        for arch in builder.ARCHES:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                manifest = self.manifest_fixture(root, arch)
                result = builder.verify_manifest(self.lock, arch, "internal", manifest, root)
                self.assertEqual(result["files_checked"], len(manifest["files"]))
                self.assertFalse(result["approval"])
                self.assertFalse(result["runtime_boot_verified"])
                self.assertTrue(result["consistent"])
                self.assertFalse(result["provenance_verified"])
                path = root / next(iter(manifest["files"]))
                path.write_bytes(b"tampered")
                with self.assertRaises(builder.ImageBuildError):
                    builder.verify_manifest(self.lock, arch, "internal", manifest, root)

    def test_manifest_detects_identity_undeclared_file_and_wrong_architecture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            manifest = self.manifest_fixture(root, "arm64")
            with self.assertRaises(builder.ImageBuildError):
                builder.verify_manifest(self.lock, "amd64", "internal", manifest, root)
            extra = root / "opt/agent-tools/unrecorded"
            extra.write_bytes(b"unrecorded executable")
            with self.assertRaises(builder.ImageBuildError):
                builder.verify_manifest(self.lock, "arm64", "internal", manifest, root)
            extra.unlink()
            seed = root / "var/lib/cloud/seed/nocloud"
            seed.mkdir(parents=True)
            (seed / "user-data").write_text("old build public key")
            with self.assertRaises(builder.ImageBuildError):
                builder.verify_manifest(self.lock, "arm64", "internal", manifest, root)

    def test_post_freeze_inspection_rejects_each_reintroduced_build_remnant(self):
        remnants = (("var/lib/dhcp/lease", "var/lib/dhcp"),
                    ("var/lib/NetworkManager/lease", "var/lib/NetworkManager"),
                    ("etc/passwd-", "etc/passwd-"), ("etc/shadow-", "etc/shadow-"),
                    ("etc/group-", "etc/group-"), ("etc/gshadow-", "etc/gshadow-"),
                    ("etc/subuid-", "etc/subuid-"), ("var/backups/shadow.bak", "var/backups/shadow.bak"),
                    ("var/log/build.log", "var/log/build.log"), ("root/.zsh_history", "root/.zsh_history"),
                    ("var/lib/systemd/random-seed", "var/lib/systemd/random-seed"),
                    ("tmp/credential", "tmp/credential"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            manifest = self.manifest_fixture(root, "arm64")
            for relative, remove in remnants:
                with self.subTest(remnant=relative):
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("synthetic build identity")
                    with self.assertRaises(builder.ImageBuildError):
                        builder.verify_manifest(self.lock, "arm64", "internal", manifest, root)
                    guest_consumer()["remove"](root / remove)
                    self.assertTrue(builder.verify_manifest(self.lock, "arm64", "internal", manifest, root)["consistent"])

    def test_confidential_recipe_and_manifest_permit_pi_only(self):
        for arch in builder.ARCHES:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                manifest = self.manifest_fixture(root, arch, "confidential")
                self.assertEqual(manifest["tools"], {"pi": "1.4.0"})
                result = builder.verify_manifest(self.lock, arch, "confidential", manifest, root)
                self.assertEqual(result["zone"], "confidential")
                with self.assertRaises(builder.ImageBuildError):
                    builder.verify_manifest(self.lock, arch, "internal", manifest, root)
                for name in ("codex", "claude"):
                    leftover = root / "opt/agent-tools" / name
                    leftover.mkdir()
                    with self.assertRaises(builder.ImageBuildError):
                        builder.verify_manifest(self.lock, arch, "confidential", manifest, root)
                    leftover.rmdir()
                    link = root / "usr/local/bin" / name
                    link.symlink_to("/missing-build-leftover")
                    with self.assertRaises(builder.ImageBuildError):
                        builder.verify_manifest(self.lock, arch, "confidential", manifest, root)
                    link.unlink()
                    changed = copy.deepcopy(manifest)
                    changed["tools"][name] = builder.VERSIONS[name]
                    with self.assertRaises(builder.ImageBuildError):
                        builder.verify_manifest(self.lock, arch, "confidential", changed, root)
                    changed = copy.deepcopy(manifest)
                    changed["files"]["opt/agent-tools/" + name + "/hidden"] = "a" * 64
                    with self.assertRaises(builder.ImageBuildError):
                        builder.verify_manifest(self.lock, arch, "confidential", changed)
    def test_only_disposable_adapter_has_a_nic(self):
        policy = {"version": 1, "zones": {zone: {"cpus": 4, "memory_mib": 4096, "disk_gib": 64,
                                                 "files": None, "model": None}
                                          for zone in vm.ZONES}}
        for arch in builder.ARCHES:
            argv = builder.build_argv(arch, "/tmp/disposable-image-build", "/tmp/build-seed.iso", "/usr/bin/hypervisor")
            self.assertTrue(any("virtio-net" in token for token in argv))
            self.assertFalse(any("hostfwd" in token or "virtio-fs" in token for token in argv))
            with self.assertRaises(builder.ImageBuildError):
                builder.build_argv(arch, "/tmp/internal", "/tmp/build-seed.iso", "/usr/bin/hypervisor")
        for zone in vm.ZONES:
            production = vm.build_vfkit_argv(zone, policy, "/tmp/" + zone, "/usr/bin/vfkit")
            self.assertFalse(any("virtio-net" in token for token in production))
            _, production = vm.build_linux_argv(zone, policy, "/tmp/" + zone, "/usr/bin/qemu", "/usr/bin/vsock")
            self.assertEqual(production[production.index("-nic") + 1], "none")


if __name__ == "__main__":
    unittest.main()
