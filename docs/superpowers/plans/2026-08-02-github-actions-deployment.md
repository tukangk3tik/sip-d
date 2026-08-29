# GitHub Actions Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate SIP-D changes on GitHub and deploy the verified Linux binary to the VPS through SSH after pushes to `main`.

**Architecture:** One GitHub Actions workflow has a validation job for pull requests and pushes, followed by a deploy job gated to push events on `main`. The deploy job uses repository secrets for its SSH connection and leaves runtime configuration on the VPS.

**Tech Stack:** GitHub Actions, Go 1.23, Ubuntu hosted runners, OpenSSH, `appleboy/scp-action`, `appleboy/ssh-action`.

## Global Constraints

- Never commit production values or API keys; `/etc/sip-d.env` remains VPS-owned.
- Build target is `linux/amd64` with CGO enabled because SIP-D uses `go-sqlite3`.
- Deploy only after successful validation for a `push` to `main`.
- Preserve `/opt/sip-d/sip-d.previous` as a rollback binary.

---

### Task 1: Add CI/CD workflow

**Files:**
- Create: `.github/workflows/ci-cd.yml`
- Test: `.github/workflows/ci-cd.yml` parsed by Ruby YAML

**Interfaces:**
- Consumes: GitHub repository secrets `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, and optional `DEPLOY_PORT`.
- Produces: Tested `sip-d` Linux AMD64 binary installed at `/opt/sip-d/sip-d` after a successful main-branch push.

- [ ] Add one workflow triggered for pull requests and pushes to `main`.
- [ ] Configure Go 1.23, execute `go test ./...` and `go vet ./...`, then build a Linux AMD64 binary with CGO enabled.
- [ ] Gate an SSH deployment job to pushes on `main`; upload the binary, preserve the prior binary, install the new version, restart `sip-d`, and call `/healthz`.
- [ ] Parse the YAML with Ruby and run the Go validation commands locally.
- [ ] Commit the workflow and its documentation with message `ci: add VPS deployment workflow`.
