# -*- coding: utf-8 -*-
"""[h650] 研究员输入采集:把车道事实组装成 {S1..S4}(纯读取,不做算术推理)。

  S1 = 心跳口径的闸门计数(skip_counts / gate_probe_counts / direction_card / 日亏);
  S2 = lane_ledger 开仓腿 × 心跳 mid 的 quote-time markout 聚合(逐币×逐边);
  S3 = 逐币最新市场价差(alpha_market.asterdex_book_ticker,失败则空并如实标注);
  S4 = 已排除路径(由 researcher.KNOWN_LAWS 承担,这里只给编号引用)。

所有来源失败都要显式出现在返回值里,研究员不得替数据源补数。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from backend.services.market_maker.direction_card import (  # noqa: E402
    direction_rows_from_legs, fetch_open_legs,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
STATUS = ROOT / "logs" / "mm_lane_status.json"

SYM_SUFFIX = {"BNB": "BNBUSDT", "NEAR": "NEARUSDT", "ARB": "ARBUSDT",
              "XRP": "XRPUSDT", "ENA": "ENAUSDT"}


def _read_status() -> Dict[str, Any]:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception as e:  # 心跳缺失 = 事实缺失,如实标注
        return {"_error": f"心跳不可读: {type(e).__name__}: {e}"}


def _market_spreads(symbols: list) -> Dict[str, Any]:
    """逐币最新 (bid, ask) 价差 bp(alpha_market 库);失败返回 {'_error': ...}。"""
    try:
        import psycopg

        env: Dict[str, str] = {}
        for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
        url = env.get("MARKET_DATABASE_URL") or env.get("DATABASE_URL") or ""
        for x in ("+psycopg2", "+psycopg", "+asyncpg"):
            url = url.replace(x, "")
        if "alpha_market" not in url:
            head, _, _ = url.rpartition("/")
            url = head + "/alpha_market"
        out: Dict[str, Any] = {}
        with psycopg.connect(url, autocommit=True) as c, c.cursor() as cur:
            for sym in symbols:
                vs = SYM_SUFFIX.get(sym, sym + "USDT")
                cur.execute(
                    "SELECT bid_px, ask_px FROM asterdex_book_ticker"
                    " WHERE symbol=%s ORDER BY event_ts_ms DESC LIMIT 1", (vs,))
                row = cur.fetchone()
                if row and row[0] and row[1] and row[1] > row[0]:
                    mid = (float(row[0]) + float(row[1])) / 2.0
                    out[sym] = {"spread_bp": round((float(row[1]) - float(row[0])) / mid * 1e4, 2)}
                else:
                    out[sym] = {"spread_bp": None}
        return out
    except Exception as e:
        return {"_error": f"市场价差不可读: {type(e).__name__}: {e}"}


def gather_researcher_stats(*, lane_id: str, hours: float = 4.0) -> Dict[str, Any]:
    """组装 {S1..S4}。任何子源失败都以 _error 键显式出现。"""
    status = _read_status()
    s1: Dict[str, Any] = {"skip_counts": status.get("skip_counts"),
                          "gate_probe_counts": status.get("gate_probe_counts"),
                          "direction_card": status.get("direction_card"),
                          "day_pnl_usd": status.get("day_pnl_usd"),
                          "fills_per_hour": status.get("fills_per_hour")}
    states = status.get("states") or {}
    symbols = [str(s) for s in (status.get("symbols") or []) if str(s)]
    mids: Dict[str, float] = {}
    for sym, st in states.items():
        if isinstance(st, dict):
            mid = float(st.get("quote_mid") or 0.0)
            if mid > 0:
                mids[str(sym)] = mid

    s2: Dict[str, Any]
    try:
        legs = fetch_open_legs(lane_id=lane_id, minutes=float(hours) * 60.0)
        rows = direction_rows_from_legs(legs, mids)
        s2 = {"rows": rows, "n_open_legs": len(legs)}
    except Exception as e:
        s2 = {"_error": f"开仓腿归因不可读: {type(e).__name__}: {e}"}

    s3 = _market_spreads(symbols)
    s4 = ["KNOWNS:见 system prompt 已证定律 1-7;"
          "防重复:OFI预测逆选择(负)/成交规模信号(负)/h284被动悲观(不可用)"]
    if status.get("_error"):
        s1["_error"] = status["_error"]
    return {"gate_stats": s1, "leg_stats": s2, "symbol_stats": s3, "excluded": s4,
            "symbols": symbols, "lane_id": lane_id}
