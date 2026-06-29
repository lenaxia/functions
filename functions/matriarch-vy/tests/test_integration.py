import os
import sys
import tempfile
from unittest.mock import Mock, patch, MagicMock as MockMagic
from pathlib import Path
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

import main as matriarch_vy_handler


def _create_fake_cbz(path: Path, name: str):
    path.mkdir(parents=True, exist_ok=True)
    cbz = path / name
    with zipfile.ZipFile(cbz, "w") as z:
        z.writestr("page_001.jpg", b"fake")
    return cbz


def _make_download_side_effect(scratch_path: Path):
    def _download(chapter, output_path, *args, **kwargs):
        _chapter_str = matriarch_vy_handler._chapter_str
        cbz = output_path / f"Chapter {_chapter_str(chapter)}.cbz"
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


def _import_fail(series_id, file_paths, *args, **kwargs):
    return False


def test_handler_with_new_chapters():
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
            scratch = Path(temp_dir) / "matriarch-vy"

            with patch.object(
                matriarch_vy_handler.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    matriarch_vy_handler.KomgaAPIClient,
                    "get_series_id",
                    return_value="test-series-id",
                ):
                    with patch.object(
                        matriarch_vy_handler.KomgaAPIClient,
                        "get_existing_books",
                        return_value=[98],
                    ):
                        with patch.object(
                            matriarch_vy_handler.VyMangaScraper,
                            "__init__",
                            lambda self, url, test_mode=False: None,
                        ):
                            with patch.object(
                                matriarch_vy_handler.VyMangaScraper,
                                "get_all_chapters",
                                return_value=[98, 99],
                            ):
                                with patch.object(
                                    matriarch_vy_handler.VyMangaScraper,
                                    "download_chapter",
                                    side_effect=_make_download_side_effect(scratch),
                                ):
                                    with patch.object(
                                        matriarch_vy_handler.KomgaAPIClient,
                                        "import_books",
                                        side_effect=_import_and_move,
                                    ) as mock_import:
                                        with patch("time.sleep"):
                                            result = matriarch_vy_handler.main()

                                            assert result["status"] == "success", (
                                                "Expected success status"
                                            )
                                            mock_import.assert_called_once()
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_handler_with_download_failures():
    saved_env = {}
    for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    def mock_komga_init(self, api_url, api_key, test_mode=False):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.test_mode = test_mode
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    def mock_vymanga_init(self, base_url, test_mode=False):
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
            scratch = Path(temp_dir) / "matriarch-vy"

            def download_some(chapter, output_path, *args, **kwargs):
                if chapter == 99.0:
                    return False
                return _make_download_side_effect(scratch)(chapter, output_path)

            with patch.object(
                matriarch_vy_handler.KomgaAPIClient, "__init__", mock_komga_init
            ):
                with patch.object(
                    matriarch_vy_handler.KomgaAPIClient,
                    "get_series_id",
                    return_value="test-series-id",
                ):
                    with patch.object(
                        matriarch_vy_handler.KomgaAPIClient,
                        "get_existing_books",
                        return_value=[97],
                    ):
                        with patch.object(
                            matriarch_vy_handler.VyMangaScraper,
                            "__init__",
                            mock_vymanga_init,
                        ):
                            with patch.object(
                                matriarch_vy_handler.VyMangaScraper,
                                "get_all_chapters",
                                return_value=[97, 98, 99, 100],
                            ):
                                with patch.object(
                                    matriarch_vy_handler.VyMangaScraper,
                                    "download_chapter",
                                    side_effect=download_some,
                                ):
                                    with patch.object(
                                        matriarch_vy_handler.KomgaAPIClient,
                                        "import_books",
                                        side_effect=_import_and_move,
                                    ) as mock_import:
                                        result = matriarch_vy_handler.main()

                                        assert result["status"] == "success", (
                                            "Expected success status"
                                        )
                                        mock_import.assert_called_once()
                                        imported_files = mock_import.call_args[0][1]
                                        assert len(imported_files) == 2, (
                                            f"Expected 2 imported files (98 and 100 succeeded), got {len(imported_files)}"
                                        )
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_handler_recovers_existing_cbz():
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
            scratch = Path(temp_dir) / "matriarch-vy"
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            _create_fake_cbz(scratch, "Chapter 99.cbz")

            with patch.object(
                matriarch_vy_handler.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    matriarch_vy_handler.KomgaAPIClient,
                    "get_series_id",
                    return_value="test-series-id",
                ):
                    with patch.object(
                        matriarch_vy_handler.KomgaAPIClient,
                        "get_existing_books",
                        return_value=[98],
                    ):
                        with patch.object(
                            matriarch_vy_handler.VyMangaScraper,
                            "__init__",
                            lambda self, url, test_mode=False: None,
                        ):
                            with patch.object(
                                matriarch_vy_handler.VyMangaScraper,
                                "get_all_chapters",
                                return_value=[98, 99, 100],
                            ):
                                with patch.object(
                                    matriarch_vy_handler.VyMangaScraper,
                                    "download_chapter",
                                    side_effect=_make_download_side_effect(scratch),
                                ):
                                    with patch.object(
                                        matriarch_vy_handler.KomgaAPIClient,
                                        "import_books",
                                        side_effect=_import_and_move,
                                    ) as mock_import:
                                        with patch("time.sleep"):
                                            result = matriarch_vy_handler.main()

                                            assert result["status"] == "success"
                                            mock_import.assert_called_once()
                                            imported_files = mock_import.call_args[0][1]
                                            assert len(imported_files) == 2
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_handler_import_fallback_to_scan():
    saved_env = {}
    for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Test Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        os.environ["KOMGA_LIBRARY_ID"] = "test-lib-id"
        if "TEST_MODE" in os.environ:
            del os.environ["TEST_MODE"]

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)
            scratch = Path(temp_dir) / "matriarch-vy"

            with patch.object(
                matriarch_vy_handler.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    matriarch_vy_handler.KomgaAPIClient,
                    "get_series_id",
                    return_value="test-series-id",
                ):
                    with patch.object(
                        matriarch_vy_handler.KomgaAPIClient,
                        "get_existing_books",
                        return_value=[98],
                    ):
                        with patch.object(
                            matriarch_vy_handler.VyMangaScraper,
                            "__init__",
                            lambda self, url, test_mode=False: None,
                        ):
                            with patch.object(
                                matriarch_vy_handler.VyMangaScraper,
                                "get_all_chapters",
                                return_value=[98, 99],
                            ):
                                with patch.object(
                                    matriarch_vy_handler.VyMangaScraper,
                                    "download_chapter",
                                    side_effect=_make_download_side_effect(scratch),
                                ):
                                    with patch.object(
                                        matriarch_vy_handler.KomgaAPIClient,
                                        "import_books",
                                        side_effect=_import_fail,
                                    ):
                                        with patch.object(
                                            matriarch_vy_handler.KomgaAPIClient,
                                            "trigger_scan",
                                            return_value=True,
                                        ) as mock_scan:
                                            result = matriarch_vy_handler.main()

                                            assert result["status"] == "success"
                                            mock_scan.assert_called_once()
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in [
            "SERIES_NAME",
            "KOMGA_API_URL",
            "KOMGA_API_KEY",
            "KOMGA_LIBRARY_ID",
            "SCRATCH_PATH",
        ]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_komga_api_error_handling():
    client = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )

    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("Network error")
        series_id = client.get_series_id("Test Series")
        assert series_id is None, "Expected None on error"


def test_vymanga_scraper_error_handling():
    scraper = matriarch_vy_handler.VyMangaScraper(
        "https://example.com", test_mode=False
    )

    with patch.object(
        scraper, "_fetch_chapter_map", side_effect=Exception("Network error")
    ):
        chapters = scraper.get_all_chapters()
        assert chapters == [], "Expected empty list on error"


def test_handler_integration_full_workflow():
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
            scratch = Path(temp_dir) / "matriarch-vy"

            with patch.object(
                matriarch_vy_handler.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    matriarch_vy_handler.KomgaAPIClient,
                    "get_series_id",
                    return_value="test-series-id",
                ):
                    with patch.object(
                        matriarch_vy_handler.KomgaAPIClient,
                        "get_existing_books",
                        return_value=[],
                    ):
                        with patch.object(
                            matriarch_vy_handler.VyMangaScraper,
                            "__init__",
                            lambda self, url, test_mode=False: None,
                        ):
                            with patch.object(
                                matriarch_vy_handler.VyMangaScraper,
                                "get_all_chapters",
                                return_value=[1.0, 2.0],
                            ):
                                with patch.object(
                                    matriarch_vy_handler.VyMangaScraper,
                                    "download_chapter",
                                    side_effect=_make_download_side_effect(scratch),
                                ):
                                    with patch.object(
                                        matriarch_vy_handler.KomgaAPIClient,
                                        "import_books",
                                        side_effect=_import_and_move,
                                    ) as mock_import:
                                        with patch("time.sleep"):
                                            result = matriarch_vy_handler.main()

                                            assert result["status"] == "success"
                                            mock_import.assert_called_once()
                                            imported_files = mock_import.call_args[0][1]
                                            assert len(imported_files) == 2
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


# ─────────────────────────── batching tests ────────────────────────────────


def _build_clients_and_scratch(tmp_dir: str):
    """Build real clients with their network methods unstubbed (we patch
    per-test)."""
    scratch = Path(tmp_dir) / "matriarch-vy-test"
    scratch.mkdir(parents=True)
    komga = matriarch_vy_handler.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )
    scraper = matriarch_vy_handler.VyMangaScraper(
        "https://example.com/series/x", test_mode=False
    )
    manager = matriarch_vy_handler.ScratchFileManager(scratch, test_mode=False)
    return komga, scraper, manager, scratch


class TestRunBatching:
    """Verify that import happens in batches of `batch_size`."""

    def test_batches_imports_when_backlog_exceeds_batch_size(self):
        """13 missing chapters with batch_size=5 → 3 import calls (5+5+3)."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with patch.object(komga, "get_series_id", return_value="s-1"), \
                 patch.object(komga, "get_existing_books", return_value=[]), \
                 patch.object(scraper, "get_all_chapters",
                              return_value=[float(c) for c in range(1, 14)]), \
                 patch.object(scraper, "download_chapter",
                              side_effect=_make_download_side_effect(scratch)), \
                 patch.object(komga, "import_books",
                              side_effect=_import_and_move) as imp, \
                 patch("time.sleep"):
                matriarch_vy_handler._run(
                    komga, scraper, mgr, scratch, "Test", "lib", False,
                    batch_size=5,
                )

            assert imp.call_count == 3
            batch_sizes = [len(call.args[1]) for call in imp.call_args_list]
            assert batch_sizes == [5, 5, 3], batch_sizes

    def test_single_batch_when_backlog_fits(self):
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with patch.object(komga, "get_series_id", return_value="s-1"), \
                 patch.object(komga, "get_existing_books", return_value=[]), \
                 patch.object(scraper, "get_all_chapters",
                              return_value=[1.0, 2.0, 3.0]), \
                 patch.object(scraper, "download_chapter",
                              side_effect=_make_download_side_effect(scratch)), \
                 patch.object(komga, "import_books",
                              side_effect=_import_and_move) as imp, \
                 patch("time.sleep"):
                matriarch_vy_handler._run(
                    komga, scraper, mgr, scratch, "Test", "lib", False,
                    batch_size=5,
                )

            assert imp.call_count == 1
            assert len(imp.call_args.args[1]) == 3

    def test_recovered_files_flushed_before_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            for c in [1, 2, 3, 4, 5]:
                _create_fake_cbz(scratch, f"Chapter {c}.cbz")

            with patch.object(komga, "get_series_id", return_value="s-1"), \
                 patch.object(komga, "get_existing_books", return_value=[]), \
                 patch.object(scraper, "get_all_chapters",
                              return_value=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]), \
                 patch.object(scraper, "download_chapter",
                              side_effect=_make_download_side_effect(scratch)), \
                 patch.object(komga, "import_books",
                              side_effect=_import_and_move) as imp, \
                 patch("time.sleep"):
                matriarch_vy_handler._run(
                    komga, scraper, mgr, scratch, "Test", "lib", False,
                    batch_size=5,
                )

            batch_sizes = [len(call.args[1]) for call in imp.call_args_list]
            assert batch_sizes == [5, 2], batch_sizes

    def test_max_downloads_per_run_caps_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with patch.object(komga, "get_series_id", return_value="s-1"), \
                 patch.object(komga, "get_existing_books", return_value=[]), \
                 patch.object(scraper, "get_all_chapters",
                              return_value=[float(c) for c in range(1, 21)]), \
                 patch.object(scraper, "download_chapter",
                              side_effect=_make_download_side_effect(scratch)) as dl, \
                 patch.object(komga, "import_books",
                              side_effect=_import_and_move), \
                 patch("time.sleep"):
                matriarch_vy_handler._run(
                    komga, scraper, mgr, scratch, "Test", "lib", False,
                    batch_size=5, max_downloads_per_run=7,
                )

            assert dl.call_count == 7
