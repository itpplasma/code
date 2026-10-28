# macOS safe zone

This directory in [itpplasma/code](https://github.com/itpplasma/code) installs
`safe pi` with Anthropic Sandbox Runtime (`srt`) 0.0.81. The runtime, policy
and wrapper belong to root. The policy opens one network destination:
`127.0.0.1:8090`. It grants reads and writes to the user's Nextcloud directory
and separate Pi state, while denying credential stores and writes to hidden
tool directories. System binaries and libraries remain readable.

## Install

On Apple Silicon macOS, with Homebrew npm and Pi already installed:

```sh
sudo /usr/bin/python3 sandbox/srt/install.py
```

The installer downloads the official standalone Node.js 24.22.2 binary and
checks its archive against the release's SHA-256 checksum list. This avoids
loading user-writable Homebrew libraries before the sandbox starts. It installs
the pinned npm package without lifecycle scripts and copies the existing local
provider key without displaying it. The supported key sources are a literal
value, a saved API key, or a `tr -d '\n' < file` resolver within local Pi or
configuration storage. No resolver command is executed.

Installed paths:

- `/opt/agents/srt`: runtime and standalone Node.js, root owned.
- `/Library/Application Support/agent-zones/safe.json`: root:wheel, mode 0644.
- `/Library/Application Support/agent-zones/pi-safe`: root-owned Pi settings,
  one provider and an empty Model Context Protocol (MCP) server list.
- `/usr/local/bin/safe`: root:wheel, mode 0755.
- `~/.local/state/pi-safe`: user-owned sessions and cache, mode 0700.

Pi selects the separate configuration using `PI_CODING_AGENT_DIR`; sessions use
`PI_CODING_AGENT_SESSION_DIR` and `--session-dir`. The wrapper disables extensions,
MCP, skills, prompt templates, themes and ambient context files. It refuses
resource, model and credential overrides and clears inherited environment
variables. Inside srt it clears `NO_PROXY` so even localhost requests traverse
the allowlist proxy; local binding stays disabled.

## Verify before changing sudo

Use a new, empty directory for the synthetic file and pass each forbidden
network destination as an argument. Neither probe URLs nor file contents are
printed by the test.

```sh
python3 sandbox/srt/test.py --probe-dir "$HOME/Nextcloud/safe-zone-test" \
  --denied-url http://192.0.2.1:8080
python3 sandbox/srt/test.py --probe-dir "$HOME/Nextcloud/safe-zone-test" \
  --denied-url http://192.0.2.1:8080 --include-pi
```

The first command runs shell probes only. The second explicitly invokes Pi for
a one-line answer and execution of the same isolation checks. A skipped model
check is incomplete validation. Pi 1.4.0 also attempts configuration-directory
locks when reading settings and credentials; compatibility with a read-only
agent directory requires checking with the real CLI before acceptance.

Credential-file probes only attempt opens, without reading bytes. The Keychain
check creates and removes a uniquely named synthetic password. Network checks
require an explicit proxy denial, and direct socket checks require a permission
error. Synthetic file contents are checked and deleted. The runtime's own
temporary directory remains writable, as documented upstream.

The sandbox itself refuses to execute `sudo` ("Operation not permitted"), so
`safe` does not depend on the host sudo policy. Host sudo stays as it is for
now: the qwen native adapter runs as the operator and calls `sudo -n` for its
admission probe, so removing `NOPASSWD` needs a narrow rule for that probe
first. The installer leaves sudo policy unchanged.

The login-Keychain deny workaround is described in
[srt issue 755](https://github.com/anthropics/sandbox-runtime/issues/755).
Absolute deny globs avoid the anchoring problem described in
[srt issue 432](https://github.com/anthropics/sandbox-runtime/issues/432).
The proxy bypass environment issue is discussed in
[srt pull request 769](https://github.com/anthropics/sandbox-runtime/pull/769).
