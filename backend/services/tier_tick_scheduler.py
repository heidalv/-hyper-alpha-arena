"""
三周期分层 Tick 调度

原则：
- 协调器 tick（快）：学习集成、持仓巡检、Paper 解锁 —— 不调 LLM
- 短线：ScalpRouter 独立循环（SCALP_FACTOR_SCAN_INTERVAL_SEC）
- 中线 AI：TIER_MID_AI_TICK_SEC（默认 120s，建议 90–150）
- 长线 AI：TIER_LONG_AI_TICK_SEC（默认 240s，建议 120–300）
- 上一轮 AI/LLM 未结束 → 跳过本轮 AI（由 FullAuto 进程锁兜底）

快速试单加速的是「学习回填 + 开单门控」，不是把三周期 AI 都压到 30s。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# session_id -> tier -> last_run_ts
_last_ai_run: Dict[str, Dict[str, float]] = {}
# session_id -> last coordinator (lightweight) run
_last_coord_run: Dict[str, float] = {}


#: settings 字段名 → (tier 名, 硬编码兜底下限)。
#: 2026-07-06 整改（审查 3 #16）：真正的下限来自 paper_fast_trial_controller.PARAM_DEFS
#: 里每个参数自己声明的 "min"（前端调参面板用同一套 min 做校验），此前本文件又在
#: PARAM_DEFS 之外单独写死了一份 15/20/60/90——两份定义一旦不一致（例如 PARAM_DEFS
#: 把 TIER_MID_AI_TICK_SEC 的 min 声明为 45，这里却仍强制 60），用户在前端把参数调到
#: 45 也会被本文件悄悄再拉回 60，配置形同虚设。改为直接读 PARAM_DEFS.min，只有在
#: PARAM_DEFS 里找不到对应字段时才退回下面这个兜底值。
_SETTINGS_FIELD_TO_TIER = {
    "TIER_COORDINATOR_TICK_SEC": ("coordinator", 15),
    "SCALP_FACTOR_SCAN_INTERVAL_SEC": ("short", 20),
    "TIER_MID_AI_TICK_SEC": ("mid", 45),
    "TIER_LONG_AI_TICK_SEC": ("long", 90),
}


def _param_min(field: str, fallback: int) -> int:
    """从 PARAM_DEFS 里读取某个 settings 字段声明的 min，读不到则用兜底值。"""
    try:
        from backend.services.paper_fast_trial_controller import PARAM_DEFS
        for p in PARAM_DEFS:
            if p.get("key") == field and "min" in p:
                return int(p["min"])
    except Exception:
        pass
    return fallback


def _intervals() -> Dict[str, int]:
    try:
        from backend.config import settings
        defaults = {"coordinator": 45, "short": 45, "mid": 120, "long": 240}
        result: Dict[str, int] = {}
        for field, (tier, fallback_min) in _SETTINGS_FIELD_TO_TIER.items():
            floor = _param_min(field, fallback_min)
            configured = int(getattr(settings, field, defaults[tier]) or defaults[tier])
            # 秒级实时：short(scalp) 下限不再被 PARAM_DEFS 旧 min(20) 抬回
            if tier == "short":
                floor = min(floor, 5)
            result[tier] = max(floor, configured)
        return result
    except Exception:
        return {"coordinator": 45, "short": 45, "mid": 120, "long": 240}


def get_intervals() -> Dict[str, int]:
    return dict(_intervals())


def _elapsed(session_id: str, tier: str) -> float:
    with _lock:
        return time.time() - (_last_ai_run.get(session_id, {}).get(tier, 0.0))


def mark_tier_run(session_id: str, tiers: List[str]) -> None:
    now = time.time()
    with _lock:
        bucket = _last_ai_run.setdefault(session_id, {})
        for t in tiers:
            bucket[t] = now


def mark_coordinator_run(session_id: str) -> None:
    with _lock:
        _last_coord_run[session_id] = time.time()


def get_due_ai_tiers(session_id: str) -> List[str]:
    """返回本轮应跑 LLM/MLTO 的 tier 列表（mid/long）。

    2026-07-20：尊重 TIER_MID_ENABLED / TIER_LONG_ENABLED 开关。
    被关闭的 tier 不会出现在返回列表里，从而不会被调度执行。
    """
    iv = _intervals()
    # 读取 tier 级别开关（默认 true 保持兼容）
    tier_enabled = {"mid": True, "long": True}
    try:
        from backend.config import settings as _s
        tier_enabled["mid"] = getattr(_s, "TIER_MID_ENABLED", True)
        tier_enabled["long"] = getattr(_s, "TIER_LONG_ENABLED", True)
    except Exception:
        pass
    due: List[str] = []
    for tier in ("mid", "long"):
        if not tier_enabled.get(tier, True):
            continue
        if _elapsed(session_id, tier) >= iv[tier]:
            due.append(tier)
    return due


def seconds_until_due(session_id: str, tier: str) -> int:
    iv = _intervals()
    gap = iv.get(tier, 120) - _elapsed(session_id, tier)
    return max(0, int(gap))


def status(session_id: str) -> Dict[str, Any]:
    iv = _intervals()
    return {
        "intervals_sec": iv,
        "due_now": get_due_ai_tiers(session_id),
        "until_due_sec": {
            t: seconds_until_due(session_id, t) for t in ("mid", "long")
        },
        "last_ai_run": dict(_last_ai_run.get(session_id, {})),
        "note": "short=ScalpRouter独立; coordinator=轻量心跳不含LLM",
    }


def format_skip_reason(session_id: str) -> str:
    iv = _intervals()
    parts = [
        f"mid {_elapsed(session_id, 'mid'):.0f}s/{iv['mid']}s",
        f"long {_elapsed(session_id, 'long'):.0f}s/{iv['long']}s",
    ]
    return " · ".join(parts)
