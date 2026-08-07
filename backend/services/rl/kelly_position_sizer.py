"""
KellyPositionSizer — Kelly准则仓位管理

基于 Kelly Criterion 计算最优仓位比例。
设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第9.8节 (Phase 7)
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class KellyPositionResult:
    """Kelly准则仓位计算结果"""
    kelly_fraction: float          # 原始Kelly比例
    adjusted_fraction: float       # 调整后比例（经安全限制）
    position_size: float           # 实际仓位大小（名义价值）
    risk_per_trade: float          # 单笔风险金额
    confidence: float              # 置信度

    @property
    def is_valid(self) -> bool:
        return self.adjusted_fraction > 0 and self.position_size > 0


class KellyPositionSizer:
    """
    Kelly准则仓位管理器

    Kelly公式: f* = (p * b - q) / b
    其中 p = 胜率, q = 1 - p, b = 平均盈利/平均亏损

    实际使用半 Kelly (fraction * 0.5) 以降低风险。
    """

    FRACTION_OF_KELLY = 0.5        # 使用半Kelly
    MAX_POSITION_PCT = 0.25        # 最大仓位25%
    MIN_TRADES_FOR_CALC = 10       # 计算Kelly的最小交易次数
    DEFAULT_WIN_RATE = 0.5         # 默认胜率
    DEFAULT_WIN_LOSS_RATIO = 1.5   # 默认盈亏比

    def __init__(
        self,
        fraction_of_kelly: float = 0.5,
        max_position_pct: float = 0.25,
        min_trades: int = 10,
    ):
        self.fraction_of_kelly = fraction_of_kelly
        self.max_position_pct = max_position_pct
        self.min_trades = min_trades

    def calculate(
        self,
        equity: float,
        win_rate: float = 0.5,
        avg_win: float = 0.0,
        avg_loss: float = 0.0,
        trade_history: Optional[List[dict]] = None,
        volatility: float = 0.0,
    ) -> KellyPositionResult:
        """
        计算 Kelly 仓位

        Args:
            equity: 当前权益
            win_rate: 胜率 (0~1)
            avg_win: 平均盈利金额
            avg_loss: 平均亏损金额 (正值)
            trade_history: 历史交易列表 [{'pnl': float}, ...]
            volatility: 当前波动率 (用于调整)
        """
        # 从交易历史提取统计
        if trade_history and len(trade_history) >= self.min_trades:
            stats = self._extract_stats(trade_history)
            win_rate = stats['win_rate']
            avg_win = stats['avg_win']
            avg_loss = stats['avg_loss']

        # 确保 avg_loss > 0
        avg_loss = abs(avg_loss) if avg_loss != 0 else 1.0
        avg_win = max(avg_win, 0.01)

        # Kelly 公式: f* = (p * b - q) / b
        # b = avg_win / avg_loss (盈亏比)
        b = avg_win / avg_loss
        p = win_rate
        q = 1.0 - p
        kelly_fraction = (p * b - q) / b if b > 0 else 0.0

        # Kelly 可以为负（期望为负的情况）
        kelly_fraction = max(kelly_fraction, 0.0)

        # 使用半 Kelly
        adjusted = kelly_fraction * self.fraction_of_kelly

        # 波动率调整：高波动时减小仓位
        if volatility > 0:
            vol_adjustment = max(0.5, 1.0 - volatility * 0.5)
            adjusted *= vol_adjustment

        # 限制最大仓位
        adjusted = min(adjusted, self.max_position_pct)

        # 计算实际仓位
        position_size = equity * adjusted
        risk_per_trade = equity * adjusted * (avg_loss / (avg_win + avg_loss + 1e-10))

        # 置信度：基于交易次数和胜率稳定性
        n_trades = len(trade_history) if trade_history else 0
        confidence = min(n_trades / 100.0, 1.0) * min(win_rate * 2, 1.0)

        return KellyPositionResult(
            kelly_fraction=kelly_fraction,
            adjusted_fraction=adjusted,
            position_size=position_size,
            risk_per_trade=risk_per_trade,
            confidence=confidence,
        )

    def _extract_stats(self, trade_history: List[dict]) -> dict:
        """从交易历史提取统计信息"""
        pnls = [t.get('pnl', 0) for t in trade_history if 'pnl' in t]
        if not pnls:
            return {'win_rate': self.DEFAULT_WIN_RATE, 'avg_win': 1.0, 'avg_loss': 1.0}

        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]

        win_rate = len(wins) / len(pnls) if pnls else self.DEFAULT_WIN_RATE
        avg_win = float(np.mean(wins)) if wins else 1.0
        avg_loss = float(np.mean(losses)) if losses else 1.0

        return {
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
        }

    @staticmethod
    def calculate_kelly_fraction(win_rate: float, win_loss_ratio: float) -> float:
        """
        静态方法：计算 Kelly 比例

        Args:
            win_rate: 胜率
            win_loss_ratio: 盈亏比 (avg_win / avg_loss)
        """
        p = win_rate
        q = 1.0 - p
        b = win_loss_ratio
        if b <= 0:
            return 0.0
        return max((p * b - q) / b, 0.0)

    # ══════════════════════════════════════════════════
    #  多币种组合Kelly（AI学习系统整合扩展）
    # ══════════════════════════════════════════════════

    def calculate_portfolio_kelly(
        self,
        kelly_results: Dict[str, 'KellyPositionResult'],
        correlations: Any = None,  # pd.DataFrame or np.ndarray
    ) -> Dict[str, 'KellyPositionResult']:
        """
        计算考虑相关性的多币种Kelly仓位

        使用多币种Kelly公式（Markowitz框架）:
        f* = C^(-1) * mu
        其中 C = 相关性矩阵, mu = 各币种预期超额收益

        Args:
            kelly_results: {symbol: KellyPositionResult} 各币种独立Kelly结果
            correlations: N×N 相关性矩阵 (DataFrame或ndarray)

        Returns:
            {symbol: KellyPositionResult} 调整后的Kelly结果
        """
        from backend.config.settings import PORTFOLIO_MAX_SINGLE_POSITION

        if not kelly_results:
            return {}

        symbols = list(kelly_results.keys())
        n = len(symbols)

        if n == 1 or correlations is None:
            # 单币种或无相关性矩阵：直接返回
            return dict(kelly_results)

        try:
            # 构建预期收益向量 mu (各币种的kelly_fraction)
            mu = np.array([
                kelly_results[s].kelly_fraction for s in symbols
            ])

            # 获取相关性矩阵
            if hasattr(correlations, 'values'):  # pd.DataFrame
                C = correlations.loc[symbols, symbols].values
            else:
                C = np.array(correlations)

            # 正则化: C_reg = C + εI
            C_reg = C + np.eye(n) * 1e-6

            # 条件数检查
            cond = np.linalg.cond(C_reg)
            if cond > 30:
                logger.warning(
                    f"[Kelly] 相关性矩阵条件数={cond:.1f}，降级为独立Kelly"
                )
                return dict(kelly_results)

            # 求解: f* = C_reg^(-1) * mu
            f_star = np.linalg.solve(C_reg, mu)

            # 逐币种限制
            adjusted_results = {}
            for i, symbol in enumerate(symbols):
                original = kelly_results[symbol]
                f_i = max(0.0, min(float(f_star[i]), PORTFOLIO_MAX_SINGLE_POSITION))

                adjusted_results[symbol] = KellyPositionResult(
                    kelly_fraction=original.kelly_fraction,
                    adjusted_fraction=f_i * self.fraction_of_kelly,
                    position_size=original.position_size * (f_i / max(original.kelly_fraction, 1e-10)),
                    risk_per_trade=original.risk_per_trade,
                    confidence=original.confidence,
                )

            return adjusted_results

        except np.linalg.LinAlgError:
            logger.warning("[Kelly] 组合Kelly求解失败，降级为独立Kelly")
            return dict(kelly_results)
        except Exception as e:
            logger.warning(f"[Kelly] 组合Kelly计算异常: {e}，降级为独立Kelly")
            return dict(kelly_results)

    def adjust_for_portfolio_risk(
        self,
        allocations: Dict[str, float],
        max_total_risk: float = 0.30,
    ) -> Dict[str, float]:
        """
        根据组合风险调整仓位

        如果总仓位超过max_total_risk，等比缩放。

        Args:
            allocations: {symbol: adjusted_fraction}
            max_total_risk: 最大组合风险

        Returns:
            调整后的 {symbol: adjusted_fraction}
        """
        total = sum(allocations.values())
        if total <= 0:
            return allocations

        if total > max_total_risk:
            scale = max_total_risk / total
            return {s: a * scale for s, a in allocations.items()}

        return allocations
