"""
契约桥接层（P2.1）。

将新契约 dataclass（contracts.types）与现有 UnifiedDataPool 的旧类型互转，
使热路径可渐进迁移——新代码用契约，旧代码经桥接消费，不一次性破坏存量。

迁移完成后（P2 全部完成），桥接可删除。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services.contracts.types import (
    DataQuality,
    Instrument,
)
from backend.services.contracts.types import (
    MarketSnapshot as ContractSnapshot,
)

if TYPE_CHECKING:
    # 避免循环导入：仅类型检查时引用
    from backend.services.unified_data_pool import (
        MarketSnapshot as LegacySnapshot,
    )


def legacy_to_contract(
    legacy: "LegacySnapshot",
    venue: str = "unknown",
    *,
    ts_ns: int = 0,
    bid: float = 0.0,
    ask: float = 0.0,
) -> ContractSnapshot:
    """旧 MarketSnapshot → 新契约 MarketSnapshot（补齐缺的字段）。"""
    inst = Instrument(
        symbol=legacy.symbol,
        venue=venue,
        kind="perp",
        tick_size=0.0,
        lot_size=0.0,
        adv_usd=0.0,
    )
    mid = legacy.price if legacy.price else (bid + ask) / 2 if (bid and ask) else 0.0
    ts = ts_ns if ts_ns else int(getattr(legacy, "timestamp", 0) * 1e9)
    return ContractSnapshot(
        ts_ns=ts,
        instrument=inst,
        bid=bid,
        ask=ask,
        mid=mid,
        last_trade=legacy.price,
        last_trade_size=0.0,
        funding_rate=legacy.funding_rate,
        open_interest=legacy.open_interest,
        quality=DataQuality.OK,
    )


def contract_to_legacy(
    snap: ContractSnapshot,
) -> "LegacySnapshot":
    """新契约 MarketSnapshot → 旧 MarketSnapshot（兼容旧消费方）。"""
    from backend.services.unified_data_pool import MarketSnapshot as LegacySnapshot
    return LegacySnapshot(
        symbol=snap.instrument.symbol,
        price=snap.mid,
        funding_rate=snap.funding_rate or 0.0,
        open_interest=snap.open_interest or 0.0,
        timestamp=snap.ts_ns / 1e9,
    )


def make_instrument(symbol: str, venue: str, kind: str = "perp", **kw) -> Instrument:
    """便捷构造 Instrument。"""
    return Instrument(symbol=symbol, venue=venue, kind=kind, **kw)
