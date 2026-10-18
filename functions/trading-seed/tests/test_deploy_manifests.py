"""Manifest <-> template cross-validation (review: manifest drift regression).

Pins the contract between deploy/trading/fission/functions.yaml (Function
CR secret refs) and secret-trading.yaml.template (provisions
trading-FUNCTIONNAME). Would have caught both the original bare-name
refs and any future drift (e.g. trading-seed vs trading-trading-seed).

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


def test_every_function_mounts_exactly_its_trading_secret():
    for f in _functions():
        fn = f["metadata"]["name"]
        refs = f["spec"].get("secrets", [])
        assert len(refs) == 1, f"{fn}: expected exactly one secret ref, got {refs}"
        assert refs[0]["name"] == "trading-" + fn, \
            f"{fn}: secret ref {refs[0]['name']!r} != 'trading-{fn}'"
        assert refs[0]["namespace"] == "fission"


def test_template_provisions_trading_functionname():
    body = _TEMPLATE.read_text()
    assert re.search(r"name:\s*trading-FUNCTIONNAME\b", body), \
        "template no longer provisions trading-FUNCTIONNAME secrets"


def test_template_matrix_lists_every_function():
    body = _TEMPLATE.read_text()
    for fn in sorted(EXPECTED_FUNCTIONS):
        assert re.search(r"\b%s\b" % re.escape(fn), body), \
            f"template key matrix is missing {fn}"


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
