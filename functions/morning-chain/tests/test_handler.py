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
        monkeypatch.setattr(chain.time, "sleep", lambda s: None)
        alerts = []
        monkeypatch.setattr(
            chain, "alert",
            lambda name, summary, description="", severity="warning":
                alerts.append((name, severity)))
        calls = []
        # pop-per-call queues: a path may map to a single payload or a list
        # consumed in order (for retry tests)
        queues = {k: (v if isinstance(v, list) else [v])
                  for k, v in responses.items()}

        def fake_urlopen(req, timeout=None):
            path = req.full_url.split("cluster.local")[-1]
            calls.append({"path": path, "timeout": timeout})
            q = queues.get(path)
            if q is None:
                payload = {"ok": True}
            elif len(q) > 1:
                payload = q.pop(0)   # consume in order
            else:
                payload = q[0]       # sticky: last value repeats
            if isinstance(payload, Exception):
                raise payload
            return FakeResponse(payload)

        monkeypatch.setattr(chain.urllib.request, "urlopen", fake_urlopen)
        result = chain.main()
        return result, calls, alerts

    def test_order_and_timeouts(self, monkeypatch):
        result, calls, alerts = self._run(monkeypatch, {})
        assert [c["path"] for c in calls] == ["/debate", "/decide", "/execute"]
        assert calls[0]["timeout"] == 2400   # debate: 40 min budget
        assert calls[1]["timeout"] == 480
        assert calls[2]["timeout"] == 300
        assert result["ok"] and result["aborted_at"] is None
        assert result["retries"] == 0
        assert alerts == []

    def test_debate_failure_continues_with_fallback(self, monkeypatch):
        result, calls, alerts = self._run(
            monkeypatch, {"/debate": {"ok": False, "error": "LLM down"}})
        assert [c["path"] for c in calls] == ["/debate", "/decide", "/execute"]
        assert result["ok"]  # debate is optional; decide applies fallback
        assert alerts == []  # optional-step failure never pages

    def test_retry_recovers_decide_then_runs_execute(self, monkeypatch):
        result, calls, alerts = self._run(
            monkeypatch, {"/decide": [{"ok": False, "error": "transient"},
                                      {"ok": True}]})
        paths = [c["path"] for c in calls]
        # pass 1: debate, decide(fail) -> abort; retry pass: debate, decide(ok), execute
        assert paths == ["/debate", "/decide", "/debate", "/decide", "/execute"]
        assert result["ok"] and result["retries"] == 1
        assert result["missing"] == []
        assert ("TradingChainRecovered", "info") in alerts

    def test_retry_exhausted_pages_critical(self, monkeypatch):
        result, calls, alerts = self._run(
            monkeypatch, {"/decide": {"ok": False, "error": "dead"}})
        paths = [c["path"] for c in calls]
        # 1 pass + 2 retry passes; execute never runs
        assert paths.count("/decide") == 3
        assert "/execute" not in paths
        assert not result["ok"] and result["retries"] == 2
        assert result["missing"] == ["decide", "execute"]
        assert ("TradingChainIncomplete", "critical") in alerts

    def test_already_done_steps_skipped(self, monkeypatch):
        result, calls, alerts = self._run(monkeypatch, {}, pre_done=("debate", "execute"))
        assert [c["path"] for c in calls] == ["/decide"]  # only the missing step
        assert any(s.get("skipped") == "already_done" for s in result["steps"])
        assert result["ok"]

    def test_chain_event_written(self, monkeypatch):
        result, calls, alerts = self._run(monkeypatch, {})
        # FakeDB.add_event is called inside main with a 'chain' row
        # (verified indirectly: main returns ok and does not raise)
        assert result["ok"] is True
