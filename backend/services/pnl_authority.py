"""pnl_authority — 已实现盈亏权威口径（2026-08-19 设计 D3）。

问题：partial_realized_pnl 大多为 0（仅部分平仓路径才写），全平盈亏散落在
close_price 价差里；PaperOrder.pnl 在多数路径不落库。各报告/归因各自估算，
口径不一致（537 笔短线曾因口径问题被误读为 0 盈利）。

统一函数：realized_pnl(position)
  - partial_realized_pnl 绝对值 > 1e-9 时以其为准（部分平仓路径的权威值）；
  - 否则用 close_price 价差 × side 方向 × size 复原（全平路径）。
数据源统一为 PaperPosition（closed/liquidated），不再依赖 PaperOrder。

## 时间基准（轮63 2026-09-18 修正）

`paper_positions.opened_at / closed_at` 是**本地时区的 naive 时间戳**
（服务器 TimeZone=Asia/Shanghai；实测 `closed_at` 最大值为本地 16:48，
而当时的 `datetime.utcnow()` 是 09:18 —— 用 utcnow 做基线会把 4 笔「未来」样本
算进 24 小时窗口，并使按日期归档的报告混进两个本地日历日）。

因此凡是要与这些列比较的时间，一律用本模块的 `now_local()`，
不要用 `datetime.utcnow()`。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Tuple


def now_local() -> datetime:
    """与 DB 的 naive 本地时间戳同基准的「现在」。

    仓库里 `datetime.utcnow()` 大量存在，但与 `paper_positions` 的时间列比较时
    必须用本函数，否则会出现 8 小时错位（Asia/Shanghai）。
    """
    return datetime.now()


def hours_ago(hours: float) -> datetime:
    """本地基准的「hours 小时前」。"""
    return now_local() - timedelta(hours=hours)


def rolling_window(hours: float) -> Tuple[datetime, datetime]:
    """本地基准的滚动窗口 (起, 止)。"""
    end = now_local()
    return end - timedelta(hours=hours), end


def local_day_window(date_str: str) -> Tuple[datetime, datetime]:
    """某个**本地日历日**的窗口 [date 00:00, date+1 00:00)。

    历史报告按本地日重建，与用户看到的日期标签一致。
    """
    d0 = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    return d0, d0 + timedelta(days=1)


def local_today() -> str:
    """本地日期 YYYY-MM-DD。"""
    return now_local().strftime("%Y-%m-%d")


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
