"""
K线 WebSocket 发布器 - 基于现有 ws_broadcast 基础设施

通过 ws_broadcast_hub 将实时 K 线数据推送给订阅了 TOPIC_KLINES 的前端客户端。
客户端通过 WebSocket 发送 subscribe_klines / unsubscribe_klines 来管理订阅。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 引用级联关系的周期映射
PERIOD_CASCADE: Dict[str, List[str]] = {
    "1m": ["1m"],
    "3m": ["3m"],
    "5m": ["5m"],
    "15m": ["5m", "15m"],
    "30m": ["5m", "15m", "30m"],
    "1h":  ["5m", "15m", "30m", "1h"],
    "2h":  ["5m", "15m", "30m", "1h", "2h"],
    "4h":  ["5m", "15m", "30m", "1h", "2h", "4h"],
    "8h":  ["5m", "15m", "30m", "1h", "2h", "4h", "8h"],
    "12h": ["5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h"],
    "1d":  ["5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d"],
    "3d":  ["5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d"],
    "1w":  ["5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w"],
    "1M":  ["5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w", "1M"],
}


async def publish_kline_update(
    symbol: str,
    period: str,
    bar: Dict[str, Any],
    indicators: Dict[str, Any] = None,
) -> None:
    """推送单根 K 线更新给订阅此 symbol+period 的前端。

    Args:
        symbol: 交易对（如 "BTC"）
        period: K 线周期（如 "1m", "1h"）
        bar: K 线数据 {"open": float, "high": float, "low": float, "close": float, "volume": float, "timestamp": int}
        indicators: 可选的已计算指标数据
    """
    from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_KLINES

    payload = {
        "symbol": symbol.upper(),
        "period": period,
        "bar": bar,
    }
    if indicators:
        payload["indicators"] = indicators

    ws_broadcast_hub.broadcast(
        TOPIC_KLINES,
        "kline_update",
        payload,
        throttle=True,
        throttle_key=(symbol.upper(), period),
    )


async def publish_resonance_update(symbol: str, resonance_data: Dict[str, Any]) -> None:
    """推送多周期共振分析结果。"""
    from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_KLINES

    ws_broadcast_hub.broadcast(
        TOPIC_KLINES,
        "resonance_update",
        {"symbol": symbol.upper(), "resonance": resonance_data},
        throttle=True,
        throttle_key=("resonance", symbol.upper()),
    )


def broadcast_after_collection(
    symbol: str,
    period: str,
    bar: Dict[str, Any],
    affected_periods: List[str] = None,
) -> None:
    """K 线实时采集后的统一广播入口。

    采集 1m 数据后，也会通知订阅了 5m/15m/1h... 等受影响周期的客户端，
    让它们知道可能需要刷新（因为长周期 K 线由短周期组合而成）。
    """
    if affected_periods is None:
        affected_periods = PERIOD_CASCADE.get(period, [period])

    from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_KLINES

    for p in affected_periods:
        payload = {
            "symbol": symbol.upper(),
            "period": p,
            "bar": bar,
        }
        # 只在原始周期推送完整 bar；关联周期仅推送刷新通知
        if p != period:
            payload["refresh_only"] = True

        ws_broadcast_hub.broadcast(
            TOPIC_KLINES,
            "kline_update",
            payload,
            throttle=True,
            throttle_key=(symbol.upper(), p),
        )
