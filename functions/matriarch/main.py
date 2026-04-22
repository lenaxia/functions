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
from typing import Optional, List, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _secret(name: str, default: str = "") -> str:
    """Read from Fission secret mount, fall back to env var."""
    path = Path(f"/secrets/fission/matriarch/{name}")
    if path.exists():
        return path.read_text().strip()
    return os.getenv(name, default)


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

    def get_existing_books(self, series_id: str) -> List[int]:
        """Get existing books in series and extract chapter numbers"""
        if self.test_mode:
            return []

        try:
            response = requests.get(
                f"{self.api_url}/api/v1/series/{series_id}/books", headers=self.headers
            )
            response.raise_for_status()
            books = response.json().get("content", [])

            chapters = []
            for book in books:
                name = book.get("name", "")
                match = re.search(r"Chapter\s+(\d+(?:\.\d+)?)", name)
                if match:
                    chapters.append(float(match.group(1)))

            return sorted(chapters)

        except Exception as e:
            logger.error(f"Error getting existing books: {e}")
            return []

    def trigger_scan(self, library_id: str = "") -> bool:
        """Trigger library scan in Komga"""
        if self.test_mode:
            return True

        try:
            if library_id:
                url = f"{self.api_url}/api/v1/libraries/{library_id}/scan"
            else:
                url = f"{self.api_url}/api/v1/libraries/scan"

            response = requests.post(url, headers=self.headers)
            response.raise_for_status()
            logger.info(f"Komga scan triggered successfully")
            return True

        except Exception as e:
            logger.error(f"Error triggering Komga scan: {e}")
            return False

    def verify_book_imported(self, series_id: str, chapter: float) -> bool:
        """Check if a chapter has been imported into Komga"""
        if self.test_mode:
            return True

        try:
            existing = self.get_existing_books(series_id)
            return chapter in existing

        except Exception as e:
            logger.error(f"Error verifying book import: {e}")
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

    def get_latest_chapter(self) -> int:
        """Get highest integer chapter number from Violet Scans"""
        if self.test_mode:
            return 100

        try:
            response = self.session.get(self.base_url, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            chapters = []
            for link in soup.select("div#chapterlist li a"):
                text = link.text.strip()
                text = re.sub(r"\s+", " ", text)
                match = re.search(r"Chapter\s+(\d+(?:\.\d+)?)", text)
                if match:
                    chapters.append(float(match.group(1)))

            integer_chapters = [int(c) for c in chapters if c == int(c)]
            if integer_chapters:
                return max(integer_chapters)
            return 0
        except Exception as e:
            logger.error(f"Error getting latest chapter from Violet Scans: {e}")
            return 0

    def download_chapter(self, chapter: int, output_path: Path) -> bool:
        """Download chapter pages and create CBZ file"""
        if self.test_mode:
            return False

        try:
            logger.info(f"Downloading Chapter {chapter} from Violet Scans")

            response = self.session.get(f"{self.base_url}", timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            chapter_link = None
            for link in soup.select("div#chapterlist li a"):
                text = link.text.strip()
                text = re.sub(r"\s+", " ", text)
                match = re.search(r"Chapter\s+" + str(chapter), text)
                if match:
                    chapter_link = link.get("href")
                    break

            if not chapter_link:
                logger.error(f"Could not find link for Chapter {chapter}")
                return False

            if not isinstance(chapter_link, str) or not chapter_link.startswith("http"):
                chapter_link = f"https://violetscans.org{chapter_link}"

            logger.info(f"Chapter URL: {chapter_link}")
            chapter_response = self.session.get(chapter_link, timeout=60)
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

            cbz_filename = output_path / f"Chapter {chapter}.cbz"

            with zipfile.ZipFile(cbz_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
                for i, img_url in enumerate(images, 1):
                    try:
                        logger.info(f"Downloading image {i + 1}/{len(images)}")
                        img_response = requests.get(img_url, timeout=30)
                        img_response.raise_for_status()

                        ext = ".jpg"
                        if "png" in str(img_url).lower():
                            ext = ".png"

                        img_data = img_response.content
                        zipf.writestr(f"page_{i:03d}{ext}", img_data)
                    except Exception as e:
                        logger.error(f"Error downloading image {i}: {e}")

            logger.info(f"Created CBZ: {cbz_filename}")
            logger.info(f"CBZ size: {cbz_filename.stat().st_size} bytes")
            return True
        except Exception as e:
            logger.error(f"Error downloading Chapter {chapter}: {e}")
            return False


class ScratchFileManager:
    """Manage scratch directory operations"""

    def __init__(self, scratch_path: Path, test_mode: bool = False):
        self.scratch_path = scratch_path
        self.test_mode = test_mode
        self.scratch_path.mkdir(parents=True, exist_ok=True)

    def write_cbz(self, chapter: int, cbz_data: bytes) -> bool:
        """Write CBZ data to scratch directory"""
        if self.test_mode:
            return False

        try:
            cbz_filename = self.scratch_path / f"Chapter {chapter}.cbz"

            with open(cbz_filename, "wb") as f:
                f.write(cbz_data)

            logger.info(f"Written to scratch: {cbz_filename}")
            return True
        except Exception as e:
            logger.error(f"Error writing to scratch: {e}")
            return False

    def list_existing_files(self) -> List[Path]:
        """List existing CBZ files in scratch directory"""
        return list(self.scratch_path.glob("*.cbz"))

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


def main() -> Dict[str, Any]:
    """
    Main handler for Fission function

    Args:
        event: Dictionary containing request/event data

    Returns:
        Dictionary with response data
    """
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

    logger.info(f"Starting Matriarch update workflow")
    logger.info(f"Series: {series_name}")
    logger.info(f"Komga API: {komga_api_url}")
    logger.info(f"Scratch path: {scratch_path}")
    logger.info(f"Dry run: {dry_run}")
    logger.info(f"Test mode: {test_mode}")

    if test_mode:
        logger.info("Test mode detected, returning early")
        return {
            "status": "success",
            "message": "Test mode - handler logic skipped",
            "test_mode": True,
        }

    if not komga_api_key:
        logger.error("KOMGA_API_KEY not provided")
        return {
            "status": "error",
            "message": "KOMGA_API_KEY is required",
        }

    komga_client = KomgaAPIClient(komga_api_url, komga_api_key, test_mode=test_mode)
    violet_scraper = VioletScansScraper(violet_url, test_mode=test_mode)
    scratch_manager = ScratchFileManager(scratch_path, test_mode=test_mode)

    try:
        logger.info("Getting series ID from Komga")
        series_id = komga_client.get_series_id(series_name)
        if not series_id:
            logger.error(f"Series not found: {series_name}")
            return {"status": "error", "message": f"Series not found: {series_name}"}

        logger.info(f"Series ID: {series_id}")
        logger.info("Getting existing chapters from Komga")
        existing_chapters = komga_client.get_existing_books(series_id)
        logger.info(f"Existing chapters: {existing_chapters}")

        latest_existing = max(existing_chapters) if existing_chapters else 0

        logger.info("Getting latest chapter from Violet Scans")
        latest_violet = violet_scraper.get_latest_chapter()
        logger.info(f"Latest Violet chapter: {latest_violet}")

        if latest_violet <= latest_existing:
            logger.info(
                f"No new chapters. Latest: {latest_violet}, Existing: {latest_existing}"
            )
            return {
                "status": "success",
                "message": f"No new chapters. Latest available: {latest_violet}",
            }

        chapters_to_download = range(int(latest_existing) + 1, latest_violet + 1)
        logger.info(f"Chapters to download: {list(chapters_to_download)}")

        if dry_run:
            logger.info("DRY RUN - would download chapters but not actually doing it")
            return {
                "status": "success",
                "message": f"Dry run complete. Would download {len(list(chapters_to_download))} chapters.",
                "chapters_to_download": list(chapters_to_download),
            }

        downloaded_chapters = []
        failed_chapters = []

        for chapter in chapters_to_download:
            logger.info(f"Processing Chapter {chapter}")
            try:
                success = violet_scraper.download_chapter(chapter, scratch_path)
                if success:
                    downloaded_chapters.append(chapter)
                    logger.info(f"Successfully downloaded Chapter {chapter}")
                else:
                    failed_chapters.append(chapter)
                    logger.error(f"Failed to download Chapter {chapter}")
            except Exception as e:
                failed_chapters.append(chapter)
                logger.error(f"Error downloading Chapter {chapter}: {e}")

        verified_chapters = []
        if downloaded_chapters:
            logger.info("Triggering Komga library scan")
            scan_success = komga_client.trigger_scan(library_id)
            if not scan_success:
                logger.warning("Komga scan failed, but chapters were downloaded")

            logger.info("Waiting a moment for Komga to process...")
            import time

            time.sleep(5)

            for chapter in downloaded_chapters:
                if komga_client.verify_book_imported(series_id, chapter):
                    verified_chapters.append(chapter)
                    cbz_file = scratch_path / f"Chapter {chapter}.cbz"
                    scratch_manager.cleanup_file(cbz_file)
                else:
                    logger.warning(f"Chapter {chapter} not yet verified in Komga")

        logger.info(f"Downloaded: {len(downloaded_chapters)} chapters")
        logger.info(f"Failed: {len(failed_chapters)} chapters")
        if downloaded_chapters:
            logger.info(f"Verified and cleaned up: {len(verified_chapters)} chapters")

        return {
            "status": "success",
            "message": f"Completed. Downloaded {len(downloaded_chapters)}, Failed {len(failed_chapters)}",
            "downloaded": downloaded_chapters,
            "failed": failed_chapters,
        }

    except Exception as e:
        logger.error(f"Handler failed with error: {e}")
        return {
            "status": "error",
            "message": f"Handler failed: {str(e)}",
        }
