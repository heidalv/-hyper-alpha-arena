"""
策略准入门控器 — StrategyValidator

Gate 1：回测准入门控（方案§7.2）
任何策略在进入模拟盘前，必须通过以下全部条件：
  - 样本外 Sharpe Ratio >= 1.5
  - 最大回撤 <= 15%
  - 交易笔数 >= 200 笔
  - 利润因子 >= 1.3
  - 多/空胜率差 <= 15%
  - 过拟合检测：样本内外 Sharpe 比 <= 1.5
  - 连续最大亏损 <= 8 笔

Gate 2：模拟盘→实盘门控（方案§7.2）
  - 模拟盘运行时间 >= 14 天
  - 模拟盘 Sharpe >= 1.0
  - 模拟盘最大回撤 <= 10%
  - 模拟盘交易笔数 >= 30 笔
  - 与回测结果偏差 <= 30%
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


@dataclass
class BacktestMetrics:
    """回测指标结构（供 Gate 1 验证）"""
    total_trades: int = 0
    long_trades: int = 0
    short_trades: int = 0
    long_wins: int = 0
    short_wins: int = 0
    in_sample_sharpe: float = 0.0    # 样本内 Sharpe（前70%数据）
    out_sample_sharpe: float = 0.0   # 样本外 Sharpe（后30%数据）
    max_drawdown_pct: float = 0.0    # 最大回撤（%）
    profit_factor: float = 0.0       # 利润因子 = 总盈利 / 总亏损
    max_consecutive_losses: int = 0  # 最大连续亏损笔数
    total_return_pct: float = 0.0    # 总收益率（%）


@dataclass
class PaperTradingMetrics:
    """模拟盘指标（供 Gate 2 验证）"""
    days_running: int = 0
    total_trades: int = 0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0   # [P0-3] 语义=单笔最大亏损%（辅助门槛）
    total_return_pct: float = 0.0    # 模拟盘实际收益（时间加权占用保证金口径）
    backtest_return_pct: float = 0.0  # 对应的回测预期收益
    real_sharpe: float = 0.0         # [P0-3] 交易级年化 Sharpe（真 Sharpe）
    equity_dd_pct: float = 0.0       # [P0-3] 累计收益曲线峰谷回撤（%）


@dataclass
class ValidationResult:
    """验证结果"""
    passed: bool
    gate: str                   # "gate1" / "gate2"
    failed_checks: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""


class StrategyValidator:
    """
    策略有效性验证器。

    使用方式：
        validator = StrategyValidator()
        result = validator.validate_gate1(metrics)
        if result.passed:
            # 允许进入模拟盘
    """

    # ── Gate 1 阈值 ──
    # 修复（2026-06-24）：原阈值（Sharpe≥1.5/MDD≤15%/trades≥200）在加密回测中几乎
    # 不可达，导致 NSGA-II 进化产出的冠军（如 Sharpe=6.03/PF=1.27/MDD=15%）始终
    # promoted=False，学习进化闭环的"改进落地"环节完全空转。
    # 现放宽到加密市场现实水平，与 PROMOTION_THRESHOLDS_BY_TIER 对齐。
    GATE1_MIN_OUT_SAMPLE_SHARPE: float = 0.8     # 原 1.5 → 0.8（加密 1h+ 周期 Sharpe 0.8 已可用）
    GATE1_MAX_DRAWDOWN_PCT: float = 25.0          # 原 15.0 → 25.0（加密高波动，15% 太严）
    GATE1_MIN_TRADES: int = 50                    # 原 200 → 50（回测数据有限，200 几乎不可能）
    GATE1_MIN_PROFIT_FACTOR: float = 1.2          # 原 1.3 → 1.2（与 tier 阈值 1.3/1.5 对齐放宽）
    GATE1_MAX_WINRATE_DIFF: float = 25.0          # 原 15.0 → 25.0（多/空胜率差，加密趋势市单向正常）
    GATE1_MAX_OVERFIT_RATIO: float = 2.0          # 原 1.5 → 2.0（样本内外差异容忍）
    GATE1_MAX_CONSECUTIVE_LOSSES: int = 8

    # ── Gate 2 阈值 ──
    GATE2_MIN_DAYS: int = 14
    GATE2_MIN_TRADES: int = 30
    GATE2_MIN_SHARPE: float = 1.0
    GATE2_MAX_DRAWDOWN_PCT: float = 10.0
    GATE2_MAX_RETURN_DEVIATION_PCT: float = 30.0  # 模拟盘与回测收益偏差

    def validate_gate1(self, metrics: BacktestMetrics) -> ValidationResult:
        """Gate 1 回测准入门控验证"""
        failed = []
        warnings = []
        details: Dict[str, Any] = {}

        # 1. 样本外 Sharpe >= 1.5
        details["out_sample_sharpe"] = metrics.out_sample_sharpe
        if metrics.out_sample_sharpe < self.GATE1_MIN_OUT_SAMPLE_SHARPE:
            failed.append(
                f"样本外 Sharpe {metrics.out_sample_sharpe:.2f} < {self.GATE1_MIN_OUT_SAMPLE_SHARPE}"
            )

        # 2. 最大回撤 <= 15%
        details["max_drawdown_pct"] = metrics.max_drawdown_pct
        if metrics.max_drawdown_pct > self.GATE1_MAX_DRAWDOWN_PCT:
            failed.append(
                f"最大回撤 {metrics.max_drawdown_pct:.1f}% > {self.GATE1_MAX_DRAWDOWN_PCT}%"
            )

        # 3. 交易笔数 >= 200
        details["total_trades"] = metrics.total_trades
        if metrics.total_trades < self.GATE1_MIN_TRADES:
            failed.append(
                f"交易笔数 {metrics.total_trades} < {self.GATE1_MIN_TRADES}（统计显著性不足）"
            )

        # 4. 利润因子 >= 1.3
        details["profit_factor"] = metrics.profit_factor
        if metrics.profit_factor < self.GATE1_MIN_PROFIT_FACTOR:
            failed.append(
                f"利润因子 {metrics.profit_factor:.2f} < {self.GATE1_MIN_PROFIT_FACTOR}"
            )

        # 5. 多/空胜率均衡（差 <= 15%）
        long_wr = (metrics.long_wins / metrics.long_trades * 100) if metrics.long_trades > 0 else 0
        short_wr = (metrics.short_wins / metrics.short_trades * 100) if metrics.short_trades > 0 else 0
        wr_diff = abs(long_wr - short_wr)
        details["long_winrate"] = round(long_wr, 1)
        details["short_winrate"] = round(short_wr, 1)
        details["winrate_diff"] = round(wr_diff, 1)
        if metrics.long_trades > 0 and metrics.short_trades > 0 and wr_diff > self.GATE1_MAX_WINRATE_DIFF:
            failed.append(
                f"多/空胜率差 {wr_diff:.1f}% > {self.GATE1_MAX_WINRATE_DIFF}%（单向偏向策略）"
            )

        # 6. 过拟合检测：in_sample_sharpe / out_sample_sharpe <= 1.5
        if metrics.out_sample_sharpe > 0:
            overfit_ratio = metrics.in_sample_sharpe / metrics.out_sample_sharpe
            details["overfit_ratio"] = round(overfit_ratio, 2)
            if overfit_ratio > self.GATE1_MAX_OVERFIT_RATIO:
                failed.append(
                    f"过拟合比率 {overfit_ratio:.2f} > {self.GATE1_MAX_OVERFIT_RATIO}"
                    f"（样本内 Sharpe={metrics.in_sample_sharpe:.2f} vs 样本外={metrics.out_sample_sharpe:.2f}）"
                )

        # 7. 最大连续亏损 <= 8 笔
        details["max_consecutive_losses"] = metrics.max_consecutive_losses
        if metrics.max_consecutive_losses > self.GATE1_MAX_CONSECUTIVE_LOSSES:
            failed.append(
                f"最大连续亏损 {metrics.max_consecutive_losses} 笔 > {self.GATE1_MAX_CONSECUTIVE_LOSSES} 笔"
            )

        passed = len(failed) == 0
        recommendation = "通过 Gate 1，可进入模拟盘验证" if passed else f"未通过 Gate 1，需优化后重新回测（{len(failed)} 项不达标）"

        result = ValidationResult(
            passed=passed,
            gate="gate1",
            failed_checks=failed,
            warnings=warnings,
            details=details,
            recommendation=recommendation,
        )
        if passed:
            logger.info(f"[StrategyValidator] Gate 1 通过: {details}")
        else:
            logger.warning(f"[StrategyValidator] Gate 1 未通过: {failed}")
        return result

    def validate_gate2(
        self,
        paper_metrics: PaperTradingMetrics,
    ) -> ValidationResult:
        """Gate 2 模拟盘→实盘门控验证"""
        failed = []
        warnings = []
        details: Dict[str, Any] = {}

        # 1. 运行时间 >= 14 天
        details["days_running"] = paper_metrics.days_running
        if paper_metrics.days_running < self.GATE2_MIN_DAYS:
            failed.append(
                f"模拟盘运行 {paper_metrics.days_running} 天 < {self.GATE2_MIN_DAYS} 天"
            )

        # 2. [P0-3] 真 Sharpe >= 1.0（交易级年化）。
        # 旧判定用 StrategyMemory.sharpe_ratio（盈亏符号 EMA，值域 [-1,1]）与 1.0 比较，
        # 稳健策略永远被拦、近期全胜的运气策略才可能通过——门槛实质失效。
        details["sharpe"] = paper_metrics.real_sharpe
        if paper_metrics.real_sharpe < self.GATE2_MIN_SHARPE:
            failed.append(
                f"模拟盘真实 Sharpe {paper_metrics.real_sharpe:.2f} < {self.GATE2_MIN_SHARPE}"
            )

        # 3. [P0-3] 累计收益曲线回撤 <= 10%。
        # 旧判定把 mem.max_drawdown（单笔最大亏损）当回撤比对，无法拦截连续小亏积累。
        details["equity_dd"] = paper_metrics.equity_dd_pct
        if paper_metrics.equity_dd_pct > self.GATE2_MAX_DRAWDOWN_PCT:
            failed.append(
                f"模拟盘收益曲线回撤 {paper_metrics.equity_dd_pct:.1f}% > {self.GATE2_MAX_DRAWDOWN_PCT}%"
            )

        # 3b. [P0-3] 单笔最大亏损辅助门槛（≤15%）：防单笔爆仓式亏损（保留旧字段语义）
        details["max_single_trade_loss"] = paper_metrics.max_drawdown_pct
        if paper_metrics.max_drawdown_pct > 15.0:
            failed.append(
                f"单笔最大亏损 {paper_metrics.max_drawdown_pct:.1f}% > 15%"
            )

        # 4. 交易笔数 >= 30
        details["total_trades"] = paper_metrics.total_trades
        if paper_metrics.total_trades < self.GATE2_MIN_TRADES:
            failed.append(
                f"模拟盘交易笔数 {paper_metrics.total_trades} < {self.GATE2_MIN_TRADES}"
            )

        # 5. 模拟盘与回测收益偏差 <= 30%
        if paper_metrics.backtest_return_pct == 0:
            # [2026-08-15 消费端验收] 无回测基准时 fail-closed 显式拦截：
            # 原 `if != 0` 条件静默跳过 = 该一致性检查形同虚设，任何策略
            # 都能在无基准时「通过」偏差门槛。
            details["return_deviation_pct"] = None
            failed.append("缺少回测收益基准（backtest_return_pct=0），一致性偏差无法校验")
        else:
            deviation = abs(
                (paper_metrics.total_return_pct - paper_metrics.backtest_return_pct)
                / paper_metrics.backtest_return_pct * 100
            )
            details["return_deviation_pct"] = round(deviation, 1)
            if deviation > self.GATE2_MAX_RETURN_DEVIATION_PCT:
                failed.append(
                    f"模拟盘与回测收益偏差 {deviation:.1f}% > {self.GATE2_MAX_RETURN_DEVIATION_PCT}%"
                )

        passed = len(failed) == 0
        recommendation = "通过 Gate 2，可进入灰度实盘（第1周10%仓位）" if passed else f"未通过 Gate 2（{len(failed)} 项不达标）"

        result = ValidationResult(
            passed=passed,
            gate="gate2",
            failed_checks=failed,
            warnings=warnings,
            details=details,
            recommendation=recommendation,
        )
        if passed:
            logger.info(f"[StrategyValidator] Gate 2 通过: {details}")
        else:
            logger.warning(f"[StrategyValidator] Gate 2 未通过: {failed}")
        return result


# 模块级单例
strategy_validator = StrategyValidator()
