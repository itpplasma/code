#!/usr/bin/env python3
"""Exercise installed settings. Model execution requires explicit --include-pi."""
import argparse
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import uuid

ZONE = Path("/Library/Application Support/agent-zones")
NODE = "/opt/agents/srt/bin/node"
CLI = "/opt/agents/srt/node_modules/@anthropic-ai/sandbox-runtime/dist/cli.js"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-dir", type=Path, required=True,
                        help="Empty synthetic test directory under Nextcloud")
    parser.add_argument("--denied-url", action="append", default=[])
    parser.add_argument("--include-pi", action="store_true")
    parser.add_argument("--after-sudo", action="store_true")
    args = parser.parse_args()
    home = Path.home()
    probe = args.probe_dir.absolute()
    if not probe.is_relative_to(home / "Nextcloud") or probe.exists():
        parser.error("probe-dir must be new and under Nextcloud")
    state = home / ".local/state/pi-safe"
    env = {
        "HOME": str(home), "USER": os.environ["USER"],
        "PATH": "/opt/agents/srt/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "SHELL": "/bin/bash", "LANG": "en_US.UTF-8", "MPLBACKEND": "Agg",
        "PI_CODING_AGENT_DIR": str(ZONE / "pi-safe"),
        "PI_CODING_AGENT_SESSION_DIR": str(state / "sessions"),
    }
    failures = 0
    results = []

    def report(name, passed, detail=""):
        nonlocal failures
        results.append({"probe": name, "passed": passed, "detail": detail})
        failures += not passed
        print(f"{'PASS' if passed else 'FAIL'} {name} {detail}", flush=True)

    def run(command):
        return subprocess.run(
            [NODE, CLI, "--settings", str(ZONE / "safe.json"), "--",
             "/usr/bin/env", "NO_PROXY=", "no_proxy=", "NODE_USE_ENV_PROXY=1", *command],
            cwd=home / "Nextcloud", env=env, capture_output=True, text=True,
            timeout=30,
        )

    def code(name, program):
        result = run(["/usr/bin/python3", "-c", program])
        report(name, result.returncode == 0 and result.stdout.strip() == "PROBE_OK",
               f"exit={result.returncode}")
        return program

    programs = []

    def denied_open(name, path, flags):
        # Open only: even a broken policy must never print or consume a secret.
        program = f"""import os, errno
try:
    fd = os.open({str(path)!r}, {flags})
except OSError as e:
    assert e.errno in (errno.EPERM, errno.EACCES), e.errno
    print('PROBE_OK')
else:
    os.close(fd)
    raise SystemExit(1)
"""
        programs.append(code(name, program))

    code("sandbox starts", "print('PROBE_OK')")
    denied_open("SSH private key", home / ".ssh/id_rsa", "os.O_RDONLY")
    denied_open("Pi host authentication", home / ".pi/agent/auth.json", "os.O_RDONLY")
    denied_open("Keychain directory", home / "Library/Keychains", "os.O_RDONLY")
    denied_open("root settings write", ZONE / "safe.json", "os.O_WRONLY")
    denied_open("root wrapper write", Path("/usr/local/bin/safe"), "os.O_WRONLY")

    service = "safe-zone-synthetic-" + uuid.uuid4().hex
    created = subprocess.run([
        "/usr/bin/security", "add-generic-password", "-s", service,
        "-a", "synthetic", "-w", "synthetic-probe-only",
    ], capture_output=True, timeout=20).returncode == 0
    socket_path = state / ("socket-" + uuid.uuid4().hex)
    unix_server = socket.socket(socket.AF_UNIX)
    unix_server.bind(str(socket_path))
    unix_server.listen(1)
    try:
        report("synthetic Keychain fixture created", created)
        if created:
            program = f"""import subprocess
r = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', {service!r}, '-w'], capture_output=True)
assert r.returncode != 0
print('PROBE_OK')
"""
            programs.append(code("Keychain API synthetic item", program))

        urls = ["https://example.com", *args.denied_url]
        for index, url in enumerate(urls):
            program = f"""import subprocess
r = subprocess.run(['/usr/bin/curl', '--fail', '--silent', '--show-error', '--max-time', '8', '-o', '/dev/null', {url!r}], capture_output=True)
assert r.returncode == 22, r.returncode
print('PROBE_OK')
"""
            programs.append(code(f"network deny {index + 1}", program))

        program = """import socket, errno
for endpoint in [('127.0.0.1', 8090), ('127.0.0.1', 52415), ('1.1.1.1', 443)]:
    s = socket.socket()
    try:
        s.connect(endpoint)
    except OSError as e:
        assert e.errno in (errno.EPERM, errno.EACCES), e.errno
    else:
        raise SystemExit(1)
    finally:
        s.close()
print('PROBE_OK')
"""
        programs.append(code("direct TCP bypass", program))

        program = f"""import socket, errno
for family, operation in [(socket.AF_INET, 'bind'), (socket.AF_UNIX, 'connect')]:
    s = socket.socket(family)
    try:
        if operation == 'bind':
            s.bind(('127.0.0.1', 0))
        else:
            s.connect({str(socket_path)!r})
    except OSError as e:
        assert e.errno in (errno.EPERM, errno.EACCES), e.errno
    else:
        raise SystemExit(1)
    finally:
        s.close()
print('PROBE_OK')
"""
        programs.append(code("local binding and Unix sockets", program))

        code("relay authenticated model listing", f"""import json, urllib.request
provider = json.load(open({str(ZONE / 'pi-safe/models.json')!r}))['providers']['itpcp']
r = urllib.request.Request(provider['baseUrl'] + '/models', headers={{'Authorization': 'Bearer ' + provider['apiKey']}})
with urllib.request.urlopen(r, timeout=10) as response:
    data = json.load(response)
assert data.get('data')
print('PROBE_OK')
""")
        program = f"""from pathlib import Path
p = Path({str(probe)!r})
p.mkdir()
f = p / 'probe.txt'
f.write_text('synthetic safe-zone probe\\n')
assert f.read_text() == 'synthetic safe-zone probe\\n'
f.unlink()
p.rmdir()
print('PROBE_OK')
"""
        code("synthetic file write/read/delete", program)
        programs.append(program)

        if args.after_sudo:
            program = """import subprocess
r = subprocess.run(['/usr/bin/sudo', '-n', 'true'], capture_output=True)
assert r.returncode != 0
print('PROBE_OK')
"""
            programs.append(code("sandbox sudo denied", program))
            host = subprocess.run(["/usr/bin/sudo", "-n", "true"], capture_output=True)
            report("host sudo requires password", host.returncode != 0)

        if args.include_pi:
            result = subprocess.run(
                ["/usr/local/bin/safe", "pi", "-p", "Reply with exactly SAFE_ZONE_OK."],
                capture_output=True, text=True, timeout=180,
            )
            report("Pi one-line model answer", result.returncode == 0 and
                   result.stdout.strip() == "SAFE_ZONE_OK")
            # Ask the real consumer to run the same behavioral checks. A file
            # written only at the end distinguishes execution from a claimed pass.
            marker = state / ("probe-" + uuid.uuid4().hex)
            combined = "\n".join("exec(" + repr(p) + ")" for p in programs)
            combined += f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('passed')\n"
            command = shlex.join(["/usr/bin/python3", "-c", combined])
            result = subprocess.run([
                "/usr/local/bin/safe", "pi", "-p",
                "Use the bash tool to run exactly this synthetic sandbox test, then reply DONE: " + command,
            ], capture_output=True, text=True, timeout=240)
            report("Pi executes isolation probes", result.returncode == 0 and
                   marker.exists() and marker.read_text() == "passed")
            marker.unlink(missing_ok=True)
        else:
            print("SKIP Pi probes: model execution not requested", flush=True)
    finally:
        unix_server.close()
        socket_path.unlink(missing_ok=True)
        if created:
            deleted = subprocess.run([
                "/usr/bin/security", "delete-generic-password", "-s", service,
            ], capture_output=True, timeout=20)
            report("synthetic Keychain fixture deleted", deleted.returncode == 0)
        # Only the synthetic fixture names created by this test are removed.
        (probe / "probe.txt").unlink(missing_ok=True)
        if probe.exists():
            probe.rmdir()
        print(json.dumps({"failures": failures, "results": results}))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
