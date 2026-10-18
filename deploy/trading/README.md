# deploy/trading — cluster-side manifests (templates)

These specs deploy the trading system. They live in this repo next to the
function code (same convention as `envs/python3-playwright/`), but they
belong to **talos-ops-prod** (GitOps). Copy them over in one PR; do NOT
apply directly.

## Landing spots

| file in here                            | talos-ops-prod path                                                              |
| --------------------------------------- | -------------------------------------------------------------------------------- |
| `cnpg-cluster.yaml`                     | `kubernetes/apps/databases/cloudnative-pg/clusters/tradingpg.yaml` (+ kustomization entry) |
| `db-init.yaml`                          | `kubernetes/apps/databases/cloudnative-pg/clusters/tradingpg-db-init.yaml` (run-once Job; keep out of the kustomization after it completes, or leave it — Jobs are immutable, Flux will flag drift) |
| `secret-trading.yaml.template`          | encrypt with sops into `tradingpg-app.sops.yaml` (ns databases) and per-function `secret-<fn>.sops.yaml` under `kubernetes/apps/fission/fission/functions/` |
| `fission/packages.yaml`                 | `kubernetes/apps/fission/fission/functions/trading/packages.yaml`                 |
| `fission/functions.yaml`                | `kubernetes/apps/fission/fission/functions/trading/functions.yaml`                |
| `fission/triggers.yaml`                 | `kubernetes/apps/fission/fission/httptriggers/trading/triggers.yaml`              |
| (env) `envs/python3-trading/fission-environment.yaml` (repo root) | `kubernetes/apps/fission/fission/environments/python3-trading.yaml` |

Add the new directories to the respective `kustomization.yaml` files
(functions/, httptriggers/, environments/, clusters/), matching how
`violetscans/` is wired in.

## Ordering

1. CNPG cluster `tradingpg` (waits for the operator)
2. `tradingpg-app` secret (sops) -> run `db-init` Job (creates role+db)
3. Per-function secrets (sops) -> Package CRs -> Function CRs -> triggers
4. Fire `POST /trading-seed` once via the router (schema + seed from the
   ai-trading git state)

## Secrets

Everything flows through sops'd Secrets mounted by Fission at
`/secrets/fission/trading-<function>/<KEY>` (secret name
`trading-<function>`, matching the template and the Function CR refs;
the reader `trading_common.secrets` discovers the mount at runtime).
The template
(`secret-trading.yaml.template`) documents the per-function key matrix.
Never commit plaintext values; generate the role password with
`openssl rand -base64 24`.
