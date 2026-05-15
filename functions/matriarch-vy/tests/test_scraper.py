"""
TDD tests for VyMangaScraper.

These tests define the expected behaviour of the scraper using realistic
HTML fixtures that mirror the actual vymanga.com/summonersky page structure.
Tests are written to fail first, then verified against the implementation.
"""

import os
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import handler as h

# ---------------------------------------------------------------------------
# HTML fixtures — representative slices of real page structure
# ---------------------------------------------------------------------------

MANGA_PAGE_HTML = """
<html><body>
<div class="div-chapter">
  <div class="list">
    <a class="list-group-item list-group-item-action list-chapter"
       href="https://aovheroes.com/rds/br/rdsd?data=CHAP217"
       id="chapter-217">
      <span>Chapter 217 : Ch 217</span>
      <p class="text-right font-italic small">May 10, 2026</p>
    </a>
    <a class="list-group-item list-group-item-action list-chapter"
       href="https://aovheroes.com/rds/br/rdsd?data=CHAP216"
       id="chapter-216">
      <span>Chapter 216 : Ch 216</span>
      <p class="text-right font-italic small">May 06, 2026</p>
    </a>
    <a class="list-group-item list-group-item-action list-chapter"
       href="https://aovheroes.com/rds/br/rdsd?data=CHAP1"
       id="chapter-1">
      <span>Chapter 1</span>
      <p class="text-right font-italic small">Jan 01, 2024</p>
    </a>
  </div>
</div>
</body></html>
"""

READING_PAGE_HTML = """
<html><body>
<div class="reading-content">
  <img class="d-block w-100 lozad"
       data-src="https://2.bp.blogspot.com/drive-storage/PAGE1=w700"
       src="https://vymanga.com/web/img/loading.gif">
  <img class="d-block w-100 lozad"
       data-src="https://2.bp.blogspot.com/drive-storage/PAGE2=w700"
       src="https://vymanga.com/web/img/loading.gif">
  <img class="d-block w-100 lozad"
       data-src="https://2.bp.blogspot.com/drive-storage/PAGE3=w700"
       src="https://vymanga.com/web/img/loading.gif">
</div>
</body></html>
"""

READING_PAGE_NO_IMAGES_HTML = """
<html><body>
<div class="reading-content">
</div>
</body></html>
"""


def _mock_response(text="", status=200):
    r = Mock()
    r.text = text
    r.status_code = status
    r.raise_for_status = Mock()
    r.headers = {"Content-Type": "image/jpeg"}
    r.content = b"FAKEIMGDATA"
    return r


# ---------------------------------------------------------------------------
# _fetch_chapter_links
# ---------------------------------------------------------------------------


class TestFetchChapterLinks:
    def test_parses_chapter_ids_and_hrefs(self):
        """Should return a dict mapping int chapter number -> href string."""
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(return_value=_mock_response(MANGA_PAGE_HTML))

        links = scraper._fetch_chapter_links()

        assert links[217] == "https://aovheroes.com/rds/br/rdsd?data=CHAP217"
        assert links[216] == "https://aovheroes.com/rds/br/rdsd?data=CHAP216"
        assert links[1] == "https://aovheroes.com/rds/br/rdsd?data=CHAP1"

    def test_returns_only_integer_chapter_ids(self):
        """Non-integer chapter ids (e.g. chapter-1-5) should be excluded."""
        html = """
        <html><body>
          <a class="list-group-item list-group-item-action list-chapter"
             href="https://example.com/ch5" id="chapter-5"><span>Ch 5</span></a>
          <a class="list-group-item list-group-item-action list-chapter"
             href="https://example.com/ch4-5" id="chapter-4-5"><span>Ch 4.5</span></a>
        </body></html>
        """
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(return_value=_mock_response(html))

        links = scraper._fetch_chapter_links()
        assert 5 in links
        assert "chapter-4-5" not in str(links)

    def test_caches_result_after_first_fetch(self):
        """The manga index page should only be fetched once even if called multiple times."""
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(return_value=_mock_response(MANGA_PAGE_HTML))

        scraper._fetch_chapter_links()
        scraper._fetch_chapter_links()
        scraper._fetch_chapter_links()

        assert scraper.session.get.call_count == 1

    def test_cache_shared_between_get_latest_and_download(self):
        """get_latest_chapter then download_chapter should only hit the index once."""
        scraper = h.VyMangaScraper("https://example.com")

        manga_response = _mock_response(MANGA_PAGE_HTML)
        reading_response = _mock_response(READING_PAGE_HTML)
        img_response = _mock_response()

        scraper.session.get = Mock(
            side_effect=[
                manga_response,  # first call: index page
                reading_response,  # second call: reading page for ch 217
                img_response,  # image 1
                img_response,  # image 2
                img_response,  # image 3
            ]
        )

        latest = scraper.get_latest_chapter()
        assert latest == 217

        with tempfile.TemporaryDirectory() as tmp:
            scraper.download_chapter(217, Path(tmp))

        # Only 1 index page fetch, then reading page, then 3 images = 5 total
        assert scraper.session.get.call_count == 5

    def test_empty_page_returns_empty_dict(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(return_value=_mock_response("<html></html>"))

        links = scraper._fetch_chapter_links()
        assert links == {}

    def test_network_error_raises(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(side_effect=Exception("network error"))

        try:
            scraper._fetch_chapter_links()
            assert False, "Expected exception to propagate"
        except Exception as e:
            assert "network error" in str(e)


# ---------------------------------------------------------------------------
# get_latest_chapter
# ---------------------------------------------------------------------------


class TestGetLatestChapter:
    def test_returns_highest_integer_chapter(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(return_value=_mock_response(MANGA_PAGE_HTML))

        assert scraper.get_latest_chapter() == 217

    def test_returns_zero_when_no_chapters(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(return_value=_mock_response("<html></html>"))

        assert scraper.get_latest_chapter() == 0

    def test_returns_100_in_test_mode(self):
        scraper = h.VyMangaScraper("https://example.com", test_mode=True)
        assert scraper.get_latest_chapter() == 100

    def test_returns_zero_on_network_error(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(side_effect=Exception("timeout"))

        assert scraper.get_latest_chapter() == 0


# ---------------------------------------------------------------------------
# _resolve_chapter_url
# ---------------------------------------------------------------------------


class TestResolveChapterUrl:
    def test_returns_href_for_known_chapter(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(return_value=_mock_response(MANGA_PAGE_HTML))

        url = scraper._resolve_chapter_url(216)
        assert url == "https://aovheroes.com/rds/br/rdsd?data=CHAP216"

    def test_returns_none_for_unknown_chapter(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(return_value=_mock_response(MANGA_PAGE_HTML))

        url = scraper._resolve_chapter_url(999)
        assert url is None

    def test_returns_none_on_network_error(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(side_effect=Exception("timeout"))

        url = scraper._resolve_chapter_url(217)
        assert url is None


# ---------------------------------------------------------------------------
# _get_highest_quality_url
# ---------------------------------------------------------------------------


class TestGetHighestQualityUrl:
    def test_replaces_w_size_suffix_with_w0(self):
        scraper = h.VyMangaScraper("https://example.com")
        url = "https://2.bp.blogspot.com/drive-storage/SOMEHASH=w700"
        assert (
            scraper._get_highest_quality_url(url)
            == "https://2.bp.blogspot.com/drive-storage/SOMEHASH=w0"
        )

    def test_replaces_any_w_size(self):
        scraper = h.VyMangaScraper("https://example.com")
        for size in ["w1", "w100", "w1200", "w9999"]:
            url = f"https://example.com/img=={size}"
            result = scraper._get_highest_quality_url(url)
            assert result.endswith("=w0"), f"Expected =w0 for {size}, got {result}"

    def test_leaves_url_unchanged_if_no_size_param(self):
        scraper = h.VyMangaScraper("https://example.com")
        url = "https://example.com/image.jpg"
        assert scraper._get_highest_quality_url(url) == url

    def test_does_not_modify_other_query_params(self):
        scraper = h.VyMangaScraper("https://example.com")
        url = "https://example.com/img?foo=bar=w400"
        result = scraper._get_highest_quality_url(url)
        assert "foo=bar" in result
        assert result.endswith("=w0")


# ---------------------------------------------------------------------------
# download_chapter
# ---------------------------------------------------------------------------


class TestDownloadChapter:
    def test_creates_cbz_with_correct_page_count(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(MANGA_PAGE_HTML),  # index
                _mock_response(READING_PAGE_HTML),  # reading page
                _mock_response(),  # page 1 img
                _mock_response(),  # page 2 img
                _mock_response(),  # page 3 img
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = scraper.download_chapter(217, out)

            assert result is True
            cbz = out / "Chapter 217.cbz"
            assert cbz.exists()
            with zipfile.ZipFile(cbz) as z:
                assert len(z.namelist()) == 3

    def test_pages_named_sequentially(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(MANGA_PAGE_HTML),
                _mock_response(READING_PAGE_HTML),
                _mock_response(),
                _mock_response(),
                _mock_response(),
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            scraper.download_chapter(217, Path(tmp))
            cbz = Path(tmp) / "Chapter 217.cbz"
            with zipfile.ZipFile(cbz) as z:
                names = sorted(z.namelist())
                assert names[0] == "page_001.jpg"
                assert names[1] == "page_002.jpg"
                assert names[2] == "page_003.jpg"

    def test_image_urls_have_w0_quality(self):
        """Verifies _get_highest_quality_url is applied — =w700 becomes =w0."""
        scraper = h.VyMangaScraper("https://example.com")
        captured_urls = []

        original_get = scraper.session.get

        def capturing_get(url, **kwargs):
            captured_urls.append(url)
            if url == "https://example.com":
                return _mock_response(MANGA_PAGE_HTML)
            elif "aovheroes" in url:
                return _mock_response(READING_PAGE_HTML)
            else:
                return _mock_response()

        scraper.session.get = capturing_get

        with tempfile.TemporaryDirectory() as tmp:
            scraper.download_chapter(217, Path(tmp))

        image_urls = [u for u in captured_urls if "blogspot" in u]
        assert len(image_urls) == 3
        for url in image_urls:
            assert url.endswith("=w0"), f"Expected =w0 quality, got: {url}"
            assert "=w700" not in url

    def test_returns_false_for_unknown_chapter(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(return_value=_mock_response(MANGA_PAGE_HTML))

        with tempfile.TemporaryDirectory() as tmp:
            result = scraper.download_chapter(999, Path(tmp))
            assert result is False

    def test_returns_false_when_no_images_on_reading_page(self):
        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(MANGA_PAGE_HTML),
                _mock_response(READING_PAGE_NO_IMAGES_HTML),
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = scraper.download_chapter(217, Path(tmp))
            assert result is False

    def test_returns_false_in_test_mode(self):
        scraper = h.VyMangaScraper("https://example.com", test_mode=True)
        with tempfile.TemporaryDirectory() as tmp:
            result = scraper.download_chapter(217, Path(tmp))
            assert result is False

    def test_detects_png_content_type(self):
        png_response = _mock_response()
        png_response.headers = {"Content-Type": "image/png"}

        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(MANGA_PAGE_HTML),
                _mock_response(READING_PAGE_HTML),
                png_response,
                _mock_response(),
                _mock_response(),
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            scraper.download_chapter(217, Path(tmp))
            cbz = Path(tmp) / "Chapter 217.cbz"
            with zipfile.ZipFile(cbz) as z:
                names = z.namelist()
                assert "page_001.png" in names

    def test_continues_on_individual_image_failure(self):
        """A failed image download should not abort the whole chapter."""
        bad_response = _mock_response()
        bad_response.raise_for_status = Mock(side_effect=Exception("403 Forbidden"))

        scraper = h.VyMangaScraper("https://example.com")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(MANGA_PAGE_HTML),
                _mock_response(READING_PAGE_HTML),
                bad_response,  # page 1 fails
                _mock_response(),  # page 2 ok
                _mock_response(),  # page 3 ok
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = scraper.download_chapter(217, Path(tmp))
            # CBZ is still created with the pages that succeeded
            assert result is True
            cbz = Path(tmp) / "Chapter 217.cbz"
            with zipfile.ZipFile(cbz) as z:
                assert len(z.namelist()) == 2
