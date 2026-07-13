"""Tests for reddit_client.py — T-601..T-623."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import httpx
import pytest
import responses

from model import RedditAPIError, RedditRateLimit


def _import_reddit_client():
    import reddit_client
    return reddit_client


def _minimal_config_dict():
    return {
        "reddit_client_id": "cid",
        "reddit_client_secret": "csec",
        "reddit_username": "bot_user",
        "reddit_password": "bot_pass",
        "reddit_user_agent": "repo-assessor/0.1 by bot_user",
        "reddit_subreddit": "selfhosted",
        "reddit_new_limit": 25,
        "reddit_api_max_retries": 3,
    }


def _make_client():
    reddit_client = _import_reddit_client()
    cfg = _minimal_config_dict()
    return reddit_client.RedditClient(cfg)


def _register_token_mock(rsps: responses.RequestsMock, access_token: str = "tok-1") -> None:
    rsps.add(
        responses.POST,
        "https://www.reddit.com/api/v1/access_token",
        json={"access_token": access_token, "token_type": "bearer", "expires_in": 3600},
        status=200,
    )


def _listing_response(children: list[dict]) -> dict:
    return {"kind": "Listing", "data": {"children": [{"kind": "t3", "data": c} for c in children]}}


def _minimal_submission_data(**overrides) -> dict:
    base = {
        "id": "abc123",
        "title": "test post",
        "selftext": "hello",
        "url": "https://example.com",
        "author": "someuser",
        "created_utc": 1783900000.0,
        "permalink": "/r/selfhosted/comments/abc123/test_post/",
        "link_flair_text": None,
        "is_self": False,
    }
    base.update(overrides)
    return base


# Auth and base client behaviour ──────────────────────────────────────────────


@responses.activate
def test_T_601_token_fetched_lazily_and_cached() -> None:
    rsps = responses
    _register_token_mock(rsps, "tok-1")
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        json=_listing_response([]),
        status=200,
    )
    client = _make_client()
    client.get_new()
    client.get_new()
    token_calls = [c for c in rsps.calls if "access_token" in c.request.url]
    assert len(token_calls) == 1, "token should be cached"


@responses.activate
def test_T_602_unauthorized_triggers_token_refresh_then_retry() -> None:
    rsps = responses
    _register_token_mock(rsps, "tok-1")
    _register_token_mock(rsps, "tok-2")  # second token on refresh
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        json={"error": "bad token"},
        status=401,
    )
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        json=_listing_response([]),
        status=200,
    )
    client = _make_client()
    client.get_new()  # should not raise
    new_calls = [c for c in rsps.calls if "/new" in c.request.url]
    assert len(new_calls) == 2


@responses.activate
def test_T_602_second_401_raises() -> None:
    rsps = responses
    _register_token_mock(rsps, "tok-1")
    _register_token_mock(rsps, "tok-2")
    for _ in range(2):
        rsps.add(
            responses.GET,
            "https://oauth.reddit.com/r/selfhosted/new",
            json={"error": "still bad"},
            status=401,
        )
    client = _make_client()
    with pytest.raises(RedditAPIError):
        client.get_new()


# get_new ─────────────────────────────────────────────────────────────────────


@responses.activate
def test_T_603_get_new_parses_listing() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        json=_listing_response([_minimal_submission_data()]),
        status=200,
    )
    client = _make_client()
    subs = client.get_new()
    assert len(subs) == 1
    s = subs[0]
    assert s.id == "abc123"
    assert s.title == "test post"
    assert s.author == "someuser"
    assert s.permalink == "/r/selfhosted/comments/abc123/test_post/"


@responses.activate
def test_T_604_get_new_empty_listing_returns_empty_list() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        json=_listing_response([]),
        status=200,
    )
    client = _make_client()
    assert client.get_new() == []


@responses.activate
def test_T_605_submission_with_null_flair() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        json=_listing_response([_minimal_submission_data(link_flair_text=None)]),
        status=200,
    )
    client = _make_client()
    subs = client.get_new()
    assert subs[0].flair is None


@responses.activate
def test_T_606_self_post_with_url_still_is_self() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        json=_listing_response([
            _minimal_submission_data(is_self=True, url="https://self.text")
        ]),
        status=200,
    )
    client = _make_client()
    subs = client.get_new()
    assert subs[0].is_self is True


# find_canonical_sticky ───────────────────────────────────────────────────────


def test_T_607_find_canonical_sticky_matches_author_and_regex() -> None:
    reddit_client = _import_reddit_client()
    comments = [
        {"id": "c1", "author": "regular", "body": "first", "distinguished": None, "stickied": False, "created_utc": 1.0},
        {"id": "c2", "author": "asimovs-auditor", "body": "Expand replies to learn how AI was used in this post/project.", "distinguished": "moderator", "stickied": True, "created_utc": 2.0},
    ]
    client = _make_client()
    found = client.find_canonical_sticky(_wrap_comments(comments), "asimovs-auditor", r"(?i)how AI was used")
    assert found is not None
    assert found.id == "c2"


def test_T_608_find_canonical_sticky_returns_none_when_no_match() -> None:
    reddit_client = _import_reddit_client()
    comments = [
        {"id": "c1", "author": "other", "body": "nope", "distinguished": None, "stickied": False, "created_utc": 1.0},
    ]
    client = _make_client()
    found = client.find_canonical_sticky(_wrap_comments(comments), "asimovs-auditor", r"how AI")
    assert found is None


def test_T_609_find_canonical_sticky_only_top_level() -> None:
    reddit_client = _import_reddit_client()
    client = _make_client()
    top_level_only = [
        RedditCommentStub(id="top1", author="someone", body="x", distinguished=None, stickied=False, created_utc=1.0),
    ]
    nested_sticky = RedditCommentStub(
        id="sticky_nested", author="asimovs-auditor",
        body="how AI was used here", distinguished="moderator",
        stickied=True, created_utc=2.0,
    )
    top_level_only[0].replies = [nested_sticky]
    nested_sticky.replies = []
    found = client.find_canonical_sticky(top_level_only, "asimovs-auditor", r"(?i)how AI was used")
    assert found is None, "nested sticky must not match"


# has_bot_reply ───────────────────────────────────────────────────────────────


def test_T_610_has_bot_reply_true_when_bot_has_top_level_comment() -> None:
    reddit_client = _import_reddit_client()
    client = _make_client()
    comments = [
        RedditCommentStub(id="c1", author="bot_user", body="assessment", distinguished=None, stickied=False, created_utc=1.0),
        RedditCommentStub(id="c2", author="someone_else", body="reply", distinguished=None, stickied=False, created_utc=2.0),
    ]
    for c in comments:
        c.replies = []
    assert client.has_bot_reply(comments, "bot_user") is True


def test_T_610_has_bot_reply_true_when_bot_replies_under_sticky() -> None:
    reddit_client = _import_reddit_client()
    client = _make_client()
    sticky = RedditCommentStub(id="s", author="asimovs-auditor", body="how AI was used", distinguished="moderator", stickied=True, created_utc=1.0)
    bot_reply = RedditCommentStub(id="b", author="bot_user", body="my assessment", distinguished=None, stickied=False, created_utc=2.0)
    sticky.replies = [bot_reply]
    bot_reply.replies = []
    assert client.has_bot_reply([sticky], "bot_user") is True


def test_T_611_has_bot_reply_false_when_no_bot_comments() -> None:
    reddit_client = _import_reddit_client()
    client = _make_client()
    comments = [
        RedditCommentStub(id="c1", author="someone", body="x", distinguished=None, stickied=False, created_utc=1.0),
    ]
    comments[0].replies = []
    assert client.has_bot_reply(comments, "bot_user") is False


# POSTs ───────────────────────────────────────────────────────────────────────


@responses.activate
def test_T_612_reply_to_comment_returns_new_id() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.POST,
        "https://oauth.reddit.com/api/comment",
        json={"json": {"errors": [], "data": {"things": [{"data": {"id": "t1_newcomment"}}]}}},
        status=200,
    )
    client = _make_client()
    new_id = client.reply_to_comment("t1_parent", "my reply")
    assert new_id == "t1_newcomment"


@responses.activate
def test_T_613_submit_text_post_returns_submission_id() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.POST,
        "https://oauth.reddit.com/api/submit",
        json={"json": {"errors": [], "data": {"id": "t3_newpost", "name": "t3_newpost"}}},
        status=200,
    )
    client = _make_client()
    sub_id = client.submit_text_post("testsub", "title", "body text")
    assert sub_id == "t3_newpost"


@responses.activate
def test_T_614_distinguish_comment_posts_with_how_yes() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.POST,
        "https://oauth.reddit.com/api/distinguish",
        json={"json": {"errors": []}},
        status=200,
    )
    client = _make_client()
    client.distinguish_comment("t1_x")
    last = rsps.calls[-1].request
    body = last.body
    if isinstance(body, bytes):
        body = body.decode()
    assert "how=yes" in body
    assert "t1_x" in body


@responses.activate
def test_T_615_distinguish_403_is_noop_no_raise() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.POST,
        "https://oauth.reddit.com/api/distinguish",
        json={"json": {"errors": [{"field": "*", "explanation": "not a mod"}]}},
        status=403,
    )
    client = _make_client()
    # should not raise
    client.distinguish_comment("t1_x")


# Errors ──────────────────────────────────────────────────────────────────────


@responses.activate
def test_T_616_rate_limit_429_raises_reddit_rate_limit() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        body="429 Too Many Requests",
        status=429,
    )
    client = _make_client()
    with pytest.raises(RedditRateLimit):
        client.get_new()


@responses.activate
def test_T_617_503_retried_once_then_raises() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        status=503,
    )
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        status=503,
    )
    client = _make_client()
    with pytest.raises(RedditAPIError):
        client.get_new()
    new_calls = [c for c in rsps.calls if "/new" in c.request.url]
    assert len(new_calls) == 2, "should retry once"


@responses.activate
def test_T_618_other_5xx_raises_immediately() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        status=500,
    )
    client = _make_client()
    with pytest.raises(RedditAPIError):
        client.get_new()


@responses.activate
def test_T_619_400_on_post_raises_with_body_in_message() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.POST,
        "https://oauth.reddit.com/api/comment",
        body="thread is locked",
        status=400,
    )
    client = _make_client()
    with pytest.raises(RedditAPIError) as exc_info:
        client.reply_to_comment("t1_parent", "x")
    assert "thread is locked" in str(exc_info.value) or "400" in str(exc_info.value)


@responses.activate
def test_T_620_network_timeout_raises() -> None:
    rsps = responses
    _register_token_mock(rsps)

    def connection_timeout(request):
        import requests as req
        raise req.ConnectionError("simulated timeout")

    rsps.add_callback(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        callback=connection_timeout,
    )
    client = _make_client()
    with pytest.raises(RedditAPIError):
        client.get_new()


@responses.activate
def test_T_621_user_agent_header_present() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        json=_listing_response([]),
        status=200,
    )
    client = _make_client()
    client.get_new()
    new_call = [c for c in rsps.calls if "/new" in c.request.url][0]
    assert new_call.request.headers["User-Agent"] == "repo-assessor/0.1 by bot_user"


# Edge cases ──────────────────────────────────────────────────────────────────


@responses.activate
def test_T_622_deleted_user_author_returns_none() -> None:
    rsps = responses
    _register_token_mock(rsps)
    rsps.add(
        responses.GET,
        "https://oauth.reddit.com/r/selfhosted/new",
        json=_listing_response([_minimal_submission_data(author=None)]),
        status=200,
    )
    client = _make_client()
    subs = client.get_new()
    assert subs[0].author is None


def test_T_623_truncated_more_comments_not_treated_as_top_level() -> None:
    reddit_client = _import_reddit_client()
    client = _make_client()
    truncated = RedditCommentStub(
        id="", author=None, body="", distinguished=None,
        stickied=False, created_utc=0.0,
        is_truncated=True,
    )
    truncated.replies = []
    found = client.find_canonical_sticky([truncated], "any", r".*")
    assert found is None


# Helpers ─────────────────────────────────────────────────────────────────────


def _wrap_comments(raw: list[dict]):
    from model import RedditComment
    import datetime as dt
    return [
        RedditComment(
            id=c["id"],
            author=c.get("author"),
            body=c["body"],
            distinguished=c.get("distinguished"),
            stickied=c.get("stickied", False),
            created_utc=dt.datetime.fromtimestamp(c["created_utc"], tz=dt.timezone.utc),
        )
        for c in raw
    ]


class RedditCommentStub:
    def __init__(self, *, id, author, body, distinguished, stickied, created_utc, is_truncated=False):
        self.id = id
        self.author = author
        self.body = body
        self.distinguished = distinguished
        self.stickied = stickied
        self.created_utc = created_utc
        self.replies: list = []
        self.is_truncated = is_truncated
