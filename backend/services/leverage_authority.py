# backend/services/leverage_authority.py
"""单一杠杆权威(根因 2 终态)。

所有路径(主控 + scalp)计算杠杆时只读此表,不再各自硬编码。
废除 _unify 的 max 覆盖(阶段 A 已止血,此处为权威源)。

说明：
- 本表是 tier 维度的 *上限 cap*，不是「按周期固定分配杠杆」。
- 交易所对同一交易对（净仓）只有一套杠杆；短/中/长是本地分仓记账。
- 实际开仓杠杆由动态杠杆等路径算出后，再经本 cap / mental_cap 钳制；
  已有仓时必须跟交易所/本地统一仓杠杆对齐（见 paper_trading_engine._unify_leverage_for_side）。
- 中长线升级禁止另立 3x/5x 等覆盖规则。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

# tier→杠杆 *上限*（ceiling）。勿把此表理解成固定分配表。
TIER_LEVERAGE_CAP: dict[str, int] = {
    "short": 20,
    "mid": 20,
    "long": 12,
}
DEFAULT_LEVERAGE = 10
MIN_LEVERAGE = 1.0


def extract_existing_symbol_leverage(
    symbol: str | None,
    positions: Optional[Iterable[Any]] = None,
) -> Optional[float]:
    """同币已有仓时返回其杠杆（多腿取 max）；无仓返回 None。

    兼容 paper / HL / CCXT 仓位 dict 或对象字段：
    symbol|coin、leverage（dict.value 或标量）、size|szi|quantity。
    """
    sym = str(symbol or "").upper().strip()
    if not sym or not positions:
        return None
    levs: list[float] = []
    for p in positions:
        try:
            if isinstance(p, dict):
                p_sym = str(p.get("symbol") or p.get("coin") or "").upper().strip()
                raw_lev = p.get("leverage")
                size = abs(float(
                    p.get("size") or p.get("szi") or p.get("quantity") or p.get("qty") or 0
                ))
            else:
                p_sym = str(
                    getattr(p, "symbol", None) or getattr(p, "coin", None) or ""
                ).upper().strip()
                raw_lev = getattr(p, "leverage", None)
                size = abs(float(
                    getattr(p, "size", None)
                    or getattr(p, "szi", None)
                    or getattr(p, "quantity", None)
                    or 0
                ))
            if p_sym != sym:
                continue
            if size <= 0 and isinstance(p, dict) and not p.get("leverage"):
                # 无 size 也无 leverage 字段 → 跳过空壳
                continue
            if isinstance(raw_lev, dict):
                lev = float(raw_lev.get("value") or 0)
            else:
                lev = float(raw_lev or 0)
            if lev >= MIN_LEVERAGE:
                levs.append(lev)
        except Exception:
            continue
    if not levs:
        return None
    return max(levs)


def resolve_leverage(
    tier: str | None,
    requested: float | None = None,
    mental_cap: float | None = None,
) -> float:
    """解析最终杠杆 = min(requested, tier_cap?, mental_cap)。

    - tier_cap: TIER_LEVERAGE_CAP[tier]（受 RISK_USE_LEVERAGE_CAP_BY_TIER 门控）
    - mental_cap:心态状态机连亏下调的 cap(respect,不被每 tick 重置 —— 阶段 A)
    - requested:动态计算/AI 建议值；已有仓时应传入交易所统一杠杆
    """
    if tier is None:
        # tier 未知时用最保守 cap(long=12),不绕过限制。此前用 max(=20)会让遗漏 tier
        # 的调用方跑到 20x、绕过 long 的 12x 上限 —— 安全默认应为收紧而非放宽。
        _cap = min(TIER_LEVERAGE_CAP.values())
    else:
        # 已知 tier 查表;未知 tier 字符串同样回退到最保守 cap(long=12)。
        _cap = TIER_LEVERAGE_CAP.get(tier, min(TIER_LEVERAGE_CAP.values()))
    _cand = float(requested) if requested is not None else float(DEFAULT_LEVERAGE)
    if mental_cap is not None:
        _cand = min(_cand, float(mental_cap))

    _use_tier_cap = True
    try:
        from backend.config.settings import RISK_USE_LEVERAGE_CAP_BY_TIER
        _use_tier_cap = bool(RISK_USE_LEVERAGE_CAP_BY_TIER)
    except Exception:
        _use_tier_cap = True
    if _use_tier_cap:
        _cand = min(_cand, float(_cap))
    else:
        # 关闭按周期 cap 时仍保留硬安全上限（与交易路径常见 20x 一致）
        _cand = min(_cand, 20.0)
    return max(MIN_LEVERAGE, _cand)
