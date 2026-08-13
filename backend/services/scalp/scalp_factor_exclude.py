"""短线因子计算排除类别/精选白名单 — Loop 与 Router 共用，禁止魔法元组分叉。

默认排除 PATTERN / BEHAVIORAL（与 2026-07-10 热路径性能修复一致）。
回滚：SCALP_EXCLUDE_PATTERN=0|false|off

[2026-08-13 P0-1] 实盘因子集收敛：默认只计算有 OOS 证据的精选池因子。
回滚：SCALP_USE_VETTED_FACTORS_ONLY=0|false|off
"""
from __future__ import annotations

import os
from typing import Optional, Set

from backend.services.factor_engine.base_factors import FactorCategory

_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def scalp_exclude_pattern_enabled() -> bool:
    raw = (os.getenv("SCALP_EXCLUDE_PATTERN", "1") or "1").strip().lower()
    if raw in _FALSY:
        return False
    return raw in _TRUTHY or raw == ""


def get_scalp_factor_exclude_categories() -> Optional[Set[FactorCategory]]:
    """返回传给 compute_all_factors(exclude_categories=...) 的集合；关闭排除时返回 None。"""
    if not scalp_exclude_pattern_enabled():
        return None
    return {FactorCategory.PATTERN, FactorCategory.BEHAVIORAL}


def scalp_use_vetted_factors_only() -> bool:
    """[2026-08-13 P0-1] 实盘只使用有 OOS 证据的精选因子（默认开）。

    实证：21.7 万信号中 130+ 无 OOS 验证的 ai_generated 因子全量加权，
    因子分数与真实胜率零相关（诊断 15/17）。默认收敛到精选池止血。
    """
    raw = (os.getenv("SCALP_USE_VETTED_FACTORS_ONLY", "true") or "true").strip().lower()
    if raw in _FALSY:
        return False
    return raw in _TRUTHY or raw == ""


def get_scalp_factor_allowlist() -> Optional[Set[str]]:
    """[2026-08-13 P0-1] 精选因子白名单（与 FACTORS 键名同构）。

    来源 = factor_active_set 表 state∈{ACTIVE,PAPER}（进化链 OOS 验证通过）
           ∪ custom_factor_store status=active 公式因子（打分闸门 A/B 级）。
    开关关闭 → None（不限制，全量计算）；开启但精选池为空 → 空集（宁缺毋滥，全拦）。
    """
    if not scalp_use_vetted_factors_only():
        return None
    allow: Set[str] = set()
    try:
        from backend.services.factor_engine.scalp_active_factor_set import scalp_active_factor_set
        for rec in scalp_active_factor_set.get_active_factors():
            fid = rec.get("factor_id")
            if fid:
                allow.add(str(fid))
    except Exception:
        pass
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import FactorActiveSet
        db = SessionLocal()
        try:
            rows = (
                db.query(FactorActiveSet.factor_id)
                .filter(FactorActiveSet.state.in_(["ACTIVE", "PAPER"]))
                .all()
            )
            for (fid,) in rows:
                if fid:
                    allow.add(str(fid))
        finally:
            db.close()
    except Exception:
        pass
    return allow
