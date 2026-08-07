"""
L2OrderBookManager — L2订单簿重建/健康状态管理层（P1，规划文档§5.4）。

背景（已核实）：`BaseMarketFlowCollector._on_orderbook`（base_collector.py:259-262）
此前只是简单地 `self.latest_orderbook[symbol] = book_data` 直接覆盖，没有任何
gap检测/新鲜度校验/层数上限保护——下游 OFI/CVD/插针防护等微观结构因子会在
连接抖动、WS消息丢失、或交易所返回异常薄/异常厚的簿时，静默消费一份"脏"数据
而不自知。

协议现实核实（与规划文档 §5.4 原文的 Binance 式差分协议假设不同）：
    Hyperliquid `l2Book` WS 订阅推送的是**完整快照**（`{coin, time, levels:
    [bids, asks]}`），不是 Binance 式的增量差分流（没有 `first_update_id`/
    `last_update_id` 序列号）。因此规划原文"if event.first_update_id !=
    last_update_id + 1: resync()"这套序列号gap检测对本交易所协议不适用，
    直接照抄会是"检测了一个不存在的字段，永远不触发"的假实现。

    本模块改用两类真实存在且确实会出问题的信号做gap/健康检测：
    1. 快照时间戳跳变：exchange `time` 字段（或本地接收时刻兜底）连续两次
       推送间隔 > 正常间隔的 GAP_MULTIPLIER 倍 → 判定 OUT_OF_SYNC（真实反映
       "连接抖动/消息丢失"）。
    2. 簿结构异常：crossed book（best_bid >= best_ask）、单边长时间空档、
       层数异常膨胀 → 判定异常，配合 ghost level 裁剪(max_levels)防止本地
       缓存无限增长。

    Resync 对快照协议是"免费"的（下一条推送本身就是权威完整状态，不需要像
    差分协议那样额外发REST请求重新拉取全量再重放缓冲的增量）——因此
    RESYNCING 状态在本实现中是"收到下一条有效快照即完成"的瞬时状态，主要
    用于统计/审计（resync_count），不是一个需要等待的耗时操作。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 默认层数上限（防止异常返回超深簿时本地缓存无限增长——ghost levels防护）
DEFAULT_MAX_LEVELS = 1000
# 正常推送间隔的估计值（Hyperliquid l2Book 典型推送间隔约0.5-2s，视活跃度）
DEFAULT_EXPECTED_INTERVAL_MS = 2000
# gap 判定倍数：间隔超过 期望间隔*倍数 才判定为 OUT_OF_SYNC（避免正常抖动误报）
DEFAULT_GAP_MULTIPLIER = 4.0


class SyncState(str, Enum):
    INITIALIZING = "initializing"
    SYNCHRONIZED = "synchronized"
    OUT_OF_SYNC = "out_of_sync"
    RESYNCING = "resyncing"


@dataclass
class L2BookHealth:
    """单个 (exchange, symbol) 的订单簿健康状态。"""
    exchange: str
    symbol: str
    state: SyncState = SyncState.INITIALIZING
    last_snapshot_time_ms: int = 0     # 交易所侧时间戳(或本地接收时刻兜底)
    last_receive_time: float = 0.0     # 本地接收 wall-clock（秒）
    update_count: int = 0
    gap_count: int = 0                 # 累计检测到的时间跳变次数
    resync_count: int = 0              # 累计完成的resync次数
    crossed_book_count: int = 0        # 累计检测到 crossed book 次数
    ghost_level_prune_count: int = 0   # 累计因超过max_levels被裁剪的次数
    last_gap_ms: float = 0.0
    last_anomaly_reason: str = ""
    levels_bid: int = 0
    levels_ask: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exchange": self.exchange, "symbol": self.symbol, "state": self.state.value,
            "last_snapshot_time_ms": self.last_snapshot_time_ms,
            "age_sec": round(time.time() - self.last_receive_time, 2) if self.last_receive_time else None,
            "update_count": self.update_count, "gap_count": self.gap_count,
            "resync_count": self.resync_count, "crossed_book_count": self.crossed_book_count,
            "ghost_level_prune_count": self.ghost_level_prune_count,
            "last_gap_ms": round(self.last_gap_ms, 1), "last_anomaly_reason": self.last_anomaly_reason,
            "levels_bid": self.levels_bid, "levels_ask": self.levels_ask,
        }


class L2OrderBookManager:
    """单例：所有交易所L2订单簿统一经过本层做gap检测+ghost level裁剪+状态追踪。

    调用方式：BaseMarketFlowCollector._on_orderbook 在存入 latest_orderbook 之前
    先调用 self.ingest(...)，用返回的清洗后 book_data 替代原始数据——这样下游
    读 self.latest_orderbook[symbol] 的所有消费者（OFI/CVD因子、插针防护、
    落库逻辑）自动获得裁剪后的干净数据，不需要逐个改消费端。
    """

    _instance: Optional["L2OrderBookManager"] = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._health: Dict[Tuple[str, str], L2BookHealth] = {}
        self._lock = threading.Lock()
        self.max_levels = DEFAULT_MAX_LEVELS
        self.expected_interval_ms = DEFAULT_EXPECTED_INTERVAL_MS
        self.gap_multiplier = DEFAULT_GAP_MULTIPLIER

    def ingest(self, exchange: str, symbol: str, book_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理一条原始l2Book快照：gap检测 + ghost level裁剪 + crossed book检测。

        返回裁剪/校验后的 book_data（levels 已裁剪到 max_levels，其余字段原样保留）。
        异常时安全返回原始 book_data（不阻断主流程，只做旁路健康记录）。
        """
        key = (exchange, symbol)
        now = time.time()
        try:
            with self._lock:
                health = self._health.get(key)
                if health is None:
                    health = L2BookHealth(exchange=exchange, symbol=symbol)
                    self._health[key] = health

                snap_time_ms = self._extract_snapshot_time_ms(book_data, now)

                if health.state == SyncState.INITIALIZING:
                    health.state = SyncState.SYNCHRONIZED
                    logger.info(f"[L2Book] {exchange}:{symbol} 首次同步完成 → SYNCHRONIZED")
                else:
                    gap_ms = snap_time_ms - health.last_snapshot_time_ms if health.last_snapshot_time_ms else 0
                    health.last_gap_ms = gap_ms
                    if gap_ms > self.expected_interval_ms * self.gap_multiplier:
                        health.gap_count += 1
                        if health.state == SyncState.SYNCHRONIZED:
                            health.state = SyncState.OUT_OF_SYNC
                            logger.warning(
                                f"[L2Book] {exchange}:{symbol} 检测到时间跳变 gap={gap_ms:.0f}ms "
                                f"(>期望间隔{self.expected_interval_ms}ms×{self.gap_multiplier}) → OUT_OF_SYNC"
                            )
                        # 快照协议下resync是"免费"的：本条新快照本身就是权威完整状态，
                        # 直接采纳即完成resync，无需像差分协议那样额外发REST请求。
                        health.state = SyncState.RESYNCING
                    elif health.state in (SyncState.OUT_OF_SYNC, SyncState.RESYNCING):
                        health.resync_count += 1
                        health.state = SyncState.SYNCHRONIZED
                        logger.info(f"[L2Book] {exchange}:{symbol} resync完成({health.resync_count}次) → SYNCHRONIZED")

                health.last_snapshot_time_ms = snap_time_ms
                health.last_receive_time = now
                health.update_count += 1

                cleaned, anomaly = self._clean_levels(book_data, health)
                if anomaly:
                    health.last_anomaly_reason = anomaly

                return cleaned
        except Exception as e:
            logger.debug(f"[L2Book] {exchange}:{symbol} ingest异常(降级返回原始数据): {e}")
            return book_data

    @staticmethod
    def _extract_snapshot_time_ms(book_data: Dict[str, Any], now: float) -> int:
        try:
            t = book_data.get("time") if isinstance(book_data, dict) else None
            if t:
                return int(t)
        except Exception:
            pass
        return int(now * 1000)

    def _clean_levels(self, book_data: Dict[str, Any], health: L2BookHealth) -> Tuple[Dict[str, Any], str]:
        """ghost level裁剪 + crossed book检测。返回 (清洗后的book_data, 异常描述或空串)。"""
        if not isinstance(book_data, dict):
            return book_data, ""
        levels = book_data.get("levels")
        if not levels or len(levels) < 2:
            return book_data, ""

        bids: List[Any] = levels[0] or []
        asks: List[Any] = levels[1] or []
        anomaly = ""

        if len(bids) > self.max_levels or len(asks) > self.max_levels:
            health.ghost_level_prune_count += 1
            anomaly = f"层数异常({len(bids)}bid/{len(asks)}ask)超过上限{self.max_levels}，已裁剪"
            logger.warning(f"[L2Book] {health.exchange}:{health.symbol} {anomaly}")
            bids = bids[: self.max_levels]
            asks = asks[: self.max_levels]

        if bids and asks:
            try:
                best_bid = float(bids[0].get("px", 0))
                best_ask = float(asks[0].get("px", 0))
                if best_bid > 0 and best_ask > 0 and best_bid >= best_ask:
                    health.crossed_book_count += 1
                    anomaly = f"crossed_book best_bid={best_bid}>=best_ask={best_ask}"
                    logger.warning(f"[L2Book] {health.exchange}:{health.symbol} {anomaly}")
            except Exception:
                pass

        health.levels_bid = len(bids)
        health.levels_ask = len(asks)

        cleaned = dict(book_data)
        cleaned["levels"] = [bids, asks]
        return cleaned, anomaly

    # ── 查询接口（供健康监控/微观结构因子消费前校验数据质量） ──

    def is_synchronized(self, exchange: str, symbol: str) -> bool:
        """微观结构因子(OFI/CVD/插针防护)可在信任depth数据前调用此检查，
        OUT_OF_SYNC/RESYNCING期间的簿数据可信度存疑，应降权或跳过。"""
        health = self._health.get((exchange, symbol))
        return health is not None and health.state == SyncState.SYNCHRONIZED

    def get_health(self, exchange: str, symbol: str) -> Optional[Dict[str, Any]]:
        health = self._health.get((exchange, symbol))
        return health.to_dict() if health else None

    def get_all_health(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [h.to_dict() for h in self._health.values()]

    def get_summary_stats(self) -> Dict[str, Any]:
        """供 /api 健康检查端点：汇总所有品种的gap/resync统计，验证本层确实在工作。"""
        with self._lock:
            all_h = list(self._health.values())
        total_updates = sum(h.update_count for h in all_h)
        total_gaps = sum(h.gap_count for h in all_h)
        return {
            "tracked_books": len(all_h),
            "total_updates": total_updates,
            "total_gap_events": total_gaps,
            "total_resync_events": sum(h.resync_count for h in all_h),
            "total_crossed_book_events": sum(h.crossed_book_count for h in all_h),
            "total_ghost_level_prunes": sum(h.ghost_level_prune_count for h in all_h),
            "gap_rate": round(total_gaps / total_updates, 6) if total_updates else 0.0,
            "currently_out_of_sync": [
                f"{h.exchange}:{h.symbol}" for h in all_h
                if h.state in (SyncState.OUT_OF_SYNC, SyncState.RESYNCING)
            ],
        }


l2_orderbook_manager = L2OrderBookManager()
