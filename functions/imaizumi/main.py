import os
import logging
import re
import json
import zipfile
import tempfile
import shutil
import time
from pathlib import Path
import requests
from typing import Optional, List, Dict, Any, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _secret(name: str, default: str = "") -> str:
    path = Path(f"/secrets/fission/imaizumi/{name}")
    if path.exists():
        return path.read_text().strip()
    return os.getenv(name, default)


def _chapter_str(chapter: float) -> str:
    return str(int(chapter)) if chapter == int(chapter) else str(chapter)


class KomgaAPIClient:
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


class AtsumaruScraper:
    ATSUMARU_BASE = "https://atsu.moe"

    def __init__(
        self,
        manga_id: str,
        scanlation_id: str = "",
        test_mode: bool = False,
    ):
        self.manga_id = manga_id
        self.scanlation_id = scanlation_id
        self.test_mode = test_mode
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            }
        )
        self._chapter_map: Optional[Dict[float, Dict[str, Any]]] = None

    def _fetch_chapter_map(self) -> Dict[float, Dict[str, Any]]:
        if self._chapter_map is not None:
            return self._chapter_map

        response = self.session.get(
            f"{self.ATSUMARU_BASE}/api/manga/allChapters",
            params={"mangaId": self.manga_id},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        chapter_map: Dict[float, Dict[str, Any]] = {}
        for ch in data.get("chapters", []):
            num = ch.get("number")
            if num is None:
                continue
            num = float(num)
            sid = ch.get("scanlationMangaId", "")

            if self.scanlation_id and sid != self.scanlation_id:
                continue

            if num not in chapter_map:
                chapter_map[num] = ch

        self._chapter_map = chapter_map
        logger.info(f"Fetched chapter map: {len(chapter_map)} chapters")
        return self._chapter_map

    def get_all_chapters(self) -> List[float]:
        if self.test_mode:
            return [float(i) for i in range(1, 101)]

        try:
            return sorted(self._fetch_chapter_map().keys())
        except Exception as e:
            logger.error(f"Error getting chapters from Atsumaru: {e}")
            return []

    def download_chapter(self, chapter: float, output_path: Path) -> bool:
        if self.test_mode:
            return False

        try:
            chapter_map = self._fetch_chapter_map()
            chapter_data = chapter_map.get(chapter)

            if not chapter_data:
                logger.error(f"Could not find data for Chapter {chapter}")
                return False

            chapter_id = chapter_data["id"]
            scanlation_manga_id = chapter_data["scanlationMangaId"]

            logger.info(f"Fetching page list for Chapter {chapter} (id={chapter_id})")
            pages_response = self.session.get(
                f"{self.ATSUMARU_BASE}/api/read/chapter",
                params={"mangaId": self.manga_id, "chapterId": chapter_id},
                timeout=60,
            )
            pages_response.raise_for_status()
            pages_data = pages_response.json()

            pages = pages_data.get("readChapter", {}).get("pages", [])
            if not pages:
                logger.error(f"No pages found for Chapter {chapter}")
                return False

            logger.info(f"Found {len(pages)} pages")

            output_path.mkdir(parents=True, exist_ok=True)
            cbz_filename = output_path / f"Chapter {_chapter_str(chapter)}.cbz"
            tmp_filename = cbz_filename.with_suffix(".cbz.tmp")

            with zipfile.ZipFile(tmp_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
                for i, page in enumerate(pages):
                    image_path = page.get("image", "")
                    if not image_path:
                        logger.warning(f"Page {i} has no image URL, skipping")
                        continue

                    image_url = (
                        image_path
                        if image_path.startswith("http")
                        else f"{self.ATSUMARU_BASE}{image_path}"
                    )

                    logger.info(f"Downloading page {i + 1}/{len(pages)}")
                    try:
                        img_response = requests.get(image_url, timeout=30)
                        img_response.raise_for_status()

                        ext = ".webp"
                        if ".png" in image_path:
                            ext = ".png"
                        elif ".jpg" in image_path or ".jpeg" in image_path:
                            ext = ".jpg"

                        zipf.writestr(f"page_{i + 1:03d}{ext}", img_response.content)
                    except Exception as e:
                        logger.error(f"Error downloading page {i + 1}: {e}")

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
                f"Found {len(recovered)} pre-existing CBZ(s) in scratch: {sorted(recovered)}"
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

    Returns chapter numbers that were NOT successfully moved (still in
    scratch). The caller treats these as carryover for the next invocation.
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

    pending_set = set(pending)
    return [c for c, f in zip(chapters_to_import, cbz_files) if f in pending_set]


def _run(
    komga_client,
    atsumaru_scraper,
    scratch_manager,
    scratch_path,
    series_name,
    library_id,
    dry_run,
    batch_size: int = 5,
    max_downloads_per_run: Optional[int] = None,
):
    """Core workflow — fetch chapter lists, diff, download missing, import.

    Imports happen in batches of `batch_size` so a single invocation makes
    incremental progress on large backlogs. `max_downloads_per_run`
    (optional) caps fresh downloads per invocation.
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

        available_chapters = atsumaru_scraper.get_all_chapters()
        logger.info(f"Available chapters on Atsumaru: {len(available_chapters)}")

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

        downloaded_total = 0
        failed_downloads = 0
        imported_total = 0
        pending_batch: List[float] = list(to_import_now)

        def flush_batch():
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
            imported_total += len(pending_batch) - len(still_pending)
            pending_batch = []

        if len(pending_batch) >= batch_size or (pending_batch and not to_download):
            flush_batch()

        for chapter in to_download:
            logger.info(f"Processing Chapter {chapter}")
            try:
                if atsumaru_scraper.download_chapter(chapter, scratch_path):
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

        flush_batch()

        logger.info(
            f"Done. Downloaded: {downloaded_total}, Failed: {failed_downloads}, "
            f"Imported: {imported_total}"
        )

    except Exception as e:
        logger.error(f"Run failed: {e}")


def main() -> Dict[str, Any]:
    scratch_base_path = _secret("SCRATCH_PATH") or "/mnt/scratch"
    scratch_path = Path(scratch_base_path) / "imaizumi"
    series_name = _secret("SERIES_NAME") or "Imaizumi Brings All the Gals to His House"
    komga_api_url = (
        _secret("KOMGA_API_URL") or "http://komga.media.svc.cluster.local:8080"
    )
    komga_api_key = _secret("KOMGA_API_KEY")
    library_id = _secret("KOMGA_LIBRARY_ID")
    manga_id = _secret("MANGA_ID") or "xurMP"
    scanlation_id = _secret("SCANLATION_ID") or ""
    dry_run = (_secret("DRY_RUN") or os.getenv("DRY_RUN", "false")).lower() == "true"
    test_mode = (
        _secret("TEST_MODE") or os.getenv("TEST_MODE", "false")
    ).lower() == "true"

    logger.info(
        f"Starting Imaizumi update — series={series_name!r} manga_id={manga_id} scratch={scratch_path} dry_run={dry_run}"
    )

    if test_mode:
        return {
            "status": "success",
            "message": "Test mode - skipped",
            "test_mode": True,
        }

    if not komga_api_key:
        logger.error("KOMGA_API_KEY not provided")
        return {"status": "error", "message": "KOMGA_API_KEY is required"}

    komga_client = KomgaAPIClient(komga_api_url, komga_api_key, test_mode=test_mode)
    atsumaru_scraper = AtsumaruScraper(
        manga_id, scanlation_id=scanlation_id, test_mode=test_mode
    )
    scratch_manager = ScratchFileManager(scratch_path, test_mode=test_mode)

    # Batch tuning. Defaults: batch_size=5, unbounded downloads per run.
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
        atsumaru_scraper,
        scratch_manager,
        scratch_path,
        series_name,
        library_id,
        dry_run,
        batch_size=batch_size,
        max_downloads_per_run=max_downloads_per_run,
    )

    return {"status": "success", "message": "Imaizumi update completed"}
