import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))
sys.path.insert(0, str(ROOT / "functions" / "morning-chain"))


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestMorningChain:
    def _run(self, monkeypatch, responses, pre_done=()):
        import main as chain
        from conftest import FakeDB
        db = FakeDB()
        for step in pre_done:
            db.steps.add((chain.utc_today(), step))
        monkeypatch.setattr(chain, "TradingDB", lambda: db)
        calls = []

        def fake_urlopen(req, timeout=None):
            path = req.full_url.split("cluster.local")[-1]
            calls.append({"path": path, "timeout": timeout})
            payload = responses.get(path, {"ok": True})
            if isinstance(payload, Exception):
                raise payload
            return FakeResponse(payload)

        monkeypatch.setattr(chain.urllib.request, "urlopen", fake_urlopen)
        return chain.main(), calls

    def test_order_and_timeouts(self, monkeypatch):
        result, calls = self._run(monkeypatch, {})
        assert [c["path"] for c in calls] == ["/debate", "/decide", "/execute"]
        assert calls[0]["timeout"] == 2400   # debate: 40 min budget
        assert calls[1]["timeout"] == 480
        assert calls[2]["timeout"] == 300
        assert result["ok"] and result["aborted_at"] is None

    def test_abort_when_decide_fails(self, monkeypatch):
        result, calls = self._run(monkeypatch, {"/decide": {"ok": False, "error": "boom"}})
        assert [c["path"] for c in calls] == ["/debate", "/decide"]
        assert not result["ok"] and result["aborted_at"] == "decide"

    def test_debate_failure_continues_with_fallback(self, monkeypatch):
        result, calls = self._run(
            monkeypatch, {"/debate": {"ok": False, "error": "LLM down"}})
        assert [c["path"] for c in calls] == ["/debate", "/decide", "/execute"]
        assert result["ok"]  # debate is optional; decide applies fallback

    def test_already_done_steps_skipped(self, monkeypatch):
        result, calls = self._run(monkeypatch, {}, pre_done=("debate", "execute"))
        assert [c["path"] for c in calls] == ["/decide"]  # only the missing step
        assert any(s.get("skipped") == "already_done" for s in result["steps"])
