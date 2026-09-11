# -*- coding: utf-8 -*-
"""[§52 2026-09-10 第 14 轮] 开仓被拒原因的**跨层传递**（修复「审计粒度缺口」）。

问题（§52.1 实证，`_audit_ml/Z64*` / `Z69`）：
mid/long 开仓被拒的**真实原因**分布在三个层次，而漏斗审计（`data/midlong_direction_audit.jsonl`，
唯一的长期持久化记录）只在 `midlong_helpers.evaluate_and_execute` 写一行**通用**原因
`reason="evaluate_and_execute_returned_false"`：

    brain.can_open_block_reason()        → 有记录（thesis 事件 open_blocked，30min 节流）
    proposal_execution 各 return False   → 只有 logger.info，**审计里无因**
    paper_execution 模拟下单失败         → 只有 logger.warning（含 code=），**审计里无因**

实测：4235 条拒仓里 **1866 条（44.1%）** 是这行通用原因；逐条取证后确认它们全是
`[RiskEngine] BLOCK ... code=daily_quota`（当日开仓配额用尽）——即「最大的单一拦截来源」
在审计里完全不可见（`Z69` 就地取证）。日志会被滚动/分片，靠日志考古不可持续。

本模块提供一个**极轻量的跨层标记**：拒绝方 `mark_open_block(code, ...)`，
审计写入方 `take_open_block()` 取走并落进 `reason` / `extra`。
- 线程/协程隔离（`ContextVar`），不跨请求串味；
- **不改变任何交易行为**（只影响审计文本）；
- 取走即清空（`take_`），避免同线程后续无关审计行被旧原因污染；
- 未标记时调用方必须回落到旧字符串，保证向后兼容（老看板/汇总不受影响）。
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BLOCK: ContextVar[Optional[Dict[str, Any]]] = ContextVar("midlong_open_block", default=None)


def mark_open_block(code: str, *, detail: str = "", layer: str = "") -> None:
    """记录「本次开仓被谁拒了」。后者覆盖前者（调用链越深=越具体，故后写覆盖）。"""
    try:
        _BLOCK.set({
            "code": str(code or "").strip()[:80],
            "detail": str(detail or "")[:200],
            "layer": str(layer or "").strip()[:40],
        })
    except Exception as exc:  # pragma: no cover - 不应发生
        logger.debug("[OpenBlock] mark 失败: %s", exc)


def peek_open_block() -> Optional[Dict[str, Any]]:
    """只读查看（不消费）。"""
    return _BLOCK.get()


def take_open_block() -> Optional[Dict[str, Any]]:
    """取走并清空（审计写入点用一次）。"""
    cur = _BLOCK.get()
    _BLOCK.set(None)
    return cur


def clear_open_block() -> None:
    _BLOCK.set(None)
