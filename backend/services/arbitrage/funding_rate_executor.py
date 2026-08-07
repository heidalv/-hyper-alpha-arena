"""
FundingRateExecutor — 资金费率套利执行器

Phase 1: Paper trading 模式
- 基于 OpportunityScanner 扫描到的机会
- 不实际下单, 模拟开/平仓并追踪收益
- 持仓管理: 每 8 小时结算一次 funding, 费率反转时平仓

Phase 2 (future): 对接 Hyperliquid 实盘下单
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import ArbitrageOpportunity, ArbitrageStatus, FundingRateSnapshot

logger = logging.getLogger(__name__)


@dataclass
class PaperFundingPosition:
    """Paper 模式的资金费率套利持仓"""
    position_id: str
    symbol: str
    strategy: str           # "funding_long" | "funding_short"
    size_usd: float
    entry_price: float
    entry_time: float
    funding_collected: float = 0.0
    funding_payments: int = 0
    unrealized_pnl: float = 0.0
    status: str = "open"
    close_time: float = 0.0
    close_reason: str = ""

    @property
    def total_pnl(self) -> float:
        return self.funding_collected + self.unrealized_pnl

    @property
    def holding_hours(self) -> float:
        end = self.close_time if self.close_time else time.time()
        return (end - self.entry_time) / 3600


@dataclass
class ExecutionResult:
    success: bool
    position_id: str = ""
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


class FundingRateExecutor:
    """资金费率套利执行器 (Paper Trading)"""

    # 配置
    MIN_ANNUAL_YIELD = 0.15       # 年化 15% 最低门槛
    MAX_POSITION_USD = 10000.0    # 单笔最大仓位
    FUNDING_INTERVAL_SEC = 8 * 3600  # 8小时
    RATE_REVERSAL_THRESHOLD = 0.0001  # 费率反转阈值
    MAX_HOLDING_HOURS = 72        # 最大持仓时间

    def __init__(self):
        self._positions: Dict[str, PaperFundingPosition] = {}
        self._closed_positions: List[PaperFundingPosition] = []
        self._last_funding_settlement: float = time.time()

    # ── Public API ───────────────────────────────

    def execute_opportunity(
        self,
        opp: ArbitrageOpportunity,
        size_usd: Optional[float] = None,
        entry_price: Optional[float] = None,
    ) -> ExecutionResult:
        """
        基于扫描到的机会开立 Paper 仓位。

        Args:
            opp: 扫描机会
            size_usd: Orchestrator 传入的实际仓位（优先于 recommended_size）
            entry_price: 入场价格（可选）

        Returns:
            ExecutionResult
        """
        # 前置检查
        if opp.expected_annual_yield < self.MIN_ANNUAL_YIELD:
            return ExecutionResult(
                success=False,
                message=f"年化收益 {opp.expected_annual_yield:.1%} < 门槛 {self.MIN_ANNUAL_YIELD:.1%}",
            )

        # 检查是否已有相同symbol的持仓
        for pos in self._positions.values():
            if pos.symbol == opp.symbol and pos.status == "open":
                return ExecutionResult(
                    success=False,
                    message=f"已有 {opp.symbol} 持仓 {pos.position_id}",
                )

        if size_usd is None or size_usd <= 0:
            size_usd = opp.recommended_size
        size_usd = min(size_usd, self.MAX_POSITION_USD)
        if size_usd <= 0:
            return ExecutionResult(
                success=False,
                message="仓位大小为 0，拒绝开仓",
            )

        pos_id = f"fr_{uuid.uuid4().hex[:8]}"

        if entry_price is None or entry_price <= 0:
            entry_price = opp.funding_snapshot.oi_total / max(
                opp.funding_snapshot.volume_24h, 1
            ) if opp.funding_snapshot and opp.funding_snapshot.volume_24h > 0 else 1.0
        entry_price = max(entry_price, 0.01)

        position = PaperFundingPosition(
            position_id=pos_id,
            symbol=opp.symbol,
            strategy=opp.strategy,
            size_usd=size_usd,
            entry_price=entry_price,
            entry_time=time.time(),
        )
        self._positions[pos_id] = position

        logger.info(
            f"[FundingRateExec] Paper开仓 {pos_id}: {opp.symbol} "
            f"strategy={opp.strategy}, size=${size_usd:.0f}, "
            f"expected_yield={opp.expected_annual_yield:.1%}"
        )

        return ExecutionResult(
            success=True,
            position_id=pos_id,
            message=f"Paper position opened: {opp.symbol} ${size_usd:.0f}",
            details={
                "symbol": opp.symbol,
                "strategy": opp.strategy,
                "size_usd": size_usd,
                "expected_yield": opp.expected_annual_yield,
            },
        )

    def settle_funding(self, current_rates: Dict[str, FundingRateSnapshot]):
        """
        结算资金费率 — 应每 ~8 小时调用一次。

        Args:
            current_rates: {symbol: FundingRateSnapshot}
        """
        now = time.time()
        if now - self._last_funding_settlement < self.FUNDING_INTERVAL_SEC * 0.9:
            return

        self._last_funding_settlement = now

        for pos in list(self._positions.values()):
            if pos.status != "open":
                continue

            snap = current_rates.get(pos.symbol)
            if not snap:
                continue

            # Funding收益: 如果 strategy=funding_long, 做多收空头费率
            # 如果费率为正(多付空), long策略 → 收取
            rate = snap.current_rate
            if pos.strategy == "funding_long":
                payout = pos.size_usd * rate
            else:
                payout = pos.size_usd * (-rate)

            pos.funding_collected += payout
            pos.funding_payments += 1

            logger.debug(
                f"[FundingRateExec] Funding结算 {pos.position_id}: "
                f"rate={rate:.6f}, payout=${payout:.2f}, "
                f"total=${pos.funding_collected:.2f}"
            )

    def check_close_conditions(self, current_rates: Dict[str, FundingRateSnapshot]):
        """检查是否需要平仓 (费率反转 / 超时)。"""
        now = time.time()
        to_close: List[str] = []

        for pos_id, pos in self._positions.items():
            if pos.status != "open":
                continue

            reason = ""
            snap = current_rates.get(pos.symbol)

            # 费率反转检测
            if snap:
                rate = snap.current_rate
                if pos.strategy == "funding_long" and rate < -self.RATE_REVERSAL_THRESHOLD:
                    reason = f"费率反转: {rate:.6f} < -{self.RATE_REVERSAL_THRESHOLD}"
                elif pos.strategy == "funding_short" and rate > self.RATE_REVERSAL_THRESHOLD:
                    reason = f"费率反转: {rate:.6f} > {self.RATE_REVERSAL_THRESHOLD}"

            # 超时
            if not reason and pos.holding_hours > self.MAX_HOLDING_HOURS:
                reason = f"持仓超时: {pos.holding_hours:.0f}h > {self.MAX_HOLDING_HOURS}h"

            if reason:
                to_close.append((pos_id, reason))

        for pos_id, reason in to_close:
            self._close_position(pos_id, reason)

    def get_closed_position_ids(self) -> List[tuple]:
        """返回已平仓但未从 orchestrator 同步的 (position_id, reason) 列表"""
        return [
            (pid, pos.close_reason or "funding_exit")
            for pid, pos in self._positions.items()
            if pos.status == "closed"
        ]

    def remove_position(self, position_id: str) -> None:
        """Orchestrator 同步平仓后清理"""
        self._positions.pop(position_id, None)

    def monitor_positions(self) -> List[Dict[str, Any]]:
        """返回所有活跃 Paper 持仓的状态快照。"""
        result = []
        for pos in self._positions.values():
            if pos.status != "open":
                continue
            result.append({
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "strategy": pos.strategy,
                "size_usd": pos.size_usd,
                "funding_collected": pos.funding_collected,
                "funding_payments": pos.funding_payments,
                "holding_hours": round(pos.holding_hours, 1),
                "total_pnl": pos.total_pnl,
            })
        return result

    def get_performance_summary(self) -> Dict[str, Any]:
        """汇总全部已平仓 + 活跃仓位绩效。"""
        all_positions = list(self._closed_positions) + [
            p for p in self._positions.values() if p.status == "open"
        ]
        if not all_positions:
            return {"total_positions": 0, "total_pnl": 0.0}

        total_pnl = sum(p.total_pnl for p in all_positions)
        total_funding = sum(p.funding_collected for p in all_positions)
        avg_holding = sum(p.holding_hours for p in all_positions) / len(all_positions)

        return {
            "total_positions": len(all_positions),
            "open_positions": sum(1 for p in self._positions.values() if p.status == "open"),
            "closed_positions": len(self._closed_positions),
            "total_pnl": round(total_pnl, 2),
            "total_funding_collected": round(total_funding, 2),
            "avg_holding_hours": round(avg_holding, 1),
        }

    # ── Internal ─────────────────────────────────

    def _close_position(self, position_id: str, reason: str):
        pos = self._positions.get(position_id)
        if not pos or pos.status != "open":
            return

        pos.status = "closed"
        pos.close_time = time.time()
        pos.close_reason = reason
        self._closed_positions.append(pos)

        logger.info(
            f"[FundingRateExec] Paper平仓 {position_id}: {pos.symbol} "
            f"pnl=${pos.total_pnl:.2f}, funding=${pos.funding_collected:.2f}, "
            f"reason={reason}"
        )
