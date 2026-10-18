"""Postgres access for the trading functions (CloudNativePG `tradingpg`).

Follows the repo-assessor postgres backend conventions: psycopg3,
autocommit, connect retries, parameterised queries only, schema applied
idempotently on connect from the bundled schema.sql.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .secrets import secret

_LOG = logging.getLogger(__name__)

SCHEMA_SQL = (Path(__file__).resolve().parent / "schema.sql").read_text()


def database_url() -> str:
    url = secret("DATABASE_URL")
    if url:
        return url
    host = secret("PGHOST", "tradingpg-rw.databases.svc.cluster.local")
    port = secret("PGPORT", "5432")
    db = secret("PGDATABASE", "trading")
    user = secret("PGUSER", "trading")
    pwd = secret("PGPASSWORD")
    return "postgresql://%s:%s@%s:%s/%s" % (user, pwd, host, port, db)


class TradingDB:
    def __init__(self, url: str | None = None, *,
                 connect_retries: int = 3, connect_retry_delay: float = 1.0,
                 ensure_schema: bool = True) -> None:
        import psycopg  # psycopg[binary] from requirements.txt

        self._closed = False
        last_err: Exception | None = None
        conn = None
        for attempt in range(connect_retries):
            try:
                conn = psycopg.connect(url or database_url(), autocommit=True)
                break
            except Exception as e:  # noqa: BLE001 - retry transient connect errors
                last_err = e
                _LOG.warning("postgres connect attempt %d/%d failed: %s",
                             attempt + 1, connect_retries, e)
                if attempt < connect_retries - 1:
                    time.sleep(connect_retry_delay)
        if conn is None:
            raise RuntimeError("postgres connect failed after %d attempts: %s"
                               % (connect_retries, last_err))
        self._conn = conn
        if ensure_schema:
            with self._conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)

    # -- low-level helpers (repo-assessor pattern) --------------------------
    def exec(self, sql: str, params: tuple = ()) -> Any:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur

    def one(self, sql: str, params: tuple = ()) -> tuple | None:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def all(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._conn.close()
            except Exception:
                pass

    # -- domain helpers ------------------------------------------------------
    def step_done(self, run_date: str, step: str) -> bool:
        row = self.one(
            "SELECT ts FROM step_completions WHERE run_date = %s AND step = %s",
            (run_date, step))
        return row is not None

    def mark_step(self, run_date: str, step: str) -> None:
        self.exec(
            "INSERT INTO step_completions (run_date, step, ts) VALUES (%s, %s, now()) "
            "ON CONFLICT (run_date, step) DO UPDATE SET ts = now()",
            (run_date, step))

    def record_decision(self, run_date: str, step: str,
                        payload: dict, status: str = "ok") -> None:
        self.exec(
            "INSERT INTO decisions (run_date, step, payload, status, updated_at) "
            "VALUES (%s, %s, %s::jsonb, %s, now()) "
            "ON CONFLICT (run_date, step) DO UPDATE SET "
            "  payload = EXCLUDED.payload, status = EXCLUDED.status, updated_at = now()",
            (run_date, step, json.dumps(payload, default=str), status))

    def get_decision(self, run_date: str, step: str) -> dict | None:
        row = self.one(
            "SELECT payload, status FROM decisions WHERE run_date = %s AND step = %s",
            (run_date, step))
        if row is None:
            return None
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return {"payload": payload, "status": row[1]}

    def add_event(self, type_: str, detail: dict) -> None:
        self.exec(
            "INSERT INTO events (ts, type, detail) VALUES (now(), %s, %s::jsonb)",
            (type_, json.dumps(detail, default=str)))

    def upsert_equity(self, strategy: str, as_of: str, equity: float) -> None:
        self.exec(
            "INSERT INTO equity_series (strategy, as_of, equity) VALUES (%s, %s, %s) "
            "ON CONFLICT (strategy, as_of) DO UPDATE SET equity = EXCLUDED.equity",
            (strategy, as_of, round(float(equity), 2)))

    def equity_series(self, strategy: str) -> list[tuple[str, float]]:
        rows = self.all(
            "SELECT as_of::text, equity FROM equity_series "
            "WHERE strategy = %s ORDER BY as_of", (strategy,))
        return [(r[0], float(r[1])) for r in rows]

    def positions(self) -> dict[str, dict]:
        rows = self.all("SELECT symbol, qty, entry FROM positions WHERE qty > 0")
        return {r[0]: {"qty": int(r[1]), "entry": float(r[2])} for r in rows}

    def set_positions(self, positions: dict[str, dict]) -> None:
        """Replace the whole book (used by seed / shadow re-sync)."""
        self.exec("DELETE FROM positions")
        for sym, p in sorted((positions or {}).items()):
            if int(p.get("qty") or 0) > 0:
                self.exec(
                    "INSERT INTO positions (symbol, qty, entry) VALUES (%s, %s, %s) "
                    "ON CONFLICT (symbol) DO UPDATE SET qty = EXCLUDED.qty, entry = EXCLUDED.entry",
                    (sym.upper(), int(p["qty"]), float(p.get("entry") or 0.0)))

    def record_fill(self, symbol: str, side: str, qty: int,
                    price: float | None, assumed_price: float | None,
                    note: str = "", shadow: bool = False) -> None:
        slip = None
        if price and assumed_price:
            slip = round((float(price) / float(assumed_price) - 1.0) * 10000.0, 2)
        self.exec(
            "INSERT INTO fills (ts, symbol, side, qty, price, assumed_price, "
            "slippage_bps, note, shadow) VALUES (now(), %s, %s, %s, %s, %s, %s, %s, %s)",
            (symbol, side, int(qty), price, assumed_price, slip, note, shadow))

    def trades_today(self, run_date: str) -> int:
        row = self.one(
            "SELECT count(*) FROM fills WHERE ts::date::text = %s AND shadow = false",
            (run_date,))
        return int(row[0]) if row else 0


def utc_today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
