# GitHub Actions Deployment Design

## Goal

Provide repeatable validation for every change and deploy the tested SIP-D binary to the existing VPS through SSH when `main` is updated.

## Scope

- Local development uses an untracked `.env` copied from `.env.example`.
- CI runs on GitHub-hosted Ubuntu runners for pull requests and pushes.
- CD runs only for pushes to `main`, using an SSH key stored in GitHub Actions secrets.
- Production settings and provider API keys remain solely in `/etc/sip-d.env` on the VPS.

## Workflow

The workflow checks out the repository, configures Go 1.23, then runs `go test ./...`, `go vet ./...`, and builds a Linux AMD64 `sip-d` binary. For a push to `main`, it uploads that binary to a temporary path on the VPS. The remote deploy step atomically replaces `/opt/sip-d/sip-d`, retains the previous version at `/opt/sip-d/sip-d.previous`, restarts `sip-d`, and probes `http://127.0.0.1:8090/healthz`.

## GitHub Secrets

Required repository secrets are `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, and `DEPLOY_HOST_FINGERPRINT`. The fingerprint is the trusted SSH host-key fingerprint for `DEPLOY_HOST`. `DEPLOY_PORT` is optional and defaults to `22`.

## Failure Handling

Validation failures prevent deployment. Deployment commands run with strict shell options, so upload, installation, restart, or health-check failures fail the workflow. The previous binary is retained for manual rollback.

## Verification

The YAML file is checked for syntax with Ruby's built-in YAML parser; existing Go tests and vet are run locally. The first production deployment is validated by the workflow's remote `/healthz` request.
