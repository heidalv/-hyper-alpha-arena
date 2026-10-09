"""周期联动协调器（2026-09-07 v1 · 用户拍板：严格一致）。

根因
====
MTOrchestrator 每币算 L/M/S 三周期 bias + recommended_slots + slot_actions，
但主脑开仓路径（mlto/brain.py）此前零引用——各车道独立决策，理论上可同币
长线做多、日内做空（左右互搏）。本模块把编排器的多周期结论转成「每币每车道
许可表」，唯一消费点挂在 brain.can_open_block_reason（所有车道开仓必经）。

严格一致规则（用户 2026-09-07 拍板）
====================================
- 长线 bias 强多（bullish 且 conf≥0.6）：中线/日内只许开多
- 长线 bias 强空（bearish 且 conf≥0.6）：中线/日内只许开空
- 中性/弱：日内双向但保证金 ×0.5（cycle_size_mult），中线双向不变
- 长线车道自身由其论题+L1 结构自决（它是最高周期，无上级可协调），不在此拦截

fail-open 原则：编排器数据缺失时不拦截（保持现状），只记录 debug。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CONF_TH = 0.6


def _allow_cycle_opposite() -> bool:
    """[2026-10-09 用户授权] 允许中线/长线反向（解除"空头锁死"）。

    实测：中线论题在偏空市况下全是 short，却被本协调器以
    `cycle_conflict:长线强多(conf=0.68)禁mid开空` 硬拒 ⇒ 中线自 9 月仅 1 笔持仓。
    开关 MIDLONG_ALLOW_CYCLE_OPPOSITE（默认 false = 今日行为）；读 .env 兜底。
    """
    import os as _os_c

    v = _os_c.getenv("MIDLONG_ALLOW_CYCLE_OPPOSITE")
    if v is None or str(v).strip() == "":
        try:
            from pathlib import Path as _P

            for _cand in (_P(__file__).resolve().parents[3] / ".env", _P.cwd() / ".env"):
                if not _cand.exists():
                    continue
                for _line in _cand.read_text(encoding="utf-8", errors="replace").splitlines():
                    _s = _line.strip()
                    if _s.startswith("MIDLONG_ALLOW_CYCLE_OPPOSITE="):
                        v = _s.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return str(v or "false").strip().lower() in ("1", "true", "yes", "on")



def coordinator_enabled() -> bool:
    try:
        from backend.config.settings import CYCLE_COORDINATOR_ENABLED
        return bool(CYCLE_COORDINATOR_ENABLED)
    except Exception:
        import os
        return os.getenv("CYCLE_COORDINATOR_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def _orch_of(symbol: str, market_summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ms = (market_summary or {}).get(str(symbol or "").upper()) or {}
    orch = ms.get("orchestrator") if isinstance(ms, dict) else None
    return orch if isinstance(orch, dict) else {}


def _bias_conf(orch: Dict[str, Any], prefix: str) -> Tuple[str, float]:
    bias = str(orch.get(f"{prefix}_bias") or "").strip().lower()
    try:
        conf = float(orch.get(f"{prefix}_confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return bias, conf


def lane_permission(
    symbol: str,
    tier: str,
    direction: str,
    market_summary: Optional[Dict[str, Any]],
) -> Tuple[bool, str, float]:
    """严格一致矩阵。返回 (allowed, reason, size_mult)。

    tier: short/mid/long；direction: long/short（拟开方向）。
    size_mult 仅在「允许但降仓」时 < 1.0（日内双向中性市 0.5）。
    """
    if not coordinator_enabled():
        return True, "", 1.0
    orch = _orch_of(symbol, market_summary)
    if not orch:
        return True, "", 1.0  # 无编排器数据：fail-open 保持现状

    lb, lc = _bias_conf(orch, "long")
    d = "long" if str(direction or "").lower() in ("long", "buy") else "short"
    t = str(tier or "").lower()
    if t not in ("short", "mid", "long"):
        t = "mid"

    strong_bull = lb == "bullish" and lc >= _CONF_TH
    strong_bear = lb == "bearish" and lc >= _CONF_TH

    # [2026-09-08] 日内波段(short)豁免趋势对齐硬拦：日内是均值回归（超买摸顶空/
    # 超卖抄底多），逆势正是其策略本身，趋势对齐会把日内车道全部卡死（UNI 实证：
    # 长线 conf=0.60 刚好压线 → 日内空被拦）。趋势对齐只约束 mid/long 趋势车道；
    # 日内的风险由它自己的 SL 2% + 熔断闸托底，不靠趋势对齐。
    _trend_aligned_tiers = ("mid", "long")

    if strong_bull:
        if d == "short" and t in _trend_aligned_tiers and not _allow_cycle_opposite():
            return False, f"cycle_conflict:长线强多(conf={lc:.2f})禁{t}开空", 1.0
        return True, "", 1.0
    if strong_bear:
        if d == "long" and t in _trend_aligned_tiers and not _allow_cycle_opposite():
            return False, f"cycle_conflict:长线强空(conf={lc:.2f})禁{t}开多", 1.0
        return True, "", 1.0
    # 中性/弱：日内双向但降仓 0.5；中线双向不变；长线车道自决不拦
    if t == "short":
        return True, "", 0.5
    return True, "", 1.0


def cycle_size_mult(
    symbol: str,
    tier: str,
    direction: str,
    market_summary: Optional[Dict[str, Any]],
) -> float:
    """开仓保证金乘数（maybe_open 调用）。拦截场景此处返回 1.0（拦截由 can_open 负责）。"""
    try:
        allowed, _reason, mult = lane_permission(symbol, tier, direction, market_summary)
        return float(mult) if allowed else 1.0
    except Exception:
        return 1.0
