"""Unit tests for KomgaAPIClient, VioletScansScraper, ScratchFileManager.

These tests construct each class directly and mock its outbound HTTP /
filesystem calls. They DON'T touch main(); for end-to-end coverage see
test_integration.py.
"""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import main


# ───────────────────────────── KomgaAPIClient ──────────────────────────────


class TestKomgaAPIClient:
    """Behaviour of the Komga REST client wrapper."""

    def test_test_mode_get_series_id_returns_sentinel(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=True)
        assert client.get_series_id("Any") == "test-series-id"

    def test_test_mode_get_existing_books_returns_empty(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=True)
        assert client.get_existing_books("any") == []

    def test_test_mode_trigger_scan_returns_true(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=True)
        assert client.trigger_scan() is True

    def test_test_mode_import_books_returns_true(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=True)
        assert client.import_books("s", ["/tmp/x.cbz"]) is True

    def test_get_series_id_returns_first_match(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.get") as mock_get:
            mock_get.return_value = Mock(
                raise_for_status=Mock(),
                json=Mock(
                    return_value={"content": [{"id": "abc-123", "name": "Test"}]}
                ),
            )
            assert client.get_series_id("Test") == "abc-123"

    def test_get_series_id_returns_none_when_empty(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.get") as mock_get:
            mock_get.return_value = Mock(
                raise_for_status=Mock(),
                json=Mock(return_value={"content": []}),
            )
            assert client.get_series_id("Missing") is None

    def test_get_series_id_returns_none_on_network_error(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.get", side_effect=Exception("network down")):
            assert client.get_series_id("Test") is None

    def test_get_series_id_strips_trailing_slash_from_url(self):
        """Constructor normalises trailing slash so URL construction is uniform."""
        client = main.KomgaAPIClient("http://k/", "key", test_mode=False)
        assert client.api_url == "http://k"

    def test_get_existing_books_parses_chapter_from_url_stem(self):
        """Chapter regex applies to the file URL's stem first.

        Komga sometimes reformats display names but the URL stem stays as
        the original filename. The function must prefer the URL stem.
        """
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.get") as mock_get:
            mock_get.return_value = Mock(
                raise_for_status=Mock(),
                json=Mock(
                    return_value={
                        "content": [
                            {"url": "/foo/Chapter 47.cbz", "name": "anything"},
                            {"url": "/foo/Chapter 048.cbz", "name": "renamed"},
                        ],
                        "last": True,
                    }
                ),
            )
            books = client.get_existing_books("s")
            assert set(books) == {47.0, 48.0}

    def test_get_existing_books_falls_back_to_name(self):
        """If URL has no chapter pattern, fall back to the display name."""
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.get") as mock_get:
            mock_get.return_value = Mock(
                raise_for_status=Mock(),
                json=Mock(
                    return_value={
                        "content": [{"url": "", "name": "Chapter 99"}],
                        "last": True,
                    }
                ),
            )
            assert client.get_existing_books("s") == [99.0]

    def test_get_existing_books_handles_decimal_chapters(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.get") as mock_get:
            mock_get.return_value = Mock(
                raise_for_status=Mock(),
                json=Mock(
                    return_value={
                        "content": [
                            {"url": "/Chapter 1.cbz", "name": ""},
                            {"url": "/Chapter 1.5.cbz", "name": ""},
                            {"url": "/Chapter 2.cbz", "name": ""},
                        ],
                        "last": True,
                    }
                ),
            )
            assert client.get_existing_books("s") == [1.0, 1.5, 2.0]

    def test_get_existing_books_paginates(self):
        """Pages are fetched until `last: True`."""
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        responses = [
            Mock(
                raise_for_status=Mock(),
                json=Mock(
                    return_value={
                        "content": [{"url": "/Chapter 1.cbz", "name": ""}],
                        "last": False,
                    }
                ),
            ),
            Mock(
                raise_for_status=Mock(),
                json=Mock(
                    return_value={
                        "content": [{"url": "/Chapter 2.cbz", "name": ""}],
                        "last": True,
                    }
                ),
            ),
        ]
        with patch("requests.get", side_effect=responses):
            books = client.get_existing_books("s")
        assert books == [1.0, 2.0]

    def test_get_existing_books_returns_empty_on_error(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.get", side_effect=Exception("boom")):
            assert client.get_existing_books("s") == []

    def test_get_existing_books_deduplicates(self):
        """If the same chapter appears twice in Komga (legacy state), dedup."""
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.get") as mock_get:
            mock_get.return_value = Mock(
                raise_for_status=Mock(),
                json=Mock(
                    return_value={
                        "content": [
                            {"url": "/Chapter 5.cbz", "name": ""},
                            {
                                "url": "/Chapter 005.cbz",
                                "name": "",
                            },  # same num, zero-padded
                        ],
                        "last": True,
                    }
                ),
            )
            assert client.get_existing_books("s") == [5.0]

    def test_trigger_scan_no_library_id_hits_global_endpoint(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.post") as mock_post:
            mock_post.return_value = Mock(raise_for_status=Mock())
            assert client.trigger_scan() is True
            assert mock_post.call_args[0][0] == "http://k/api/v1/libraries/scan"

    def test_trigger_scan_with_library_id_targets_library(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.post") as mock_post:
            mock_post.return_value = Mock(raise_for_status=Mock())
            client.trigger_scan("lib-42")
            assert mock_post.call_args[0][0] == "http://k/api/v1/libraries/lib-42/scan"

    def test_trigger_scan_returns_false_on_error(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.post", side_effect=Exception("503")):
            assert client.trigger_scan() is False

    def test_import_books_builds_payload(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.post") as mock_post:
            mock_post.return_value = Mock(raise_for_status=Mock())
            assert client.import_books(
                "series-1", [Path("/tmp/a.cbz"), Path("/tmp/b.cbz")]
            )
            payload = mock_post.call_args.kwargs["json"]
            assert payload["copyMode"] == "MOVE"
            assert len(payload["books"]) == 2
            assert payload["books"][0]["seriesId"] == "series-1"

    def test_import_books_returns_false_on_error(self):
        client = main.KomgaAPIClient("http://k", "key", test_mode=False)
        with patch("requests.post", side_effect=Exception("409")):
            assert client.import_books("s", [Path("/tmp/x.cbz")]) is False

    def test_headers_include_api_key(self):
        client = main.KomgaAPIClient("http://k", "abc123", test_mode=False)
        assert client.headers["X-API-Key"] == "abc123"


# ──────────────────────── VioletScansScraper ───────────────────────────────


class TestVioletScansScraper:
    """Basic class behaviour. Detailed parsing tests live in test_scraper.py."""

    def test_test_mode_get_all_chapters_returns_1_to_100(self):
        scraper = main.VioletScansScraper(
            "https://violetscans.org/comics/x/", test_mode=True
        )
        chapters = scraper.get_all_chapters()
        assert len(chapters) == 100
        assert chapters[0] == 1.0
        assert chapters[-1] == 100.0

    def test_test_mode_download_returns_false(self):
        scraper = main.VioletScansScraper(
            "https://violetscans.org/comics/x/", test_mode=True
        )
        with tempfile.TemporaryDirectory() as tmp:
            assert scraper.download_chapter(1.0, Path(tmp)) is False

    def test_get_all_chapters_returns_empty_on_network_error(self):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(side_effect=Exception("DNS failure"))
        assert scraper.get_all_chapters() == []

    def test_user_agent_set(self):
        """Violet Scans needs a non-default UA to avoid bot blocks."""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        ua = scraper.session.headers["User-Agent"]
        assert "Mozilla" in ua and "Chrome" in ua

    def test_caches_chapter_map(self):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper._chapter_map = {1.0: "https://example.com/1"}
        # If cache exists, _fetch_chapter_map returns it without hitting the wire.
        scraper.session.get = Mock(side_effect=Exception("should not be called"))
        assert scraper._fetch_chapter_map() == {1.0: "https://example.com/1"}


# ─────────────────────────── ScratchFileManager ────────────────────────────


class TestScratchFileManager:
    def test_init_creates_scratch_directory(self, tmp_path):
        scratch = tmp_path / "new-scratch"
        assert not scratch.exists()
        main.ScratchFileManager(scratch)
        assert scratch.is_dir()

    def test_recover_existing_finds_complete_cbz_files(self, tmp_path):
        (tmp_path / "Chapter 1.cbz").write_bytes(b"a")
        (tmp_path / "Chapter 47.cbz").write_bytes(b"b")
        (tmp_path / "Chapter 2.5.cbz").write_bytes(b"c")
        mgr = main.ScratchFileManager(tmp_path)
        assert mgr.recover_existing() == [1.0, 2.5, 47.0]

    def test_recover_existing_removes_tmp_partials(self, tmp_path):
        (tmp_path / "Chapter 1.cbz").write_bytes(b"a")
        partial = tmp_path / "Chapter 99.cbz.tmp"
        partial.write_bytes(b"partial")

        mgr = main.ScratchFileManager(tmp_path)
        result = mgr.recover_existing()

        assert result == [1.0]
        assert not partial.exists()

    def test_recover_existing_ignores_unrelated_files(self, tmp_path):
        (tmp_path / "Chapter 1.cbz").write_bytes(b"a")
        (tmp_path / "notes.txt").write_bytes(b"junk")
        (tmp_path / "random.cbz").write_bytes(b"no chapter pattern")  # no number
        mgr = main.ScratchFileManager(tmp_path)
        assert mgr.recover_existing() == [1.0]

    def test_recover_existing_returns_empty_when_dir_empty(self, tmp_path):
        mgr = main.ScratchFileManager(tmp_path)
        assert mgr.recover_existing() == []

    def test_cleanup_file_removes_existing(self, tmp_path):
        target = tmp_path / "Chapter 1.cbz"
        target.write_bytes(b"x")
        mgr = main.ScratchFileManager(tmp_path)
        assert mgr.cleanup_file(target) is True
        assert not target.exists()

    def test_cleanup_file_returns_false_for_missing(self, tmp_path):
        mgr = main.ScratchFileManager(tmp_path)
        assert mgr.cleanup_file(tmp_path / "missing.cbz") is False

    def test_cleanup_file_test_mode_no_op(self, tmp_path):
        target = tmp_path / "Chapter 1.cbz"
        target.write_bytes(b"x")
        mgr = main.ScratchFileManager(tmp_path, test_mode=True)
        assert mgr.cleanup_file(target) is False
        assert target.exists(), "test_mode should not delete files"


# ──────────────────────────── _chapter_str ────────────────────────────────


class TestChapterStr:
    def test_integer_chapter_has_no_decimal(self):
        assert main._chapter_str(47.0) == "47"

    def test_decimal_chapter_preserves_fraction(self):
        assert main._chapter_str(47.5) == "47.5"

    def test_zero(self):
        assert main._chapter_str(0.0) == "0"

    def test_negative_handled(self):
        """Defensive: negative chapter numbers shouldn't crash. Real-world
        chapter numbers are always non-negative, but the formatter shouldn't
        explode if a malformed Komga entry slips through."""
        assert main._chapter_str(-1.0) == "-1"
