import sys
from pathlib import Path

# make include/ + THIS function's own code importable when run from the
# function dir (each suite must import its own main.py, never a sibling's)
ROOT = Path(__file__).resolve().parents[3]
_FUNCTION_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "include"))
sys.path.insert(0, str(_FUNCTION_DIR))


class FakeDB:
    """In-memory TradingDB covering the read paths status/ uses."""

    def __init__(self):
        self.decisions = {}
        self.steps = set()
        self.events = []          # (id, ts, type, detail)
        self.equity = {}
        self.positions_state = {}
        self._event_id = 0

    def step_done(self, run_date, step):
        return (run_date, step) in self.steps

    def mark_step(self, run_date, step):
        self.steps.add((run_date, step))

    def record_decision(self, run_date, step, payload, status="ok"):
        self.decisions[(run_date, step)] = {"payload": payload, "status": status}

    def get_decision(self, run_date, step):
        row = self.decisions.get((run_date, step))
        return {"payload": row["payload"], "status": row["status"]} if row else None

    def add_event(self, type_, detail):
        self._event_id += 1
        from datetime import datetime, timezone
        self.events.append((self._event_id, datetime.now(timezone.utc),
                            type_, detail))

    def upsert_equity(self, strategy, as_of, equity):
        self.equity[(strategy, as_of)] = equity

    def positions(self):
        return self.positions_state

    def one(self, sql, params=()):
        halt = [e for e in self.events if e[2] in ("halt_set", "halt_cleared")]
        if "halt" in sql:
            return (halt[-1][2],) if halt else None
        if "step = 'execute'" in sql:
            rows = [(d, r["payload"]) for (d, s), r in self.decisions.items()
                    if s == "execute"]
            return max(rows) if rows else None
        if "type = %s" in sql:
            t = params[0]
            matches = [e for e in self.events if e[2] == t]
            return (matches[-1][1], matches[-1][3]) if matches else None
        return None

    def all(self, sql, params=()):
        if "equity_series" in sql:
            best = {}
            for (strategy, as_of), equity in self.equity.items():
                if strategy not in best or as_of > best[strategy][0]:
                    best[strategy] = (as_of, equity)
            return [(s, d, e) for s, (d, e) in best.items()]
        if "step_completions" in sql:
            since = params[0]
            return [(d, s, ts) for (d, s), ts in
                    sorted(((k, f"{k[0]}T00:00:00Z") for k in self.steps),
                           reverse=True) if d >= since]
        if "FROM events" in sql and "WHERE" not in sql:
            return [(e[0], e[1], e[2]) for e in reversed(self.events[-10:])]
        return []

    def close(self):
        pass
