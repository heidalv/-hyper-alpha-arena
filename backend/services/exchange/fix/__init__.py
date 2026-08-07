"""
FIX 4.4 骨架（P3.2c，方案 §P3.2c / §2.2.6，机构量级预留）。

目标：机构量级时上 Binance/OKX FIX 4.4（drop-copy + 序列号 gap 恢复）。
当前：接口骨架，默认禁用。启用由配置 + 影子轨道验证后（R3，非人工开关）。

FIX vs REST+WS（对标 §2.2.6）：
    - WS 主导行情；FIX 胜在标准化/drop-copy/序列号 gap 恢复/OMS 集成
    - 机构量级（大单/多账户）才需要，不阻塞 MVP
    - crypto 无 SBE/二进制 FIX（那是 TradFi）
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class FIXConfig:
    """FIX 4.4 连接配置。"""
    host: str = ""
    port: int = 0
    sender_comp_id: str = ""
    target_comp_id: str = ""
    password: str = ""
    heartbeat_interval: int = 30
    enabled: bool = False  # 默认禁用，机构量级才启用


class FIXClient:
    """
    FIX 4.4 客户端骨架。

    生产：基于 simplefix 库实现 logon/new/order/cancel。
    当前：接口对齐，实际 IO 待接入。

    序列号 gap 恢复：FIX MsgSeqNum 跳变 → 发 ResendRequest(35=2)。
    """

    def __init__(self, config: FIXConfig | None = None):
        self.config = config or FIXConfig()
        self._connected = False
        self._last_seq_recv = 0
        self._last_seq_sent = 0

    def connect(self) -> bool:
        """建立 FIX 会话（logon）。"""
        if not self.config.enabled:
            return False
        # TODO: simplefix 实现 logon(35=A)
        self._connected = True
        return True

    def send_order(self, symbol: str, side: str, qty: float,
                   price: Optional[float] = None, order_type: str = "2") -> dict:
        """
        下单（NewOrderSingle 35=D）。
        order_type: 1=Market, 2=Limit, 默认 Limit。
        """
        if not self._connected:
            return {"status": "REJECTED", "reason": "not connected"}
        self._last_seq_sent += 1
        # TODO: 构造 FIX 消息发送
        return {
            "status": "SENT",
            "seq": self._last_seq_sent,
            "symbol": symbol, "side": side, "qty": qty,
            "price": price, "order_type": order_type,
        }

    def cancel_order(self, orig_clord_id: str) -> dict:
        """撤单（OrderCancelRequest 35=F）。"""
        if not self._connected:
            return {"status": "REJECTED", "reason": "not connected"}
        return {"status": "CANCEL_SENT", "orig_clord_id": orig_clord_id}

    def check_seq_gap(self, incoming_seq: int) -> bool:
        """
        序列号 gap 检测。
        返回 True 表示有缺口（需 ResendRequest）。
        """
        if incoming_seq != self._last_seq_recv + 1:
            return True
        self._last_seq_recv = incoming_seq
        return False

    def disconnect(self) -> None:
        self._connected = False


def is_fix_enabled() -> bool:
    """FIX 是否启用（默认 false，机构量级才开）。"""
    return os.environ.get("FIX_ENABLED", "false").lower() in ("1", "true", "yes")
