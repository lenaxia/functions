# deploy/trading — cluster-side manifests (templates)

These specs deploy the trading system. They live in this repo next to the
function code (same convention as `envs/python3-trading/`), but they
belong to **talos-ops-prod** (GitOps). Copy them over in one PR; do NOT
apply directly.

## Landing spots

| file in here                            | talos-ops-prod path                                                              |
| --------------------------------------- | -------------------------------------------------------------------------------- |
| `cnpg-cluster.yaml`                     | `kubernetes/apps/databases/cloudnative-pg/clusters/tradingpg.yaml` (+ kustomization entry). The `bootstrap.initdb.secret` (tradingpg-app) creates the `trading` role + database at first boot — no db-init Job |
| `secret-trading.yaml.template`          | encrypt with sops into `clusters/tradingpg-app.sops.yaml` (ns databases) and `functions/trading/secret-trading.sops.yaml` (ns fission — ONE shared secret for all 8 functions) |
| `fission/packages.yaml`                 | `kubernetes/apps/fission/fission/functions/trading/packages.yaml`                 |
| `fission/functions.yaml`                | `kubernetes/apps/fission/fission/functions/trading/functions.yaml`                |
| `fission/triggers.yaml`                 | `kubernetes/apps/fission/fission/httptriggers/trading/triggers.yaml`              |
| (env) `envs/python3-trading/fission-environment.yaml` (repo root) | `kubernetes/apps/fission/fission/environments/python3-trading.yaml` |

Add the new directories to the respective `kustomization.yaml` files
(functions/, httptriggers/, environments/, clusters/), matching how
`violetscans/` is wired in.

## Ordering

1. CNPG cluster `tradingpg` + `tradingpg-app` sops secret (bootstrap
   creates role+db on first boot). NOTE: bootstrap only runs at cluster
   initialization — if the cluster already came up before this secret
   existed, delete the empty cluster (`kubectl -n databases delete
   cluster tradingpg` + its PVC; pre-seed = zero data loss) and let Flux
   recreate it.
2. Shared `trading` secret (sops) -> Package CRs -> Function CRs -> triggers
3. Fire `POST /trading-seed` once via the router (schema + seed from the
   ai-trading git state)

## Secrets

Everything flows through TWO sops'd Secrets: `tradingpg-app`
(username+password, consumed by CNPG's initdb bootstrap) and the shared
`trading` secret, mounted by every function at
`/secrets/fission/trading/<KEY>` (the runtime reader discovers the
single mount per pod). Functions read only the keys they use. Never
commit plaintext values; generate the password with
`openssl rand -base64 24`.
