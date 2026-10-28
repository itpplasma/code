"""Real key, file, daemon-parser and stream tests for generated guest inputs."""

import json
import hashlib
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from sandbox.ai_zones import guest


def receive_all(endpoint):
    chunks = []
    while True:
        chunk = endpoint.recv(65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


class TestSeed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="agent-zone-guest-")
        cls.directory = pathlib.Path(cls.scratch.name)
        cls.host = cls.directory / "host"
        cls.operator = cls.directory / "operator"
        for key in (cls.host, cls.operator):
            subprocess.run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                           check=True, capture_output=True)
        cls.private = cls.host.read_text()
        cls.public = cls.host.with_suffix(".pub").read_text().strip()
        cls.operator_public = cls.operator.with_suffix(".pub").read_text().strip()

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def seed(self):
        return guest.build_seed("internal", self.operator_public, self.private, self.public)

    def test_real_unencrypted_host_key_and_separate_operator_key(self):
        seed = self.seed()
        config = json.loads(seed["user-data"].split("\n", 1)[1])
        self.assertEqual(config["ssh_keys"]["ed25519_private"], self.private)
        self.assertEqual(config["ssh_keys"]["ed25519_public"], self.public)
        self.assertEqual(set(config["ssh_keys"]), {"ed25519_private", "ed25519_public"})
        staged = self.directory / "staged-host"
        staged.write_text(config["ssh_keys"]["ed25519_private"])
        staged.chmod(0o600)
        derived = subprocess.run(["/usr/bin/ssh-keygen", "-y", "-f", str(staged)],
                                 check=True, capture_output=True, text=True).stdout.split()[:2]
        self.assertEqual(derived, self.public.split()[:2])
        self.assertNotEqual(self.operator_public.split()[1], self.public.split()[1])
        self.assertEqual(config["users"][0]["sudo"], ["ALL=(ALL) NOPASSWD:ALL"])
        with self.assertRaises(guest.GuestSeedError):
            guest.build_seed("internal", self.operator_public, self.private, self.operator_public)

    def test_generated_seed_installs_and_starts_its_relays(self):
        config = json.loads(self.seed()["user-data"].split("\n", 1)[1])
        files = {item["path"]: item for item in config["write_files"]}
        self.assertEqual(files["/usr/local/lib/agent-zone-relay.py"]["content"], guest.relay_source())
        for name, unit in guest.relay_units().items():
            self.assertEqual(files["/etc/systemd/system/" + name]["content"], unit)
            self.assertNotIn("cloud-final", unit)
        self.assertIn(["/usr/sbin/sshd", "-t"], config["runcmd"])
        self.assertIn(["systemctl", "enable", "--now", *guest.relay_units()], config["runcmd"])
        self.assertNotIn("ssh", config)
        self.assertTrue(all("modules-load" not in name for name in files))

    def test_generated_sshd_dropin_with_real_daemon(self):
        daemon = "/usr/sbin/sshd"
        if not os.path.exists(daemon):
            self.skipTest("OpenSSH server binary unavailable")
        config = json.loads(self.seed()["user-data"].split("\n", 1)[1])
        dropin = next(item["content"] for item in config["write_files"]
                      if item["path"].endswith("00-agent-zone.conf"))
        path = self.directory / "sshd-test.conf"
        path.write_text(dropin)
        result = subprocess.run([daemon, "-T", "-f", str(path), "-h", str(self.host)],
                                check=True, capture_output=True, text=True)
        values = dict(line.split(" ", 1) for line in result.stdout.splitlines())
        for key in ("passwordauthentication", "kbdinteractiveauthentication", "permitrootlogin",
                    "allowtcpforwarding", "allowagentforwarding", "x11forwarding", "permittunnel"):
            self.assertEqual(values[key], "no", key)
        self.assertEqual(values["disableforwarding"], "yes")
        self.assertEqual(values["listenaddress"], "127.0.0.1:22")

    def test_write_seed_private_files(self):
        with tempfile.TemporaryDirectory() as root:
            output = str(pathlib.Path(root) / "seed")
            guest.write_seed("internal", output, str(self.operator.with_suffix(".pub")),
                             str(self.host), str(self.host.with_suffix(".pub")))
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o700)
            for name in ("user-data", "meta-data"):
                self.assertEqual(os.stat(output + "/" + name).st_mode & 0o777, 0o600)
            with self.assertRaises(guest.GuestSeedError):
                guest.write_seed("internal", output, str(self.operator.with_suffix(".pub")),
                                 str(self.host), str(self.host.with_suffix(".pub")))


class TestStagingIO(unittest.TestCase):
    def test_symlink_fifo_and_oversized_inputs_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "input"
            path.write_bytes(b"12345")
            link = pathlib.Path(root) / "link"
            link.symlink_to(path)
            fifo = pathlib.Path(root) / "fifo"
            os.mkfifo(fifo)
            for filename, limit in ((path, 4), (link, 10), (fifo, 10)):
                with self.assertRaises(guest.GuestSeedError):
                    guest._read_regular_file(str(filename), "input", limit)

    def test_input_growth_is_bounded_while_reading(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "input"
            path.write_bytes(b"a")
            actual = os.read
            def growing_read(fd, count):
                with path.open("ab") as handle:
                    handle.write(b"b" * 16)
                return actual(fd, count)
            with mock.patch.object(guest.os, "read", growing_read):
                with self.assertRaises(guest.GuestSeedError):
                    guest._read_regular_file(str(path), "input", 8)

    def test_output_directory_permissions_and_acl(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "output"
            path.mkdir(mode=0o755)
            with self.assertRaises(guest.GuestSeedError):
                guest._prepare_output_dir(str(path))
            path.chmod(0o500)
            with self.assertRaises(guest.GuestSeedError):
                guest._prepare_output_dir(str(path))
            path.chmod(0o700)
            with mock.patch.object(guest, "assert_no_mutating_acl", side_effect=guest.PolicyError("ACL grants mutation")):
                with self.assertRaises(guest.GuestSeedError):
                    guest._prepare_output_dir(str(path))
            link = pathlib.Path(root) / "link"
            link.symlink_to(path)
            with self.assertRaises(guest.GuestSeedError):
                guest._prepare_output_dir(str(link))

    def test_output_short_writes_and_symlinks(self):
        with tempfile.TemporaryDirectory() as root:
            target = str(pathlib.Path(root) / "out")
            actual = os.write
            with mock.patch.object(guest.os, "write", lambda fd, data: actual(fd, data[:3])):
                guest._write_new_file(target, b"binary\x00payload\xff", 0o600)
            self.assertEqual(pathlib.Path(target).read_bytes(), b"binary\x00payload\xff")
            link = pathlib.Path(root) / "link"
            link.symlink_to(target)
            with self.assertRaises(FileExistsError):
                guest._write_new_file(str(link), b"overwrite", 0o600)
            self.assertEqual(pathlib.Path(target).read_bytes(), b"binary\x00payload\xff")

    @unittest.skipUnless(sys.platform == "darwin", "Darwin extended ACL test")
    def test_actual_mutating_acl_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            output = pathlib.Path(root) / "output"
            output.mkdir(mode=0o700)
            subprocess.run(["/bin/chmod", "+a", "everyone allow write", str(output)], check=True)
            with self.assertRaises(guest.GuestSeedError):
                guest._prepare_output_dir(str(output))

    def test_image_hash_checks_real_regular_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            image = pathlib.Path(root) / "image.raw"
            data = b"synthetic published-fixture substitute\x00" * 100
            image.write_bytes(data)
            pin = {"version": 1, "distribution": "debian", "release": "13",
                   "architecture": "arm64", "format": "raw", "sha256": hashlib.sha256(data).hexdigest(),
                   "source_url": "https://cloud.debian.org/images/cloud/trixie/20261026-2643/debian-13-genericcloud-arm64-20261026-2643.raw"}
            descriptor = pathlib.Path(root) / "descriptor.json"
            descriptor.write_text(json.dumps(pin))
            summary, status = guest.verify_image(str(image), str(descriptor))
            self.assertEqual(status, 0)
            self.assertEqual(summary["size_bytes"], len(data))
            image.write_bytes(data + b"different")
            self.assertEqual(guest.verify_image(str(image), str(descriptor))[1], 1)
            image.unlink()
            os.mkfifo(image)
            with self.assertRaises(guest.GuestSeedError):
                guest.verify_image(str(image), str(descriptor))


class TestRelay(unittest.TestCase):
    def setUp(self):
        self.relay = {"__name__": "relay_under_test"}
        exec(compile(guest.relay_source(), "generated-relay.py", "exec"), self.relay)

    def pump(self):
        client, left = socket.socketpair()
        right, server = socket.socketpair()
        for endpoint in (client, server):
            endpoint.settimeout(5)
            self.addCleanup(endpoint.close)
        failures = []
        def run():
            try:
                self.relay["pump"](left, right, buffer_size=1024, idle_timeout=4)
            except BaseException as exc:
                failures.append(exc)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return client, server, thread, failures

    def test_fragmented_binary_half_close_then_response(self):
        client, server, thread, failures = self.pump()
        request = bytes(range(256)) * 20
        for offset in range(0, len(request), 17):
            client.sendall(request[offset:offset + 17])
        client.shutdown(socket.SHUT_WR)
        self.assertEqual(receive_all(server), request)
        response = b"\xff\x00response" * 1000
        server.sendall(response)
        server.shutdown(socket.SHUT_WR)
        self.assertEqual(receive_all(client), response)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    def test_full_duplex_large_streams_and_backpressure(self):
        client, server, thread, failures = self.pump()
        first, second = bytes(range(256)) * 8192, b"\x00\xff" * 1048576
        writes = []
        def send(endpoint, data):
            try:
                endpoint.sendall(data)
                endpoint.shutdown(socket.SHUT_WR)
            except BaseException as exc:
                writes.append(exc)
        senders = [threading.Thread(target=send, args=args, daemon=True)
                   for args in ((client, first), (server, second))]
        readers = {}
        def read(name, endpoint):
            readers[name] = receive_all(endpoint)
        receivers = [threading.Thread(target=read, args=args, daemon=True)
                     for args in (("client", client), ("server", server))]
        for worker in senders + receivers:
            worker.start()
        for worker in senders + receivers:
            worker.join(8)
            self.assertFalse(worker.is_alive())
        thread.join(5)
        self.assertEqual(readers, {"client": second, "server": first})
        self.assertEqual(writes + failures, [])

    def test_broker_entrypoint_uses_tcp_pair_and_fixed_vsock_target(self):
        listener = self.relay["bind_tcp"]("127.0.0.1", 0)
        self.addCleanup(listener.close)
        with mock.patch.dict(self.relay, {
            "AF_VSOCK": 40,
            "bind_tcp": lambda host, port: listener if (host, port) == ("127.0.0.1", 5001) else None,
            "serve": lambda actual, connector, stop: self.assertIs(actual, listener),
            "signal": mock.Mock(),
        }):
            self.assertEqual(self.relay["main"](["broker"]), 0)
        self.assertEqual(self.relay["VMADDR_ANY"], 0xffffffff)

    def test_multiple_connections_and_connection_cap(self):
        with tempfile.TemporaryDirectory() as root:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(pathlib.Path(root) / "relay.sock"))
            listener.listen(8)
            stop = threading.Event()
            peers, connected = [], threading.Event()
            def connector():
                left, right = socket.socketpair()
                peers.append(right)
                connected.set()
                return left
            worker = threading.Thread(target=self.relay["serve"], args=(listener, connector),
                                      kwargs={"max_connections": 2, "idle_timeout": 2, "stop": stop}, daemon=True)
            worker.start()
            clients = []
            try:
                for _ in range(2):
                    connected.clear()
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.settimeout(3)
                    client.connect(listener.getsockname())
                    clients.append(client)
                    self.assertTrue(connected.wait(3))
                third = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                third.settimeout(3)
                third.connect(listener.getsockname())
                self.assertEqual(third.recv(1), b"")
                third.close()
                for index, client in enumerate(clients):
                    client.sendall(bytes([index, 255]))
                    peers[index].settimeout(3)
                    self.assertEqual(peers[index].recv(2), bytes([index, 255]))
            finally:
                for endpoint in clients + peers:
                    endpoint.close()
                stop.set()
                worker.join(5)
                listener.close()
            self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
