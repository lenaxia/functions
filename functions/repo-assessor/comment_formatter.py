"""Format an Assessment + BaselineProfile into a single Reddit markdown comment.

Constraints:
- Output ≤ 10000 chars (Reddit limit).
- No unescaped pipes inside table cells.
- Footer with disclosure URLs always present.
- Truncation strategy: shorten evidence → drop strengths → drop security table.
"""

from __future__ import annotations

from typing import Any

from model import Assessment, BaselineProfile


MAX_OUTPUT_CHARS = 10000
MIN_OUTPUT_CHARS = 2000
_TRUNCATE_HINTS_MAX = 200


def format_comment(assessment: Assessment, baseline: BaselineProfile, config: dict | Any) -> str:
    return _CommentBuilder(assessment, baseline, config).build()


class _CommentBuilder:
    def __init__(self, assessment: Assessment, baseline: BaselineProfile, config: dict | Any) -> None:
        self._a = assessment
        self._b = baseline
        self._c = config
        self._drop_strengths = False
        self._drop_security = False
        self._evidence_cap = 0  # 0 = no cap

    def build(self) -> str:
        rendered = self._render_full()
        if len(rendered) <= MAX_OUTPUT_CHARS:
            return rendered

        for cap in (500, 250, 100, 50, 20):
            self._evidence_cap = cap
            rendered = self._render_full()
            if len(rendered) <= MAX_OUTPUT_CHARS:
                return rendered

        self._drop_strengths = True
        rendered = self._render_full()
        if len(rendered) <= MAX_OUTPUT_CHARS:
            return rendered

        self._drop_security = True
        rendered = self._render_full()
        if len(rendered) <= MAX_OUTPUT_CHARS:
            return rendered

        if len(rendered) < MIN_OUTPUT_CHARS:
            raise ValueError(
                f"comment truncated below MIN_OUTPUT_CHARS ({MIN_OUTPUT_CHARS}): {len(rendered)}"
            )
        return rendered[:MAX_OUTPUT_CHARS]

    def _render_full(self) -> str:
        parts: list[str] = []
        parts.append(self._header())
        parts.append(self._metadata_table())
        parts.append(self._scores_table())
        if not self._drop_security:
            parts.append(self._security_table())
        parts.append(self._ai_usage_block())
        parts.append(self._concerns_block())
        if not self._drop_strengths:
            parts.append(self._strengths_block())
        parts.append(self._tldr_block())
        parts.append(self._overall_block())
        parts.append(self._footer())
        return "\n\n".join(parts)

    def _header(self) -> str:
        return f"**Repo assessment** — {self._a.metadata.repo_url}"

    def _metadata_table(self) -> str:
        m = self._a.metadata
        rows = [
            ("Age", _fmt_int(m.repo_age_days), _fmt_int(self._b.repo_age_days)),
            ("Avg commits/month", _fmt_float(m.avg_commits_per_month), _fmt_float(self._b.avg_commits_per_month)),
            ("Commits last 3mo", _fmt_pct(m.pct_commits_last_3_months), _fmt_pct(self._b.pct_commits_last_3_months)),
            ("Contributors", _fmt_int(m.contributors), _fmt_int(self._b.contributors)),
            ("License", m.license or "—", self._b.license),
            ("Primary language", m.primary_language or "—", "—"),
        ]
        header = f"| Metric | This repo | Baseline ({self._b.name}) |\n|---|---|---|"
        body = "\n".join(f"| {label} | {a} | {b} |" for label, a, b in rows)
        return header + "\n" + body

    def _scores_table(self) -> str:
        baseline_scores = self._b.scores
        a = self._a
        rows = [
            ("Code quality", a.code_quality, baseline_scores.get("code_quality")),
            ("Release/CI", a.release_ci_flow, baseline_scores.get("release_ci_flow")),
            ("Test/code ratio", a.test_to_code_ratio[1], baseline_scores.get("test_to_code_ratio")),
            ("Architecture", a.architectural_robustness, baseline_scores.get("architectural_robustness")),
        ]
        header = (
            f"**Scores** (1-5, 5=best; vs {self._b.name}):\n\n"
            "| Dimension | Score | vs baseline | Evidence |\n|---|---|---|---|"
        )
        body_lines = []
        for label, score_ev, base_score in rows:
            delta = _delta_str(score_ev.score, base_score) if base_score is not None else "—"
            evidence = _cap(score_ev.evidence, self._evidence_cap)
            body_lines.append(
                f"| {label} | {score_ev.score} | {delta} | {_escape_pipe(evidence)} |"
            )
        ratio = a.test_to_code_ratio[0]
        return header + "\n" + "\n".join(body_lines) + f"\n\n*Test/code ratio: {_fmt_float(ratio)}*"

    def _security_table(self) -> str:
        baseline_sec = self._b.security
        a = self._a.security
        rows = [
            ("Internal-only", a.internal_only, baseline_sec.get("internal_only")),
            ("Reverse proxy", a.reverse_proxy, baseline_sec.get("reverse_proxy")),
            ("SSO exposed", a.sso, baseline_sec.get("sso")),
        ]
        header = "**Security posture by deployment**:\n\n| Deployment | Score | vs baseline | Evidence |\n|---|---|---|---|"
        body = []
        for label, score_ev, base_score in rows:
            delta = _delta_str(score_ev.score, base_score) if base_score is not None else "—"
            evidence = _cap(score_ev.evidence, self._evidence_cap)
            body.append(f"| {label} | {score_ev.score} | {delta} | {_escape_pipe(evidence)} |")
        return header + "\n" + "\n".join(body)

    def _ai_usage_block(self) -> str:
        ai = self._a.ai_usage
        return f"**AI usage**: {ai.level.value} — {_escape_pipe(ai.evidence)}"

    def _concerns_block(self) -> str:
        if not self._a.key_concerns:
            return "**Concerns**: —"
        bullets = "\n".join(f"* {_escape_pipe(c)}" for c in self._a.key_concerns)
        return f"**Concerns**:\n\n{bullets}"

    def _strengths_block(self) -> str:
        if not self._a.key_strengths:
            return "**Strengths**: —"
        bullets = "\n".join(f"* {_escape_pipe(s)}" for s in self._a.key_strengths)
        return f"**Strengths**:\n\n{bullets}"

    def _tldr_block(self) -> str:
        return f"**TL;DR**: {_escape_pipe(self._a.tldr)}"

    def _overall_block(self) -> str:
        return f"Overall concern vs {self._b.name}: **{self._a.overall_concern_vs_baseline.value}**."

    def _footer(self) -> str:
        source = self._c["bot_source_url"]
        issues = self._c["bot_issues_url"]
        lss = self._c["llmsafespaces_footer_url"]
        return (
            "^(^(*Bot-generated assessment. Scores and evidence are produced by an AI agent "
            f"running in an isolated [LLMSafeSpaces]({lss}) workspace. "
            f"Source: [repo-assessor]({source}). "
            f"Concerns or bugs: [open an issue]({issues}).*)^)"
        )


def _fmt_int(v: int | None) -> str:
    return str(v) if v is not None else "—"


def _fmt_float(v: float | None) -> str:
    return f"{v:.1f}" if v is not None else "—"


def _fmt_pct(v: float | None) -> str:
    return f"{v:.1f}%" if v is not None else "—"


def _delta_str(this: int, baseline: int | None) -> str:
    if baseline is None:
        return "—"
    diff = this - baseline
    if diff > 0:
        return f"+{diff}"
    if diff < 0:
        return str(diff)
    return "="


def _cap(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _escape_pipe(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
