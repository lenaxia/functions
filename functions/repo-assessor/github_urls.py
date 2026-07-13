"""GitHub repository URL extraction and normalisation.

Matches github.com/{owner}/{repo} URLs, including subresources
(/issues, /pulls, /releases, /blob/...). Rejects gist.github.com and
github.com/{user} (single-segment). Normalises www / raw / scheme-less
variants to a canonical https://github.com/{owner}/{repo} form.
"""

from __future__ import annotations

import re


_HOST_PATTERN = r"(?:github\.com|www\.github\.com|raw\.githubusercontent\.com)"
_OWNER_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?"
_REPO_PATTERN = r"[A-Za-z0-9._-]+"

_URL_PATTERN = re.compile(
    r"(?:https?://)?"
    + _HOST_PATTERN
    + r"/"
    + r"(?P<owner>" + _OWNER_PATTERN + r")"
    + r"/"
    + r"(?P<repo>" + _REPO_PATTERN + r")"
)


def extract_github_urls(text: str) -> list[str]:
    if not text:
        return []
    seen: list[str] = []
    for m in _URL_PATTERN.finditer(text):
        if _is_gist(text, m):
            continue
        normalized = f"https://github.com/{m.group('owner')}/{_strip_dot_git(m.group('repo'))}"
        if normalized not in seen:
            seen.append(normalized)
    return seen


def extract_first_github_url(*texts: str) -> str | None:
    for t in texts:
        urls = extract_github_urls(t)
        if urls:
            return urls[0]
    return None


def normalize_github_url(url: str) -> str:
    m = _URL_PATTERN.search(url)
    if not m:
        raise ValueError(f"not a github repo URL: {url!r}")
    return f"https://github.com/{m.group('owner')}/{_strip_dot_git(m.group('repo'))}"


def _is_gist(text: str, match: re.Match) -> bool:
    start = max(0, match.start() - 5)
    return "gist." in text[start:match.start() + len("github.com") + 5]


def _strip_dot_git(repo: str) -> str:
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    while len(repo) > 1 and repo[-1] in "._-":
        repo = repo[:-1]
    return repo
