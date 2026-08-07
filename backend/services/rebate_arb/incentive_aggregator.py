"""
IncentiveAggregator — 交易所激励数据中央聚合器

核心职责：
1. 并行从所有已启用交易所拉取激励数据（费率/积分/返利/活动）
2. 存储快照到数据库（时间序列）
3. 提供最新数据给 engine 和 API 层
4. 跟踪数据新鲜度，提供健康报告

Usage:
    from backend.services.rebate_arb.incentive_aggregator import incentive_aggregator
    data = await incentive_aggregator.fetch_all()
    latest = incentive_aggregator.get_latest()
    report = incentive_aggregator.get_freshness_report()
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from backend.services.exchange.base_exchange_client import (
    BaseExchangeClient,
    ExchangeIncentiveSummary,
)
from backend.services.rebate_arb.incentive_cache import incentive_cache

logger = logging.getLogger(__name__)


class IncentiveAggregator:
    """
    Collects real incentive data from all connected exchanges.
    Stores snapshots for historical tracking and provides fresh data to engine.
    """

    # Exchange names that should be aggregated
    SUPPORTED_EXCHANGES = ["asterdex", "binance", "okx", "bybit", "gateio", "hyperliquid"]

    def __init__(self):
        self._latest: Dict[str, ExchangeIncentiveSummary] = {}
        self._campaigns: Dict[str, List[Dict]] = {}
        self._last_fetch_times: Dict[str, float] = {}
        self._fetch_errors: Dict[str, str] = {}
        self._fetch_count = 0
        self._db_session_factory: Optional[Callable] = None
        self._exchange_getter: Optional[Callable] = None
        # [2026-07-06 病灶C] 无 adapter 时降级为 program_registry 离线数据源
        self._offline_mode: bool = False

    def configure(
        self,
        exchange_getter: Callable[[str], Optional[BaseExchangeClient]],
        db_session_factory: Optional[Callable] = None,
    ):
        """
        Configure the aggregator with exchange access and DB.

        Args:
            exchange_getter: Callable that takes exchange name -> adapter or None
            db_session_factory: Optional callable returning a DB session
        """
        self._exchange_getter = exchange_getter
        self._db_session_factory = db_session_factory
        logger.info("[IncentiveAggregator] Configured with %s exchanges", len(self.SUPPORTED_EXCHANGES))

    async def fetch_all(self) -> Dict[str, ExchangeIncentiveSummary]:
        """
        Parallel fetch incentive data from all enabled exchanges.
        Stores results in _latest and persists snapshots to DB.

        Returns:
            Dict mapping exchange name to its IncentiveSummary
        """
        if self._exchange_getter is None:
            logger.warning("[IncentiveAggregator] Not configured, returning empty")
            return {}

        self._fetch_count += 1
        tasks = []
        exchange_names = []

        # Load config
        try:
            from backend.config.rebate_config_loader import rebate_config
            config = rebate_config
        except Exception:
            config = None

        for name in self.SUPPORTED_EXCHANGES:
            # Check if exchange is enabled in config
            if config and name in config.exchanges:
                if not config.exchanges[name].enabled:
                    continue

            adapter = self._exchange_getter(name)
            if adapter is not None:
                tasks.append(self._fetch_single(name, adapter))
                exchange_names.append(name)

        if not tasks:
            # [2026-07-06 病灶C 修复] 本环境无交易所客户端/密钥（get_client 全返回 None）。
            # 不再只是 warning 后返回空、让下游在空数据上空转，而是明确降级为
            # program_registry 离线权威数据源。get_latest_as_dict 会据此填充费率/积分。
            self._offline_mode = True
            if self._fetch_count == 1 or self._fetch_count % 20 == 0:
                logger.info(
                    "[IncentiveAggregator] 无可用 adapter，降级为 program_registry 离线数据源"
                    "（费率/积分/程序状态来自离线注册表；实时抓取需交易所客户端）"
                )
            return self._latest

        # Parallel fetch with timeout per exchange
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for name, result in zip(exchange_names, results):
            if isinstance(result, Exception):
                self._fetch_errors[name] = str(result)
                logger.warning("[IncentiveAggregator] %s fetch failed: %s", name, result)
            elif result is not None:
                self._latest[name] = result
                self._last_fetch_times[name] = time.time()
                self._fetch_errors.pop(name, None)

        # Persist snapshots to DB (non-blocking)
        if self._db_session_factory:
            try:
                await self._store_snapshots()
            except Exception as e:
                logger.warning("[IncentiveAggregator] Snapshot storage failed: %s", e)

        success_count = sum(1 for n in exchange_names if n not in self._fetch_errors)
        logger.info(
            "[IncentiveAggregator] Fetch #%d: %d/%d exchanges OK",
            self._fetch_count, success_count, len(exchange_names)
        )

        return self._latest

    async def _fetch_single(
        self, name: str, adapter: BaseExchangeClient
    ) -> Optional[ExchangeIncentiveSummary]:
        """Fetch incentive data from a single exchange with timeout."""
        try:
            summary = await asyncio.wait_for(
                adapter.get_incentive_summary(),
                timeout=30.0,
            )
            # 同步拉取进行中的活动/竞赛（S4 数据管线，失败不影响主数据）
            try:
                campaigns = await asyncio.wait_for(
                    adapter.get_active_campaigns(),
                    timeout=15.0,
                )
                self._campaigns[name] = [
                    {**c, "exchange": c.get("exchange", name)}
                    for c in (campaigns or [])
                    if isinstance(c, dict)
                ]
            except Exception as ce:
                logger.debug("[IncentiveAggregator] %s campaigns fetch skip: %s", name, ce)
            return summary
        except asyncio.TimeoutError:
            raise Exception(f"Timeout fetching {name} incentive data")
        except Exception as e:
            raise Exception(f"{name}: {e}")

    async def _store_snapshots(self):
        """Persist latest data as time-series snapshots."""
        if not self._db_session_factory:
            return

        db = self._db_session_factory()
        try:
            from backend.database.models import RebateIncentiveSnapshotDB
            now = datetime.utcnow()

            for name, summary in self._latest.items():
                snapshot = RebateIncentiveSnapshotDB(
                    exchange=name,
                    snapshot_time=now,
                    fee_tier_name=summary.fee_tier.tier_name,
                    maker_rate=summary.fee_tier.maker_rate,
                    taker_rate=summary.fee_tier.taker_rate,
                    rebate_rate=summary.fee_tier.rebate_rate,
                    points_balance=summary.points.points_balance,
                    points_multiplier=summary.points.points_multiplier,
                    volume_30d=summary.fee_tier.volume_30d_usd,
                    data_json="{}",
                )
                db.add(snapshot)

            from backend.database.connection import sqlite_write_commit
            sqlite_write_commit(db, label="incentive_snapshots")
        except Exception as e:
            logger.warning("[IncentiveAggregator] DB snapshot error: %s", e)
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass

    def get_latest(self) -> Dict[str, ExchangeIncentiveSummary]:
        """Get cached latest data (no API call)."""
        return self._latest

    def get_latest_as_dict(self) -> Dict[str, Dict[str, Any]]:
        """
        Get latest data formatted for engine.scan_all_strategies(incentive_data=...).
        Transforms ExchangeIncentiveSummary into the dict format strategies expect.
        """
        result: Dict[str, Any] = {}

        for name, summary in self._latest.items():
            entry = {
                # 兼容两种命名：策略代码读 tier_name，旧消费方读 fee_tier
                "fee_tier": summary.fee_tier.tier_name,
                "tier_name": summary.fee_tier.tier_name,
                "maker_rate": summary.fee_tier.maker_rate,
                "taker_rate": summary.fee_tier.taker_rate,
                "rebate_rate": summary.fee_tier.rebate_rate,
                "volume_30d": summary.fee_tier.volume_30d_usd,
                "next_tier_volume": summary.fee_tier.next_tier_volume,
                "points_balance": summary.points.points_balance,
                "points_multiplier": summary.points.points_multiplier,
                "daily_points_rate": summary.points.daily_points_rate,
                "airdrop_eligible": summary.points.airdrop_eligible,
                "estimated_airdrop_value": summary.points.estimated_airdrop_value,
                "base_rebate_rate": summary.rebate.base_rebate_rate,
                "current_rebate_rate": summary.rebate.current_rebate_rate,
                "stacked_multiplier": summary.rebate.stacked_multiplier,
                "rh_points": summary.points.points_balance if name == "asterdex" else 0,
                "alpha_daily_rate": summary.points.daily_points_rate if name == "binance" else 0,
            }
            if name == "asterdex":
                # S1/S6 期望的扁平字段；适配器未提供时给策略默认值 0.1
                entry["rh_per_1k_usd"] = float(
                    getattr(summary.points, "rh_per_1k_usd", 0.0) or 0.1
                )
            if name == "binance":
                entry["alpha_points_balance"] = summary.points.points_balance
            result[name] = entry

        # [2026-07-06 病灶C 修复] 对没有实时数据的交易所，用 program_registry 离线兜底，
        # 保证引擎扫描不再跑在全空数据上。离线条目标 data_source="offline"。
        try:
            from backend.config.rebate_config_loader import rebate_config

            enabled_exchanges = [
                n for n in self.SUPPORTED_EXCHANGES
                if not (n in rebate_config.exchanges and not rebate_config.exchanges[n].enabled)
            ]
        except Exception:
            enabled_exchanges = list(self.SUPPORTED_EXCHANGES)

        for name in enabled_exchanges:
            if name not in result:
                result[name] = self._offline_entry(name)

        # 跨所活动聚合（S4 数据来源，由 _fetch_single 同步抓取）
        active_campaigns: List[Dict] = []
        for camps in self._campaigns.values():
            active_campaigns.extend(camps)
        result["active_campaigns"] = active_campaigns
        return result

    def _offline_entry(self, name: str) -> Dict[str, Any]:
        """无实时数据时，从 program_registry 构造某交易所的离线激励条目。

        提供 maker/taker/rebate 费率、该所 active 积分项目的状态与规则摘要，
        让下游策略在"离线但真实"的费率上评估，而不是在全零数据上空转。
        """
        try:
            from backend.services.rebate_arb import program_registry as pr

            fees = pr.get_offline_incentive(name)
            active = [p for p in pr.all_programs() if p.exchange == name and p.is_active()]
            prog = active[0] if active else pr.get_program_for_strategy(
                {"asterdex": "S8", "binance": "S7", "hyperliquid": "S3"}.get(name, "")
            )
        except Exception:
            fees = {"maker_rate": 0.0002, "taker_rate": 0.0005, "rebate_rate": 0.0}
            prog = None

        entry: Dict[str, Any] = {
            "fee_tier": "offline",
            "tier_name": "offline",
            "maker_rate": fees.get("maker_rate", 0.0002),
            "taker_rate": fees.get("taker_rate", 0.0005),
            "rebate_rate": fees.get("rebate_rate", 0.0),
            "volume_30d": 0.0,
            "next_tier_volume": 0.0,
            "points_balance": 0.0,
            "points_multiplier": 1.0,
            "daily_points_rate": 0.0,
            "airdrop_eligible": bool(prog and prog.is_active()),
            "estimated_airdrop_value": 0.0,
            "base_rebate_rate": fees.get("rebate_rate", 0.0),
            "current_rebate_rate": fees.get("rebate_rate", 0.0),
            "stacked_multiplier": 1.0,
            "rh_points": 0,
            "alpha_daily_rate": 0,
            # 离线元数据（供前端/诊断展示、供 program-active 自检交叉校验）
            "data_source": "offline",
            "program_id": prog.program_id if prog else None,
            "program_status": prog.status if prog else "unknown",
            "points_rule": prog.points_rule if prog else "",
        }
        if name == "asterdex":
            entry["rh_per_1k_usd"] = 0.1
        if name == "binance":
            entry["alpha_points_balance"] = 0.0
        return entry

    def get_active_programs(self) -> List[Dict[str, Any]]:
        """[2026-07-06 Phase1] 覆盖当前所有 active 积分项目（含超出 6 家 CEX 的新 DEX：
        Backpack/Paradex/Lighter/Pacifica/Extended 等），供前端/扫描器展示可刷项目。

        字段含 maker/taker 费率、积分规则摘要、程序状态、起止日。数据来自离线权威
        program_registry（本环境无法联网抓取，实时刷新为可选增强）。
        """
        try:
            from backend.services.rebate_arb import program_registry as pr

            return [p.to_dict() for p in pr.active_programs()]
        except Exception as exc:
            logger.debug("[IncentiveAggregator] active programs 读取失败: %s", exc)
            return []

    def get_funding_matrix(
        self,
        funding_rates: Dict[str, Dict[str, float]],
        *,
        horizon_days: float = 7.0,
        use_taker: bool = True,
        min_net_apr: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """[2026-07-06 Phase1] 多场所资金费率矩阵 → 最优 delta-neutral 多空腿组合。

        funding_rates 形如 {exchange: {symbol: funding_rate}}，可来自实时快照或
        Phase 4 历史回放。无数据时返回空列表（不再在空数据上空转）。
        """
        try:
            from backend.services.rebate_arb.funding_rate_matrix import scan_funding_matrix

            combos = scan_funding_matrix(
                funding_rates,
                horizon_days=horizon_days,
                use_taker=use_taker,
                min_net_apr=min_net_apr,
            )
            return [c.to_dict() for c in combos]
        except Exception as exc:
            logger.debug("[IncentiveAggregator] funding matrix 计算失败: %s", exc)
            return []

    def get_freshness_report(self) -> Dict[str, Dict[str, Any]]:
        """
        Per-exchange data freshness and health report.
        Used by API and frontend for status indicators.
        """
        now = time.time()
        report = {}

        for name in self.SUPPORTED_EXCHANGES:
            last_time = self._last_fetch_times.get(name, 0)
            age = now - last_time if last_time > 0 else -1
            error = self._fetch_errors.get(name)

            if age < 0:
                health = "unknown"
            elif age < 120:  # < 2 min
                health = "fresh"
            elif age < 600:  # < 10 min
                health = "stale"
            else:
                health = "expired"

            report[name] = {
                "last_update": last_time,
                "age_seconds": round(age, 1) if age >= 0 else None,
                "health": health,
                "error": error,
                "has_data": name in self._latest,
            }

        return report

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregator statistics."""
        return {
            "fetch_count": self._fetch_count,
            "exchanges_with_data": len(self._latest),
            "exchanges_with_errors": len(self._fetch_errors),
            "cache_stats": incentive_cache.get_stats(),
        }


# Singleton
incentive_aggregator = IncentiveAggregator()


INCENTIVE_FETCH_JOB_ID = "rebate_incentive_fetch"
INCENTIVE_FETCH_INTERVAL_SECONDS = 300


def _run_incentive_fetch() -> None:
    """定时任务体：同步桥接调用 fetch_all。"""
    try:
        from backend.services.arbitrage.async_bridge import run_async_safe

        run_async_safe(incentive_aggregator.fetch_all(), default={})
    except Exception as e:
        logger.warning("[IncentiveAggregator] scheduled fetch failed: %s", e)


def schedule_incentive_fetch_task(task_scheduler) -> None:
    """
    配置聚合器并注册定时拉取任务（修复旧版从未 configure / 从未调度的问题）。

    在 startup 时调用一次；没有它 _latest 永远为空，
    tick_context 只能走逐所 REST 降级路径且拿不到 active_campaigns。
    """
    try:
        from backend.database.connection import SessionLocal
        from backend.services.exchange.exchange_manager import get_exchange_manager

        mgr = get_exchange_manager()
        incentive_aggregator.configure(
            exchange_getter=lambda name: mgr.get_client(name),
            db_session_factory=SessionLocal,
        )
    except Exception as e:
        logger.warning("[IncentiveAggregator] configure failed: %s", e)
        return

    task_scheduler.add_interval_task(
        task_func=_run_incentive_fetch,
        interval_seconds=INCENTIVE_FETCH_INTERVAL_SECONDS,
        task_id=INCENTIVE_FETCH_JOB_ID,
    )
    # 启动后先拉一次，避免首个 tick 拿不到激励数据
    try:
        import threading

        threading.Thread(
            target=_run_incentive_fetch, daemon=True, name="incentive-first-fetch"
        ).start()
    except Exception:
        pass
    logger.info(
        "[IncentiveAggregator] scheduled every %ss", INCENTIVE_FETCH_INTERVAL_SECONDS
    )
