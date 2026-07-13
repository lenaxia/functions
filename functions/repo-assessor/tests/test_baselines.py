"""Tests for baselines.py — T-401..T-410."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from model import Category, StateError


def _import_baselines():
    import baselines
    return baselines


def _write_baselines(path: Path, content: dict) -> None:
    path.write_text(json.dumps(content))


def test_T_401_load_baselines_returns_dict_keyed_by_categories(tmp_path: Path) -> None:
    baselines = _import_baselines()
    path = tmp_path / "baselines.json"
    _write_baselines(path, _minimal_baselines())
    result = baselines.load_baselines(str(path))
    expected_keys = {Category.MEDIA, Category.SECURITY, Category.DASHBOARD, Category.NETWORKING, Category.MONITORING}
    assert set(result.keys()) == expected_keys


def test_T_402_baseline_profile_fields_populated(tmp_path: Path) -> None:
    baselines = _import_baselines()
    path = tmp_path / "baselines.json"
    _write_baselines(path, _minimal_baselines())
    result = baselines.load_baselines(str(path))
    sonarr = result[Category.MEDIA]
    assert sonarr.name
    assert sonarr.repo
    assert sonarr.url
    assert sonarr.repo_age_days > 0
    assert sonarr.contributors > 0
    assert sonarr.scores
    assert sonarr.security


def test_T_403_pick_baseline_media_returns_sonarr(tmp_path: Path) -> None:
    baselines = _import_baselines()
    path = tmp_path / "baselines.json"
    _write_baselines(path, _minimal_baselines())
    bs = baselines.load_baselines(str(path))
    chosen = baselines.pick_baseline(bs, Category.MEDIA, default=Category.SECURITY)
    assert chosen.name == "Sonarr"


def test_T_404_pick_baseline_other_falls_back_to_default(tmp_path: Path) -> None:
    baselines = _import_baselines()
    path = tmp_path / "baselines.json"
    _write_baselines(path, _minimal_baselines())
    bs = baselines.load_baselines(str(path))
    chosen = baselines.pick_baseline(bs, Category.OTHER, default=Category.SECURITY)
    assert chosen.name == "Vaultwarden"


def test_T_405_category_takes_priority_over_default(tmp_path: Path) -> None:
    baselines = _import_baselines()
    path = tmp_path / "baselines.json"
    _write_baselines(path, _minimal_baselines())
    bs = baselines.load_baselines(str(path))
    chosen = baselines.pick_baseline(bs, Category.MEDIA, default=Category.SECURITY)
    assert chosen.name == "Sonarr"


def test_T_406_missing_file_raises_with_path(tmp_path: Path) -> None:
    baselines = _import_baselines()
    missing = tmp_path / "nope.json"
    with pytest.raises(StateError) as exc:
        baselines.load_baselines(str(missing))
    assert str(missing) in str(exc.value)


def test_T_407_malformed_json_raises_with_parse_error(tmp_path: Path) -> None:
    baselines = _import_baselines()
    path = tmp_path / "baselines.json"
    path.write_text("{ not json")
    with pytest.raises(StateError) as exc:
        baselines.load_baselines(str(path))
    assert "parse" in str(exc.value).lower() or "json" in str(exc.value).lower()


def test_T_408_unknown_category_in_baselines_raises(tmp_path: Path) -> None:
    baselines = _import_baselines()
    path = tmp_path / "baselines.json"
    payload = _minimal_baselines()
    payload["baselines"]["nonexistent_category"] = payload["baselines"]["media"]
    _write_baselines(path, payload)
    with pytest.raises(StateError) as exc:
        baselines.load_baselines(str(path))
    assert "nonexistent_category" in str(exc.value)


def_T_408_alt_message = "unknown category in baselines.json should be reported"


def test_T_408_known_category_typo_caught(tmp_path: Path) -> None:
    baselines = _import_baselines()
    path = tmp_path / "baselines.json"
    payload = _minimal_baselines()
    payload["baselines"]["medi"] = payload["baselines"]["media"]  # typo
    del payload["baselines"]["media"]
    _write_baselines(path, payload)
    with pytest.raises(StateError):
        baselines.load_baselines(str(path))


def test_T_409_score_values_in_range(tmp_path: Path) -> None:
    baselines = _import_baselines()
    path = tmp_path / "baselines.json"
    _write_baselines(path, _minimal_baselines())
    bs = baselines.load_baselines(str(path))
    for profile in bs.values():
        for score in profile.scores.values():
            assert 1 <= score <= 5
        for score in profile.security.values():
            assert 1 <= score <= 5


def test_T_409_score_out_of_range_rejected(tmp_path: Path) -> None:
    baselines = _import_baselines()
    path = tmp_path / "baselines.json"
    payload = _minimal_baselines()
    payload["baselines"]["media"]["profile"]["scores"]["code_quality"] = 9
    _write_baselines(path, payload)
    with pytest.raises(StateError):
        baselines.load_baselines(str(path))


def test_T_410_load_returns_independent_objects(tmp_path: Path) -> None:
    baselines = _import_baselines()
    path = tmp_path / "baselines.json"
    _write_baselines(path, _minimal_baselines())
    bs1 = baselines.load_baselines(str(path))
    bs2 = baselines.load_baselines(str(path))
    assert bs1[Category.MEDIA] == bs2[Category.MEDIA]
    assert bs1 is not bs2


def _minimal_baselines() -> dict:
    return {
        "version": 1,
        "default_category": "media",
        "baselines": {
            "media": {
                "name": "Sonarr",
                "repo": "Sonarr/Sonarr",
                "url": "https://github.com/Sonarr/Sonarr",
                "category_description": "Media",
                "profile": {
                    "repo_age_days": 4380,
                    "avg_commits_per_month": 30,
                    "pct_commits_last_3_months": 8,
                    "contributors": 350,
                    "stars": 12000,
                    "license": "GPL-3.0",
                    "scores": {
                        "code_quality": 4,
                        "release_ci_flow": 5,
                        "test_to_code_ratio": 4,
                        "architectural_robustness": 4,
                    },
                    "security": {"internal_only": 5, "reverse_proxy": 4, "sso": 3},
                },
                "notes": "Reference.",
            },
            "security": {
                "name": "Vaultwarden",
                "repo": "dani-garcia/vaultwarden",
                "url": "https://github.com/dani-garcia/vaultwarden",
                "category_description": "Security",
                "profile": {
                    "repo_age_days": 2190,
                    "avg_commits_per_month": 18,
                    "pct_commits_last_3_months": 5,
                    "contributors": 280,
                    "stars": 42000,
                    "license": "GPL-3.0",
                    "scores": {
                        "code_quality": 5,
                        "release_ci_flow": 5,
                        "test_to_code_ratio": 4,
                        "architectural_robustness": 5,
                    },
                    "security": {"internal_only": 5, "reverse_proxy": 5, "sso": 5},
                },
                "notes": "Reference.",
            },
            "dashboard": {
                "name": "Homer",
                "repo": "bastienwirtz/homer",
                "url": "https://github.com/bastienwirtz/homer",
                "category_description": "Dashboard",
                "profile": {
                    "repo_age_days": 2190,
                    "avg_commits_per_month": 6,
                    "pct_commits_last_3_months": 4,
                    "contributors": 90,
                    "stars": 9500,
                    "license": "Apache-2.0",
                    "scores": {
                        "code_quality": 4,
                        "release_ci_flow": 4,
                        "test_to_code_ratio": 3,
                        "architectural_robustness": 4,
                    },
                    "security": {"internal_only": 5, "reverse_proxy": 5, "sso": 4},
                },
                "notes": "Reference.",
            },
            "networking": {
                "name": "Pi-hole",
                "repo": "pi-hole/pi-hole",
                "url": "https://github.com/pi-hole/pi-hole",
                "category_description": "Networking",
                "profile": {
                    "repo_age_days": 3650,
                    "avg_commits_per_month": 20,
                    "pct_commits_last_3_months": 6,
                    "contributors": 400,
                    "stars": 49000,
                    "license": "EUPL-1.2",
                    "scores": {
                        "code_quality": 4,
                        "release_ci_flow": 4,
                        "test_to_code_ratio": 3,
                        "architectural_robustness": 4,
                    },
                    "security": {"internal_only": 4, "reverse_proxy": 3, "sso": 2},
                },
                "notes": "Reference.",
            },
            "monitoring": {
                "name": "Uptime Kuma",
                "repo": "louislam/uptime-kuma",
                "url": "https://github.com/louislam/uptime-kuma",
                "category_description": "Monitoring",
                "profile": {
                    "repo_age_days": 1825,
                    "avg_commits_per_month": 35,
                    "pct_commits_last_3_months": 10,
                    "contributors": 250,
                    "stars": 60000,
                    "license": "MIT",
                    "scores": {
                        "code_quality": 4,
                        "release_ci_flow": 4,
                        "test_to_code_ratio": 3,
                        "architectural_robustness": 4,
                    },
                    "security": {"internal_only": 5, "reverse_proxy": 4, "sso": 3},
                },
                "notes": "Reference.",
            },
        },
    }
