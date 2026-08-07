"""
动态 Universe 选择（P2.9，方案 §P2.9/§5.1）。

目标：按 ADV/流动性/容量动态选品。新品种自动进影子轨道（不直接 ACTIVE）。
分层：主力（BTC/ETH perp）→ 次主力 → TOP20-50 长尾 → 现货/期权/稳定币对。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UniverseTier(str, Enum):
    CORE = "core"           # 主力：BTC/ETH perp
    SECONDARY = "secondary" # 次主力：SOL/BNB 等
    LONG_TAIL = "long_tail" # TOP20-50
    SPECIAL = "special"     # 现货/期权/稳定币对


@dataclass
class UniverseEntry:
    symbol: str
    venue: str
    tier: UniverseTier
    adv_usd: float = 0.0
    capacity_usd: float = 0.0
    shadow: bool = False    # 新品种先影子


@dataclass
class UniverseConfig:
    """universe 分层配置。"""
    min_adv_core: float = 1e9        # 主力最小 ADV
    min_adv_secondary: float = 2e8   # 次主力
    min_adv_long_tail: float = 5e7   # 长尾
    max_universe_size: int = 50


class UniverseAgent:
    """
    动态 Universe 选择。

    输入：候选品种的 ADV 数据。
    输出：分层 universe，新品种标 shadow（进影子，不直接 ACTIVE）。
    """

    def __init__(self, config: UniverseConfig | None = None):
        self.config = config or UniverseConfig()
        self._current: dict[str, UniverseEntry] = {}

    def select(self, candidates: list[dict]) -> list[UniverseEntry]:
        """
        从候选（含 symbol/venue/adv_usd）选出 universe。

        candidates: [{"symbol": "BTC-PERP", "venue": "binance", "adv_usd": 5e9}, ...]
        """
        cfg = self.config
        selected: list[UniverseEntry] = []
        for c in candidates:
            sym = c.get("symbol", "")
            venue = c.get("venue", "unknown")
            adv = c.get("adv_usd", 0.0)
            if adv >= cfg.min_adv_core:
                tier = UniverseTier.CORE
            elif adv >= cfg.min_adv_secondary:
                tier = UniverseTier.SECONDARY
            elif adv >= cfg.min_adv_long_tail:
                tier = UniverseTier.LONG_TAIL
            else:
                continue  # 流动性不足，不入选
            is_new = sym not in self._current
            entry = UniverseEntry(symbol=sym, venue=venue, tier=tier,
                                  adv_usd=adv, shadow=is_new)
            selected.append(entry)
            self._current[sym] = entry

        # 限制总量，按 tier 优先级 + ADV 降序
        tier_order = {UniverseTier.CORE: 0, UniverseTier.SECONDARY: 1,
                      UniverseTier.LONG_TAIL: 2, UniverseTier.SPECIAL: 3}
        selected.sort(key=lambda e: (tier_order[e.tier], -e.adv_usd))
        return selected[: cfg.max_universe_size]

    def promote_from_shadow(self, symbol: str) -> bool:
        """影子品种满足条件后晋升（解 shadow）。"""
        if symbol in self._current and self._current[symbol].shadow:
            self._current[symbol].shadow = False
            return True
        return False

    def current(self) -> list[UniverseEntry]:
        return list(self._current.values())
