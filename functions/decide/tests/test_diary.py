"""Trade-diary regression: decide must keep state/log.txt alive in the GHA
daily-run line format — the cutover disabled the workflow that used to
write it, silently blinding the end-of-day digest (2026-10-26..11-05)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "include"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import diary_lines  # noqa: E402

from trading_common import gitstate  # noqa: E402


class TestDiaryLines:
    def test_matches_gha_format(self):
        gate_log = [
            {"tag": "TRADED", "line": "sell LLY x7 stop -5% from 246.15"},
            {"tag": "TRADED", "line": "buy COP x67 momentum: RS +6.73 pts vs SPY"},
            {"tag": "SKIP_BUY", "line": "CVX max 2 trades/day reached"},
            {"tag": "DD_VETO", "line": "PFE thesis excerpt"},
        ]
        lines = diary_lines("2026-11-06T13:46:01Z", ["AAPL", "COP"], gate_log)
        assert lines[0] == "2026-11-06T13:46:01Z DAILY data_snapshot AAPL,COP"
        assert lines[1] == "2026-11-06T13:46:01Z TRADED sell LLY x7 stop -5% from 246.15"
        assert lines[2] == "2026-11-06T13:46:01Z TRADED buy COP x67 momentum: RS +6.73 pts vs SPY"
        assert lines[3] == "2026-11-06T13:46:01Z SKIP_BUY CVX max 2 trades/day reached"
        assert lines[4] == "2026-11-06T13:46:01Z DD_VETO PFE thesis excerpt"

    def test_empty_and_malformed_gate_log(self):
        assert diary_lines("t", [], []) == ["t DAILY data_snapshot "]
        assert diary_lines("t", ["A"], [{"tag": "", "line": "x"}, {"tag": "T"},
                                        {"line": "y"}]) == ["t DAILY data_snapshot A"]


class TestAppendDiaryGuards:
    def test_no_token_is_a_noop_false(self, monkeypatch):
        monkeypatch.setattr(gitstate, "secret", lambda n, d="": "")
        assert gitstate.append_diary(["x DAILY y"]) is False

    def test_empty_lines_is_a_noop_false(self, monkeypatch):
        monkeypatch.setattr(gitstate, "secret", lambda n, d="": "tok")
        assert gitstate.append_diary([]) is False
