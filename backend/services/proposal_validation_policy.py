"""提案 Paper 验证策略 — Pace 联动时长 + 应用后成交切片（post-apply SRR）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

# 各 Pace 档位：最短浸泡时间（h）与超时 inconclusive 上限（h）
PACE_VALIDATION_POLICY: Dict[str, Dict[str, int]] = {
    "turbo": {"min_age_hours": 2, "max_wait_hours": 6},
    "warm": {"min_age_hours": 4, "max_wait_hours": 12},
    "balanced": {"min_age_hours": 8, "max_wait_hours": 24},
    "conservative": {"min_age_hours": 12, "max_wait_hours": 48},
}

# 窄训练期专用 profile（由 training_phase.active 自动切换）
TRAINING_NARROW_POLICY: Dict[str, int] = {
    "min_age_hours": 2,
    "max_wait_hours": 24,
    "min_samples": 3,
}


def current_paper_gear() -> str:
    try:
        from backend.services.paper_pace_controller import paper_pace_controller

        gear = (paper_pace_controller.gear or "balanced").lower()
        return gear if gear in PACE_VALIDATION_POLICY else "balanced"
    except Exception:
        return "balanced"


def validation_policy_for_gear(gear: Optional[str] = None) -> Dict[str, Any]:
    """返回当前档位验证参数（可被 OPENCODE_VALIDATION_HOURS 覆盖 max_wait）。"""
    try:
        from backend.services.training_phase_service import is_active

        if is_active():
            base = dict(TRAINING_NARROW_POLICY)
            base["gear"] = "training_narrow"
            base["mode"] = "post_apply_slice"
            return base
    except Exception:
        pass

    g = (gear or current_paper_gear()).lower()
    base = dict(PACE_VALIDATION_POLICY.get(g, PACE_VALIDATION_POLICY["balanced"]))
    try:
        from backend.config.settings import OPENCODE_VALIDATION_HOURS, OPENCODE_VALIDATION_USE_PACE

        if not OPENCODE_VALIDATION_USE_PACE:
            h = int(OPENCODE_VALIDATION_HOURS or 24)
            base["max_wait_hours"] = h
            base["min_age_hours"] = min(base["min_age_hours"], max(2, h // 4))
    except Exception:
        pass
    base["gear"] = g
    base["mode"] = "post_apply_slice"
    return base


def min_eval_samples(*, force: bool = False) -> int:
    try:
        from backend.services.training_phase_service import is_active

        if is_active():
            return 3
    except Exception:
        pass
    return 3 if force else 5


def can_evaluate_proposal(
    *,
    age_hours: float,
    post_apply_closed: int,
    gear: Optional[str] = None,
    force: bool = False,
) -> tuple[bool, str]:
    """是否满足评估条件（post-apply 样本 + Pace 最短浸泡）。"""
    if force:
        return post_apply_closed >= min_eval_samples(force=True), "force"

    pol = validation_policy_for_gear(gear)
    min_age = float(pol["min_age_hours"])
    min_n = min_eval_samples(force=False)

    if post_apply_closed < min_n:
        return False, f"samples_{post_apply_closed}<{min_n}"
    if age_hours < min_age:
        return False, f"age_{age_hours:.1f}h<{min_age}h"
    return True, "ready"


def should_mark_inconclusive(*, age_hours: float, post_apply_closed: int, gear: Optional[str] = None) -> bool:
    pol = validation_policy_for_gear(gear)
    max_wait = float(pol.get("max_wait_hours") or 24)
    if age_hours < max_wait:
        return False
    return post_apply_closed < min_eval_samples(force=False)
