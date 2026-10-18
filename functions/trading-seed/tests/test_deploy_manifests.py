"""Manifest <-> template cross-validation (review: manifest drift regression).

Pins the contract between deploy/trading/fission/functions.yaml (Function
CR secret refs), packages.yaml (URL pins), and secret-trading.yaml.template
(all-functions shared `trading` secret). Would have caught the original
bare-name refs, the trading-seed naming drift, and any future drift
(e.g. config bumps without URL bumps).

Runs in CI (function-builder checks out the full repo and pytest runs
from the function dir). Skipped under `task local-build-*` temp work
dirs, where the deploy/ tree is not rsynced in.
"""
import re
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # requirements-test.txt

_FN_DIR = Path(__file__).resolve().parents[1]
_REPO = _FN_DIR.parents[1]  # functions/<fn> -> functions/ (repo root)
_MANIFEST = _REPO / "deploy" / "trading" / "fission" / "functions.yaml"
_TEMPLATE = _REPO / "deploy" / "trading" / "secret-trading.yaml.template"

pytestmark = pytest.mark.skipif(
    not _MANIFEST.exists(), reason="deploy/ tree not present (local-build work dir)")

EXPECTED_FUNCTIONS = {"debate", "decide", "execute", "score", "digest",
                      "morning-chain", "afternoon-chain", "trading-seed"}


def _functions():
    docs = [d for d in yaml.safe_load_all(_MANIFEST.read_text()) if d]
    return [d for d in docs if d.get("kind") == "Function"]


def test_exactly_the_eight_trading_functions():
    names = {f["metadata"]["name"] for f in _functions()}
    assert names == EXPECTED_FUNCTIONS


def test_every_function_mounts_the_shared_trading_secret():
    for f in _functions():
        fn = f["metadata"]["name"]
        refs = f["spec"].get("secrets", [])
        assert len(refs) == 1, f"{fn}: expected exactly one secret ref, got {refs}"
        assert refs[0]["name"] == "trading", \
            f"{fn}: secret ref {refs[0]['name']!r} != 'trading'"
        assert refs[0]["namespace"] == "fission"


def test_template_provisions_the_shared_secret():
    body = _TEMPLATE.read_text()
    assert re.search(r"name:\s*trading-FUNCTIONNAME\b", body) is None, \
        "template still provisions per-function trading-FUNCTIONNAME secrets"
    assert re.search(r"^\s{4}name:\s*trading\s*$", body, re.M), \
        "template no longer provisions the shared `trading` secret"


_REQUIRED_TEMPLATE_KEYS = {"PGHOST", "PGPORT", "PGDATABASE", "PGUSER",
                           "PGPASSWORD", "LLM_API_BASE", "LLM_API_KEY",
                           "LLM_MODEL", "ALPACA_PAPER_API_KEY",
                           "ALPACA_PAPER_API_SECRET",
                           "ALPACA_PAPER_API_ENDPOINT", "PILOT_NOTIONAL",
                           "GITHUB_TOKEN", "SHADOW"}


def test_template_shared_key_set_is_complete():
    body = _TEMPLATE.read_text()
    for key in sorted(_REQUIRED_TEMPLATE_KEYS):
        assert re.search(r"^\s{4}%s:" % key, body, re.M), \
            f"template missing key {key} in the shared secret"


def test_template_cnpg_bootstrap_secret_contract():
    """tradingpg-app must match CNPG's bootstrap.initdb.secret contract:
    kubernetes.io/basic-auth with username+password, and username must
    equal the PGUSER the functions authenticate as."""
    docs = [d for d in yaml.safe_load_all(_TEMPLATE.read_text()) if d]
    app = docs[0]
    assert app["metadata"]["name"] == "tradingpg-app"
    assert app["metadata"]["namespace"] == "databases"
    assert app["type"] == "kubernetes.io/basic-auth"
    for key in ("username", "password"):
        assert key in app["stringData"], f"tradingpg-app missing {key}"
    shared = docs[1]
    assert app["stringData"]["username"] == shared["stringData"]["PGUSER"], \
        "CNPG bootstrap username must match the functions' PGUSER"


_PACKAGES = _REPO / "deploy" / "trading" / "fission" / "packages.yaml"


def test_package_urls_pair_with_config_versions():
    """Package CR URLs must pin the config.yaml version of their function.

    A config bump without a URL bump silently stages stale zips for the
    cluster (0.1.0 carried the broken hardcoded-mount secrets.py).
    """
    docs = [d for d in yaml.safe_load_all(_PACKAGES.read_text()) if d]
    pkgs = [d for d in docs if d.get("kind") == "Package"]
    assert len(pkgs) == len(EXPECTED_FUNCTIONS)
    for p in pkgs:
        fn = p["metadata"]["name"]
        url = p["spec"]["deployment"]["url"]
        m = re.search(r"/download/%s-(v?[\d.]+)/%s-(v?[\d.]+)\.zip$"
                      % (re.escape(fn), re.escape(fn)), url)
        assert m and m.group(1) == m.group(2), \
            f"{fn}: malformed pin URL {url}"
        cfg = yaml.safe_load((_REPO / "functions" / fn / "config.yaml").read_text())
        assert m.group(1) == cfg["version"], \
            f"{fn}: package URL pins {m.group(1)} but config.yaml says {cfg['version']}"
