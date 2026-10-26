import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))
sys.path.insert(0, str(ROOT / "functions" / "afternoon-chain"))


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestAfternoonChain:
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
        assert [c["path"] for c in calls] == ["/score", "/digest"]
        assert calls[0]["timeout"] == 900
        assert calls[1]["timeout"] == 120
        assert result["ok"] and result["retries"] == 0
        assert alerts == []

    def test_digest_runs_even_when_score_fails(self, monkeypatch):
        result, calls, alerts = self._run(
            monkeypatch, {"/score": {"ok": False, "error": "x"}})
        paths = [c["path"] for c in calls]
        # pass 1: score(fail), digest; 2 retry passes: score(fail), digest
        assert paths.count("/score") == 3
        assert paths.count("/digest") == 3  # digest re-runs each pass until done
        assert not result["ok"]
        assert ("TradingChainIncomplete", "critical") in alerts

    def test_retry_recovers_score(self, monkeypatch):
        result, calls, alerts = self._run(
            monkeypatch, {"/score": [{"ok": False, "error": "yahoo 5xx"},
                                     {"ok": True}]})
        paths = [c["path"] for c in calls]
        # pass1: score(fail), digest(ok); pass2: score(ok), digest skipped? no:
        # FakeDB doesn't record completions on responses, digest is re-called
        assert paths == ["/score", "/digest", "/score", "/digest"]
        assert result["ok"] and result["retries"] == 1
        assert ("TradingChainRecovered", "info") in alerts

    def test_skip_done_steps(self, monkeypatch):
        result, calls, alerts = self._run(monkeypatch, {}, pre_done=("score",))
        assert [c["path"] for c in calls] == ["/digest"]
        assert result["ok"]
