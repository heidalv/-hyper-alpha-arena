# -*- coding: utf-8 -*-
"""[P2 大轮回 2026-09-27] 出场唯一裁决序（§9.2）——仲裁层。

设计 §9.2 优先级（唯一裁决序）：
  ① 硬止损（结构/波动） ② 失效条件 ③ 保本线 ④ 分批止盈 ⑤ 时间止损 ⑥ 极端回撤闸
「禁止同一时刻由两个模块各自决定出场（现在的多套出场并行是事故来源）」。

实现：不改 20+ 处平仓调用点，在 `paper_engine.close_position` 入口做**意图仲裁**：
  - 每个仓维护最近一次已执行平仓的 (时间, 原因, 优先级)；
  - 窗口（默认 10s）内：更低优先级的新平仓意图 → **跳过**并写审计日志
    （`[ExitArbiter] skip`）；更高/相同优先级 → 放行并更新登记；
  - 全部意图（含被跳过的）都记录，使「谁想平、谁赢、谁被让路」可复盘。
  - 部分平仓（quantity<全量）视为独立腿，不受窗口内其它腿仲裁（分批止盈腿不互斥）；
    只对**全量平仓意图**与**同优先级冲突**做让路。

回滚：EXIT_ARBITER_ENABLED=false。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_WINDOW_S = 10.0
_state: Dict[int, Tuple[float, str, int]] = {}
_lock = threading.Lock()

# 前缀 → 优先级（越高越先执行）。未命中 → 50（中等，不拦不抢）。
_PRIORITY_RULES = [
    ("stop_loss", 100), ("sl", 100), ("liquidation", 100), ("force_close", 110),
    ("manual", 110),
    ("emergency_drawdown", 95), ("defensive", 90),
    ("trend_broken", 85), ("rule_exit", 85), ("trend_e1", 85),
    ("invalidation", 80), ("thesis_invalidation", 80), ("ai_reverse", 75),
    ("breakeven", 70), ("profit_lock", 70), ("be", 70),
    ("staged_tp", 60), ("tp", 60), ("take_profit", 60),
    ("trailing", 55), ("trailing_stop", 55), ("callback", 55),
    ("max_hold", 40), ("time", 40), ("max_hold_timeout", 40),
    ("rebalance", 30), ("non_core", 30), ("rotation", 30),
    ("dust_cleanup", 10), ("dust", 10),
]


def enabled() -> bool:
    return (os.getenv("EXIT_ARBITER_ENABLED", "true") or "true").strip().lower() in (
        "1", "true", "yes", "on")


def window_s() -> float:
    try:
        return max(1.0, float(os.getenv("EXIT_ARBITER_WINDOW_S", "10") or 10))
    except (TypeError, ValueError):
        return _WINDOW_S


def priority_of(reason: str) -> int:
    r = str(reason or "").lower()
    for prefix, prio in _PRIORITY_RULES:
        if r.startswith(prefix):
            return prio
    return 50


def allow_close(position_id: Optional[int], reason: str, *,
                full_close: bool, ts: Optional[float] = None) -> Tuple[bool, str]:
    """平仓意图仲裁。返回 (是否执行, 说明)。

    - position_id 为空（历史调用不传）→ 直接放行（无法仲裁，保持旧行为）；
    - 非全量（部分腿）→ 放行并只登记（分批止盈腿之间不互斥）；
    - 全量平仓：窗口内已有更高优先级平仓执行过 → skip。
    """
    if not enabled():
        return True, "arbiter_off"
    if position_id is None:
        return True, "no_position_id"
    now = ts or time.time()
    prio = priority_of(reason)
    with _lock:
        if not full_close:
            _record_locked(position_id, now, reason, prio)
            return True, f"partial_leg(p{prio})"
        row = _state.get(position_id)
        if row:
            t0, best_reason, best_prio = row
            if now - t0 <= window_s() and prio < best_prio:
                logger.warning(
                    "[ExitArbiter] skip 平仓意图 %s pos=%s 原因=%s(p%d) < 已执行 %s(p%d) "
                    "（%.1fs 内，§9.2 唯一裁决序：低优先级让路）",
                    "full" if full_close else "partial", position_id, reason, prio,
                    best_reason, best_prio, now - t0,
                )
                return False, f"lower_priority({prio}<{best_prio} by {best_reason})"
        _record_locked(position_id, now, reason, prio)
        return True, f"accepted(p{prio})"


def _record_locked(position_id: int, ts: float, reason: str, prio: int) -> None:
    """调用方必须已持有 _lock。"""
    row = _state.get(position_id)
    if row and ts - row[0] <= window_s() and prio < row[2]:
        return  # 保持窗口内最高优先级
    _state[position_id] = (ts, reason, prio)
    # 惰性清理过期条目（避免无限增长）
    if len(_state) > 4096:
        now = ts
        for k in [k for k, v in _state.items() if now - v[0] > window_s()]:
            _state.pop(k, None)
