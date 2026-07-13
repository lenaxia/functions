"""Static baseline profile loader.

Baselines are the comparison reference for each non-`other` category.
Stored as static JSON so re-running the assessment prompt against a
baseline repo and committing the result is the update path.
"""

from __future__ import annotations

import json
from pathlib import Path

from model import BaselineProfile, Category, StateError


_BASELINE_SCORE_KEYS = (
    "code_quality",
    "release_ci_flow",
    "test_to_code_ratio",
    "architectural_robustness",
)
_BASELINE_SECURITY_KEYS = ("internal_only", "reverse_proxy", "sso")


def load_baselines(path: str) -> dict[Category, BaselineProfile]:
    p = Path(path)
    if not p.exists():
        raise StateError(f"baselines file not found: {p}")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise StateError(f"baselines parse error in {p}: {e}") from e

    raw = data.get("baselines")
    if not isinstance(raw, dict) or not raw:
        raise StateError(f"baselines file {p}: missing or empty 'baselines' object")

    result: dict[Category, BaselineProfile] = {}
    for key, payload in raw.items():
        try:
            category = Category(key)
        except ValueError as e:
            raise StateError(
                f"baselines file {p}: unknown category {key!r}; "
                f"valid: {sorted(c.value for c in Category)}"
            ) from e
        result[category] = _build_profile(category, payload, p)
    return result


def pick_baseline(
    baselines: dict[Category, BaselineProfile],
    category: Category,
    *,
    default: Category,
) -> BaselineProfile:
    if category in baselines:
        return baselines[category]
    if default in baselines:
        return baselines[default]
    raise StateError(
        f"neither category {category.value!r} nor default {default.value!r} "
        f"present in baselines"
    )


def _build_profile(category: Category, payload: dict, source: Path) -> BaselineProfile:
    try:
        profile_payload = payload["profile"]
        scores_raw = profile_payload["scores"]
        security_raw = profile_payload["security"]
    except KeyError as e:
        raise StateError(f"baselines file {source}: category {category.value!r} missing field {e}") from e

    _require_score_keys(source, category, scores_raw, _BASELINE_SCORE_KEYS)
    _require_score_keys(source, category, security_raw, _BASELINE_SECURITY_KEYS)

    return BaselineProfile(
        name=payload["name"],
        repo=payload["repo"],
        url=payload["url"],
        category_description=payload.get("category_description", ""),
        repo_age_days=int(profile_payload["repo_age_days"]),
        avg_commits_per_month=float(profile_payload["avg_commits_per_month"]),
        pct_commits_last_3_months=float(profile_payload["pct_commits_last_3_months"]),
        contributors=int(profile_payload["contributors"]),
        stars=int(profile_payload["stars"]),
        license=str(profile_payload["license"]),
        scores={k: int(v) for k, v in scores_raw.items()},
        security={k: int(v) for k, v in security_raw.items()},
        notes=payload.get("notes", ""),
    )


def _require_score_keys(source: Path, category: Category, data: dict, keys: tuple[str, ...]) -> None:
    for k in keys:
        if k not in data:
            raise StateError(
                f"baselines file {source}: category {category.value!r} missing score key {k!r}"
            )
        v = data[k]
        if not isinstance(v, int) or v < 1 or v > 5:
            raise StateError(
                f"baselines file {source}: category {category.value!r} score {k!r} "
                f"must be int in 1..5, got {v!r}"
            )
