"""Tests for github_urls.py — T-501..T-514."""

from __future__ import annotations

import pytest


def _import_github_urls():
    import github_urls
    return github_urls


def test_T_501_plain_https_url_extracted() -> None:
    g = _import_github_urls()
    urls = g.extract_github_urls("see https://github.com/owner/repo for details")
    assert urls == ["https://github.com/owner/repo"]


def test_T_502_dot_git_suffix_normalized() -> None:
    g = _import_github_urls()
    urls = g.extract_github_urls("https://github.com/owner/repo.git")
    assert urls == ["https://github.com/owner/repo"]


def test_T_503_issues_pulls_blob_normalized_to_repo_root() -> None:
    g = _import_github_urls()
    for suffix in ("/issues", "/pulls", "/releases", "/blob/main/file.go", "/wiki"):
        urls = g.extract_github_urls(f"https://github.com/owner/repo{suffix}")
        assert urls == [f"https://github.com/owner/repo"], suffix


def test_T_504_query_and_anchor_stripped() -> None:
    g = _import_github_urls()
    urls = g.extract_github_urls("https://github.com/owner/repo?tab=readme#header")
    assert urls == ["https://github.com/owner/repo"]


def test_T_505_multiple_urls_deduped() -> None:
    g = _import_github_urls()
    text = """
    see https://github.com/owner/repo and also https://github.com/owner/repo/issues
    plus https://github.com/another/project
    """
    urls = g.extract_github_urls(text)
    assert urls == [
        "https://github.com/owner/repo",
        "https://github.com/another/project",
    ]


def test_T_506_gist_urls_not_matched() -> None:
    g = _import_github_urls()
    urls = g.extract_github_urls("https://gist.github.com/user/abc123")
    assert urls == []


def test_T_507_raw_githubusercontent_normalized_to_repo_root() -> None:
    g = _import_github_urls()
    urls = g.extract_github_urls(
        "https://raw.githubusercontent.com/owner/repo/main/file.go"
    )
    assert urls == ["https://github.com/owner/repo"]


def test_T_508_bare_url_without_scheme_normalized() -> None:
    g = _import_github_urls()
    urls = g.extract_github_urls("clone github.com/owner/repo today")
    assert urls == ["https://github.com/owner/repo"]


def test_T_509_www_subdomain_normalized() -> None:
    g = _import_github_urls()
    urls = g.extract_github_urls("https://www.github.com/owner/repo")
    assert urls == ["https://github.com/owner/repo"]


def test_T_510_markdown_link_extracted() -> None:
    g = _import_github_urls()
    urls = g.extract_github_urls("see [the repo](https://github.com/owner/repo)")
    assert urls == ["https://github.com/owner/repo"]


def test_T_511_angle_brackets_extracted() -> None:
    g = _import_github_urls()
    urls = g.extract_github_urls("<https://github.com/owner/repo>")
    assert urls == ["https://github.com/owner/repo"]


def test_T_512_empty_input_returns_empty() -> None:
    g = _import_github_urls()
    assert g.extract_github_urls("") == []
    assert g.extract_first_github_url("") is None
    assert g.extract_first_github_url("", "") is None


def test_T_513_same_repo_five_times_one_entry() -> None:
    g = _import_github_urls()
    text = " ".join(["https://github.com/o/r"] * 5)
    urls = g.extract_github_urls(text)
    assert urls == ["https://github.com/o/r"]


def test_T_514_normalize_is_idempotent() -> None:
    g = _import_github_urls()
    test_cases = [
        "https://github.com/owner/repo",
        "https://github.com/owner/repo.git",
        "https://github.com/owner/repo/issues",
        "https://www.github.com/owner/repo",
        "https://raw.githubusercontent.com/owner/repo/main/x.go",
    ]
    for url in test_cases:
        once = g.normalize_github_url(url)
        twice = g.normalize_github_url(once)
        assert once == twice, f"not idempotent: {url} -> {once} -> {twice}"


def test_extract_first_returns_first_from_multiple() -> None:
    g = _import_github_urls()
    text = "https://github.com/first/repo and https://github.com/second/repo"
    assert g.extract_first_github_url(text) == "https://github.com/first/repo"


def test_extract_first_searches_multiple_texts() -> None:
    g = _import_github_urls()
    assert g.extract_first_github_url("no link here", "but https://github.com/x/y") == \
        "https://github.com/x/y"


def test_extract_returns_empty_when_no_github_url() -> None:
    g = _import_github_urls()
    assert g.extract_github_urls("https://gitlab.com/owner/repo") == []
    assert g.extract_github_urls("https://bitbucket.org/owner/repo") == []


def test_short_repo_path_not_matched() -> None:
    g = _import_github_urls()
    # only one path segment — not a valid repo URL
    assert g.extract_github_urls("https://github.com/owner") == []


def test_punctuation_around_url_handled() -> None:
    g = _import_github_urls()
    for surround in ["(url)", "[url]", '"url"', "url.", "url,", "url;"]:
        text = surround.replace("url", "https://github.com/o/r")
        urls = g.extract_github_urls(text)
        assert urls == ["https://github.com/o/r"], surround
