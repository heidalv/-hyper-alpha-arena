# -*- coding: utf-8 -*-
"""尾部 funding 收割（v3 方向 5，p3-promotion）。

规则（方案）：|年化 funding| 极端高（默认 > 50%）连续 ≥3 个结算窗口 → 候选；
费率回落到温和区（默认 < 15% 年化）→ 平仓信号。

本阶段默认 **shadow**：只写 signal_ledger / 落盘，不下单。
`FUNDING_TAIL_LIVE=true` 且过研究桶阶梯 + 非 freeze 时才意图小资金（仍受
live_trading_enabled 硬关）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "cashflow" / "funding_tail"
STRATEGY_ID = "funding_tail_harvest"


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def enabled() -> bool:
    return _env_true("FUNDING_TAIL_ENABLED", True)


def live_requested() -> bool:
    return _env_true("FUNDING_TAIL_LIVE", False)


def _apr_from_8h_rate(rate: float) -> float:
    """8h 费率 → 简单年化（365*3）。"""
    return float(rate) * 3.0 * 365.0


def scan_tail_candidates(*, lookback_settlements: int = 6) -> Dict[str, Any]:
    """从 perp_funding 找极端费率币。无多场所数据 → ok=False 如实说明。"""
    if not enabled():
        return {"ok": False, "skipped": True, "reason": "FUNDING_TAIL_ENABLED=false"}

    enter_apr = _env_float("FUNDING_TAIL_ENTER_APR", 0.50)
    exit_apr = _env_float("FUNDING_TAIL_EXIT_APR", 0.15)
    min_streak = int(_env_float("FUNDING_TAIL_MIN_STREAK", 3))

    out: Dict[str, Any] = {
        "ok": True, "ts_ms": int(time.time() * 1000),
        "enter_apr": enter_apr, "exit_apr": exit_apr, "min_streak": min_streak,
        "candidates": [], "exits": [], "notes": [],
    }
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            # 近 N 个结算窗口按 symbol 聚合（表结构：symbol, funding_rate, ts/funding_time）
            rows = db.execute(text(
                "SELECT symbol, exchange, funding_rate, "
                "COALESCE(EXTRACT(EPOCH FROM funding_time), EXTRACT(EPOCH FROM ts), 0) * 1000 AS ts_ms "
                "FROM perp_funding "
                "WHERE COALESCE(EXTRACT(EPOCH FROM funding_time), EXTRACT(EPOCH FROM ts), 0) * 1000 "
                "  >= :lo "
                "ORDER BY symbol, ts_ms DESC LIMIT 20000"
            ), {"lo": int(time.time() * 1000) - lookback_settlements * 8 * 3600 * 1000}).mappings().all()
        finally:
            db.close()
    except Exception as exc:
        out["ok"] = False
        out["reason"] = f"perp_funding 读取失败: {exc}"
        _persist(out)
        return out

    by_sym: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        sym = str(r.get("symbol") or "")
        if not sym:
            continue
        by_sym.setdefault(sym, []).append(dict(r))

    for sym, hist in by_sym.items():
        hist = sorted(hist, key=lambda x: float(x.get("ts_ms") or 0), reverse=True)
        rates = []
        for h in hist[:lookback_settlements]:
            try:
                rates.append(float(h.get("funding_rate")))
            except (TypeError, ValueError):
                continue
        if len(rates) < min_streak:
            continue
        aprs = [_apr_from_8h_rate(abs(r)) for r in rates]
        streak = 0
        for a in aprs:
            if a >= enter_apr:
                streak += 1
            else:
                break
        latest_signed = rates[0]
        latest_apr = _apr_from_8h_rate(abs(latest_signed))
        # 空费率正 → 多头付费 → 收割做空；负费率 → 做多
        side = "short" if latest_signed > 0 else "long"
        if streak >= min_streak:
            out["candidates"].append({
                "symbol": sym, "side": side, "streak": streak,
                "latest_rate": latest_signed, "latest_apr": round(latest_apr, 4),
                "exchange": hist[0].get("exchange"),
            })
        elif latest_apr < exit_apr and any(_apr_from_8h_rate(abs(r)) >= enter_apr for r in rates[1:min_streak + 1]):
            out["exits"].append({
                "symbol": sym, "latest_apr": round(latest_apr, 4),
                "note": "费率回落，平仓信号",
            })

    out["n_candidates"] = len(out["candidates"])
    out["n_exits"] = len(out["exits"])
    _persist(out)
    _maybe_shadow_signals(out["candidates"])
    return out


def _maybe_shadow_signals(candidates: List[Dict[str, Any]]) -> None:
    if not candidates:
        return
    try:
        from backend.services.analysis import ledgers
        for c in candidates[:20]:
            side = str(c.get("side") or "short")
            direction = -1 if side == "short" else 1
            ledgers.record_signal(
                source=STRATEGY_ID,
                symbol=str(c["symbol"]),
                direction=direction,
                strength=min(1.0, float(c.get("latest_apr") or 0) / 2.0),
                horizon_ms=24 * 3600 * 1000,
                payload={"apr": c.get("latest_apr"), "streak": c.get("streak"),
                         "kind": "funding_tail", "side": side},
            )
    except Exception as exc:
        logger.debug("[funding_tail] shadow signal 跳过: %s", exc)


def run_harvest(*, execute: bool = False) -> Dict[str, Any]:
    scan = scan_tail_candidates()
    scan["execute"] = bool(execute) and live_requested()
    if not scan.get("execute"):
        scan["mode"] = "shadow"
        scan["notes"] = list(scan.get("notes") or []) + ["默认影子：FUNDING_TAIL_LIVE=false 或不 execute"]
        return scan

    # Live 意图：研究桶名义 + freeze 检查；真下单留接线完备后
    try:
        from backend.services.allocation.capital_allocator import research_notional_for, promotion_frozen
        freeze = promotion_frozen()
        if freeze.get("frozen"):
            scan["ok"] = False
            scan["reason"] = "promotion_freeze"
            scan["mode"] = "blocked"
            return scan
        notion = research_notional_for(STRATEGY_ID)
        scan["research_notional"] = notion
        if notion.get("notional_usd", 0) <= 0:
            scan["mode"] = "blocked"
            scan["reason"] = "研究桶名义为 0（仍在 shadow 或未 promote）"
            return scan
    except Exception as exc:
        scan["notes"] = list(scan.get("notes") or []) + [f"allocator: {exc}"]

    scan["mode"] = "live_intent"
    scan["notes"] = list(scan.get("notes") or []) + [
        "p3 只产出候选与名义；真下单需 OMS 腿 + live_trading_enabled（当前硬关）"
    ]
    return scan


def _persist(payload: Dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("[funding_tail] 落盘失败: %s", exc)


def latest() -> Optional[Dict[str, Any]]:
    p = DATA_DIR / "latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def scheduled_funding_tail() -> Dict[str, Any]:
    logger.info("[funding_tail] scan tick")
    return run_harvest(execute=False)
