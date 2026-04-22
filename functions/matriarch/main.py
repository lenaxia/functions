import os
import logging
import re
import json
import zipfile
import tempfile
import shutil
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from typing import Optional, List, Dict, Any, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _secret(name: str, default: str = "") -> str:
    """Read from Fission secret mount, fall back to env var."""
    path = Path(f"/secrets/fission/matriarch/{name}")
    if path.exists():
        return path.read_text().strip()
    return os.getenv(name, default)


def _chapter_str(chapter: float) -> str:
    """Format chapter number as string — strip trailing .0 for integers."""
    return str(int(chapter)) if chapter == int(chapter) else str(chapter)


class KomgaAPIClient:
    """Handle all Komga API interactions"""

    def __init__(self, api_url: str, api_key: str, test_mode: bool = False):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.test_mode = test_mode
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    def get_series_id(self, series_name: str) -> Optional[str]:
        """Find series ID by name"""
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
        """Get ALL existing books in series, normalizing chapter numbers.

        Handles zero-padded names (Chapter 000 = 1, Chapter 047 = 47) and
        decimal names (Chapter 47.5) correctly so set diff works regardless
        of how the CBZ was originally named.
        """
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
                    # Use the actual file URL to get the filename — more
                    # reliable than the display name which Komga may reformat.
                    fname = Path(book.get("url", "")).stem  # e.g. "Chapter 047"
                    # Try filename first, fall back to display name
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
        """Trigger library scan in Komga"""
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
        """Import CBZ files directly into Komga via the import API"""
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
    """Handle scraping and download from Violet Scans"""

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
        # Cache: {chapter_num: chapter_url}
        self._chapter_map: Optional[Dict[float, str]] = None

    def _fetch_chapter_map(self) -> Dict[float, str]:
        """Fetch the chapter list once and cache it."""
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
        """Get all chapter numbers available on Violet Scans"""
        if self.test_mode:
            return [float(i) for i in range(1, 101)]

        try:
            return sorted(self._fetch_chapter_map().keys())
        except Exception as e:
            logger.error(f"Error getting chapters from Violet Scans: {e}")
            return []

    def download_chapter(self, chapter: float, output_path: Path) -> bool:
        """Download chapter pages and create CBZ file"""
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

            # Write to a temp file first — if we crash mid-download the
            # partial file won't be mistaken for a complete CBZ on recovery.
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

            # Atomic rename — only exists as .cbz once fully written
            tmp_filename.rename(cbz_filename)
            logger.info(
                f"Created CBZ: {cbz_filename} ({cbz_filename.stat().st_size:,} bytes)"
            )
            return True

        except Exception as e:
            logger.error(f"Error downloading Chapter {chapter}: {e}")
            # Clean up any partial temp file
            tmp = output_path / f"Chapter {_chapter_str(chapter)}.cbz.tmp"
            if tmp.exists():
                tmp.unlink()
            return False


class ScratchFileManager:
    """Manage scratch directory operations"""

    def __init__(self, scratch_path: Path, test_mode: bool = False):
        self.scratch_path = scratch_path
        self.test_mode = test_mode
        self.scratch_path.mkdir(parents=True, exist_ok=True)

    def recover_existing(self) -> List[float]:
        """Find any complete CBZ files left from a previous interrupted run.

        Returns chapter numbers for CBZ files that are fully written
        (i.e. not .cbz.tmp partials).
        """
        recovered = []
        for f in self.scratch_path.glob("*.cbz"):
            match = re.search(r"Chapter\s+(\d+(?:\.\d+)?)\.cbz$", f.name)
            if match:
                recovered.append(float(match.group(1)))
        if recovered:
            logger.info(
                f"Found {len(recovered)} pre-existing CBZ(s) in scratch to recover: {sorted(recovered)}"
            )
        # Clean up any leftover .tmp files from a previous crash
        for tmp in self.scratch_path.glob("*.cbz.tmp"):
            logger.warning(f"Removing partial temp file: {tmp}")
            tmp.unlink()
        return sorted(recovered)

    def cleanup_file(self, filepath: Path) -> bool:
        """Remove file from scratch directory"""
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


def _run(
    komga_client,
    violet_scraper,
    scratch_manager,
    scratch_path,
    series_name,
    library_id,
    dry_run,
):
    """Core workflow — fetch chapter lists, diff, download missing, import."""
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
        logger.info(f"Missing chapters to download: {missing}")

        # Recover any complete CBZ files left from a previous interrupted run —
        # skip re-downloading them, go straight to import.
        recovered = set(scratch_manager.recover_existing())
        to_import_now = sorted(recovered & set(missing))
        to_download = sorted([c for c in missing if c not in recovered])

        if to_import_now:
            logger.info(
                f"Recovering {len(to_import_now)} chapter(s) from previous run: {to_import_now}"
            )

        if not missing and not to_import_now:
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

        # Download missing chapters
        downloaded = list(to_import_now)  # start with recovered
        for chapter in to_download:
            logger.info(f"Processing Chapter {chapter}")
            try:
                if violet_scraper.download_chapter(chapter, scratch_path):
                    downloaded.append(chapter)
                else:
                    logger.error(f"Failed Chapter {chapter}")
            except Exception as e:
                logger.error(f"Error Chapter {chapter}: {e}")

        # Import everything we have
        if downloaded:
            cbz_files = [
                scratch_path / f"Chapter {_chapter_str(c)}.cbz" for c in downloaded
            ]
            existing_cbz = [f for f in cbz_files if f.exists()]
            if existing_cbz:
                logger.info(f"Importing {len(existing_cbz)} CBZ file(s) into Komga")
                if komga_client.import_books(series_id, existing_cbz, copy_mode="MOVE"):
                    logger.info("Import successful")
                else:
                    logger.warning("Import API failed, falling back to scan")
                    komga_client.trigger_scan(library_id)

        logger.info(
            f"Done. Downloaded: {len(downloaded)}, Failed: {len(to_download) - (len(downloaded) - len(to_import_now))}"
        )

    except Exception as e:
        logger.error(f"Run failed: {e}")


def main() -> Dict[str, Any]:
    """Fission entry point."""
    scratch_base_path = _secret("SCRATCH_PATH") or "/mnt/scratch"
    scratch_path = Path(scratch_base_path) / "matriarch"
    series_name = _secret("SERIES_NAME") or "I'll Be The Matriarch In This Life"
    komga_api_url = (
        _secret("KOMGA_API_URL") or "http://komga.media.svc.cluster.local:8080"
    )
    komga_api_key = _secret("KOMGA_API_KEY")
    library_id = _secret("KOMGA_LIBRARY_ID")
    violet_url = (
        _secret("VIOLET_URL")
        or "https://violetscans.org/comics/ill-be-the-matriarch-in-this-life/"
    )
    dry_run = (_secret("DRY_RUN") or os.getenv("DRY_RUN", "false")).lower() == "true"
    test_mode = (
        _secret("TEST_MODE") or os.getenv("TEST_MODE", "false")
    ).lower() == "true"

    logger.info(
        f"Starting Matriarch update — series={series_name!r} scratch={scratch_path} dry_run={dry_run}"
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
    violet_scraper = VioletScansScraper(violet_url, test_mode=test_mode)
    scratch_manager = ScratchFileManager(scratch_path, test_mode=test_mode)

    _run(
        komga_client,
        violet_scraper,
        scratch_manager,
        scratch_path,
        series_name,
        library_id,
        dry_run,
    )

    return {"status": "success", "message": "Matriarch update completed"}
