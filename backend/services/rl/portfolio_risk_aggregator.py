"""
PortfolioRiskAggregator — 多币种风险聚合器

职责:
1. 汇总所有币种的Kelly仓位
2. 滚动窗口计算币种间风险相关性矩阵
3. 协调DRL决策与Kelly仓位
4. 强制风控阈值（与DeterministicRiskGate对齐）

设计原则:
- 阈值从 settings.py 统一读取，不硬编码
- 相关性矩阵使用滚动窗口 + 增量更新，避免全量重算
- 正则化防止奇异矩阵，条件数检查保证数值稳定性
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Allocation:
    """单币种仓位分配"""
    symbol: str
    kelly_fraction: float
    adjusted_fraction: float
    position_size: float
    risk_contribution: float = 0.0
    correlation_with_others: float = 0.0
    portfolio_fraction: float = 0.0  # 占总仓位比例
    forced_adjustment: str = ""      # 被强制调整的原因


@dataclass
class PortfolioAllocation:
    """组合仓位分配结果"""
    allocations: List[Allocation]
    total_risk: float = 0.0
    correlation_risk: float = 0.0
    forced_adjustments: List[str] = field(default_factory=list)
    correlation_matrix: Optional[np.ndarray] = None


class PortfolioRiskAggregator:
    """
    多币种风险聚合器

    使用滚动窗口计算多币种收益率相关性矩阵，
    基于相关性调整Kelly仓位，并强制组合风控阈值。
    """

    CORRELATION_WINDOW = 252           # ~10天小时线
    CORRELATION_REGULARIZATION = 1e-6  # 正则化参数
    CORRELATION_MAX_CONDITION = 30.0   # 条件数阈值
    CORRELATION_UPDATE_INTERVAL = 300  # 5分钟增量更新间隔

    def __init__(self):
        self._correlation_cache: Dict[str, Any] = {}
        self._last_correlation_update: float = 0.0
        self._returns_cache: Optional[np.ndarray] = None

    # ══════════════════════════════════════════════════
    #  核心方法
    # ══════════════════════════════════════════════════

    def aggregate(
        self,
        kelly_results: Dict[str, Any],
        equity: float = 0.0,
    ) -> PortfolioAllocation:
        """
        聚合多币种Kelly仓位

        Args:
            kelly_results: {symbol: KellyPositionResult}
            equity: 当前权益

        Returns:
            PortfolioAllocation — 包含各币种分配、总风险、相关性风险
        """
        from backend.config.settings import PORTFOLIO_MAX_RISK, PORTFOLIO_MAX_SINGLE_POSITION

        if not kelly_results:
            return PortfolioAllocation(allocations=[])

        # 1. 构建初始分配
        allocations = []
        for symbol, result in kelly_results.items():
            alloc = Allocation(
                symbol=symbol,
                kelly_fraction=result.kelly_fraction if hasattr(result, 'kelly_fraction') else result.get('kelly_fraction', 0),
                adjusted_fraction=result.adjusted_fraction if hasattr(result, 'adjusted_fraction') else result.get('adjusted_fraction', 0),
                position_size=result.position_size if hasattr(result, 'position_size') else result.get('position_size', 0),
            )
            allocations.append(alloc)

        # 2. 计算相关性风险
        symbols = [a.symbol for a in allocations]
        correlation_risk = 0.0
        if len(symbols) > 1:
            correlation_risk = self._estimate_correlation_risk(symbols)

        # 3. 强制单币种仓位限制
        for alloc in allocations:
            if alloc.adjusted_fraction > PORTFOLIO_MAX_SINGLE_POSITION:
                alloc.forced_adjustment = f"单币种仓位{alloc.adjusted_fraction:.2%}超过上限{PORTFOLIO_MAX_SINGLE_POSITION:.2%}"
                alloc.adjusted_fraction = PORTFOLIO_MAX_SINGLE_POSITION
                alloc.position_size = equity * PORTFOLIO_MAX_SINGLE_POSITION

        # 4. 强制组合风险限制
        total_fraction = sum(a.adjusted_fraction for a in allocations)
        forced_adjustments = []
        if total_fraction > PORTFOLIO_MAX_RISK:
            scale = PORTFOLIO_MAX_RISK / total_fraction
            forced_adjustments.append(
                f"组合风险{total_fraction:.2%}超过上限{PORTFOLIO_MAX_RISK:.2%}，等比缩放{scale:.2f}"
            )
            for alloc in allocations:
                alloc.adjusted_fraction *= scale
                alloc.position_size = equity * alloc.adjusted_fraction

        # 5. 计算各币种风险贡献度和组合占比
        total_adj = sum(a.adjusted_fraction for a in allocations) or 1.0
        for alloc in allocations:
            alloc.portfolio_fraction = alloc.adjusted_fraction / total_adj
            alloc.risk_contribution = alloc.adjusted_fraction * (1.0 + correlation_risk)

        total_risk = sum(a.risk_contribution for a in allocations)

        return PortfolioAllocation(
            allocations=allocations,
            total_risk=total_risk,
            correlation_risk=correlation_risk,
            forced_adjustments=forced_adjustments,
        )

    def check_correlation_risk(self, symbols: List[str]) -> float:
        """
        检查币种间相关性风险

        Returns:
            相关性风险值 (0~1)，越高表示组合集中度风险越大
        """
        if len(symbols) <= 1:
            return 0.0
        return self._estimate_correlation_risk(symbols)

    def force_position_limits(
        self,
        allocations: List[Allocation],
        equity: float = 0.0,
    ) -> List[Allocation]:
        """强制风控阈值（仅做限制，不改变Kelly计算）"""
        from backend.config.settings import PORTFOLIO_MAX_RISK, PORTFOLIO_MAX_SINGLE_POSITION

        for alloc in allocations:
            if alloc.adjusted_fraction > PORTFOLIO_MAX_SINGLE_POSITION:
                alloc.adjusted_fraction = PORTFOLIO_MAX_SINGLE_POSITION
                alloc.position_size = equity * PORTFOLIO_MAX_SINGLE_POSITION
                alloc.forced_adjustment = "单币种上限"

        total = sum(a.adjusted_fraction for a in allocations)
        if total > PORTFOLIO_MAX_RISK:
            scale = PORTFOLIO_MAX_RISK / total
            for alloc in allocations:
                alloc.adjusted_fraction *= scale
                alloc.position_size = equity * alloc.adjusted_fraction

        return allocations

    # ══════════════════════════════════════════════════
    #  相关性计算
    # ══════════════════════════════════════════════════

    def compute_rolling_correlation(
        self,
        returns_df: Any,  # pd.DataFrame
    ) -> Any:  # pd.DataFrame
        """
        使用pandas滚动相关系数计算相关性矩阵

        Args:
            returns_df: 多币种收益率DataFrame (index=timestamp, columns=symbols)

        Returns:
            N×N 相关系数矩阵 (最近一个窗口)
        """
        import pandas as pd

        if len(returns_df) < 20:
            # 数据不足，返回单位矩阵
            n = len(returns_df.columns)
            return pd.DataFrame(
                np.eye(n), index=returns_df.columns, columns=returns_df.columns
            )

        # 滚动相关系数
        window = min(self.CORRELATION_WINDOW, len(returns_df))
        corr = returns_df.rolling(window).corr().dropna()

        if corr.empty:
            n = len(returns_df.columns)
            return pd.DataFrame(
                np.eye(n), index=returns_df.columns, columns=returns_df.columns
            )

        # 取最后一个时间点的相关系数矩阵
        last_idx = corr.index.get_level_values(0)[-1]
        corr_matrix = corr.loc[last_idx]

        # 正则化: 对角线 + εI
        n = len(corr_matrix)
        corr_matrix = corr_matrix + np.eye(n) * self.CORRELATION_REGULARIZATION

        # 条件数检查
        try:
            cond = np.linalg.cond(corr_matrix.values)
            if cond > self.CORRELATION_MAX_CONDITION:
                logger.warning(
                    f"[PortfolioRisk] 相关性矩阵条件数={cond:.1f}，"
                    f"超过阈值{self.CORRELATION_MAX_CONDITION}，增加正则化"
                )
                # 增加正则化强度
                extra_reg = (cond - self.CORRELATION_MAX_CONDITION) * 1e-6
                corr_matrix = corr_matrix + np.eye(n) * extra_reg
        except np.linalg.LinAlgError:
            logger.warning("[PortfolioRisk] 相关性矩阵奇异，降级为单位矩阵")
            corr_matrix = pd.DataFrame(
                np.eye(n), index=corr_matrix.index, columns=corr_matrix.columns
            )

        # 缓存
        self._correlation_cache = {
            'matrix': corr_matrix,
            'computed_at': time.time(),
            'symbols': list(corr_matrix.columns),
        }

        return corr_matrix

    def align_timestamps(
        self,
        klines_dict: Dict[str, Any],  # Dict[str, pd.DataFrame]
    ) -> Any:  # pd.DataFrame
        """
        对齐多币种K线时间戳，前向填充缺失值

        Args:
            klines_dict: {symbol: DataFrame with timestamp, close columns}

        Returns:
            对齐后的收益率DataFrame
        """
        import pandas as pd

        if not klines_dict:
            return pd.DataFrame()

        # 合并所有币种的close价格
        close_dict = {}
        for symbol, df in klines_dict.items():
            if 'close' in df.columns and 'timestamp' in df.columns:
                s = df.set_index('timestamp')['close'].sort_index()
                close_dict[symbol] = s

        if not close_dict:
            return pd.DataFrame()

        # 对齐: outer join + 前向填充
        aligned = pd.DataFrame(close_dict)
        aligned = aligned.ffill().dropna()

        # 计算收益率
        returns = aligned.pct_change().dropna()

        return returns

    def incremental_correlation_update(
        self,
        existing_corr: Any,  # pd.DataFrame
        new_returns: Any,     # pd.DataFrame
        decay: float = 0.98,
    ) -> Any:  # pd.DataFrame
        """
        增量更新相关性矩阵（避免全量重算）

        new_corr = decay * existing_corr + (1 - decay) * new_batch_corr
        """
        import pandas as pd

        if new_returns.empty:
            return existing_corr

        new_batch_corr = new_returns.corr()
        if new_batch_corr.isna().any().any():
            return existing_corr

        # 确保维度对齐
        common_symbols = list(
            set(existing_corr.columns) & set(new_batch_corr.columns)
        )
        if len(common_symbols) < 2:
            return existing_corr

        existing_sub = existing_corr.loc[common_symbols, common_symbols]
        new_sub = new_batch_corr.loc[common_symbols, common_symbols]

        updated = decay * existing_sub + (1 - decay) * new_sub

        # 确保对角线为1
        for s in common_symbols:
            updated.loc[s, s] = 1.0

        return updated

    # ══════════════════════════════════════════════════
    #  内部方法
    # ══════════════════════════════════════════════════

    def _estimate_correlation_risk(self, symbols: List[str]) -> float:
        """
        估算相关性风险（0~1）

        基于缓存的相关性矩阵，计算平均非对角线相关系数。
        高相关性 → 高风险（组合分散化不足）。
        """
        if not self._correlation_cache or 'matrix' not in self._correlation_cache:
            return 0.1  # 默认低相关性风险

        corr = self._correlation_cache['matrix']
        cached_symbols = self._correlation_cache.get('symbols', [])

        # 取交集
        common = [s for s in symbols if s in cached_symbols]
        if len(common) < 2:
            return 0.1

        sub = corr.loc[common, common]
        n = len(sub)

        # 平均非对角线绝对相关系数
        mask = ~np.eye(n, dtype=bool)
        avg_corr = np.mean(np.abs(sub.values[mask]))

        return float(avg_corr)


# 全局单例
portfolio_risk_aggregator = PortfolioRiskAggregator()
