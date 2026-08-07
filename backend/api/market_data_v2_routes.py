"""Market data v2 shadow ingest API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.services.market_data_adapters.registry import exchange_adapter_registry
from backend.services.market_data_ingest_queue import IngestTask, market_data_ingest_queue
from backend.services.raw_market_event_store import raw_market_event_store
from backend.services.market_data_shadow_compare import market_data_shadow_compare
from backend.services.market_data_v2_scheduler import market_data_v2_scheduler
from backend.services.exchange_data_profile import exchange_data_profile_service
from backend.services.snapshot_producer import snapshot_producer
from backend.services.snapshot_reader import snapshot_reader
from backend.services.snapshot_scheduler import snapshot_scheduler
from backend.services.qaa_snapshot_bridge import qaa_snapshot_bridge
from backend.services.symbol_registry import symbol_registry
from backend.services.kline_quality_repair_service import kline_quality_repair_service
from backend.services.market_data_db_optimizer import market_data_db_optimizer

router = APIRouter(prefix="/api/market-data-v2", tags=["market_data_v2"])


class ShadowIngestRequest(BaseModel):
    exchange: str = "hyperliquid"
    symbol: str = "BTC"
    data_type: str = "kline"
    market_type: str = "perp"
    timeframe: str = "1m"
    limit: int = 100


class SnapshotCaptureRequest(BaseModel):
    symbols: list[str] | None = None
    periods: list[str] | None = None
    exchange: str | None = None
    count: int = 50
    force: bool = False


class KlineQualityRepairRequest(BaseModel):
    exchange: str | None = None
    exchanges: list[str] | None = None
    symbols: list[str] | None = None
    periods: list[str] | None = None
    limit: int | None = None
    apply: bool = False


@router.get("/status")
def get_market_data_v2_status() -> dict[str, Any]:
    """Return v2 adapter and ingest queue status."""
    return {
        "ingest_queue": market_data_ingest_queue.status(),
        "adapters": {
            key: value.__dict__
            for key, value in exchange_adapter_registry.status().items()
        },
        "registered_exchanges": exchange_adapter_registry.registered_exchanges(),
        "scheduler": market_data_v2_scheduler.status(),
        "snapshot_scheduler": snapshot_scheduler.status(),
        "qaa_snapshot_bridge": qaa_snapshot_bridge.status(),
        "kline_quality_repair": kline_quality_repair_service.status(),
        "db_optimizer": market_data_db_optimizer.status(),
    }


@router.get("/symbols/{exchange}")
def get_exchange_symbols(exchange: str, market_type: str = "perp") -> dict[str, Any]:
    mappings = symbol_registry.list_exchange(exchange, market_type=market_type)
    return {
        "exchange": exchange,
        "market_type": market_type,
        "symbols": [mapping.__dict__ for mapping in mappings],
    }


@router.get("/raw-summary")
def get_raw_event_summary(limit: int = 20) -> dict[str, Any]:
    return raw_market_event_store.summary(limit=limit)


@router.get("/exchange-profiles")
def get_exchange_data_profiles() -> dict[str, Any]:
    return exchange_data_profile_service.get_profiles()


@router.get("/shadow-compare")
def compare_shadow_klines(
    exchange: str = "hyperliquid",
    symbol: str = "BTC",
    timeframe: str = "1m",
    limit: int = 200,
    settle_periods: int = 3,
    settle_seconds: int = 4500,
) -> dict[str, Any]:
    return market_data_shadow_compare.compare_klines(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        settle_periods=settle_periods,
        settle_seconds=settle_seconds,
    )


@router.post("/shadow-ingest")
async def submit_shadow_ingest(request: ShadowIngestRequest) -> dict[str, Any]:
    task = IngestTask(
        exchange=request.exchange,
        symbol=request.symbol,
        data_type=request.data_type,
        market_type=request.market_type,
        timeframe=request.timeframe,
        limit=max(1, min(request.limit, 500)),
    )
    task = await market_data_ingest_queue.submit(task)
    return {
        "task_id": task.task_id,
        "status": task.status,
        "error": task.error,
        "enabled": market_data_ingest_queue.enabled(),
    }


@router.post("/scheduler/start")
async def start_v2_scheduler() -> dict[str, Any]:
    return market_data_v2_scheduler.start()


@router.post("/scheduler/stop")
async def stop_v2_scheduler() -> dict[str, Any]:
    return await market_data_v2_scheduler.stop()


@router.post("/scheduler/tick-once")
async def tick_v2_scheduler_once() -> dict[str, Any]:
    return await market_data_v2_scheduler.tick_once()


@router.get("/snapshot/status")
def get_snapshot_status() -> dict[str, Any]:
    return snapshot_reader.status()


@router.get("/snapshot/latest")
def get_latest_snapshot(max_age: float = 120) -> dict[str, Any]:
    snapshot = snapshot_reader.get_snapshot(max_age=max_age)
    return {"snapshot": snapshot, "status": snapshot_reader.status()}


@router.post("/snapshot/capture")
def capture_snapshot(request: SnapshotCaptureRequest) -> dict[str, Any]:
    return snapshot_producer.capture(
        symbols=request.symbols,
        periods=request.periods,
        exchange=request.exchange,
        count=request.count,
        force=request.force,
    )


@router.post("/snapshot/tick-once")
async def tick_snapshot_once(force: bool = False) -> dict[str, Any]:
    return await snapshot_scheduler.tick_once(force=force)


@router.post("/snapshot/start")
async def start_snapshot_scheduler() -> dict[str, Any]:
    return snapshot_scheduler.start()


@router.post("/snapshot/stop")
async def stop_snapshot_scheduler() -> dict[str, Any]:
    return await snapshot_scheduler.stop()


@router.get("/kline-quality/status")
def get_kline_quality_repair_status() -> dict[str, Any]:
    return kline_quality_repair_service.status()


@router.post("/kline-quality/tick-once")
async def tick_kline_quality_repair_once(request: KlineQualityRepairRequest) -> dict[str, Any]:
    return await kline_quality_repair_service.tick_once(
        apply=request.apply,
        exchange=request.exchange,
        exchanges=",".join(request.exchanges) if request.exchanges else None,
        symbols=",".join(request.symbols) if request.symbols else None,
        periods=",".join(request.periods) if request.periods else None,
        limit=request.limit,
    )


@router.post("/kline-quality/start")
async def start_kline_quality_repair() -> dict[str, Any]:
    return kline_quality_repair_service.start()


@router.post("/kline-quality/stop")
async def stop_kline_quality_repair() -> dict[str, Any]:
    return await kline_quality_repair_service.stop()


@router.get("/db/status")
def get_market_data_db_status() -> dict[str, Any]:
    return market_data_db_optimizer.status()


@router.post("/db/ensure-indexes")
def ensure_market_data_db_indexes() -> dict[str, Any]:
    return market_data_db_optimizer.ensure_indexes()


@router.post("/db/optimize")
def optimize_market_data_db() -> dict[str, Any]:
    return market_data_db_optimizer.optimize()
