import os
import logging
import re
import zipfile
import time
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from typing import Optional, List, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
            series_list = response.json()

            if not series_list or len(series_list) == 0:
                logger.error(f"Series not found: {series_name}")
                return None

            return series_list[0].get("id")

        except Exception as e:
            logger.error(f"Error getting series ID: {e}")
            return None

    def get_existing_books(self, series_id: str) -> List[int]:
        if self.test_mode:
            return []

        try:
            response = requests.get(
                f"{self.api_url}/api/v1/series/{series_id}/books", headers=self.headers
            )
            response.raise_for_status()
            books = response.json()

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
        if self.test_mode:
            return True

        try:
            existing = self.get_existing_books(series_id)
            return chapter in existing

        except Exception as e:
            logger.error(f"Error verifying book import: {e}")
            return False


class VyMangaScraper:
    IMAGE_SIZE_ORIGINAL = "=w0"

    def __init__(self, base_url: str, test_mode: bool = False):
        self.base_url = base_url
        self.test_mode = test_mode
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
        )
        self._chapter_links: Optional[Dict[int, str]] = None

    def _fetch_chapter_links(self) -> Dict[int, str]:
        """Fetch and cache chapter number -> redirect URL mapping from the manga page."""
        if self._chapter_links is not None:
            return self._chapter_links

        response = self.session.get(self.base_url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        links: Dict[int, str] = {}
        for link in soup.select("a.list-group-item.list-chapter"):
            link_id = str(link.get("id", ""))
            match = re.search(r"chapter-(\d+)", link_id)
            if match:
                num = int(match.group(1))
                href = link.get("href")
                if href:
                    links[num] = str(href)

        self._chapter_links = links
        return links

    def _resolve_chapter_url(self, chapter: int) -> Optional[str]:
        try:
            links = self._fetch_chapter_links()
            url = links.get(chapter)
            if not url:
                logger.error(f"Could not find link for Chapter {chapter}")
            return url
        except Exception as e:
            logger.error(f"Error finding chapter {chapter} link: {e}")
            return None

    def _get_highest_quality_url(self, img_url: str) -> str:
        match = re.search(r"=w\d+$", img_url)
        if match:
            return re.sub(r"=w\d+$", self.IMAGE_SIZE_ORIGINAL, img_url)
        return img_url

    def get_latest_chapter(self) -> int:
        if self.test_mode:
            return 100

        try:
            links = self._fetch_chapter_links()
            integer_chapters = list(links.keys())
            if integer_chapters:
                return max(integer_chapters)
            return 0
        except Exception as e:
            logger.error(f"Error getting latest chapter from VyManga: {e}")
            return 0

    def download_chapter(self, chapter: int, output_path: Path) -> bool:
        if self.test_mode:
            return False

        try:
            logger.info(f"Downloading Chapter {chapter} from VyManga")

            chapter_url = self._resolve_chapter_url(chapter)
            if not chapter_url:
                return False

            logger.info(
                f"Resolved chapter URL, following redirects for Chapter {chapter}"
            )
            response = self.session.get(chapter_url, timeout=60, allow_redirects=True)
            response.raise_for_status()

            chapter_soup = BeautifulSoup(response.text, "html.parser")

            images = []
            for img in chapter_soup.select("img.d-block.w-100.lozad"):
                img_url = img.get("data-src") or img.get("src")
                if img_url:
                    if not img_url.startswith("http"):
                        img_url = f"https:{img_url}"
                    img_url = self._get_highest_quality_url(img_url)
                    images.append(img_url)

            if not images:
                logger.error(f"No images found for Chapter {chapter}")
                return False

            logger.info(f"Found {len(images)} images (original quality)")
            output_path.mkdir(parents=True, exist_ok=True)

            cbz_filename = output_path / f"Chapter {chapter}.cbz"

            with zipfile.ZipFile(cbz_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
                for i, img_url in enumerate(images, 1):
                    try:
                        logger.info(f"Downloading image {i}/{len(images)}")
                        img_response = self.session.get(img_url, timeout=30)
                        img_response.raise_for_status()

                        content_type = img_response.headers.get("Content-Type", "")
                        if "png" in content_type.lower():
                            ext = ".png"
                        elif "webp" in content_type.lower():
                            ext = ".webp"
                        elif "gif" in content_type.lower():
                            ext = ".gif"
                        else:
                            ext = ".jpg"

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
    def __init__(self, scratch_path: Path, test_mode: bool = False):
        self.scratch_path = scratch_path
        self.test_mode = test_mode
        self.scratch_path.mkdir(parents=True, exist_ok=True)

    def write_cbz(self, chapter: int, cbz_data: bytes) -> bool:
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
        return list(self.scratch_path.glob("*.cbz"))

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


def handler(event: Dict[str, Any]) -> Dict[str, Any]:
    scratch_base_path = os.getenv("SCRATCH_PATH", "/mnt/scratch")
    scratch_path = Path(scratch_base_path) / "matriarch-vy"
    series_name = os.getenv(
        "SERIES_NAME", "I'll Be The Matriarch In This Life (VyManga)"
    )
    komga_api_url = os.getenv(
        "KOMGA_API_URL", "http://komga.media.svc.cluster.local:8080"
    )
    komga_api_key = os.getenv("KOMGA_API_KEY", "")
    library_id = os.getenv("KOMGA_LIBRARY_ID", "")
    vymanga_url = os.getenv(
        "VYMANGA_URL",
        "https://vymanga.com/manga/ill-be-the-matriarch-in-this-life-9+++es5l",
    )
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
    test_mode = os.getenv("TEST_MODE", "false").lower() == "true"

    logger.info(f"Starting Matriarch-VY update workflow")
    logger.info(f"Series: {series_name}")
    logger.info(f"Komga API: {komga_api_url}")
    logger.info(f"Scratch path: {scratch_path}")
    logger.info(f"VyManga URL: {vymanga_url}")
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
    vymanga_scraper = VyMangaScraper(vymanga_url, test_mode=test_mode)
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

        logger.info("Getting latest chapter from VyManga")
        latest_vy = vymanga_scraper.get_latest_chapter()
        logger.info(f"Latest VyManga chapter: {latest_vy}")

        if latest_vy <= latest_existing:
            logger.info(
                f"No new chapters. Latest: {latest_vy}, Existing: {latest_existing}"
            )
            return {
                "status": "success",
                "message": f"No new chapters. Latest available: {latest_vy}",
            }

        chapters_to_download = range(int(latest_existing) + 1, latest_vy + 1)
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
                success = vymanga_scraper.download_chapter(chapter, scratch_path)
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
