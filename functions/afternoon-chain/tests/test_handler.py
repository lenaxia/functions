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
        assert [c["path"] for c in calls] == ["/score", "/digest"]
        assert calls[0]["timeout"] == 900
        assert calls[1]["timeout"] == 120

    def test_digest_runs_even_when_score_fails(self, monkeypatch):
        result, calls = self._run(monkeypatch, {"/score": {"ok": False, "error": "x"}})
        assert [c["path"] for c in calls] == ["/score", "/digest"]
        assert not result["ok"]  # chain reports the score failure...

    def test_skip_done_steps(self, monkeypatch):
        result, calls = self._run(monkeypatch, {}, pre_done=("score",))
        assert [c["path"] for c in calls] == ["/digest"]
