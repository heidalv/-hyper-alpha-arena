"""
交易所费率配置

定义各交易所的 Maker/Taker 费率、提现费用、默认滑点估算。
用于套利系统的费用影响分析。

可通过 DB 配置覆盖默认值。
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from .unified_models import ExchangeFeeSchedule

logger = logging.getLogger(__name__)

# ── 默认费率配置 ──
# 可通过 update_fee_schedule() 覆盖

_DEFAULT_SCHEDULES: Dict[str, Dict] = {
    "hyperliquid": {
        "exchange_id": "hyperliquid",
        "maker_rate": 0.0002,      # 0.02%
        "taker_rate": 0.0005,      # 0.05%
        "withdrawal_fee_usd": 0.0, # L1 无提现费
        "slippage_bps_estimate": 3.0,
    },
    "binance": {
        "exchange_id": "binance",
        "maker_rate": 0.0010,      # 0.10%
        "taker_rate": 0.0010,      # 0.10%
        "withdrawal_fee_usd": 1.0, # 变动，取平均估算
        "slippage_bps_estimate": 5.0,
    },
    "bybit": {
        "exchange_id": "bybit",
        "maker_rate": 0.0010,      # 0.10%
        "taker_rate": 0.0010,      # 0.10%
        "withdrawal_fee_usd": 1.0,
        "slippage_bps_estimate": 5.0,
    },
    "okx": {
        "exchange_id": "okx",
        "maker_rate": 0.0008,      # 0.08%
        "taker_rate": 0.0010,      # 0.10%
        "withdrawal_fee_usd": 1.0,
        "slippage_bps_estimate": 5.0,
    },
    "gateio": {
        "exchange_id": "gateio",
        "maker_rate": 0.0015,      # 0.15%
        "taker_rate": 0.0015,      # 0.15%
        "withdrawal_fee_usd": 1.0,
        "slippage_bps_estimate": 8.0,
    },
    "asterdex": {
        "exchange_id": "asterdex",
        "maker_rate": 0.0005,      # 0.05%
        "taker_rate": 0.0005,      # 0.05%
        "withdrawal_fee_usd": 0.5,
        "slippage_bps_estimate": 5.0,
    },
}


class FeeScheduleRegistry:
    """费率配置注册表"""

    def __init__(self):
        self._schedules: Dict[str, ExchangeFeeSchedule] = {}
        self._load_defaults()

    def _load_defaults(self):
        for exchange_id, config in _DEFAULT_SCHEDULES.items():
            self._schedules[exchange_id] = ExchangeFeeSchedule(**config)

    def get(self, exchange_id: str) -> ExchangeFeeSchedule:
        exchange_id = exchange_id.lower()
        if exchange_id not in self._schedules:
            logger.warning(f"[FeeSchedule] 未知交易所 {exchange_id}，使用默认费率")
            return ExchangeFeeSchedule(
                exchange_id=exchange_id,
                maker_rate=0.0010,
                taker_rate=0.0010,
                slippage_bps_estimate=5.0,
            )
        return self._schedules[exchange_id]

    def update(self, exchange_id: str, schedule: ExchangeFeeSchedule):
        self._schedules[exchange_id.lower()] = schedule

    def list_exchanges(self) -> list:
        return list(self._schedules.keys())

    def cross_exchange_round_trip_cost(
        self,
        notional: float,
        exchange_a: str,
        exchange_b: str,
        include_transfer: bool = False,
    ) -> float:
        """
        计算跨交易所套利的往返交易成本

        Args:
            notional: 单腿名义价值
            exchange_a: 交易所A
            exchange_b: 交易所B
            include_transfer: 是否包含跨所转账费用

        Returns:
            总成本（入场+出场，双腿）
        """
        fee_a = self.get(exchange_a)
        fee_b = self.get(exchange_b)

        # 入场：双腿各付一次 taker
        entry_cost = fee_a.entry_cost(notional) + fee_b.entry_cost(notional)

        # 出场：双腿各付一次 taker
        exit_cost = fee_a.exit_cost(notional) + fee_b.exit_cost(notional)

        # 滑点：双腿各付一次
        slippage = (notional * fee_a.slippage_bps_estimate / 10000
                    + notional * fee_b.slippage_bps_estimate / 10000)

        # 跨所转账费用（如果需要）
        transfer = 0.0
        if include_transfer:
            transfer = fee_a.withdrawal_fee_usd + fee_b.withdrawal_fee_usd

        return entry_cost + exit_cost + slippage + transfer

    def single_exchange_round_trip_cost(
        self,
        notional: float,
        exchange: str,
    ) -> float:
        """计算单交易所套利的往返交易成本（资金费率套利等）"""
        fee = self.get(exchange)
        return fee.round_trip_cost(notional)


# ── 模块级单例 ──
fee_registry = FeeScheduleRegistry()
