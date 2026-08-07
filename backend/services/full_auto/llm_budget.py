"""MidLong v2 Phase3 — Master / MidLong / Scalp LLM 分桶预算。

与全局 LLM_GLOBAL_MAX_CONCURRENT 正交：全局关闭时本模块仍可单独限流。

多账户（2026-08）：
  - 平台天花板：LLM_BUDGET_MASTER/MIDLONG/...（全进程共享硬顶）
  - 租户槽位：LLM_BUDGET_PER_TENANT=true 时，每个 tenant 另有独立 Semaphore，
    账户 A 打满 midlong 不会占死账户 B 的 midlong 槽（但仍受平台天花板约束）。
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_tls = threading.local()
_init_lock = threading.Lock()
_sems: dict = {}  # key -> Semaphore | None（None=该桶不限）


def _enabled() -> bool:
    try:
        from backend.config.settings import LLM_BUDGET_ENABLED
        return bool(LLM_BUDGET_ENABLED)
    except Exception:
        return True


def _per_tenant_enabled() -> bool:
    try:
        from backend.config.settings import LLM_BUDGET_PER_TENANT
        return bool(LLM_BUDGET_PER_TENANT)
    except Exception:
        return True


def classify_llm_bucket(caller: Optional[str]) -> str:
    """按 caller 字符串归入 master | midlong | scalp | other。"""
    c = (caller or "").lower()
    if any(x in c for x in (
        "scalp", "scalpagent", "scalp_router", "scalpexecution",
        "short_tier", "intraday",
    )):
        return "scalp"
    if any(x in c for x in (
        "trendagent", "trend_agent", "swingagent", "swing_agent",
        "mlto", "midlong", "thesis", "qual_layer", "decision_hub",
    )):
        return "midlong"
    if any(x in c for x in (
        "master", "mastercontroller", "qaa", "analyst_system",
        "fullauto", "unified_analys", "run_full_analysis",
    )):
        return "master"
    return "other"


def _current_tenant_id() -> Optional[int]:
    try:
        from backend.core.tenant import tenant_id_var
        tid = tenant_id_var.get()
        return int(tid) if tid is not None else None
    except Exception:
        return None


def _bucket_limit(bucket: str) -> int:
    try:
        from backend.config.settings import (
            LLM_BUDGET_MASTER,
            LLM_BUDGET_MIDLONG,
            LLM_BUDGET_OTHER,
            LLM_BUDGET_SCALP,
            MASTER_MIDLONG_LLM_MODE,
        )
        if bucket == "midlong":
            return max(0, int(LLM_BUDGET_MIDLONG))
        if bucket == "scalp":
            return max(0, int(LLM_BUDGET_SCALP))
        if bucket == "master":
            n = max(0, int(LLM_BUDGET_MASTER))
            if str(MASTER_MIDLONG_LLM_MODE or "summary").lower() == "summary":
                return max(1, min(n, int(LLM_BUDGET_MASTER)))
            return n
        return max(0, int(LLM_BUDGET_OTHER))
    except Exception:
        return {"midlong": 6, "master": 6, "scalp": 9, "other": 4}.get(bucket, 2)


def _tenant_bucket_limit(bucket: str) -> int:
    try:
        from backend.config.settings import (
            LLM_BUDGET_TENANT_MASTER,
            LLM_BUDGET_TENANT_MIDLONG,
            LLM_BUDGET_TENANT_OTHER,
            LLM_BUDGET_TENANT_SCALP,
        )
        if bucket == "midlong":
            return max(0, int(LLM_BUDGET_TENANT_MIDLONG))
        if bucket == "scalp":
            return max(0, int(LLM_BUDGET_TENANT_SCALP))
        if bucket == "master":
            return max(0, int(LLM_BUDGET_TENANT_MASTER))
        return max(0, int(LLM_BUDGET_TENANT_OTHER))
    except Exception:
        return {"midlong": 2, "master": 2, "scalp": 3, "other": 2}.get(bucket, 2)


def _get_sem(key: str, limit: int) -> Optional[threading.Semaphore]:
    with _init_lock:
        if key not in _sems:
            if limit <= 0:
                _sems[key] = None
                logger.info("[LLMBudget] key=%s 不限制", key)
            else:
                _sems[key] = threading.Semaphore(limit)
                logger.info("[LLMBudget] key=%s limit=%d", key, limit)
        return _sems[key]


def _wait_seconds(bucket: str) -> float:
    try:
        from backend.config.settings import (
            LLM_BUDGET_WAIT_MASTER,
            LLM_BUDGET_WAIT_MIDLONG,
            LLM_BUDGET_WAIT_OTHER,
            LLM_BUDGET_WAIT_SCALP,
            LLM_SEMAPHORE_WAIT_SECONDS,
        )
        if bucket == "midlong":
            return float(LLM_BUDGET_WAIT_MIDLONG)
        if bucket == "master":
            return float(LLM_BUDGET_WAIT_MASTER)
        if bucket == "scalp":
            return float(LLM_BUDGET_WAIT_SCALP)
        if bucket == "other":
            return float(LLM_BUDGET_WAIT_OTHER)
        return float(LLM_SEMAPHORE_WAIT_SECONDS)
    except Exception:
        return {"midlong": 45.0, "master": 15.0, "scalp": 20.0, "other": 10.0}.get(
            bucket, 20.0
        )


def acquire_llm_budget(*, caller: Optional[str]) -> bool:
    """获取分桶槽；失败返回 False。成功时在 tls 记录以便 release。"""
    if not _enabled():
        return True
    if getattr(_tls, "bucket", None):
        _tls.nested = int(getattr(_tls, "nested", 0) or 0) + 1
        return True

    bucket = classify_llm_bucket(caller)
    wait_s = max(0.0, _wait_seconds(bucket))
    held_keys: list = []

    # 1) 平台天花板
    plat = _get_sem(f"platform:{bucket}", _bucket_limit(bucket))
    if plat is not None:
        if not plat.acquire(timeout=wait_s):
            logger.warning(
                "[LLMBudget] 平台槽超时 bucket=%s caller=%s wait=%.1fs",
                bucket, caller, wait_s,
            )
            return False
        held_keys.append(f"platform:{bucket}")

    # 2) 租户槽（有 tenant 上下文时）
    tid = _current_tenant_id() if _per_tenant_enabled() else None
    if tid is not None:
        tkey = f"tenant:{tid}:{bucket}"
        tsem = _get_sem(tkey, _tenant_bucket_limit(bucket))
        if tsem is not None:
            if not tsem.acquire(timeout=wait_s):
                # 归还已持有的平台槽
                if plat is not None:
                    try:
                        plat.release()
                    except Exception:
                        pass
                logger.warning(
                    "[LLMBudget] 租户槽超时 tenant=%s bucket=%s caller=%s",
                    tid, bucket, caller,
                )
                return False
            held_keys.append(tkey)

    _tls.bucket = bucket
    _tls.held_keys = held_keys
    _tls.held = bool(held_keys)
    return True


def release_llm_budget() -> None:
    nested = int(getattr(_tls, "nested", 0) or 0)
    if nested > 0:
        _tls.nested = nested - 1
        return
    held_keys = list(getattr(_tls, "held_keys", None) or [])
    _tls.bucket = None
    _tls.held = False
    _tls.held_keys = None
    for key in reversed(held_keys):
        sem = _sems.get(key)
        if sem is not None:
            try:
                sem.release()
            except Exception:
                pass
