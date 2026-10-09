# -*- coding: utf-8 -*-
"""[P1 大轮回 2026-09-27] 一次定价（§7.2）—— 取代六层缩仓乘子链。

设计 §7.1 实测：位置闸×0.25 × regime探针×0.25 × swing共识×0.50 × V5×0.25 × MTF×0.60
× brain 上界 0.30 = **0.0014**——每一层都以为自己在"轻微打折"，乘起来是 0.14%。
本模块按 §7.2 提供单一权威定价：

    risk_usd = equity × risk_pct(lane)              # 日内 0.6% / 趋势 1.0%
    stop_pct = |sl_price - price| / price           # 由市场决定，不固定
    notional = risk_usd / stop_pct
    notional = min(notional, equity × max_weight)   # 单币上限
    margin   = notional / leverage                  # 杠杆不动（用户口径）

**否决 or 定价**：各层闸门要么否决（allowed=False + reason），要么不再改动大小。
缩仓乘子链在 `MIDLONG_ONE_PRICE_MODE=true` 时只作诊断（不消费）；false = 回滚到旧链。

纯函数 + 环境参数透传，便于单测与回滚审计。
"""
from __future__ import annotations

import os
from typing import Optional

# ── 车道 → 风险预算/单币权重上限（设计 §13.2 建议值）──
LANE_RISK_PCT = {"intraday": 0.006, "trend": 0.010}      # 0.6% / 1.0%
LANE_MAX_WEIGHT = {"intraday": 0.15, "trend": 0.10}       # 单币名义/权益上限
DEFAULT_LANE = "intraday"


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def one_price_enabled() -> bool:
    """一次定价总开关（部署 .env=true；false 回滚到旧乘子链）。"""
    return (os.getenv("MIDLONG_ONE_PRICE_MODE", "false") or "false").strip().lower() in (
        "1", "true", "yes", "on")


def lane_of(tier: Optional[str]) -> str:
    """timeframe_tier → 车道（intraday/trend），用于选风险预算。"""
    t = str(tier or "").strip().lower()
    if t in ("long", "trend", "trend_follow"):
        return "trend"
    return "intraday"


def risk_pct(lane: str) -> float:
    return _env_f(f"P1_RISK_PCT_{lane.upper()}", LANE_RISK_PCT.get(lane, LANE_RISK_PCT[DEFAULT_LANE]))


def max_weight(lane: str) -> float:
    return _env_f(f"P1_MAX_WEIGHT_{lane.upper()}", LANE_MAX_WEIGHT.get(lane, LANE_MAX_WEIGHT[DEFAULT_LANE]))


def price_once(
    *,
    equity: float,
    tier: Optional[str],
    price: float,
    stop_loss: Optional[float],
    leverage: float = 1.0,
    notional_floor_usd: float = 0.0,
    posterior_mult: float = 1.0,
) -> dict:
    """一次定价：返回 {risk_usd, stop_pct, notional, margin, capped, reason}。

    - equity ≤ 0 或 price ≤ 0 → notional=0 + reason（调用方拒绝，不猜）；
    - stop_loss 缺失/非法 → stop_pct=0 → 用 notional_floor_usd 兜底（若>0），否则 0；
    - 名义先按 风险预算/止损距离，再压单币权重上限（capped 标记）；
    - posterior_mult（[P4 §11.2] 学习后验乘子 m∈[0.5,1.5]）作用在风险预算上——
      one-price 模式下**唯一**允许的学习乘子（禁止再叠其它乘子）。
    """
    lane = lane_of(tier)
    r_pct = risk_pct(lane)
    w_max = max_weight(lane)
    out = {"lane": lane, "risk_pct": r_pct, "max_weight": w_max,
           "risk_usd": 0.0, "stop_pct": 0.0, "notional": 0.0, "margin": 0.0,
           "capped": False, "reason": ""}
    if equity <= 0:
        out["reason"] = "equity<=0"
        return out
    if price <= 0:
        out["reason"] = "price<=0"
        return out
    try:
        m = max(0.5, min(1.5, float(posterior_mult or 1.0)))
    except (TypeError, ValueError):
        m = 1.0
    risk_usd = equity * r_pct * m
    stop_pct = 0.0
    try:
        sl = float(stop_loss or 0)
        if sl > 0:
            stop_pct = abs(sl - price) / price
    except (TypeError, ValueError):
        stop_pct = 0.0
    out["risk_usd"] = round(risk_usd, 2)
    out["stop_pct"] = round(stop_pct, 6)
    if stop_pct > 0:
        notional = risk_usd / stop_pct
    elif notional_floor_usd > 0:
        notional = notional_floor_usd
        out["reason"] = "stop_pct=0→floor"
    else:
        out["reason"] = "stop_pct=0"
        return out
    cap = equity * w_max
    out["capped"] = notional > cap
    if out["capped"]:
        notional = cap
        out["reason"] = f"capped_by_weight({w_max:.0%})"
    lev = max(0.1, float(leverage or 1.0))
    out["notional"] = round(notional, 2)
    out["margin"] = round(notional / lev, 2)
    return out
