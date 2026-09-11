# -*- coding: utf-8 -*-
"""E5 事件驱动策略族（影子车道）。

  e5_2_funding_shock   资金费 Δ 超 3σ + OI 同向增 → 拥挤反转（4h）
  e5_3_liq_cascade     滚动清算额破 P99 + 方向单边 → 反向均值回归（2h）
  e5_5_news_hedge      高影响新闻 → 避险窗口 + 短期方向信号

三者共用 `base.EventShadowStrategy`：同一份 `detect()` 同时驱动 backtest / shadow / live，
KPI 口径统一（N、命中率 Wilson CI、平均超额 bp 及 95% CI、净期望下界、是否过晋升门）。
"""
from backend.services.strategies.event.base import (  # noqa: F401
    COST_BP,
    PROMOTION_MIN_N,
    EventShadowStrategy,
    EventSignal,
    get_strategy,
    register_strategy,
    registered_strategies,
)


def _register_all() -> None:
    """导入即注册三个策略（幂等）。"""
    import logging

    log = logging.getLogger(__name__)
    for mod in ("e5_2_funding_shock", "e5_3_liquidation_cascade", "e5_5_news_hedge"):
        try:
            __import__(f"backend.services.strategies.event.{mod}")
        except Exception as exc:  # pragma: no cover
            log.warning("[strategies.event] %s 注册失败: %s", mod, exc)


_register_all()
