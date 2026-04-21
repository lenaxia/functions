import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

import handler as matriarch_handler


def test_handler_success():
    """Test that handler returns success status with test mode"""
    saved_env = {}
    for key in ["SCRATCH_PATH", "TEST_MODE"]:
        if key in os.environ:
            saved_env[key] = os.environ[key]

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SCRATCH_PATH"] = str(temp_dir)
            os.environ["TEST_MODE"] = "true"

            result = matriarch_handler.handler({})

            assert result["status"] == "success", (
                f"Expected success, got {result['status']}"
            )
            assert "message" in result, "Expected message in result"
            assert result["test_mode"] == True, "Expected test_mode to be True"
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SCRATCH_PATH", "TEST_MODE"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]


def test_missing_api_key():
    """Test that handler requires KOMGA_API_KEY"""
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

        result = matriarch_handler.handler({})

        assert result["status"] == "error", "Expected error status"
        assert "KOMGA_API_KEY" in result["message"], "Expected API key error message"
    finally:
        for key, value in saved_env.items():
            os.environ[key] = value
        for key in ["SERIES_NAME", "KOMGA_API_URL", "KOMGA_API_KEY", "TEST_MODE"]:
            if key not in saved_env and key in os.environ:
                del os.environ[key]
