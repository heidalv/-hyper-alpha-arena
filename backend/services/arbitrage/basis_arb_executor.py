"""
BasisArbExecutor — 期现基差套利执行器 (Paper Trading)

Phase 1: Paper trading
- 监控永续合约-现货指数基差
- 基差超过阈值时记录模拟开仓 (做空永续 + 做多现货)
- 追踪基差收敛过程和模拟盈亏
- 基差收敛到目标或超时后平仓

Phase 2 (future): 跨交易所实盘执行
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class BasisSnapshot:
    """基差快照"""
    symbol: str
    perp_price: float      # 永续合约价格
    spot_price: float      # 现货价格
    basis_pct: float       # 基差百分比 = (perp - spot) / spot * 100
    timestamp: float = field(default_factory=time.time)

    @property
    def is_premium(self) -> bool:
        """永续溢价 (做空永续有利)"""
        return self.basis_pct > 0

    @property
    def is_discount(self) -> bool:
        """永续折价 (做多永续有利)"""
        return self.basis_pct < 0


@dataclass
class PaperBasisPosition:
    """Paper 模式的基差套利持仓"""
    position_id: str
    symbol: str
    direction: str          # "short_perp" (正基差) | "long_perp" (负基差)
    size_usd: float
    entry_basis_pct: float
    entry_perp_price: float
    entry_spot_price: float
    entry_time: float
    current_basis_pct: float = 0.0
    unrealized_pnl: float = 0.0
    status: str = "open"
    close_time: float = 0.0
    close_reason: str = ""

    @property
    def basis_change(self) -> float:
        """基差变化 (收敛为正值收益)"""
        if self.direction == "short_perp":
            return self.entry_basis_pct - self.current_basis_pct
        else:
            return self.current_basis_pct - self.entry_basis_pct

    @property
    def holding_hours(self) -> float:
        end = self.close_time if self.close_time else time.time()
        return (end - self.entry_time) / 3600


class BasisArbExecutor:
    """期现基差套利执行器 (Paper Trading)"""

    # 配置
    ENTRY_THRESHOLD_PCT = 0.3     # 基差 > 0.3% 才开仓
    EXIT_TARGET_PCT = 0.05        # 基差收敛到 0.05% 以下平仓
    MAX_POSITION_USD = 10000.0
    MAX_HOLDING_HOURS = 48
    MAX_OPEN_POSITIONS = 5

    def __init__(self):
        self._positions: Dict[str, PaperBasisPosition] = {}
        self._closed_positions: List[PaperBasisPosition] = []
        self._basis_history: Dict[str, List[BasisSnapshot]] = {}

    # ── Public API ───────────────────────────────

    def record_basis(self, snapshot: BasisSnapshot):
        """记录基差快照, 用于趋势判断。"""
        self._basis_history.setdefault(snapshot.symbol, []).append(snapshot)
        # 保留最近 200 条
        if len(self._basis_history[snapshot.symbol]) > 200:
            self._basis_history[snapshot.symbol] = self._basis_history[snapshot.symbol][-200:]

    def scan_and_execute(self, snapshots: List[BasisSnapshot]) -> List[Dict[str, Any]]:
        """
        扫描基差快照, 自动开仓/平仓。

        Args:
            snapshots: 当前各 symbol 的基差快照

        Returns:
            操作记录列表
        """
        actions = []

        for snap in snapshots:
            self.record_basis(snap)

            # 更新现有持仓的当前基差
            for pos in self._positions.values():
                if pos.symbol == snap.symbol and pos.status == "open":
                    pos.current_basis_pct = snap.basis_pct
                    pos.unrealized_pnl = pos.basis_change / 100.0 * pos.size_usd

            # 检查平仓条件
            close_actions = self._check_close(snap)
            actions.extend(close_actions)

            # 检查开仓条件
            open_action = self._check_open(snap)
            if open_action:
                actions.append(open_action)

        return actions

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """返回所有活跃 Paper 持仓。"""
        return [
            {
                "position_id": p.position_id,
                "symbol": p.symbol,
                "direction": p.direction,
                "size_usd": p.size_usd,
                "entry_basis_pct": p.entry_basis_pct,
                "current_basis_pct": p.current_basis_pct,
                "basis_change": round(p.basis_change, 4),
                "unrealized_pnl": round(p.unrealized_pnl, 2),
                "holding_hours": round(p.holding_hours, 1),
            }
            for p in self._positions.values()
            if p.status == "open"
        ]

    def get_performance_summary(self) -> Dict[str, Any]:
        """汇总绩效。"""
        all_pos = list(self._closed_positions) + [
            p for p in self._positions.values() if p.status == "open"
        ]
        if not all_pos:
            return {"total_positions": 0, "total_pnl": 0.0}

        closed_pnl = sum(p.unrealized_pnl for p in self._closed_positions)
        open_pnl = sum(p.unrealized_pnl for p in self._positions.values() if p.status == "open")

        return {
            "total_positions": len(all_pos),
            "open_positions": sum(1 for p in self._positions.values() if p.status == "open"),
            "closed_positions": len(self._closed_positions),
            "realized_pnl": round(closed_pnl, 2),
            "unrealized_pnl": round(open_pnl, 2),
            "total_pnl": round(closed_pnl + open_pnl, 2),
        }

    # ── Internal ─────────────────────────────────

    def _check_open(self, snap: BasisSnapshot) -> Optional[Dict[str, Any]]:
        """检查是否应该开仓。"""
        # 已有此 symbol 的持仓
        for p in self._positions.values():
            if p.symbol == snap.symbol and p.status == "open":
                return None

        # 达到上限
        open_count = sum(1 for p in self._positions.values() if p.status == "open")
        if open_count >= self.MAX_OPEN_POSITIONS:
            return None

        abs_basis = abs(snap.basis_pct)
        if abs_basis < self.ENTRY_THRESHOLD_PCT:
            return None

        # 确认基差方向稳定 (连续3条同方向)
        history = self._basis_history.get(snap.symbol, [])
        if len(history) < 3:
            return None
        recent = history[-3:]
        if snap.is_premium:
            if not all(s.basis_pct > 0 for s in recent):
                return None
            direction = "short_perp"
        else:
            if not all(s.basis_pct < 0 for s in recent):
                return None
            direction = "long_perp"

        size = min(abs_basis / self.ENTRY_THRESHOLD_PCT * 2000, self.MAX_POSITION_USD)
        pos_id = f"basis_{uuid.uuid4().hex[:8]}"

        position = PaperBasisPosition(
            position_id=pos_id,
            symbol=snap.symbol,
            direction=direction,
            size_usd=size,
            entry_basis_pct=snap.basis_pct,
            entry_perp_price=snap.perp_price,
            entry_spot_price=snap.spot_price,
            entry_time=time.time(),
            current_basis_pct=snap.basis_pct,
        )
        self._positions[pos_id] = position

        logger.info(
            f"[BasisArb] Paper开仓 {pos_id}: {snap.symbol} "
            f"dir={direction}, basis={snap.basis_pct:.3f}%, size=${size:.0f}"
        )

        return {
            "action": "open",
            "position_id": pos_id,
            "symbol": snap.symbol,
            "direction": direction,
            "basis_pct": snap.basis_pct,
            "size_usd": size,
        }

    def _check_close(self, snap: BasisSnapshot) -> List[Dict[str, Any]]:
        """检查是否需要平仓。"""
        actions = []

        for pos in list(self._positions.values()):
            if pos.symbol != snap.symbol or pos.status != "open":
                continue

            reason = ""

            # 基差收敛到目标
            if abs(snap.basis_pct) < self.EXIT_TARGET_PCT:
                reason = f"基差收敛: {snap.basis_pct:.4f}% < {self.EXIT_TARGET_PCT}%"

            # 基差方向反转 (做空永续时基差变负)
            if not reason:
                if pos.direction == "short_perp" and snap.basis_pct < -self.ENTRY_THRESHOLD_PCT:
                    reason = f"基差反转: {snap.basis_pct:.3f}%"
                elif pos.direction == "long_perp" and snap.basis_pct > self.ENTRY_THRESHOLD_PCT:
                    reason = f"基差反转: {snap.basis_pct:.3f}%"

            # 超时
            if not reason and pos.holding_hours > self.MAX_HOLDING_HOURS:
                reason = f"持仓超时: {pos.holding_hours:.0f}h"

            if reason:
                pos.status = "closed"
                pos.close_time = time.time()
                pos.close_reason = reason
                self._closed_positions.append(pos)

                logger.info(
                    f"[BasisArb] Paper平仓 {pos.position_id}: {pos.symbol} "
                    f"pnl=${pos.unrealized_pnl:.2f}, basis_change={pos.basis_change:.3f}%, "
                    f"reason={reason}"
                )

                actions.append({
                    "action": "close",
                    "position_id": pos.position_id,
                    "symbol": pos.symbol,
                    "pnl": pos.unrealized_pnl,
                    "reason": reason,
                })

        return actions
