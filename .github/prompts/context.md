Repository: functions — lenaxia's Fission serverless functions for Kubernetes (`github.com/lenaxia/functions`). Each function lives under `functions/<name>/` with a Python entry point (`main.py`, `main()` callable), a `config.yaml` (Fission spec metadata), optional `requirements.txt`, and tests. Shared code lives in `include/` (rsynced into build context). Deployed via Fission on the homelab Talos cluster (talos-ops-prod Flux).

Key directories:
- functions/<name>/main.py — the function entry point (Python 3.12)
- functions/<name>/config.yaml — Fission function/package metadata
- functions/<name>/requirements.txt — Python dependencies (prefer stdlib)
- include/ — shared utilities rsynced into build contexts
- deploy/ — cluster-side specs (CloudNativePG, secrets, TimeTriggers)

Design principles:
- Python 3.12, stdlib-first (requests/numpy/pandas only if justified)
- Idempotent: every function safe to re-run (checks step_completions table before executing)
- Secrets via /secrets/fission/<fn>/<KEY> sops-age mounts — NEVER hardcoded, NEVER in logs
- Long-running functions on newdeploy executor with appropriate timeouts
- Functions chain via Fission router (HTTP) — chains are thin, steps do the work
- Shadow mode (SHADOW env): compute and record decisions without placing orders
- Postgres (CloudNativePG `tradingpg`) is the operational state store; git is the audit journal
- Kill-switches and equity guards are structural, not optional — review any change that weakens them
