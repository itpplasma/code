#!/usr/bin/env bash
# Behavioral checks; --memory additionally exercises the real user cgroup.
# shellcheck disable=SC2016 # bash -c payloads expand in the child shell.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="$repo_root/scripts/heavy-run"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export HEAVY_RUN_LOCK="$tmp/heavy.lock"

"$runner" --help >/dev/null
env -u OMP_NUM_THREADS -u OPENBLAS_NUM_THREADS "$runner" bash -c \
    '[[ $OMP_NUM_THREADS == 1 && $OPENBLAS_NUM_THREADS == 1 ]]'
OMP_NUM_THREADS=3 OPENBLAS_NUM_THREADS='' "$runner" bash -c \
    '[[ $OMP_NUM_THREADS == 3 && ${OPENBLAS_NUM_THREADS+x} == x && -z $OPENBLAS_NUM_THREADS ]]'
status=0
"$runner" bash -c 'exit 37' || status=$?
[[ $status -eq 37 ]]
# Preserve streams, working directory, environment, and literal argument text.
printf 'input\n' | HEAVY_RUN_TEST_VALUE='value with spaces' "$runner" bash -c '
    set -e
    read -r line
    [[ $line == input && $PWD == "$1" && $HEAVY_RUN_TEST_VALUE == "value with spaces" ]]
    [[ $2 == "\$literal with spaces" ]]
    printf output
    printf error >&2
' heavy-run-test "$PWD" '$literal with spaces' >"$tmp/out" 2>"$tmp/err"
[[ $(cat "$tmp/out") == output && $(cat "$tmp/err") == error ]]
status=0
"$runner" bash -c 'kill -KILL $$' >"$tmp/out" 2>"$tmp/err" || status=$?
[[ $status -eq 137 ]]
echo 'PASS: commands, threads, exit/signal status, streams, cwd, environment, arguments'

# Restrict PATH to exercise unavailable systemd and missing flock behavior.
mkdir "$tmp/bin"
ln -s "$(command -v bash)" "$tmp/bin/bash"
if command -v flock >/dev/null 2>&1; then
    ln -s "$(command -v flock)" "$tmp/bin/flock"
    PATH="$tmp/bin" "$runner" bash -c 'printf fallback' >"$tmp/out" 2>"$tmp/err"
    [[ $(cat "$tmp/out") == fallback ]]
    grep -q 'without memory/CPU limits' "$tmp/err"
    rm "$tmp/bin/flock"
fi
PATH="$tmp/bin" "$runner" bash -c 'printf plain' >"$tmp/out" 2>"$tmp/err"
[[ $(cat "$tmp/out") == plain ]]
grep -q 'without serialization' "$tmp/err"
echo 'PASS: fallback warnings and command execution'

if command -v flock >/dev/null 2>&1; then
    # Both payloads attempt an exclusive directory; overlap fails immediately.
    cat >"$tmp/job" <<'SH'
set -euo pipefail
mkdir "$1/exclusive"
printf '%s-start\n' "$2" >>"$1/order"
sleep 0.5
printf '%s-end\n' "$2" >>"$1/order"
rmdir "$1/exclusive"
SH
    "$runner" bash "$tmp/job" "$tmp" A &
    first=$!
    "$runner" bash "$tmp/job" "$tmp" B &
    second=$!
    wait "$first"
    wait "$second"
    order="$(cat "$tmp/order")"
    [[ $order == $'A-start\nA-end\nB-start\nB-end' ||
       $order == $'B-start\nB-end\nA-start\nA-end' ]]
    echo 'PASS: two concurrent invocations serialize'
else
    echo 'SKIP: serialization needs flock'
fi

if [[ ${1:-} == --memory ]]; then
    status=0
    HEAVY_RUN_MEM_MAX=infinity "$runner" bash -c 'echo UNCONTAINED' \
        >"$tmp/out" 2>"$tmp/err" || status=$?
    [[ $status -eq 125 && ! -s "$tmp/out" ]]
    grep -q 'refusing payload' "$tmp/err"
    echo 'PASS: missing finite kernel cap refuses payload (exit 125)'
    # Read the payload's actual cgroup, touch every page, and retain an observer
    # in that cgroup to read final events after the allocating child is killed.
    # OOMPolicy is start-only for scopes. This real-systemd shim sets it only
    # on disposable test scopes; it and oom_score_adj preserve the observer.
    export HEAVY_RUN_TEST_SYSTEMD_RUN
    HEAVY_RUN_TEST_SYSTEMD_RUN=$(command -v systemd-run)
    cat >"$tmp/bin/systemd-run" <<'SH'
#!/usr/bin/env bash
exec "$HEAVY_RUN_TEST_SYSTEMD_RUN" --property=OOMPolicy=continue "$@"
SH
    chmod +x "$tmp/bin/systemd-run"
    cat >"$tmp/allocate.py" <<'PYTHON'
import os
import pathlib
import signal
import subprocess
import sys


def membership():
    text = pathlib.Path("/proc/self/cgroup").read_text()
    relative = next(line.split("::", 1)[1] for line in text.splitlines()
                    if line.startswith("0::"))
    print(f"pid={os.getpid()} /proc/self/cgroup: {text.strip()}", flush=True)
    return pathlib.Path("/sys/fs/cgroup") / relative.lstrip("/")


def events(group):
    return {key: int(value) for key, value in
            (line.split() for line in (group / "memory.events").read_text().splitlines())}


def check(condition, message):
    if not condition:
        raise RuntimeError("FAIL: " + message)


group = membership()
if "--child" in sys.argv:
    check(str(group) == sys.argv[-1], "allocating child escaped the asserted cgroup")
    pathlib.Path("/proc/self/oom_score_adj").write_text("1000")
    data = bytearray(1024 ** 3)
    print("bytearray returned; memory.current=" + (group / "memory.current").read_text().strip(), flush=True)
    for offset in range(0, len(data), os.sysconf("SC_PAGE_SIZE")):
        data[offset] = 123
        if offset % (64 * 1024 ** 2) == 0:
            rss = next(line for line in pathlib.Path("/proc/self/status").read_text().splitlines()
                       if line.startswith("VmRSS:"))
            print(f"touched={offset} memory.current=" +
                  (group / "memory.current").read_text().strip() + " " + rss, flush=True)
    print("SURVIVED: wrote every page of 1 GiB", flush=True)
    sys.exit(0)

control_group = subprocess.check_output(
    ["systemctl", "--user", "show", group.name, "-p", "ControlGroup", "--value"], text=True).strip()
check(group == pathlib.Path("/sys/fs/cgroup") / control_group.lstrip("/"),
      "payload cgroup differs from the scope ControlGroup")
check("memory" in (group / "cgroup.controllers").read_text().split(), "memory controller unavailable")
check(subprocess.check_output(
    ["systemctl", "--user", "show", group.name, "-p", "OOMPolicy", "--value"], text=True).strip()
      == "continue", "test scope must retain its observer after OOM")
check((group / "memory.max").read_text().strip() == "536870912", "memory.max is not 512 MiB")
check((group / "memory.high").read_text().strip() == "536870912", "memory.high is not 512 MiB")
check((group / "memory.swap.max").read_text().strip() == "2147483648", "memory.swap.max is not 2 GiB")
properties = []
no_swap = "--no-swap" in sys.argv
if no_swap:
    properties.append("MemorySwapMax=0")
if properties:
    subprocess.run(["systemctl", "--user", "set-property", "--runtime", group.name, *properties], check=True)
check((group / "memory.swap.max").read_text().strip() == ("0" if no_swap else "2147483648"),
      "test swap limit did not take effect")
print("VERIFIED: scope ControlGroup matches payload; memory.max=512MiB swap.max=" +
      (group / "memory.swap.max").read_text().strip(), flush=True)
print("user manager cgroup.subtree_control=" + pathlib.Path(
    f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/cgroup.subtree_control"
).read_text().strip(), flush=True)
print("scope cgroup.controllers=" + (group / "cgroup.controllers").read_text().strip(), flush=True)
before = events(group)
child = subprocess.Popen([sys.executable, __file__, "--child", str(group)])
# Block in waitpid rather than allocating polling buffers in a full cgroup.
# The child reports live memory.current/RSS; the kernel retains peak counters.
def timed_out(signum, frame):
    child.kill()
    child.wait()
    raise RuntimeError("FAIL: allocation timed out; cap enforcement was not verified")


signal.signal(signal.SIGALRM, timed_out)
signal.alarm(60)
try:
    child.wait()
finally:
    signal.alarm(0)
after = events(group)
delta = {key: after[key] - before[key] for key in after}
print(f"child returncode={child.returncode}", flush=True)
for name in ("memory.peak", "memory.swap.peak"):
    if (group / name).exists():
        print(name + "=" + (group / name).read_text().strip(), flush=True)
print(f"memory.events delta={delta}", flush=True)
check(delta["max"] > 0, "allocation never hit memory.max")
if no_swap:
    check(child.returncode == -9 and delta["oom_kill"] > 0,
          "MEMORY CAP DID NOT BIND: fully touched 1 GiB must be OOM-killed with swap disabled")
    print("PASS: swap-disabled cap binds; memory.events max>0 and oom_kill>0", flush=True)
else:
    check(child.returncode in (0, -9), "unexpected allocator failure")
    check(child.returncode != -9 or delta["oom_kill"] > 0, "SIGKILL without a cgroup OOM event")
    print("PASS: default 2 GiB swap; hard RAM cap reached", flush=True)
PYTHON
    for mode in --default-swap --no-swap; do
        status=0
        PATH="$tmp/bin:$PATH" HEAVY_RUN_MEM_HIGH=512M HEAVY_RUN_MEM_MAX=512M \
            "$runner" python3 "$tmp/allocate.py" "$mode" >"$tmp/memory.log" 2>&1 || status=$?
        cat "$tmp/memory.log"
        if [[ $status -ne 0 ]]; then
            echo "FAIL: memory containment was not verified ($mode, exit $status)" >&2
            exit 1
        fi
        grep -q '^PASS:' "$tmp/memory.log" || {
            echo "FAIL: missing memory enforcement result ($mode)" >&2
            exit 1
        }
    done
fi
