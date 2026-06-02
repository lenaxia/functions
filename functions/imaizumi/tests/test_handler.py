import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as imaizumi


def test_handler_test_mode():
    saved_env = {}
    for key in [
        "SERIES_NAME",
        "KOMGA_API_URL",
        "KOMGA_API_KEY",
        "KOMGA_LIBRARY_ID",
        "MANGA_ID",
        "SCANLATION_ID",
        "DRY_RUN",
        "TEST_MODE",
    ]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Test Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        os.environ["KOMGA_LIBRARY_ID"] = "test-library-id"
        os.environ["MANGA_ID"] = "test-manga-id"
        os.environ["DRY_RUN"] = "true"
        os.environ["TEST_MODE"] = "true"

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            result = imaizumi.main()

            assert result["status"] == "success"
            assert result["test_mode"] is True
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in [
            "SERIES_NAME",
            "KOMGA_API_URL",
            "KOMGA_API_KEY",
            "KOMGA_LIBRARY_ID",
            "MANGA_ID",
            "SCANLATION_ID",
            "DRY_RUN",
            "TEST_MODE",
        ]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_handler_missing_api_key():
    saved_env = {}
    for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Test Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        if "KOMGA_API_KEY" in os.environ:
            del os.environ["KOMGA_API_KEY"]
        if "TEST_MODE" in os.environ:
            del os.environ["TEST_MODE"]

        result = imaizumi.main()

        assert result["status"] == "error"
        assert "KOMGA_API_KEY" in result["message"]
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]
