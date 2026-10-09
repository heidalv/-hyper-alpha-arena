# -*- coding: utf-8 -*-
"""正文截断口径（**单一来源**）—— 活动流 / 决策流里"分析正文"保留多少字符。

## 为什么要有这个模块（2026-09-19 排查实证）
排查发现：主脑的分析正文被**硬编码截断**，而截断长度定得过小，
导致界面上"看不到 agent 在说什么"：

| 位置 | 旧截断 | 实测后果 |
|---|---|---|
| `full_auto_routes.py` 活动流 `reasoning` | `[:120]` | **mid 83% / long 68% 的条目正好顶在 120 字符**；而同一批论题的 `reasoning_content` 中位 **2853~3809 字符** ⇒ 界面只见约 **4%** |
| `atas_routes.py` 决策流 `reasoning` | `[:200]` | 决策理由只见片段（前端还再 `line-clamp-2` 一次） |

本模块把口径集中到一处，并允许用环境变量调整（默认 **800**，`0` = 不截断）：

    AGENT_ACTIVITY_REASONING_MAX=800   # 字符数；0 表示不截断

调用方一律用 `clip(text)`，**不要**再写 `[:N]` 字面量 ——
有测试钉住这一点（`test_agent_wall_and_reasoning_20260919.py`）。
"""
from __future__ import annotations

import os

#: 默认保留字符数（旧硬编码为 120/200；800 ≈ 旧值的 4~7 倍，30 条 × 800 字 ≈ 24KB，可接受）
DEFAULT_REASONING_MAX = 800


def reasoning_max(default: int = DEFAULT_REASONING_MAX) -> int:
    """当前生效的正文截断长度；`0` 表示不截断。非法值回退到默认。"""
    raw = (os.getenv("AGENT_ACTIVITY_REASONING_MAX") or "").strip()
    if not raw:
        return max(0, int(default))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return max(0, int(default))


def clip(text: object, max_chars: int | None = None) -> str:
    """按当前口径截断正文。`max_chars=None` 时读环境变量；`0` 表示不截断。"""
    s = "" if text is None else str(text)
    n = reasoning_max() if max_chars is None else max(0, int(max_chars))
    if n == 0 or len(s) <= n:
        return s
    return s[:n]


def clip_with_flag(text: object, max_chars: int | None = None) -> tuple[str, bool]:
    """返回 `(文本, 是否被截断)`；供需要在前端显示"已截断"标记的调用点使用。"""
    s = "" if text is None else str(text)
    out = clip(s, max_chars)
    return out, len(out) < len(s)
