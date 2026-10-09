# -*- coding: utf-8 -*-
"""[工作流②-1] 目标波动率**影子版** + 杠杆×止损风险预算检查（2026-10-04）。

## 为什么（实测证据）
1. `VOL_TARGET_ANNUAL` **零消费者**（仅出现在 `analysis/context_pack.py:34` 的环境变量白名单里）；
2. `services/trend_core.py:103-104` 虽有 `TREND_WEIGHTING=vol_target` / `TREND_VOL_TARGET=0.35`，
   但实测 **400 条已平仓**：`Spearman(σ̂, 名义敞口) = +0.421`（目标波动率应为**负**相关），
   名义敞口中位数恒定 $51、保证金 $5 ⇒ **目标波动率没有驱动仓位**；
3. 对照样例：高波 OPENAI（σ̂=2988%）名义 $41 vs 低波 ASTER（σ̂=4%）名义 $42 —— 波动差 700 倍、仓位相同；
4. **意外风险**：实测持仓 `leverage=10`，叠加现有 `sl=8%` ⇒ **单笔止损 = 80% 保证金**。

## 本模块的定位
**影子**：只计算、只入账，**绝不改变下单**。两件事：
  · `vol_target_scale()`：稳健 σ̂（4h 收益、极值截断、数据不足返回 None）→ `clip(target/σ̂, lo, hi)`
  · `risk_budget_check()`：`sl_pct × leverage` 占保证金比例，超过预算即标记（默认**只标记不拦**）

## 回滚开关（全部默认"维持现状"）
| 开关 | 默认 | 作用 |
|---|---|---|
| `VOLTARGET_SHADOW` | `false` | 打开后按决策逐条计算并记录 |
| `VOLTARGET_ENABLED` | `false` | **保留给未来真正生效用**；本模块不读取它来下单 |
| `VOLTARGET_TARGET_ANNUAL` | `0.35` | 年化目标波动（与 `TREND_VOL_TARGET` 同义） |
| `VOLTARGET_SIGMA_FLOOR/CAP` | `0.05 / 2.0` | σ̂ 截断（防小市值币日线噪声，实测出现过 2988%） |
| `LEV_SL_RISK_BUDGET` | `0.5` | `sl×lev` 超过该比例即标记 `over_budget` |
"""
from __future__ import annotations

import logging
import math
import os
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

BARS_PER_YEAR_4H = 365 * 6  # 4h 约 6 根/天
MIN_BARS = 30


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def realized_vol(symbol: str, asof=None, *, period: str = "4h", bars: int = 90) -> Optional[float]:
    """稳健年化已实现波动：4h 对数收益的样本标准差 × √(每年根数)，并截断到 [floor, cap]。

    返回 None 表示数据不足（调用方必须回退，**不许猜**）。
    """
    try:
        from sqlalchemy import create_engine, text

        url = os.getenv("MARKET_DB_URL",
                        "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
        eng = create_engine(url)
        ts = asof or datetime.now(timezone.utc)
        with eng.connect() as c:
            rows = c.execute(
                text(
                    """
                    SELECT close_price FROM crypto_klines
                    WHERE symbol = :s AND period = :p AND timestamp <= extract(epoch from :t)
                    ORDER BY timestamp DESC LIMIT :n
                    """
                ),
                {"s": str(symbol).upper(), "p": period, "t": ts, "n": int(bars)},
            ).fetchall()
        closes = [float(r[0]) for r in rows if r[0] is not None][::-1]
        if len(closes) < MIN_BARS:
            return None
        rets = [math.log(closes[i + 1] / closes[i]) for i in range(len(closes) - 1) if closes[i] > 0]
        if len(rets) < MIN_BARS - 1:
            return None
        per_year = BARS_PER_YEAR_4H if period == "4h" else (365 if period == "1d" else 365 * 24)
        sigma = statistics.stdev(rets) * math.sqrt(per_year)
        floor = _env_f("VOLTARGET_SIGMA_FLOOR", 0.05)
        cap = _env_f("VOLTARGET_SIGMA_CAP", 2.0)
        return max(floor, min(cap, sigma))
    except Exception as exc:  # 数据层问题 ⇒ None（回退），不猜
        logger.debug("[VolTargetShadow] realized_vol 失败 %s: %s", symbol, exc)
        return None


def vol_target_scale(sigma: Optional[float], *, target: Optional[float] = None,
                     lo: float = 0.3, hi: float = 1.0) -> Optional[float]:
    """`clip(target/σ̂, lo, hi)`；σ̂ 缺失返回 None（调用方回退）。"""
    if not sigma or sigma <= 0:
        return None
    tgt = float(target if target is not None else _env_f("VOLTARGET_TARGET_ANNUAL", 0.35))
    return max(lo, min(hi, tgt / float(sigma)))


def risk_budget_check(*, sl_pct: float, leverage: float,
                      budget: Optional[float] = None) -> Dict[str, Any]:
    """杠杆×止损的保证金占用检查（影子：只标记，不拦单）。"""
    b = float(budget if budget is not None else _env_f("LEV_SL_RISK_BUDGET", 0.5))
    used = abs(float(sl_pct or 0.0)) * abs(float(leverage or 0.0))
    return {
        "margin_at_risk": round(used, 4),
        "budget": b,
        "over_budget": used > b,
        "severity": "high" if used > 1.0 else ("warn" if used > b else "ok"),
    }


def shadow_eval(*, symbol: str, asof=None, sl_pct: float = 0.0, leverage: float = 0.0,
                actual_notional: Optional[float] = None,
                equity: Optional[float] = None, record: bool = True) -> Dict[str, Any]:
    """对一条决策/持仓做影子评估（不改变任何下单行为）。"""
    if str(os.getenv("VOLTARGET_SHADOW", "false")).strip().lower() not in ("1", "true", "yes", "on"):
        return {"enabled": False, "reason": "VOLTARGET_SHADOW=false"}
    sigma = realized_vol(symbol, asof)
    scale = vol_target_scale(sigma)
    theo_notional = None
    if scale is not None and equity:
        theo_notional = float(equity) * scale * abs(float(leverage or 1.0))
    rb = risk_budget_check(sl_pct=sl_pct, leverage=leverage)
    out = {
        "enabled": True,
        "symbol": str(symbol).upper(),
        "sigma_annual": round(sigma, 4) if sigma else None,
        "vol_target_scale": round(scale, 4) if scale is not None else None,
        "theoretical_notional": round(theo_notional, 2) if theo_notional else None,
        "actual_notional": round(actual_notional, 2) if actual_notional else None,
        **rb,
        "note": "shadow only —— 不参与下单",
    }
    if record:
        try:
            from backend.services.learning_core import orchestrator
            from backend.services.learning_core.envelope import EvolutionEnvelope

            env = EvolutionEnvelope.root(
                stage="observe", source="vol_target_shadow",
                symbol=out["symbol"],
                payload={k: v for k, v in out.items() if k != "symbol"},
                metrics={"sigma_annual": sigma or 0.0, "scale": scale or 0.0},
                status="pending",
            )
            orchestrator.emit(env)
            out["lineage_id"] = env.lineage_id
        except Exception as exc:  # fail-open
            logger.debug("[VolTargetShadow] 入账跳过: %s", exc)
    return out
