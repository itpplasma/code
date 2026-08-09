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

Current ITPcp members with access are tracked in the issue
"Add users to EUROfusion devops" (itpplasma/code#18):
Chris, Winny, Sergei, Max.

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
  the `gitlab-ssh.hpc.cineca.it` endpoint, reached through the ITPcp
  group [eurofusion](https://gitlab.hpc.cineca.it/groups/eurofusion)).
  *Having an account is not the same as having access to the relevant
  project*; confirm the group membership, not just the account.

## SSH key setup

Two different kinds of SSH credentials are involved, and they must not
be confused:

- **GitLab SSH key**: for Git access to the EUROfusion DevOps GitLab,
  reuse the SSH key you already set up for GitHub/GitLab (see
  [README.md](../README.md#getting-started), `~/.ssh` with a blank
  passphrase). There is no need to generate a new key; add `id_rsa.pub`
  to your DevOps GitLab profile
  (<https://gitlab.hpc.cineca.it/-/user_settings/ssh_keys>).
- **Gateway (EFGW) authentication**: contrary to the GitLab SSH key,
  the Gateway does **not** accept a bare `id_rsa` key. Logging in
  requires a **time-limited SSH certificate** obtained from the
  `efgw` smallstep (step-CA) provisioner, which additionally enforces
  2FA. Install the
  [smallstep CLI](https://smallstep.com/docs/step-cli/installation/)
  and log in once per certificate lifetime (by default repeatedly
  re-running the login when prompted) with:

  ```bash
  step ssh login <user> --provisioner efgw
  ```

  This prompts for the 2FA code and writes a short-lived SSH
  certificate into `~/.ssh` that the Gateway accepts; the Gateway is
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
#    eurofusion group (https://gitlab.hpc.cineca.it/groups/eurofusion),
#    confirm membership of the ITPcp group/project
#    (step 3 is the one that matters operationally)
```

Only when all three succeed — and step 3 shows the required group
membership — does ITPcp consider the account usable.
