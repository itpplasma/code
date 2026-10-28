"""Operator argv, record and raw/hybrid SSH transport tests."""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import socket
import stat
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

from test_agent_zone_deploy import deploy, policy
from ai_zones import cli


class ClientTests(unittest.TestCase):
    def test_exact_cli_grammar(self):
        self.assertEqual(cli.parse_args(["internal"]), ("internal", []))
        self.assertEqual(cli.parse_args(["confidential", "--", "printf", "a b"]),
                         ("confidential", ["printf", "a b"]))
        for argv in ([], ["public"], ["internal", "--"], ["internal", "-L", "5001"],
                     ["--config=/tmp/x", "internal"], ["internal", "--", "echo\x00"]):
            with self.assertRaises(cli.ClientError):
                cli.parse_args(argv)

    def test_ssh_pins_forwarding_and_shell_quoting(self):
        remote = ["printf", "%s\n", "a b", "'quote'", "$(touch /tmp/no)", "", "--path"]
        argv = cli.ssh_argv("/Library/AgentSandbox", "internal", "/Users/operator/.ssh/agent-sandbox/internal_ed25519", remote)
        self.assertEqual(argv[0], "/usr/bin/ssh")
        self.assertEqual(argv[1:3], ["-F", "/dev/null"])
        self.assertIn("IdentityAgent=none", argv)
        self.assertIn("ForwardAgent=no", argv)
        self.assertIn("ClearAllForwardings=yes", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("UserKnownHostsFile=/Library/AgentSandbox/pins/internal.known_hosts", argv)
        self.assertIn("ProxyCommand=/usr/bin/sudo -n /Library/AgentSandbox/bin/admin proxy internal", argv)
        self.assertEqual(shlex.split(argv[-1]), remote)
        self.assertEqual(argv[-2], "agent@internal")
        self.assertNotIn("accept-new", " ".join(argv))

    def test_start_then_ssh_with_clean_environment(self):
        operator = {"name": "operator", "uid": 501, "private_dir": "/Users/operator/.ssh/agent-sandbox"}
        calls = []
        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return types.SimpleNamespace(returncode=0)
        with mock.patch.object(cli.sys, "platform", "darwin"), \
             mock.patch.object(cli, "operator_record", return_value=operator), \
             mock.patch.object(cli, "private_key", return_value=operator["private_dir"] + "/internal_ed25519"), \
             mock.patch.object(cli, "assert_protected"), \
             mock.patch.dict(os.environ, {"HOME": "/tmp/attacker", "SSH_AUTH_SOCK": "/tmp/stolen", "PYTHONPATH": "/tmp/hook", "AI_ZONE_PREFIX": "/tmp/evil"}):
            self.assertEqual(cli.client("internal", [], runner=runner), 0)
        self.assertEqual(calls[0][0], ["/usr/bin/sudo", "-n", "/Library/AgentSandbox/bin/admin", "start", "internal"])
        self.assertEqual(calls[1][0][0], "/usr/bin/ssh")
        for _, kwargs in calls:
            self.assertEqual(kwargs["cwd"], "/")
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})

    def test_start_failure_prevents_ssh(self):
        with mock.patch.object(cli, "operator_record", return_value={}), \
             mock.patch.object(cli, "private_key", return_value="/fixed/key"), \
             mock.patch.object(cli, "assert_protected"):
            runner = mock.Mock(return_value=types.SimpleNamespace(returncode=1))
            with self.assertRaises(cli.ClientError):
                cli.client("internal", [], runner=runner)
            self.assertEqual(runner.call_count, 1)

    def test_real_policy_rejects_unprotected_operator_record(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config"
            config.mkdir()
            (config / "operator.json").write_text("{}")
            with self.assertRaises(policy.PolicyError):
                cli.operator_record(temp)

    def test_operator_record_binds_real_uid_not_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config"
            config.mkdir()
            record = {"version": 1, "zones": list(cli.ZONES), "operator": {
                "name": "operator", "uid": 501, "private_dir": "/Users/operator/.ssh/agent-sandbox"}}
            (config / "operator.json").write_text(json.dumps(record))
            actual = types.SimpleNamespace(pw_name="operator", pw_dir="/Users/operator")
            # Only the root-chain assertion is synthetic here; separate test exercises it.
            with mock.patch.object(cli, "assert_protected"), mock.patch.object(cli.pwd, "getpwuid", return_value=actual):
                self.assertEqual(cli.operator_record(temp, 501), record["operator"])
                with self.assertRaises(cli.ClientError):
                    cli.operator_record(temp, 502)
                with self.assertRaises(cli.ClientError):
                    cli.operator_record(temp, 0)

    def test_proxy_transport_raw_mac_and_linux_handshake(self):
        for platform_name in ("darwin", "linux"):
            with self.subTest(platform=platform_name), tempfile.TemporaryDirectory() as temp:
                zone = "internal"
                parent = Path(temp) / "run/internal/vm"
                parent.mkdir(parents=True, mode=0o700)
                parent.chmod(0o700)
                paths = deploy.socket_paths(temp, zone)
                path = paths["ssh"] if platform_name == "darwin" else paths["linux_ssh"]
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                listener.bind(path)
                os.chmod(path, 0o600)
                listener.listen(1)
                self.addCleanup(listener.close)
                seen = []
                dropped = []
                def test_drop(vm):
                    dropped.append((vm["uid"], vm["gid"]))
                def server():
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(2)
                        if platform_name == "linux":
                            data = bytearray()
                            while not data.endswith(b"\n"):
                                data.extend(connection.recv(1))
                            seen.append(bytes(data))
                            connection.sendall(b"OK 2222\n")
                        connection.sendall(b"SSH-2.0-synthetic\r\n")
                        seen.append(connection.recv(64))
                thread = threading.Thread(target=server)
                thread.start()
                accounts = {zone: {"vm": {"uid": os.getuid(), "gid": os.getgid()}}}
                with mock.patch.object(deploy.policy, "assert_protected"), mock.patch.object(deploy.policy, "assert_no_mutating_acl"):
                    connection = deploy.proxy_connect(temp, zone, accounts, platform_name, _drop_identity=test_drop)
                self.assertEqual(dropped, [(os.getuid(), os.getgid())])
                with connection:
                    self.assertEqual(connection.recv(64), b"SSH-2.0-synthetic\r\n")
                    connection.sendall(b"client SSH bytes")
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
                if platform_name == "linux":
                    self.assertEqual(seen[0], b"CONNECT 2222\n")
                self.assertEqual(seen[-1], b"client SSH bytes")

    def test_permanent_proxy_drop_closes_descriptors_then_groups_gid_uid(self):
        calls = []
        state = {"uid": 0, "gid": 0, "groups": [0, 80]}
        native = types.SimpleNamespace(
            listdir=lambda path: ["0", "1", "2", "3", "8"],
            close=lambda fd: calls.append(("close", fd)),
            setgroups=lambda groups: (calls.append(("setgroups", groups)), state.update(groups=groups)),
            setgid=lambda gid: (calls.append(("setgid", gid)), state.update(gid=gid)),
            setuid=lambda uid: (calls.append(("setuid", uid)), state.update(uid=uid)),
            getuid=lambda: state["uid"], geteuid=lambda: state["uid"],
            getgid=lambda: state["gid"], getegid=lambda: state["gid"],
            getgroups=lambda: state["groups"],
            getresuid=lambda: (state["uid"],) * 3,
            getresgid=lambda: (state["gid"],) * 3)
        reader = lambda: [state["gid"]] if sys.platform == "darwin" else state["groups"]
        deploy._drop_proxy_identity({"uid": 602, "gid": 602}, syscalls=native, _group_reader=reader)
        self.assertEqual(calls, [("close", 3), ("close", 8), ("setgroups", []), ("setgid", 602), ("setuid", 602)])
        self.assertEqual(state, {"uid": 602, "gid": 602, "groups": []})
        native.getresuid = lambda: (602, 602, 0)
        with self.assertRaisesRegex(deploy.DeployError, "saved user"):
            deploy._drop_proxy_identity({"uid": 602, "gid": 602}, syscalls=native, _group_reader=reader)

    def test_native_process_group_query_avoids_darwin_directory_membership(self):
        if sys.platform != "darwin":
            self.skipTest("Darwin kernel and directory group APIs differ")
        with mock.patch.object(deploy.os, "getgroups", side_effect=OSError(22, "directory membership EINVAL")):
            groups = deploy._kernel_process_groups()
        self.assertIsInstance(groups, list)
        self.assertGreaterEqual(len(groups), 1)
        self.assertEqual(groups[0], os.getegid())

    def test_proxy_drop_rejects_extra_kernel_groups(self):
        native = types.SimpleNamespace(listdir=lambda path: [], setgroups=lambda groups: None,
            setgid=lambda gid: None, setuid=lambda uid: None,
            getuid=lambda: 602, geteuid=lambda: 602, getgid=lambda: 602, getegid=lambda: 602,
            getgroups=lambda: [])
        for groups in ([602, 80], [80], [602, 602]):
            with self.subTest(groups=groups), self.assertRaisesRegex(deploy.DeployError, "remove root authority"):
                deploy._drop_proxy_identity({"uid": 602, "gid": 602}, syscalls=native,
                                            _group_reader=lambda: groups)

    def test_proxy_drop_failure_never_creates_or_connects_socket(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp) / "run/internal/vm"
            parent.mkdir(parents=True)
            parent.chmod(0o700)
            path = deploy.socket_paths(temp, "internal")["ssh"]
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with listener:
                listener.bind(path)
                os.chmod(path, 0o600)
                accounts = {"internal": {"vm": {"uid": os.getuid(), "gid": os.getgid()}}}
                def fail(vm):
                    raise deploy.DeployError("synthetic drop refusal")
                with mock.patch.object(deploy.policy, "assert_protected"), \
                     mock.patch.object(deploy.socket, "socket") as create:
                    with self.assertRaisesRegex(deploy.DeployError, "drop refusal"):
                        deploy.proxy_connect(temp, "internal", accounts, "darwin", _drop_identity=fail)
                    create.assert_not_called()

    def test_failed_setgroups_setgid_or_setuid_stops_drop_sequence(self):
        for failing in ("setgroups", "setgid", "setuid"):
            calls = []
            def action(name):
                def call(value):
                    calls.append(name)
                    if name == failing:
                        raise PermissionError("synthetic refusal")
                return call
            native = types.SimpleNamespace(listdir=lambda path: [], setgroups=action("setgroups"),
                                           setgid=action("setgid"), setuid=action("setuid"))
            with self.assertRaisesRegex(deploy.DeployError, "privilege drop failed"):
                deploy._drop_proxy_identity({"uid": 602, "gid": 602}, syscalls=native)
            self.assertEqual(calls, ["setgroups", "setgid", "setuid"][:["setgroups", "setgid", "setuid"].index(failing) + 1])

    def test_connected_unix_peer_identity_is_native(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        with left, right:
            self.assertEqual(deploy.connected_peer_identity(left), (os.geteuid(), os.getegid()))

    def test_proxy_refuses_wrong_connected_peer_before_sending_handshake(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp) / "run/internal/vm"
            parent.mkdir(parents=True)
            parent.chmod(0o700)
            path = deploy.socket_paths(temp, "internal")["linux_ssh"]
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with listener:
                listener.bind(path)
                os.chmod(path, 0o600)
                listener.listen(1)
                accounts = {"internal": {"vm": {"uid": os.getuid(), "gid": os.getgid()}}}
                with mock.patch.object(deploy.policy, "assert_protected"), \
                     mock.patch.object(deploy, "connected_peer_identity", return_value=(os.getuid() + 1, os.getgid())):
                    with self.assertRaisesRegex(deploy.DeployError, "connected peer identity"):
                        deploy.proxy_connect(temp, "internal", accounts, "linux", _drop_identity=lambda vm: None)
                peer, _ = listener.accept()
                with peer:
                    self.assertEqual(peer.recv(64), b"")

    def test_relay_preserves_stdin_after_peer_write_half_close(self):
        host, peer = socket.socketpair()
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        errors = []
        def relay():
            try:
                deploy.relay_stdio(host, input_read, output_write)
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=relay)
        thread.start()
        try:
            peer.settimeout(2)
            peer.sendall(b"server finished writing")
            peer.shutdown(socket.SHUT_WR)
            self.assertEqual(os.read(output_read, 64), b"server finished writing")
            os.write(input_write, b"client still writing")
            os.close(input_write)
            input_write = None
            received = bytearray()
            while True:
                chunk = peer.recv(64)
                if not chunk:
                    break
                received.extend(chunk)
            self.assertEqual(bytes(received), b"client still writing")
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
        finally:
            host.close()
            peer.close()
            for fd in (input_read, input_write, output_read, output_write):
                if fd is not None:
                    os.close(fd)

    def test_wrong_proxy_socket_owner_and_mode_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp) / "run/internal/vm"
            parent.mkdir(parents=True)
            parent.chmod(0o755)
            accounts = {"internal": {"vm": {"uid": os.getuid(), "gid": os.getgid()}}}
            with self.assertRaisesRegex(deploy.DeployError, "directory owner/mode"):
                deploy.proxy_connect(temp, "internal", accounts, "darwin")


if __name__ == "__main__":
    unittest.main()
