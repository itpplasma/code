"""Synthetic behavioural probes for the broker's generated Seatbelt profile."""
from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sandbox"))
from ai_zones import confinement as c

# This installed canonical runtime is an unprivileged test source, not a
# production authority: /Applications is group writable on the probed host.
APPLE_RUNTIME = "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9"
APPLE_PYTHON = APPLE_RUNTIME + "/bin/python3.9"
LIVE = sys.platform == "darwin" and os.path.isfile(APPLE_PYTHON)
MODERN_RUNTIME = os.environ.get("AI_ZONES_TEST_RUNTIME_ROOT")
MODERN_PYTHON = os.path.join(MODERN_RUNTIME, "bin/python3.14") if MODERN_RUNTIME else None


def profile_args(**over):
    args = dict(unix_socket="/zone/state/b.sock", state_dir="/zone/state",
                model_port=8081, data_roots=[("/data/in", "read"), ("/data/out", "readwrite")],
                python="/Library/AgentSandbox/runtime/Python3.framework/Versions/3.9/bin/python3.9",
                runtime_root="/Library/AgentSandbox/runtime/Python3.framework/Versions/3.9",
                code_roots=["/Library/AgentSandbox/code"],
                read_files=["/Library/AgentSandbox/conf/policy.json", "/Library/AgentSandbox/conf/token"])
    args.update(over)
    return args


class GeneratorTests(unittest.TestCase):
    def test_literal_quoting(self):
        self.assertEqual(c.sbpl_quote('/a"b\\c'), '"/a\\"b\\\\c"')
        for bad in ("", "relative", "//a", "/a/", "/a//b", "/a/../b", "/a/./b",
                    "/a\x00", "/a\n", "/a\t", "/a\x7f", "/a\ud800", b"/a", "/" + "a" * 1025):
            with self.subTest(bad=repr(bad)), self.assertRaises(c.ConfinementError):
                c.sbpl_quote(bad)

    def test_relocated_runtime_and_exact_configuration(self):
        text = c.generate_macos_profile(**profile_args())
        self.assertTrue(text.startswith("(version 1)\n(deny default)\n"))
        self.assertIn('(allow network-outbound (remote tcp4 "localhost:8081"))', text)
        self.assertIn('(allow file-read* (literal "/Library/AgentSandbox/conf/token"))', text)
        self.assertIn('(allow file-read-metadata (literal "/Library/AgentSandbox/conf"))', text)
        for bad in ("regex", "SDK", "mach-lookup", "sysctl", "process-fork", "Applications",
                    "dirhelper", "application.sb", "system.sb", "CommandLineTools"):
            self.assertNotIn(bad, text)
        self.assertNotIn('(allow file-read* (subpath "/Library/AgentSandbox/conf"))', text)
        self.assertNotIn('(allow file-read* (subpath "/"))', text)

    def test_model_port_none_is_no_tcp_authority(self):
        """model_port=None means no TCP grant at all, nothing else changes."""
        full = c.generate_macos_profile(**profile_args())
        null = c.generate_macos_profile(**profile_args(model_port=None))
        self.assertIn('(allow network-outbound (remote tcp4 "localhost:8081"))', full)
        self.assertNotIn("network-outbound (remote", null)
        self.assertNotIn("tcp4", null)
        self.assertNotIn("localhost", null)
        self.assertEqual(null, "\n".join(line for line in full.splitlines()
                                          if "network-outbound (remote" not in line) + "\n")
        for port in (1, 22, 443, 8080, 65535):
            text = c.generate_macos_profile(**profile_args(model_port=port))
            self.assertIn(f'(allow network-outbound (remote tcp4 "localhost:{port}"))', text)

    def test_null_data_roots_are_no_data_authority(self):
        """An empty data_roots grants no data path, only the zone's own state."""
        null = c.generate_macos_profile(**profile_args(model_port=None, data_roots=()))
        self.assertNotIn("/data", null)
        self.assertEqual(null.count("file-write*"), 1)
        self.assertIn('(allow file-read* file-write* (subpath "/zone/state"))', null)
        self.assertIn('(allow network-bind (literal "/zone/state/b.sock"))', null)
        self.assertIn('(allow network-outbound (literal "/zone/state/b.sock"))', null)
        self.assertIn('(allow file-read* (literal "/Library/AgentSandbox/conf/token"))', null)
        self.assertIn('(allow file-read* (literal "/Library/AgentSandbox/conf/policy.json"))', null)
        self.assertIn('(allow file-read* (subpath "/Library/AgentSandbox/runtime/Python3.framework/Versions/3.9"))', null)
        self.assertIn('(allow file-read* (subpath "/Library/AgentSandbox/code"))', null)
        for over in (dict(data_roots=[("/data/in", "read")]), dict(data_roots=[("/data/out", "readwrite")])):
            with self.subTest(over=over):
                self.assertIn("/data", c.generate_macos_profile(**profile_args(**over)))

    def test_model_port_rejects_every_non_port_value(self):
        for bad in (True, False, 0, -1, 53, 65536, "80", "", "None",
                    8081.0, [8081], (8081,), {}, object()):
            with self.subTest(model_port=repr(bad)), self.assertRaises(c.ConfinementError):
                c.generate_macos_profile(**profile_args(model_port=bad))
        for good in (None, 1, 443, 8081, 65535):
            with self.subTest(model_port=repr(good)):
                c.generate_macos_profile(**profile_args(model_port=good))

    def test_invalid_grants(self):
        cases = [dict(state_dir="/"), dict(runtime_root="/"), dict(code_roots=["/"]),
                 dict(read_files=["/"]), dict(data_roots=[("/", "read")]),
                 dict(python="/usr/bin/python3"), dict(model_port=True), dict(model_port=53),
                 dict(runtime_uname="true"), dict(model_port=65536), dict(model_port=0), dict(model_port="80"),
                 dict(data_roots=[("/data", "rw")]), dict(code_roots=[]),
                 dict(unix_socket="/different/b.sock"),
                 dict(state_dir="/Library", unix_socket="/Library/sock"),
                 dict(data_roots=[("/Library/AgentSandbox/conf", "readwrite")]),
                 dict(data_roots=[("/zone", "read")]),
                 dict(data_roots=[("/data", "readwrite"), ("/data/in", "read")])]
        for over in cases:
            with self.subTest(over=over), self.assertRaises(c.ConfinementError):
                c.generate_macos_profile(**profile_args(**over))

    def test_launch_has_explicit_runtime_and_build_gate(self):
        with patch.object(c, "assert_macos_build", return_value=c.TESTED_MACOS_BUILD):
            argv = c.macos_launch_argv("/p.sb", "/code/main.py", python=APPLE_PYTHON, runtime_root=APPLE_RUNTIME)
        self.assertEqual(argv, [c.SANDBOX_EXEC, "-f", "/p.sb", APPLE_PYTHON,
                                "-I", "-S", "-B", "/code/main.py"])
        self.assertEqual(c.launch_env(), {})
        with patch.object(c.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "unknown\n", "")):
            with self.assertRaisesRegex(c.ConfinementError, "retesting"):
                c.macos_launch_argv("/p.sb", "/code/main.py", python=APPLE_PYTHON, runtime_root=APPLE_RUNTIME)

    def test_linux_readonly_mount_and_namespace_plan(self):
        text = c.generate_systemd_unit_fragment(socket_path="/run/zone/b.sock", root_directory="/srv/root",
                    data_mounts=[("/srv/in", "/data/in", "read")],
                    model_socket=("/run/model.sock", "/run/model.sock"))
        self.assertIn("BindReadOnlyPaths=/srv/in:/data/in\n", text)
        self.assertIn("RootDirectory=/srv/root", text)
        self.assertIn("PrivateNetwork=yes", text)
        self.assertIn("RUNTIME UNTESTED", text)
        self.assertNotIn(":ro", text)
        for bad in ("/data/x y", "/data/x:y", "/data/%n", '/data/"x', "/"):
            with self.assertRaises(c.ConfinementError):
                c.generate_systemd_unit_fragment(socket_path="/run/b.sock", root_directory="/srv/root",
                    data_mounts=[("/srv/in", bad, "read")], model_socket=("/run/m.sock", "/run/m.sock"))


ENTRY = r'''
import ctypes, http.client, importlib.util, json, os, socket, ssl, subprocess, sys
root, model, other = sys.argv[1:]
model, other = int(model), int(other)
out = {'executable': sys.executable, 'sys_path': sys.path, 'environment': dict(os.environ)}
def probe(name, fn):
    try: out[name] = {'allowed': True, 'value': fn()}
    except Exception as e: out[name] = {'allowed': False, 'errno': getattr(e, 'errno', None), 'detail': str(e)}
def read(path):
    with open(root + '/' + path) as f: return f.read()
def write(path):
    with open(root + '/' + path, 'w') as f: return f.write('synthetic')
def connect(family, address):
    with socket.socket(family) as s:
        s.settimeout(2); s.connect(address)
def own():
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(root+'/state/b.sock'); server.listen(1)
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(root+'/state/b.sock')
            accepted, _ = server.accept(); accepted.close()
probe('read', lambda: read('data/in/file'))
probe('write', lambda: write('data/out/file'))
probe('state', lambda: write('state/file'))
probe('token', lambda: read('conf/token'))
probe('policy_file', lambda: read('conf/policy.json'))
probe('own_uds', own)
probe('model_tcp', lambda: connect(socket.AF_INET, ('127.0.0.1', model)))
for name, path in [('sibling','sibling/secret'), ('config_sibling','conf/secret'),
                   ('symlink','data/in/link'), ('prefix','data/input/secret'),
                   ('regex_shadow','shadow/Applications/XcodeXapp/Contents/Developer/Platforms/MacOSXxplatform/Developer/SDKs/Fake/SDKSettings.plist'),
                   ('profile','profile.sb')]:
    probe(name, lambda path=path: read(path))
for name, path in [('readonly_write','data/in/file'), ('sibling_write','sibling/file'),
                   ('code_write','code/main.py'), ('token_write','conf/token')]:
    probe(name, lambda path=path: write(path))
probe('parent_listing', lambda: os.listdir(root))
probe('sibling_metadata', lambda: os.stat(root+'/sibling/secret'))
probe('other_tcp', lambda: connect(socket.AF_INET, ('127.0.0.1', other)))
probe('other_uds', lambda: connect(socket.AF_UNIX, root+'/sibling/other.sock'))
def udp():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s: s.sendto(b'x', ('127.0.0.1', other))
probe('udp', udp)
probe('dns_udp', lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'x', ('127.0.0.1', 53)))
probe('dns_tcp', lambda: connect(socket.AF_INET, ('127.0.0.1', 53)))
probe('dns_resolver', lambda: socket.getaddrinfo('localhost', 80))
for name, command in [('shell', '/bin/sh'), ('same_python_child', sys.executable)]:
    probe(name, lambda command=command: subprocess.run([command, '-c', 'pass'], env={}, capture_output=True, timeout=5).returncode)
def fork():
    pid = os.fork()
    if pid == 0: os._exit(0)
    os.waitpid(pid, 0)
probe('fork', fork)
def spawn():
    pid = os.posix_spawn(sys.executable, [sys.executable, '-I', '-S', '-B', '-c', 'pass'], {})
    return os.waitpid(pid, 0)[1]
probe('posix_spawn', spawn)
probe('ipv6_model_port', lambda: connect(socket.AF_INET6, ('::1', model)))
probe('runtime_write', lambda: open(sys.executable, 'ab'))
def tcp_bind():
    with socket.socket(socket.AF_INET) as s: s.bind(('127.0.0.1', 0))
probe('tcp_bind', tcp_bind)
probe('direct_exec_other', lambda: os.execve('/usr/bin/true', ['true'], {}))
lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
def other_sysctl():
    size = ctypes.c_size_t()
    if lib.sysctlbyname(b'hw.memsize', None, ctypes.byref(size), None, 0) != 0:
        raise OSError(ctypes.get_errno(), 'sysctlbyname hw.memsize')
    return size.value
probe('other_sysctl', other_sysctl)
bootstrap = ctypes.c_uint.in_dll(lib, 'bootstrap_port').value
lib.bootstrap_look_up.argtypes = [ctypes.c_uint, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint)]
def mach(name):
    port = ctypes.c_uint()
    result = lib.bootstrap_look_up(bootstrap, name.encode(), ctypes.byref(port))
    if port.value: lib.mach_port_deallocate(lib.mach_task_self(), port)
    return [result, bool(port.value)]
for name in ('com.apple.bsd.dirhelper', 'com.apple.dnssd.service', 'com.apple.coreservices.launchservicesd', 'com.apple.appleevents'):
    probe('mach_'+name, lambda name=name: mach(name))
if os.path.isfile(root+'/code/policy.py'):
    spec = importlib.util.spec_from_file_location('probe_policy', root+'/code/policy.py')
    policy = importlib.util.module_from_spec(spec); sys.modules[spec.name] = policy; spec.loader.exec_module(policy)
    probe('native_acl_file', lambda: policy.assert_no_mutating_acl(root+'/conf/policy.json'))
    probe('native_acl_root', lambda: policy.assert_no_mutating_acl('/'))
    probe('policy_parse', lambda: sorted(policy.load_policy(root+'/conf/policy.json', protected=False).zones))
    probe('production_policy', lambda: policy.load_policy(root+'/conf/policy.json'))
print(json.dumps(out))
'''


@unittest.skipUnless(LIVE, "requires macOS and the canonical test interpreter")
class LiveSeatbeltTests(unittest.TestCase):
    runtime_root = APPLE_RUNTIME
    python = APPLE_PYTHON
    runtime_executables = [APPLE_RUNTIME+"/Resources/Python.app/Contents/MacOS/Python"]
    runtime_uname = False

    def setUp(self):
        c.assert_macos_build()
        control = subprocess.run(['/usr/sbin/sysctl', '-n', 'hw.memsize'], env={},
                                 capture_output=True, text=True, check=True, timeout=10)
        self.assertGreater(int(control.stdout), 0)
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="conf-", dir="/private/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)
        for directory in ("code", "state", "data/in", "data/out", "data/input", "sibling", "conf"):
            os.makedirs(self.root + "/" + directory)
        shadow = "shadow/Applications/XcodeXapp/Contents/Developer/Platforms/MacOSXxplatform/Developer/SDKs/Fake/SDKSettings.plist"
        os.makedirs(os.path.dirname(self.root+"/"+shadow))
        for path in ("data/in/file", "sibling/secret", "data/input/secret", "conf/token", "conf/secret", shadow):
            with open(self.root+"/"+path, "w") as f: f.write("SYNTHETIC-CANARY")
        os.symlink(self.root+"/sibling/secret", self.root+"/data/in/link")
        with open(self.root+"/code/main.py", "w") as f: f.write(ENTRY)
        # Integration is opt-in until the shared policy lands in this checkout.
        policy_source = os.environ.get("AI_ZONES_POLICY_SOURCE")
        if policy_source:
            shutil.copyfile(policy_source, self.root+"/code/policy.py")
        self.have_policy = bool(policy_source)
        with open(self.root+"/conf/policy.json", "w") as f:
            json.dump({"version": 1, "zones": {name: {"cpus": 1, "memory_mib": 1024, "disk_gib": 1, "files": None, "model": None} for name in ("internal", "confidential")}}, f)
        self.listeners = []
        self.threads = []
        self.stop = threading.Event()
        self.addCleanup(self.close_servers)
        for _ in range(2):
            s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(4); s.settimeout(.1)
            self.listeners.append(s)
        uds = socket.socket(socket.AF_UNIX); uds.bind(self.root+"/sibling/other.sock"); uds.listen(4); uds.settimeout(.1)
        self.listeners.append(uds)
        ipv6 = socket.socket(socket.AF_INET6)
        ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        ipv6.bind(('::1', self.listeners[0].getsockname()[1])); ipv6.listen(4); ipv6.settimeout(.1)
        self.listeners.append(ipv6)
        for s in self.listeners:
            thread = threading.Thread(target=self.serve, args=(s,), daemon=True)
            thread.start(); self.threads.append(thread)
        # Both denied endpoints are real, reachable listeners before confinement.
        for s in self.listeners:
            with socket.socket(s.family) as client: client.connect(s.getsockname())

    def serve(self, server):
        while not self.stop.is_set():
            try:
                conn, _ = server.accept(); conn.close()
            except socket.timeout: pass
            except OSError: break

    def close_servers(self):
        self.stop.set()
        for server in self.listeners: server.close()
        for thread in self.threads: thread.join(timeout=1)

    def profile(self, **over):
        args = profile_args(
            unix_socket=self.root+"/state/b.sock", state_dir=self.root+"/state",
            python=self.python, runtime_root=self.runtime_root, code_roots=[self.root+"/code"],
            runtime_executables=self.runtime_executables, runtime_uname=self.runtime_uname,
            model_port=self.listeners[0].getsockname()[1],
            data_roots=[(self.root+"/data/in", "read"), (self.root+"/data/out", "readwrite")],
            read_files=[self.root+"/conf/policy.json", self.root+"/conf/token"])
        args.update(over)
        return c.generate_macos_profile(**args)

    def run_profile(self, text, entry=None):
        path = self.root+"/profile.sb"
        with open(path, "w") as f: f.write(text)
        argv = c.macos_launch_argv(path, entry or self.root+"/code/main.py",
                                   python=self.python, runtime_root=self.runtime_root)
        argv += [self.root, str(self.listeners[0].getsockname()[1]), str(self.listeners[1].getsockname()[1])]
        return subprocess.run(argv, env=c.launch_env(), cwd="/", close_fds=True,
                              capture_output=True, text=True, timeout=30)

    def test_live_matrix(self):
        out = self.run_profile(self.profile())
        self.assertEqual(out.returncode, 0, out.stderr)
        results = json.loads(out.stdout)
        for name in ("read", "write", "state", "token", "policy_file", "own_uds", "model_tcp"):
            self.assertTrue(results[name]["allowed"], (name, results[name]))
        for name in ("sibling", "config_sibling", "symlink", "prefix", "regex_shadow", "profile",
                     "readonly_write", "sibling_write", "code_write", "token_write", "parent_listing",
                     "other_tcp", "other_uds", "udp", "dns_udp", "dns_tcp", "shell", "same_python_child", "runtime_write", "fork", "posix_spawn", "ipv6_model_port", "sibling_metadata",
                     "tcp_bind", "direct_exec_other", "other_sysctl"):
            self.assertFalse(results[name]["allowed"], (name, results[name]))
            self.assertIn(results[name]["errno"], (errno.EPERM, errno.EACCES), (name, results[name]))
        self.assertFalse(results["dns_resolver"]["allowed"], results["dns_resolver"])
        for name, result in results.items():
            if name.startswith("mach_"): self.assertEqual(result["value"], [1100, False], (name, result))
        self.assertEqual(results["executable"], self.python)
        self.assertTrue(all(p.startswith(self.runtime_root+"/") for p in results["sys_path"]))
        self.assertNotIn("PYTHONPATH", results["environment"])
        if self.have_policy:
            for name in ("native_acl_root", "native_acl_file", "policy_parse"):
                self.assertTrue(results[name]["allowed"], (name, results[name]))
            self.assertFalse(results["production_policy"]["allowed"])
            self.assertIn("protected check:", results["production_policy"]["detail"])

    def _profile_text(self):
        with open(self.root + "/profile.sb") as handle:
            return handle.read()

    def test_live_null_capabilities(self):
        """A null profile denies the live model port and every data path."""
        model_port = self.listeners[0].getsockname()[1]
        other_port = self.listeners[1].getsockname()[1]
        # Positive controls: both listeners are reachable and both data paths
        # are readable or writable by this unconfined test process right now.
        for port in (model_port, other_port):
            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                self.assertIsNotNone(client)
        with open(self.root + "/data/in/file") as canary:
            self.assertEqual(canary.read(), "SYNTHETIC-CANARY")
        with open(self.root + "/data/out/file", "w") as canary:
            canary.write("SYNTHETIC-CANARY")
        out = self.run_profile(self.profile(model_port=None, data_roots=()))
        self.assertEqual(out.returncode, 0, out.stderr)
        results = json.loads(out.stdout)
        self.assertNotIn("tcp4", self._profile_text())
        for name in ("state", "token", "policy_file", "own_uds"):
            self.assertTrue(results[name]["allowed"], (name, results[name]))
        for name in ("model_tcp", "read", "write", "other_tcp", "other_uds", "udp",
                     "dns_udp", "dns_tcp", "ipv6_model_port", "tcp_bind", "parent_listing",
                     "sibling", "config_sibling", "symlink", "prefix", "profile"):
            self.assertFalse(results[name]["allowed"], (name, results[name]))
            self.assertIn(results[name]["errno"], (errno.EPERM, errno.EACCES), (name, results[name]))
        self.assertFalse(results["dns_resolver"]["allowed"], results["dns_resolver"])
        # Neither data path was touched: the denied writes left both canaries.
        for path in ("data/in/file", "data/out/file"):
            with open(self.root + "/" + path) as canary:
                self.assertEqual(canary.read(), "SYNTHETIC-CANARY", path)

    def test_corrupt_profile_fails_closed(self):
        out = self.run_profile(self.profile()+"\n(this-operation-does-not-exist)\n")
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(out.stdout, "")
        self.assertFalse(os.path.exists(self.root+"/state/file"))

    def test_complete_sexpression_injection_is_literal(self):
        hostile = self.root+'/data/evil"))(allow file-read* (subpath "/")) ;'
        os.makedirs(hostile)
        target = hostile+"/file"
        with open(target, "w") as f: f.write("allowed synthetic")
        entry = self.root+"/code/quoted.py"
        with open(entry, "w") as f:
            f.write("import json\nresult = [open(%r).read()]\n" % target)
            f.write("try: open(%r).read(); result.append('ESCAPE')\n" % (self.root+"/sibling/secret"))
            f.write("except PermissionError: result.append('DENIED')\nprint(json.dumps(result))\n")
        args = profile_args(unix_socket=self.root+"/state/b.sock", state_dir=self.root+"/state",
                            python=self.python, runtime_root=self.runtime_root, code_roots=[self.root+"/code"],
            runtime_executables=self.runtime_executables, runtime_uname=self.runtime_uname,
                            data_roots=[(hostile, "read")], read_files=[])
        out = self.run_profile(c.generate_macos_profile(**args), entry)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), ["allowed synthetic", "DENIED"])


@unittest.skipUnless(sys.platform == "darwin" and MODERN_PYTHON and os.path.isfile(MODERN_PYTHON),
                     "set AI_ZONES_TEST_RUNTIME_ROOT to probe a staged CPython 3.14 runtime")
class ModernRuntimeSeatbeltTests(LiveSeatbeltTests):
    runtime_root = MODERN_RUNTIME
    python = MODERN_PYTHON
    runtime_executables = []
    runtime_uname = True

    def test_ctypes_requires_explicit_uname_capability(self):
        out = self.run_profile(self.profile(runtime_uname=False))
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(out.stdout, "")
        self.assertIn("_os.uname()", out.stderr)
        self.assertIn("Operation not permitted", out.stderr)


if __name__ == "__main__":
    unittest.main()
