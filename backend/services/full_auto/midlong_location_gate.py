# -*- coding: utf-8 -*-
"""中长线开仓「位置闸」——禁在 24h 区间高位追多 / 低位追空（2026-09-09）。

## 数据依据（本机实测，非拍脑袋）

对 99 笔真实 mid/long 成交（strategy_trades，剔除 e2e 测试产物，2026-07-14 ~ 09-08）
逐笔取入场时刻的 1h K 线做事件研究（`_audit_ml/21_timing.py`）：

- 入场价在**前 24h 高低区间中的分位**与入场后 24h 收益强负相关：

  | 24h 区间分位 | n  | 入场后 24h 均值 | 胜率  |
  |---|---|---|---|
  | 0-20%（贴近低点）  | 7  | **+1.86%** | 0.857 |
  | 20-40%            | 23 | -0.65%     | 0.391 |
  | 40-60%            | 20 | +2.28%     | 0.750 |
  | 60-80%            | 13 | -3.21%     | 0.231 |
  | 80-100%（贴近高点）| 33 | **-3.89%** | **0.152** |

  61% 的开仓落在区间上半部（60% 分位以上），这些仓的 24h 胜率只有 0.15~0.23。

- 入场前 24h 已经下跌 >5% 时（追跌/接刀）：n=18，入场后 24h **-4.41%**，胜率 0.167。
- 入场前 24h 已上涨 0-2%（温和顺势）：n=22，+0.03%，胜率 0.636 —— 反而最好。

结论：**不是「不能顺势」，而是「不能在区间末端追」**。系统在 ranging regime 下
（92/99 笔）持续在 24h 区间上缘做多，本质是买在短期极值、等均值回归。

## 闸的语义

仅对 **mid/long tier 的新开仓** 生效，且仅在 **ranging / unknown regime** 下生效
（trend 态由既有 trend 逻辑与 regime 路由负责，不叠加限制）：

1. `long`：现价在 24h 区间分位 ≥ `MIDLONG_LOCATION_MAX_PCT_LONG`（默认 60）→ 拒开；
2. `short`：现价在 24h 区间分位 ≤ `MIDLONG_LOCATION_MIN_PCT_SHORT`（默认 40）→ 拒开；
3. 追跌保护：入场前 24h 涨跌幅 ≤ `-MIDLONG_LOCATION_MAX_ADVERSE_24H_PCT`（默认 5%）
   且方向为 long → 拒开（接飞刀）；对称地，24h 已涨 ≥ +5% 时 short → 拒开。

数据来源：`market_summary[symbol]` 的 `indicators_1h`（high/low 序列）或
`price_24h_high_pct` / `price_24h_low_pct` / `price_change_24h_pct`。
**取不到数据时 fail-open**（放行），避免闸自身成为停摆源。

回滚：`MIDLONG_LOCATION_GATE_ENABLED=false` 一键关闭。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("MIDLONG_LOCATION_GATE_ENABLED", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _applies_regime(regime: str) -> bool:
    """只对震荡/未知态生效；trend 态不叠加（避免与趋势逻辑打架）。"""
    reg = str(regime or "").strip().lower()
    allow = (os.getenv("MIDLONG_LOCATION_REGIMES", "ranging,unknown") or "ranging,unknown")
    return reg in {x.strip().lower() for x in allow.split(",") if x.strip()}


def _defer_to_long_gate_enabled() -> bool:
    """[2026-09-11 V14/V17 修复] 日线 chop 时位置规则是否让位给 learned 多头闸。"""
    return (os.getenv("MIDLONG_LOCATION_DEFER_TO_LONG_GATE", "true") or "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _long_gate_authoritative(symbol: str, tier: str) -> bool:
    """日线 chop 且该 tier 受 `MIDLONG_LONG_MODE=learned` 多头闸管辖 → 本闸位置规则让位。

    ## 为什么必须让位（V17 合成证明，`_audit_ml/V17_gate_deadlock_proof.py`）

    mid 层 + 日线 chop + RegimeAgent=ranging 时两闸互斥：

    | 场景 | learned 多头闸（chop 档） | 位置闸 | 结果 |
    |---|---|---|---|
    | pos=40%，chg=+3% | 拒（pos<60） | 放行 | 无单可开 |
    | pos=70%，chg=+3% | **放行**（第十八轮标定的最优档） | 拒（pos≥60） | **无单可开** |

    即第十八轮用近 30 天真实 P&L 标定出的 chop 多头最优档（pos≥60 & chg≥2 →
    +0.406%/笔、多头 +1.722%/胜率 0.857）在本闸存在时**永不执行**。
    实证支持让位：V8/V9（179 笔）显示被本闸拒的 ≥60% 档实际 PnL 为正（+27.44），
    而放行的 <60% 档为负（−190.77）；chop 多头无可用位置子过滤。
    让位只跳过**位置**规则，追跌接刀（24h 已跌 ≥5%）仍然拦截，空头规则不变。
    回滚：`MIDLONG_LOCATION_DEFER_TO_LONG_GATE=false`。
    """
    if not _defer_to_long_gate_enabled():
        return False
    try:
        from backend.services.full_auto.midlong_circuit_gate import (
            _daily_regime,
            _long_mode,
            _tier_in_learned,
        )
        if _long_mode() != "learned":
            return False
        if not _tier_in_learned(tier):
            return False
        return _daily_regime(str(symbol or "").upper()) == "chop"
    except Exception as exc:  # noqa: BLE001 — 判定失败保持旧行为
        logger.debug("[location_gate] 让位判定失败(按旧行为执行): %s", exc)
        return False


def _sym_block(market_summary: Optional[dict], symbol: str) -> Dict[str, Any]:
    if not isinstance(market_summary, dict):
        return {"symbol": str(symbol or "").upper()}
    for key in (symbol, symbol.upper(), symbol.lower()):
        blk = market_summary.get(key)
        if isinstance(blk, dict):
            # 把 symbol 附到块里，供 _range_from_klines 兜底使用
            if "symbol" not in blk:
                blk = dict(blk)
                blk["symbol"] = symbol.upper()
            return blk
    return {"symbol": str(symbol or "").upper()}


# 1h K 线兜底缓存（symbol -> (ts, hi, lo, last_close)），60s TTL，避免热路径反复拉 K 线
_RANGE_CACHE: Dict[str, tuple] = {}
_RANGE_TTL_SEC = 60.0


def _range_from_klines(symbol: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """兜底：按需拉最近 24 根 1h K 线算高低沿与最新收盘（60s 缓存）。
    返回 (hi, lo, last_close)；失败返回 (None, None, None)。"""
    sym = str(symbol or "").upper()
    if not sym:
        return None, None, None
    now = time.time()
    row = _RANGE_CACHE.get(sym)
    if row and now - row[0] < _RANGE_TTL_SEC:
        return row[1], row[2], row[3]
    try:
        from backend.services.kline_data_service import kline_service

        raw = kline_service.get_aggregated_klines(sym, "1h", count=30)
        if not raw or len(raw) < 3:
            return None, None, None
        rows = list(raw)[-24:]
        hi = max(float(r["high"]) for r in rows)
        lo = min(float(r["low"]) for r in rows)
        last = float(rows[-1]["close"])
        _RANGE_CACHE[sym] = (now, hi, lo, last)
        return hi, lo, last
    except Exception as exc:
        logger.debug("[location_gate] 1h K 线兜底失败 %s: %s", sym, exc)
        return None, None, None


def _range_position(ms: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """返回 (区间分位%, 24h高, 24h低)；数据不足返回 (None, None, None)。"""
    if not isinstance(ms, dict):
        return None, None, None
    px = None
    for k in ("price", "current_price", "last_price", "mark_price", "close"):
        try:
            v = ms.get(k)
            if v is not None and float(v) > 0:
                px = float(v)
                break
        except (TypeError, ValueError):
            continue

    # 1) 首选：midlong_helpers 注入的 24h 高低沿（inject_midlong_indicators 就地缓存）
    hi = lo = None
    try:
        _h = ms.get("range_24h_high")
        _l = ms.get("range_24h_low")
        if _h is not None and _l is not None and float(_h) > float(_l):
            hi, lo = float(_h), float(_l)
    except (TypeError, ValueError):
        hi = lo = None

    # 2) 次选：indicators_1h 的 highs/lows 序列
    if hi is None or lo is None:
        ind = ms.get("indicators_1h") if isinstance(ms.get("indicators_1h"), dict) else {}
        highs = ind.get("highs") or ind.get("high")
        lows = ind.get("lows") or ind.get("low")
        try:
            if isinstance(highs, (list, tuple)) and isinstance(lows, (list, tuple)) and highs and lows:
                hi = max(float(x) for x in highs[-24:])
                lo = min(float(x) for x in lows[-24:])
        except (TypeError, ValueError):
            hi = lo = None

    # 3) 兜底：按需拉 1h K 线（60s TTL 缓存，只在真正要开仓时触发）
    if hi is None or lo is None or px is None:
        _h2, _l2, _c2 = _range_from_klines(str(ms.get("symbol") or ms.get("_symbol") or ""))
        if _h2 is not None:
            if hi is None or lo is None:
                hi, lo = _h2, _l2
            if px is None and _c2:
                px = _c2

    # 4) 兼容「只给百分比口径」的链路：用现价反推 24h 上下沿
    if (hi is None or lo is None):
        try:
            _hi_pct = ms.get("price_24h_high_pct")
            _lo_pct = ms.get("price_24h_low_pct")
            if px and _hi_pct is not None and _lo_pct is not None:
                hi = px * (1.0 + abs(float(_hi_pct)) / 100.0)
                lo = px * (1.0 - abs(float(_lo_pct)) / 100.0)
        except (TypeError, ValueError):
            hi = lo = None

    if px is None or hi is None or lo is None or hi <= lo:
        return None, hi, lo
    return (px - lo) / (hi - lo) * 100.0, hi, lo


def _change_24h_pct(ms: Dict[str, Any]) -> Optional[float]:
    for k in ("price_change_24h_pct", "change_24h_pct", "pct_change_24h"):
        try:
            v = ms.get(k)
            if v is not None:
                v = float(v)
                # 兼容 0~1 小数口径
                return v * 100.0 if abs(v) <= 1.0 else v
        except (TypeError, ValueError):
            continue
    return None


def _paper_shrink_enabled() -> bool:
    """[M4 2026-09-14] paper 下位置闸从 veto 降级为缩仓放行。"""
    return (os.getenv("MIDLONG_LOCATION_PAPER_SHRINK_ENABLED", "true") or "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _paper_shrink_mult() -> float:
    try:
        v = float(os.getenv("MIDLONG_LOCATION_PAPER_SHRINK_MULT", "0.25") or 0.25)
        return max(0.05, min(1.0, v))
    except (TypeError, ValueError):
        return 0.25


def _paper_shrink_ceiling() -> Optional[float]:
    """[2026-09-16 验收轮6] paper 缩仓天花板：24h 区间分位 ≥ 该值 → 连缩仓都不放行，硬 veto。

    两日亏损审计：ASTER/VIRTUAL 在 83~90% 分位追多开仓，是最大 giveback 来源
    （追顶开的仓论点无法兑现，峰值 +0.2~0.5% 就掉头）。60-70% 档仍缩仓×0.25
    收集样本；≥70% 直接否决（追顶无样本价值）。0 = 关闭（回滚到纯缩仓放行）。
    """
    try:
        v = float(os.getenv("MIDLONG_LOCATION_PAPER_SHRINK_CEILING", "70") or 70)
    except (TypeError, ValueError):
        v = 70.0
    return None if v <= 0 else v


def location_gate_check(
    symbol: str,
    action: str,
    *,
    tier: str = "",
    regime: str = "",
    market_summary: Optional[dict] = None,
    paper_mode: bool = False,
) -> Tuple[bool, str, Dict[str, Any]]:
    """开仓前的「位置」否决检查。返回 (allow, reason, detail)。

    [M4 2026-09-14] paper_mode=true 且命中否决条件时：不 veto，改为缩仓放行
    （detail 携带 paper_shrink_mult，调用方乘到 margin 上）。理由：位置闸的统计
    依据来自旧策略结构的 99 笔样本，paper 的使命是收集**当前策略**的新样本；
    live 口径保持硬 veto 不变。回滚：MIDLONG_LOCATION_PAPER_SHRINK_ENABLED=false。
    """
    if not _enabled():
        return True, "location_gate 未启用", {}
    act = str(action or "").lower()
    if act not in ("buy", "sell"):
        return True, "非开仓动作", {}
    t = str(tier or "").strip().lower()
    tiers = (os.getenv("MIDLONG_LOCATION_TIERS", "mid,long") or "mid,long")
    if t not in {x.strip().lower() for x in tiers.split(",") if x.strip()}:
        return True, f"location_gate: tier={t} 不适用", {}
    if not _applies_regime(regime):
        return True, f"location_gate: regime={regime} 不限制", {}

    sym = str(symbol or "").upper()
    ms = _sym_block(market_summary, sym)
    pos_pct, hi, lo = _range_position(ms)
    chg24 = _change_24h_pct(ms)
    detail: Dict[str, Any] = {
        "symbol": sym, "tier": t, "regime": str(regime or ""),
        "action": act, "range_pos_pct": (round(pos_pct, 2) if pos_pct is not None else None),
        "high_24h": hi, "low_24h": lo, "change_24h_pct": chg24,
    }

    max_long = _f("MIDLONG_LOCATION_MAX_PCT_LONG", 60.0)
    min_short = _f("MIDLONG_LOCATION_MIN_PCT_SHORT", 40.0)
    adverse = _f("MIDLONG_LOCATION_MAX_ADVERSE_24H_PCT", 5.0)
    _paper_shrink = bool(paper_mode) and _paper_shrink_enabled()

    def _veto(reason: str) -> Tuple[bool, str, Dict[str, Any]]:
        if _paper_shrink:
            _mult = _paper_shrink_mult()
            _d2 = dict(detail)
            _d2["paper_shrink_mult"] = _mult
            _d2["paper_shrink_veto_reason"] = reason
            return (
                True,
                f"location_gate: paper 缩仓×{_mult:.2f} 放行（live 仍 veto）: {reason}",
                _d2,
            )
        return False, reason, detail

    # [2026-09-11 V14/V17] 日线 chop + mid 学习门 ⇒ 位置规则让位（否则两闸互斥、
    # mid 层在震荡日无单可开；详见 _long_gate_authoritative docstring）。
    _defer_pos = bool(act == "buy" and _long_gate_authoritative(sym, t))
    if _defer_pos:
        detail["defer_to_long_gate"] = True

    # 1) 区间位置
    if pos_pct is not None and not _defer_pos:
        if act == "buy" and pos_pct >= max_long:
            _v = (
                f"location_gate_veto: 24h区间分位{pos_pct:.0f}%≥{max_long:.0f}% 高位追多"
                f"（实测该带 24h 胜率 0.15-0.23）"
            )
            _ceil = _paper_shrink_ceiling()
            if _paper_shrink and _ceil is not None and pos_pct >= _ceil:
                _d3 = dict(detail)
                _d3["paper_shrink_ceiling"] = _ceil
                return False, (
                    f"location_gate_veto: 24h区间分位{pos_pct:.0f}%≥追高天花板{_ceil:.0f}% 硬否决"
                    f"（paper 不追顶；<{_ceil:.0f}% 仍缩仓放行收集样本）"
                ), _d3
            return _veto(_v)
        if act == "sell" and pos_pct <= min_short:
            return _veto(
                f"location_gate_veto: 24h区间分位{pos_pct:.0f}%≤{min_short:.0f}% 低位追空"
                f"（实测该带 24h 胜率 0.15-0.23）"
            )

    # 2) 追跌 / 追涨
    if chg24 is not None:
        if act == "buy" and chg24 <= -abs(adverse):
            return _veto(
                f"location_gate_veto: 24h已跌{chg24:.1f}% 逆势接刀做多"
                f"（实测该带 24h 均值 -4.41%/胜率 0.167）"
            )
        if act == "sell" and chg24 >= abs(adverse):
            return _veto(
                f"location_gate_veto: 24h已涨{chg24:.1f}% 逆势追空做空"
            )

    if pos_pct is None and chg24 is None:
        return True, "location_gate: 无区间/涨跌数据（fail-open）", detail
    if _defer_pos:
        return True, "location_gate: 日线 chop 让位给 learned 多头闸（位置规则跳过）", detail
    return True, "location_gate: 位置合规", detail
