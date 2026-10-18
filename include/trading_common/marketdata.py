"""Live US-stock snapshot — verbatim port of ai-trading/marketdata.py.

Only change: the universe.txt file read is replaced by an embedded constant
(Fission packages have no repo checkout). Yahoo Finance chart API, per-symbol
independent degradation, identical output shape.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
DEFAULTS = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "META", "GOOGL", "AMD"]

# From ai-trading/universe.txt (~110 liquid names; comments stripped).
UNIVERSE_FILE_LINES = """
AAPL ABBV ABT ACN ADBE AMD AMGN AMT AMZN AVGO AXP BA BAC BK BKNG BLK BMY BRK-B C CAT CHTR CL CMCSA COF COP COST CRM CSCO CVS CVX DE DHR DIS DUK EMR ETN F FDX GD GE GEV GILD GOOG GOOGL GS HD HON IBM INTC INTU ISRG JNJ JPM KHC KO LIN LLY LMT LOW MA MCD MDLZ MDT MET META MMM MO MRK MS MSFT NEE NFLX NKE NVDA ORCL PEP PFE PG PLTR PM PYPL QCOM RTX SBUX SCHW SO SPG T TGT TMO TMUS TSLA TXN UNH UNP UPS V VST VZ WFC WMT XOM
MU WDC PGR ODFL CRWD PANW ANET SMCI CEG APP NET DASH
"""


def _universe() -> list[str]:
    out: list[str] = []
    for line in UNIVERSE_FILE_LINES.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.extend(line.split())
    return [t.upper() for t in out]


def get(url: str, timeout: int = 12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def yahoo(sym: str) -> dict:
    d = get("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=5d" % sym)
    res = d["chart"]["result"][0]
    meta = res["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    chg_1d = round((price / prev - 1) * 100, 2) if (price and prev) else None
    closes = []
    try:
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    except Exception:
        pass
    chg_5d = round((price / closes[0] - 1) * 100, 2) if (price and len(closes) >= 2) else None
    return {
        "sym": sym,
        "price": price,
        "prev_close": prev,
        "chg_1d_pct": chg_1d,
        "chg_5d_pct": chg_5d,
        "week_52_high": meta.get("fiftyTwoWeekHigh"),
        "week_52_low": meta.get("fiftyTwoWeekLow"),
        "regular_market_time": meta.get("regularMarketTime"),
    }


def snapshot(extra: list[str] | None = None) -> dict:
    """Port of marketdata.py main(): one snapshot dict."""
    extra = [a.upper() for a in (extra or []) if a.isalpha() or "-" in a]
    syms = list(dict.fromkeys(DEFAULTS + _universe() + extra))
    stocks = []
    for s in syms:
        try:
            stocks.append(yahoo(s))
        except Exception as e:  # noqa: BLE001 - per-symbol degradation
            stocks.append({"sym": s, "error": str(e)[:120]})
    return {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "stocks": stocks,
    }
