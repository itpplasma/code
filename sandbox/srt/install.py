#!/usr/bin/env python3
"""Install the macOS zone as root; never change sudo policy here."""
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import shlex
import subprocess
import tarfile
import urllib.request


def main():
    if os.geteuid() != 0 or os.uname().sysname != "Darwin":
        raise SystemExit("Run with sudo on macOS")
    user = os.environ.get("SUDO_USER")
    if not user or user == "root":
        raise SystemExit("SUDO_USER must identify the zone owner")
    owner = pwd.getpwnam(user)
    home = Path(owner.pw_dir)
    if any(c in str(home) + user for c in "'\n\r*?[]"):
        raise SystemExit("Unsupported home or account spelling")
    source = Path(__file__).resolve().parent
    prefix = Path("/opt/agents/srt")
    zone = Path("/Library/Application Support/agent-zones")
    for directory in (prefix, prefix / "bin", zone, zone / "pi-safe"):
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise SystemExit("Refusing symlink install directory")
        os.chown(directory, 0, 0)
        directory.chmod(0o755)
    subprocess.run([
        "/opt/homebrew/bin/npm", "install", "--prefix", str(prefix),
        "--ignore-scripts", "--no-audit", "--no-fund",
        "@anthropic-ai/sandbox-runtime@0.0.81",
    ], check=True)
    # Homebrew's node links user-writable dylibs. The official binary is
    # standalone, so srt starts entirely from the root-owned install.
    if os.uname().machine != "arm64":
        raise SystemExit("This installer targets Apple Silicon")
    release = "node-v24.22.2-darwin-arm64"
    base = "https://nodejs.org/dist/v24.22.2/"
    archive = urllib.request.urlopen(base + release + ".tar.gz").read()
    sums = urllib.request.urlopen(base + "SHASUMS256.txt").read().decode()
    expected = next(line.split()[0] for line in sums.splitlines()
                    if line.split()[-1] == release + ".tar.gz")
    if hashlib.sha256(archive).hexdigest() != expected:
        raise SystemExit("Node archive checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        (prefix / "bin/node").write_bytes(bundle.extractfile(release + "/bin/node").read())
    (prefix / "bin/node").chmod(0o755)
    for entry in [prefix, *prefix.rglob("*")]:
        os.chown(entry, 0, 0, follow_symlinks=False)
        if not entry.is_symlink():
            entry.chmod(0o755 if entry.is_dir() or os.access(entry, os.X_OK) else 0o644)

    def install(target, data, mode=0o644):
        if target.is_symlink():
            raise SystemExit("Refusing symlink target")
        target.write_text(data)
        os.chown(target, 0, 0)
        target.chmod(mode)

    def template(path):
        return path.read_text().replace("@HOME@", str(home)).replace("@USER@", user)

    install(zone / "safe.json", template(source / "safe.json"))
    for name in ("settings.json", "mcp.json"):
        install(zone / "pi-safe" / name, (source / "pi-safe" / name).read_text())
    # Parse only the provider record; never echo credentials or copy extensions.
    old = json.loads((home / ".pi/agent/models.json").read_text())["providers"]["itpcp"]
    key = old.get("apiKey", "")
    auth = home / ".pi/agent/auth.json"
    if auth.exists():
        saved = json.loads(auth.read_text()).get("itpcp", {})
        if saved.get("type") == "api_key":
            key = saved.get("key", key)
    if isinstance(key, str) and key.startswith("!"):
        parts = shlex.split(key[1:])
        if (len(parts) != 5 or parts[0] not in ("tr", "/usr/bin/tr")
                or parts[1] != "-d" or parts[2] not in ("\\n", "\\r\\n", "\n")
                or parts[3] != "<"):
            raise SystemExit("Unsupported key resolver; no commands were run")
        key_path = Path(parts[4].replace("~/", str(home) + "/", 1)).resolve()
        if not any(key_path.is_relative_to(home / d) for d in (".pi", ".config")):
            raise SystemExit("Key resolver must reference a local config file")
        key = key_path.read_text().replace("\n", "").replace("\r", "")
    if not isinstance(key, str) or not key or key.startswith(("!", "$")):
        raise SystemExit("A literal itpcp key is required")
    models = json.loads((source / "pi-safe/models.json").read_text())
    models["providers"]["itpcp"]["apiKey"] = key
    install(zone / "pi-safe/models.json", json.dumps(models, indent=2) + "\n")
    state = home / ".local/state/pi-safe"
    state.mkdir(parents=True, exist_ok=True)
    os.chown(state, owner.pw_uid, owner.pw_gid)
    state.chmod(0o700)
    Path("/usr/local/bin").mkdir(parents=True, exist_ok=True)
    install(Path("/usr/local/bin/safe"), template(source / "safe"), 0o755)
    subprocess.run([str(prefix / "bin/node"), str(prefix / "node_modules/@anthropic-ai/sandbox-runtime/dist/cli.js"), "--version"], check=True)
    print("Installed zone; sudo policy unchanged")


if __name__ == "__main__":
    main()
