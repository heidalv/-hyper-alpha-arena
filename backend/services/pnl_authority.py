"""pnl_authority — 已实现盈亏权威口径（2026-08-19 设计 D3）。

问题：partial_realized_pnl 大多为 0（仅部分平仓路径才写），全平盈亏散落在
close_price 价差里；PaperOrder.pnl 在多数路径不落库。各报告/归因各自估算，
口径不一致（537 笔短线曾因口径问题被误读为 0 盈利）。

统一函数：realized_pnl(position)
  - partial_realized_pnl 绝对值 > 1e-9 时以其为准（部分平仓路径的权威值）；
  - 否则用 close_price 价差 × side 方向 × size 复原（全平路径）。
数据源统一为 PaperPosition（closed/liquidated），不再依赖 PaperOrder。
"""
from __future__ import annotations

from typing import Any


def realized_pnl(position: Any) -> float:
    """统一已实现盈亏口径。position 可为 PaperPosition 或 dict。"""
    try:
        pr = float(getattr(position, "partial_realized_pnl", None)
                   if not isinstance(position, dict) else position.get("partial_realized_pnl") or 0) or 0.0
        if abs(pr) > 1e-9:
            return pr
        entry = float(getattr(position, "entry_price", None)
                      if not isinstance(position, dict) else position.get("entry_price") or 0) or 0.0
        close = float(getattr(position, "close_price", None)
                      if not isinstance(position, dict) else position.get("close_price") or 0) or 0.0
        size = float(getattr(position, "size", None)
                     if not isinstance(position, dict) else position.get("size") or 0) or 0.0
        side = str(getattr(position, "side", None)
                   if not isinstance(position, dict) else position.get("side") or "").lower()
        sd = 1.0 if side == "long" else -1.0
        return (close - entry) * sd * size
    except Exception:
        return 0.0
