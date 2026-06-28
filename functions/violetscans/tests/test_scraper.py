"""Site-specific HTML/JS parsing tests for VioletScansScraper.

These fixtures match the actual structure of violetscans.org pages as of
the function's release. If the site DOM changes, these tests will fail
and signal the need for a scraper update.

The HTML samples are minimal-but-representative — they include only the
structural elements the parser cares about, so tests are not coupled to
unrelated DOM noise.
"""

import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import Mock

import main

# ───────────────────────────── HTML fixtures ───────────────────────────────


# Chapter list page. Selector under test: div#chapterlist li a
CHAPTER_LIST_HTML = """
<html><body>
<div id="chapterlist">
  <ul class="clstyle">
    <li data-num="3">
      <a href="https://violetscans.org/chapter-3/">
        <div class="chapternum">Chapter 3</div>
        <div class="chapterdate">2 days ago</div>
      </a>
    </li>
    <li data-num="2.5">
      <a href="https://violetscans.org/chapter-2-5/">
        <div class="chapternum">Chapter 2.5</div>
      </a>
    </li>
    <li data-num="2">
      <a href="/chapter-2/">
        <div class="chapternum">Chapter 2</div>
      </a>
    </li>
    <li data-num="1">
      <a href="https://violetscans.org/chapter-1/">
        <div class="chapternum">Chapter 1</div>
      </a>
    </li>
  </ul>
</div>
</body></html>
"""


# Reader page primary path: ts_reader.run() with a JSON config.
# The real site emits this with trailing commas, which the parser strips.
def _reader_html_with_ts_reader(image_urls):
    images_json = ",\n".join(f'"{u}"' for u in image_urls)
    return f"""
<html><body>
<script>
ts_reader.run({{
    "sources":[
        {{"source":"Main Server","images":[{images_json},]}},
    ],
    "prevUrl":"https://violetscans.org/prev/",
    "nextUrl":"https://violetscans.org/next/",
}});
</script>
</body></html>
"""


# Reader fallback path: HTML <img> tags inside div.reading-content
READER_FALLBACK_HTML = """
<html><body>
<div class="reading-content">
  <img src="https://violetscans.org/manga/series/01.jpg">
  <img src="https://violetscans.org/manga/series/02.jpg">
  <img src="https://violetscans.org/manga/series/03.jpg">
</div>
</body></html>
"""


# Fallback with protocol-relative + path-relative URLs.
READER_FALLBACK_RELATIVE_HTML = """
<html><body>
<div class="reading-content">
  <img src="//cdn.example.com/manga/x/01.jpg">
  <img src="/manga/series/02.jpg">
</div>
</body></html>
"""


# Fallback via #readerarea (second selector tried).
READER_FALLBACK_READERAREA_HTML = """
<html><body>
<div id="readerarea">
  <img src="https://violetscans.org/manga/series/01.jpg">
</div>
</body></html>
"""


# Fallback via data-src lazy loading.
READER_FALLBACK_DATA_SRC_HTML = """
<html><body>
<img data-src="https://violetscans.org/manga/series/01.jpg" src="placeholder.gif">
</body></html>
"""


READER_NO_IMAGES_HTML = (
    """<html><body><div class="reading-content"></div></body></html>"""
)


def _mock_response(text="", content_type="image/jpeg", content=b"IMG"):
    r = Mock()
    r.text = text
    r.raise_for_status = Mock()
    r.headers = {"Content-Type": content_type}
    r.content = content
    return r


# ──────────────────── _fetch_chapter_map / get_all_chapters ────────────────


class TestFetchChapterMap:
    def test_parses_chapters_and_urls(self):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(return_value=_mock_response(CHAPTER_LIST_HTML))

        chapter_map = scraper._fetch_chapter_map()

        assert chapter_map[1.0] == "https://violetscans.org/chapter-1/"
        assert chapter_map[3.0] == "https://violetscans.org/chapter-3/"

    def test_includes_decimal_chapters(self):
        """Half-chapter (.5) entries are real on violetscans — must be kept."""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(return_value=_mock_response(CHAPTER_LIST_HTML))

        chapter_map = scraper._fetch_chapter_map()
        assert 2.5 in chapter_map

    def test_normalises_relative_hrefs(self):
        """A relative href like /chapter-2/ becomes a full violetscans.org URL.

        Without this, the scraper would later fail to fetch the chapter page.
        """
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(return_value=_mock_response(CHAPTER_LIST_HTML))

        chapter_map = scraper._fetch_chapter_map()
        assert chapter_map[2.0] == "https://violetscans.org/chapter-2/"

    def test_caches_after_first_call(self):
        """The chapter map is fetched once per scraper instance."""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(return_value=_mock_response(CHAPTER_LIST_HTML))

        scraper._fetch_chapter_map()
        scraper._fetch_chapter_map()
        scraper._fetch_chapter_map()

        assert scraper.session.get.call_count == 1

    def test_empty_chapter_list_returns_empty_map(self):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            return_value=_mock_response(
                "<html><body><div id='chapterlist'></div></body></html>"
            )
        )

        assert scraper._fetch_chapter_map() == {}

    def test_no_chapterlist_div_returns_empty_map(self):
        """Defensive: page entirely missing the expected wrapper doesn't crash."""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(return_value=_mock_response("<html></html>"))

        assert scraper._fetch_chapter_map() == {}

    def test_skips_links_without_chapter_text(self):
        """Pinned promos / navigation links inside the chapter div are ignored."""
        html = """
        <html><body>
        <div id="chapterlist">
          <ul>
            <li><a href="/about/"><div>About this series</div></a></li>
            <li><a href="/chapter-1/"><div class="chapternum">Chapter 1</div></a></li>
          </ul>
        </div>
        </body></html>
        """
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(return_value=_mock_response(html))

        chapter_map = scraper._fetch_chapter_map()
        assert chapter_map == {1.0: "https://violetscans.org/chapter-1/"}

    def test_skips_links_without_href(self):
        html = """
        <html><body>
        <div id="chapterlist">
          <ul><li><a><div class="chapternum">Chapter 99</div></a></li></ul>
        </div>
        </body></html>
        """
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(return_value=_mock_response(html))
        assert scraper._fetch_chapter_map() == {}

    def test_network_error_propagates(self):
        """_fetch_chapter_map does not swallow errors — get_all_chapters does."""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(side_effect=Exception("502 Bad Gateway"))

        try:
            scraper._fetch_chapter_map()
            assert False, "expected exception"
        except Exception as e:
            assert "502" in str(e)


class TestGetAllChapters:
    def test_returns_sorted(self):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(return_value=_mock_response(CHAPTER_LIST_HTML))

        assert scraper.get_all_chapters() == [1.0, 2.0, 2.5, 3.0]

    def test_returns_empty_on_error(self):
        """Network errors are caught here, not in _fetch_chapter_map."""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(side_effect=Exception("DNS"))

        assert scraper.get_all_chapters() == []


# ────────────────────────── download_chapter (ts_reader primary) ───────────


class TestDownloadChapterTsReader:
    """The primary code path: extract image URLs from ts_reader.run() JSON."""

    def test_extracts_images_from_ts_reader_json(self, tmp_path):
        images = [
            "https://violetscans.org/manga/x/01.jpg",
            "https://violetscans.org/manga/x/02.jpg",
        ]
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(_reader_html_with_ts_reader(images)),
            ]
        )

        # Patch the per-image requests.get used for downloading bytes.
        with _patch_image_download():
            assert scraper.download_chapter(1.0, tmp_path) is True

        cbz = tmp_path / "Chapter 1.cbz"
        assert cbz.exists()
        with zipfile.ZipFile(cbz) as z:
            assert len(z.namelist()) == 2

    def test_handles_trailing_commas_in_ts_reader_json(self, tmp_path):
        """The site's ts_reader.run() payload contains trailing commas
        (technically invalid JSON). The parser strips them; without that
        cleanup `json.loads` would fail and silently fall back to selectors."""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        # The fixture deliberately has trailing commas — see _reader_html_with_ts_reader
        html = _reader_html_with_ts_reader(["https://violetscans.org/manga/x/01.jpg"])
        assert ",]" in html or ",}" in html, "fixture must contain trailing commas"

        scraper.session.get = Mock(
            side_effect=[_mock_response(CHAPTER_LIST_HTML), _mock_response(html)]
        )

        with _patch_image_download():
            assert scraper.download_chapter(1.0, tmp_path) is True

    def test_concatenates_images_across_sources(self, tmp_path):
        """ts_reader.sources can contain multiple servers; all images merge."""
        html = """
<html><body><script>
ts_reader.run({
    "sources":[
        {"source":"S1","images":["https://x/manga/a.jpg","https://x/manga/b.jpg"]},
        {"source":"S2","images":["https://x/manga/c.jpg"]},
    ],
});
</script></body></html>
"""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[_mock_response(CHAPTER_LIST_HTML), _mock_response(html)]
        )

        with _patch_image_download():
            scraper.download_chapter(1.0, tmp_path)

        with zipfile.ZipFile(tmp_path / "Chapter 1.cbz") as z:
            assert len(z.namelist()) == 3

    def test_falls_back_when_ts_reader_json_is_malformed(self, tmp_path):
        """If ts_reader.run() is present but unparseable, the fallback
        selector logic should still succeed."""
        html = """
<html><body>
<script>ts_reader.run({this is not json at all});</script>
<div class="reading-content">
  <img src="https://violetscans.org/manga/x/01.jpg">
</div>
</body></html>
"""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[_mock_response(CHAPTER_LIST_HTML), _mock_response(html)]
        )

        with _patch_image_download():
            assert scraper.download_chapter(1.0, tmp_path) is True
        with zipfile.ZipFile(tmp_path / "Chapter 1.cbz") as z:
            assert len(z.namelist()) == 1


# ─────────────────────── download_chapter (HTML fallback) ──────────────────


class TestDownloadChapterFallback:
    def test_reading_content_selector(self, tmp_path):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(READER_FALLBACK_HTML),
            ]
        )

        with _patch_image_download():
            scraper.download_chapter(1.0, tmp_path)

        with zipfile.ZipFile(tmp_path / "Chapter 1.cbz") as z:
            assert len(z.namelist()) == 3

    def test_readerarea_selector(self, tmp_path):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(READER_FALLBACK_READERAREA_HTML),
            ]
        )

        with _patch_image_download():
            assert scraper.download_chapter(1.0, tmp_path) is True

    def test_data_src_selector(self, tmp_path):
        """Lazy-loaded images need data-src extraction."""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(READER_FALLBACK_DATA_SRC_HTML),
            ]
        )

        with _patch_image_download() as get_mock:
            scraper.download_chapter(1.0, tmp_path)

        # Confirm we actually downloaded from the data-src URL, not placeholder.gif
        called_urls = [c.args[0] for c in get_mock.call_args_list]
        assert any("01.jpg" in u for u in called_urls)
        assert not any("placeholder" in u for u in called_urls)

    def test_normalises_protocol_relative_urls(self, tmp_path):
        """`//cdn.example.com/...` → `https://cdn.example.com/...`."""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(READER_FALLBACK_RELATIVE_HTML),
            ]
        )

        with _patch_image_download() as get_mock:
            scraper.download_chapter(1.0, tmp_path)

        urls = [c.args[0] for c in get_mock.call_args_list]
        assert all(u.startswith("http") for u in urls), urls
        assert any("https://cdn.example.com/manga" in u for u in urls)
        assert any("https://violetscans.org/manga" in u for u in urls)


# ───────────────────────── download_chapter (errors) ───────────────────────


class TestDownloadChapterErrors:
    def test_unknown_chapter_returns_false(self, tmp_path):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(return_value=_mock_response(CHAPTER_LIST_HTML))

        # Chapter 999 isn't in the list; downloader must short-circuit.
        assert scraper.download_chapter(999.0, tmp_path) is False

    def test_no_images_returns_false_no_cbz_created(self, tmp_path):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(READER_NO_IMAGES_HTML),
            ]
        )

        assert scraper.download_chapter(1.0, tmp_path) is False
        assert list(tmp_path.glob("*.cbz")) == []

    def test_chapter_page_network_error_returns_false(self, tmp_path):
        """If the reader page can't be fetched, return False and clean up."""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")

        def get(url, **kwargs):
            if url == "https://violetscans.org/comics/x/":
                return _mock_response(CHAPTER_LIST_HTML)
            raise Exception("connection refused")

        scraper.session.get = Mock(side_effect=get)

        assert scraper.download_chapter(1.0, tmp_path) is False
        assert list(tmp_path.glob("*.cbz.tmp")) == [], (
            "temp file should be cleaned up on failure"
        )

    def test_continues_on_individual_image_failure(self, tmp_path):
        """One bad image shouldn't fail the whole chapter — pages are best-effort."""
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(
                    _reader_html_with_ts_reader(
                        [
                            "https://violetscans.org/manga/x/01.jpg",
                            "https://violetscans.org/manga/x/BAD.jpg",
                            "https://violetscans.org/manga/x/03.jpg",
                        ]
                    )
                ),
            ]
        )

        def fake_get(url, **kwargs):
            if "BAD" in url:
                resp = Mock()
                resp.raise_for_status = Mock(side_effect=Exception("403"))
                return resp
            return _mock_response(content=b"IMG")

        with _patch_image_download(side_effect=fake_get):
            assert scraper.download_chapter(1.0, tmp_path) is True

        # CBZ created with the two good pages.
        with zipfile.ZipFile(tmp_path / "Chapter 1.cbz") as z:
            names = z.namelist()
        assert len(names) == 2

    def test_temp_file_renamed_to_final_only_on_success(self, tmp_path):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(
                    _reader_html_with_ts_reader(
                        [
                            "https://violetscans.org/manga/x/01.jpg",
                        ]
                    )
                ),
            ]
        )

        with _patch_image_download():
            scraper.download_chapter(1.0, tmp_path)

        # After success the .tmp must be gone, the final must exist.
        assert (tmp_path / "Chapter 1.cbz").exists()
        assert not (tmp_path / "Chapter 1.cbz.tmp").exists()


# ─────────────────────────── file naming / packaging ───────────────────────


class TestPackaging:
    def test_integer_chapter_cbz_has_no_decimal(self, tmp_path):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(
                    _reader_html_with_ts_reader(
                        [
                            "https://violetscans.org/manga/x/01.jpg",
                        ]
                    )
                ),
            ]
        )

        with _patch_image_download():
            scraper.download_chapter(1.0, tmp_path)

        assert (tmp_path / "Chapter 1.cbz").exists()
        assert not (tmp_path / "Chapter 1.0.cbz").exists()

    def test_decimal_chapter_cbz_preserves_fraction(self, tmp_path):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(
                    _reader_html_with_ts_reader(
                        [
                            "https://violetscans.org/manga/x/01.jpg",
                        ]
                    )
                ),
            ]
        )

        with _patch_image_download():
            scraper.download_chapter(2.5, tmp_path)

        assert (tmp_path / "Chapter 2.5.cbz").exists()

    def test_pages_named_sequentially_zero_padded(self, tmp_path):
        urls = [f"https://violetscans.org/manga/x/{i:02d}.jpg" for i in range(1, 4)]
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(_reader_html_with_ts_reader(urls)),
            ]
        )

        with _patch_image_download():
            scraper.download_chapter(1.0, tmp_path)

        with zipfile.ZipFile(tmp_path / "Chapter 1.cbz") as z:
            names = sorted(z.namelist())
        assert names == ["page_001.jpg", "page_002.jpg", "page_003.jpg"]

    def test_png_url_gets_png_extension(self, tmp_path):
        scraper = main.VioletScansScraper("https://violetscans.org/comics/x/")
        scraper.session.get = Mock(
            side_effect=[
                _mock_response(CHAPTER_LIST_HTML),
                _mock_response(
                    _reader_html_with_ts_reader(
                        [
                            "https://violetscans.org/manga/x/01.png",
                        ]
                    )
                ),
            ]
        )

        with _patch_image_download():
            scraper.download_chapter(1.0, tmp_path)

        with zipfile.ZipFile(tmp_path / "Chapter 1.cbz") as z:
            assert "page_001.png" in z.namelist()


# ─────────────────────────────── helpers ───────────────────────────────────


from contextlib import contextmanager


@contextmanager
def _patch_image_download(side_effect=None):
    """Patch `requests.get` (used for image bytes) — not the session.get used
    for HTML page fetches.
    """
    from unittest.mock import patch as up

    if side_effect is None:
        with up("main.requests.get", return_value=_mock_response(content=b"IMG")) as m:
            yield m
    else:
        with up("main.requests.get", side_effect=side_effect) as m:
            yield m
