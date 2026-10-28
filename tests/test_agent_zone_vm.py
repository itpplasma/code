"""Parser-contract and framing tests for the AI zone hypervisor command builders.

These tests assert on the exact ``argv`` arrays the builders emit and on the
behaviour of the vsock handshake helper with real sockets.  They show that no
forbidden device, share or management channel can be expressed through the API
and that a malformed or hostile path is refused.  They are not proof of runtime
isolation: no VM is started here, and the executables are not checked for
ownership, hash or execution chain (deployment layer).
"""

import copy
import json
import os
import pathlib
import shutil
import socket
import sys
import tempfile
import threading
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from sandbox.ai_zones import vm
from sandbox.ai_zones.policy import load_policy, validate_policy

VM_PATH = REPO_ROOT / "sandbox" / "ai_zones" / "vm.py"
QEMU = "/usr/bin/qemu-system-x86_64"
BACKEND = "/usr/bin/vhost-device-vsock"
VFKIT = "/usr/local/bin/vfkit"


def policy_for(zone="internal", **overrides):
    entry = {"cpus": 4, "memory_mib": 4096, "disk_gib": 64, "files": None, "model": None}
    zones = {name: dict(entry) for name in vm.ZONES}
    zones[zone].update(overrides)
    return {"version": 1, "zones": zones}


class BuilderTestCase(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="agent-zone-state-")
        self.addCleanup(shutil.rmtree, self.state, True)

    def assertRefused(self, zone, policy, state, qemu_or_vfkit, second=None, *, why=""):
        args = (zone, policy, state, qemu_or_vfkit) + ((second,) if second is not None else ())
        with self.assertRaises(vm.VMBuildError, msg=why):
            if second is None:
                vm.build_vfkit_argv(*args)
            else:
                vm.build_linux_argv(*args)


class TestVfkitArgv(BuilderTestCase):
    def test_exact_command(self):
        self.assertEqual(
            vm.build_vfkit_argv("internal", policy_for(), self.state, VFKIT),
            [
                VFKIT,
                "--cpus", "4",
                "--memory", "4096",
                "--bootloader", "efi,variable-store=%s/efi-vars,create" % self.state,
                "--device", "virtio-blk,path=%s/root.raw" % self.state,
                "--device", "virtio-rng",
                "--device", "virtio-balloon",
                "--device", "virtio-serial,logFilePath=%s/console.log" % self.state,
                "--device", "virtio-vsock,port=2222,socketURL=%s/ssh.sock,connect" % self.state,
                "--device", "virtio-vsock,port=5001,socketURL=%s/broker.sock,listen" % self.state,
                "--restful-uri", "none://",
            ],
        )

    def test_no_network_share_or_management_surface(self):
        joined = " ".join(vm.build_vfkit_argv("confidential", policy_for("confidential"), self.state, "/opt/vfkit/vfkit"))
        for needle in (
            "virtio-net", "virtio-fs", "virtiofs", "rosetta", "virtio-gpu", "virtio-input",
            "ignition", "sharedDir", "mountTag", "cloud-init", "--kernel", "--initrd",
            "tcp://", "unix://", "--unix-socket", "--clipboard", "--usb",
        ):
            self.assertNotIn(needle, joined, needle)

    def test_management_rest_stays_disabled(self):
        argv = vm.build_vfkit_argv("internal", policy_for(), self.state, VFKIT)
        self.assertIn("--restful-uri", argv)
        self.assertEqual(argv[argv.index("--restful-uri") + 1], "none://")

    def test_only_the_two_fixed_vsock_channels(self):
        argv = vm.build_vfkit_argv("internal", policy_for(), self.state, VFKIT)
        devices = [argv[i + 1] for i, token in enumerate(argv) if token == "--device"]
        self.assertEqual(len(devices), 6)
        vsocks = [d for d in devices if d.startswith("virtio-vsock")]
        self.assertEqual(len(vsocks), 2)
        self.assertIn("port=2222", vsocks[0])
        self.assertIn("port=5001", vsocks[1])

    def test_every_device_path_is_under_the_state_dir(self):
        argv = vm.build_vfkit_argv("internal", policy_for(), self.state, VFKIT)
        checked = [t for t in argv if any(k in t.lower() for k in ("path=", "socketurl=", "variable-store="))]
        self.assertEqual(len(checked), 5)
        for token in checked:
            self.assertIn("%s/" % self.state, token)

    def test_comma_in_state_dir_cannot_inject_a_device(self):
        self.assertRefused("internal", policy_for(), self.state + ",x,--device=virtio-net", VFKIT, why="comma")

    def test_path_must_be_absolute_normalized_delimiter_free(self):
        for bad in ("state/root", "../state", self.state + "/", self.state + "//", self.state + "/.", self.state + "\x00x", self.state + ",share=off"):
            self.assertRefused("internal", policy_for(), bad, VFKIT, why=repr(bad))

    def test_socket_paths_must_not_need_url_escaping(self):
        for suffix in (" with space", "%20", "é", "#x", "?x"):
            self.assertRefused("internal", policy_for(), self.state + suffix, VFKIT)
        argv = vm.build_vfkit_argv("internal", policy_for(), self.state + " with space", VFKIT,
                                   runtime_dir=self.state, broker_socket=self.state + "/bridge.sock")
        self.assertIn("virtio-blk,path=" + self.state + " with space/root.raw", argv)

    def test_separate_vm_state_runtime_and_broker(self):
        argv = vm.build_vfkit_argv("internal", policy_for(), self.state, VFKIT,
                                   runtime_dir="/run/ai/internal", broker_socket="/run/ai/broker/internal.sock")
        self.assertIn("virtio-vsock,port=2222,socketURL=/run/ai/internal/ssh.sock,connect", argv)
        self.assertIn("virtio-vsock,port=5001,socketURL=/run/ai/broker/internal.sock,listen", argv)
        self.assertIn("virtio-blk,path=" + self.state + "/root.raw", argv)
        for bad in ("relative", "/run/ai/../x", "/run/ai,foo", "/run/ai with space"):
            with self.assertRaises(vm.VMBuildError):
                vm.build_vfkit_argv("internal", policy_for(), self.state, VFKIT, broker_socket=bad)
        with self.assertRaises(vm.VMBuildError):
            vm.build_vfkit_argv("internal", policy_for(), self.state, VFKIT,
                                broker_socket=self.state + "/ssh.sock")

    def test_executable_must_be_absolute_and_delimiter_free(self):
        for bad in ("vfkit", "./vfkit", "/usr/local/bin/vf,kit", "/usr/local/bin/vf kit/../vfkit"):
            with self.assertRaises(vm.VMBuildError, msg=bad):
                vm.build_vfkit_argv("internal", policy_for(), self.state, bad)

    def test_zone_must_be_a_fixed_name(self):
        for bad in ("internal/../confidential", "Internal", "ai", "", "internal ", "confidential/x", "internal,"):
            with self.assertRaises(vm.VMBuildError, msg=bad):
                vm.build_vfkit_argv(bad, policy_for(), self.state, VFKIT)

    def test_resources_outside_bounds_refused(self):
        for overrides in (
            {"cpus": 0}, {"cpus": 257}, {"cpus": True}, {"cpus": "4"}, {"cpus": 4.0},
            {"memory_mib": 128}, {"memory_mib": 1048577}, {"memory_mib": True},
            {"disk_gib": 0}, {"disk_gib": 16385}, {"disk_gib": False},
        ):
            self.assertRefused("internal", policy_for(**overrides), self.state, VFKIT, why=str(overrides))

    def test_unknown_or_missing_policy_keys_refused(self):
        good = policy_for()
        self.assertRefused("internal", dict(good, extra={}), self.state, VFKIT, why="unknown top key")
        self.assertRefused("internal", {"zones": good["zones"]}, self.state, VFKIT, why="missing version")
        entry = dict(good["zones"]["internal"])
        entry["gpu"] = True
        self.assertRefused("internal", {"version": 1, "zones": {"internal": entry}}, self.state, VFKIT, why="unknown zone key")
        del entry["gpu"]
        del entry["disk_gib"]
        self.assertRefused("internal", {"version": 1, "zones": {"internal": entry}}, self.state, VFKIT, why="missing disk_gib")
        self.assertRefused("internal", {"version": True, "zones": good["zones"]}, self.state, VFKIT, why="bool version")
        self.assertRefused("internal", {"version": 2, "zones": good["zones"]}, self.state, VFKIT, why="version 2")
        bad = copy.deepcopy(good)
        del bad["zones"]["confidential"]
        self.assertRefused("confidential", bad, self.state, VFKIT, why="zone absent from policy")

    def test_files_and_model_schema(self):
        for files in (
            {"root": self.state, "writable": True, "extra": 1},
            {"root": "relative"},
            {"root": self.state},
            {"root": self.state, "writable": "yes"},
            {"root": self.state, "writable": 1},
            [self.state],
        ):
            self.assertRefused("internal", policy_for(files=files), self.state, VFKIT, why=str(files))

    def test_model_url_must_be_a_plain_fixed_origin(self):
        model = {"url": "https://models.example.invalid", "name": "m", "token_file": None}
        vm.build_vfkit_argv("internal", policy_for(model=model), self.state, VFKIT)
        for bad_url in (
            "http://user@models.example.invalid/a.bin",
            "https://models.example.invalid/a.bin?token=x",
            "https://models.example.invalid/a.bin#frag",
            "ftp://models.example.invalid/a.bin",
            "https://models.example.invalid/a.bin",
        ):
            self.assertRefused("internal", policy_for(model=dict(model, url=bad_url)), self.state, VFKIT, why=bad_url)
        for bad_token in ("token", "../token", self.state + "/token\x00"):
            self.assertRefused("internal", policy_for(model=dict(model, token_file=bad_token)), self.state, VFKIT, why=bad_token)

    def test_environment_cannot_change_output(self):
        vfkit_before = vm.build_vfkit_argv("internal", policy_for(), self.state, VFKIT)
        linux_before = vm.build_linux_argv("internal", policy_for(), self.state, QEMU, BACKEND)
        for name, value in (
            ("VFKIT_PATH", "/evil/vfkit"),
            ("QEMU_PATH", "/evil/qemu"),
            ("STATE_DIR", "/evil/state"),
            ("AI_ZONE", "confidential"),
            ("AI_ZONE_CPUS", "128"),
            ("AI_ZONE_MEMORY_MIB", "999999"),
            ("AI_SANDBOX_INSTANCE", "ai-cloud"),
            ("VFKIT_DEVICE", "virtio-net"),
            ("QEMU_EXTRA_ARGS", "-device virtio-net-pci"),
        ):
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)
        self.assertEqual(vfkit_before, vm.build_vfkit_argv("internal", policy_for(), self.state, VFKIT))
        self.assertEqual(linux_before, vm.build_linux_argv("internal", policy_for(), self.state, QEMU, BACKEND))

    def test_builders_write_nothing(self):
        vm.build_vfkit_argv("internal", policy_for(), self.state, VFKIT)
        vm.build_linux_argv("internal", policy_for(), self.state, QEMU, BACKEND)
        self.assertEqual(os.listdir(self.state), [])

    def test_real_immutable_and_loaded_policy(self):
        raw = policy_for()
        immutable = validate_policy(raw)
        with self.assertRaises(TypeError):
            immutable.zones["internal"] = immutable.zones["internal"]
        expected = vm.build_vfkit_argv("internal", raw, self.state, VFKIT)
        self.assertEqual(expected, vm.build_vfkit_argv("internal", immutable, self.state, VFKIT))
        path = os.path.join(self.state, "policy.json")
        with open(path, "w") as handle:
            json.dump(raw, handle)
        loaded = load_policy(path, protected=False)
        self.assertEqual(expected, vm.build_vfkit_argv("internal", loaded, self.state, VFKIT))
        self.assertEqual(vm.build_linux_argv("internal", raw, self.state, QEMU, BACKEND),
                         vm.build_linux_argv("internal", loaded, self.state, QEMU, BACKEND))


class TestLinuxArgv(BuilderTestCase):
    def test_linux_runtime_sockets_separate_from_disk(self):
        backend, qemu = vm.build_linux_argv("internal", policy_for(), self.state, QEMU, BACKEND,
                                            runtime_dir="/run/ai/internal")
        self.assertIn("uds-path=/run/ai/internal/vsock", backend[2])
        self.assertIn("socket,id=vsock,reconnect=0,path=/run/ai/internal/vhost.socket", qemu)
        self.assertIn("file=" + self.state + "/root.raw,format=raw,if=virtio", qemu)

    def test_backend_exact(self):
        backend, _ = vm.build_linux_argv("internal", policy_for(), self.state, QEMU, BACKEND)
        self.assertEqual(
            backend,
            [
                BACKEND,
                "--vm",
                "guest-cid=4,uds-path=%s/vsock,socket=%s/vhost.socket,groups=internal" % (self.state, self.state),
            ],
        )

    def test_confidential_zone_gets_its_own_cid_and_group(self):
        internal, _ = vm.build_linux_argv("internal", policy_for(), self.state, QEMU, BACKEND)
        confidential, _ = vm.build_linux_argv("confidential", policy_for("confidential"), self.state, QEMU, BACKEND)
        self.assertIn("guest-cid=4", internal[2])
        self.assertIn("groups=internal", internal[2])
        self.assertIn("guest-cid=5", confidential[2])
        self.assertIn("groups=confidential", confidential[2])
        self.assertNotIn("internal", confidential[2].replace(self.state, ""))

    def test_backend_never_forwards_to_another_vsock_endpoint(self):
        for zone in vm.ZONES:
            backend, _ = vm.build_linux_argv(zone, policy_for(zone), self.state, QEMU, BACKEND)
            joined = " ".join(backend)
            self.assertNotIn("forward-cid", joined)
            self.assertNotIn("forward-listen", joined)
            self.assertIn("groups=", joined)

    def test_backend_executable_must_be_absolute(self):
        for bad in ("vhost-device-vsock", "./vhost-device-vsock", "/usr/bin/vhost,device-vsock"):
            with self.assertRaises(vm.VMBuildError, msg=bad):
                vm.build_linux_argv("internal", policy_for(), self.state, QEMU, bad)

    def test_qemu_exact(self):
        _, qemu = vm.build_linux_argv("internal", policy_for(), self.state, QEMU, BACKEND)
        self.assertEqual(
            qemu,
            [
                QEMU,
                "-nodefaults",
                "-no-user-config",
                "-nic", "none",
                "-display", "none",
                "-monitor", "none",
                "-machine", "q35,accel=kvm,memory-backend=mem0",
                "-object", "memory-backend-memfd,id=mem0,size=%d,share=on" % (4096 * 1024 * 1024),
                "-m", "4096",
                "-smp", "4",
                "-drive", "file=%s/root.raw,format=raw,if=virtio" % self.state,
                "-device", "virtio-rng-pci",
                "-device", "virtio-balloon-pci",
                "-chardev", "socket,id=vsock,reconnect=0,path=%s/vhost.socket" % self.state,
                "-device", "vhost-user-vsock-pci,chardev=vsock",
            ],
        )

    def test_qemu_has_no_kernel_firmware_network_or_host_devices(self):
        joined = " ".join(vm.build_linux_argv("internal", policy_for(), self.state, QEMU, BACKEND)[1])
        for needle in (
            "-kernel", "-initrd", "-bios", "-pflash", "-netdev", "-virtfs", "-fsdev",
            "-qmp", "-serial", "-usb", "-vga", "-soundhw", "virtio-net", "guest-agent",
            "-monitor stdio", "-net ",
        ):
            self.assertNotIn(needle, joined, needle)
        self.assertIn("-nic none", joined)
        self.assertIn("-monitor none", joined)

    def test_no_kernel_vhost_fallback(self):
        joined = " ".join(vm.build_linux_argv("internal", policy_for(), self.state, QEMU, BACKEND)[1])
        self.assertIn("vhost-user-vsock-pci", joined)
        self.assertNotIn("vhost-vsock-pci", joined.replace("vhost-user-vsock-pci", ""))
        self.assertIn("share=on", joined)

    def test_memory_backend_tracks_policy(self):
        _, qemu = vm.build_linux_argv("internal", policy_for(memory_mib=2048, cpus=2), self.state, QEMU, BACKEND)
        self.assertIn("memory-backend-memfd,id=mem0,size=%d,share=on" % (2048 * 1024 * 1024), qemu)
        self.assertEqual(qemu[qemu.index("-m") + 1], "2048")
        self.assertEqual(qemu[qemu.index("-smp") + 1], "2")

    def test_injected_paths_refused(self):
        for bad_state in (self.state + ",share=off", self.state + "\n", self.state + "/.."):
            with self.assertRaises(vm.VMBuildError, msg=repr(bad_state)):
                vm.build_linux_argv("internal", policy_for(), bad_state, QEMU, BACKEND)

    def test_state_paths_are_the_fixed_set(self):
        paths = vm.state_paths(self.state)
        self.assertEqual(sorted(paths), sorted(vm.STATE_FILENAMES))
        self.assertEqual(paths["root.raw"], self.state + "/root.raw")
        self.assertEqual(paths["vsock"], self.state + "/vsock")
        self.assertRaises(vm.VMBuildError, vm.state_paths, "relative/state")
        self.assertRaises(vm.VMBuildError, vm.state_paths, self.state + "/..")


class TestVsockHandshake(BuilderTestCase):
    """Behaviour of the fixed CONNECT/OK framing on a real unix socket."""

    def setUp(self):
        super().setUp()
        self.sockdir = tempfile.mkdtemp(prefix="agent-zone-vsock-")
        self.addCleanup(shutil.rmtree, self.sockdir, True)
        self.path = os.path.join(self.sockdir, "vsock")

    def serve(self, responder):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.path)
        listener.listen(1)
        self.addCleanup(listener.close)

        def loop():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            try:
                responder(conn)
            except OSError:
                pass
            finally:
                conn.close()

        threading.Thread(target=loop, daemon=True).start()

    def test_binary_stream_after_handshake(self):
        def responder(conn):
            self.assertEqual(conn.recv(16), b"CONNECT 2222\n")
            conn.sendall(b"OK 2222\n")
            conn.sendall(conn.recv(4096))

        self.serve(responder)
        stream = vm.vsock_ssh_connect(self.path, timeout=5)
        self.addCleanup(stream.close)
        stream.sendall(b"\x00SSH-\x01\xff")
        self.assertEqual(stream.recv(4096), b"\x00SSH-\x01\xff")

    def test_vfkit_is_raw_with_no_connect_handshake(self):
        def responder(conn):
            conn.sendall(b"SSH-2.0-test\r\n")
            self.assertEqual(conn.recv(64), b"client-data")
        self.serve(responder)
        with vm.vfkit_ssh_connect(self.path, timeout=5) as stream:
            self.assertEqual(stream.recv(64), b"SSH-2.0-test\r\n")
            stream.sendall(b"client-data")

    def test_linux_wrong_acknowledged_port_refused(self):
        def responder(conn):
            conn.recv(16)
            conn.sendall(b"OK 12345\n")
        self.serve(responder)
        self.assertRaises(vm.VMBuildError, vm.linux_vsock_ssh_connect, self.path, 2222, 5)

    def test_wrong_response_refused(self):
        def responder(conn):
            conn.recv(16)
            conn.sendall(b"ERR no listener\n")

        self.serve(responder)
        self.assertRaises(vm.VMBuildError, vm.vsock_ssh_connect, self.path, 2222, 5)

    def test_closed_before_response_refused(self):
        def responder(conn):
            conn.recv(16)

        self.serve(responder)
        self.assertRaises(vm.VMBuildError, vm.vsock_ssh_connect, self.path, 2222, 5)

    def test_overlong_response_line_refused(self):
        def responder(conn):
            conn.recv(16)
            conn.sendall(b"OK 2222 " + b"A" * 200 + b"\n")

        self.serve(responder)
        self.assertRaises(vm.VMBuildError, vm.vsock_ssh_connect, self.path, 2222, 5)

    def test_no_response_times_out(self):
        def responder(conn):
            conn.recv(4096)
            threading.Event().wait(2)

        self.serve(responder)
        with self.assertRaises(vm.VMBuildError):
            vm.vsock_ssh_connect(self.path, timeout=1)

    def test_only_the_fixed_port_is_reachable(self):
        for port in (22, 5001, 0, "2222", None):
            with self.assertRaises(vm.VMBuildError, msg=str(port)):
                vm.vsock_ssh_connect(self.path, port)

    def test_bad_path_and_timeout_refused(self):
        self.assertRaises(vm.VMBuildError, vm.vsock_ssh_connect, "relative/vsock")
        self.assertRaises(vm.VMBuildError, vm.vsock_ssh_connect, self.path, 2222, 0)
        self.assertRaises(vm.VMBuildError, vm.vsock_ssh_connect, self.path, 2222, 10 ** 6)


class TestModuleHygiene(unittest.TestCase):
    def test_no_environment_lookup_or_execution(self):
        source = VM_PATH.read_text()
        for needle in ("os.environ", "os.getenv", "subprocess", "os.system", "shell=True", "eval(", "exec("):
            self.assertNotIn(needle, source, needle)

    def test_no_host_manager_paths(self):
        source = VM_PATH.read_text()
        for needle in ("/run/libvirt", "/dev/vhost-vsock", "qemu:///system"):
            self.assertNotIn(needle, source, needle)


if __name__ == "__main__":
    unittest.main()
