import os
import sys
import tempfile
from unittest.mock import Mock, patch, MagicMock as MockMagic
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

import handler as matriarch_handler


def test_komga_api_get_series_id_success():
    """Test KomgaAPIClient.get_series_id returns series ID"""
    saved_env = {}
    if "KOMGA_API_URL" in os.environ:
        saved_env["KOMGA_API_URL"] = os.environ["KOMGA_API_URL"]
    if "KOMGA_API_KEY" in os.environ:
        saved_env["KOMGA_API_KEY"] = os.environ["KOMGA_API_KEY"]

    try:
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"

        client = matriarch_handler.KomgaAPIClient(
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
    """Test KomgaAPIClient.get_series_id returns None when not found"""
    client = matriarch_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )

    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        series_id = client.get_series_id("Non-existent Series")
        assert series_id is None, "Expected None when series not found"


def test_komga_api_get_existing_books():
    """Test KomgaAPIClient.get_existing_books returns chapter numbers"""
    client = matriarch_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=True
    )

    books = client.get_existing_books("test-series-id")
    assert books == [], f"Expected empty list, got {books}"


def test_komga_api_get_existing_books_with_chapters():
    """Test KomgaAPIClient.get_existing_books parses chapter numbers correctly"""
    client = matriarch_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )

    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.json.return_value = [
            {"name": "Chapter 1"},
            {"name": "Chapter 2"},
            {"name": "Chapter 3.5"},
            {"name": "Other Book"},
        ]
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        books = client.get_existing_books("test-series-id")
        assert len(books) == 3, f"Expected 3 chapters, got {len(books)}"
        assert 1.0 in books, "Expected Chapter 1"
        assert 2.0 in books, "Expected Chapter 2"
        assert 3.5 in books, "Expected Chapter 3.5"


def test_komga_api_trigger_scan():
    """Test KomgaAPIClient.trigger_scan returns True"""
    client = matriarch_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=True
    )

    result = client.trigger_scan()
    assert result is True, "Expected True for test mode"


def test_komga_api_verify_book_imported():
    """Test KomgaAPIClient.verify_book_imported returns True in test mode"""
    client = matriarch_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=True
    )

    result = client.verify_book_imported("test-series-id", 100)
    assert result is True, "Expected True for test mode"


def test_violet_scraper_get_latest_chapter():
    """Test VioletScansScraper.get_latest_chapter returns max chapter"""
    scraper = matriarch_handler.VioletScansScraper(
        "https://example.com", test_mode=True
    )

    latest = scraper.get_latest_chapter()
    assert latest == 100, f"Expected 100, got {latest}"


def test_violet_scraper_get_latest_chapter_no_chapters():
    """Test VioletScansScraper.get_latest_chapter returns 0 when no chapters"""
    scraper = matriarch_handler.VioletScansScraper(
        "https://example.com", test_mode=False
    )

    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.text = "<div id='chapterlist'></div>"
        mock_get.return_value = mock_response

        latest = scraper.get_latest_chapter()
        assert latest == 0, "Expected 0 when no chapters found"


def test_violet_scraper_download_chapter_test_mode():
    """Test VioletScansScraper.download_chapter returns False in test mode"""
    scraper = matriarch_handler.VioletScansScraper(
        "https://example.com", test_mode=True
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir)
        result = scraper.download_chapter(100, output_path)
        assert result is False, "Expected False for test mode"


def test_scratch_file_manager_init():
    """Test ScratchFileManager creates directory"""
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir) / "scratch"
        manager = matriarch_handler.ScratchFileManager(scratch_path, test_mode=True)

        assert manager.scratch_path == scratch_path
        assert scratch_path.exists(), "Scratch directory should be created"


def test_scratch_file_manager_write_cbz_test_mode():
    """Test ScratchFileManager.write_cbz returns False in test mode"""
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_handler.ScratchFileManager(scratch_path, test_mode=True)

        result = manager.write_cbz(100, b"fake cbz data")
        assert result is False, "Expected False for test mode"


def test_scratch_file_manager_list_existing_files():
    """Test ScratchFileManager.list_existing_files returns list of CBZ files"""
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_handler.ScratchFileManager(scratch_path, test_mode=False)

        (scratch_path / "Chapter 100.cbz").write_bytes(b"test")
        (scratch_path / "Chapter 101.cbz").write_bytes(b"test")
        (scratch_path / "other.txt").write_bytes(b"test")

        files = manager.list_existing_files()
        assert len(files) == 2, f"Expected 2 files, got {len(files)}"


def test_scratch_file_manager_cleanup_file():
    """Test ScratchFileManager.cleanup_file removes file"""
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_handler.ScratchFileManager(scratch_path, test_mode=False)

        test_file = scratch_path / "test.cbz"
        test_file.write_bytes(b"test")

        result = manager.cleanup_file(test_file)
        assert result is True, "Expected True when cleanup succeeds"
        assert not test_file.exists(), "File should be removed"


def test_scratch_file_manager_cleanup_file_not_exists():
    """Test ScratchFileManager.cleanup_file returns False when file doesn't exist"""
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_handler.ScratchFileManager(scratch_path, test_mode=False)

        test_file = scratch_path / "nonexistent.cbz"
        result = manager.cleanup_file(test_file)
        assert result is False, "Expected False when file doesn't exist"


def test_handler_series_not_found():
    """Test handler returns error when series not found in Komga"""
    saved_env = {}
    for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Non-existent Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        if "TEST_MODE" in os.environ:
            del os.environ["TEST_MODE"]

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            with patch.object(
                matriarch_handler.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    matriarch_handler.KomgaAPIClient, "get_series_id", return_value=None
                ):
                    result = matriarch_handler.handler({})

                    assert result["status"] == "error", "Expected error status"
                    assert "not found" in result["message"].lower(), (
                        "Expected 'not found' in message"
                    )
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_handler_no_new_chapters():
    """Test handler returns success when no new chapters available"""
    saved_env = {}
    for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Test Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        if "TEST_MODE" in os.environ:
            del os.environ["TEST_MODE"]

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            with patch.object(
                matriarch_handler.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    matriarch_handler.KomgaAPIClient,
                    "get_series_id",
                    return_value="test-series-id",
                ):
                    with patch.object(
                        matriarch_handler.KomgaAPIClient,
                        "get_existing_books",
                        return_value=[100, 99, 98],
                    ):
                        with patch.object(
                            matriarch_handler.VioletScansScraper,
                            "__init__",
                            lambda self, url, test_mode=False: None,
                        ):
                            with patch.object(
                                matriarch_handler.VioletScansScraper,
                                "get_latest_chapter",
                                return_value=100,
                            ):
                                result = matriarch_handler.handler({})

                                assert result["status"] == "success", (
                                    "Expected success status"
                                )
                                assert (
                                    "no new chapters" in result["message"].lower()
                                    or "Latest available" in result["message"]
                                ), (
                                    f"Expected 'no new chapters' message, got: {result['message']}"
                                )
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_handler_dry_run():
    """Test handler lists chapters in dry run mode without downloading"""
    saved_env = {}
    for key in [
        "SERIES_NAME",
        "KOMGA_API_URL",
        "KOMGA_API_KEY",
        "DRY_RUN",
        "TEST_MODE",
    ]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Test Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        os.environ["DRY_RUN"] = "true"
        if "TEST_MODE" in os.environ:
            del os.environ["TEST_MODE"]

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            with patch.object(
                matriarch_handler.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    matriarch_handler.KomgaAPIClient,
                    "get_series_id",
                    return_value="test-series-id",
                ):
                    with patch.object(
                        matriarch_handler.KomgaAPIClient,
                        "get_existing_books",
                        return_value=[98],
                    ):
                        with patch.object(
                            matriarch_handler.VioletScansScraper,
                            "__init__",
                            lambda self, url, test_mode=False: None,
                        ):
                            with patch.object(
                                matriarch_handler.VioletScansScraper,
                                "get_latest_chapter",
                                return_value=101,
                            ):
                                result = matriarch_handler.handler({})

                                assert result["status"] == "success", (
                                    "Expected success status"
                                )
                                assert "dry run" in result["message"].lower(), (
                                    "Expected 'dry run' in message"
                                )
                                assert "chapters_to_download" in result, (
                                    "Expected chapters_to_download in result"
                                )
                                assert result["chapters_to_download"] == [
                                    99,
                                    100,
                                    101,
                                ], (
                                    f"Expected [99, 100, 101], got {result['chapters_to_download']}"
                                )
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in [
            "SERIES_NAME",
            "KOMGA_API_URL",
            "KOMGA_API_KEY",
            "DRY_RUN",
            "SCRATCH_PATH",
        ]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_handler_exception_handling():
    """Test handler handles exceptions gracefully"""
    saved_env = {}
    for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Test Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        if "TEST_MODE" in os.environ:
            del os.environ["TEST_MODE"]

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            with patch.object(
                matriarch_handler.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    matriarch_handler.KomgaAPIClient,
                    "get_series_id",
                    side_effect=Exception("Test exception"),
                ):
                    result = matriarch_handler.handler({})

                    assert result["status"] == "error", "Expected error status"
                    assert (
                        "failed" in result["message"].lower()
                        or "Test exception" in result["message"]
                    ), f"Expected error message, got: {result['message']}"
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]
