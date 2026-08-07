"""
GlobalCapitalCoordinator — V3 与 Rebate 共享的全局资金池

防止两套套利系统重复占用同一权益。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class GlobalCapitalCoordinator:
    """
    全局资金协调器

    - 初始化时从 arb_config + rebate_config 读取分配比例
    - V3 使用 funding_rate_arb + cross_exchange_spread 池
    - Rebate 使用 rebate_points_arb 池
    - emergency_reserve 不可分配
    """

    _instance: Optional["GlobalCapitalCoordinator"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._equity = 0.0
        self._allocations: Dict[str, float] = {}
        self._used: Dict[str, float] = {}
        self._state_lock = threading.Lock()
        self._load_config()

    def _load_config(self) -> None:
        self._ratios = {
            "funding_rate_arb": 0.10,
            "cross_exchange_spread": 0.20,
            "rebate_points_arb": 0.60,
            "emergency_reserve": 0.10,
        }
        self._v3_total_ratio = 0.30
        try:
            from backend.config.rebate_config_loader import rebate_config as rc
            if rc and rc.capital_allocation:
                cfg = rc.capital_allocation
                self._ratios = {
                    "funding_rate_arb": cfg.funding_rate_arb,
                    "cross_exchange_spread": cfg.cross_exchange_spread,
                    "rebate_points_arb": cfg.rebate_points_arb,
                    "emergency_reserve": cfg.emergency_reserve,
                }
        except Exception:
            pass
        try:
            from backend.config.arb_config_loader import arb_config
            self._v3_total_ratio = arb_config.capital_allocation.v3_arbitrage_total
        except Exception:
            pass

    @classmethod
    def get_instance(cls) -> "GlobalCapitalCoordinator":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def update_equity(self, total_equity: float) -> None:
        """权益变化时重建各池上限（保留已用金额）"""
        if total_equity <= 0:
            return
        with self._state_lock:
            old_used = dict(self._used)
            self._equity = total_equity
            self._allocations = {
                pool: total_equity * ratio
                for pool, ratio in self._ratios.items()
            }
            self._used = {pool: old_used.get(pool, 0.0) for pool in self._ratios}
        logger.debug(
            "[GlobalCapital] 权益=${:,.0f}, V3池=${:,.0f}, Rebate池=${:,.0f}".format(
                total_equity,
                self.get_v3_pool_available(),
                self.get_pool_available("rebate_points_arb"),
            )
        )

    def reset_pools(self, total_equity: float) -> None:
        """Paper 验证启动：清零各池占用，避免历史状态导致资金不足。"""
        if total_equity <= 0:
            return
        with self._state_lock:
            self._equity = total_equity
            self._allocations = {
                pool: total_equity * ratio
                for pool, ratio in self._ratios.items()
            }
            self._used = {pool: 0.0 for pool in self._ratios}
        logger.info(
            "[GlobalCapital] Paper 重置: 权益=${:,.0f}, Rebate池=${:,.0f}".format(
                total_equity,
                self.get_pool_available("rebate_points_arb"),
            )
        )

    def get_v3_pool_total(self) -> float:
        """V3 可用总池 = funding + cross 分配之和"""
        with self._state_lock:
            pools = ("funding_rate_arb", "cross_exchange_spread")
            return sum(
                self._allocations.get(p, 0) - self._used.get(p, 0)
                for p in pools
            )

    def get_v3_pool_available(self) -> float:
        return max(0.0, self.get_v3_pool_total())

    def get_pool_available(self, pool: str) -> float:
        with self._state_lock:
            allocated = self._allocations.get(pool, 0)
            used = self._used.get(pool, 0)
            return max(0.0, allocated - used)

    def request(self, pool: str, amount: float, strategy_id: str = "") -> Dict[str, Any]:
        with self._state_lock:
            available = self._allocations.get(pool, 0) - self._used.get(pool, 0)
            if amount > available:
                return {
                    "granted": False,
                    "amount": 0.0,
                    "remaining": max(0.0, available),
                    "pool": pool,
                }
            self._used[pool] = self._used.get(pool, 0) + amount
            remaining = self._allocations.get(pool, 0) - self._used[pool]
            logger.info(
                "[GlobalCapital] 分配 ${:,.0f} → {} ({}) 剩余=${:,.0f}".format(
                    amount, pool, strategy_id, remaining
                )
            )
            return {
                "granted": True,
                "amount": amount,
                "remaining": remaining,
                "pool": pool,
            }

    def release(self, pool: str, amount: float, strategy_id: str = "") -> None:
        with self._state_lock:
            used = self._used.get(pool, 0)
            self._used[pool] = max(0.0, used - amount)
            logger.debug(
                "[GlobalCapital] 释放 ${:,.0f} ← {} ({})".format(
                    min(amount, used), pool, strategy_id
                )
            )

    def pool_for_strategy(self, source: str) -> str:
        s = source.lower().replace("-", "_")
        if s in ("funding_rate", "funding_long", "funding_short") or s.startswith("funding"):
            return "funding_rate_arb"
        if s in (
            "cross_exchange", "cross_exchange_spread",
            "spot_perp_basis", "basis",
        ):
            return "cross_exchange_spread"
        return "funding_rate_arb"

    def get_status(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "total_equity": self._equity,
                "allocations": dict(self._allocations),
                "used": dict(self._used),
                "available": {
                    pool: max(0, self._allocations.get(pool, 0) - self._used.get(pool, 0))
                    for pool in self._ratios
                },
                "v3_available": self.get_v3_pool_available(),
            }


global_capital_coordinator = GlobalCapitalCoordinator.get_instance()
