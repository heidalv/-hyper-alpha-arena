"""交易对快速因子策略选择器（后端核心，供 AI 选币观察者调用）。

对单个交易对扫描 period × factor_set × threshold 矩阵，硬门禁通过写
pair_strategy_candidates（不自动上线）。可先检查/补齐 K 线。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

EXCHANGE = "asterdex"

PERIOD_CONFIGS = {
    "5m": {"days": 60, "sl": 0.005, "tp": 0.01, "max_hold": 24, "cooldown": 6,
           "min_bars": 4000, "backfill_days": 60},
    "1h": {"days": 180, "sl": 0.01, "tp": 0.02, "max_hold": 12, "cooldown": 3,
           "min_bars": 1000, "backfill_days": 180},
    "4h": {"days": 365, "sl": 0.02, "tp": 0.04, "max_hold": 6, "cooldown": 2,
           "min_bars": 500, "backfill_days": 365},
}
FACTOR_SETS = ["hybrid", "meanrev", "breakout"]
THRESHOLDS = [0.3, 0.5, 0.7]


def ensure_table() -> None:
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    with system_identity():
        with SessionLocal() as db:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS pair_strategy_candidates ("
                " id BIGSERIAL PRIMARY KEY,"
                " symbol VARCHAR(32) NOT NULL,"
                " period VARCHAR(8) NOT NULL,"
                " factor_set VARCHAR(16) NOT NULL,"
                " params_json JSONB NOT NULL,"
                " metrics_json JSONB NOT NULL,"
                " gate_verdict VARCHAR(24) NOT NULL,"
                " generated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            ))
            db.commit()


def _verdict(metrics: Dict[str, Any]) -> str:
    total = metrics.get("total") or {}
    older = metrics.get("older_half") or {}
    newer = metrics.get("newer_half") or {}
    n = int(total.get("n", 0) or 0)
    pf = total.get("profit_factor")
    t = total.get("t_stat")
    older_pf = older.get("profit_factor")
    newer_pf = newer.get("profit_factor")
    if (
        n >= 100 and pf is not None and pf >= 1.0
        and older_pf is not None and older_pf >= 0.95
        and newer_pf is not None and newer_pf >= 0.95
    ):
        return "pass" if (t is not None and t > 1.0) else "promising"
    return "fail"


def _bar_count(symbol: str, period: str) -> int:
    from backend.core.tenant import system_identity
    from backend.database.connection import MarketSessionLocal

    with system_identity():
        with MarketSessionLocal() as db:
            r = db.execute(
                text(
                    "SELECT COUNT(DISTINCT timestamp) AS n FROM crypto_klines "
                    "WHERE symbol = :s AND period = :p AND exchange = :ex"
                ),
                {"s": symbol, "p": period, "ex": EXCHANGE},
            ).mappings().first()
    return int(r["n"] or 0) if r else 0


async def _backfill_period(symbol: str, period: str, days: int,
                           max_wait_sec: int = 300) -> Dict[str, Any]:
    from backend.services.kline_history_sync import KlineHistorySync, SyncStatus

    sync = KlineHistorySync()
    started = await sync.start_sync(symbols=[symbol], periods=[period], days=days,
                                    exchange=EXCHANGE)
    if "error" in started:
        return {"ok": False, "error": started["error"]}
    deadline = time.time() + max_wait_sec
    while sync.progress.status == SyncStatus.RUNNING and time.time() < deadline:
        await asyncio.sleep(5)
    prog = sync.get_progress()
    return {
        "ok": prog.get("status") == "completed",
        "status": prog.get("status"),
        "synced": prog.get("total_records_synced"),
    }


def ensure_data(symbol: str, periods: List[str],
                max_wait_sec: int = 300) -> Dict[str, Any]:
    """检查各周期 K 线覆盖，不足则回填。返回 {period: {ok, bars, action}}。"""
    out: Dict[str, Any] = {}
    for period in periods:
        cfg = PERIOD_CONFIGS.get(period)
        if not cfg:
            continue
        bars = _bar_count(symbol, period)
        entry = {"bars": bars, "min_bars": cfg["min_bars"], "action": "ok"}
        if bars < cfg["min_bars"]:
            try:
                res = asyncio.run(
                    _backfill_period(symbol, period, cfg["backfill_days"], max_wait_sec)
                )
                entry["action"] = "backfilled"
                entry["backfill"] = res
                entry["bars_after"] = _bar_count(symbol, period)
            except Exception as e:
                entry["action"] = "backfill_failed"
                entry["error"] = str(e)[:200]
        out[period] = entry
    return out


def run_pair_selector(symbol: str, periods: Optional[List[str]] = None,
                      ensure_data_first: bool = False,
                      max_wait_sec: int = 300) -> Dict[str, Any]:
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from backend.services.scalp.kline_factor_backtest import run_kline_factor_backtest

    symbol = symbol.upper()
    periods = periods or list(PERIOD_CONFIGS.keys())
    ensure_table()
    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "exchange": EXCHANGE,
        "candidates": [],
    }
    if ensure_data_first:
        report["data"] = ensure_data(symbol, periods, max_wait_sec)
    pass_count = 0
    with system_identity():
        with SessionLocal() as db:
            for period in periods:
                cfg = PERIOD_CONFIGS.get(period)
                if not cfg:
                    continue
                for fs in FACTOR_SETS:
                    for thr in THRESHOLDS:
                        try:
                            res = run_kline_factor_backtest(
                                symbols=[symbol], days=cfg["days"],
                                exchange=EXCHANGE, period=period,
                                threshold=thr, sl_pct=cfg["sl"], tp_pct=cfg["tp"],
                                max_hold_candles=cfg["max_hold"],
                                cooldown_candles=cfg["cooldown"],
                                n_perm=100, scenario="realistic",
                                factor_set=fs, save_report=False,
                            )
                        except Exception as e:
                            logger.warning("[PairSelector] %s %s %s 回测失败: %s",
                                           symbol, period, fs, e)
                            continue
                        total = res.get("total") or {}
                        n = int(total.get("n", 0) or 0)
                        if n < 100:
                            verdict = "insufficient_data"
                        else:
                            verdict = _verdict({
                                "total": total,
                                "older_half": res.get("older_half"),
                                "newer_half": res.get("newer_half"),
                            })
                        params = {
                            "threshold": thr, "z_window": 120,
                            "sl_pct": cfg["sl"], "tp_pct": cfg["tp"],
                            "max_hold_candles": cfg["max_hold"],
                            "cooldown_candles": cfg["cooldown"],
                            "days": cfg["days"],
                        }
                        metrics = {
                            "n": n,
                            "avg_net_ret": total.get("avg_net_ret"),
                            "profit_factor": total.get("profit_factor"),
                            "t_stat": total.get("t_stat"),
                            "permutation_p": total.get("permutation_p"),
                            "older_pf": (res.get("older_half") or {}).get("profit_factor"),
                            "newer_pf": (res.get("newer_half") or {}).get("profit_factor"),
                        }
                        row = db.execute(
                            text(
                                "INSERT INTO pair_strategy_candidates "
                                "(symbol, period, factor_set, params_json, metrics_json, gate_verdict) "
                                "VALUES (:s, :p, :f, :params, :metrics, :v) RETURNING id"
                            ),
                            {
                                "s": symbol, "p": period, "f": fs,
                                "params": json.dumps(params, ensure_ascii=False),
                                "metrics": json.dumps(metrics, ensure_ascii=False),
                                "v": verdict,
                            },
                        ).mappings().first()
                        cand_id = int(row["id"]) if row else None
                        # 每条立即提交，避免长回测期间持有写事务被 LeakGuard 强杀
                        db.commit()
                        if verdict == "pass":
                            pass_count += 1
                        report["candidates"].append({
                            "period": period, "factor_set": fs,
                            "id": cand_id, "params": params,
                            "metrics": metrics, "verdict": verdict,
                        })
            db.commit()
    report["n_pass"] = pass_count
    # 自动晋级：≥2 个 pass 且存在 PF≥1.2 / t≥1.5 的强候选 → 自动绑定（不再手动点）
    try:
        min_pass = int(os.getenv("PAIR_AUTO_PROMOTE_MIN_PASS", "2"))
        min_pf = float(os.getenv("PAIR_AUTO_PROMOTE_MIN_PF", "1.2"))
        min_t = float(os.getenv("PAIR_AUTO_PROMOTE_MIN_T", "1.5"))
        pass_list = [c for c in report["candidates"]
                     if c.get("verdict") == "pass" and c.get("id")]
        strong = [
            c for c in pass_list
            if (c["metrics"].get("profit_factor") or 0) >= min_pf
            and (c["metrics"].get("t_stat") or 0) >= min_t
        ]
        if len(pass_list) >= min_pass and strong:
            best = max(strong, key=lambda c: c["metrics"]["profit_factor"] or 0)
            from backend.services.scalp.scalp_bindings import enable_candidate
            binding = enable_candidate(best["id"])
            report["auto_enabled"] = {
                "candidate_id": best["id"],
                "config": "%s %s %s thr=%s" % (
                    symbol, best["period"], best["factor_set"],
                    best["params"].get("threshold")),
                "binding": binding,
            }
            logger.info("[PairSelector] %s 自动晋级启用 binding=%s",
                        symbol, binding.get("id"))
    except Exception as e:
        logger.warning("[PairSelector] %s 自动晋级失败: %s", symbol, e)
    logger.info("[PairSelector] %s 扫描完成: configs=%d pass=%d",
                symbol, len(report["candidates"]), pass_count)
    return report


def processed_within_hours(symbol: str, hours: int = 24) -> bool:
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with system_identity():
        with SessionLocal() as db:
            r = db.execute(
                text(
                    "SELECT 1 FROM pair_strategy_candidates "
                    "WHERE symbol = :s AND generated_at >= :since LIMIT 1"
                ),
                {"s": symbol.upper(), "since": since},
            ).mappings().first()
    return r is not None


def auto_promote_best(symbol: str) -> Optional[Dict[str, Any]]:
    """对已有 pass 候选做自动晋级（幂等：已有 running 绑定则跳过）。"""
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    min_pass = int(os.getenv("PAIR_AUTO_PROMOTE_MIN_PASS", "2"))
    min_pf = float(os.getenv("PAIR_AUTO_PROMOTE_MIN_PF", "1.2"))
    min_t = float(os.getenv("PAIR_AUTO_PROMOTE_MIN_T", "1.5"))
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT id, period, factor_set, metrics_json FROM pair_strategy_candidates "
                    "WHERE symbol = :s AND gate_verdict = 'pass' ORDER BY id DESC"
                ),
                {"s": symbol.upper()},
            ).mappings().all()
            if len(rows) < min_pass:
                return None
            has_running = db.execute(
                text(
                    "SELECT 1 FROM pair_strategy_bindings "
                    "WHERE symbol = :s AND status = 'running' LIMIT 1"
                ),
                {"s": symbol.upper()},
            ).mappings().first()
            if has_running:
                return {"skipped": "already_running"}
    strong = []
    for r in rows:
        try:
            m = r["metrics_json"]
            if isinstance(m, str):
                m = json.loads(m)
            if (float(m.get("profit_factor") or 0) >= min_pf
                    and float(m.get("t_stat") or 0) >= min_t):
                strong.append((int(r["id"]), float(m.get("profit_factor") or 0)))
        except Exception:
            continue
    if not strong:
        return None
    best_id = max(strong, key=lambda x: x[1])[0]
    from backend.services.scalp.scalp_bindings import enable_candidate
    return enable_candidate(best_id)
