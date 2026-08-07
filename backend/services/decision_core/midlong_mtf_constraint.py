"""MidLongMtfConstraint — 中长线入场多周期一致性约束（S1-3，泛化自 scalp_mtf_constraint）。

背景
====
独立中长线循环（`_maintain_mlto_theses_for_session` → `_try_execute_independent_agent_open`）
里，SwingAgent(mid)/TrendAgent(long) 各自只看自己那层的信号就开单，**没有"更高周期
必须不强烈反向"的硬约束**——中线可以在日线明确下跌时逆势做多，长线可以在自己的日线
锚定被 4h 深度背离时硬扛。这是中长线胜率低、回撤大的结构性来源之一。

本模块复用 OrchBG 已缓存进 `market_data["orchestrator"]` 的多周期偏向
（long_bias=日线、mid_bias=4h、short_bias=15m）做**无额外网络/重算**的一致性判定：

- **mid(swing)**：锚定周期=日线(long_bias)。日线强反向→否决；日线弱反向或 4h 反向→缩仓。
- **long(trend)**：锚定周期=日线(long_bias)本身。日线强反向→否决（与自身论点矛盾）；
  4h(mid) 强反向→缩仓（深度回调风险）。
- 冲突≥2 → 否决。

产出 `(veto, size_multiplier, conflicts, reason)`，由调用方强制执行。
flag 门控 `MIDLONG_MTF_ENFORCE_ENABLED`，默认开启，可秒回滚。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MidLongMtfResult:
    veto: bool = False
    size_multiplier: float = 1.0
    conflicts: List[str] = field(default_factory=list)
    reason: str = ""


# 轻量运行统计（供健康视图看否决率），进程内累计。
_MTF_STATS: Dict[str, Dict[str, int]] = {}


def _bump_stats(tier: str, veto: bool, downsized: bool) -> None:
    st = _MTF_STATS.setdefault(tier, {"total": 0, "veto": 0, "downsize": 0})
    st["total"] += 1
    if veto:
        st["veto"] += 1
    elif downsized:
        st["downsize"] += 1


def get_mtf_stats() -> Dict[str, Any]:
    """按 tier 返回 MTF 约束的否决/缩仓统计。"""
    out: Dict[str, Any] = {}
    for tier, st in _MTF_STATS.items():
        total = st["total"]
        out[tier] = {
            "total": total,
            "veto": st["veto"],
            "downsize": st["downsize"],
            "veto_rate": round(st["veto"] / total, 4) if total else None,
            "downsize_rate": round(st["downsize"] / total, 4) if total else None,
        }
    return out


def _dir_to_bias(direction: str) -> str:
    d = (direction or "").lower()
    if d in ("long", "buy", "bullish"):
        return "bullish"
    if d in ("short", "sell", "bearish"):
        return "bearish"
    return "neutral"


def _cfg(name: str, default):
    from backend.config import settings as _s
    return getattr(_s, name, default)


def _bias_conf(orch: Dict[str, Any], key: str) -> float:
    for k in (f"{key}_confidence", f"{key}_conf"):
        try:
            v = orch.get(k)
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def evaluate_midlong_mtf_constraint(
    symbol: str,
    tier: str,
    direction: str,
    market_data: Optional[Dict[str, Any]],
) -> MidLongMtfResult:
    """对中长线开仓方向施加多周期一致性约束。

    Args:
        symbol: 交易对
        tier: mid / long
        direction: 开仓意图方向 long/short（或 buy/sell）
        market_data: 需含 `orchestrator` 字段（OrchBG 写入的多周期偏向）
    """
    if not bool(_cfg("MIDLONG_MTF_ENFORCE_ENABLED", True)):
        return MidLongMtfResult(reason="mtf_enforce_disabled")

    trade_bias = _dir_to_bias(direction)
    if trade_bias == "neutral":
        return MidLongMtfResult(reason="方向中性，无需约束")

    orch = {}
    if isinstance(market_data, dict):
        orch = market_data.get("orchestrator") or {}
    if not isinstance(orch, dict) or not orch:
        # 数据缺失不一刀切禁开（避免因无数据而全线停摆）
        return MidLongMtfResult(reason="无多周期数据，跳过约束")

    long_bias = str(orch.get("long_bias") or "neutral").lower()   # 日线
    mid_bias = str(orch.get("mid_bias") or "neutral").lower()     # 4h
    long_conf = _bias_conf(orch, "long")
    mid_conf = _bias_conf(orch, "mid")

    strong_conf = float(_cfg("MIDLONG_MTF_STRONG_CONF", 0.7) or 0.7)
    conflict_mult = float(_cfg("MIDLONG_MTF_CONFLICT_MULT", 0.6) or 0.6)

    conflicts: List[str] = []
    size_mult = 1.0
    veto = False
    _tier = (tier or "mid").lower()

    if _tier in ("long", "trend", "position"):
        # 长线：日线是自身锚定，若日线强反向说明论点自相矛盾 → 否决
        if long_bias != "neutral" and long_bias != trade_bias:
            if long_conf >= strong_conf:
                veto = True
                conflicts.append(f"日线强反向({long_bias},conf={long_conf:.2f})→否决")
            else:
                size_mult *= conflict_mult
                conflicts.append(f"日线弱反向({long_bias})→缩仓×{conflict_mult:.2f}")
        # 4h 强反向 → 深度回调风险，缩仓
        if mid_bias != "neutral" and mid_bias != trade_bias and mid_conf >= strong_conf:
            size_mult *= conflict_mult
            conflicts.append(f"4h强反向({mid_bias})→缩仓×{conflict_mult:.2f}")
    else:
        # 中线(swing)：日线为更高锚定周期
        if long_bias != "neutral" and long_bias != trade_bias:
            if long_conf >= strong_conf:
                veto = True
                conflicts.append(f"日线强反向({long_bias},conf={long_conf:.2f})→否决")
            else:
                size_mult *= conflict_mult
                conflicts.append(f"日线弱反向({long_bias})→缩仓×{conflict_mult:.2f}")
        # 4h 反向 → 缩仓
        if mid_bias != "neutral" and mid_bias != trade_bias:
            size_mult *= conflict_mult
            conflicts.append(f"4h反向({mid_bias})→缩仓×{conflict_mult:.2f}")

    # 修复：原"冲突≥2→否决"在震荡市几乎必然触发（日线弱反+4h弱反=2冲突）。
    # 改为"全部周期一致强反向才 veto"——对齐 unified_gate 的 multi_freq 柔性化。
    # 单周期/弱反向只缩仓不否决，给趋势恢复机会。
    # 仅当日线 AND 4h 都强反向(conf≥strong_conf)且方向与开仓相反时才 veto。
    long_strong_reverse = (long_bias != "neutral" and long_bias != trade_bias and long_conf >= strong_conf)
    mid_strong_reverse = (mid_bias != "neutral" and mid_bias != trade_bias and mid_conf >= strong_conf)
    if long_strong_reverse and mid_strong_reverse:
        veto = True
        conflicts.append("日+4h双强反向→否决")
    elif not veto:
        # 多周期弱反向只缩仓
        pass

    reason = "; ".join(conflicts) if conflicts else "多周期无冲突"
    _bump_stats(_tier, veto, size_mult < 0.999)
    if conflicts:
        logger.info(
            "[MidLongMTF] %s tier=%s bias=%s long(1d)=%s(%.2f) mid(4h)=%s(%.2f) → veto=%s size×%.2f (%s)",
            symbol, _tier, trade_bias, long_bias, long_conf, mid_bias, mid_conf,
            veto, size_mult, reason,
        )

    return MidLongMtfResult(
        veto=veto,
        size_multiplier=max(0.0, size_mult),
        conflicts=conflicts,
        reason=reason,
    )
