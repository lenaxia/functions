# Manga Scraper Guide — Building Komga Updater Functions

This guide covers how to build a manga scraper Fission function that downloads chapters from a scanlation site and imports them into Komga.

## Assumptions

This guide assumes the following infrastructure is already in place. These are specific to the `lenaxia/talos-ops-prod` cluster and will differ if you're deploying elsewhere:

- **Fission** is installed on the Kubernetes cluster with the `python3-manga-updater` environment
- **Komga** is deployed and accessible at `http://komga.media.svc.cluster.local:8080`
- **A Komga library** exists with its root at `/mnt/media` on the `komga-media-volume` PVC
- **A shared scratch PVC** (`scratch-matriarch`, RWX via Longhorn NFS) is mounted at `/mnt/scratch` in both the Fission environment and the Komga pod
- **Flux** is reconciling manifests from `lenaxia/talos-ops-prod`
- **SOPS** is configured for encrypting Kubernetes secrets with age
- **The router LoadBalancer** is reachable (e.g., `192.168.5.138` for HTTP triggers)

This guide is **Python-only**. No Node.js or Go examples are provided.

## Architecture

```
Scanlation Site (VyManga, VioletScans, etc.)
    ↓ scrape chapter list + image URLs
Fission Function
    ↓ download images → build CBZ files
    ↓ POST /api/v1/books/import (MOVE mode)
Komga
    ↓ moves CBZ from scratch into library path
    ↓ auto-organizes into series
```

### Key Components

Every manga scraper has four classes/sections:

| Component | Purpose |
|---|---|
| `KomgaAPIClient` | All Komga REST API interactions |
| `Scraper` (site-specific) | Fetch chapter list, download images, build CBZ |
| `ScratchFileManager` | Manage scratch directory, recover interrupted downloads |
| `main()` + `_run()` | Fission entrypoint and core workflow |

### Shared Infrastructure (cluster-specific)

These resources are defined in `talos-ops-prod` and are specific to this deployment:

| Resource | Details |
|---|---|
| **Environment** | `python3-manga-updater` — Fission Python env with NFS scratch mount at `/mnt/scratch` |
| **Scratch PVC** | `scratch-matriarch` — RWX Longhorn volume shared between Fission pods and Komga pod |
| **Komga** | Media server at `http://komga.media.svc.cluster.local:8080` |
| **Komga library** | Root at `/mnt/media` on `komga-media-volume` PVC |

The scratch PVC is mounted via NFS in the Fission environment spec (`kubernetes/apps/fission/fission/environments/python3-manga-updater.yaml`). The Komga deployment mounts both the media PVC and the scratch PVC.

---

## Required Secrets

Each function needs a SOPS-encrypted Kubernetes Secret (`secret.sops.yaml` in `talos-ops-prod`) with these keys:

| Key | Example | Purpose |
|---|---|---|
| `KOMGA_API_KEY` | (admin API key) | Authenticate with Komga |
| `KOMGA_API_URL` | `http://komga.media.svc.cluster.local:8080` | Komga service URL |
| `KOMGA_LIBRARY_ID` | `<LIBRARY_ID>` | Komga library ID for fallback scan |
| `SERIES_NAME` | `I'll Be The Matriarch In This Life (VyManga)` | Exact name in Komga |
| `<SITE>_URL` | `VYMANGA_URL` or `VIOLET_URL` | Series page on scanlation site. Key name is arbitrary — match what `main.py` reads via `_secret()` |

Optional keys: `SCRATCH_PATH` (default `/mnt/scratch`), `DRY_RUN`, `TEST_MODE`.

Secrets are mounted by Fission at `/secrets/fission/<function-name>/<key>` as individual files.

---

## File Structure

```
functions/<function-name>/
    config.yaml              Environment, runtime, version
    main.py                  All code in one file (entrypoint = main.main)
    requirements.txt         Production deps (requests, beautifulsoup4, lxml)
    requirements-test.txt    Test deps (pytest)
    pytest.ini               Pytest config
    tests/
        test_simple.py       Basic handler initialization tests
        test_handler.py      Env var handling, missing secrets
        test_classes.py      Unit tests for each class
        test_integration.py  End-to-end workflow with mocked APIs
        test_scraper.py      Detailed scraper parsing tests
```

**Important**: The entrypoint file must be named `main.py` and the function must be named `main()`. Fission is configured with `functionName: main` which resolves to `main.main`.

---

## Code Pattern

### Secrets Helper

```python
def _secret(name: str, default: str = "") -> str:
    path = Path(f"/secrets/fission/<function-name>/{name}")
    if path.exists():
        return path.read_text().strip()
    return os.getenv(name, default)
```

Fission mounts secrets as files, not env vars. Always read from `/secrets/fission/<func>/<key>` first, fall back to env var for local testing.

### Chapter String Formatting

```python
def _chapter_str(chapter: float) -> str:
    return str(int(chapter)) if chapter == int(chapter) else str(chapter)
```

Ensures `1.0` → `"1"` (not `"1.0"`) while keeping `1.5` → `"1.5"`.

### KomgaAPIClient

```python
class KomgaAPIClient:
    def __init__(self, api_url, api_key, test_mode=False):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    def get_series_id(self, series_name):
        # GET /api/v1/series?search=<name>
        # Returns first match's ID or None

    def get_existing_books(self, series_id):
        # GET /api/v1/series/{id}/books (paginated, 500/page)
        # Parse chapter numbers from filenames using regex
        # Returns sorted list of float chapter numbers

    def import_books(self, series_id, file_paths, copy_mode="MOVE"):
        # POST /api/v1/books/import
        # Body: {"books": [{"sourceFile": ..., "seriesId": ...}], "copyMode": "MOVE"}
        # MOVE means Komga moves file from scratch into its library dir

    def trigger_scan(self, library_id):
        # POST /api/v1/libraries/{id}/scan
        # Fallback if import_books fails
```

**Key API details:**
- `GET /api/v1/series?search=` returns `{"content": [...], "last": bool}` (paginated)
- `GET /api/v1/series/{id}/books?size=500&page=N` returns paginated books
- `POST /api/v1/books/import` is async — accepts the request but processes in background
- `seriesId` is **required** for import — Komga cannot auto-create series via API
- Series are only created when Komga scans a directory containing CBZ files

### Scraper (Site-Specific)

```python
class MyScraper:
    def __init__(self, base_url, test_mode=False):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 ...",
        })
        self._chapter_map = None  # Cache: {chapter_num: url}

    def _fetch_chapter_map(self):
        # Fetch the manga's chapter listing page once
        # Parse with BeautifulSoup to extract {chapter_number: url}
        # Cache in self._chapter_map

    def get_all_chapters(self):
        # Returns sorted list of chapter numbers
        # In test_mode: returns [1.0, 2.0, ..., 100.0]

    def download_chapter(self, chapter, output_path):
        # 1. Look up chapter URL from chapter_map
        # 2. Fetch the reading page HTML
        # 3. Parse image URLs from the page
        # 4. Write to .cbz.tmp (atomic write pattern)
        # 5. Rename to .cbz when complete
        # Returns True on success, False on failure
```

### ScratchFileManager

```python
class ScratchFileManager:
    def __init__(self, scratch_path, test_mode=False):
        self.scratch_path = scratch_path
        self.scratch_path.mkdir(parents=True, exist_ok=True)

    def recover_existing(self):
        # Find *.cbz files from previous interrupted run
        # Clean up any *.cbz.tmp partial files
        # Returns list of recovered chapter numbers

    def cleanup_file(self, filepath):
        # Delete a file from scratch
```

### Core Workflow (`_run`)

```
1. get_series_id(series_name)     → fail if not found
2. get_existing_books(series_id)  → set of chapter numbers in Komga
3. scraper.get_all_chapters()     → set of chapters available online
4. Diff: missing = available - existing
5. recover_existing()             → CBZ files from previous crashed run
6. Download missing chapters to /mnt/scratch/<func>/
7. import_books(series_id, cbz_files, copy_mode="MOVE")
8. Poll: wait for Komga to move files out of scratch (max 5 min)
9. If import fails: trigger_scan(library_id) as fallback
```

### Entry Point (`main`)

```python
def main() -> Dict[str, Any]:
    series_name = _secret("SERIES_NAME") or "Default Series Name"
    komga_api_url = _secret("KOMGA_API_URL") or "http://komga.media.svc.cluster.local:8080"
    komga_api_key = _secret("KOMGA_API_KEY")
    site_url = _secret("SITE_URL") or "https://example.com/manga/..."
    dry_run = (_secret("DRY_RUN") or "false").lower() == "true"
    test_mode = (_secret("TEST_MODE") or "false").lower() == "true"
    scratch_path = Path(_secret("SCRATCH_PATH") or "/mnt/scratch") / "<func-name>"

    if test_mode:
        return {"status": "success", "test_mode": True}

    if not komga_api_key:
        return {"status": "error", "message": "KOMGA_API_KEY is required"}

    _run(
        KomgaAPIClient(komga_api_url, komga_api_key),
        MyScraper(site_url),
        ScratchFileManager(scratch_path),
        scratch_path,
        series_name,
        _secret("KOMGA_LIBRARY_ID"),
        dry_run,
    )
    return {"status": "success", "message": "Update completed"}
```

---

## Atomic CBZ Write Pattern

Always write to a `.cbz.tmp` file first, then rename:

```python
tmp_filename = cbz_filename.with_suffix(".cbz.tmp")
with zipfile.ZipFile(tmp_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
    for i, img_url in enumerate(images, 1):
        img_response = session.get(img_url, timeout=30)
        zipf.writestr(f"page_{i:03d}.jpg", img_response.content)
tmp_filename.rename(cbz_filename)
```

This ensures `recover_existing()` can distinguish complete CBZ files (`.cbz`) from partial downloads (`.cbz.tmp`). On crash recovery, partials are deleted and complete files are re-imported.

---

## Komga Import Flow

```
Function downloads to:  /mnt/scratch/<func>/Chapter X.cbz
                           ↓
Komga import API:       POST /api/v1/books/import
                        {"books": [{"sourceFile": "/mnt/scratch/<func>/Chapter X.cbz", "seriesId": "..."}], "copyMode": "MOVE"}
                           ↓
Komga moves file:       /mnt/scratch/<func>/Chapter X.cbz → /mnt/media/<Series Name>/Chapter X.cbz
                           ↓
Function polls:         Check if file still exists in scratch every 3s, timeout 5 min
```

Both the function pod and Komga pod mount the same scratch PVC. Komga also mounts the media PVC where it stores the library. The `MOVE` copy_mode means Komga handles the file transfer — the function doesn't need access to the media volume.

---

## Deployment (talos-ops-prod)

### 1. Function + Package (`functions/<func>.yaml`)

```yaml
apiVersion: fission.io/v1
kind: Package
metadata:
  name: my-scraper
  namespace: fission
spec:
  environment:
    name: python3-manga-updater
    namespace: fission
  deployment:
    type: url
    url: https://github.com/lenaxia/functions/releases/latest/download/my-scraper-1.0.0.zip
---
apiVersion: fission.io/v1
kind: Function
metadata:
  name: my-scraper
  namespace: fission
spec:
  environment:
    name: python3-manga-updater
    namespace: fission
  package:
    packageref:
      name: my-scraper
      namespace: fission
    functionName: main
  secrets:
    - name: my-scraper
      namespace: fission
  InvokeStrategy:
    StrategyType: execution
    ExecutionStrategy:
      ExecutorType: newdeploy
      MinScale: 1
      MaxScale: 1
      SpecializationTimeout: 120
```

### 2. HTTP Trigger (`httptriggers/<func>.yaml`)

```yaml
apiVersion: fission.io/v1
kind: HTTPTrigger
metadata:
  name: my-scraper
  namespace: fission
spec:
  functionref:
    name: my-scraper
    type: name
  relativeurl: /my-scraper
  methods:
    - GET
    - POST
```

### 3. Timer Trigger (`httptriggers/<func>-timer.yaml`)

```yaml
apiVersion: fission.io/v1
kind: TimeTrigger
metadata:
  name: my-scraper-daily
  namespace: fission
spec:
  cron: "0 15 * * *"
  functionref:
    name: my-scraper
    type: name
```

### 4. Secret (`functions/secret-<func>.sops.yaml`)

```yaml
apiVersion: v1
kind: Secret
type: Opaque
metadata:
  name: my-scraper
  namespace: fission
stringData:
  KOMGA_API_KEY: ENC[...]
  KOMGA_API_URL: ENC[...]
  KOMGA_LIBRARY_ID: ENC[...]
  SERIES_NAME: ENC[...]
  SITE_URL: ENC[...]
```

### 5. Bootstrap the Series

> **Limitation**: Komga's `POST /api/v1/books/import` requires an existing `seriesId`. There is no API to create a series. Series are only created when Komga scans a directory containing CBZ files. This means the first run requires manual bootstrapping. A future improvement would be to have the function auto-bootstrap by writing directly into the Komga library path (requires adding the media volume mount to the Fission environment).

The bootstrap commands below are run from within the cluster pods. Adjust pod names and paths for your deployment:

```bash
# From the Komga pod, create the series directory in the library
kubectl exec -n media deploy/komga -- mkdir -p "/mnt/media/<Series Name>"

# Copy a placeholder CBZ (or from another series) so Komga discovers it
kubectl exec -n media deploy/komga -- cp "/mnt/media/<Existing Series>/Chapter 001.cbz" "/mnt/media/<Series Name>/Chapter 001.cbz"

# Trigger a library scan via the API
kubectl exec -n fission <pod> -c python3-manga-updater -- python3 -c "
import urllib.request
req = urllib.request.Request('http://komga.media.svc.cluster.local:8080/api/v1/libraries/<LIBRARY_ID>/scan', method='POST', headers={'X-API-Key': '<KEY>'})
urllib.request.urlopen(req)
"
```

Wait ~10 seconds, then verify: `GET /api/v1/series?search=<Series Name>` should return the new series with its ID.

---

## Testing Strategy

### Unit Tests (`test_classes.py`)

Test each class in isolation with `test_mode=True` or mocked network:

- `KomgaAPIClient`: test_mode returns stubs; mock `requests.get` for real API tests
- `Scraper`: test `_fetch_chapter_map` with HTML fixtures; test `get_all_chapters` with mocked map
- `ScratchFileManager`: use `tempfile.TemporaryDirectory` for real filesystem tests
- `_run()`: mock all three classes, verify the download+import workflow

### Integration Tests (`test_integration.py`)

Key challenge: `download_chapter` returns `True` but doesn't create real files in mock mode. The `_run()` function checks `f.exists()` after download, so tests must create real CBZ files:

```python
def _make_download_side_effect(scratch_path):
    def _download(chapter, output_path, *args, **kwargs):
        cbz = output_path / f"Chapter {_chapter_str(chapter)}.cbz"
        output_path.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(cbz, "w") as z:
            z.writestr("page_001.jpg", b"fake")
        return True
    return _download
```

Similarly, `import_books` with `copy_mode="MOVE"` means `_run()` polls until files disappear from scratch. Tests must delete files to simulate the move:

```python
def _import_and_move(series_id, file_paths, *args, **kwargs):
    for f in file_paths:
        p = Path(f)
        if p.exists():
            p.unlink()
    return True
```

### Scraper Tests (`test_scraper.py`)

Provide HTML fixtures matching the real site structure. Test:
- Chapter list parsing (chapter numbers + URLs)
- Caching (second call hits cache, no second HTTP request)
- Image URL extraction and quality upgrade (`=w700` → `=w0`)
- CBZ file structure (sequential page naming, correct extensions)
- Error handling (unknown chapter, missing images, network failures)
- Temp file cleanup on failure

---

## Checklist for New Scrapers

- [ ] Create `functions/<name>/` with `config.yaml`, `main.py`, `requirements.txt`, `requirements-test.txt`, `pytest.ini`, `tests/`
- [ ] Implement `KomgaAPIClient` (copy from existing — it's identical across functions)
- [ ] Implement site-specific `Scraper` class
  - [ ] `_fetch_chapter_map()` — parse manga page for `{chapter_num: url}`
  - [ ] `get_all_chapters()` — return sorted chapter numbers
  - [ ] `download_chapter()` — fetch reading page, extract images, build CBZ
  - [ ] Image quality upgrade if site serves resized images
- [ ] Implement `ScratchFileManager` (copy from existing)
- [ ] Implement `_run()` and `main()` (copy from existing, adjust variable names)
- [ ] Write tests (5 files, ~50+ tests total)
- [ ] Run `pytest` — all tests must pass
- [ ] Bump version in `config.yaml`
- [ ] Commit, push, wait for CI release
- [ ] Create Fission manifests in `talos-ops-prod`: Package, Function, HTTPTrigger, TimeTrigger
- [ ] Create SOPS-encrypted secret with required keys
- [ ] Bootstrap the Komga series (create dir + copy first CBZ + scan)
- [ ] Trigger via `curl http://<router>/<func>` and verify

---

## Common Pitfalls

1. **Entrypoint naming**: Must be `main.py` with `def main()`. Not `handler.py` / `handler()`.
2. **Secret reading**: Fission mounts secrets as files at `/secrets/fission/<func>/<key>`, not env vars. Use the `_secret()` helper.
3. **Import polling**: `import_books` is async. Must poll until files disappear from scratch, or the test hangs forever.
4. **Test file creation**: Mocked `download_chapter` must create real CBZ files on disk, otherwise `_run()` finds no files to import.
5. **Series must exist**: Komga won't create series via API. Bootstrap manually before first run.
6. **Scratch path**: Use `/mnt/scratch/<func-name>/` as subdirectory — multiple functions share the same PVC.
7. **`copy_mode="MOVE"`**: Komga moves files from scratch to library. After import, files disappear from scratch automatically.
