# -*- coding: utf-8 -*-
"""基差入场门控（p3-promotion）。

V3 基差 Paper 执行器已有；本模块只回答「能否按研究桶放量入场」：
  - promotion_freeze → 拒
  - 研究桶阶梯名义 > 0 或显式 BASIS_SMALL_ENABLED
  - 价差绝对值过门槛（env）
默认不下单，只给 orchestrator / API 做预检。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional


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


def basis_entry_allowed(
    *,
    basis_pct: float,
    strategy_id: str = "basis_arb",
) -> Dict[str, Any]:
    from backend.services.allocation.capital_allocator import (
        promotion_frozen, research_notional_for,
    )

    min_abs = _env_float("BASIS_ENTRY_MIN_ABS_PCT", 0.15)  # 0.15%
    freeze = promotion_frozen()
    if freeze.get("frozen"):
        return {"allowed": False, "reason": "promotion_freeze", "freeze": freeze}
    if abs(float(basis_pct)) < min_abs:
        return {"allowed": False, "reason": f"|basis|={basis_pct} < {min_abs}%",
                "min_abs_pct": min_abs}
    notion = research_notional_for(strategy_id)
    small_ok = _env_true("BASIS_SMALL_ENABLED", True)
    if notion.get("notional_usd", 0) <= 0 and not small_ok:
        return {"allowed": False, "reason": "研究桶未放量且 BASIS_SMALL_ENABLED=false",
                "notional": notion}
    # 未晋级时可用配置名义做 Paper
    notional = float(notion.get("notional_usd") or 0) or _env_float("BASIS_PAPER_NOTIONAL_USD", 200.0)
    return {
        "allowed": True,
        "notional_usd": notional,
        "basis_pct": basis_pct,
        "min_abs_pct": min_abs,
        "stage": notion.get("stage"),
        "live": _env_true("BASIS_LIVE", False),
        "note": "BASIS_LIVE 默认 false：预检通过仍走 Paper",
    }
