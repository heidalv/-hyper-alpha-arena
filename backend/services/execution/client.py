"""
ExecutionClient 抽象 + 双轨执行（P3.1，方案 §P3.1，红线 R2+R3）。

R2 回测/实盘同核：策略代码不感知引擎，只换 ExecutionClient。
R3 双轨常驻：每个实盘决策同时跑纸上影子轨道（架构层始终开启，无开关）。
    影子偏差超阈 → 作为熔断信号源（P3.4 消费）。

基于现有 ExecutionChannel 抽象（PaperExecutor/LiveExecutor），构建：
    - ExecutionClient：Lean 契约版执行接口（ApprovedTarget → OrderEvent）
    - DualTrackExecutor：同时调 live + paper client，实时对比成交差异
    - ShadowDeviation：偏差指标（P3.4 熔断输入）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from backend.services.contracts.types import (
    ApprovedTarget,
    OrderEvent,
    OrderStatus,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShadowDeviation:
    """影子偏差（live vs paper 成交对比）。P3.4 熔断输入。"""
    ts_ns: int
    instrument_symbol: str
    price_dev_bps: float       # 成交价偏差（bps）
    latency_dev_ms: float      # 延迟偏差（ms）
    fill_qty_diff: float       # 成交量差（绝对值）
    severity: str = "OK"       # OK / WARN / CRITICAL


class ExecutionClient:
    """
    Lean 契约版执行接口（ApprovedTarget → OrderEvent）。

    子类：
        LiveExecutionClient：包现有 live_executor + adapter（真下单）
        PaperExecutionClient：包 paper_exchange_simulator（模拟）
        BacktestExecutionClient：历史事件流回放（P3.3 parity）

    策略代码只调此接口，不感知是 live/paper/backtest（R2）。
    """

    def execute(self, target: ApprovedTarget) -> OrderEvent:
        """执行 ApprovedTarget，返回 OrderEvent。子类实现。"""
        raise NotImplementedError

    def cancel(self, client_id: str) -> bool:
        """撤单。"""
        raise NotImplementedError


def _make_order_event(target: ApprovedTarget, status: OrderStatus,
                      fill_price: float, fill_qty: float, fee: float,
                      client_id: str, venue_order_id: str = "",
                      side: str = "") -> OrderEvent:
    """构造 OrderEvent（内部辅助）。"""
    s = side or ("buy" if target.approved_qty >= 0 else "sell")
    return OrderEvent(
        ts_ns=target.ts_ns, instrument=target.instrument,
        client_id=client_id, venue_order_id=venue_order_id or None,
        side=s, price=fill_price, qty=abs(fill_qty),
        status=status, fill_price=fill_price, fill_qty=fill_qty,
        fee=fee, ts_event_ns=time.time_ns(),
    )


class DualTrackExecutor:
    """
    双轨执行器（R3：架构层始终开启，无开关）。

    每个 ApprovedTarget 同时进入：
        ① live client（真下单）
        ② paper client（同核模拟成交）
    对比两者成交价/延迟/量 → ShadowDeviation。
    偏差超阈 → 上报（P3.4 熔断消费）。

    不存在"测完再开"——双轨是执行架构的固有属性。
    """

    def __init__(
        self,
        live_client: ExecutionClient,
        paper_client: ExecutionClient,
        *,
        warn_dev_bps: float = 5.0,
        critical_dev_bps: float = 20.0,
    ):
        self.live = live_client
        self.paper = paper_client
        self.warn_bps = warn_dev_bps
        self.critical_bps = critical_dev_bps
        self.deviations: list[ShadowDeviation] = []
        self._consecutive_critical = 0

    def execute_dual(self, target: ApprovedTarget) -> tuple[OrderEvent, OrderEvent, ShadowDeviation]:
        """
        同时执行 live + paper，返回 (live_event, paper_event, deviation)。

        paper 同步执行（模拟，无 IO）；live 实际下单。
        """
        t0_live = time.perf_counter()
        try:
            live_evt = self.live.execute(target)
        except Exception as e:
            logger.error(f"[DualTrack] live 执行异常: {e}", exc_info=False)
            live_evt = _make_order_event(target, OrderStatus.REJECTED, 0, 0, 0,
                                         client_id=f"live_err_{target.ts_ns}")
        latency_live_ms = (time.perf_counter() - t0_live) * 1000

        t0_paper = time.perf_counter()
        try:
            paper_evt = self.paper.execute(target)
        except Exception as e:
            logger.error(f"[DualTrack] paper 执行异常: {e}", exc_info=False)
            paper_evt = _make_order_event(target, OrderStatus.REJECTED, 0, 0, 0,
                                          client_id=f"paper_err_{target.ts_ns}")
        latency_paper_ms = (time.perf_counter() - t0_paper) * 1000

        dev = self._compute_deviation(target, live_evt, paper_evt,
                                      latency_live_ms, latency_paper_ms)
        self.deviations.append(dev)
        if dev.severity == "CRITICAL":
            self._consecutive_critical += 1
        else:
            self._consecutive_critical = 0
        return live_evt, paper_evt, dev

    def _compute_deviation(
        self, target: ApprovedTarget, live: OrderEvent, paper: OrderEvent,
        lat_live_ms: float, lat_paper_ms: float,
    ) -> ShadowDeviation:
        """计算 live vs paper 偏差。"""
        # 价格偏差（bps）
        if live.fill_price and paper.fill_price and paper.fill_price > 0:
            price_dev = abs(live.fill_price - paper.fill_price) / paper.fill_price * 1e4
        else:
            price_dev = 0.0
        # 成交量差
        qty_diff = abs(live.fill_qty - paper.fill_qty)
        # 延迟差
        lat_dev = abs(lat_live_ms - lat_paper_ms)

        severity = "OK"
        if price_dev >= self.critical_bps:
            severity = "CRITICAL"
        elif price_dev >= self.warn_bps:
            severity = "WARN"

        return ShadowDeviation(
            ts_ns=time.time_ns(), instrument_symbol=target.instrument.symbol,
            price_dev_bps=price_dev, latency_dev_ms=lat_dev,
            fill_qty_diff=qty_diff, severity=severity,
        )

    def consecutive_critical_count(self) -> int:
        """连续 critical 偏差计数（P3.4 熔断用）。"""
        return self._consecutive_critical

    def recent_deviations(self, n: int = 100) -> list[ShadowDeviation]:
        return self.deviations[-n:]

    def stats(self) -> dict:
        if not self.deviations:
            return {"total": 0}
        critical = sum(1 for d in self.deviations if d.severity == "CRITICAL")
        warn = sum(1 for d in self.deviations if d.severity == "WARN")
        return {
            "total": len(self.deviations),
            "critical": critical,
            "warn": warn,
            "consecutive_critical": self._consecutive_critical,
        }
