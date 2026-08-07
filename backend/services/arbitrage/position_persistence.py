"""
V3 套利仓位 DB 持久化

Orchestrator 内存仓位与 ArbitragePosition 表同步。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from .unified_models import ArbHedgePosition, ArbitrageStatus, ExecutionMode, StrategyType

logger = logging.getLogger(__name__)


def _strategy_to_db(strategy: StrategyType) -> str:
    mapping = {
        StrategyType.FUNDING_RATE: "funding_rate",
        StrategyType.CROSS_EXCHANGE_SPREAD: "cross_exchange",
        StrategyType.SPOT_PERP_BASIS: "basis",
    }
    return mapping.get(strategy, strategy.value if hasattr(strategy, "value") else str(strategy))


def _mode_to_db(mode: ExecutionMode) -> str:
    return mode.value if hasattr(mode, "value") else str(mode)


def save_position_open(
    pos: ArbHedgePosition,
    size_usd: float,
    mode: ExecutionMode,
) -> None:
    """开仓写入 DB"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ArbitragePosition

        db = SessionLocal()
        try:
            strategy = _strategy_to_db(pos.strategy)
            if strategy == "funding_rate":
                direction = "funding_short" if pos.short_size > 0 and pos.long_size == 0 else "funding_long"
                if pos.long_size > 0 and pos.short_size > 0:
                    direction = "funding_hedged"
                strategy = direction if direction.startswith("funding") else strategy

            row = ArbitragePosition(
                position_id=pos.position_id,
                symbol=pos.symbol,
                strategy=strategy,
                long_size=Decimal(str(pos.long_size)) if pos.long_size else None,
                long_entry_price=Decimal(str(pos.long_entry_price)) if pos.long_entry_price else None,
                short_size=Decimal(str(pos.short_size)) if pos.short_size else None,
                short_entry_price=Decimal(str(pos.short_entry_price)) if pos.short_entry_price else None,
                delta=Decimal(str(pos.delta)),
                accumulated_funding=Decimal("0"),
                status="active",
                entry_time=datetime.fromtimestamp(pos.entry_time, tz=timezone.utc),
                exchange_long=pos.exchange_long or None,
                exchange_short=pos.exchange_short or None,
                entry_z_score=Decimal(str(pos.entry_z_score)) if pos.entry_z_score else None,
                entry_spread_pct=Decimal(str(pos.entry_spread_pct)) if pos.entry_spread_pct else None,
                entry_basis_pct=Decimal(str(pos.entry_basis_pct)) if pos.entry_basis_pct else None,
                entry_edge=Decimal(str(pos.entry_edge)) if pos.entry_edge else None,
                mode=_mode_to_db(mode),
                size_usd=Decimal(str(size_usd)),
            )
            db.add(row)
            db.commit()
            logger.info("[ArbPersist] 开仓写入 DB: %s", pos.position_id)
        except Exception as e:
            db.rollback()
            logger.error("[ArbPersist] 开仓写入失败 %s: %s", pos.position_id, e)
        finally:
            db.close()
    except Exception as e:
        logger.error("[ArbPersist] DB 不可用: %s", e)


def save_position_close(
    position_id: str,
    reason: str,
    pnl: float = 0.0,
    accumulated_funding: float = 0.0,
) -> None:
    """平仓更新 DB"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ArbitragePosition

        db = SessionLocal()
        try:
            row = db.query(ArbitragePosition).filter(
                ArbitragePosition.position_id == position_id
            ).first()
            if row is None:
                logger.debug("[ArbPersist] 平仓未找到 DB 记录: %s", position_id)
                return
            row.status = "closed"
            row.close_time = datetime.now(timezone.utc)
            row.close_reason = reason
            row.pnl = Decimal(str(pnl))
            row.accumulated_funding = Decimal(str(accumulated_funding))
            db.commit()
            logger.info("[ArbPersist] 平仓更新 DB: %s", position_id)
        except Exception as e:
            db.rollback()
            logger.error("[ArbPersist] 平仓更新失败 %s: %s", position_id, e)
        finally:
            db.close()
    except Exception as e:
        logger.error("[ArbPersist] DB 不可用: %s", e)


def load_active_positions() -> Dict[str, ArbHedgePosition]:
    """启动时从 DB 恢复活跃仓位到内存"""
    result: Dict[str, ArbHedgePosition] = {}
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ArbitragePosition

        db = SessionLocal()
        try:
            rows = db.query(ArbitragePosition).filter(
                ArbitragePosition.status == "active"
            ).all()
            for row in rows:
                strategy_raw = row.strategy or "funding_rate"
                if strategy_raw.startswith("funding"):
                    st = StrategyType.FUNDING_RATE
                elif strategy_raw == "cross_exchange":
                    st = StrategyType.CROSS_EXCHANGE_SPREAD
                elif strategy_raw == "basis":
                    st = StrategyType.SPOT_PERP_BASIS
                else:
                    st = StrategyType.FUNDING_RATE

                entry_ts = row.entry_time.timestamp() if row.entry_time else 0
                pos = ArbHedgePosition(
                    position_id=row.position_id,
                    symbol=row.symbol,
                    strategy=st,
                    long_size=float(row.long_size or 0),
                    long_entry_price=float(row.long_entry_price or 0),
                    short_size=float(row.short_size or 0),
                    short_entry_price=float(row.short_entry_price or 0),
                    delta=float(row.delta or 0),
                    entry_time=entry_ts,
                    status=ArbitrageStatus.ACTIVE,
                    exchange_long=row.exchange_long or "",
                    exchange_short=row.exchange_short or "",
                    entry_z_score=float(row.entry_z_score or 0),
                    entry_spread_pct=float(row.entry_spread_pct or 0),
                    entry_basis_pct=float(row.entry_basis_pct or 0),
                )
                result[row.position_id] = pos
            if result:
                logger.info("[ArbPersist] 从 DB 恢复 %d 个活跃仓位", len(result))
        finally:
            db.close()
    except Exception as e:
        logger.debug("[ArbPersist] 恢复仓位失败: %s", e)
    return result
