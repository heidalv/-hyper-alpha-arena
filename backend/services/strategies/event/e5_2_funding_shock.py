# -*- coding: utf-8 -*-
"""E5-2 资金费突变 → 拥挤反转（v3 方向 4，p2-event-strategies）。

方案原文：`|Δfunding| > 3σ 且 OI 同向增 → 拥挤反转（4h 级）`。

规则（全部因果，只用事件时刻之前的数据）：
  1. 取 `perp_funding` 的**结算费率**（binance 每 8h 一条），按交易所归一到 8h 口径；
  2. Δ = rate8h[t] − rate8h[t−1]；用**滚动窗**（默认前 60 条 ≈ 20 天）的均值/标准差算 z，
     窗口不足 `min_history` 条 → 该币跳过（不造数）；
  3. |z| ≥ 3 触发。方向 = **反转**：funding 暴涨（多头拥挤付费）→ 做空；暴跌（空头拥挤）→ 做多；
  4. OI 同向增确认（`require_oi=true` 时）：事件前后 `position_structure` 的 OI 变化 ≥ `oi_min_pct`。
     [2026-09-04] `position_structure` 与 funding 重叠仅 ~26 币，约 39% 的 z 命中死在「无 OI」。
     改为：**有 OI 且未达阈值 → 仍丢弃**（真的未确认）；**取不到 OI → 降置信入账**
     （`payload.oi_confirmed=false`），不再整条扔掉。影子期否则 30 天只剩 3 条，永远攒不够 KPI。
  5. horizon 4h（方案「4h 级」）。

强度：|z| 映射到 0–10；置信度：|z| 与 OI 确认幅度共同决定，封顶 0.8。

回测口径：`backtest()` 用 `require_oi=false` 可拿满 365 天 funding 历史做敏感性分析，
`shadow()` 恒用 `.env` 里的 `E5_FUNDING_REQUIRE_OI`（默认 true，与方案一致）。
"""
from __future__ import annotations

import logging
import math
import statistics
from typing import Any, Dict, List, Optional

from backend.services.strategies.event.base import (
    EventShadowStrategy,
    EventSignal,
    base_symbol,
    env_float,
    env_int,
    env_true,
    register_strategy,
)

logger = logging.getLogger(__name__)

STRATEGY_ID = "e5_2_funding_shock"


def _market_db():
    from backend.database.connection import MarketSessionLocal

    return MarketSessionLocal()


def load_funding_series(exchange: str, since_ms: int, until_ms: int, *,
                        settled_only: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    """{base_symbol: [{ts_ms, rate8h}, ...]}（按时间升序）。

    settled_only：binance 的 `perp_funding` 同时存 5min premiumIndex 快照与 8h 结算费率，
    结算费率的时间戳落在 8h 边界上（00/08/16 UTC）；只留边界样本才是"每 8h 一条"的真实序列。
    """
    from sqlalchemy import text

    try:
        from backend.services.events.funding_universe import rate_8h
    except Exception:
        def rate_8h(ex: str, rate: float) -> float:  # type: ignore
            return float(rate)

    out: Dict[str, List[Dict[str, Any]]] = {}
    db = _market_db()
    try:
        rows = db.execute(
            text(
                "SELECT symbol, timestamp, funding_rate FROM perp_funding "
                "WHERE exchange = :ex AND timestamp >= :lo AND timestamp <= :hi AND funding_rate IS NOT NULL "
                "ORDER BY symbol, timestamp"
            ),
            {"ex": exchange, "lo": int(since_ms), "hi": int(until_ms)},
        ).mappings().all()
    except Exception as exc:
        logger.warning("[%s] perp_funding 读取失败: %s", STRATEGY_ID, exc)
        return out
    finally:
        db.close()

    eight_h = 8 * 3600 * 1000
    for r in rows:
        ts = int(r["timestamp"])
        if settled_only and ts % eight_h > 60000:
            continue     # 非 8h 边界 → 是 premiumIndex 快照，不是结算费率
        base = base_symbol(r["symbol"])
        if not base:
            continue
        try:
            r8 = float(rate_8h(exchange, float(r["funding_rate"])))
        except Exception:
            continue
        out.setdefault(base, []).append({"ts_ms": ts, "rate8h": r8})
    return out


def load_oi_series(since_ms: int, until_ms: int) -> Dict[str, List[Dict[str, Any]]]:
    """{base_symbol: [{ts_ms, oi_usd}, ...]}（升序）。position_structure 仅覆盖部分币种。"""
    from sqlalchemy import text

    out: Dict[str, List[Dict[str, Any]]] = {}
    db = _market_db()
    try:
        rows = db.execute(
            text(
                "SELECT symbol, ts_ms, open_interest_value FROM position_structure "
                "WHERE ts_ms >= :lo AND ts_ms <= :hi AND open_interest_value IS NOT NULL ORDER BY symbol, ts_ms"
            ),
            {"lo": int(since_ms), "hi": int(until_ms)},
        ).mappings().all()
    except Exception as exc:
        logger.warning("[%s] position_structure 读取失败: %s", STRATEGY_ID, exc)
        return out
    finally:
        db.close()
    for r in rows:
        out.setdefault(base_symbol(r["symbol"]), []).append(
            {"ts_ms": int(r["ts_ms"]), "oi_usd": float(r["open_interest_value"])}
        )
    return out


def _oi_change_pct(series: List[Dict[str, Any]], ts_ms: int, window_ms: int) -> Optional[float]:
    """事件时刻 OI 相对 window_ms 之前的变化率（%）。两端任一取不到 → None。"""
    if not series:
        return None
    before = [x for x in series if x["ts_ms"] <= ts_ms - window_ms]
    at = [x for x in series if x["ts_ms"] <= ts_ms]
    if not before or not at:
        return None
    p0 = before[-1]["oi_usd"]
    p1 = at[-1]["oi_usd"]
    if p0 <= 0:
        return None
    return (p1 / p0 - 1.0) * 100.0


class FundingShockStrategy(EventShadowStrategy):
    strategy_id = STRATEGY_ID
    description = "资金费 Δ 超 3σ 且 OI 同向增 → 拥挤反转（4h）"
    default_horizon_h = 4.0

    def __init__(self, *, require_oi: Optional[bool] = None):
        self.z_threshold = env_float("E5_FUNDING_Z", 3.0)
        self.min_history = env_int("E5_FUNDING_MIN_HISTORY", 60)     # 滚动窗最少样本（≈20 天）
        self.roll_window = env_int("E5_FUNDING_ROLL_WINDOW", 60)
        self.require_oi = env_true("E5_FUNDING_REQUIRE_OI", True) if require_oi is None else bool(require_oi)
        self.oi_min_pct = env_float("E5_FUNDING_OI_MIN_PCT", 3.0)
        self.oi_window_h = env_float("E5_FUNDING_OI_WINDOW_H", 8.0)
        self.exchange = "binance"
        self.horizon_h = env_float("E5_FUNDING_HORIZON_H", 4.0)
        self.min_abs_rate = env_float("E5_FUNDING_MIN_ABS_8H", 0.0002)  # 绝对费率下限，滤掉 0 附近的噪声跳变

    def detect(self, *, since_ms: int, until_ms: int, limit: int = 5000,
               notes: Optional[List[str]] = None) -> List[EventSignal]:
        notes = notes if notes is not None else []
        # 滚动窗需要事件前的历史 → 向前多取 min_history × 8h
        warmup_ms = (self.min_history + 2) * 8 * 3600 * 1000
        series = load_funding_series(self.exchange, since_ms - warmup_ms, until_ms)
        if not series:
            notes.append("perp_funding 无结算费率样本")
            return []

        oi_series: Dict[str, List[Dict[str, Any]]] = {}
        if self.require_oi:
            oi_series = load_oi_series(since_ms - int(self.oi_window_h * 3600 * 1000) * 2, until_ms)
            if not oi_series:
                notes.append("position_structure 无样本：所有命中将按无 OI 覆盖降置信入账")

        out: List[EventSignal] = []
        skipped_short = skipped_oi_low = soft_no_oi = 0
        for base, seq in series.items():
            if len(seq) < self.min_history + 2:
                skipped_short += 1
                continue
            for i in range(1, len(seq)):
                ts = seq[i]["ts_ms"]
                if ts < since_ms or ts > until_ms:
                    continue
                lo = max(1, i - self.roll_window)
                hist = [seq[j]["rate8h"] - seq[j - 1]["rate8h"] for j in range(lo, i)]
                if len(hist) < self.min_history:
                    continue
                delta = seq[i]["rate8h"] - seq[i - 1]["rate8h"]
                mu = statistics.fmean(hist)
                sd = statistics.pstdev(hist)
                if sd <= 1e-12:
                    continue
                z = (delta - mu) / sd
                if abs(z) < self.z_threshold:
                    continue
                if abs(seq[i]["rate8h"]) < self.min_abs_rate:
                    continue

                oi_chg = None
                oi_confirmed = True
                if self.require_oi:
                    oi_chg = _oi_change_pct(oi_series.get(base) or [], ts, int(self.oi_window_h * 3600 * 1000))
                    if oi_chg is None:
                        # 无 OI 覆盖：降置信入账，不整条扔掉（否则主流币大量漏检）
                        oi_confirmed = False
                        soft_no_oi += 1
                    elif oi_chg < self.oi_min_pct:
                        skipped_oi_low += 1
                        continue

                # 拥挤反转：费率暴涨（多头付费）→ 做空；暴跌（空头付费）→ 做多
                direction = -1 if z > 0 else 1
                strength = min(10.0, abs(z))
                conf = min(0.8, 0.45 + 0.05 * (abs(z) - self.z_threshold) + (0.05 if oi_confirmed and oi_chg else 0.0))
                if not oi_confirmed:
                    conf = min(conf, 0.55)  # 无 OI 确认的信号上限压低，KPI 可单独切片
                out.append(EventSignal(
                    ts_ms=ts,
                    symbol=base,
                    direction=direction,
                    horizon_h=self.horizon_h,
                    strength=round(strength, 2),
                    confidence=round(conf, 3),
                    reason=f"Δfunding z={z:.2f}（阈值 {self.z_threshold}）"
                           + (f"，OI {self.oi_window_h:.0f}h 变化 {oi_chg:.1f}%" if oi_chg is not None
                              else "，无 OI 覆盖（降置信）"),
                    payload={
                        "strategy": self.strategy_id, "z": round(z, 3),
                        "delta_8h": round(delta, 8), "rate_8h": round(seq[i]["rate8h"], 8),
                        "prev_rate_8h": round(seq[i - 1]["rate8h"], 8),
                        "oi_change_pct": (round(oi_chg, 2) if oi_chg is not None else None),
                        "oi_confirmed": oi_confirmed,
                        "require_oi": self.require_oi, "exchange": self.exchange,
                    },
                ))
                if len(out) >= limit:
                    notes.append(f"命中数达上限 {limit}，已截断")
                    break
            if len(out) >= limit:
                break

        if skipped_short:
            notes.append(f"{skipped_short} 个币历史不足 {self.min_history} 条被跳过")
        if skipped_oi_low:
            notes.append(f"{skipped_oi_low} 次 z 命中因 OI 增幅 < {self.oi_min_pct}% 被丢弃")
        if soft_no_oi:
            notes.append(f"{soft_no_oi} 次 z 命中无 OI 覆盖，已降置信入账")
        out.sort(key=lambda s: s.ts_ms)
        return out


def build() -> FundingShockStrategy:
    return FundingShockStrategy()


register_strategy(STRATEGY_ID, build)
