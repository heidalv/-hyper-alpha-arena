"""持仓追踪服务 - Position Tracker Service (合约交易增强版)

核心职责：开仓后持续监控，杠杆感知风控，极端行情滚仓，最大程度保护本金。

风控分层：
Layer 1 - 基础止损：ATR / 固定百分比止损，杠杆越高止损越紧
Layer 2 - 移动止损：盈利后启动追踪，锁定利润
Layer 3 - 杠杆安全网：实时计算距爆仓距离，临近时强制减仓
Layer 4 - 趋势反转检测：EMA 交叉预警
Layer 5 - 极端行情滚仓：顺势加仓，用浮盈做保证金
Layer 6 - 紧急熔断：单笔亏损达杠杆安全线 → 立即全平
"""

import logging
import threading
import time
import math
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict

from sqlalchemy.orm import Session
from backend.database.connection import SessionLocal
from backend.database.models import StrategyTrade, AIStrategy

logger = logging.getLogger(__name__)

MONITOR_INTERVAL_SECONDS = 10


@dataclass
class TrackedPosition:
    """被追踪的持仓"""
    strategy_id: str
    trade_id: Optional[int] = None
    symbol: str = ""
    side: str = ""  # buy / sell
    entry_price: float = 0.0
    current_price: float = 0.0
    quantity: float = 0.0
    leverage: float = 1.0

    # 止盈止损
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    original_sl: float = 0.0
    original_tp: float = 0.0

    # 移动止损
    trailing_active: bool = False
    trailing_activation_pct: float = 0.02
    trailing_distance_pct: float = 0.01
    highest_price: float = 0.0
    lowest_price: float = 999999.0

    # 杠杆风控
    liquidation_price: float = 0.0     # 预估爆仓价
    margin_usage_pct: float = 0.0      # 保证金使用率
    distance_to_liq_pct: float = 1.0   # 距爆仓距离百分比
    leverage_safe: bool = True         # 杠杆是否安全

    # 滚仓 (snowball)
    snowball_enabled: bool = False
    snowball_max_adds: int = 3
    snowball_profit_threshold: float = 0.05
    snowball_add_ratio: float = 0.3
    snowball_adds_done: int = 0        # 已追加次数
    snowball_total_added: float = 0.0  # 已追加总量
    snowball_last_add_price: float = 0.0

    # 分批止盈
    tp_levels: List[Dict] = field(default_factory=list)
    tp_levels_hit: List[bool] = field(default_factory=list)

    # 状态
    pnl_pct: float = 0.0              # 价格百分比变动
    pnl_leveraged_pct: float = 0.0    # 杠杆放大后的收益率
    opened_at: str = ""
    last_check_at: str = ""
    health_score: float = 1.0
    alerts: List[str] = field(default_factory=list)


class PositionTrackerService:
    """持仓追踪服务（单例）- 合约交易增强版"""

    _instance: Optional["PositionTrackerService"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._tracked: Dict[str, TrackedPosition] = {}
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
        logger.info("[PositionTracker] 持仓追踪服务已初始化 (合约增强版)")

    def start(self):
        if self._running:
            return
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("[PositionTracker] 监控循环已启动")

    def stop(self):
        self._running = False
        logger.info("[PositionTracker] 监控循环已停止")

    def start_tracking(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float = 0.0,
        leverage: float = 1.0,
        stop_loss_price: float = 0.0,
        take_profit_price: float = 0.0,
        trailing_activation_pct: float = 0.02,
        trailing_distance_pct: float = 0.01,
        trade_id: int = None,
        tp_levels: List[Dict] = None,
        snowball_enabled: bool = False,
        snowball_max_adds: int = 3,
        snowball_profit_threshold: float = 0.05,
        snowball_add_ratio: float = 0.3,
    ):
        """注册新持仓到追踪"""
        key = f"{strategy_id}:{symbol}"

        # 计算预估爆仓价
        liq_price = self._calc_liquidation_price(entry_price, side, leverage)

        pos = TrackedPosition(
            strategy_id=strategy_id,
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            current_price=entry_price,
            quantity=quantity,
            leverage=leverage,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            original_sl=stop_loss_price,
            original_tp=take_profit_price,
            trailing_activation_pct=trailing_activation_pct,
            trailing_distance_pct=trailing_distance_pct,
            highest_price=entry_price,
            lowest_price=entry_price,
            liquidation_price=liq_price,
            tp_levels=tp_levels or [],
            tp_levels_hit=[False] * len(tp_levels or []),
            snowball_enabled=snowball_enabled,
            snowball_max_adds=snowball_max_adds,
            snowball_profit_threshold=snowball_profit_threshold,
            snowball_add_ratio=snowball_add_ratio,
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._tracked[key] = pos
        logger.info(
            f"[PositionTracker] 开始追踪 {key}: "
            f"side={side}, entry={entry_price}, leverage={leverage}x, "
            f"SL={stop_loss_price}, TP={take_profit_price}, "
            f"liq≈{liq_price:.2f}, snowball={'ON' if snowball_enabled else 'OFF'}"
        )
        if not self._running:
            self.start()

    def stop_tracking(self, strategy_id: str, symbol: str):
        key = f"{strategy_id}:{symbol}"
        with self._lock:
            self._tracked.pop(key, None)
        logger.info(f"[PositionTracker] 停止追踪 {key}")

    def get_tracked_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [asdict(p) for p in self._tracked.values()]

    def get_position_status(self, strategy_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        key = f"{strategy_id}:{symbol}"
        with self._lock:
            pos = self._tracked.get(key)
            return asdict(pos) if pos else None

    # =========================================================================
    # 杠杆风控计算
    # =========================================================================

    @staticmethod
    def _calc_liquidation_price(entry: float, side: str, leverage: float) -> float:
        """估算爆仓价格（简化公式，Cross Margin）

        多头爆仓价 ≈ entry × (1 - 1/leverage + 维持保证金率)
        空头爆仓价 ≈ entry × (1 + 1/leverage - 维持保证金率)
        维持保证金率通过 fee_schedule_service 获取（默认 binance=0.004）
        """
        if leverage <= 1:
            return 0.0
        # 中心化费率: 走 fee_schedule_service（默认 binance，与历史行为一致）
        try:
            from backend.services.fee_schedule_service import get_maint_margin_rate
            maint_margin_rate = get_maint_margin_rate("binance")
        except Exception:
            maint_margin_rate = 0.004  # fallback（与历史值一致）
        if side == "buy":
            return round(entry * (1 - 1 / leverage + maint_margin_rate), 6)
        else:
            return round(entry * (1 + 1 / leverage - maint_margin_rate), 6)

    def _update_leverage_safety(self, pos: TrackedPosition):
        """更新杠杆安全指标"""
        if pos.leverage <= 1:
            pos.leverage_safe = True
            pos.distance_to_liq_pct = 1.0
            return

        if pos.liquidation_price <= 0:
            pos.liquidation_price = self._calc_liquidation_price(
                pos.entry_price, pos.side, pos.leverage
            )

        if pos.side == "buy":
            dist = (pos.current_price - pos.liquidation_price) / pos.current_price
        else:
            dist = (pos.liquidation_price - pos.current_price) / pos.current_price

        pos.distance_to_liq_pct = round(max(dist, 0), 6)

        # 距爆仓 < 5% → 极度危险
        if pos.distance_to_liq_pct < 0.05:
            pos.leverage_safe = False
            pos.alerts.append(
                f"⚠️ 爆仓警报！距爆仓仅 {pos.distance_to_liq_pct*100:.1f}%"
            )
            pos.health_score = 0.0
        elif pos.distance_to_liq_pct < 0.10:
            pos.leverage_safe = False
            pos.alerts.append(
                f"⚠ 接近爆仓 {pos.distance_to_liq_pct*100:.1f}%，建议减仓"
            )
            pos.health_score = min(pos.health_score, 0.15)
        elif pos.distance_to_liq_pct < 0.20:
            pos.alerts.append(
                f"距爆仓 {pos.distance_to_liq_pct*100:.1f}%，注意风控"
            )
            pos.health_score = min(pos.health_score, 0.4)
        else:
            pos.leverage_safe = True

    # =========================================================================
    # 监控循环
    # =========================================================================

    def _monitor_loop(self):
        logger.info("[PositionTracker] 监控循环运行中...")
        while self._running:
            try:
                with self._lock:
                    positions = list(self._tracked.values())

                if not positions:
                    time.sleep(MONITOR_INTERVAL_SECONDS)
                    continue

                for pos in positions:
                    try:
                        self._check_position(pos)
                    except Exception as e:
                        logger.error(f"[PositionTracker] 检查持仓异常 {pos.symbol}: {e}")

            except Exception as e:
                logger.error(f"[PositionTracker] 监控循环异常: {e}", exc_info=True)

            time.sleep(MONITOR_INTERVAL_SECONDS)

    def _check_position(self, pos: TrackedPosition):
        """检查单个持仓 - 完整风控流水线"""
        new_price = self._get_current_price(pos.symbol)
        if new_price <= 0:
            return

        pos.current_price = new_price
        pos.last_check_at = datetime.now(timezone.utc).isoformat()

        # 计算 PnL（价格变动百分比 + 杠杆放大后的收益率）
        if pos.side == "buy":
            pos.pnl_pct = (new_price - pos.entry_price) / pos.entry_price
            pos.highest_price = max(pos.highest_price, new_price)
        else:
            pos.pnl_pct = (pos.entry_price - new_price) / pos.entry_price
            pos.lowest_price = min(pos.lowest_price, new_price)

        pos.pnl_leveraged_pct = pos.pnl_pct * pos.leverage

        pos.alerts = []
        pos.health_score = 1.0

        # ── Layer 1: 杠杆安全检查（最高优先级）──
        self._update_leverage_safety(pos)

        # ── Layer 2: 移动止损 ──
        self._check_trailing_stop(pos)

        # ── Layer 3: 分批止盈 ──
        self._check_tp_levels(pos)

        # ── Layer 4: 趋势反转检测 ──
        self._check_trend_reversal(pos)

        # ── Layer 5: 时间止损 ──
        self._check_time_stop(pos)

        # ── Layer 6: 极端行情滚仓检查 ──
        if pos.snowball_enabled:
            self._check_snowball_opportunity(pos)

        # ── Layer 7: 综合健康评估 ──
        self._update_health_score(pos)

        # ── 紧急熔断（杠杆安全线或大幅亏损）──
        if self._should_emergency_exit(pos):
            self._execute_emergency_exit(pos)

    # =========================================================================
    # Layer 2 - 移动止损
    # =========================================================================

    def _check_trailing_stop(self, pos: TrackedPosition):
        """移动止损检查（杠杆感知）"""
        # 杠杆放大后的实际收益来决定是否激活
        effective_pnl = pos.pnl_leveraged_pct

        if pos.side == "buy":
            if effective_pnl >= pos.trailing_activation_pct and not pos.trailing_active:
                pos.trailing_active = True
                pos.alerts.append(
                    f"移动止损已激活 (杠杆收益 {effective_pnl*100:.1f}%)"
                )
                logger.info(
                    f"[PositionTracker] {pos.symbol} 移动止损激活，"
                    f"杠杆收益 {effective_pnl*100:.1f}% (价格变动 {pos.pnl_pct*100:.1f}%)"
                )

            if pos.trailing_active:
                new_sl = pos.highest_price * (1 - pos.trailing_distance_pct)
                if new_sl > pos.stop_loss_price:
                    old_sl = pos.stop_loss_price
                    pos.stop_loss_price = round(new_sl, 6)
                    # 确保止损至少保本
                    if new_sl >= pos.entry_price and old_sl < pos.entry_price:
                        pos.alerts.append("止损已上移至保本以上，利润已锁定")
                        logger.info(f"[PositionTracker] {pos.symbol} 止损上移至保本以上")
                    elif old_sl > 0 and abs(new_sl - old_sl) / old_sl > 0.001:
                        logger.info(
                            f"[PositionTracker] {pos.symbol} 移动止损上移: "
                            f"${old_sl:.2f} → ${pos.stop_loss_price:.2f}"
                        )
        else:
            if effective_pnl >= pos.trailing_activation_pct and not pos.trailing_active:
                pos.trailing_active = True
                pos.alerts.append(
                    f"移动止损已激活 (杠杆收益 {effective_pnl*100:.1f}%)"
                )

            if pos.trailing_active:
                new_sl = pos.lowest_price * (1 + pos.trailing_distance_pct)
                if new_sl < pos.stop_loss_price or pos.stop_loss_price <= 0:
                    old_sl = pos.stop_loss_price
                    pos.stop_loss_price = round(new_sl, 6)
                    if new_sl <= pos.entry_price and (old_sl > pos.entry_price or old_sl <= 0):
                        pos.alerts.append("止损已下移至保本以上，利润已锁定")

        # 检查是否触及止损
        if pos.stop_loss_price > 0:
            if pos.side == "buy" and pos.current_price <= pos.stop_loss_price:
                pos.alerts.append(f"触及止损 ${pos.stop_loss_price:.2f}")
            elif pos.side == "sell" and pos.current_price >= pos.stop_loss_price:
                pos.alerts.append(f"触及止损 ${pos.stop_loss_price:.2f}")

    # =========================================================================
    # Layer 3 - 分批止盈
    # =========================================================================

    def _check_tp_levels(self, pos: TrackedPosition):
        """分批止盈检查"""
        if not pos.tp_levels:
            return

        for i, level in enumerate(pos.tp_levels):
            if i >= len(pos.tp_levels_hit):
                pos.tp_levels_hit.append(False)

            if pos.tp_levels_hit[i]:
                continue

            target_pct = level.get("pct", 0)
            close_ratio = level.get("close_ratio", 0.3)

            if pos.pnl_pct >= target_pct:
                pos.tp_levels_hit[i] = True
                close_qty = pos.quantity * close_ratio
                pos.alerts.append(
                    f"分批止盈L{i+1}触发: 盈利{pos.pnl_pct*100:.1f}% ≥ {target_pct*100:.1f}%, "
                    f"平 {close_ratio*100:.0f}% ({close_qty:.4f})"
                )
                logger.info(
                    f"[PositionTracker] {pos.symbol} 分批止盈 L{i+1}: "
                    f"平 {close_ratio*100:.0f}% at {pos.current_price}"
                )
                # 实际部分平仓在此触发
                self._execute_partial_close(pos, close_ratio, f"分批止盈L{i+1}")
                break  # 每轮只触发一级

    # =========================================================================
    # Layer 4 - 趋势反转检测
    # =========================================================================

    def _check_trend_reversal(self, pos: TrackedPosition):
        db = SessionLocal()
        try:
            from backend.services.strategy_coordinator import StrategyCoordinator
            from backend.services.exchange_config import get_active_exchange

            coordinator = StrategyCoordinator(db)
            exchange = get_active_exchange()
            now_ts = int(datetime.now(timezone.utc).timestamp())

            klines = coordinator._query_klines(
                pos.symbol, "15m", now_ts - 3 * 86400, now_ts, exchange
            )
            if not klines or len(klines) < 21:
                return

            closes = [k["close"] for k in klines]
            ema9 = coordinator._calc_ema(closes, 9)
            ema21 = coordinator._calc_ema(closes, 21)

            if pos.side == "buy" and ema9 < ema21:
                reversal_strength = (ema21 - ema9) / ema21 * 100 if ema21 > 0 else 0
                if reversal_strength > 0.3:
                    pos.alerts.append(
                        f"趋势反转信号: EMA9({ema9:.0f}) < EMA21({ema21:.0f})"
                    )
                    pos.health_score = min(pos.health_score, 0.4)
                    # 高杠杆下趋势反转更危险
                    if pos.leverage >= 5 and reversal_strength > 0.5:
                        pos.health_score = min(pos.health_score, 0.2)
                        pos.alerts.append("高杠杆+强反转，强烈建议减仓")
            elif pos.side == "sell" and ema9 > ema21:
                reversal_strength = (ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
                if reversal_strength > 0.3:
                    pos.alerts.append(
                        f"趋势反转信号: EMA9({ema9:.0f}) > EMA21({ema21:.0f})"
                    )
                    pos.health_score = min(pos.health_score, 0.4)
                    if pos.leverage >= 5 and reversal_strength > 0.5:
                        pos.health_score = min(pos.health_score, 0.2)
                        pos.alerts.append("高杠杆+强反转，强烈建议减仓")
        except Exception as e:
            logger.warning(f"[PositionTracker] 趋势检测失败 {pos.symbol}: {e}")
        finally:
            db.close()

    # =========================================================================
    # Layer 5 - 时间止损
    # =========================================================================

    def _check_time_stop(self, pos: TrackedPosition):
        if not pos.opened_at:
            return
        try:
            opened = datetime.fromisoformat(pos.opened_at)
            hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
            if hours > 72 and pos.pnl_pct < 0:
                pos.alerts.append(
                    f"持仓超 {hours:.0f}h 且亏损 {pos.pnl_pct*100:.1f}%"
                )
                pos.health_score = min(pos.health_score, 0.3)
        except Exception:
            pass

    # =========================================================================
    # Layer 6 - 极端行情滚仓 (Snowball)
    # =========================================================================

    def _check_snowball_opportunity(self, pos: TrackedPosition):
        """极端行情滚仓检查（已退役为监控层，仅产出警报/日志）

        真实加仓统一由 full_auto pyramid 分支 → evaluate_pyramid → paper_engine 执行。
        此处只做趋势检测和信号提示。
        """
        if pos.snowball_adds_done >= pos.snowball_max_adds:
            return

        if pos.pnl_pct < pos.snowball_profit_threshold:
            return

        if pos.snowball_last_add_price > 0:
            price_change = abs(pos.current_price - pos.snowball_last_add_price) / pos.snowball_last_add_price
            if price_change < 0.01:
                return

        is_extreme = self._detect_extreme_trend(pos)
        if not is_extreme:
            return

        add_ratios = [0.30, 0.20, 0.15, 0.10]
        ratio = add_ratios[min(pos.snowball_adds_done, len(add_ratios) - 1)]

        pos.alerts.append(
            f"🔥 滚仓信号（监控）: "
            f"检测到极端趋势，建议追加 {ratio*100:.0f}%, "
            f"浮盈 {pos.pnl_leveraged_pct*100:.1f}% — "
            f"由 full_auto pyramid 执行"
        )

        logger.info(
            f"[PositionTracker] {pos.symbol} 滚仓信号（仅监控）: "
            f"ratio={ratio:.0%}, pnl={pos.pnl_leveraged_pct*100:.1f}%, "
            f"price={pos.current_price}"
        )

    def _detect_extreme_trend(self, pos: TrackedPosition) -> bool:
        """检测是否处于极端单边趋势"""
        db = SessionLocal()
        try:
            from backend.services.strategy_coordinator import StrategyCoordinator
            from backend.services.exchange_config import get_active_exchange

            coordinator = StrategyCoordinator(db)
            exchange = get_active_exchange()
            now_ts = int(datetime.now(timezone.utc).timestamp())

            klines = coordinator._query_klines(
                pos.symbol, "15m", now_ts - 24 * 3600, now_ts, exchange
            )
            if not klines or len(klines) < 20:
                return False

            closes = [k["close"] for k in klines]
            volumes = [k["volume"] for k in klines]
            highs = [k["high"] for k in klines]
            lows = [k["low"] for k in klines]

            # 1. EMA 强趋势判断
            ema9 = coordinator._calc_ema(closes, 9)
            ema21 = coordinator._calc_ema(closes, 21)
            ema_spread = abs(ema9 - ema21) / ema21 if ema21 > 0 else 0

            # 2. 最近 6 根 K 线中同方向占比
            recent = closes[-6:]
            up_count = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i-1])
            down_count = len(recent) - 1 - up_count
            direction_ratio = up_count / (len(recent) - 1) if len(recent) > 1 else 0.5

            # 3. 成交量放大
            recent_vol = sum(volumes[-6:]) / 6 if len(volumes) >= 6 else 0
            older_vol = sum(volumes[-20:-6]) / 14 if len(volumes) >= 20 else recent_vol
            vol_ratio = recent_vol / older_vol if older_vol > 0 else 1.0

            # 判断极端趋势
            if pos.side == "buy":
                is_strong = (
                    ema9 > ema21
                    and ema_spread > 0.005
                    and direction_ratio >= 0.7
                    and vol_ratio > 1.3
                )
            else:
                is_strong = (
                    ema9 < ema21
                    and ema_spread > 0.005
                    and (1 - direction_ratio) >= 0.7
                    and vol_ratio > 1.3
                )

            if is_strong:
                logger.info(
                    f"[PositionTracker] {pos.symbol} 极端趋势确认: "
                    f"ema_spread={ema_spread:.4f}, dir_ratio={direction_ratio:.2f}, "
                    f"vol_ratio={vol_ratio:.2f}"
                )
            return is_strong

        except Exception as e:
            logger.warning(f"[PositionTracker] 极端趋势检测异常 {pos.symbol}: {e}")
            return False
        finally:
            db.close()

    def _execute_snowball_add(self, pos: TrackedPosition, add_qty: float):
        """已退役 — 滚仓执行统一由 full_auto pyramid 分支处理。
        此方法保留仅为向后兼容，不再修改仓位或下单。"""
        logger.info(
            f"[PositionTracker] _execute_snowball_add 已退役，"
            f"滚仓信号 {pos.symbol} +{add_qty:.4f} 交由 full_auto pyramid 执行"
        )

    # =========================================================================
    # 综合健康评估 + 紧急熔断
    # =========================================================================

    def _update_health_score(self, pos: TrackedPosition):
        """计算持仓健康度（杠杆加权）"""
        score = pos.health_score  # 保留前面层已经设置的惩罚

        # 杠杆放大后的亏损更严重
        if pos.pnl_leveraged_pct < -0.15:
            score = min(score, 0.1)
        elif pos.pnl_leveraged_pct < -0.08:
            score -= 0.3
        elif pos.pnl_leveraged_pct < -0.03:
            score -= 0.1
        elif pos.pnl_leveraged_pct > 0.05:
            score += 0.1

        if pos.trailing_active and pos.pnl_pct > 0:
            score += 0.1

        for alert in pos.alerts:
            if "反转" in alert:
                score -= 0.2
            if "止损" in alert and "移动" not in alert:
                score -= 0.3
            if "爆仓" in alert:
                score -= 0.5

        pos.health_score = max(0.0, min(1.0, score))

        if pos.health_score < 0.2:
            pos.alerts.append(
                f"CRITICAL: 持仓健康度极低 {pos.health_score:.2f}，建议平仓"
            )
            logger.warning(
                f"[PositionTracker] {pos.symbol} 健康度极低: "
                f"{pos.health_score:.2f}, leveraged_pnl={pos.pnl_leveraged_pct*100:.1f}%"
            )

    def _should_emergency_exit(self, pos: TrackedPosition) -> bool:
        """判断是否需要紧急平仓 - 杠杆感知"""
        # 条件1：杠杆放大后亏损超过保证金的 80%
        lev_loss_limit = -0.80 / pos.leverage if pos.leverage > 1 else -0.10
        if pos.pnl_pct < lev_loss_limit:
            pos.alerts.append(
                f"杠杆亏损熔断: 价格变动 {pos.pnl_pct*100:.1f}% × {pos.leverage}x杠杆 "
                f"= 实际亏损 {pos.pnl_leveraged_pct*100:.1f}%"
            )
            return True

        # 条件2：距爆仓不足 5%
        if pos.leverage > 1 and pos.distance_to_liq_pct < 0.05:
            pos.alerts.append(
                f"爆仓熔断: 距爆仓仅 {pos.distance_to_liq_pct*100:.1f}%"
            )
            return True

        # 条件3：杠杆放大后实际亏损超过 20%
        if pos.pnl_leveraged_pct < -0.20:
            pos.alerts.append(
                f"杠杆亏损超20%熔断: {pos.pnl_leveraged_pct*100:.1f}%"
            )
            return True

        return False

    def _execute_emergency_exit(self, pos: TrackedPosition):
        """紧急平仓"""
        logger.warning(
            f"[PositionTracker] 🚨 紧急平仓 {pos.symbol}: "
            f"leverage={pos.leverage}x, pnl={pos.pnl_pct*100:.2f}%, "
            f"leveraged_pnl={pos.pnl_leveraged_pct*100:.2f}%, "
            f"dist_to_liq={pos.distance_to_liq_pct*100:.1f}%, "
            f"alerts={pos.alerts}"
        )
        db = SessionLocal()
        try:
            strategy = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == pos.strategy_id
            ).first()
            if not strategy or not strategy.auto_execute:
                logger.info(
                    "[PositionTracker] 策略非自动执行，仅记录紧急退出信号"
                )
                return

            from backend.services.trading_commands import execute_hyperliquid_close_decisions
            from backend.database.models import Account

            account = db.query(Account).filter(
                Account.id == strategy.account_id
            ).first()
            if not account:
                return

            close_side = "sell" if pos.side == "buy" else "buy"
            decision = {
                "operation": close_side,
                "symbol": pos.symbol,
                "target_portion_of_balance": 0,
                "reason": (
                    f"PositionTracker紧急平仓: "
                    f"pnl={pos.pnl_leveraged_pct*100:.2f}% "
                    f"(leverage={pos.leverage}x)"
                ),
            }

            logger.info(f"[PositionTracker] 执行紧急平仓 {pos.symbol} (HyperLiquid)")
            if getattr(account, "hyperliquid_enabled", "false") == "true":
                execute_hyperliquid_close_decisions(db, account.id, [decision])

            self.stop_tracking(pos.strategy_id, pos.symbol)

        except Exception as e:
            logger.error(f"[PositionTracker] 紧急平仓失败 {pos.symbol}: {e}")
        finally:
            db.close()

    def _execute_partial_close(self, pos: TrackedPosition, ratio: float, reason: str):
        """部分平仓"""
        db = SessionLocal()
        try:
            strategy = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == pos.strategy_id
            ).first()
            if not strategy or not strategy.auto_execute:
                logger.info(
                    f"[PositionTracker] 非自动执行，仅记录部分平仓信号: "
                    f"{pos.symbol} {reason}"
                )
                return

            from backend.services.trading_commands import execute_hyperliquid_close_decisions
            from backend.database.models import Account

            account = db.query(Account).filter(
                Account.id == strategy.account_id
            ).first()
            if not account:
                return

            close_qty = pos.quantity * ratio
            close_side = "sell" if pos.side == "buy" else "buy"
            decision = {
                "operation": close_side,
                "symbol": pos.symbol,
                "quantity": close_qty,
                "target_portion_of_balance": 0,
                "reason": f"PositionTracker {reason}",
            }

            if getattr(account, "hyperliquid_enabled", "false") == "true":
                execute_hyperliquid_close_decisions(db, account.id, [decision])

            pos.quantity -= close_qty
            logger.info(
                f"[PositionTracker] {pos.symbol} 部分平仓: "
                f"ratio={ratio*100:.0f}%, 剩余={pos.quantity:.4f}"
            )

        except Exception as e:
            logger.error(
                f"[PositionTracker] 部分平仓失败 {pos.symbol}: {e}"
            )
        finally:
            db.close()

    # =========================================================================
    # 辅助
    # =========================================================================

    def _get_current_price(self, symbol: str) -> float:
        try:
            from backend.services.strategy_coordinator import StrategyCoordinator
            from backend.services.exchange_config import get_active_exchange
            exchange = get_active_exchange()
            return StrategyCoordinator._get_realtime_price(symbol, exchange)
        except Exception:
            return 0.0


position_tracker = PositionTrackerService()
