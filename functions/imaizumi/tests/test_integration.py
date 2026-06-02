import os
import sys
import tempfile
import zipfile
from unittest.mock import Mock, patch
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as imaizumi


def _make_download_side_effect(scratch_path):
    def _download(chapter, output_path):
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        cbz = output_path / f"Chapter {imaizumi._chapter_str(chapter)}.cbz"
        with zipfile.ZipFile(cbz, "w") as z:
            z.writestr("page_001.webp", b"fake")
        return True

    return _download


def _import_and_move(series_id, file_paths, *args, **kwargs):
    for f in file_paths:
        p = Path(f)
        if p.exists():
            p.unlink()
    return True


def test_full_workflow_with_new_chapters():
    saved_env = {}
    for key in [
        "SERIES_NAME",
        "KOMGA_API_URL",
        "KOMGA_API_KEY",
        "MANGA_ID",
        "TEST_MODE",
    ]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Test Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        os.environ["MANGA_ID"] = "test-manga"
        if "TEST_MODE" in os.environ:
            del os.environ["TEST_MODE"]

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            with patch.object(
                imaizumi.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    imaizumi.KomgaAPIClient, "get_series_id", return_value="series-id"
                ):
                    with patch.object(
                        imaizumi.KomgaAPIClient,
                        "get_existing_books",
                        return_value=[2.0],
                    ):
                        with patch.object(
                            imaizumi.AtsumaruScraper,
                            "__init__",
                            lambda self,
                            manga_id,
                            scanlation_id="",
                            test_mode=False: None,
                        ):
                            with patch.object(
                                imaizumi.AtsumaruScraper,
                                "get_all_chapters",
                                return_value=[1.0, 2.0, 3.0],
                            ):
                                scratch = Path(temp_dir) / "imaizumi"
                                with patch.object(
                                    imaizumi.AtsumaruScraper,
                                    "download_chapter",
                                    side_effect=_make_download_side_effect(scratch),
                                ):
                                    with patch.object(
                                        imaizumi.KomgaAPIClient,
                                        "import_books",
                                        side_effect=_import_and_move,
                                    ):
                                        with patch("time.sleep"):
                                            result = imaizumi.main()

                                            assert result["status"] == "success"
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in [
            "SERIES_NAME",
            "KOMGA_API_URL",
            "KOMGA_API_KEY",
            "MANGA_ID",
            "SCRATCH_PATH",
        ]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_dry_run():
    saved_env = {}
    for key in [
        "SERIES_NAME",
        "KOMGA_API_URL",
        "KOMGA_API_KEY",
        "MANGA_ID",
        "DRY_RUN",
        "TEST_MODE",
    ]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Test Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        os.environ["MANGA_ID"] = "test-manga"
        os.environ["DRY_RUN"] = "true"
        if "TEST_MODE" in os.environ:
            del os.environ["TEST_MODE"]

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            with patch.object(
                imaizumi.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    imaizumi.KomgaAPIClient, "get_series_id", return_value="series-id"
                ):
                    with patch.object(
                        imaizumi.KomgaAPIClient,
                        "get_existing_books",
                        return_value=[1.0],
                    ):
                        with patch.object(
                            imaizumi.AtsumaruScraper,
                            "__init__",
                            lambda self,
                            manga_id,
                            scanlation_id="",
                            test_mode=False: None,
                        ):
                            with patch.object(
                                imaizumi.AtsumaruScraper,
                                "get_all_chapters",
                                return_value=[1.0, 2.0, 3.0],
                            ):
                                result = imaizumi.main()

                                assert result["status"] == "success"
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in [
            "SERIES_NAME",
            "KOMGA_API_URL",
            "KOMGA_API_KEY",
            "MANGA_ID",
            "DRY_RUN",
            "SCRATCH_PATH",
        ]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_no_missing_chapters():
    saved_env = {}
    for key in [
        "SERIES_NAME",
        "KOMGA_API_URL",
        "KOMGA_API_KEY",
        "MANGA_ID",
        "TEST_MODE",
    ]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Test Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        os.environ["MANGA_ID"] = "test-manga"
        if "TEST_MODE" in os.environ:
            del os.environ["TEST_MODE"]

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            with patch.object(
                imaizumi.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    imaizumi.KomgaAPIClient, "get_series_id", return_value="series-id"
                ):
                    with patch.object(
                        imaizumi.KomgaAPIClient,
                        "get_existing_books",
                        return_value=[1.0, 2.0, 3.0],
                    ):
                        with patch.object(
                            imaizumi.AtsumaruScraper,
                            "__init__",
                            lambda self,
                            manga_id,
                            scanlation_id="",
                            test_mode=False: None,
                        ):
                            with patch.object(
                                imaizumi.AtsumaruScraper,
                                "get_all_chapters",
                                return_value=[1.0, 2.0, 3.0],
                            ):
                                result = imaizumi.main()

                                assert result["status"] == "success"
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in [
            "SERIES_NAME",
            "KOMGA_API_URL",
            "KOMGA_API_KEY",
            "MANGA_ID",
            "SCRATCH_PATH",
        ]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_series_not_found():
    saved_env = {}
    for key in [
        "SERIES_NAME",
        "KOMGA_API_URL",
        "KOMGA_API_KEY",
        "MANGA_ID",
        "TEST_MODE",
    ]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Nonexistent Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        os.environ["MANGA_ID"] = "test-manga"
        if "TEST_MODE" in os.environ:
            del os.environ["TEST_MODE"]

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            with patch.object(
                imaizumi.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    imaizumi.KomgaAPIClient, "get_series_id", return_value=None
                ):
                    result = imaizumi.main()

                    assert result["status"] == "success"
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in [
            "SERIES_NAME",
            "KOMGA_API_URL",
            "KOMGA_API_KEY",
            "MANGA_ID",
            "SCRATCH_PATH",
        ]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_exception_handling():
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
                imaizumi.KomgaAPIClient,
                "__init__",
                lambda self, url, key, test_mode=False: None,
            ):
                with patch.object(
                    imaizumi.KomgaAPIClient,
                    "get_series_id",
                    side_effect=Exception("Test exception"),
                ):
                    result = imaizumi.main()

                    assert result["status"] == "success"
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "SCRATCH_PATH"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]
