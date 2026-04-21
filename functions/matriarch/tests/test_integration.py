import os
import sys
import tempfile
from unittest.mock import Mock, patch, MagicMock as MockMagic
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

import handler as matriarch_handler


def test_handler_with_new_chapters():
    """Test handler downloads and verifies new chapters"""
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
                                return_value=99,
                            ):
                                with patch.object(
                                    matriarch_handler.VioletScansScraper,
                                    "download_chapter",
                                    return_value=True,
                                ):
                                    with patch.object(
                                        matriarch_handler.KomgaAPIClient,
                                        "trigger_scan",
                                        return_value=True,
                                    ):
                                        with patch.object(
                                            matriarch_handler.KomgaAPIClient,
                                            "verify_book_imported",
                                            return_value=True,
                                        ):
                                            with patch("time.sleep"):
                                                result = matriarch_handler.handler({})

                                                assert result["status"] == "success", (
                                                    "Expected success status"
                                                )
                                                assert result["downloaded"] == [99], (
                                                    f"Expected [99], got {result['downloaded']}"
                                                )
                                                assert result["failed"] == [], (
                                                    f"Expected [], got {result['failed']}"
                                                )
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_handler_with_download_failures():
    """Test handler handles download failures gracefully"""
    saved_env = {}
    for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    def mock_komga_init(self, api_url, api_key, test_mode=False):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.test_mode = test_mode
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    def mock_violet_init(self, base_url, test_mode=False):
        self.base_url = base_url
        self.test_mode = test_mode

    try:
        os.environ["SERIES_NAME"] = "Test Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        if "TEST_MODE" in os.environ:
            del os.environ["TEST_MODE"]

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            with patch.object(
                matriarch_handler.KomgaAPIClient, "__init__", mock_komga_init
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
                            mock_violet_init,
                        ):
                            with patch.object(
                                matriarch_handler.VioletScansScraper,
                                "get_latest_chapter",
                                return_value=100,
                            ):
                                with patch.object(
                                    matriarch_handler.VioletScansScraper,
                                    "download_chapter",
                                    side_effect=[True, False, True],
                                ):
                                    result = matriarch_handler.handler({})

                                    assert result["status"] == "success", (
                                        "Expected success status"
                                    )
                                    assert 99 in result["downloaded"], (
                                        "Expected 99 in downloaded"
                                    )
                                    assert 100 in result["failed"], (
                                        "Expected 100 in failed"
                                    )
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_handler_with_partial_verification():
    """Test handler handles partial import verification"""
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
                                return_value=100,
                            ):
                                with patch.object(
                                    matriarch_handler.VioletScansScraper,
                                    "download_chapter",
                                    return_value=True,
                                ):
                                    with patch.object(
                                        matriarch_handler.KomgaAPIClient,
                                        "trigger_scan",
                                        return_value=True,
                                    ):
                                        with patch.object(
                                            matriarch_handler.KomgaAPIClient,
                                            "verify_book_imported",
                                            side_effect=[True, False, True],
                                        ):
                                            with patch("time.sleep"):
                                                result = matriarch_handler.handler({})

                                                assert result["status"] == "success", (
                                                    "Expected success status"
                                                )
                                                assert result["downloaded"] == [
                                                    99,
                                                    100,
                                                ], (
                                                    f"Expected [99, 100], got {result['downloaded']}"
                                                )
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_komga_api_error_handling():
    """Test KomgaAPIClient handles request errors"""
    client = matriarch_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )

    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("Network error")
        series_id = client.get_series_id("Test Series")
        assert series_id is None, "Expected None on error"


def test_violet_scraper_error_handling():
    """Test VioletScansScraper handles request errors"""
    scraper = matriarch_handler.VioletScansScraper(
        "https://example.com", test_mode=False
    )

    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("Network error")
        latest = scraper.get_latest_chapter()
        assert latest == 0, "Expected 0 on error"


def test_scratch_file_manager_write_cbz_success():
    """Test ScratchFileManager.write_cbz writes file successfully"""
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = matriarch_handler.ScratchFileManager(scratch_path, test_mode=False)

        cbz_data = b"fake cbz data"
        result = manager.write_cbz(100, cbz_data)

        assert result is True, "Expected True on successful write"
        expected_file = scratch_path / "Chapter 100.cbz"
        assert expected_file.exists(), "CBZ file should exist"
        assert expected_file.read_bytes() == cbz_data, "CBZ data should match"


def test_scratch_file_manager_error_handling():
    """Test ScratchFileManager handles write errors"""
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir) / "readonly"
        scratch_path.mkdir()
        scratch_path.chmod(0o444)

        manager = matriarch_handler.ScratchFileManager(scratch_path, test_mode=False)

        result = manager.write_cbz(100, b"test")
        assert result is False, "Expected False on write error"

        scratch_path.chmod(0o755)


def test_handler_integration_full_workflow():
    """Test handler executes full workflow successfully"""
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
                        return_value=[],
                    ):
                        with patch.object(
                            matriarch_handler.VioletScansScraper,
                            "__init__",
                            lambda self, url, test_mode=False: None,
                        ):
                            with patch.object(
                                matriarch_handler.VioletScansScraper,
                                "get_latest_chapter",
                                return_value=2,
                            ):
                                with patch.object(
                                    matriarch_handler.VioletScansScraper,
                                    "download_chapter",
                                    return_value=True,
                                ):
                                    with patch.object(
                                        matriarch_handler.KomgaAPIClient,
                                        "trigger_scan",
                                        return_value=True,
                                    ):
                                        with patch.object(
                                            matriarch_handler.KomgaAPIClient,
                                            "verify_book_imported",
                                            return_value=True,
                                        ):
                                            with patch("time.sleep"):
                                                result = matriarch_handler.handler({})

                                                assert result["status"] == "success"
                                                assert result["downloaded"] == [1, 2]
                                                assert result["failed"] == []
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]
