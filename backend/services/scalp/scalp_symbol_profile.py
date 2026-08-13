"""选币 × 短线因子联动画像（M2，只读计算 + 画像表写入）。

对每个候选币汇总：
- 信号级：近 N 天信号数、胜率、平均净收益
- 交易级：纸盘 scalp 笔数、胜率、净盈亏、每笔期望（PPE）
- 数据级：5m 可用 K 线数、完整性 %

写表：symbol_scalp_profile（upsert），供 auto_coin / AI 选币 agent 只读引用。
默认不参与选币分数，仅展示与告警。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]


def _round(v: Any, nd: int = 4) -> Any:
    try:
        return round(float(v), nd)
    except Exception:
        return None


def build_symbol_scalp_profile(days: int = 30,
                               exchange: Optional[str] = None) -> Dict[str, Any]:
    from backend.database.connection import SessionLocal, MarketSessionLocal
    from backend.core.tenant import system_identity

    start = datetime.now(timezone.utc) - timedelta(days=days)
    start_ts = int(start.timestamp())
    symbols: Dict[str, Dict[str, Any]] = {}

    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT symbol, COUNT(*) AS n, "
                    "COUNT(*) FILTER (WHERE settled AND win IS NOT NULL AND win) AS n_win_settled, "
                    "COUNT(*) FILTER (WHERE settled AND win IS NOT NULL) AS n_settled, "
                    "COALESCE(AVG(net_ret) FILTER (WHERE settled AND win IS NOT NULL), 0) AS avg_net "
                    "FROM scalp_signal_log WHERE signal_ts >= :start "
                    "GROUP BY symbol"
                ),
                {"start": start_ts},
            ).mappings().all()
            for r in rows:
                sym = str(r["symbol"])
                n_settled = int(r["n_settled"] or 0)
                symbols.setdefault(sym, {})["signal"] = {
                    "n": int(r["n"] or 0),
                    "n_settled": n_settled,
                    "win_rate": _round(
                        (r["n_win_settled"] or 0) / n_settled if n_settled else 0.0, 4
                    ),
                    "avg_net_ret": _round(r["avg_net"], 6),
                }

            rows = db.execute(
                text(
                    "SELECT symbol, COUNT(*) AS n, "
                    "COUNT(*) FILTER (WHERE pnl > 0) AS n_win, "
                    "COALESCE(SUM(pnl), 0) AS pnl, "
                    "COALESCE(SUM(pnl) FILTER (WHERE pnl > 0), 0) AS gross_win, "
                    "COALESCE(SUM(pnl) FILTER (WHERE pnl < 0), 0) AS gross_loss "
                    "FROM paper_orders "
                    "WHERE trade_nature = 'scalp' AND status = 'filled' "
                    "AND pnl IS NOT NULL AND created_at >= :start "
                    "GROUP BY symbol"
                ),
                {"start": start},
            ).mappings().all()
            for r in rows:
                sym = str(r["symbol"])
                n = int(r["n"] or 0)
                pnl = float(r["pnl"] or 0.0)
                gross_win = float(r["gross_win"] or 0.0)
                gross_loss = float(r["gross_loss"] or 0.0)
                symbols.setdefault(sym, {})["paper"] = {
                    "n": n,
                    "win_rate": _round((r["n_win"] or 0) / n if n else 0.0, 4),
                    "net_pnl": _round(pnl, 4),
                    "ppe": _round(pnl / n if n else 0.0, 6),
                    "profit_factor": _round(gross_win / abs(gross_loss), 4) if gross_loss else None,
                }

        with MarketSessionLocal() as db:
            ex_clause = ""
            params: Dict[str, Any] = {"start": start_ts}
            if exchange:
                ex_clause = " AND exchange = :ex"
                params["ex"] = exchange
            rows = db.execute(
                text(
                    "SELECT symbol, COUNT(DISTINCT timestamp) AS n, "
                    "MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts "
                    "FROM crypto_klines WHERE period='5m' AND timestamp >= :start%s "
                    "GROUP BY symbol" % ex_clause
                ),
                params,
            ).mappings().all()
            for r in rows:
                sym = str(r["symbol"])
                n = int(r["n"] or 0)
                first_ts = int(r["first_ts"] or 0)
                last_ts = int(r["last_ts"] or 0)
                span_days = max(1.0, (last_ts - first_ts) / 86400.0) if last_ts else 0.0
                expected = int(span_days * 288) + 1 if span_days else 0
                completeness = min(100.0, n / max(1, expected) * 100.0) if expected else 0.0
                symbols.setdefault(sym, {})["data"] = {
                    "bars_5m": n,
                    "completeness_pct": round(completeness, 2),
                    "span_days": round(span_days, 2),
                }

        # 写画像表（upsert）
        with SessionLocal() as db:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS symbol_scalp_profile ("
                " symbol VARCHAR(32) PRIMARY KEY,"
                " report_date DATE NOT NULL,"
                " signals_n INT, signals_win_rate DOUBLE PRECISION,"
                " signals_avg_net_ret DOUBLE PRECISION,"
                " trades_n INT, trades_win_rate DOUBLE PRECISION,"
                " trades_net_pnl DOUBLE PRECISION, trades_ppe DOUBLE PRECISION,"
                " trades_profit_factor DOUBLE PRECISION,"
                " data_bars_5m INT, data_completeness_pct DOUBLE PRECISION,"
                " data_span_days DOUBLE PRECISION,"
                " updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            ))
            today = datetime.now(timezone.utc).date()
            for sym, rec in symbols.items():
                sig = rec.get("signal") or {}
                pap = rec.get("paper") or {}
                dat = rec.get("data") or {}
                db.execute(
                    text(
                        "INSERT INTO symbol_scalp_profile "
                        "(symbol, report_date, signals_n, signals_win_rate, signals_avg_net_ret, "
                        " trades_n, trades_win_rate, trades_net_pnl, trades_ppe, trades_profit_factor, "
                        " data_bars_5m, data_completeness_pct, data_span_days, updated_at) "
                        "VALUES (:symbol, :report_date, :sn, :swr, :snet, "
                        " :tn, :twr, :tnet, :tppe, :tpf, "
                        " :bars, :comp, :span, now()) "
                        "ON CONFLICT (symbol) DO UPDATE SET "
                        " report_date = EXCLUDED.report_date,"
                        " signals_n = EXCLUDED.signals_n, signals_win_rate = EXCLUDED.signals_win_rate,"
                        " signals_avg_net_ret = EXCLUDED.signals_avg_net_ret,"
                        " trades_n = EXCLUDED.trades_n, trades_win_rate = EXCLUDED.trades_win_rate,"
                        " trades_net_pnl = EXCLUDED.trades_net_pnl, trades_ppe = EXCLUDED.trades_ppe,"
                        " trades_profit_factor = EXCLUDED.trades_profit_factor,"
                        " data_bars_5m = EXCLUDED.data_bars_5m,"
                        " data_completeness_pct = EXCLUDED.data_completeness_pct,"
                        " data_span_days = EXCLUDED.data_span_days,"
                        " updated_at = now()"
                    ),
                    {
                        "symbol": sym,
                        "report_date": today,
                        "sn": sig.get("n"), "swr": sig.get("win_rate"),
                        "snet": sig.get("avg_net_ret"),
                        "tn": pap.get("n"), "twr": pap.get("win_rate"),
                        "tnet": pap.get("net_pnl"), "tppe": pap.get("ppe"),
                        "tpf": pap.get("profit_factor"),
                        "bars": dat.get("bars_5m"), "comp": dat.get("completeness_pct"),
                        "span": dat.get("span_days"),
                    },
                )
            db.commit()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "exchange": exchange,
        "n_symbols": len(symbols),
        "symbols": symbols,
    }
    out_dir = _ROOT / "reports" / "scalp_symbols"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("scalp_symbol_profile_%s.json" % datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("选币画像已更新: %s", path)
    try:
        from backend.services.scalp.scalp_heartbeat import touch
        touch("scalp_symbol_profile", "ok", {"n_symbols": len(symbols)})
    except Exception:
        pass
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="选币×因子画像")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--exchange", default=None)
    args = ap.parse_args()
    rep = build_symbol_scalp_profile(days=args.days, exchange=args.exchange)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
