import os
import sys
import tempfile
from unittest.mock import Mock, patch
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as imaizumi


def test_komga_api_get_series_id_test_mode():
    client = imaizumi.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=True
    )
    assert client.get_series_id("Test Series") == "test-series-id"


def test_komga_api_get_series_id_success():
    client = imaizumi.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )
    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.json.return_value = {"content": [{"id": "series-123"}]}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        assert client.get_series_id("Test Series") == "series-123"


def test_komga_api_get_series_id_not_found():
    client = imaizumi.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )
    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.json.return_value = {"content": []}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        assert client.get_series_id("Nonexistent") is None


def test_komga_api_get_existing_books_test_mode():
    client = imaizumi.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=True
    )
    assert client.get_existing_books("test-series-id") == []


def test_komga_api_get_existing_books_with_chapters():
    client = imaizumi.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )
    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.json.return_value = {
            "content": [
                {"name": "Chapter 1", "url": ""},
                {"name": "Chapter 2", "url": ""},
                {"name": "Chapter 3.5", "url": ""},
            ],
            "last": True,
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        books = client.get_existing_books("test-series-id")
        assert len(books) == 3
        assert 1.0 in books
        assert 2.0 in books
        assert 3.5 in books


def test_komga_api_trigger_scan_test_mode():
    client = imaizumi.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=True
    )
    assert client.trigger_scan() is True


def test_komga_api_import_books_test_mode():
    client = imaizumi.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=True
    )
    assert client.import_books("series-id", ["/tmp/test.cbz"]) is True


def test_komga_api_error_handling():
    client = imaizumi.KomgaAPIClient(
        "http://komga.example.com", "test-key", test_mode=False
    )
    with patch("requests.get", side_effect=Exception("Network error")):
        assert client.get_series_id("Test") is None


def test_atsumaru_scraper_get_all_chapters_test_mode():
    scraper = imaizumi.AtsumaruScraper("test-id", test_mode=True)
    chapters = scraper.get_all_chapters()
    assert len(chapters) == 100
    assert chapters[0] == 1.0
    assert chapters[-1] == 100.0


def test_atsumaru_scraper_download_chapter_test_mode():
    scraper = imaizumi.AtsumaruScraper("test-id", test_mode=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        result = scraper.download_chapter(1.0, Path(temp_dir))
        assert result is False


def test_atsumaru_scraper_get_all_chapters():
    scraper = imaizumi.AtsumaruScraper("test-manga", test_mode=False)
    with patch.object(scraper, "session") as mock_session:
        mock_response = Mock()
        mock_response.json.return_value = {
            "chapters": [
                {"number": 1, "id": "ch1", "scanlationMangaId": "scan1"},
                {"number": 2, "id": "ch2", "scanlationMangaId": "scan1"},
                {"number": 3, "id": "ch3", "scanlationMangaId": "scan1"},
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        chapters = scraper.get_all_chapters()
        assert chapters == [1.0, 2.0, 3.0]


def test_atsumaru_scraper_scanlation_filter():
    scraper = imaizumi.AtsumaruScraper(
        "test-manga", scanlation_id="scan1", test_mode=False
    )
    with patch.object(scraper, "session") as mock_session:
        mock_response = Mock()
        mock_response.json.return_value = {
            "chapters": [
                {"number": 1, "id": "ch1a", "scanlationMangaId": "scan1"},
                {"number": 1, "id": "ch1b", "scanlationMangaId": "scan2"},
                {"number": 2, "id": "ch2a", "scanlationMangaId": "scan1"},
                {"number": 2, "id": "ch2b", "scanlationMangaId": "scan2"},
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        chapters = scraper.get_all_chapters()
        assert len(chapters) == 2
        assert chapters == [1.0, 2.0]


def test_atsumaru_scraper_chapter_map_caching():
    scraper = imaizumi.AtsumaruScraper("test-manga", test_mode=False)
    with patch.object(scraper, "session") as mock_session:
        mock_response = Mock()
        mock_response.json.return_value = {
            "chapters": [
                {"number": 1, "id": "ch1", "scanlationMangaId": "scan1"},
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        scraper.get_all_chapters()
        scraper.get_all_chapters()

        assert mock_session.get.call_count == 1


def test_scratch_file_manager_init():
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir) / "scratch"
        manager = imaizumi.ScratchFileManager(scratch_path, test_mode=True)

        assert manager.scratch_path == scratch_path
        assert scratch_path.exists()


def test_scratch_file_manager_recover_existing():
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = imaizumi.ScratchFileManager(scratch_path, test_mode=False)

        (scratch_path / "Chapter 1.cbz").write_bytes(b"test")
        (scratch_path / "Chapter 2.cbz").write_bytes(b"test")
        (scratch_path / "Chapter 2.cbz.tmp").write_bytes(b"partial")

        recovered = manager.recover_existing()
        assert recovered == [1.0, 2.0]
        assert not (scratch_path / "Chapter 2.cbz.tmp").exists()


def test_scratch_file_manager_cleanup():
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = imaizumi.ScratchFileManager(scratch_path, test_mode=False)

        test_file = scratch_path / "test.cbz"
        test_file.write_bytes(b"test")

        assert manager.cleanup_file(test_file) is True
        assert not test_file.exists()


def test_scratch_file_manager_cleanup_test_mode():
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch_path = Path(temp_dir)
        manager = imaizumi.ScratchFileManager(scratch_path, test_mode=True)

        test_file = scratch_path / "test.cbz"
        test_file.write_bytes(b"test")

        assert manager.cleanup_file(test_file) is False
        assert test_file.exists()


def test_chapter_str():
    assert imaizumi._chapter_str(1.0) == "1"
    assert imaizumi._chapter_str(1.5) == "1.5"
    assert imaizumi._chapter_str(49.0) == "49"
