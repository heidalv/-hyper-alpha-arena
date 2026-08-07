"""
RebateArbPositionMonitor — 返利仓位生命周期监控器

监控活跃仓位状态、积分进度、VIP等级变化和退出条件。
线程安全设计。
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from .models import (
    RebatePosition,
    RebatePositionStatus,
    RebateStrategyType,
)

logger = logging.getLogger(__name__)


class RebateArbPositionMonitor:
    """返利仓位监控器"""

    # ── 监控参数 ──
    MAX_HOLD_SECONDS = 86400 * 30      # 最大持仓30天
    STALE_DATA_SECONDS = 3600           # 数据过期阈值 1小时
    POINTS_DECAY_WARNING_DAYS = 3       # 积分过期预警天数
    VIP_DOWNGRADE_VOLUME_PCT = 0.8      # VIP降级预警: 交易量低于要求的80%
    PAPER_STOP_LOSS_NOTIONAL_PCT = 0.008  # Paper 名义亏损兜底
    PAPER_STOP_LOSS_MARGIN_PCT = 0.04     # Paper 保证金亏损 4% 强平（主止损）
    LIVE_STOP_LOSS_PCT = 0.05             # Live 相对名义 5% 止损

    def __init__(self):
        self._positions: Dict[str, RebatePosition] = {}
        self._lock = threading.RLock()
        self._points_cache: Dict[str, Dict[str, Any]] = {}  # exchange -> points_data
        self._vip_cache: Dict[str, Dict[str, Any]] = {}      # exchange -> vip_data
        self._performance_log: List[Dict[str, Any]] = []

    # ── 仓位管理 ──

    def add_position(self, position: RebatePosition) -> None:
        """添加新仓位到监控"""
        with self._lock:
            self._positions[position.position_id] = position
            logger.info(
                f"[RebateMonitor] 新仓位: {position.position_id} "
                f"策略={position.strategy_type.value} "
                f"交易所={position.source_exchange}"
            )

    def remove_position(self, position_id: str) -> Optional[RebatePosition]:
        """移除仓位"""
        with self._lock:
            pos = self._positions.pop(position_id, None)
            if pos:
                self._performance_log.append({
                    "position_id": position_id,
                    "strategy": pos.strategy_type.value,
                    "pnl": pos.current_pnl,
                    "rebate": pos.accumulated_rebate,
                    "hold_hours": pos.hold_duration_hours,
                    "close_ts": time.time(),
                })
            return pos

    def get_active_positions(self) -> List[RebatePosition]:
        """获取所有活跃仓位"""
        with self._lock:
            return [
                p for p in self._positions.values()
                if p.status == RebatePositionStatus.ACTIVE
            ]

    def get_position(self, position_id: str) -> Optional[RebatePosition]:
        """获取指定仓位"""
        with self._lock:
            return self._positions.get(position_id)

    def get_positions_by_strategy(self, strategy: RebateStrategyType) -> List[RebatePosition]:
        """按策略类型获取仓位"""
        with self._lock:
            return [
                p for p in self._positions.values()
                if p.strategy_type == strategy
                and p.status == RebatePositionStatus.ACTIVE
            ]

    # ── 仓位更新 ──

    def update_position_pnl(
        self, position_id: str, current_pnl: float, accumulated_rebate: float = 0.0
    ) -> bool:
        """更新仓位盈亏"""
        with self._lock:
            pos = self._positions.get(position_id)
            if not pos:
                return False
            pos.current_pnl = current_pnl
            pos.accumulated_rebate += accumulated_rebate
            return True

    def update_position_points(self, position_id: str, points: float) -> bool:
        """更新仓位累计积分"""
        with self._lock:
            pos = self._positions.get(position_id)
            if not pos:
                return False
            pos.accumulated_points += points
            return True

    def close_position(self, position_id: str, reason: str = "") -> bool:
        """关闭仓位"""
        with self._lock:
            pos = self._positions.get(position_id)
            if not pos:
                return False
            pos.status = RebatePositionStatus.CLOSED
            self._performance_log.append({
                "position_id": position_id,
                "strategy": pos.strategy_type.value,
                "pnl": pos.current_pnl,
                "rebate": pos.accumulated_rebate,
                "points": pos.accumulated_points,
                "hold_hours": pos.hold_duration_hours,
                "close_reason": reason,
                "close_ts": time.time(),
            })
            logger.info(
                f"[RebateMonitor] 仓位关闭: {position_id} "
                f"原因={reason} PnL=${pos.current_pnl:.2f} "
                f"返利=${pos.accumulated_rebate:.2f}"
            )
            return True

    # ── 监控检查 ──

    def check_exits(self) -> List[Dict[str, Any]]:
        """
        检测退出条件

        Returns:
            需要退出的仓位列表及原因
        """
        exits = []
        now = time.time()

        try:
            from backend.services.rebate_arb.rebate_position_mtm import refresh_all_paper_positions_mtm

            refresh_all_paper_positions_mtm()
        except Exception as exc:
            logger.debug("[RebateMonitor] MTM refresh before exits skipped: %s", exc)

        with self._lock:
            for pos in self._positions.values():
                if pos.status != RebatePositionStatus.ACTIVE:
                    continue

                # 超时退出
                hold_duration = now - pos.entry_time
                if hold_duration > pos.max_hold_seconds:
                    exits.append({
                        "position_id": pos.position_id,
                        "reason": "max_hold_exceeded",
                        "detail": f"持仓 {hold_duration/86400:.1f} 天超过上限",
                    })

                if pos.current_pnl < 0:
                    from backend.services.rebate_arb.s8_param_learner import (
                        PAPER_STOP_LOSS_MARGIN_PCT,
                        PAPER_STOP_LOSS_NOTIONAL_PCT,
                        resolve_position_margin_usd,
                    )

                    margin = resolve_position_margin_usd(pos)
                    loss_abs = abs(pos.current_pnl)
                    hit = False
                    detail = ""
                    if pos.paper_mode and margin > 0:
                        loss_margin_pct = loss_abs / margin
                        if loss_margin_pct >= PAPER_STOP_LOSS_MARGIN_PCT:
                            hit = True
                            detail = (
                                f"Paper 保证金止损 {loss_margin_pct:.2%} "
                                f"≥ {PAPER_STOP_LOSS_MARGIN_PCT:.2%} "
                                f"(亏${loss_abs:.2f}/保证金${margin:.2f})"
                            )
                    if not hit:
                        loss_notional_pct = loss_abs / max(pos.total_size, 1.0)
                        threshold = (
                            PAPER_STOP_LOSS_NOTIONAL_PCT
                            if pos.paper_mode
                            else self.LIVE_STOP_LOSS_PCT
                        )
                        if loss_notional_pct >= threshold:
                            hit = True
                            detail = (
                                f"{'Paper' if pos.paper_mode else 'Live'} 名义止损 "
                                f"{loss_notional_pct:.2%} ≥ {threshold:.2%}"
                            )
                    if hit:
                        exits.append({
                            "position_id": pos.position_id,
                            "reason": "stop_loss",
                            "detail": detail,
                        })

        return exits

    def check_points_progress(self) -> Dict[str, Dict[str, Any]]:
        """
        检查各交易所积分进度

        Returns:
            {exchange: {"balance": float, "rate": float, "decay_warning": bool}}
        """
        result = {}
        now = time.time()

        for exchange, data in self._points_cache.items():
            balance = data.get("balance", 0.0)
            daily_rate = data.get("daily_rate", 0.0)
            last_update = data.get("last_update", 0.0)

            # 检查数据是否过期
            is_stale = (now - last_update) > self.STALE_DATA_SECONDS

            result[exchange] = {
                "balance": balance,
                "daily_rate": daily_rate,
                "is_stale": is_stale,
                "decay_warning": False,  # 需要外部数据更新
                "last_update": last_update,
            }

        return result

    def check_vip_status(self) -> Dict[str, Dict[str, Any]]:
        """
        检查各交易所 VIP 等级状态

        Returns:
            {exchange: {"tier": str, "volume_30d": float, "next_tier_volume": float,
                        "downgrade_risk": bool}}
        """
        result = {}

        for exchange, data in self._vip_cache.items():
            tier = data.get("tier", "VIP0")
            volume_30d = data.get("volume_30d", 0.0)
            next_tier_volume = data.get("next_tier_volume", 0.0)
            min_volume = data.get("min_volume", 0.0)

            # 降级风险: 当前交易量低于最低要求的80%
            downgrade_risk = (
                min_volume > 0
                and volume_30d < min_volume * self.VIP_DOWNGRADE_VOLUME_PCT
            )

            result[exchange] = {
                "tier": tier,
                "volume_30d": volume_30d,
                "next_tier_volume": next_tier_volume,
                "downgrade_risk": downgrade_risk,
            }

        return result

    def update_points_cache(self, exchange: str, points_data: Dict[str, Any]) -> None:
        """更新积分缓存"""
        with self._lock:
            points_data["last_update"] = time.time()
            self._points_cache[exchange] = points_data

    def update_vip_cache(self, exchange: str, vip_data: Dict[str, Any]) -> None:
        """更新 VIP 缓存"""
        with self._lock:
            vip_data["last_update"] = time.time()
            self._vip_cache[exchange] = vip_data

    # ── 状态查询 ──

    def get_status(self) -> Dict[str, Any]:
        """获取监控器整体状态"""
        with self._lock:
            active = [p for p in self._positions.values() if p.status == RebatePositionStatus.ACTIVE]
            total_pnl = sum(p.current_pnl for p in active)
            total_rebate = sum(p.accumulated_rebate for p in active)
            total_points = sum(p.accumulated_points for p in active)

            return {
                "active_positions": len(active),
                "total_pnl": total_pnl,
                "total_rebate": total_rebate,
                "total_points": total_points,
                "positions_by_strategy": {
                    s.value: len([p for p in active if p.strategy_type == s])
                    for s in RebateStrategyType
                },
                "closed_count": len(self._performance_log),
                "exits_pending": len(self.check_exits()),
            }

    def get_performance_summary(self) -> Dict[str, Any]:
        """获取绩效汇总"""
        with self._lock:
            if not self._performance_log:
                return {"total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0}

            total = len(self._performance_log)
            wins = sum(1 for t in self._performance_log if t.get("pnl", 0) > 0)
            total_pnl = sum(t.get("pnl", 0) for t in self._performance_log)
            total_rebate = sum(t.get("rebate", 0) for t in self._performance_log)
            total_points = sum(t.get("points", 0) for t in self._performance_log)

            by_strategy: Dict[str, Dict[str, float]] = {}
            for t in self._performance_log:
                s = t.get("strategy", "unknown")
                if s not in by_strategy:
                    by_strategy[s] = {"count": 0, "pnl": 0.0, "rebate": 0.0}
                by_strategy[s]["count"] += 1
                by_strategy[s]["pnl"] += t.get("pnl", 0)
                by_strategy[s]["rebate"] += t.get("rebate", 0)

            return {
                "total_trades": total,
                "win_rate": wins / max(total, 1),
                "total_pnl": total_pnl,
                "total_rebate": total_rebate,
                "total_points": total_points,
                "net_pnl": total_pnl + total_rebate,
                "by_strategy": by_strategy,
            }


# 模块级单例
rebate_position_monitor = RebateArbPositionMonitor()
