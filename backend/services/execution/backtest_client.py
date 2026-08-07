"""
回测执行客户端 + parity 框架（P3.3，方案 §P3.3，红线 R2）。

目标：回测/实盘同核。策略代码不感知引擎，BacktestExecutionClient
复用同一 Alpha→Portfolio→Risk→Execution 链，ExecutionClient 换 backtest 版。

nautilus_trader 式 parity：Backtest/Paper/Live 三 client 共享同核，
唯一变量是 fill 模型。偏差量化进 ShadowDeviation 监控 + 熔断。

完成标准（方案 P3.3）：同代码同事件流，backtest/paper/live 成交序列
差异仅来自 fill 模型近似。
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.services.contracts.types import (
    ApprovedTarget,
    OrderEvent,
    OrderStatus,
)
from backend.services.execution.client import ExecutionClient, _make_order_event


@dataclass
class FillModel:
    """
    成交模型（backtest 用，模拟滑点/部分成交/拒绝）。

    生产环境 LiveExecutionClient 用真实成交；此处用历史 L2 + 滑点曲线近似。
    parity 偏差即来自此模型的近似程度。
    """
    slippage_bps: float = 1.0        # 基础滑点（bps）
    partial_fill_prob: float = 0.0   # 部分成交概率（流动性不足时）
    taker_fee_bps: float = 3.5       # taker 费率（bps，Hyperliquid 0.035%）
    maker_fee_bps: float = 2.0       # maker 费率

    def simulate_fill(self, qty: float, ref_price: float, side: str,
                      book_depth_usd: float = 0.0) -> tuple[float, float, OrderStatus]:
        """
        模拟成交。返回 (fill_price, fill_qty, status)。

        滑点 = base + impact(qty, depth)
        """
        sign = 1.0 if side == "buy" else -1.0
        # 基础滑点
        slip = self.slippage_bps / 1e4
        # 市场冲击（线性简化）：大单 + 浅盘 → 更大滑点
        if book_depth_usd > 0:
            impact = min(0.01, qty * ref_price / book_depth_usd * 0.1)
            slip += impact
        fill_price = ref_price * (1 + sign * slip)
        # 部分成交
        fill_qty = qty
        status = OrderStatus.FILLED
        if self.partial_fill_prob > 0 and book_depth_usd > 0:
            max_fill = min(qty, book_depth_usd / ref_price)
            if max_fill < qty:
                fill_qty = max_fill
                status = OrderStatus.PARTIAL
        return fill_price, fill_qty, status

    def fee(self, fill_qty: float, fill_price: float, is_maker: bool = False) -> float:
        rate = self.maker_fee_bps if is_maker else self.taker_fee_bps
        return fill_qty * fill_price * rate / 1e4


class BacktestExecutionClient(ExecutionClient):
    """
    回测执行客户端。

    与 LiveExecutionClient/PaperExecutionClient 同接口（R2 parity）。
    用历史事件流 + FillModel 模拟成交。
    """

    def __init__(self, fill_model: FillModel | None = None,
                 price_oracle=None):
        """
        price_oracle: callable(ts_ns, symbol) -> (ref_price, book_depth_usd)
                      从历史 L2 数据提供参考价 + 盘口深度。
        """
        self.fill_model = fill_model or FillModel()
        self.price_oracle = price_oracle
        self.events: list[OrderEvent] = []
        self._order_counter = 0

    def execute(self, target: ApprovedTarget) -> OrderEvent:
        """回测执行：用 price_oracle 取参考价 + FillModel 模拟。"""
        if self.price_oracle is None:
            # 无 oracle：用 0 价 + REJECTED（测试用）
            self._order_counter += 1
            return _make_order_event(
                target, OrderStatus.REJECTED, 0, 0, 0,
                client_id=f"bt_{self._order_counter}",
            )
        ref_price, depth = self.price_oracle(target.ts_ns, target.instrument.symbol)
        side = "buy" if target.approved_qty >= 0 else "sell"
        fill_price, fill_qty, status = self.fill_model.simulate_fill(
            abs(target.approved_qty), ref_price, side, depth,
        )
        fee = self.fill_model.fee(fill_qty, fill_price)
        self._order_counter += 1
        evt = _make_order_event(
            target, status, fill_price, fill_qty, fee,
            client_id=f"bt_{self._order_counter}",
            side=side,
        )
        self.events.append(evt)
        return evt

    def cancel(self, client_id: str) -> bool:
        return True  # 回测撤单总是成功


def run_parity_check(
    approved_targets: list[ApprovedTarget],
    live_client: ExecutionClient,
    backtest_client: BacktestExecutionClient,
    *,
    max_price_dev_bps: float = 30.0,
    max_fill_qty_diff_pct: float = 0.1,
) -> dict:
    """
    Parity 校验：同一组 ApprovedTarget 过 live + backtest client，
    对比成交序列差异。

    返回 {max_price_dev_bps, max_qty_diff_pct, n_compared, parity_ok}。
    parity_ok = 所有偏差在阈值内。
    """
    live_events = [live_client.execute(t) for t in approved_targets]
    bt_events = [backtest_client.execute(t) for t in approved_targets]

    max_dev = 0.0
    max_qty_diff = 0.0
    n = 0
    for le, be in zip(live_events, bt_events):
        if le.fill_price and be.fill_price and be.fill_price > 0:
            dev = abs(le.fill_price - be.fill_price) / be.fill_price * 1e4
            max_dev = max(max_dev, dev)
        qty_diff = abs(le.fill_qty - be.fill_qty)
        denom = max(abs(le.fill_qty), abs(be.fill_qty), 1e-9)
        max_qty_diff = max(max_qty_diff, qty_diff / denom)
        n += 1

    return {
        "n_compared": n,
        "max_price_dev_bps": max_dev,
        "max_fill_qty_diff_pct": max_qty_diff,
        "parity_ok": max_dev <= max_price_dev_bps and max_qty_diff <= max_fill_qty_diff_pct,
    }
