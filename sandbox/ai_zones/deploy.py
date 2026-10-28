"""Reviewed bootstrap and fixed CORE administration for offline agent zones.

The bootstrap itself and its Python runtime require human review. A manifest
hash does not authenticate the program which is checking that hash. Ordinary
release signatures authorize payload copies only, never CORE or host policy.
"""
from __future__ import annotations

import argparse
import errno
import grp
import hashlib
import json
import os
import platform
import plistlib
import pwd
import re
import selectors
import socket
import stat
import subprocess
import sys
import time

from ai_zones import policy, release, runtime

ZONES = ("internal", "confidential")
PREFIXES = {"darwin": "/Library/AgentSandbox", "linux": "/opt/agent-sandbox"}
CORE_FILES = frozenset("ai_zones/" + name + ".py" for name in
                       ("deploy", "cli", "policy", "release", "runtime"))
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
RUNTIME_ID = "cpython-3.14.10-pbs-20261026-arm64"
RUNTIME_VERSION = "3.14.10"
RUNTIME_PROVENANCE = {
    "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/"
           "cpython-3.14.10%2B20261026-aarch64-apple-darwin-install_only_stripped.tar.gz",
    "release": "20261026", "commit": "fa506e0858ac9f2cc5b289c137b888a2aa24d1ee",
    "sha256": "95eb8c87d16af37c54e9765e31d8c56c0d8ffa9968ea0f704f73a9b83f84e9f9",
    "attestation_sha256": "6aa8363c0084b475c78f004f52cf66d17dabd200278464eb47b329b26633c4b2"}
GATES = ("trusted_runtime_missing", "service_profiles_pending",
         "vm_transport_integration_pending", "guest_boot_acceptance_pending",
         "host_isolation_acceptance_pending", "supported_os_build_pending", "policy_approval_pending",
         "filevault_encryption_acceptance_pending", "remote_recovery_acceptance_pending")
NAMES = {
    "internal": {"vm": "_aivmi", "broker": "_aibki", "ipc": "_aiipc_i"},
    "confidential": {"vm": "_aivmc", "broker": "_aibkc", "ipc": "_aiipc_c"},
}
HEX = re.compile(r"[0-9a-f]{64}\Z")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z")
MAX_JSON = 128 << 10


class DeployError(Exception):
    """Fail-closed refusal, with no implied deployment success."""


def prefix_for(platform_name=None):
    try:
        return PREFIXES[platform_name or sys.platform]
    except KeyError as exc:
        raise DeployError("unsupported platform") from exc


def runtime_python(prefix):
    return prefix + "/runtimes/" + RUNTIME_ID + "/bin/python3.14"


def _path(value):
    if (not isinstance(value, str) or not value.startswith("/") or
            value.startswith("//") or os.path.normpath(value) != value or
            len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise DeployError("expected normalized absolute path without controls")
    return value


def _sha(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise DeployError("expected lowercase SHA256")
    return value


def _exact(doc, keys, where):
    if not isinstance(doc, dict) or set(doc) != set(keys):
        raise DeployError(where + ": unexpected or missing keys")
    return doc


def _pairs(pairs):
    doc = {}
    for key, value in pairs:
        if key in doc:
            raise DeployError("duplicate JSON key")
        doc[key] = value
    return doc


def encoded(doc):
    return (json.dumps(doc, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _read(path, limit=MAX_JSON):
    return release._read_exact(_path(path), limit, "bootstrap input")


def _json(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              DeployError("nonfinite JSON")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DeployError("invalid bounded JSON") from exc


def _file_digest(path, limit=1 << 40):
    parent = release._open_absolute_dir(os.path.dirname(_path(path)))
    try:
        fd = os.open(os.path.basename(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=parent)
    finally:
        os.close(parent)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                not 0 < before.st_size <= limit):
            raise DeployError("artifact must be bounded nonempty single-link file")
        sha, size = hashlib.sha256(), 0
        while True:
            data = os.read(fd, 1 << 20)
            if not data:
                break
            size += len(data)
            if size > limit:
                raise DeployError("artifact exceeds running byte bound")
            sha.update(data)
        after = os.fstat(fd)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
             before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size,
                                    after.st_mtime_ns, after.st_ctime_ns) or size != before.st_size):
            raise DeployError("artifact changed while hashing")
        return {"path": path, "sha256": sha.hexdigest(), "size": size}
    finally:
        os.close(fd)


def checked_artifact(doc, limit=MAX_JSON):
    _exact(doc, ("path", "sha256"), "artifact")
    actual = _file_digest(_path(doc["path"]), limit)
    if actual["sha256"] != _sha(doc["sha256"]):
        raise DeployError("artifact digest mismatch")
    return actual


def core_inputs(manifest_path, expected):
    raw = _read(manifest_path)
    if digest(raw) != _sha(expected):
        raise DeployError("CORE manifest differs from independently reviewed hash")
    doc = _exact(_json(raw), ("version", "core_id", "files", "runtime"), "CORE manifest")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise DeployError("unsupported CORE manifest")
    if not isinstance(doc["core_id"], str) or not SAFE_ID.fullmatch(doc["core_id"]):
        raise DeployError("unsafe CORE identifier")
    _runtime_contract(doc["runtime"])
    _exact(doc["files"], CORE_FILES, "CORE import closure")
    retained = {}
    for name, item in sorted(doc["files"].items()):
        info = checked_artifact(item, 1 << 20)
        data = _read(info["path"], 1 << 20)
        if digest(data) != info["sha256"]:
            raise DeployError("CORE input changed")
        retained[name] = data
    # The executing bootstrap's dependency closure must be the reviewed one.
    for module, name in ((sys.modules[__name__], "deploy"), (policy, "policy"),
                         (release, "release"), (runtime, "runtime")):
        if digest(_read(os.path.abspath(module.__file__), 1 << 20)) != digest(
                retained["ai_zones/" + name + ".py"]):
            raise DeployError("executing bootstrap differs from reviewed CORE")
    return doc["core_id"], retained, raw, doc["runtime"]


def _runtime_contract(doc):
    _exact(doc, ("runtime_id", "python_version", "manifest_sha256", "provenance"), "CORE runtime contract")
    if (doc["runtime_id"] != RUNTIME_ID or doc["python_version"] != RUNTIME_VERSION or
            doc["provenance"] != RUNTIME_PROVENANCE):
        raise DeployError("CORE runtime contract differs from reviewed modern runtime")
    _sha(doc["manifest_sha256"])
    return doc


def _bound_runtime_manifest(doc, contract):
    _runtime_contract(contract)
    if (doc.get("runtime_id") != RUNTIME_ID or doc.get("python_version") != RUNTIME_VERSION or
            doc.get("provenance") != RUNTIME_PROVENANCE or doc.get("runtime_root") != "runtimes/" + RUNTIME_ID or
            doc.get("python") != "bin/python3.14" or
            digest(runtime.canonical_manifest(doc)) != contract["manifest_sha256"]):
        raise DeployError("signed runtime differs from independently approved CORE contract")
    return doc


def _verify_selected_runtime(prefix, contract):
    verifier = getattr(runtime, "verify_installed_runtime", None)
    if verifier is None:
        raise DeployError("installed runtime verifier interface is pending")
    selected = _exact(verifier(prefix), ("manifest", "runtime_root", "python"), "installed runtime result")
    _bound_runtime_manifest(selected["manifest"], contract)
    if (selected["runtime_root"] != prefix + "/runtimes/" + RUNTIME_ID or
            selected["python"] != runtime_python(prefix)):
        raise DeployError("installed runtime canonical paths differ from CORE")
    return selected


def _assert_distinct_runtime_authorities(runtime_key, tool_key):
    command = [release.OPENSSL, "rsa", "-pubin", "-pubout", "-outform", "DER"]
    if _run(command, input_data=runtime_key) == _run(command, input_data=tool_key):
        raise DeployError("runtime and ordinary tool signing authorities must differ")


def initial_runtime_argv(prefix, core_id, bundle):
    if prefix != PREFIXES["darwin"] or not isinstance(core_id, str) or not SAFE_ID.fullmatch(core_id):
        raise DeployError("invalid frozen CORE bootstrap target")
    return ["/usr/bin/python3", "-I", "-S", "-B", prefix + "/core/" + core_id + "/bootstrap.py",
            "install-runtime", _path(bundle)]


def _runtime_plan(inputs, prefix, contract, tool_public_key):
    key_path = getattr(inputs, "runtime_public_key", None)
    bundle = getattr(inputs, "runtime_bundle", None)
    if prefix == PREFIXES["linux"]:
        if key_path is not None or bundle is not None:
            raise DeployError("Mac runtime artifacts cannot provision the pending Linux adapter")
        return {"contract": None, "python": None, "runtime_root": None, "bundle": None,
                "public_key_sha256": None, "status": "linux_system_runtime_and_confinement_pending"}, None
    key = _read(key_path, 64 << 10) if key_path is not None else None
    if key is not None:
        _assert_distinct_runtime_authorities(key, tool_public_key)
    if bundle is not None:
        if key_path is None:
            raise DeployError("runtime bundle requires separate reviewed runtime public key")
        manifest = runtime.verify_runtime_bundle(_path(bundle), _path(key_path))
        _bound_runtime_manifest(manifest, contract)
    return {"contract": contract, "python": runtime_python(prefix),
            "runtime_root": prefix + "/runtimes/" + RUNTIME_ID,
            "bundle": _path(bundle) if bundle is not None else None,
            "public_key_sha256": digest(key) if key is not None else None,
            "status": "candidate_verified_root_installation_pending" if bundle is not None else
                      "missing_signed_runtime_bundle"}, key


def null_policy():
    doc = {"version": 1, "zones": {zone: {"cpus": 4, "memory_mib": 4096,
           "disk_gib": 64, "files": None, "model": None} for zone in ZONES}}
    policy.validate_policy(doc)
    return doc


def service_policy(full, zone):
    if zone not in ZONES:
        raise DeployError("unknown zone")
    policy.validate_policy(full)
    view = null_policy()
    view["zones"][zone] = dict(full["zones"][zone])
    policy.validate_policy(view)
    return view


def _run(argv, *, input_data=None):
    try:
        result = subprocess.run(argv, input=input_data, capture_output=True,
                                timeout=30, env=dict(ENV, OPENSSL_CONF="/dev/null"), cwd="/", check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeployError("fixed host command unavailable: " + argv[0]) from exc
    if result.returncode or len(result.stdout) > MAX_JSON or len(result.stderr) > MAX_JSON:
        raise DeployError("fixed host command failed: " + argv[0])
    return result.stdout


def inventory():
    users = sorted((entry.pw_name, entry.pw_uid, entry.pw_gid, entry.pw_dir,
                    entry.pw_shell) for entry in pwd.getpwall())
    groups = sorted((entry.gr_name, entry.gr_gid, sorted(entry.gr_mem))
                    for entry in grp.getgrall())
    return {"users": users, "groups": groups}


def account_plan(host_inventory):
    user_names = {entry[0] for entry in host_inventory["users"]}
    group_names = {entry[0] for entry in host_inventory["groups"]}
    fixed = {name for values in NAMES.values() for name in values.values()}
    if fixed & (user_names | group_names):
        raise DeployError("existing fixed service identity is not adopted")
    used = {entry[1] for entry in host_inventory["users"]} | {
            entry[1] for entry in host_inventory["groups"]}
    free = iter(number for number in range(600, 1000) if number not in used)
    result = {}
    try:
        for zone in ZONES:
            row = {}
            for role in ("vm", "broker", "ipc"):
                row[role] = {"name": NAMES[zone][role], "gid": next(free)}
                if role != "ipc":
                    row[role]["uid"] = row[role]["gid"]
            row["ipc"]["members"] = [row["vm"]["name"], row["broker"]["name"]]
            result[zone] = row
    except StopIteration as exc:
        raise DeployError("no bounded unused service IDs") from exc
    return result


def _require_unused_record(record):
    if record.startswith("/Users/"):
        lookup, name = pwd.getpwnam, record[len("/Users/"):]
    elif record.startswith("/Groups/"):
        lookup, name = grp.getgrnam, record[len("/Groups/"):]
    else:
        raise DeployError("invalid Directory Service record kind")
    try:
        lookup(name)
    except KeyError:
        return
    raise DeployError("existing identity or alias is not overwritten: " + record)


def host_facts(operator, runner=_run):
    platform_name = sys.platform
    if platform_name not in PREFIXES:
        raise DeployError("unsupported platform")
    if not isinstance(operator, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}", operator):
        raise DeployError("unsafe operator name")
    account = pwd.getpwnam(operator)
    if account.pw_uid == 0 or operator in {n for v in NAMES.values() for n in v.values()}:
        raise DeployError("operator must be a distinct non-root human identity")
    host_inventory = inventory()
    for names in NAMES.values():
        for name in names.values():
            _require_unused_record("/Users/" + name)
            _require_unused_record("/Groups/" + name)
    build = (runner(["/usr/bin/sw_vers", "-buildVersion"]).decode("ascii").strip()
             if platform_name == "darwin" else platform.release())
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,96}", build):
        raise DeployError("invalid host build")
    return {"platform": platform_name, "machine": platform.machine(), "os_build": build,
            "operator": {"name": operator, "uid": account.pw_uid,
                         "private_dir": _path(account.pw_dir + "/.ssh/agent-sandbox")},
            "inventory_sha256": digest(encoded(host_inventory)),
            "accounts": account_plan(host_inventory)}


def layout(prefix, accounts):
    entries = []
    def directory(path, uid=0, gid=0, mode=0o755):
        entries.append({"path": prefix + path, "uid": uid, "gid": gid,
                        "mode": format(mode, "04o"), "type": "directory"})
    for base in ("/state", "/run", "/config"):
        directory(base)
    for zone in ZONES:
        a = accounts[zone]
        for base in ("/state/", "/run/"):
            directory(base + zone, mode=0o711)
            directory(base + zone + "/vm", a["vm"]["uid"], a["vm"]["gid"], 0o700)
        directory("/run/" + zone + "/broker", a["broker"]["uid"], a["ipc"]["gid"], 0o710)
        directory("/state/" + zone + "/broker", a["broker"]["uid"], a["broker"]["gid"], 0o700)
        directory("/config/" + zone, 0, a["broker"]["gid"], 0o710)
    return entries


def socket_paths(prefix, zone):
    if zone not in ZONES:
        raise DeployError("unknown zone")
    return {"ssh": prefix + "/run/" + zone + "/vm/ssh.sock",
            "linux_ssh": prefix + "/run/" + zone + "/vm/host",
            "broker": prefix + "/run/" + zone + "/broker/broker.sock"}


def sudoers_text(prefix, operator):
    if prefix not in PREFIXES.values() or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}", operator):
        raise DeployError("invalid sudoers identity or prefix")
    commands = [prefix + "/bin/admin " + action + " " + zone
                for action in ("start", "status", "proxy") for zone in ZONES]
    return ("Defaults:" + operator + " env_reset\n" + operator +
            " ALL=(root) NOPASSWD:NOSETENV: " + ", ".join(commands) + "\n")


def admin_args(argv):
    if len(argv) != 2 or argv[0] not in ("start", "status", "proxy") or argv[1] not in ZONES:
        raise DeployError("admin requires exactly start|status|proxy internal|confidential")
    return argv[0], argv[1]


def _ssh_public(data):
    try:
        fields = data.decode("ascii").strip().split()
    except UnicodeError as exc:
        raise DeployError("invalid SSH public key") from exc
    if (len(fields) < 2 or fields[0] != "ssh-ed25519" or len(fields[1]) > 128 or
            not re.fullmatch(r"[A-Za-z0-9+/]+=*", fields[1])):
        raise DeployError("only bounded ed25519 SSH keys are staged")
    import base64
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except ValueError as exc:
        raise DeployError("invalid SSH key encoding") from exc
    if (len(blob) != 51 or blob[:4] != b"\0\0\0\x0b" or
            blob[4:15] != b"ssh-ed25519" or blob[15:19] != b"\0\0\0 "):
        raise DeployError("invalid SSH ed25519 key blob")
    return fields[0] + " " + fields[1]


def staging_inputs(path, runner=_run):
    raw = _read(path)
    doc = _exact(_json(raw), ("version", "zones"), "staging manifest")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise DeployError("unsupported staging manifest")
    _exact(doc["zones"], ZONES, "staged zones")
    result, retained = {}, {}
    fields = ("seed", "host_private_key", "host_public_key", "operator_public_key", "acceptance")
    for zone in ZONES:
        row = _exact(doc["zones"][zone], fields, "staged zone")
        result[zone], retained[zone] = {}, {}
        for name in fields:
            if name == "acceptance" and row[name] is None:
                result[zone][name] = None
                continue
            limit = 32 << 20 if name == "seed" else MAX_JSON
            info = checked_artifact(row[name], limit)
            result[zone][name] = info
            data = _read(info["path"], limit)
            if digest(data) != info["sha256"]:
                raise DeployError("staged artifact changed")
            retained[zone][name] = data
        derived = runner(["/usr/bin/ssh-keygen", "-y", "-P", "", "-f", result[zone]["host_private_key"]["path"]])
        if _ssh_public(derived) != _ssh_public(retained[zone]["host_public_key"]):
            raise DeployError("guest SSH host key pair mismatch")
        _ssh_public(retained[zone]["operator_public_key"])
    if (_ssh_public(retained[ZONES[0]]["operator_public_key"]) ==
            _ssh_public(retained[ZONES[1]]["operator_public_key"])):
        raise DeployError("operator SSH keys must be distinct per zone")
    return result, retained, raw


def acceptance_receipt(raw, expected):
    keys = ("version", "platform", "os_build", "release_sha256", "vfkit_sha256",
            "image_sha256", "seed_sha256", "profile_sha256", "zone", "suite_sha256", "passed")
    doc = _exact(_json(raw), keys, "acceptance receipt")
    if type(doc["version"]) is not int or doc["version"] != 1 or doc["passed"] is not True:
        raise DeployError("acceptance receipt is not a passing version 1 result")
    for name in ("release_sha256", "vfkit_sha256", "image_sha256", "seed_sha256",
                 "profile_sha256", "suite_sha256"):
        _sha(doc[name])
    for key, value in expected.items():
        if key not in doc or doc[key] != value:
            raise DeployError("acceptance artifact/host binding mismatch")
    return doc


def _vfkit(path, expected, platform_name, runner):
    info = _file_digest(path, 256 << 20)
    if info["sha256"] != _sha(expected):
        raise DeployError("vfkit digest mismatch")
    if platform_name == "darwin":
        runner(["/usr/bin/codesign", "--verify", "--strict", path])
        result = runner(["/usr/bin/codesign", "-d", "--entitlements", ":-", path])
        try:
            entitlements = plistlib.loads(result)
        except Exception as exc:
            raise DeployError("cannot verify virtualization entitlement") from exc
        if entitlements.get("com.apple.security.virtualization") is not True:
            raise DeployError("vfkit lacks virtualization entitlement")
    return info


def _descriptor(path, image_digest):
    raw = _read(path)
    fields = ("version", "distribution", "release", "architecture", "format", "sha256", "source_url")
    doc = _exact(_json(raw), fields, "image descriptor")
    if (type(doc["version"]) is not int or doc["version"] != 1 or doc["distribution"] != "debian" or
            doc["release"] != "13" or doc["architecture"] not in ("arm64", "amd64") or
            doc["format"] != "raw" or _sha(doc["sha256"]) != image_digest):
        raise DeployError("image descriptor does not match supported pinned Debian raw image")
    if (not isinstance(doc["source_url"], str) or not re.fullmatch(
            r"https://cloud\.debian\.org/images/cloud/trixie/20[0-9]{6}-[0-9]+/[A-Za-z0-9._-]+\.raw",
            doc["source_url"])):
        raise DeployError("image source must be an immutable official Debian snapshot")
    if doc["architecture"] not in doc["source_url"].rsplit("/", 1)[-1]:
        raise DeployError("image source filename architecture mismatch")
    return raw, doc


def build_plan(inputs, *, facts=None, runner=_run):
    """Read-only plan. Injected facts/runner are test APIs, never CLI options."""
    core_id, core, core_manifest, runtime_contract = core_inputs(inputs.core_manifest, inputs.expect_core_sha256)
    staged, retained, staging_manifest = staging_inputs(inputs.staging_manifest, runner)
    facts = facts if facts is not None else host_facts(inputs.operator, runner)
    if facts["operator"]["name"] != inputs.operator:
        raise DeployError("host operator mismatch")
    prefix = prefix_for(facts["platform"])
    public_key = _read(inputs.public_key, 64 << 10)
    # Retain the exact trust bytes used by the shared RSA verifier.
    release_id, payload = release._verified_bundle(_path(inputs.bundle), public_key)
    runtime_plan, runtime_key = _runtime_plan(inputs, prefix, runtime_contract, public_key)
    runtime_plan["bootstrap_argv"] = (initial_runtime_argv(prefix, core_id, runtime_plan["bundle"])
                                      if runtime_plan["bundle"] is not None else None)
    runtime_plan["bootstrap_approval"] = "Requires independently approved frozen CORE and human-reviewed system Python"
    image = _file_digest(inputs.image)
    descriptor_raw, descriptor = _descriptor(inputs.descriptor, image["sha256"])
    vfkit = _vfkit(inputs.vfkit, inputs.vfkit_sha256, facts["platform"], runner)
    if facts["platform"] == "darwin" and (facts["machine"] != "arm64" or descriptor["architecture"] != "arm64"):
        raise DeployError("Mac target requires Apple Silicon and arm64 image")
    parent = os.path.dirname(prefix)
    policy.assert_protected(parent, directory=True)
    if os.path.lexists(prefix):
        raise DeployError("installation target exists; bootstrap never overwrites it")
    release_digest = digest(_read(inputs.bundle + "/manifest.json", 1 << 20))
    for zone in ZONES:
        if staged[zone]["acceptance"] is not None:
            acceptance_receipt(retained[zone]["acceptance"], {
                "platform": facts["platform"], "os_build": facts["os_build"],
                "release_sha256": release_digest, "vfkit_sha256": vfkit["sha256"],
                "image_sha256": image["sha256"], "seed_sha256": staged[zone]["seed"]["sha256"],
                "zone": zone})
    plan = {"version": 1, "status": "offline_foundation_only", "prefix": prefix,
            "host": facts, "core_id": core_id, "core_manifest_sha256": digest(core_manifest),
            "core_files": {name: digest(data) for name, data in sorted(core.items())},
            "release": {"id": release_id, "manifest_sha256": release_digest,
                        "files": {name: digest(data) for name, data in sorted(payload.items())}},
            "tool_public_key_sha256": digest(public_key),
            "staging_manifest_sha256": digest(staging_manifest), "staged": staged,
            "image": image, "descriptor_sha256": digest(descriptor_raw), "vfkit": vfkit,
            "directories": layout(prefix, facts["accounts"]),
            "sockets": {zone: socket_paths(prefix, zone) for zone in ZONES},
            "policy": null_policy(), "runtime": runtime_plan,
            "activation_gates": list(GATES),
            "host_preparation": "fixed CORE prepare-host; no service loading or sudoers installation",
            "sudoers_candidate": sudoers_text(prefix, facts["operator"]["name"])}
    if facts["platform"] != "darwin":
        plan["activation_gates"].append("linux_adapter_runtime_untested")
    plan["plan_sha256"] = digest(encoded(plan))
    if len(encoded(plan)) > MAX_JSON:
        raise DeployError("deployment plan exceeds byte bound")
    retained_inputs = {"core": core, "core_manifest": core_manifest, "payload": payload,
                       "staged": retained, "descriptor": descriptor_raw,
                       "public_key": public_key, "runtime_public_key": runtime_key,
                       "staging_manifest": staging_manifest}
    return plan, retained_inputs


def _new_file(path, data, mode=0o444, uid=0, gid=0):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        release._write_all(fd, data, "foundation file")
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir(path, mode=0o755, uid=0, gid=0):
    os.mkdir(path, mode)
    os.chown(path, uid, gid)
    os.chmod(path, mode)
    policy.assert_no_mutating_acl(path)


def _copy_pinned(source, target, expected, uid=0, gid=0):
    parent = release._open_absolute_dir(os.path.dirname(_path(source)))
    try:
        source_fd = os.open(os.path.basename(source), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                            dir_fd=parent)
    finally:
        os.close(parent)
    target_fd = None
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise DeployError("copy source is not single-link regular file")
        target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        sha, size = hashlib.sha256(), 0
        while True:
            data = os.read(source_fd, 1 << 20)
            if not data:
                break
            size += len(data)
            if size > expected["size"]:
                raise DeployError("copy exceeds planned size")
            sha.update(data)
            release._write_all(target_fd, data, "pinned copy")
        after = os.fstat(source_fd)
        if (size != expected["size"] or sha.hexdigest() != expected["sha256"] or
                (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)):
            raise DeployError("copy differs from pinned artifact")
        os.fchown(target_fd, uid, gid)
        os.fchmod(target_fd, 0o600)
        os.fsync(target_fd)
    finally:
        os.close(source_fd)
        if target_fd is not None:
            os.close(target_fd)


def _bootstrap_text(prefix, core_id):
    core_root = prefix + "/core/" + core_id
    # Only independently pinned CORE is on the application import path.
    return ("import os,sys\n"
            "os.environ.clear();os.environ.update(PATH='/usr/bin:/bin',LANG='C',LC_ALL='C')\n"
            "os.chdir('/')\n"
            "sys.path.insert(0," + repr(core_root) + ")\n"
            "from ai_zones.deploy import installed_main\n"
            "raise SystemExit(installed_main(sys.argv[1:]))\n").encode("ascii")


def _admin_launcher(prefix, core_id):
    return ("#!/bin/sh\nexec /usr/bin/env -i PATH=/usr/bin:/bin LANG=C LC_ALL=C " +
            runtime_python(prefix) + " -I -S -B " + prefix + "/core/" + core_id +
            "/bootstrap.py admin \"$@\"\n").encode("ascii")


def stage_foundation(inputs, expected_plan):
    if os.geteuid() != 0:
        raise DeployError("stage requires real root invocation")
    plan, retained = build_plan(inputs)
    if plan["plan_sha256"] != _sha(expected_plan):
        raise DeployError("stale plan; no foundation files written")
    if plan["host"]["platform"] != "darwin":
        raise DeployError("Linux emits adapter plan only until runtime acceptance")
    prefix, core_id = plan["prefix"], plan["core_id"]
    # The target must be absent and its complete parent chain protected.
    policy.assert_protected(os.path.dirname(prefix), directory=True)
    _mkdir(prefix, 0o755)
    # Partial stages remain identifiable and are never silently overwritten.
    try:
        for name, mode in (("management", 0o700), ("trust", 0o700), ("core", 0o755),
                           ("runtime", 0o755), ("releases", 0o755), ("bin", 0o755),
                           ("config", 0o755), ("pins", 0o755)):
            _mkdir(prefix + "/" + name, mode)
        _new_file(prefix + "/management/INSTALLATION", b"agent-sandbox-foundation-v1\n", 0o600)
        _new_file(prefix + "/management/plan.json", encoded(plan), 0o600)
        _new_file(prefix + "/management/policy.json", encoded(null_policy()), 0o600)
        _new_file(prefix + "/management/core-manifest.json", retained["core_manifest"], 0o600)
        _new_file(prefix + "/trust/release.pub", retained["public_key"], 0o400)
        if retained["runtime_public_key"] is not None:
            _new_file(prefix + "/trust/runtime.pub", retained["runtime_public_key"], 0o400)
        _new_file(prefix + "/management/staging-manifest.json", retained["staging_manifest"], 0o600)
        _new_file(prefix + "/management/sudoers.candidate", plan["sudoers_candidate"].encode("ascii"), 0o400)
        root = prefix + "/core/" + core_id
        _mkdir(root)
        _mkdir(root + "/ai_zones")
        _new_file(root + "/ai_zones/__init__.py", b"")
        for name, data in retained["core"].items():
            _new_file(root + "/" + name, data)
        _new_file(root + "/bootstrap.py", _bootstrap_text(prefix, core_id))
        _new_file(prefix + "/bin/admin", _admin_launcher(prefix, core_id), 0o555)
        _new_file(prefix + "/bin/ai", ("#!/bin/sh\nexec /usr/bin/env -i PATH=/usr/bin:/bin LANG=C LC_ALL=C " +
                  runtime_python(prefix) + " -I -S -B " + root +
                  "/bootstrap.py cli \"$@\"\n").encode("ascii"), 0o555)
        # Use the shared verifier's retained-byte installer; no payload import.
        prefix_fd = release._open_absolute_dir(prefix)
        releases_fd = release._open_absolute_dir(prefix + "/releases")
        try:
            release._install_verified(prefix_fd, releases_fd, plan["release"]["id"], retained["payload"])
        finally:
            os.close(releases_fd)
            os.close(prefix_fd)
        _mkdir(prefix + "/management/artifacts", 0o700)
        _copy_pinned(plan["image"]["path"], prefix + "/management/artifacts/base.raw", plan["image"])
        _copy_pinned(plan["vfkit"]["path"], prefix + "/runtime/vfkit", plan["vfkit"])
        os.chmod(prefix + "/runtime/vfkit", 0o555)
        # Verify the protected retained executable too; mutable input paths do
        # not establish the installed binary's code signature.
        _vfkit(prefix + "/runtime/vfkit", plan["vfkit"]["sha256"], "darwin", _run)
        _new_file(prefix + "/management/artifacts/image-descriptor.json", retained["descriptor"], 0o400)
        for zone in ZONES:
            _mkdir(prefix + "/config/" + zone, 0o700)
            _new_file(prefix + "/config/" + zone + "/service-policy.json", encoded(service_policy(null_policy(), zone)), 0o400)
            _mkdir(prefix + "/management/artifacts/" + zone, 0o700)
            for name, data in retained["staged"][zone].items():
                _new_file(prefix + "/management/artifacts/" + zone + "/" + name, data, 0o600)
            derived = _run(["/usr/bin/ssh-keygen", "-y", "-P", "", "-f",
                            prefix + "/management/artifacts/" + zone + "/host_private_key"])
            if _ssh_public(derived) != _ssh_public(retained["staged"][zone]["host_public_key"]):
                raise DeployError("protected staged guest host key pair mismatch")
            pin = zone + " " + _ssh_public(retained["staged"][zone]["host_public_key"]) + "\n"
            _new_file(prefix + "/pins/" + zone + ".known_hosts", pin.encode("ascii"), 0o444)
        public = {"version": 1, "operator": plan["host"]["operator"], "zones": list(ZONES)}
        _new_file(prefix + "/config/operator.json", encoded(public), 0o444)
        _new_file(prefix + "/management/STAGED", b"offline; activation denied\n", 0o400)
    except BaseException:
        # Retain the explicit managed partial stage for review, never erase unknown state.
        raise
    return {"status": "staged_offline", "plan_sha256": plan["plan_sha256"],
            "activation_gates": plan["activation_gates"], "accounts_created": False,
            "services_loaded": False, "runtime_ready": False}


def _load_plan(prefix):
    policy.assert_protected(prefix + "/management/INSTALLATION")
    if _read(prefix + "/management/INSTALLATION") != b"agent-sandbox-foundation-v1\n":
        raise DeployError("unrecognized installation")
    policy.assert_protected(prefix + "/management/plan.json")
    plan = _json(_read(prefix + "/management/plan.json"))
    expected = plan.pop("plan_sha256", None)
    if expected != digest(encoded(plan)):
        raise DeployError("installed plan digest mismatch")
    plan["plan_sha256"] = expected
    if plan.get("prefix") != prefix or plan.get("host", {}).get("platform") != sys.platform:
        raise DeployError("installed plan belongs to another host platform")
    return plan


def _assert_core_sources(prefix, plan):
    core = prefix + "/core/" + plan["core_id"]
    for name, expected in plan["core_files"].items():
        if name not in CORE_FILES:
            raise DeployError("invalid installed CORE closure")
        path = core + "/" + name
        policy.assert_protected(path)
        if digest(_read(path, 1 << 20)) != expected:
            raise DeployError("CORE source digest mismatch")
    if set(plan["core_files"]) != CORE_FILES:
        raise DeployError("incomplete CORE closure")
    if os.path.abspath(__file__) != core + "/ai_zones/deploy.py":
        raise DeployError("installed operation requires fixed protected CORE")
    package_path = core + "/ai_zones/__init__.py"
    policy.assert_protected(package_path)
    if _read(package_path) != b"":
        raise DeployError("fixed CORE package initializer changed")
    manifest_path = prefix + "/management/core-manifest.json"
    policy.assert_protected(manifest_path)
    raw = _read(manifest_path)
    if digest(raw) != plan["core_manifest_sha256"]:
        raise DeployError("independent CORE approval manifest changed")
    declared = _json(raw)
    if declared.get("runtime") != plan["runtime"]["contract"]:
        raise DeployError("runtime contract differs from independently approved CORE")
    contract = _runtime_contract(declared["runtime"])
    for module, name in ((policy, "policy"), (release, "release"), (runtime, "runtime")):
        if os.path.abspath(module.__file__) != core + "/ai_zones/" + name + ".py":
            raise DeployError("loaded dependency is outside approved frozen CORE")
    policy.assert_protected(core + "/bootstrap.py")
    if _read(core + "/bootstrap.py") != _bootstrap_text(prefix, plan["core_id"]):
        raise DeployError("CORE dispatcher changed")
    return contract


def _assert_core(prefix, plan):
    contract = _assert_core_sources(prefix, plan)
    _verify_selected_runtime(prefix, contract)
    core = prefix + "/core/" + plan["core_id"]
    expected_python = runtime_python(prefix)
    if os.path.abspath(sys.executable) != expected_python:
        raise DeployError("installed root operation requires protected dedicated runtime")
    policy.assert_protected(expected_python)
    for import_path in sys.path:
        if not import_path or not (import_path == core or import_path.startswith(core + "/") or
                                   import_path.startswith(prefix + "/runtimes/" + RUNTIME_ID + "/")):
            raise DeployError("unexpected installed interpreter import path")
        if os.path.lexists(import_path):
            policy.assert_protected(import_path, directory=os.path.isdir(import_path))


def account_commands(prefix, accounts):
    """Exact Darwin Directory Service operations; no caller-provided names."""
    commands = []
    for zone in ZONES:
        row = accounts[zone]
        for role in ("vm", "broker", "ipc"):
            identity = row[role]
            if identity["name"] != NAMES[zone][role] or type(identity["gid"]) is not int or not 600 <= identity["gid"] < 1000:
                raise DeployError("invalid fixed account record")
            group = "/Groups/" + identity["name"]
            commands.extend([["/usr/bin/dscl", ".", "-create", group],
                             ["/usr/bin/dscl", ".", "-create", group, "PrimaryGroupID", str(identity["gid"])],
                             ["/usr/bin/dscl", ".", "-create", group, "Password", "*"]])
            if role != "ipc":
                if identity.get("uid") != identity["gid"]:
                    raise DeployError("invalid private primary identity")
                user = "/Users/" + identity["name"]
                commands.append(["/usr/bin/dscl", ".", "-create", user])
                for key, value in (("Password", "*"), ("IsHidden", "1"), ("UserShell", "/usr/bin/false"),
                                   ("NFSHomeDirectory", prefix + "/state/" + zone + "/" + role),
                                   ("PrimaryGroupID", str(identity["gid"])), ("UniqueID", str(identity["uid"]))):
                    commands.append(["/usr/bin/dscl", ".", "-create", user, key, value])
        ipc = "/Groups/" + row["ipc"]["name"]
        if row["ipc"].get("members") != [row["vm"]["name"], row["broker"]["name"]]:
            raise DeployError("invalid IPC membership")
        commands.append(["/usr/bin/dscl", ".", "-create", ipc, "GroupMembership",
                         row["vm"]["name"], row["broker"]["name"]])
    return commands


def _verify_accounts(prefix, accounts):
    for zone in ZONES:
        row = accounts[zone]
        for role in ("vm", "broker"):
            expected = row[role]
            actual = pwd.getpwnam(expected["name"])
            if ((actual.pw_uid, actual.pw_gid, actual.pw_shell, actual.pw_dir) !=
                    (expected["uid"], expected["gid"], "/usr/bin/false", prefix + "/state/" + zone + "/" + role)):
                raise DeployError("created service account properties mismatch")
            allowed = {expected["gid"]}
            if role in ("vm", "broker"):
                allowed.add(row["ipc"]["gid"])
            if set(os.getgrouplist(expected["name"], expected["gid"])) != allowed:
                raise DeployError("unexpected supplementary service group")
        for role in ("vm", "broker", "ipc"):
            group = grp.getgrnam(row[role]["name"])
            members = row[role].get("members", [])
            if group.gr_gid != row[role]["gid"] or sorted(group.gr_mem) != sorted(members):
                raise DeployError("created group membership mismatch")


def prepare_host():
    """Root-only installed CORE action; not reachable through the sudoers API."""
    if os.geteuid() != 0:
        raise DeployError("host preparation requires root")
    prefix = prefix_for()
    plan = _load_plan(prefix)
    _assert_core(prefix, plan)
    if sys.platform != "darwin":
        raise DeployError("Linux host preparation remains plan-only")
    policy.assert_protected("/usr/bin/dscl")
    if os.path.lexists(prefix + "/management/PREPARED"):
        raise DeployError("host already prepared; no account replacement")
    if digest(encoded(inventory())) != plan["host"]["inventory_sha256"]:
        raise DeployError("host identity inventory changed; no account writes")
    accounts = plan["host"]["accounts"]
    commands = account_commands(prefix, accounts)
    for entry in plan["directories"]:
        if not entry["path"].startswith(prefix + "/config") and os.path.lexists(entry["path"]):
            raise DeployError("unexpected host state path before account preparation")
    # Refuse altered foundation material before creating even the first identity.
    management_policy = policy.load_policy(prefix + "/management/policy.json")
    if management_policy != policy.validate_policy(null_policy()):
        raise DeployError("host preparation requires null-capability policy")
    checks = [(prefix + "/management/artifacts/base.raw", plan["image"]),
              (prefix + "/runtime/vfkit", plan["vfkit"])]
    for zone in ZONES:
        service = prefix + "/config/" + zone + "/service-policy.json"
        if policy.load_policy(service) != policy.validate_policy(null_policy()):
            raise DeployError("host preparation requires null service policy")
        for name, info in plan["staged"][zone].items():
            if info is not None:
                checks.append((prefix + "/management/artifacts/" + zone + "/" + name, info))
    for artifact, expected in checks:
        policy.assert_protected(artifact)
        actual = _file_digest(artifact)
        if (actual["sha256"], actual["size"]) != (expected["sha256"], expected["size"]):
            raise DeployError("staged artifact changed before host preparation")
    lock = prefix + "/management/prepare.lock"
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        if digest(encoded(inventory())) != plan["host"]["inventory_sha256"]:
            raise DeployError("host identity inventory changed under preparation lock")
        # Keep the journal on failure: partially created identities are not adopted.
        for number, command in enumerate(commands):
            if len(command) == 4:
                _require_unused_record(command[3])
            release._write_all(fd, encoded({"operation": number, "state": "before", "argv": command}), "prepare journal")
            os.fsync(fd)
            _run(command)
            release._write_all(fd, encoded({"operation": number, "state": "done"}), "prepare journal")
            os.fsync(fd)
        _verify_accounts(prefix, accounts)
        for entry in plan["directories"]:
            if entry["path"].startswith(prefix + "/config"):
                continue
            _mkdir(entry["path"], int(entry["mode"], 8), entry["uid"], entry["gid"])
        for zone in ZONES:
            row = accounts[zone]
            config = prefix + "/config/" + zone
            policy.assert_protected(config, directory=True)
            os.chown(config, 0, row["broker"]["gid"])
            os.chmod(config, 0o710)
            service = config + "/service-policy.json"
            policy.assert_protected(service)
            os.chown(service, 0, row["broker"]["gid"])
            os.chmod(service, 0o640)
            base = prefix + "/state/" + zone + "/vm/"
            source = prefix + "/management/artifacts/base.raw"
            _copy_pinned(source, base + "root.raw", plan["image"], row["vm"]["uid"], row["vm"]["gid"])
            for artifact, name in (("seed", "seed.img"), ("host_private_key", "ssh_host_ed25519")):
                source = prefix + "/management/artifacts/" + zone + "/" + artifact
                _copy_pinned(source, base + name, plan["staged"][zone][artifact], row["vm"]["uid"], row["vm"]["gid"])
        _new_file(prefix + "/management/PREPARED", encoded({"plan_sha256": plan["plan_sha256"]}), 0o400)
        return {"status": "prepared_offline", "services_loaded": False, "activation_gates": plan["activation_gates"]}
    finally:
        os.close(fd)
        # A completed journal is retained too; never retry an ambiguous preparation.


def connected_peer_identity(sock):
    """Authenticate the connected Unix peer before any application bytes."""
    if sys.platform == "darwin":
        import ctypes
        native = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        function = native.getpeereid
        function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
                             ctypes.POINTER(ctypes.c_uint)]
        function.restype = ctypes.c_int
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        if function(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise DeployError("cannot authenticate Darwin Unix socket peer")
        return uid.value, gid.value
    if sys.platform == "linux":
        import struct
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                              struct.calcsize("3i"))
        _, uid, gid = struct.unpack("3i", raw)
        return uid, gid
    raise DeployError("Unix peer credential API unavailable")


def _kernel_process_groups():
    """Read credentials without Darwin's directory-membership getgroups alias."""
    if sys.platform == "linux":
        return os.getgroups()
    if sys.platform != "darwin":
        raise DeployError("native process group query unavailable")
    import ctypes
    native = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    getgroups = native.getgroups
    getgroups.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    getgroups.restype = ctypes.c_int
    count = getgroups(0, None)
    if not 0 <= count <= 1024:
        raise DeployError("cannot read bounded Darwin kernel process groups")
    if count == 0:
        return []
    groups = (ctypes.c_uint * count)()
    if getgroups(count, groups) != count:
        raise DeployError("Darwin kernel process groups changed or query failed")
    return list(groups)


def _drop_proxy_identity(vm, *, syscalls=os, _group_reader=_kernel_process_groups):
    """Discard root, saved IDs and inherited descriptors before connecting."""
    uid, gid = vm.get("uid"), vm.get("gid")
    if (type(uid) is not int or type(gid) is not int or
            not 600 <= uid < 1000 or uid != gid):
        raise DeployError("invalid fixed VM proxy identity")
    try:
        # This one-shot process needs only its already-connected standard streams.
        descriptor_dir = "/dev/fd" if sys.platform == "darwin" else "/proc/self/fd"
        descriptors = syscalls.listdir(descriptor_dir)
        if len(descriptors) > 65536 or any(not name.isdigit() for name in descriptors):
            raise DeployError("cannot bound inherited proxy descriptors")
        for name in descriptors:
            fd = int(name)
            if fd > 2:
                try:
                    syscalls.close(fd)
                except OSError as exc:
                    # Directory enumeration may include its own closed descriptor.
                    if exc.errno != errno.EBADF:
                        raise
        syscalls.setgroups([])
        syscalls.setgid(gid)
        # As root, setuid sets real, effective and saved user IDs permanently.
        syscalls.setuid(uid)
        # Darwin's kernel list includes the effective primary group itself.
        expected_groups = [gid] if sys.platform == "darwin" else []
        if ((syscalls.getuid(), syscalls.geteuid()) != (uid, uid) or
                (syscalls.getgid(), syscalls.getegid()) != (gid, gid) or
                _group_reader() != expected_groups):
            raise DeployError("proxy identity drop did not remove root authority")
        if hasattr(syscalls, "getresuid") and syscalls.getresuid() != (uid, uid, uid):
            raise DeployError("proxy retained a saved user identity")
        if hasattr(syscalls, "getresgid") and syscalls.getresgid() != (gid, gid, gid):
            raise DeployError("proxy retained a saved group identity")
    except OSError as exc:
        raise DeployError("permanent proxy privilege drop failed") from exc


def proxy_connect(prefix, zone, accounts, platform_name=None, *,
                  _drop_identity=_drop_proxy_identity):
    platform_name = platform_name or sys.platform
    paths = socket_paths(prefix, zone)
    path = paths["ssh"] if platform_name == "darwin" else paths["linux_ssh"]
    parent = os.path.dirname(path)
    parent_st = os.lstat(parent)
    vm = accounts[zone]["vm"]
    if (not stat.S_ISDIR(parent_st.st_mode) or stat.S_IMODE(parent_st.st_mode) != 0o700 or
            parent_st.st_uid != vm["uid"] or parent_st.st_gid != vm["gid"]):
        raise DeployError("SSH runtime directory owner/mode mismatch")
    policy.assert_protected(os.path.dirname(parent), directory=True)
    policy.assert_no_mutating_acl(parent)
    before = os.lstat(path)
    if (not stat.S_ISSOCK(before.st_mode) or before.st_uid != vm["uid"] or
            before.st_gid != vm["gid"] or stat.S_IMODE(before.st_mode) & 0o077):
        raise DeployError("SSH socket owner/type/mode mismatch")
    policy.assert_no_mutating_acl(path)
    _drop_identity(vm)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(10)
        sock.connect(path)
        if connected_peer_identity(sock) != (vm["uid"], vm["gid"]):
            raise DeployError("SSH socket connected peer identity mismatch")
        after = os.lstat(path)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise DeployError("SSH socket changed during connect")
        if platform_name == "linux":
            sock.sendall(b"CONNECT 2222\n")
            line = bytearray()
            while len(line) <= 32 and not line.endswith(b"\n"):
                chunk = sock.recv(1)
                if not chunk:
                    break
                line.extend(chunk)
            if bytes(line) != b"OK 2222\n":
                raise DeployError("Linux SSH hybrid handshake mismatch")
        elif platform_name != "darwin":
            raise DeployError("unsupported SSH transport")
        sock.settimeout(None)
        return sock
    except BaseException:
        sock.close()
        raise


def relay_stdio(sock, input_fd=0, output_fd=1):
    """Bounded duplex relay with half-close, backpressure and idle timeout."""
    selector = selectors.DefaultSelector()
    output, inbound = bytearray(), bytearray()
    stdin_open = socket_open = True
    original = {fd: os.get_blocking(fd) for fd in (input_fd, output_fd)}
    try:
        for fd in original:
            os.set_blocking(fd, False)
        sock.setblocking(False)
        last = time.monotonic()
        while stdin_open or socket_open or inbound or output:
            for key in list(selector.get_map().values()):
                selector.unregister(key.fileobj)
            if stdin_open and len(output) < 65536:
                selector.register(input_fd, selectors.EVENT_READ, "stdin")
            events = (selectors.EVENT_READ if socket_open and len(inbound) < 65536 else 0)
            if output:
                events |= selectors.EVENT_WRITE
            if events:
                selector.register(sock, events, "socket")
            if inbound:
                selector.register(output_fd, selectors.EVENT_WRITE, "stdout")
            if not selector.get_map():
                break
            ready = selector.select(1)
            if time.monotonic() - last > 300:
                raise DeployError("SSH proxy idle timeout")
            for key, mask in ready:
                try:
                    if key.data == "stdin":
                        data = os.read(input_fd, min(16384, 65536 - len(output)))
                        if data:
                            output.extend(data)
                        else:
                            stdin_open = False
                            if not output:
                                sock.shutdown(socket.SHUT_WR)
                    elif key.data == "stdout":
                        count = os.write(output_fd, inbound)
                        del inbound[:count]
                    else:
                        if mask & selectors.EVENT_READ:
                            data = sock.recv(min(16384, 65536 - len(inbound)))
                            if data:
                                inbound.extend(data)
                            else:
                                socket_open = False
                        if mask & selectors.EVENT_WRITE:
                            count = sock.send(output)
                            del output[:count]
                            if not output and not stdin_open:
                                sock.shutdown(socket.SHUT_WR)
                    last = time.monotonic()
                except BlockingIOError:
                    continue
    finally:
        selector.close()
        for fd, blocking in original.items():
            os.set_blocking(fd, blocking)


def installed_admin(argv):
    action, zone = admin_args(argv)
    if os.geteuid() != 0:
        raise DeployError("admin requires root through exact sudoers command")
    prefix = prefix_for()
    plan = _load_plan(prefix)
    _assert_core(prefix, plan)
    if action == "status":
        print(json.dumps({"zone": zone, "state": "offline_foundation",
                          "prepared": os.path.exists(prefix + "/management/PREPARED"),
                          "activation_gates": plan["activation_gates"]}, sort_keys=True))
        return 0
    if action == "start":
        # This CORE generation stages/prepares only. A new independently reviewed
        # generation must integrate verified runtime/profiles before activation.
        raise DeployError("activation refused: " + ", ".join(plan["activation_gates"]))
    prepared = prefix + "/management/PREPARED"
    policy.assert_protected(prepared)
    if _json(_read(prepared)) != {"plan_sha256": plan["plan_sha256"]}:
        raise DeployError("SSH proxy requires matching prepared host identities")
    _verify_accounts(prefix, plan["host"]["accounts"])
    public = prefix + "/management/artifacts/" + zone + "/host_public_key"
    pin = prefix + "/pins/" + zone + ".known_hosts"
    policy.assert_protected(public)
    policy.assert_protected(pin)
    public_bytes = _read(public)
    if digest(public_bytes) != plan["staged"][zone]["host_public_key"]["sha256"]:
        raise DeployError("staged SSH host public key changed")
    if _read(pin) != (zone + " " + _ssh_public(public_bytes) + "\n").encode("ascii"):
        raise DeployError("SSH host pin differs from prepared zone key")
    with proxy_connect(prefix, zone, plan["host"]["accounts"]) as sock:
        relay_stdio(sock)
    return 0


def install_initial_runtime(bundle):
    """Human-reviewed system Python may perform only this initial CORE action."""
    if os.geteuid() != 0:
        raise DeployError("initial runtime installation requires root")
    if sys.platform != "darwin":
        raise DeployError("initial runtime bootstrap is supported only on macOS")
    bundle = _path(bundle)
    prefix = prefix_for()
    plan = _load_plan(prefix)
    contract = _assert_core_sources(prefix, plan)
    if (os.path.lexists(prefix + "/runtime-current") or
            os.path.lexists(prefix + "/runtimes/" + RUNTIME_ID)):
        raise DeployError("initial runtime bootstrap refuses an existing runtime")
    key_path, tool_key_path = prefix + "/trust/runtime.pub", prefix + "/trust/release.pub"
    policy.assert_protected(key_path)
    policy.assert_protected(tool_key_path)
    key, tool_key = _read(key_path, 64 << 10), _read(tool_key_path, 64 << 10)
    if digest(key) != plan["runtime"]["public_key_sha256"]:
        raise DeployError("runtime trust key differs from reviewed stage plan")
    _assert_distinct_runtime_authorities(key, tool_key)
    _bound_runtime_manifest(runtime.verify_runtime_bundle(bundle, key_path), contract)
    installed = runtime.install_runtime(bundle, prefix, key_path,
        expected_manifest_sha256=contract["manifest_sha256"])
    if installed != prefix + "/runtimes/" + RUNTIME_ID:
        raise DeployError("runtime installer returned an unexpected root")
    selected = _verify_selected_runtime(prefix, contract)
    return {"status": "runtime_installed_activation_pending", "python": selected["python"],
            "activation_gates": plan["activation_gates"]}


def install_tool_bundle(bundle):
    """Ordinary signatures can replace only allowlisted release payload bytes."""
    if os.geteuid() != 0:
        raise DeployError("tool update requires root; no operator sudo permission")
    prefix = prefix_for()
    plan = _load_plan(prefix)
    _assert_core(prefix, plan)
    return release.install_bundle(_path(bundle), prefix, prefix + "/trust/release.pub")


def installed_main(argv):
    os.environ.clear()
    os.environ.update(ENV)
    os.chdir("/")
    try:
        if argv and argv[0] == "admin":
            return installed_admin(argv[1:])
        if argv == ["prepare-host"]:
            print(json.dumps(prepare_host(), sort_keys=True))
            return 0
        if len(argv) == 2 and argv[0] == "install-runtime":
            print(json.dumps(install_initial_runtime(argv[1]), sort_keys=True))
            return 0
        if len(argv) == 2 and argv[0] == "update-tool":
            print(json.dumps({"release": install_tool_bundle(argv[1])}, sort_keys=True))
            return 0
        if argv and argv[0] == "cli":
            from ai_zones.cli import main as cli_main
            return cli_main(argv[1:])
        raise DeployError("unknown installed CORE action")
    except (DeployError, policy.PolicyError, release.ReleaseError, runtime.RuntimeError, OSError, KeyError) as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 1


class StrictParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        raw = list(sys.argv[1:] if args is None else args)
        options = [word.split("=", 1)[0] for word in raw if word.startswith("--")]
        if len(options) != len(set(options)):
            self.error("duplicate option")
        return super().parse_args(raw, namespace)


def parser():
    p = StrictParser(description=__doc__, allow_abbrev=False)
    actions = p.add_mutually_exclusive_group(required=True)
    for name in ("plan", "stage", "apply", "activate"):
        actions.add_argument("--" + name, action="store_true")
    for name in ("bundle", "public-key", "image", "descriptor", "vfkit", "vfkit-sha256",
                 "operator", "core-manifest", "expect-core-sha256", "staging-manifest"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--expect-plan-sha256")
    p.add_argument("--runtime-public-key")
    p.add_argument("--runtime-bundle")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.plan:
            plan, _ = build_plan(args)
            print(json.dumps(plan, sort_keys=True, separators=(",", ":")))
            return 0
        if args.apply or args.activate:
            # Do not create even a lock before this generation's gates pass.
            raise DeployError("activation unavailable in this reviewed foundation generation: " + ", ".join(GATES))
        if args.expect_plan_sha256 is None:
            raise DeployError("stage requires explicit reviewed --expect-plan-sha256")
        print(json.dumps(stage_foundation(args, args.expect_plan_sha256), sort_keys=True))
        return 0
    except (DeployError, policy.PolicyError, release.ReleaseError, runtime.RuntimeError, OSError, KeyError) as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
