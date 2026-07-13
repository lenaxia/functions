"""Reddit OAuth2 + JSON API client.

Uses `requests` (matches the matriarch/violetscans convention in this repo).
Lazy OAuth2 token acquisition with one refresh-on-401 retry. Internal
5xx-retry-once. 429 surfaced as RedditRateLimit for the caller's backoff.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from typing import Any

import requests

from model import (
    RedditAPIError,
    RedditComment,
    RedditRateLimit,
    RedditSubmission,
)


_LOG = logging.getLogger(__name__)
_OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_OAUTH_API_BASE = "https://oauth.reddit.com"
_DEFAULT_TIMEOUT = 30
_5XX_RETRY_BACKOFF = 1.0


class RedditClient:
    def __init__(self, config: dict | Any) -> None:
        self._client_id = config["reddit_client_id"]
        self._client_secret = config["reddit_client_secret"]
        self._username = config["reddit_username"]
        self._password = config["reddit_password"]
        self._user_agent = config["reddit_user_agent"]
        self._subreddit = config["reddit_subreddit"]
        self._new_limit = int(config["reddit_new_limit"])
        self._max_retries = int(config.get("reddit_api_max_retries", 3))
        self._token: str | None = None
        self._session = requests.Session()
        self._session.headers["User-Agent"] = self._user_agent

    def get_new(self) -> list[RedditSubmission]:
        data = self._request(
            "GET",
            f"/r/{self._subreddit}/new",
            params={"limit": self._new_limit},
        )
        children = data.get("data", {}).get("children", [])
        results: list[RedditSubmission] = []
        for c in children:
            if not isinstance(c, dict) or "data" not in c:
                continue
            try:
                results.append(_parse_submission(c["data"]))
            except (KeyError, ValueError, TypeError) as e:
                _LOG.warning("skipping malformed submission entry: %s", e)
        return results

    def get_comments(self, submission_id: str) -> list[RedditComment]:
        data = self._request(
            "GET",
            f"/comments/{submission_id}",
            params={"limit": 200, "depth": 2},
        )
        if not isinstance(data, list) or len(data) < 2:
            return []
        top_level = data[1].get("data", {}).get("children", [])
        results: list[RedditComment] = []
        for c in top_level:
            if not isinstance(c, dict) or "data" not in c:
                continue
            try:
                parsed = _parse_comment(c["data"])
                parsed.replies = _parse_replies(c["data"].get("replies"))
                results.append(parsed)
            except (KeyError, ValueError, TypeError) as e:
                _LOG.warning("skipping malformed comment entry: %s", e)
        return results

    def find_canonical_sticky(
        self,
        comments: list[Any],
        author: str,
        text_regex: str,
    ) -> Any | None:
        pattern = re.compile(text_regex)
        for c in comments:
            if _is_truncated(c):
                continue
            if getattr(c, "author", None) != author:
                continue
            if pattern.search(getattr(c, "body", "") or ""):
                return c
        return None
    def has_bot_reply(self, comments: list[Any], bot_username: str) -> bool:
        for c in comments:
            if _author_is(c, bot_username):
                return True
            for r in iter_replies(c):
                if _author_is(r, bot_username):
                    return True
        return False

    def reply_to_comment(self, comment_id: str, markdown: str) -> str:
        data = self._request(
            "POST",
            "/api/comment",
            data={"thing_id": comment_id, "text": markdown, "api_type": "json"},
        )
        return _extract_comment_id(data)

    def submit_text_post(self, subreddit: str, title: str, body: str) -> str:
        data = self._request(
            "POST",
            "/api/submit",
            data={
                "kind": "self",
                "sr": subreddit,
                "title": title,
                "text": body,
                "api_type": "json",
            },
        )
        return _extract_submit_id(data)

    def distinguish_comment(self, comment_id: str) -> None:
        try:
            self._request(
                "POST",
                "/api/distinguish",
                data={"id": comment_id, "how": "yes", "api_type": "json"},
            )
        except RedditAPIError as e:
            if "403" in str(e) or "not a mod" in str(e).lower():
                _LOG.warning("distinguish failed (not a mod): %s", e)
                return
            raise

    def close(self) -> None:
        self._session.close()

    def _request(self, method: str, path: str, *, params: dict | None = None, data: dict | None = None) -> Any:
        url = f"{_OAUTH_API_BASE}{path}"
        return self._request_with_retry(
            method, url, path=path, params=params, data=data, is_first_attempt=True
        )

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        path: str,
        params: dict | None,
        data: dict | None,
        is_first_attempt: bool,
    ) -> Any:
        headers = self._auth_headers()
        try:
            resp = self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                data=data,
                timeout=_DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            raise RedditAPIError(f"network error: {e}") from e

        if resp.status_code == 401 and is_first_attempt:
            _LOG.info("reddit 401, refreshing token")
            self._token = None
            return self._request_with_retry(
                method, url, path=path, params=params, data=data, is_first_attempt=False
            )

        if resp.status_code == 429:
            raise RedditRateLimit(f"429 rate limited: {resp.text[:200]}")

        if 500 <= resp.status_code < 600:
            if method == "GET" and is_first_attempt:
                _LOG.warning("reddit %d, retrying once", resp.status_code)
                time.sleep(_5XX_RETRY_BACKOFF)
                return self._request_with_retry(
                    method, url, path=path, params=params, data=data, is_first_attempt=False
                )
            raise RedditAPIError(f"reddit {resp.status_code}: {resp.text[:200]}")

        if resp.status_code >= 400:
            raise RedditAPIError(
                f"reddit {resp.status_code} on {method} {path}: {resp.text[:300]}"
            )

        try:
            return resp.json()
        except ValueError as e:
            raise RedditAPIError(f"non-json response from {method} {path}: {resp.text[:200]}") from e

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            self._login()
        return {"Authorization": f"Bearer {self._token}"}

    def _login(self) -> None:
        try:
            resp = self._session.post(
                _OAUTH_TOKEN_URL,
                auth=(self._client_id, self._client_secret),
                data={
                    "grant_type": "password",
                    "username": self._username,
                    "password": self._password,
                },
                headers={"User-Agent": self._user_agent},
                timeout=_DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            raise RedditAPIError(f"token request failed: {e}") from e
        if resp.status_code != 200:
            raise RedditAPIError(f"token request {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise RedditAPIError(f"token response missing access_token: {body}")
        self._token = token


def _parse_submission(data: dict) -> RedditSubmission:
    return RedditSubmission(
        id=data["id"],
        title=data.get("title", ""),
        selftext=data.get("selftext", "") or "",
        url=data.get("url", "") or "",
        author=data.get("author"),
        created_utc=dt.datetime.fromtimestamp(
            float(data.get("created_utc", 0.0)), tz=dt.timezone.utc
        ),
        permalink=data.get("permalink", ""),
        flair=data.get("link_flair_text"),
        is_self=bool(data.get("is_self", False)),
    )


def _parse_comment(data: dict) -> RedditComment:
    return RedditComment(
        id=data.get("id", ""),
        author=data.get("author"),
        body=data.get("body", "") or "",
        distinguished=data.get("distinguished"),
        stickied=bool(data.get("stickied", False)),
        created_utc=dt.datetime.fromtimestamp(
            float(data.get("created_utc", 0.0)), tz=dt.timezone.utc
        ),
    )


def _parse_replies(replies_field: Any) -> list:
    if not isinstance(replies_field, dict):
        return []
    children = replies_field.get("data", {}).get("children", [])
    if not isinstance(children, list):
        return []
    return [_parse_comment(c["data"]) for c in children if isinstance(c, dict) and "data" in c]


def _is_truncated(comment: Any) -> bool:
    return bool(getattr(comment, "is_truncated", False)) or getattr(comment, "id", "x") == ""


def _author_is(comment: Any, username: str) -> bool:
    return getattr(comment, "author", None) == username


def iter_replies(comment: Any):
    replies = getattr(comment, "replies", None) or []
    for r in replies:
        yield r
        yield from iter_replies(r)


def _extract_comment_id(response: Any) -> str:
    try:
        body = response["json"]
    except (KeyError, TypeError) as e:
        raise RedditAPIError(f"unexpected comment response shape: {response}") from e
    errors = body.get("errors") or []
    if errors:
        raise RedditAPIError(f"reddit rejected comment: {errors}")
    try:
        things = body["data"]["things"]
        return things[0]["data"]["id"]
    except (KeyError, IndexError, TypeError) as e:
        raise RedditAPIError(f"comment response missing id: {response}") from e


def _extract_submit_id(response: Any) -> str:
    try:
        body = response["json"]
    except (KeyError, TypeError) as e:
        raise RedditAPIError(f"unexpected submit response shape: {response}") from e
    errors = body.get("errors") or []
    if errors:
        raise RedditAPIError(f"reddit rejected submit: {errors}")
    try:
        return body["data"]["id"]
    except (KeyError, TypeError) as e:
        raise RedditAPIError(f"submit response missing id: {response}") from e
