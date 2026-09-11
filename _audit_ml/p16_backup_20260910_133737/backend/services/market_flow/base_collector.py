"""
BaseMarketFlowCollector — 市场流采集器抽象基类

将原 MarketFlowCollector（单所硬绑 Hyperliquid、60s 聚合）抽象为：
- 统一的 start/stop/subscribe/health/status 接口
- 可配置聚合窗口（解决"非实时"痛点，从 60s 降到默认 15s）
- 可复用的 TradeBuffer + 批量并发 flush
- 断线 replay 补数（子类实现 fetch_recent_trades 时启用）

子类只需实现：
    exchange_id       — 交易所标识（如 "hyperliquid" / "asterdex"）
    _run_source_loop()— 启动该交易所的行情源（WS/REST），将 ExchangeTrade 喂给 _on_trade()
    （可选）_flush_orderbook / _flush_asset_metrics / fetch_recent_trades
"""

from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from services.exchange.base_exchange_client import ExchangeTrade

logger = logging.getLogger(__name__)


def _book_depth_notional(book_data: Any, levels: int = 5):
    """从订单簿快照计算前 N 档名义深度 (bid, ask)。

    兼容两种既有格式：
    - HL l2Book: {"levels": [[bids], [asks]]}，元素 {"px","sz"}
    - asterdex:  {"bids": [{"px","sz"}...], "asks": [...]}
    任一单边无有效档位返回 (None, None)。
    """
    if not isinstance(book_data, dict):
        return None, None
    if book_data.get("levels"):
        try:
            bids_raw, asks_raw = book_data["levels"][0] or [], book_data["levels"][1] or []
        except (TypeError, IndexError, ValueError):
            return None, None
    elif book_data.get("bids") or book_data.get("asks"):
        bids_raw, asks_raw = book_data.get("bids"), book_data.get("asks")
    else:
        return None, None

    def _notional(rows) -> float:
        total = 0.0
        for r in (rows or [])[:levels]:
            try:
                if isinstance(r, dict):
                    total += float(r["px"]) * float(r["sz"])
                else:
                    total += float(r[0]) * float(r[1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        return total

    bid_d, ask_d = _notional(bids_raw), _notional(asks_raw)
    if bid_d <= 0 or ask_d <= 0:
        return None, None
    return bid_d, ask_d


@dataclass
class TradeBuffer:
    """单 symbol 在一个聚合窗口内的成交聚合"""
    taker_buy_volume: Decimal = Decimal("0")
    taker_sell_volume: Decimal = Decimal("0")
    taker_buy_count: int = 0
    taker_sell_count: int = 0
    taker_buy_notional: Decimal = Decimal("0")
    taker_sell_notional: Decimal = Decimal("0")
    high_price: Optional[Decimal] = None
    low_price: Optional[Decimal] = None
    total_volume: Decimal = Decimal("0")
    total_notional: Decimal = Decimal("0")

    def reset(self) -> None:
        self.taker_buy_volume = Decimal("0")
        self.taker_sell_volume = Decimal("0")
        self.taker_buy_count = 0
        self.taker_sell_count = 0
        self.taker_buy_notional = Decimal("0")
        self.taker_sell_notional = Decimal("0")
        self.high_price = None
        self.low_price = None
        self.total_volume = Decimal("0")
        self.total_notional = Decimal("0")


class BaseMarketFlowCollector(ABC):
    """
    所有交易所市场流采集器的抽象基类。

    每个 collector 实例绑定一个交易所，各自独立线程/协程运行，
    多实例通过 MarketFlowCollectorRegistry 并行，共享 market 表（按 exchange 字段隔离）。
    """

    # 默认聚合窗口（秒）。子类可覆盖；registry 启动时可被配置覆盖。
    DEFAULT_AGGREGATION_WINDOW_SECONDS: int = 15

    def __init__(
        self,
        aggregation_window_seconds: Optional[int] = None,
        on_symbol_subscribed: Optional[Callable[[str], None]] = None,
    ):
        self.running: bool = False
        self.subscribed_symbols: List[str] = []
        self._pending_symbols: List[str] = []

        # 可配置聚合窗口
        self.aggregation_window_seconds: int = (
            aggregation_window_seconds
            if aggregation_window_seconds is not None
            else self.DEFAULT_AGGREGATION_WINDOW_SECONDS
        )

        # 数据缓冲（按 symbol）
        self.trade_buffers: Dict[str, TradeBuffer] = {}
        self.latest_orderbook: Dict[str, Any] = {}
        self.latest_asset_ctx: Dict[str, Any] = {}

        # 数据新鲜度（按数据源类别记录最近更新时间）
        self.last_update_time: Dict[str, float] = {
            "l2book": 0.0,
            "asset_ctx": 0.0,
            "trades": 0.0,
        }

        # 定时器
        self.last_flush_time: float = time.time()
        self.flush_timer: Optional[threading.Timer] = None

        # 线程安全
        self.buffer_lock = threading.Lock()
        self._source_thread: Optional[threading.Thread] = None
        self._source_started = threading.Event()
        self._on_symbol_subscribed = on_symbol_subscribed

        logger.info(
            "[%s] MarketFlowCollector 初始化 (window=%ss)",
            self.exchange_id, self.aggregation_window_seconds,
        )

    # ── 子类必须实现的接口 ──

    @property
    @abstractmethod
    def exchange_id(self) -> str:
        """交易所标识，如 'hyperliquid' / 'asterdex'（用于 DB exchange 字段隔离）"""
        ...

    @abstractmethod
    def _run_source_loop(self, symbols: List[str]) -> None:
        """
        启动该交易所的行情源（WS 订阅 / REST 轮询），在独立线程中阻塞运行。

        实现要求：
        - 收到成交时调 self._on_trade(ExchangeTrade(...))
        - 收到订单簿时调 self._on_orderbook(symbol, book_data)
        - 收到资产上下文(OI/funding)时调 self._on_asset_ctx(symbol, ctx_data)
        - self.running 变 False 时应优雅退出循环
        """
        ...

    # ── 公共生命周期 ──

    def start(self, symbols: Optional[List[str]] = None) -> bool:
        """启动采集器。symbols 为空时返回 False。幂等（已运行则返回 True）。"""
        if self.running:
            logger.warning("[%s] collector 已在运行", self.exchange_id)
            return True

        if symbols is not None:
            self._pending_symbols = list(symbols)

        if not self._pending_symbols:
            logger.warning("[%s] 无 symbols，采集器未启动", self.exchange_id)
            return False

        self.running = True
        self._source_started.clear()
        self.subscribed_symbols = []
        self.trade_buffers = {}

        # 启动行情源线程
        symbols_to_run = list(self._pending_symbols)
        self._source_thread = threading.Thread(
            target=self._source_thread_main,
            args=(symbols_to_run,),
            name=f"market-flow-{self.exchange_id}",
            daemon=True,
        )
        self._source_thread.start()

        # 启动 flush 定时器
        self._schedule_flush()

        logger.info(
            "[%s] 采集器已启动，监控 symbols=%s",
            self.exchange_id, symbols_to_run,
        )
        return True

    def stop(self) -> None:
        """停止采集器并清理资源。幂等。"""
        if not self.running:
            return

        self.running = False

        # 取消 flush 定时器
        if self.flush_timer:
            self.flush_timer.cancel()
            self.flush_timer = None

        # flush 残留数据
        try:
            self._flush_to_database()
        except Exception as e:
            logger.warning("[%s] stop 时 flush 失败: %s", self.exchange_id, e)

        # 等待行情源线程退出（最多 3s）
        if self._source_thread and self._source_thread.is_alive():
            self._source_thread.join(timeout=3.0)

        logger.info("[%s] 采集器已停止", self.exchange_id)

    def refresh_subscriptions(self, new_symbols: List[str]) -> None:
        """
        更新订阅的 symbol 集合。默认实现是"全量重启"，
        子类如支持增量订阅（如 HL 原生 unsubscribe/subscribe）可覆盖以减少抖动。
        """
        if set(new_symbols) == set(self.subscribed_symbols):
            return
        if not self.running:
            return
        logger.info(
            "[%s] refresh_subscriptions: %s → %s（全量重启源）",
            self.exchange_id, self.subscribed_symbols, new_symbols,
        )
        self.stop()
        self.start(new_symbols)

    # ── 子类把行情数据喂进来 ──

    def _on_trade(self, trade: ExchangeTrade) -> None:
        """子类收到逐笔成交时调用，累加到对应 symbol 的 buffer。"""
        self.last_update_time["trades"] = time.time()
        symbol = trade.symbol
        price = Decimal(str(trade.price))
        size = Decimal(str(trade.size))
        notional = price * size

        with self.buffer_lock:
            buffer = self.trade_buffers.get(symbol)
            if buffer is None:
                buffer = TradeBuffer()
                self.trade_buffers[symbol] = buffer

            if trade.is_taker_buy:
                buffer.taker_buy_volume += size
                buffer.taker_buy_count += 1
                buffer.taker_buy_notional += notional
            else:
                buffer.taker_sell_volume += size
                buffer.taker_sell_count += 1
                buffer.taker_sell_notional += notional

            buffer.total_volume += size
            buffer.total_notional += notional

            if buffer.high_price is None or price > buffer.high_price:
                buffer.high_price = price
            if buffer.low_price is None or price < buffer.low_price:
                buffer.low_price = price

        # 旁路：推送到事件总线（兼容旧 MarketDataHub 行为）
        try:
            from services.market_data_hub import market_data_hub
            market_data_hub.publish_trade(
                self.exchange_id, symbol,
                {"px": trade.price, "sz": trade.size, "side": "B" if trade.is_taker_buy else "A",
                 "time": trade.timestamp},
                source="ws",
            )
        except Exception:
            pass

    def _on_orderbook(self, symbol: str, book_data: Any) -> None:
        """子类收到订单簿时调用。

        [2026-07-18 新增，规划文档§5.4] 此前直接覆盖存入，无gap检测/层数
        上限保护——L2OrderBookManager 在存入前做统一的时间跳变检测+ghost
        level裁剪+crossed book检测，所有子类自动获得清洗后的数据，无需
        逐个交易所改消费端。ingest失败时内部已降级返回原始数据，不阻断主流程。
        """
        self.last_update_time["l2book"] = time.time()
        try:
            from services.market_flow.l2_orderbook_manager import l2_orderbook_manager
            if isinstance(book_data, dict) and "levels" in book_data:
                book_data = l2_orderbook_manager.ingest(self.exchange_id, symbol, book_data)
        except Exception as e:
            logger.debug("[%s] L2OrderBookManager ingest跳过: %s", getattr(self, "exchange_id", ""), e)
        self.latest_orderbook[symbol] = book_data
        # [v6-S2-1] L2 重建层接线：把清洗后的快照喂给默认重建器（跳变防护 + 深度派生）， # flush 时从重建器取末帧计算前5档名义深度落库（见 _current_depth_notional）。 # 兼容两种既有快照格式：HL levels（{"levels": [[bids],[asks]]}）与 # asterdex 的 bids/asks 数组（[{"px","sz"}, ...]）。
        try:
            if isinstance(book_data, dict):
                from services.market_flow.l2_reconstructor import default_reconstructor
                if book_data.get("levels"):
                    default_reconstructor.ingest_hl(
                        self.exchange_id, symbol,
                        [book_data["levels"][0] or [], book_data["levels"][1] or []],
                    )
                elif book_data.get("bids") or book_data.get("asks"):
                    def _pairs(rows: Any) -> list:
                        out = []
                        for r in rows or []:
                            try:
                                if isinstance(r, dict):
                                    out.append((float(r["px"]), float(r["sz"])))
                                else:
                                    out.append((float(r[0]), float(r[1])))
                            except (TypeError, ValueError, IndexError, KeyError):
                                continue
                        return out
                    default_reconstructor.ingest_book(
                        self.exchange_id, symbol,
                        _pairs(book_data.get("bids")), _pairs(book_data.get("asks")),
                    )
        except Exception as e:
            logger.warning("[%s] L2Reconstructor ingest跳过: %s", getattr(self, "exchange_id", ""), e)

    def _on_asset_ctx(self, symbol: str, ctx_data: Any) -> None:
        """子类收到资产上下文（OI/funding）时调用。"""
        self.last_update_time["asset_ctx"] = time.time()
        self.latest_asset_ctx[symbol] = ctx_data

    # ── 内部：行情源线程主函数 ──

    def _source_thread_main(self, symbols: List[str]) -> None:
        try:
            # 子类实现应在连接成功后置位 self._source_started
            self._run_source_loop(symbols)
        except Exception as e:
            logger.error(
                "[%s] 行情源线程异常退出: %s", self.exchange_id, e, exc_info=True,
            )
        finally:
            self._source_started.set()  # 确保不卡住 start 调用方

    # ── flush 机制（可复用）──

    def _schedule_flush(self) -> None:
        if not self.running:
            return
        self.flush_timer = threading.Timer(
            self.aggregation_window_seconds, self._flush_and_reschedule,
        )
        self.flush_timer.daemon = True
        self.flush_timer.start()

    def _flush_and_reschedule(self) -> None:
        if not self.running:
            return
        try:
            self._flush_to_database()
        except Exception as e:
            logger.error("[%s] flush 异常: %s", self.exchange_id, e, exc_info=True)
        self._schedule_flush()

    def _flush_to_database(self) -> None:
        """
        将所有 symbol 的缓冲数据 flush 到数据库。
        与旧实现一致：每个 symbol 一个短事务。
        """
        symbols = list(self.subscribed_symbols)
        if not symbols:
            # 也处理已收到数据但尚未登记到 subscribed_symbols 的情况
            with self.buffer_lock:
                symbols = list(self.trade_buffers.keys())
        if not symbols:
            return

        # 对齐到聚合窗口边界的时间戳
        window_ms = self.aggregation_window_seconds * 1000
        timestamp_ms = int(time.time() * 1000)
        timestamp_ms = (timestamp_ms // window_ms) * window_ms

        flushed = 0
        from backend.database.connection import MarketSessionLocal
        for symbol in symbols:
            db = MarketSessionLocal()
            try:
                self._flush_trades(db, symbol, timestamp_ms)
                self._flush_orderbook(db, symbol, timestamp_ms)
                self._flush_asset_metrics(db, symbol, timestamp_ms)
                db.commit()
                flushed += 1
            except Exception as e:
                db.rollback()
                _err_s = str(e)
                if "UniqueViolation" in _err_s or "duplicate key" in _err_s.lower():
                    logger.debug("[%s] flush %s duplicate ignored", self.exchange_id, symbol)
                else:
                    logger.error("[%s] flush %s 失败: %s", self.exchange_id, symbol, e)
            finally:
                db.close()

        if flushed > 0:
            logger.debug(
                "[%s] flushed %d/%d symbols", self.exchange_id, flushed, len(symbols),
            )
            self._run_signal_detection()

    def _current_depth_notional(self, symbol: str) -> tuple:
        """[v6-S2-1] 取当前末帧前5档名义深度 (bid, ask)。

        主来源：实例属性 self.latest_orderbook —— _on_orderbook 与 flush 位于同一
        采集器实例内共享，天然规避"模块双加载导致重建器单例不一致"（2026-08-06
        实测 services.* 与 backend.services.* 可被加载成两个不同模块实例）；
        L2 重建器仅作辅助来源。无订单簿帧或单边为空时返回 (None, None)。
        """
        try:
            bid_d, ask_d = _book_depth_notional(self.latest_orderbook.get(symbol))
            if bid_d is not None:
                return bid_d, ask_d
        except Exception as e:
            logger.warning("[%s] 深度列读取(实例订单簿)失败: %s", getattr(self, "exchange_id", ""), e)
        try:
            from services.market_flow.l2_reconstructor import default_reconstructor
            frame = default_reconstructor.latest(self.exchange_id, symbol)
            if frame is None or not frame.bids or not frame.asks:
                return None, None
            bid_d, ask_d = frame.notional_depth(5)
            return float(bid_d), float(ask_d)
        except Exception as e:
            logger.warning("[%s] 深度列读取(重建器)失败: %s", getattr(self, "exchange_id", ""), e)
            return None, None

    def _flush_trades(self, db, symbol: str, timestamp_ms: int) -> None:
        """将 symbol 的 trade buffer 落库（upsert）。可被子类覆盖以适配 schema。"""
        from backend.database.models import MarketTradesAggregated

        # [v6-S2-1] 当前桶末帧前5档名义深度（无帧时 None，列保持 NULL）
        bid_depth_top5, ask_depth_top5 = self._current_depth_notional(symbol)

        with self.buffer_lock:
            buffer = self.trade_buffers.get(symbol)
            if not buffer or buffer.total_volume == 0:
                return

            vwap = None
            if buffer.total_volume > 0:
                vwap = buffer.total_notional / buffer.total_volume

            existing = db.query(MarketTradesAggregated).filter(
                MarketTradesAggregated.exchange == self.exchange_id,
                MarketTradesAggregated.symbol == symbol,
                MarketTradesAggregated.timestamp == timestamp_ms,
            ).first()

            if existing:
                existing.taker_buy_volume = buffer.taker_buy_volume
                existing.taker_sell_volume = buffer.taker_sell_volume
                existing.taker_buy_count = buffer.taker_buy_count
                existing.taker_sell_count = buffer.taker_sell_count
                existing.taker_buy_notional = buffer.taker_buy_notional
                existing.taker_sell_notional = buffer.taker_sell_notional
                existing.vwap = vwap
                existing.high_price = buffer.high_price
                existing.low_price = buffer.low_price
                existing.bid_depth_top5 = bid_depth_top5
                existing.ask_depth_top5 = ask_depth_top5
            else:
                db.add(MarketTradesAggregated(
                    exchange=self.exchange_id,
                    symbol=symbol,
                    timestamp=timestamp_ms,
                    taker_buy_volume=buffer.taker_buy_volume,
                    taker_sell_volume=buffer.taker_sell_volume,
                    taker_buy_count=buffer.taker_buy_count,
                    taker_sell_count=buffer.taker_sell_count,
                    taker_buy_notional=buffer.taker_buy_notional,
                    taker_sell_notional=buffer.taker_sell_notional,
                    vwap=vwap,
                    high_price=buffer.high_price,
                    low_price=buffer.low_price,
                    bid_depth_top5=bid_depth_top5,
                    ask_depth_top5=ask_depth_top5,
                ))

            buffer.reset()

    def _flush_orderbook(self, db, symbol: str, timestamp_ms: int) -> None:
        """
        落库订单簿快照。默认无操作（需要订单簿的子类覆盖）。
        HL 实现会覆盖此方法（它有原生 L2 数据）。
        """
        return

    def _flush_asset_metrics(self, db, symbol: str, timestamp_ms: int) -> None:
        """
        落库资产指标（OI/funding）。默认无操作（需要 asset_ctx 的子类覆盖）。
        """
        return

    def _run_signal_detection(self) -> None:
        """flush 后触发信号检测（与旧实现一致）。"""
        try:
            from services.signal_detection_service import signal_detection_service
            for symbol in self.subscribed_symbols:
                market_data = {
                    "asset_ctx": self.latest_asset_ctx.get(symbol, {}),
                    "orderbook": self.latest_orderbook.get(symbol, {}),
                }
                triggered = signal_detection_service.detect_signals(symbol, market_data)
                if triggered:
                    logger.info(
                        "[%s] %s 触发信号池: %s",
                        self.exchange_id, symbol,
                        [p["pool_name"] for p in triggered],
                    )
        except Exception as e:
            logger.error("[%s] 信号检测异常: %s", self.exchange_id, e, exc_info=True)

    # ── 健康检查 / 状态 ──

    def is_healthy(self) -> bool:
        """数据是否新鲜（30s 内有过更新）。"""
        now = time.time()
        for key in ("l2book", "asset_ctx", "trades"):
            t = self.last_update_time.get(key, 0.0)
            if t > 0 and (now - t) <= 30:
                return True
        return False

    def get_status(self) -> Dict[str, Any]:
        return {
            "exchange": self.exchange_id,
            "running": self.running,
            "symbols": list(self.subscribed_symbols),
            "buffer_count": len(self.trade_buffers),
            "aggregation_window_s": self.aggregation_window_seconds,
            "healthy": self.is_healthy(),
            "last_update": dict(self.last_update_time),
        }
