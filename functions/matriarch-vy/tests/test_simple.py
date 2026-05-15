import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

import handler as matriarch_vy_handler


def test_handler_initializes_correctly():
    saved_env = {}
    for key in [
        "SERIES_NAME",
        "KOMGA_API_URL",
        "KOMGA_API_KEY",
        "KOMGA_LIBRARY_ID",
        "VYMANGA_URL",
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
        os.environ["VYMANGA_URL"] = "https://example.com"
        os.environ["DRY_RUN"] = "true"
        os.environ["TEST_MODE"] = "true"

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            result = matriarch_vy_handler.handler({})

            assert result["status"] == "success", f"Expected success"
            assert "message" in result
            assert result["test_mode"] == True
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in [
            "SERIES_NAME",
            "KOMGA_API_URL",
            "KOMGA_API_KEY",
            "KOMGA_LIBRARY_ID",
            "VYMANGA_URL",
            "DRY_RUN",
            "TEST_MODE",
        ]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_handler_with_api_key():
    saved_env = {}
    for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        os.environ["SERIES_NAME"] = "Test Series"
        os.environ["KOMGA_API_URL"] = "http://komga.example.com"
        os.environ["KOMGA_API_KEY"] = "test-key-12345"
        os.environ["TEST_MODE"] = "true"

        result = matriarch_vy_handler.handler({})

        assert result["status"] == "success", "Expected success status"
        assert result["test_mode"] == True
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]
