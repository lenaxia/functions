import sys
from pathlib import Path

# make include/ + function code importable when run from the function dir
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))
sys.path.insert(0, str(ROOT / "functions" / "trading-seed"))


class FakeDB:
    """Minimal in-memory TradingDB for offline tests."""

    def __init__(self):
        self.decisions = {}
        self.steps = set()
        self.events = []
        self.equity = {}
        self.positions_state = {}
        self.fills = []

    def step_done(self, run_date, step):
        return (run_date, step) in self.steps

    def mark_step(self, run_date, step):
        self.steps.add((run_date, step))

    def record_decision(self, run_date, step, payload, status="ok"):
        self.decisions[(run_date, step)] = {"payload": payload, "status": status}

    def get_decision(self, run_date, step):
        return self.decisions.get((run_date, step))

    def add_event(self, type_, detail):
        self.events.append((type_, detail))

    def upsert_equity(self, strategy, as_of, equity):
        self.equity[(strategy, as_of)] = equity

    def positions(self):
        return self.positions_state

    def set_positions(self, positions):
        self.positions_state = {
            s: p for s, p in (positions or {}).items()
            if int(p.get("qty") or 0) > 0}

    def record_fill(self, *a, **k):
        self.fills.append((a, k))

    def trades_today(self, run_date):
        return 0

    def close(self):
        pass
