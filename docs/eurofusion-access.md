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

- **Gateway (SSH) account**: grants command-line access to
  EUROfusion-provided systems. You receive the exact host from the
  account-grant mail; use that, do not guess a hostname.
- **DevOps GitLab membership**: group/project membership on the
  EUROfusion DevOps GitLab, via the web SSO login. *Having an account
  is not the same as having access to the relevant project*; confirm
  the group membership, not just the account.

## SSH key setup

Reuse the SSH key you already set up for GitHub/GitLab (see
[README.md](../README.md#getting-started), `~/.ssh` with a blank
passphrase). There is no need to generate a new key; add `id_rsa.pub`
to your EUROfusion profile where the account grant instructs.

## Verifying access

On your ITPcp machine, after your account is granted:

```bash
# 1. Interactive login to the EUROfusion Gateway front-end
#    (exact host: take from the account-grant mail, do not guess)
ssh <user>@<gateway-host>

# 2. Git access to the EUROfusion DevOps GitLab
#    (exact host: take from the account-grant mail, do not guess)
ssh -T git@<eufus-gitlab-host>
#    Expected: "Welcome to GitLab, @<user>!"

# 3. Web SSO
#    log in to the DevOps GitLab web UI and confirm membership of the
#    ITPcp group/project (step 3 is the one that matters operationally)
```

Only when all three succeed — and step 3 shows the required group
membership — does ITPcp consider the account usable.
