import os
import sys
import tempfile
import zipfile
from unittest.mock import Mock, patch, MagicMock as MockMagic
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

import main as matriarch_vy_handler


def test_komga_api_get_series_id_success():
    saved_env = {}
    if "KOMGA_API_URL" in os.environ:
        saved_env["KOMGA_API_URL"] = os.environ["KOMGA_API_URL"]
    if "KOMGA_API_KEY" in os.environ:
        saved_env["KOMGA_API_KEY"] = os.environ["KOMGA_API_KEY"]

    try:
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"

        client = matriarch_vy_handler.KomgaAPIClient(
            "http://komga.example.com", "test-key-12345", test_mode=True
        )

        series_id = client.get_series_id("Test Series")
        assert series_id == "test-series-id", (
            f"Expected test-series-id, got {series_id}"
        )
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["KOMGA_API_URL", "KOMGA_API_KEY"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_komga_api_get_series_id_not_found():
    client = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )

    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.json.return_value = {"content": [], "last": True}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        series_id = client.get_series_id("Non-existent Series")
        assert series_id is None, "Expected None when series not found"


def test_komga_api_get_existing_books():
    client = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=True
    )

    books = client.get_existing_books("test-series-id")
    assert books == [], f"Expected empty list, got {books}"


def test_komga_api_get_existing_books_with_chapters():
    client = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )

    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.json.return_value = {
            "content": [
                {"name": "Chapter 1", "url": ""},
                {"name": "Chapter 2", "url": ""},
                {"name": "Chapter 3.5", "url": ""},
                {"name": "Other Book", "url": ""},
            ],
            "last": True,
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        books = client.get_existing_books("test-series-id")
        assert len(books) == 3, f"Expected 3 chapters, got {len(books)}"
        assert 1.0 in books, "Expected Chapter 1"
        assert 2.0 in books, "Expected Chapter 2"
        assert 3.5 in books, "Expected Chapter 3.5"


def test_komga_api_trigger_scan():
    client = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=True
    )

    result = client.trigger_scan()
    assert result is True, "Expected True for test mode"


def test_komga_api_import_books():
    client = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=True
    )

    result = client.import_books("test-series-id", ["/tmp/test.cbz"])
    assert result is True, "Expected True for test mode"


def test_vymanga_scraper_get_all_chapters():
    scraper = matriarch_vy_handler.VyMangaScraper("https://example.com", test_mode=True)

    chapters = scraper.get_all_chapters()
    assert len(chapters) == 100, f"Expected 100 chapters, got {len(chapters)}"
    assert chapters[0] == 1.0
    assert chapters[-1] == 100.0


def test_vymanga_scraper_get_all_chapters_error():
    scraper = matriarch_vy_handler.VyMangaScraper(
        "https://example.com", test_mode=False
    )

    with patch.object(
        scraper, "_fetch_chapter_map", side_effect=Exception("Network error")
    ):
        chapters = scraper.get_all_chapters()
        assert chapters == [], "Expected empty list on error"


def test_vymanga_scraper_download_chapter_test_mode():
    scraper = matriarch_vy_handler.VyMangaScraper("https://example.com", test_mode=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir)
        result = scraper.download_chapter(100, output_path)
        assert result is False, "Expected False for test mode"


def test_vymanga_scraper_get_highest_quality_url():
    scraper = matriarch_vy_handler.VyMangaScraper(
        "https://example.com", test_mode=False
    )

    url_with_size = "https://2.bp.blogspot.com/drive-storage/AJQWtBP=w700"
    result = scraper._get_highest_quality_url(url_with_size)
    assert result.endswith("=w0"), f"Expected URL ending with =w0, got {result}"

    url_no_size = "https://2.bp.blogspot.com/drive-storage/AJQWtBP"
    result = scraper._get_highest_quality_url(url_no_size)
    assert result == url_no_size, "URL without size param should be unchanged"


def test_scratch_file_manager_init():
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir) / "scratch"
        manager = matriarch_vy_handler.ScratchFileManager(scratch_path, test_mode=True)

        assert manager.scratch_path == scratch_path
        assert scratch_path.exists(), "Scratch directory should be created"


def test_scratch_file_manager_recover_existing():
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_vy_handler.ScratchFileManager(scratch_path, test_mode=False)

        (scratch_path / "Chapter 100.cbz").write_bytes(b"test")
        (scratch_path / "Chapter 101.cbz").write_bytes(b"test")
        (scratch_path / "Chapter 102.cbz.tmp").write_bytes(b"partial")
        (scratch_path / "other.txt").write_bytes(b"test")

        recovered = manager.recover_existing()
        assert len(recovered) == 2, f"Expected 2 chapters, got {len(recovered)}"
        assert 100.0 in recovered
        assert 101.0 in recovered
        assert not (scratch_path / "Chapter 102.cbz.tmp").exists(), (
            "Temp file should be cleaned"
        )


def test_scratch_file_manager_cleanup_file():
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_vy_handler.ScratchFileManager(scratch_path, test_mode=False)

        test_file = scratch_path / "test.cbz"
        test_file.write_bytes(b"test")

        result = manager.cleanup_file(test_file)
        assert result is True, "Expected True when cleanup succeeds"
        assert not test_file.exists(), "File should be removed"


def test_scratch_file_manager_cleanup_file_not_exists():
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_vy_handler.ScratchFileManager(scratch_path, test_mode=False)

        test_file = scratch_path / "nonexistent.cbz"
        result = manager.cleanup_file(test_file)
        assert result is False, "Expected False when file doesn't exist"


def test_run_series_not_found():
    komga = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )
    scraper = matriarch_vy_handler.VyMangaScraper(
        "https://example.com", test_mode=False
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_vy_handler.ScratchFileManager(scratch_path, test_mode=False)

        with patch.object(komga, "get_series_id", return_value=None):
            matriarch_vy_handler._run(
                komga, scraper, manager, scratch_path, "Test Series", "lib-id", False
            )


def test_run_no_missing_chapters():
    komga = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )
    scraper = matriarch_vy_handler.VyMangaScraper(
        "https://example.com", test_mode=False
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_vy_handler.ScratchFileManager(scratch_path, test_mode=False)

        with patch.object(komga, "get_series_id", return_value="series-1"):
            with patch.object(
                komga, "get_existing_books", return_value=[1.0, 2.0, 3.0]
            ):
                with patch.object(
                    scraper, "get_all_chapters", return_value=[1.0, 2.0, 3.0]
                ):
                    matriarch_vy_handler._run(
                        komga, scraper, manager, scratch_path, "Test", "lib-id", False
                    )


def test_run_dry_run():
    komga = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )
    scraper = matriarch_vy_handler.VyMangaScraper(
        "https://example.com", test_mode=False
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_vy_handler.ScratchFileManager(scratch_path, test_mode=False)

        with patch.object(komga, "get_series_id", return_value="series-1"):
            with patch.object(komga, "get_existing_books", return_value=[2.0]):
                with patch.object(
                    scraper, "get_all_chapters", return_value=[1.0, 2.0, 3.0]
                ):
                    matriarch_vy_handler._run(
                        komga, scraper, manager, scratch_path, "Test", "lib-id", True
                    )


def _make_download_side_effect(scratch_path: Path):
    def _download(chapter, output_path, *args, **kwargs):
        _cs = matriarch_vy_handler._chapter_str
        cbz = output_path / f"Chapter {_cs(chapter)}.cbz"
        output_path.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(cbz, "w") as z:
            z.writestr("page_001.jpg", b"fake")
        return True

    return _download


def _import_and_move(series_id, file_paths, *args, **kwargs):
    for f in file_paths:
        p = Path(f)
        if p.exists():
            p.unlink()
    return True


def test_run_downloads_and_imports():
    komga = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )
    scraper = matriarch_vy_handler.VyMangaScraper(
        "https://example.com", test_mode=False
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_vy_handler.ScratchFileManager(scratch_path, test_mode=False)

        with patch.object(komga, "get_series_id", return_value="series-1"):
            with patch.object(komga, "get_existing_books", return_value=[1.0]):
                with patch.object(scraper, "get_all_chapters", return_value=[1.0, 2.0]):
                    with patch.object(
                        scraper,
                        "download_chapter",
                        side_effect=_make_download_side_effect(scratch_path),
                    ):
                        with patch.object(
                            komga, "import_books", side_effect=_import_and_move
                        ) as mock_import:
                            with patch("time.sleep"):
                                matriarch_vy_handler._run(
                                    komga,
                                    scraper,
                                    manager,
                                    scratch_path,
                                    "Test",
                                    "lib-id",
                                    False,
                                )

                                mock_import.assert_called_once()
                                call_args = mock_import.call_args
                                assert call_args[0][0] == "series-1"
                                assert len(call_args[0][1]) == 1


def test_run_exception_handling():
    komga = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )
    scraper = matriarch_vy_handler.VyMangaScraper(
        "https://example.com", test_mode=False
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_vy_handler.ScratchFileManager(scratch_path, test_mode=False)

        with patch.object(
            komga, "get_series_id", side_effect=Exception("Test exception")
        ):
            matriarch_vy_handler._run(
                komga, scraper, manager, scratch_path, "Test", "lib-id", False
            )


def test_komga_api_error_handling():
    client = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )

    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("Network error")
        series_id = client.get_series_id("Test Series")
        assert series_id is None, "Expected None on error"
