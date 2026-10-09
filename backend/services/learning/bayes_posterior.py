# -*- coding: utf-8 -*-
"""[P4 大轮回 2026-09-27] §11.2 后验 → 行为三通路（贝叶斯后验层）。

设计 §11.2：
  1. 每个 (lane, direction, symbol, regime) 维护 Beta(α,β) 胜率后验与盈亏比后验；
     分层先验（全局 → 车道 → 方向 → 币种），样本 < 30 自动回退到上层先验（防过拟合）；
  2. 后验 → 行为（唯一允许的三条通路）：
     - 选币排序加权（后验期望 × 因子分数）——本模块输出 `posterior_score`；
     - 仓位乘子 m ∈ [0.5, 1.5]（单调、有界；one-price 模式下唯一允许的学习乘子）；
     - 否决（后验期望 < −成本×3 且样本 ≥ 30）。

事实源（P0 已闭环）：paper_positions（已平仓）LEFT JOIN strategy_trades
（decision_context.paper_position_id → regime）：
  net = unrealized_pnl − (partial_fee_paid + coalesce(final_fee_paid,0))
        − (funding_paid − funding_received)
车道：tier mid/short/scalp → intraday；long → trend。
回滚：P4_POSTERIOR_VETO_ENABLED=false / P4_POSTERIOR_MULT_ENABLED=false。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 事实缓存（300s）与最小样本阈值
_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"ts": 0.0, "facts": []}
_CACHE_TTL_S = 300.0
_MIN_N = 30


def _env_bool(key: str, default: bool) -> bool:
    try:
        return (os.getenv(key, "true" if default else "false") or ""
                ).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return default


def lane_of(tier: Optional[str]) -> str:
    t = str(tier or "").strip().lower()
    return "trend" if t in ("long", "trend", "trend_follow", "position") else "intraday"


def _norm_regime(regime: Optional[str]) -> str:
    r = str(regime or "").strip().lower()
    if not r or r in ("?", "none", "null"):
        return "unknown"
    return r


def load_facts(db, since_days: int = 30, force: bool = False) -> List[Dict[str, Any]]:
    """已平仓净额事实（P0 口径；默认 30 天 = 用户作废线后的当前系统行为）。"""
    now = time.time()
    with _cache_lock:
        if not force and now - _cache["ts"] < _CACHE_TTL_S and _cache["facts"]:
            return _cache["facts"]
    from sqlalchemy import text
    rows = db.execute(text("""
        SELECT p.symbol, p.side, COALESCE(p.timeframe_tier,'mid') AS tier,
               COALESCE(st.decision_context->>'regime','unknown') AS regime,
               COALESCE(p.unrealized_pnl,0) AS gross,
               COALESCE(p.partial_fee_paid,0) + COALESCE(p.final_fee_paid,0) AS fees,
               COALESCE(p.funding_paid,0) - COALESCE(p.funding_received,0) AS funding
        FROM paper_positions p
        LEFT JOIN strategy_trades st
          ON st.decision_context->>'paper_position_id' = p.id::text
        WHERE p.status='closed' AND p.closed_at IS NOT NULL
          AND p.closed_at >= now() - make_interval(days => :d)
          AND p.close_price IS NOT NULL AND p.close_price > 0
    """), {"d": int(since_days)}).fetchall()
    facts = []
    for r in rows:
        sym, side, tier, regime, gross, fees, funding = r
        net = float(gross or 0) - float(fees or 0) - float(funding or 0)
        facts.append({
            "symbol": str(sym or "").upper(),
            "direction": "long" if str(side or "").lower().startswith("l") else "short",
            "lane": lane_of(tier),
            "regime": _norm_regime(regime),
            "net": net,
        })
    with _cache_lock:
        _cache["ts"] = now
        _cache["facts"] = facts
    return facts


def _filter(facts: List[Dict[str, Any]], **cond) -> List[Dict[str, Any]]:
    return [f for f in facts if all(f.get(k) == v for k, v in cond.items())]


def _beta_stats(rows: List[Dict[str, Any]], prior: Tuple[float, float]) -> Dict[str, Any]:
    wins = sum(1 for f in rows if f["net"] > 0)
    losses = len(rows) - wins
    a0, b0 = prior
    alpha, beta = a0 + wins, b0 + losses
    n = len(rows)
    return {
        "n": n, "wins": wins, "alpha": round(alpha, 3), "beta": round(beta, 3),
        "mean_wr": round(alpha / (alpha + beta), 4) if (alpha + beta) > 0 else 0.5,
    }


def posterior_for(
    db, *, lane: str, direction: str, symbol: str, regime: str = "unknown",
) -> Dict[str, Any]:
    """分层后验：全局(1,1) → 车道×方向 → 币种 → regime 桶；n<30 回退上层。"""
    facts = load_facts(db)
    all_rows = facts
    lane_rows = _filter(all_rows, lane=lane, direction=direction)
    sym_rows = _filter(lane_rows, symbol=str(symbol).upper())
    reg_rows = _filter(sym_rows, regime=_norm_regime(regime))

    global_stats = _beta_stats(all_rows, (1.0, 1.0))
    lane_stats = _beta_stats(lane_rows, (global_stats["alpha"], global_stats["beta"]))
    sym_stats = _beta_stats(sym_rows, (lane_stats["alpha"], lane_stats["beta"]))
    reg_stats = _beta_stats(reg_rows, (sym_stats["alpha"], sym_stats["beta"]))

    # 层级回退：regime 桶 n<30 → 币种层；币种 n<30 → 车道×方向层
    if reg_stats["n"] >= _MIN_N:
        level, stats, prior_mean = "regime", reg_stats, sym_stats["mean_wr"]
    elif sym_stats["n"] >= _MIN_N:
        level, stats, prior_mean = "symbol", sym_stats, lane_stats["mean_wr"]
    else:
        level, stats, prior_mean = "lane_direction", lane_stats, global_stats["mean_wr"]

    rows_used = {"regime": reg_rows, "symbol": sym_rows, "lane_direction": lane_rows}[level]
    nets = [f["net"] for f in rows_used]
    n_net = len(nets)
    mean_net = sum(nets) / n_net if n_net else 0.0
    var = sum((x - mean_net) ** 2 for x in nets) / n_net if n_net > 1 else 0.0

    return {
        "lane": lane, "direction": direction, "symbol": str(symbol).upper(),
        "regime": _norm_regime(regime), "level": level,
        "n": stats["n"], "mean_wr": stats["mean_wr"],
        "prior_mean_wr": round(prior_mean, 4),
        "alpha": stats["alpha"], "beta": stats["beta"],
        "mean_net": round(mean_net, 4), "n_net": n_net,
        "se_net": round((var / n_net) ** 0.5, 4) if n_net > 1 else None,
    }


def posterior_score(db, *, lane: str, direction: str, symbol: str,
                    regime: str = "unknown") -> float:
    """选币加权输入：后验胜率均值（0~1）。供 AI 候选排序（× 因子分数）。"""
    return float(posterior_for(db, lane=lane, direction=direction,
                               symbol=symbol, regime=regime)["mean_wr"])


def posterior_multiplier(db, *, lane: str, direction: str, symbol: str,
                         regime: str = "unknown") -> float:
    """仓位乘子 m∈[0.5,1.5]：单调有界，EV=0→1.0，EV≤−3×成本→0.5，EV≥1.5×成本→1.5。

    one-price 模式下这是唯一允许的学习乘子（§11.2 三通路之一，禁止再叠其它乘子）。
    """
    p = posterior_for(db, lane=lane, direction=direction,
                      symbol=symbol, regime=regime)
    cost = _avg_cost_usd(db)
    if cost <= 0 or not p["n_net"]:
        return 1.0
    m = 1.0 + p["mean_net"] / (3.0 * cost)
    return round(max(0.5, min(1.5, m)), 3)


def posterior_veto(db, *, lane: str, direction: str, symbol: str,
                   regime: str = "unknown") -> Tuple[bool, str]:
    """否决通路：样本 ≥30 且 后验期望 < −成本×3 → 否决。

    [P6 bug 修复③ 2026-09-28] 只对**具体桶**（symbol/regime 层）否决；
    lane_direction 兜底层代表"整条车道的先验"——按它否决会冻结整个方向的开仓
    （违反 §1.2「不因怕亏而压制开仓」：模拟盘的任务是攒数据）。
    """
    p = posterior_for(db, lane=lane, direction=direction,
                      symbol=symbol, regime=regime)
    if p["level"] not in ("symbol", "regime"):
        return False, f"lane_level_no_veto(level={p['level']}, n={p['n']})"
    if p["n"] < _MIN_N:
        return False, f"sample_insufficient({p['n']})"
    cost = _avg_cost_usd(db)
    if cost <= 0:
        return False, "cost_unavailable"
    if p["mean_net"] < -3.0 * cost:
        return True, (
            f"posterior_veto: {symbol} {lane}/{direction}/{p['regime']} "
            f"n={p['n']} mean_net={p['mean_net']:.2f} < -3×cost({3*cost:.2f})"
        )
    return False, f"posterior_ok(mean_net={p['mean_net']:.2f}, cost={cost:.2f})"


def _avg_cost_usd(db) -> float:
    """近 30 天全体事实的平均单笔成本（费+资金费），下限 0.05 美元。

    用 30 天窗口：历史行 final_fee_paid 为 NULL（09-20 前）会把均值拖成 0，
    导致 -3×成本 阈值失去意义；下限兜底小名义仓的极低成本。
    """
    from sqlalchemy import text
    rows = db.execute(text("""
        SELECT AVG(COALESCE(p.partial_fee_paid,0) + COALESCE(p.final_fee_paid,0)
                   + COALESCE(p.funding_paid,0) - COALESCE(p.funding_received,0))
        FROM paper_positions p
        WHERE p.status='closed' AND p.closed_at >= now() - interval '30 days'
          AND p.close_price IS NOT NULL
    """)).fetchone()
    try:
        v = float(rows[0] or 0)
    except (TypeError, ValueError):
        v = 0.0
    return max(v, 0.05)


def invalidate_cache() -> None:
    with _cache_lock:
        _cache["ts"] = 0.0
        _cache["facts"] = []
