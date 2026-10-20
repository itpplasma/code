# EUROfusion access

EUROfusion provides its own "DevOps" compute and collaboration
infrastructure (Gateway access, a DevOps GitLab, and the IMAS data
model / Access Layer) that ITPcp members need for IMAS-facing work and
for anything that must be built or run on EUROfusion-provided systems.

Unlike the TU Graz GitLab or GitHub, account access is **not
self-service**: an account request goes through a project/unit
administrator and typically takes weeks. Start early.

## Who requests access

Account and group membership is requested by a project administrator.
At ITPcp, ask the group lead (Christopher Albert) to sponsor the
request and to have you added to the ITPcp group/project on the
EUROfusion DevOps GitLab.

Access for Chris, Winny, Sergei and Max is tracked in
[issue #18](https://github.com/itpplasma/code/issues/18). Each person verifies
their own access; inclusion in the tracker does not confirm access.

## What you need

- **Gateway (SSH) account**: grants command-line access to the
  EUROfusion Gateway (EFGW), reachable at `login.eufus.eu`. Access is
  not self-service: register in the CINECA **UserDB portal**
  (https://userdb.hpc.cineca.it), send the signed GUA to the EUROfusion
  Coordination Officer, then submit the HPC-access request on UserDB.
  Granting is confirmed by CINECA emails with your username and the
  set-up link for 2FA plus the time-limited SSH certificate issued via
  the `efgw` smallstep (step-CA) provisioner (see
  <https://docs.hpc.cineca.it/specific_users/gateway.html>).

- **DevOps GitLab membership**: group/project membership on the
  EUROfusion DevOps GitLab, via the web SSO login at
  <https://gitlab.hpc.cineca.it/> (the same CINECA-hosted GitLab as
  the `gitlab-ssh.hpc.cineca.it` endpoint). Obtain the exact required
  group/project URL from the account-grant message or Christopher Albert;
  do not infer it from the EUROfusion group name.
  *Having an account is not the same as having access to the relevant
  project*; confirm the group membership, not just the account.

## SSH key setup

Two different kinds of SSH credentials are involved, and they must not
be confused:

- **GitLab SSH key**: for Git access to the EUROfusion DevOps GitLab,
  use a passphrase-protected SSH key (see
  [README.md](../README.md#getting-started)). An existing protected key
  can be reused; add its public key to your DevOps GitLab profile
  (<https://gitlab.hpc.cineca.it/-/user_settings/ssh_keys>).
- **Gateway (EFGW) authentication**: contrary to the GitLab SSH key,
  the Gateway does **not** accept a bare `id_rsa` key. Logging in
  requires a **time-limited SSH certificate** obtained from the
  `efgw` smallstep (step-CA) provisioner, which additionally enforces
  2FA. Install the
  [smallstep CLI](https://smallstep.com/docs/step-cli/installation/)
  and complete CINECA’s
  [client bootstrap and ssh-agent setup](https://docs.hpc.cineca.it/general/access.html#how-to-configure-smallstep-client).
  Use the EFGW activation link from the Gateway guide when configuring 2FA.
  With the agent running, obtain a certificate using:

  ```bash
  step ssh login <user> --provisioner efgw
  ```

  This authenticates through the browser with 2FA and loads the short-lived
  SSH certificate into your agent. Repeat when it expires. The Gateway is
  reached through the `login.eufus.eu` host (see
  <https://docs.hpc.cineca.it/specific_users/gateway.html>).

## Verifying access

On your ITPcp machine, after your account is granted:

```bash
# 1. Obtain the time-limited Gateway SSH certificate (2FA required)
step ssh login <user> --provisioner efgw
#    Then interactive login to the EUROfusion Gateway front-end (EFGW)
ssh <user>@login.eufus.eu

# 2. Git access to the EUROfusion DevOps GitLab (hosted by CINECA)
ssh -T git@gitlab-ssh.hpc.cineca.it
#    Expected: "Welcome to GitLab, @<user>!"

# 3. Web SSO
#    log in at https://gitlab.hpc.cineca.it/ and, under the
#    exact group/project URL supplied in the grant message or by the
#    group lead, confirm the required ITPcp project membership
#    (step 3 is the one that matters operationally)
```

Only when all three succeed — and step 3 shows the required group
membership — does ITPcp consider the account usable.
