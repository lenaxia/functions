import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as imaizumi


def test_main_returns_success_in_test_mode():
    saved_env = {}
    for key in ["SCRATCH_PATH", "TEST_MODE", "KOMGA_API_KEY"]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)
            os.environ["TEST_MODE"] = "true"
            os.environ["KOMGA_API_KEY"] = "test-key"

            result = imaizumi.main()

            assert result["status"] == "success"
            assert "message" in result
            assert result["test_mode"] is True
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SCRATCH_PATH", "TEST_MODE", "KOMGA_API_KEY"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_main_returns_error_without_api_key():
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

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)

            result = imaizumi.main()

            assert result["status"] == "error"
            assert "KOMGA_API_KEY" in result["message"]
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]
