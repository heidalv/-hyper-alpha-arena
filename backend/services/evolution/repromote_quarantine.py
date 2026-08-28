"""隔离因子复评晋升：把仍有表达式、近期仍有正向净 IC 的 QUARANTINE 拉回 PAPER。"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def repromote_quarantine_factors(
    *,
    period: str = "4h",
    limit: int = 40,
    min_net_ic: float | None = None,
) -> Dict[str, Any]:
    """复评隔离因子；达标者回 PAPER（影子交易），不直接 ACTIVE。"""
    from backend.services.evolution.factor_evolution_loop import (
        _forward_returns,
        _kline_to_fields,
        _load_data,
        _log_evolution,
        _min_net_ic_threshold,
        _save_active_factors,
        _trailing_net_ic,
        resolve_evolution_symbols,
    )
    from backend.services.factor_engine.active_set_policy import (
        ActiveSetRole,
        load_factor_active_rows,
    )

    thr = float(min_net_ic) if min_net_ic is not None else float(_min_net_ic_threshold())
    rows = load_factor_active_rows(ActiveSetRole.QUARANTINE, parse_expr=True, limit=limit)
    if not rows:
        return {"ok": True, "scanned": 0, "promoted": [], "skipped": [], "message": "隔离区为空"}

    symbols = resolve_evolution_symbols()
    dfs = _load_data(symbols, period=period)
    if not dfs:
        return {"ok": False, "error": "取数失败，无法复评"}

    promoted: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    # [2026-08-28] 隔离冷却：隔离后 N 天内不复评（与晋升冷却同口径）。
    # 实证：seed_vol10 03:07 被 4h 轮隔离 → 04:15 复评任务拉回(0.0272) →
    # 06:12 被 15m 轮再隔离(-0.0241)——同夜横跳，阈值附近 flapping。
    try:
        _cool_d = float(os.getenv("FACTOR_QUARANTINE_COOLDOWN_DAYS", "7") or 7)
    except (TypeError, ValueError):
        _cool_d = 7.0
    from datetime import datetime as _dt, timezone as _tz
    _now = _dt.now(_tz.utc)

    for f in rows:
        fid = f.get("factor_id")
        _deact = f.get("deactivated_at")
        if _deact is not None and _cool_d > 0:
            try:
                if _deact.tzinfo is None:
                    _deact = _deact.replace(tzinfo=_tz.utc)
                _age_d = (_now - _deact).total_seconds() / 86400.0
            except Exception:
                _age_d = 0.0
            if _age_d < _cool_d:
                skipped.append({
                    "factor_id": fid,
                    "reason": f"隔离冷却中（{_age_d:.1f}天 < {_cool_d:.1f}天）",
                })
                continue
        expr = f.get("expr")
        if not expr or not f.get("expr_ast"):
            skipped.append({"factor_id": fid, "reason": "无表达式"})
            continue
        # [2026-08-27] 与 M2 复评同口径：尾部窗口净 IC（全窗口径把旧 regime
        # 拖尾算进衰减判据，误杀近期有边因子——d6f82d36 全窗 0.0095 vs 尾部 0.138）。
        _tr = _trailing_net_ic(expr, dfs)
        if _tr is None:
            skipped.append({"factor_id": fid, "reason": "求值失败"})
            continue
        ic_mean = float(_tr["ic_mean"])
        t = float(_tr["turnover"])
        net = float(_tr["net_ic"])
        if net < thr:
            skipped.append({
                "factor_id": fid,
                "reason": f"net_ic={net:.4f}<{thr:.4f}",
                "ic": round(ic_mean, 4),
                "net_ic": round(net, 4),
            })
            continue

        row = {
            "factor_id": fid,
            "expr": expr,
            "expr_ast": f.get("expr_ast"),
            "expr_id": f.get("expr_id"),
            "source": f.get("source") or "repromote",
            "state": "PAPER",
            "period": period,
            "icir": float(f.get("icir") or ic_mean),
            "last_net_ic": round(net, 6),
            "turnover": round(t, 6),
        }
        promoted.append(row)
        _log_evolution(
            fid, "repromote",
            source=row["source"],
            state_from="QUARANTINE",
            state_to="PAPER",
            action="repromote_to_paper",
            reason=f"复评 net_ic={net:.4f} ic={ic_mean:.4f} thr={thr:.4f}",
            metrics={"net_ic": net, "ic": ic_mean, "period": period},
        )

    if promoted:
        _save_active_factors(promoted)

    logger.info(
        "[FactorEvo] 隔离复评晋升 scanned=%d promoted=%d skipped=%d thr=%.4f",
        len(rows), len(promoted), len(skipped), thr,
    )
    return {
        "ok": True,
        "period": period,
        "threshold_net_ic": thr,
        "scanned": len(rows),
        "promoted": [{"factor_id": p["factor_id"], "last_net_ic": p["last_net_ic"]} for p in promoted],
        "skipped": skipped[:30],
        "message": f"复评 {len(rows)}，回 PAPER {len(promoted)}，跳过 {len(skipped)}",
    }
