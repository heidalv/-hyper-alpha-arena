"""
DEX 连接器接口 + MEV 防护（P3.2b，方案 §P3.2b / §5.5）。

目标：扩展 DEX 执行（dYdX/Drift/GMX）+ 链上腿走 Flashbots Protect 防 MEV/夹击。

当前：接口预留（生产接 RPC + 合约 ABI）。
连接器复用现有 ccxt_base_adapter 接口风格；L2 重建器（P2.6）扩展覆盖 DEX 盘口。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


class DEXConnector(Protocol):
    """DEX 连接器接口（与 CEX adapter 同风格）。"""

    def place_order(self, symbol: str, side: str, qty: float,
                    price: Optional[float] = None) -> dict:
        """下单（返回 venue 响应）。"""
        ...

    def cancel_order(self, order_id: str) -> bool:
        ...

    def get_position(self, symbol: str) -> Optional[dict]:
        ...


@dataclass
class FlashbotsConfig:
    """Flashbots Protect RPC 配置（防 MEV/夹击）。"""
    protect_rpc_url: str = "https://rpc.flashbots.net"
    enabled: bool = True
    max_block_number: Optional[int] = None  # 截止区块


class MEVProtector:
    """
    MEV 防护：链上交易走 Flashbots Protect RPC，绕过公共 mempool。

    公共 mempool 暴露 → 可被三明治攻击。Flashbots Protect 私有提交。
    """

    def __init__(self, config: FlashbotsConfig | None = None):
        self.config = config or FlashbotsConfig()

    def should_protect(self, tx_value_usd: float, *, threshold_usd: float = 1e4) -> bool:
        """大额交易强制走 Protect（小交易可忽略）。"""
        return self.config.enabled and tx_value_usd >= threshold_usd

    def protected_rpc(self) -> str:
        """返回 Protect RPC URL（替代公共 RPC）。"""
        return self.config.protect_rpc_url if self.config.enabled else ""


# 连接器注册（生产实现待 RPC/ABI 接入）
_REGISTRY: dict[str, type] = {}


def register_dex(name: str, connector_cls: type) -> None:
    _REGISTRY[name] = connector_cls


def get_dex_connector(name: str) -> Optional[type]:
    return _REGISTRY.get(name)


# 占位连接器（接口对齐，实际逻辑待接入）
class DriftConnector:
    """Drift Protocol 连接器（Solana）。待接入。"""
    venue = "drift"

    def place_order(self, symbol, side, qty, price=None):
        raise NotImplementedError("Drift 连接器待 RPC 接入")

    def cancel_order(self, order_id):
        raise NotImplementedError

    def get_position(self, symbol):
        raise NotImplementedError


class GMXConnector:
    """GMX 连接器（Arbitrum）。待接入。"""
    venue = "gmx"

    def place_order(self, symbol, side, qty, price=None):
        raise NotImplementedError("GMX 连接器待合约 ABI 接入")

    def cancel_order(self, order_id):
        raise NotImplementedError

    def get_position(self, symbol):
        raise NotImplementedError


register_dex("drift", DriftConnector)
register_dex("gmx", GMXConnector)
