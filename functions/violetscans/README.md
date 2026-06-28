# violetscans

Generic Violet Scans (violetscans.org) manga updater for Komga.

This is a **series-agnostic** Fission function. The same Package is consumed by
multiple `Function` Custom Resources, one per series. Each Function CR mounts a
different Kubernetes Secret which carries the per-series configuration
(SERIES_NAME, VIOLET_URL, etc.).

## How identity is resolved

Fission mounts a Function's referenced Secret at
`/secrets/<namespace>/<secret-name>/<key>`. Because this Package doesn't know
its consumer's secret name at build time, `main.py` discovers it at startup by
scanning `/secrets/fission/*/` for a subdirectory containing both
`VIOLET_URL` and `KOMGA_API_KEY` keys.

You can override the discovery with the `FISSION_SECRET_NAME` environment
variable if you ever need to disambiguate (e.g. multiple secrets mounted into
the same pod).

The discovered secret name is also used as the scratch subdirectory under
`/mnt/scratch/` so concurrent functions never collide.

## Required Secret keys

| Key | Required | Purpose |
|---|---|---|
| `SERIES_NAME` | yes | Series name to search for in Komga (substring match) |
| `VIOLET_URL` | yes | Full URL of the series page on violetscans.org |
| `KOMGA_API_KEY` | yes | Komga API key |
| `KOMGA_API_URL` | no  | Defaults to `http://komga.media.svc.cluster.local:8080` |
| `KOMGA_LIBRARY_ID` | no | Used only as fallback when import API fails |
| `SCRATCH_PATH` | no | Defaults to `/mnt/scratch` |
| `SCRATCH_SUBDIR` | no | Defaults to the secret name |
| `DRY_RUN` | no | `true` to scan and report without downloading |
| `TEST_MODE` | no | `true` to short-circuit the whole run |

## Local testing

```bash
task local-build-violetscans
```

The build packages the function code into a versioned zip in the repo root.

## Deployment

The Package and Function CRs live in the talos-ops-prod repository under
`kubernetes/apps/fission/fission/functions/`. One Package per source site is
shared across all per-series Function CRs.
