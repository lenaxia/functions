"""
Generic Violet Scans manga updater.

This is a series-agnostic Fission function. Per-series identity comes
entirely from the mounted Kubernetes Secret (SERIES_NAME, VIOLET_URL,
KOMGA_* keys, etc.). The same Package can be referenced by multiple
Function CRs, each mounting a different Secret.

The mount path is discovered at runtime by scanning /secrets/fission/<name>/
for a subdirectory containing the required keys (VIOLET_URL and
KOMGA_API_KEY at minimum). The matching subdirectory name is also used as
the scratch subdirectory so concurrent functions never collide.
"""

import os
import logging
import re
import json
import zipfile
import time
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from typing import Optional, List, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SECRET_BASE_DIR = Path("/secrets/fission")
REQUIRED_SECRET_KEYS = ("VIOLET_URL", "KOMGA_API_KEY")


def _discover_secret_dir() -> Optional[Path]:
    """Find the active secret mount under /secrets/fission/.

    Fission mounts each Function's referenced Secret at
    /secrets/<namespace>/<secret-name>/. Because this Package is generic,
    we don't know the secret name at build time. Instead, scan for the
    one subdirectory that contains the required keys.
    """
    if not SECRET_BASE_DIR.is_dir():
        return None

    candidates: List[Path] = []
    for entry in SECRET_BASE_DIR.iterdir():
        if not entry.is_dir():
            continue
        if all((entry / k).exists() for k in REQUIRED_SECRET_KEYS):
            candidates.append(entry)

    if len(candidates) == 1:
        logger.info(f"Discovered secret mount: {candidates[0]}")
        return candidates[0]
    if len(candidates) > 1:
        logger.warning(
            f"Multiple candidate secret mounts found: {candidates}. "
            "Using the first one — set FISSION_SECRET_NAME env var to "
            "disambiguate if needed."
        )
        return candidates[0]
    return None


# Cache the discovered secret directory at import time so we don't rescan
# on every _secret() call.
_SECRET_DIR: Optional[Path] = None
_SECRET_NAME: Optional[str] = None


def _init_secret_source():
    """Resolve the secret source once. Honour an explicit override first."""
    global _SECRET_DIR, _SECRET_NAME

    override = os.getenv("FISSION_SECRET_NAME")
    if override:
        candidate = SECRET_BASE_DIR / override
        if candidate.is_dir():
            _SECRET_DIR = candidate
            _SECRET_NAME = override
            logger.info(f"Using explicit secret mount: {candidate}")
            return

    discovered = _discover_secret_dir()
    if discovered:
        _SECRET_DIR = discovered
        _SECRET_NAME = discovered.name


def _secret(name: str, default: str = "") -> str:
    """Read a value from the active Fission secret mount, falling back to env."""
    if _SECRET_DIR is not None:
        path = _SECRET_DIR / name
        if path.exists():
            return path.read_text().strip()
    return os.getenv(name, default)


def _chapter_str(chapter: float) -> str:
    """Format chapter number as string — strip trailing .0 for integers."""
    return str(int(chapter)) if chapter == int(chapter) else str(chapter)


class KomgaAPIClient:
    """Handle all Komga API interactions."""

    def __init__(self, api_url: str, api_key: str, test_mode: bool = False):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.test_mode = test_mode
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    def get_series_id(self, series_name: str) -> Optional[str]:
        if self.test_mode:
            return "test-series-id"

        try:
            response = requests.get(
                f"{self.api_url}/api/v1/series",
                headers=self.headers,
                params={"search": series_name},
            )
            response.raise_for_status()
            series_list = response.json().get("content", [])

            if not series_list:
                logger.error(f"Series not found: {series_name}")
                return None

            return series_list[0].get("id")

        except Exception as e:
            logger.error(f"Error getting series ID: {e}")
            return None

    def get_existing_books(self, series_id: str) -> List[float]:
        """Get ALL existing books in series, normalizing chapter numbers."""
        if self.test_mode:
            return []

        try:
            chapters = []
            page = 0
            while True:
                response = requests.get(
                    f"{self.api_url}/api/v1/series/{series_id}/books",
                    headers=self.headers,
                    params={"size": 500, "page": page},
                )
                response.raise_for_status()
                data = response.json()
                books = data.get("content", [])

                for book in books:
                    fname = Path(book.get("url", "")).stem
                    for text in (fname, book.get("name", "")):
                        match = re.search(r"Chapter\s+(\d+(?:\.\d+)?)", text)
                        if match:
                            chapters.append(float(match.group(1)))
                            break

                if data.get("last", True):
                    break
                page += 1

            return sorted(set(chapters))

        except Exception as e:
            logger.error(f"Error getting existing books: {e}")
            return []

    def trigger_scan(self, library_id: str = "") -> bool:
        if self.test_mode:
            return True

        try:
            url = (
                f"{self.api_url}/api/v1/libraries/{library_id}/scan"
                if library_id
                else f"{self.api_url}/api/v1/libraries/scan"
            )
            response = requests.post(url, headers=self.headers)
            response.raise_for_status()
            logger.info("Komga scan triggered successfully")
            return True

        except Exception as e:
            logger.error(f"Error triggering Komga scan: {e}")
            return False

    def import_books(
        self, series_id: str, file_paths: list, copy_mode: str = "MOVE"
    ) -> bool:
        if self.test_mode:
            return True

        try:
            payload = {
                "books": [
                    {"sourceFile": str(p), "seriesId": series_id} for p in file_paths
                ],
                "copyMode": copy_mode,
            }
            response = requests.post(
                f"{self.api_url}/api/v1/books/import",
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()
            logger.info(f"Imported {len(file_paths)} book(s) into Komga")
            return True

        except Exception as e:
            logger.error(f"Error importing books into Komga: {e}")
            return False


class VioletScansScraper:
    """Generic Violet Scans scraper — works for any series on violetscans.org."""

    def __init__(self, base_url: str, test_mode: bool = False):
        self.base_url = base_url
        self.test_mode = test_mode
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
        )
        self._chapter_map: Optional[Dict[float, str]] = None

    def _fetch_chapter_map(self) -> Dict[float, str]:
        if self._chapter_map is not None:
            return self._chapter_map

        response = self.session.get(self.base_url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        chapter_map = {}
        for link in soup.select("div#chapterlist li a"):
            href = link.get("href", "")
            if not href:
                continue
            text = re.sub(r"\s+", " ", link.text.strip())
            match = re.search(r"Chapter\s+(\d+(?:\.\d+)?)", text)
            if match:
                num = float(match.group(1))
                url = (
                    href
                    if href.startswith("http")
                    else f"https://violetscans.org{href}"
                )
                chapter_map[num] = url

        self._chapter_map = chapter_map
        logger.info(f"Fetched chapter map: {len(chapter_map)} chapters")
        return self._chapter_map

    def get_all_chapters(self) -> List[float]:
        if self.test_mode:
            return [float(i) for i in range(1, 101)]

        try:
            return sorted(self._fetch_chapter_map().keys())
        except Exception as e:
            logger.error(f"Error getting chapters from Violet Scans: {e}")
            return []

    def download_chapter(self, chapter: float, output_path: Path) -> bool:
        if self.test_mode:
            return False

        try:
            chapter_map = self._fetch_chapter_map()
            chapter_url = chapter_map.get(chapter)

            if not chapter_url:
                logger.error(f"Could not find URL for Chapter {chapter} in chapter map")
                return False

            logger.info(f"Downloading Chapter {chapter} from {chapter_url}")
            chapter_response = self.session.get(chapter_url, timeout=60)
            chapter_response.raise_for_status()
            content = chapter_response.text

            # Primary: parse ts_reader.run() JS config
            images = []
            ts_match = re.search(r"ts_reader\.run\(\s*({.*?})\s*\)", content, re.DOTALL)
            if ts_match:
                try:
                    json_str = ts_match.group(1)
                    json_str = re.sub(r",\s*}", "}", json_str)
                    json_str = re.sub(r",\s*]", "]", json_str)
                    ts_config = json.loads(json_str)
                    for source in ts_config.get("sources", []):
                        images.extend(source.get("images", []))
                except Exception as e:
                    logger.warning(f"Could not parse ts_reader config: {e}")

            # Fallback: HTML selectors
            if not images:
                chapter_soup = BeautifulSoup(content, "html.parser")
                for selector in [
                    "div.reading-content img",
                    "#readerarea img",
                    "img[src*='/manga/']",
                    "img[data-src*='/manga/']",
                ]:
                    for img in chapter_soup.select(selector):
                        src = img.get("data-src") or img.get("src")
                        if not src:
                            continue
                        src = str(src)
                        if "/manga/" in src:
                            if not src.startswith("http"):
                                src = (
                                    f"https:{src}"
                                    if src.startswith("//")
                                    else f"https://violetscans.org{src}"
                                )
                            if src not in images:
                                images.append(src)
                    if images:
                        break

            if not images:
                logger.error(f"No images found for Chapter {chapter}")
                return False

            logger.info(f"Found {len(images)} images")
            output_path.mkdir(parents=True, exist_ok=True)

            cbz_filename = output_path / f"Chapter {_chapter_str(chapter)}.cbz"

            # Atomic write: temp file first, then rename.
            tmp_filename = cbz_filename.with_suffix(".cbz.tmp")
            with zipfile.ZipFile(tmp_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
                for i, img_url in enumerate(images, 1):
                    try:
                        logger.info(f"Downloading image {i}/{len(images)}")
                        img_response = requests.get(img_url, timeout=30)
                        img_response.raise_for_status()
                        ext = ".png" if "png" in str(img_url).lower() else ".jpg"
                        zipf.writestr(f"page_{i:03d}{ext}", img_response.content)
                    except Exception as e:
                        logger.error(f"Error downloading image {i}: {e}")

            tmp_filename.rename(cbz_filename)
            logger.info(
                f"Created CBZ: {cbz_filename} ({cbz_filename.stat().st_size:,} bytes)"
            )
            return True

        except Exception as e:
            logger.error(f"Error downloading Chapter {chapter}: {e}")
            tmp = output_path / f"Chapter {_chapter_str(chapter)}.cbz.tmp"
            if tmp.exists():
                tmp.unlink()
            return False


class ScratchFileManager:
    """Manage scratch directory operations."""

    def __init__(self, scratch_path: Path, test_mode: bool = False):
        self.scratch_path = scratch_path
        self.test_mode = test_mode
        self.scratch_path.mkdir(parents=True, exist_ok=True)

    def recover_existing(self) -> List[float]:
        recovered = []
        for f in self.scratch_path.glob("*.cbz"):
            match = re.search(r"Chapter\s+(\d+(?:\.\d+)?)\.cbz$", f.name)
            if match:
                recovered.append(float(match.group(1)))
        if recovered:
            logger.info(
                f"Found {len(recovered)} pre-existing CBZ(s) in scratch to recover: {sorted(recovered)}"
            )
        for tmp in self.scratch_path.glob("*.cbz.tmp"):
            logger.warning(f"Removing partial temp file: {tmp}")
            tmp.unlink()
        return sorted(recovered)

    def cleanup_file(self, filepath: Path) -> bool:
        if self.test_mode:
            return False
        try:
            if filepath.exists():
                filepath.unlink()
                logger.info(f"Cleaned up: {filepath}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error cleaning up file {filepath}: {e}")
            return False


def _import_batch(
    komga_client,
    scratch_manager,
    scratch_path,
    series_id,
    library_id,
    chapters_to_import,
):
    """Import a batch of chapters into Komga and wait for them to move.

    Returns the chapter numbers that were NOT successfully moved (still in
    scratch). The caller treats these as carryover for the next invocation
    or retries them later.

    Behaviour notes:
    - Files that don't exist on disk (e.g. download failed) are silently
      skipped — they're not in the batch.
    - Polling deadline is per-batch, not cumulative across batches, because
      Komga's async import processes batches independently.
    - On Import API failure, fall back to a library scan for THIS batch.
    """
    if not chapters_to_import:
        return []

    cbz_files = [
        scratch_path / f"Chapter {_chapter_str(c)}.cbz" for c in chapters_to_import
    ]
    existing_cbz = [f for f in cbz_files if f.exists()]
    if not existing_cbz:
        logger.warning(
            f"Batch import skipped: none of the {len(chapters_to_import)} "
            f"requested CBZ files exist on disk"
        )
        return list(chapters_to_import)

    logger.info(f"Importing batch of {len(existing_cbz)} CBZ file(s) into Komga")
    if not komga_client.import_books(series_id, existing_cbz, copy_mode="MOVE"):
        logger.warning("Import API failed for batch, falling back to scan")
        komga_client.trigger_scan(library_id)
        # Without import-API confirmation we can't know which files moved.
        # Pessimistically return everything as "still pending."
        return [c for c, f in zip(chapters_to_import, cbz_files) if f.exists()]

    logger.info("Batch import accepted by Komga (async) — polling for completion")
    deadline = time.time() + 300
    pending = list(existing_cbz)
    while pending and time.time() < deadline:
        time.sleep(3)
        pending = [f for f in pending if f.exists()]

    if pending:
        logger.warning(
            f"{len(pending)} file(s) in batch not moved by Komga within timeout"
        )
    else:
        logger.info(f"Batch of {len(existing_cbz)} file(s) moved by Komga successfully")

    # Return chapter numbers for any pending (not-yet-moved) files.
    pending_set = set(pending)
    return [c for c, f in zip(chapters_to_import, cbz_files) if f in pending_set]


def _run(
    komga_client,
    violet_scraper,
    scratch_manager,
    scratch_path,
    series_name,
    library_id,
    dry_run,
    batch_size: int = 5,
    max_downloads_per_run: Optional[int] = None,
):
    """Core workflow — fetch chapter lists, diff, download missing, import.

    Imports happen in batches of `batch_size` to bound the execution time
    of any single trigger invocation. For large backlogs this means an
    invocation makes incremental progress (downloads + imports N chapters)
    rather than trying to do all the work before returning.

    `max_downloads_per_run`, when set, caps the total number of fresh
    downloads in a single invocation (recovered files don't count toward
    the cap — they always get imported). This is the harder bound on
    execution time when needed.
    """
    try:
        logger.info("Getting series ID from Komga")
        series_id = komga_client.get_series_id(series_name)
        if not series_id:
            logger.error(f"Series not found: {series_name}")
            return

        logger.info(f"Series ID: {series_id}")
        existing_chapters = set(komga_client.get_existing_books(series_id))
        logger.info(f"Existing chapters in Komga: {len(existing_chapters)}")

        available_chapters = violet_scraper.get_all_chapters()
        logger.info(f"Available chapters on Violet Scans: {len(available_chapters)}")

        missing = sorted([c for c in available_chapters if c not in existing_chapters])
        logger.info(f"Missing chapters: {missing}")

        recovered = set(scratch_manager.recover_existing())

        orphaned = recovered - set(missing)
        if orphaned:
            logger.info(
                f"Cleaning up {len(orphaned)} orphaned scratch file(s) already in Komga"
            )
            for c in orphaned:
                scratch_manager.cleanup_file(
                    scratch_path / f"Chapter {_chapter_str(c)}.cbz"
                )

        to_import_now = sorted(recovered & set(missing))
        to_download = sorted([c for c in missing if c not in recovered])

        if to_import_now:
            logger.info(
                f"Recovering {len(to_import_now)} chapter(s) from previous run: {to_import_now}"
            )

        if not missing:
            logger.info("No missing chapters — already up to date.")
            return

        if dry_run:
            logger.info(
                f"DRY RUN — would download {len(to_download)} chapter(s): {to_download}"
            )
            if to_import_now:
                logger.info(
                    f"DRY RUN — would import {len(to_import_now)} recovered chapter(s): {to_import_now}"
                )
            return

        # Apply max-downloads cap (if set). Recovered files still get imported.
        if (
            max_downloads_per_run is not None
            and len(to_download) > max_downloads_per_run
        ):
            logger.info(
                f"Capping downloads at {max_downloads_per_run} (of "
                f"{len(to_download)} missing); remaining will be picked up "
                f"on next run"
            )
            to_download = to_download[:max_downloads_per_run]

        # Build the batch queue. Recovered files go first (they're already
        # on disk; import immediately). Then alternate download + import in
        # batches of `batch_size`.
        downloaded_total = 0
        failed_downloads = 0
        imported_total = 0
        pending_batch: List[float] = list(to_import_now)

        def flush_batch():
            """Import whatever's in the current batch, reset the buffer."""
            nonlocal pending_batch, imported_total
            if not pending_batch:
                return
            still_pending = _import_batch(
                komga_client,
                scratch_manager,
                scratch_path,
                series_id,
                library_id,
                pending_batch,
            )
            # Anything not pending was successfully moved.
            imported_total += len(pending_batch) - len(still_pending)
            # Pending files stay in scratch; they'll be picked up by the
            # next invocation's recovery logic. We don't retry within the
            # same invocation to avoid head-of-line blocking on a stuck
            # import.
            pending_batch = []

        # If we have enough recovered files to fill a batch already, flush
        # them before starting any downloads. This makes a run with no new
        # downloads (everything cached) still make import progress.
        if len(pending_batch) >= batch_size or (pending_batch and not to_download):
            flush_batch()

        for chapter in to_download:
            logger.info(f"Processing Chapter {chapter}")
            try:
                if violet_scraper.download_chapter(chapter, scratch_path):
                    downloaded_total += 1
                    pending_batch.append(chapter)
                else:
                    logger.error(f"Failed Chapter {chapter}")
                    failed_downloads += 1
            except Exception as e:
                logger.error(f"Error Chapter {chapter}: {e}")
                failed_downloads += 1

            if len(pending_batch) >= batch_size:
                flush_batch()

        # Final flush for any leftover (downloads + recovered) not yet imported.
        flush_batch()

        logger.info(
            f"Done. Downloaded: {downloaded_total}, Failed: {failed_downloads}, "
            f"Imported: {imported_total}"
        )

    except Exception as e:
        logger.error(f"Run failed: {e}")


def main() -> Dict[str, Any]:
    """Fission entry point."""
    _init_secret_source()

    # Scratch subdir defaults to the secret name (which is the Function name
    # by convention), ensuring concurrent runs of different series never
    # collide on the same scratch directory.
    scratch_subdir = _secret("SCRATCH_SUBDIR") or _SECRET_NAME or "violetscans"
    scratch_base_path = _secret("SCRATCH_PATH") or "/mnt/scratch"
    scratch_path = Path(scratch_base_path) / scratch_subdir

    series_name = _secret("SERIES_NAME")
    komga_api_url = (
        _secret("KOMGA_API_URL") or "http://komga.media.svc.cluster.local:8080"
    )
    komga_api_key = _secret("KOMGA_API_KEY")
    library_id = _secret("KOMGA_LIBRARY_ID")
    violet_url = _secret("VIOLET_URL")
    dry_run = (_secret("DRY_RUN") or os.getenv("DRY_RUN", "false")).lower() == "true"
    test_mode = (
        _secret("TEST_MODE") or os.getenv("TEST_MODE", "false")
    ).lower() == "true"

    logger.info(
        f"Starting Violet Scans update — secret={_SECRET_NAME!r} series={series_name!r} "
        f"scratch={scratch_path} dry_run={dry_run}"
    )

    if test_mode:
        return {
            "status": "success",
            "message": "Test mode - skipped",
            "test_mode": True,
            "secret_name": _SECRET_NAME,
        }

    missing = [
        ("SERIES_NAME", series_name),
        ("VIOLET_URL", violet_url),
        ("KOMGA_API_KEY", komga_api_key),
    ]
    missing_keys = [k for k, v in missing if not v]
    if missing_keys:
        msg = f"Required secret key(s) not provided: {', '.join(missing_keys)}"
        logger.error(msg)
        return {"status": "error", "message": msg}

    komga_client = KomgaAPIClient(komga_api_url, komga_api_key, test_mode=test_mode)
    violet_scraper = VioletScansScraper(violet_url, test_mode=test_mode)
    scratch_manager = ScratchFileManager(scratch_path, test_mode=test_mode)

    # Batch tuning. Defaults are conservative for HTTP-trigger execution:
    # batch_size=5 keeps individual import cycles short. max_downloads_per_run
    # is unbounded by default — set it to a small value (e.g. 10) on series
    # with large backlogs to bound a single invocation's wall-clock time.
    def _parse_int(name: str, default: Optional[int]) -> Optional[int]:
        raw = _secret(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                f"Invalid integer for {name}={raw!r}; using default {default}"
            )
            return default

    batch_size = _parse_int("BATCH_SIZE", 5) or 5
    max_downloads_per_run = _parse_int("MAX_DOWNLOADS_PER_RUN", None)

    _run(
        komga_client,
        violet_scraper,
        scratch_manager,
        scratch_path,
        series_name,
        library_id,
        dry_run,
        batch_size=batch_size,
        max_downloads_per_run=max_downloads_per_run,
    )

    return {
        "status": "success",
        "message": "Violet Scans update completed",
        "secret_name": _SECRET_NAME,
        "series": series_name,
    }
