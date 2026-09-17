"""[轮48 2026-09-17] 周期语义单一真源 —— 「日内(intraday)」与「长期趋势(trend)」。

## 为什么需要这个模块

用户口径（2026-09-17）：
> 现在是两个周期进行，实际上就是不能叫中期和长期，实际上是**日内**和**长期趋势**两个周期。

实测支持该判断（`paper_positions` mid+long 全样本，n=347）：
    mid  车道 n=272  中位持仓 3.2h，94% < 24h，>7d 0 笔   → 日内
    long 车道 n= 75  中位持仓 12.8h，68% < 24h，>7d 0 笔  → 也不是长期
    唯一真长周期 = trend_e1（设计 168h，实际中位 23.3h，唯一大额正收益源 +51.86）

而代码里的历史命名是**二元且自相矛盾**的：
    - `scalp`   ≈ 日内（1m/3m/5m/15m/30m/1h）——**15m 曾被错误归入"短线"**
    - `midlong` ≈ 长期趋势（4h/1d）
    - 同一个 15m 任务：`main.py` 里叫 `factor_evolution_mid_15m_daily_v7`（"**中**周期"），
      但 `_tag_one_short_horizon` 给它打的是 `horizon=scalp` + `s5m_` 前缀（"**短**周期"）
    - `factor_discovery` 里 `horizon="midlong"` 的 interval 是 **4h**，
      而 `main.py` 的 `run_mid_factor_evolution_loop` 是 **15m** —— 同名不同义

后果（实测）：15m 因子被打 `horizon=scalp` 标签 → 进 `scalp_active_factor_set`
（判据 `horizon != "midlong"`）→ 该池所属车道已判死 → **15m 因子永远无法进入交易**。
因子库现状（730 个）：active 12 个全在 4h/1d；15m 20 个、1h 24 个，**0 candidate / 0 active**。

## 本模块的定位

**只提供语义映射，不改写存量数据、不改变默认行为。**
存量记录里的 `horizon=scalp` / `horizon=midlong` 继续按原样读；
本模块提供 `normalize_horizon()` 做等价换算，以及 `INTRADAY` 这个**新标签**的唯一定义处。

新标签的启用由 `FACTOR_EVO_INTRADAY_HORIZON_ENABLED` 控制（默认 false = 完全现状）。
"""

from __future__ import annotations

import os
from typing import Optional

# ── 规范周期名（唯一真源）──
INTRADAY = "intraday"   # 日内：分钟级到小时级，前瞻 1.5–4h
TREND = "trend"         # 长期趋势：4h 到月线，前瞻 24h–3d

# ── 周期 → 周期档 ──
# 依据 factor_evolution_loop._PERIOD_FWD_BARS 的实际前瞻时长分档：
#   15m→6根=1.5h、30m→4根=2h、1h→2根=2h、2h→1根=2h   ⇒ 日内
#   4h→6根=24h、8h→6根=48h、1d→3根=3d                ⇒ 长期趋势
INTRADAY_PERIODS: frozenset[str] = frozenset({"1m", "3m", "5m", "15m", "30m", "1h", "2h"})
TREND_PERIODS: frozenset[str] = frozenset({"4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"})

# ── 存量标签 → 规范档（只读兼容，不改写数据）──
_LEGACY_TO_CANONICAL: dict[str, str] = {
    "scalp": INTRADAY,
    "short": INTRADAY,
    "intraday": INTRADAY,
    "midlong": TREND,
    "long": TREND,
    "trend": TREND,
}
# 注意：**故意不把 "mid" 映射进来**。"mid" 在代码里有两种互斥含义
# （factor_discovery=4h / main.py V7=15m），映射它只会把歧义固化。
# 需要判档时请用 period_to_cycle(period)，不要用标签字符串。

# 启用开关：默认空串 = 完全维持现状（15m 仍按 scalp 打标）。
#
# 采用**周期列表**而非布尔开关，原因：中线车道实际消费的周期是
# `midlong_helpers.py:1815` 的 ("15m","1h","4h","1d") —— 只有 15m/1h 该迁到日内档；
# 若用布尔开关，1m/3m/5m 会一并被移出短线池，而中线**不消费**它们，等于制造新的孤儿。
# 列表式与既有 `FACTOR_EVO_SCALP_PERIODS` 模式一致。
_ENV_PERIODS = "FACTOR_EVO_INTRADAY_PERIODS"

# 中线车道实际消费的周期（迁档的候选上限）
MID_LANE_CONSUMED_PERIODS: frozenset[str] = frozenset({"15m", "1h"})


def intraday_periods() -> frozenset[str]:
    """返回被迁到「日内」档的周期集合（默认空 = 不迁，维持现状）。

    只接受 INTRADAY_PERIODS 内的值；非法/越界项被忽略（避免把 4h 误迁进日内档）。
    """
    raw = str(os.getenv(_ENV_PERIODS, "") or "")
    out = set()
    for item in raw.split(","):
        p = item.strip()
        if p and p in INTRADAY_PERIODS:
            out.add(p)
    return frozenset(out)


def intraday_horizon_enabled() -> bool:
    """是否启用「日内」档标签（默认 false = 现状）。"""
    return bool(intraday_periods())


def is_intraday_tagged_period(period: object) -> bool:
    """该周期本次是否应打 horizon=intraday（而非历史上的 scalp）。"""
    return str(period or "").strip() in intraday_periods()


def normalize_horizon(raw: object) -> Optional[str]:
    """把任意历史/新增 horizon 值归一到规范档；无法识别返回 None。

    只做换算，不声称存量数据已被改写。
    """
    key = str(raw or "").strip().lower()
    if not key:
        return None
    return _LEGACY_TO_CANONICAL.get(key)


def period_to_cycle(period: object) -> Optional[str]:
    """K 线周期 → 规范周期档（INTRADAY / TREND）；未知周期返回 None。"""
    p = str(period or "").strip()
    if not p:
        return None
    # 归一大小写：1M(月) 与 1m(分) 必须区分，故先精确匹配再退化
    if p in TREND_PERIODS:
        return TREND
    if p in INTRADAY_PERIODS:
        return INTRADAY
    low = p.lower()
    if low in TREND_PERIODS:
        return TREND
    if low in INTRADAY_PERIODS:
        return INTRADAY
    return None


def is_intraday_period(period: object) -> bool:
    return period_to_cycle(period) == INTRADAY


def is_trend_period(period: object) -> bool:
    return period_to_cycle(period) == TREND


def horizon_tag_for_period(period: object, *, intraday_enabled: bool | None = None) -> Optional[str]:
    """返回该周期在**新打标**时应写的 horizon 值。

    - 迁档列表为空（默认）：沿用历史行为 —— 日内侧写 "scalp"；
    - 该周期在迁档列表内：写 "intraday"。
    """
    if intraday_enabled is None:
        intraday_enabled = is_intraday_tagged_period(period)
    cyc = period_to_cycle(period)
    if cyc == INTRADAY:
        return INTRADAY if intraday_enabled else "scalp"
    if cyc == TREND:
        return "midlong"
    return None


__all__ = [
    "INTRADAY", "TREND",
    "INTRADAY_PERIODS", "TREND_PERIODS", "MID_LANE_CONSUMED_PERIODS",
    "intraday_periods", "intraday_horizon_enabled", "is_intraday_tagged_period",
    "normalize_horizon", "period_to_cycle",
    "is_intraday_period", "is_trend_period", "horizon_tag_for_period",
]
