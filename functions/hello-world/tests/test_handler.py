import pytest
from handler import handler


def test_handler_default():
    """Test handler with default name"""
    result = handler({})
    assert result["message"] == "Hello, World!"
    assert result["status"] == "success"


def test_handler_custom_name():
    """Test handler with custom name"""
    result = handler({"name": "Alice"})
    assert result["message"] == "Hello, Alice!"
    assert result["status"] == "success"
