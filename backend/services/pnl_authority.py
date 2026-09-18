"""pnl_authority — 已实现盈亏权威口径（2026-08-19 设计 D3）。

问题：partial_realized_pnl 大多为 0（仅部分平仓路径才写），全平盈亏散落在
close_price 价差里；PaperOrder.pnl 在多数路径不落库。各报告/归因各自估算，
口径不一致（537 笔短线曾因口径问题被误读为 0 盈利）。

统一函数：realized_pnl(position)
  - **已平仓/爆仓**（status ∈ closed/liquidated）以 `unrealized_pnl` 为准 —— 该列在平仓时被
    引擎复用为「**本笔最终已实现盈亏合计**」（paper_trading_engine 平仓路径显式写入
    `total_pnl = final_pnl + partial_pnl_sum`），是唯一完整口径；
  - 未平仓（或 status 缺失的纯 dict 入参）退回：partial_realized_pnl 绝对值 > 1e-9 时以其为准，
    否则用 close_price 价差 × side 方向 × size 复原。

## 轮65 修正：分档止盈（scaled-out）的尾部那一腿曾被整段丢掉

旧实现在任何情况下都优先返回 `partial_realized_pnl`。但分档止盈每次减仓只**累加一腿**到该列
（`pos.partial_realized_pnl = 老值 + partial_pnl`），平仓时**不清零**，而最终那一腿写在
`unrealized_pnl` 里。于是 `realized_pnl()` 只返回前面几腿之和 —— 实测：

    仓位 4672（UNI long）订单账本：+7.55(staged_tp1) +9.23(staged_tp2) +28.96(tp) = 45.74
      → unrealized_pnl（引擎合计，正确） = 45.742
      → 旧 realized_pnl()（只有前两腿）   = 16.781     少算 28.96
    仓位 4655（XPL short）：+7.59 + 23.77 = 31.35
      → 旧 realized_pnl() = 7.588                       少算 23.77

全库实测：**534 笔已平仓记录两口径不一致**。受影响的下游是亏损归因与日报/周报 ——
一笔实际净亏的单会被判成盈利，于是「该亏损车道」显示为盈利、亏损归因块永不触发。

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
    """统一已实现盈亏口径。position 可为 PaperPosition 或 dict。

    判定顺序（自上而下，取第一个可用口径）：

      1. **已平仓且 `unrealized_pnl` 非 0** → 用它。引擎平仓路径把「本笔最终合计」
         （final_pnl + partial 各腿之和）写进该列，是唯一完整口径。
      2. `partial_realized_pnl` 非 0 → 用它（分档止盈各腿之和）。
      3. `(close_price − entry_price) × 方向 × size` 复原（全平路径）。

    ## 为什么第 1 步要求「非 0」而不是「status 是 closed」

    全库 3520 笔已平仓里，**1271 笔 `unrealized_pnl` 为 0**，其中 1180 笔按平仓价差
    明明有盈亏（实测例：UNI long entry=4.0625 close=4.0246 size=10.93 → 约 −0.41）。
    这批主要是 `max_hold_timeout` 等旧路径，平仓时没有回写该列。
    若无条件用 `unrealized_pnl`，这 1180 笔会从「有盈亏」变成 0 —— 比原 bug 更糟。
    因此只在它确实有值时才采信，否则退回第 2/3 步（即原行为）。
    """
    def _g(name, default=None):
        if isinstance(position, dict):
            return position.get(name, default)
        return getattr(position, name, default)

    def _f(name):
        try:
            v = _g(name)
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    status = str(_g(name="status", default="") or "").strip().lower()
    upnl = _f("unrealized_pnl")
    if status in ("closed", "liquidated") and abs(upnl) > 1e-9:
        return upnl

    pr = _f("partial_realized_pnl")
    if abs(pr) > 1e-9:
        return pr

    entry = _f("entry_price")
    close = _f("close_price")
    size = _f("size")
    side = str(_g(name="side") or "").lower()
    sd = 1.0 if side == "long" else -1.0
    return (close - entry) * sd * size
