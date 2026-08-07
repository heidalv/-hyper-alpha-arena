"""
Factor Selector - 因子选择器

提供因子有效性评估和选择功能：
1. IC (Information Coefficient) 计算
2. 因子衰减度计算
3. 因子有效性排名
4. 最优因子筛选

Author: Hyper-Alpha-Arena
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import numpy as np
import pandas as pd

from .base_factors import FactorEngine, FactorCategory, FactorValue

logger = logging.getLogger(__name__)


@dataclass
class FactorIC:
    """因子IC统计"""
    name: str
    ic_mean: float
    ic_std: float
    ic_count: int
    rank_ic_mean: float
    rank_ic_std: float
    t_stat: float
    p_value: float
    is_significant: bool = False


@dataclass
class FactorDecay:
    """因子衰减度"""
    name: str
    current_ic: float
    historical_ic: float
    decay_rate: float
    half_life: float
    trend: str  # improving, stable, declining
    recommendation: str


@dataclass
class FactorRank:
    """因子综合排名"""
    name: str
    category: FactorCategory
    ic_rank: int
    decay_rank: int
    combined_score: float
    final_weight: float
    is_selected: bool
    reason: str


class FactorSelector:
    """
    因子选择器
    
    基于历史数据计算因子有效性，
    选择预测能力最强且最稳定的因子
    """
    
    def __init__(self, factor_engine: FactorEngine):
        self.engine = factor_engine
        self.historical_ic: Dict[str, List[float]] = defaultdict(list)
        self.historical_returns: Dict[str, List[float]] = defaultdict(list)
        self.ic_lookback = 20  # IC计算回溯期
        self.decay_lookback = 10  # 衰减计算回溯期
        
    def calculate_ic(
        self, 
        factor_values: Dict[str, float], 
        forward_returns: Dict[str, float],
        method: str = 'rank'
    ) -> Dict[str, float]:
        """
        计算因子的信息系数 (IC)
        
        Args:
            factor_values: 因子值
            forward_returns: 未来收益
            method: 'pearson' 或 'rank'
            
        Returns:
            因子名称 -> IC值
        """
        if not factor_values or not forward_returns:
            return {}
        
        common_symbols = set(factor_values.keys()) & set(forward_returns.keys())
        if len(common_symbols) < 3:
            return {}
        
        ic_results = {}
        
        for factor_name in factor_values:
            factor_list = []
            return_list = []
            
            for symbol in common_symbols:
                if symbol in self.historical_ic[factor_name]:
                    factor_list.append(factor_values[symbol])
                    return_list.append(forward_returns[symbol])
            
            if len(factor_list) < 3:
                ic_results[factor_name] = 0.0
                continue
            
            try:
                if method == 'pearson':
                    ic = self._pearson_ic(factor_list, return_list)
                else:
                    ic = self._rank_ic(factor_list, return_list)
                    
                ic_results[factor_name] = ic
                
            except Exception as e:
                logger.warning(f"[FactorSelector] IC calc error for {factor_name}: {e}")
                ic_results[factor_name] = 0.0
        
        return ic_results
    
    def _pearson_ic(self, factor_vals: List[float], returns: List[float]) -> float:
        """Pearson相关系数"""
        if len(factor_vals) != len(returns):
            return 0.0
            
        n = len(factor_vals)
        if n < 3:
            return 0.0
            
        mean_f = np.mean(factor_vals)
        mean_r = np.mean(returns)
        
        cov = np.sum((f - mean_f) * (r - mean_r) for f, r in zip(factor_vals, returns)) / n
        std_f = np.std(factor_vals) + 1e-8
        std_r = np.std(returns) + 1e-8
        
        return cov / (std_f * std_r)
    
    def _rank_ic(self, factor_vals: List[float], returns: List[float]) -> float:
        """Rank IC (Spearman)"""
        if len(factor_vals) != len(returns):
            return 0.0
            
        n = len(factor_vals)
        if n < 3:
            return 0.0
        
        try:
            from scipy import stats
            ic, _ = stats.spearmanr(factor_vals, returns)
            return ic if not np.isnan(ic) else 0.0
        except ImportError:
            return self._pearson_ic(factor_vals, returns)
    
    def calculate_factor_ic_series(
        self, 
        factor_name: str, 
        factor_history: List[float], 
        return_history: List[float]
    ) -> List[float]:
        """
        计算因子IC时间序列
        
        用于评估因子IC的稳定性
        """
        if len(factor_history) < self.ic_lookback * 2:
            return []
        
        ic_series = []
        
        for i in range(self.ic_lookback, len(factor_history)):
            start = i - self.ic_lookback
            factor_slice = factor_history[start:i]
            return_slice = return_history[start:i]
            
            if len(factor_slice) == self.ic_lookback:
                ic = self._rank_ic(factor_slice, return_slice)
                ic_series.append(ic)
        
        return ic_series
    
    def calculate_ic_statistics(self, factor_name: str) -> Optional[FactorIC]:
        """
        计算因子IC统计
        
        Returns:
            FactorIC对象，包含IC均值、IC标准差、t统计量等
        """
        if factor_name not in self.historical_ic:
            return None
        
        ic_list = self.historical_ic[factor_name]
        if len(ic_list) < 5:
            return None
        
        ic_array = np.array(ic_list)
        
        ic_mean = float(np.mean(ic_array))
        ic_std = float(np.std(ic_array))
        ic_count = len(ic_list)
        
        rank_ic_list = ic_list  # 使用相同的历史数据
        rank_ic_array = np.array(rank_ic_list)
        rank_ic_mean = float(np.mean(rank_ic_array))
        rank_ic_std = float(np.std(rank_ic_array))
        
        t_stat = 0.0
        p_value = 1.0
        
        if ic_std > 0 and ic_count > 1:
            se = ic_std / np.sqrt(ic_count)
            t_stat = ic_mean / se if se > 0 else 0.0
            from scipy import stats
            p_value = 2 * (1 - stats.t.cdf(abs(t_stat), ic_count - 1))
        
        is_significant = abs(ic_mean) > 0.05 and p_value < 0.05
        
        return FactorIC(
            name=factor_name,
            ic_mean=ic_mean,
            ic_std=ic_std,
            ic_count=ic_count,
            rank_ic_mean=rank_ic_mean,
            rank_ic_std=rank_ic_std,
            t_stat=t_stat,
            p_value=p_value,
            is_significant=is_significant
        )
    
    def calculate_decay(self, factor_name: str) -> Optional[FactorDecay]:
        """
        计算因子衰减度
        
        因子随时间推移可能失效，
        此函数计算当前IC相对于历史的衰减程度
        """
        if factor_name not in self.historical_ic:
            return None
        
        ic_history = self.historical_ic[factor_name]
        if len(ic_history) < self.decay_lookback * 2:
            return None
        
        current_ic = np.mean(ic_history[-self.decay_lookback:])
        historical_ic = np.mean(ic_history[:-self.decay_lookback])
        
        if abs(historical_ic) < 0.01:
            return None
        
        decay_rate = (current_ic - historical_ic) / abs(historical_ic)
        
        if decay_rate < -0.3:
            trend = 'declining'
            recommendation = '降低权重或替换'
        elif decay_rate > 0.1:
            trend = 'improving'
            recommendation = '增加权重'
        else:
            trend = 'stable'
            recommendation = '维持当前权重'
        
        half_life = 0.0
        if decay_rate < 0:
            half_life = 0.693 / abs(decay_rate + 0.01)
        
        return FactorDecay(
            name=factor_name,
            current_ic=current_ic,
            historical_ic=historical_ic,
            decay_rate=decay_rate,
            half_life=half_life,
            trend=trend,
            recommendation=recommendation
        )
    
    def rank_factors(
        self, 
        factor_values: Dict[str, float], 
        forward_returns: Dict[str, float],
        selected_count: int = 10
    ) -> List[FactorRank]:
        """
        因子综合排名
        
        考虑因素：
        1. IC均值 (预测能力)
        2. IC稳定性 (IC标准差)
        3. 因子衰减度 (是否在衰退)
        4. 因子类别分散度
        """
        ic_results = self.calculate_ic(factor_values, forward_returns)
        
        factor_ranks = []
        
        for factor_name in factor_values:
            ic = ic_results.get(factor_name, 0.0)
            ic_stats = self.calculate_ic_statistics(factor_name)
            decay = self.calculate_decay(factor_name)
            
            factor_info = self.engine.get_factor_info(factor_name)
            category = factor_info['category'] if factor_info else FactorCategory.MOMENTUM
            
            base_score = abs(ic) * (1 if ic > 0 else -1)
            
            stability_penalty = 0.0
            if ic_stats and ic_stats.ic_count > 1:
                stability_penalty = min(0.2, ic_stats.ic_std * 0.5)
            
            decay_bonus = 0.0
            if decay:
                if decay.trend == 'improving':
                    decay_bonus = 0.1
                elif decay.trend == 'declining':
                    decay_bonus = -0.15
            
            combined_score = base_score - stability_penalty + decay_bonus
            
            reason_parts = []
            reason_parts.append(f"IC={ic:.3f}")
            if ic_stats:
                reason_parts.append(f"稳定性={ic_stats.ic_std:.3f}")
            if decay:
                reason_parts.append(f"趋势={decay.trend}")
            
            factor_ranks.append(FactorRank(
                name=factor_name,
                category=category,
                ic_rank=0,
                decay_rank=0,
                combined_score=combined_score,
                final_weight=0.0,
                is_selected=False,
                reason="; ".join(reason_parts)
            ))
        
        factor_ranks.sort(key=lambda x: x.combined_score, reverse=True)
        
        for i, rank in enumerate(factor_ranks):
            rank.ic_rank = i + 1
        
        decay_ranks = sorted(factor_ranks, key=lambda x: self._get_decay_score(x.name), reverse=True)
        for i, rank in enumerate(decay_ranks):
            rank.decay_rank = i + 1
        
        for rank in factor_ranks:
            rank.final_weight = self._calculate_final_weight(rank)
        
        selected = 0
        for rank in factor_ranks:
            if selected < selected_count:
                rank.is_selected = True
                selected += 1
        
        return factor_ranks
    
    def _get_decay_score(self, factor_name: str) -> float:
        """获取因子的衰减评分"""
        decay = self.calculate_decay(factor_name)
        if not decay:
            return 0.0
        
        score = decay.current_ic
        if decay.trend == 'improving':
            score += 0.1
        elif decay.trend == 'declining':
            score -= 0.2
        
        return score
    
    def _calculate_final_weight(self, rank: FactorRank) -> float:
        """计算最终权重"""
        base_weight = max(0.0, rank.combined_score)
        
        category_bonus = {
            FactorCategory.MOMENTUM: 1.0,
            FactorCategory.TREND: 1.0,
            FactorCategory.MEAN_REVERSION: 0.9,
            FactorCategory.VOLUME: 0.85,
            FactorCategory.VOLATILITY: 0.8,
            FactorCategory.MARKET_FLOW: 0.9,
            FactorCategory.STRENGTH: 0.85,
            FactorCategory.PATTERN: 0.75
        }
        
        bonus = category_bonus.get(rank.category, 1.0)
        
        decay_penalty = 0.9 if rank.decay_rank > len(category_bonus) else 1.0
        
        return base_weight * bonus * decay_penalty
    
    def select_top_factors(
        self, 
        factor_values: Dict[str, float], 
        forward_returns: Dict[str, float],
        top_n: int = 10
    ) -> Dict[str, float]:
        """
        选择最优因子
        
        Args:
            factor_values: 当前因子值
            forward_returns: 未来收益
            top_n: 选择数量
            
        Returns:
            选中的因子及其权重
        """
        ranks = self.rank_factors(factor_values, forward_returns, top_n)
        
        selected = {r.name: r.final_weight for r in ranks if r.is_selected}
        
        total_weight = sum(selected.values())
        if total_weight > 0:
            selected = {k: v / total_weight for k, v in selected.items()}
        
        logger.info(f"[FactorSelector] Selected {len(selected)} factors, total weight: {total_weight:.3f}")
        
        return selected
    
    def update_history(
        self, 
        factor_name: str, 
        factor_value: float, 
        forward_return: float
    ):
        """更新历史数据"""
        self.historical_ic[factor_name].append(factor_value)
        self.historical_returns[factor_name].append(forward_return)
        
        max_history = 100
        if len(self.historical_ic[factor_name]) > max_history:
            self.historical_ic[factor_name] = self.historical_ic[factor_name][-max_history:]
            self.historical_returns[factor_name] = self.historical_returns[factor_name][-max_history:]
    
    def get_factor_report(self, factor_name: str) -> Dict:
        """获取因子评估报告"""
        ic_stats = self.calculate_ic_statistics(factor_name)
        decay = self.calculate_decay(factor_name)
        
        factor_info = self.engine.get_factor_info(factor_name)
        
        return {
            'name': factor_name,
            'category': factor_info['category'].value if factor_info else 'unknown',
            'ic_statistics': {
                'mean': ic_stats.ic_mean if ic_stats else None,
                'std': ic_stats.ic_std if ic_stats else None,
                'count': ic_stats.ic_count if ic_stats else None,
                't_stat': ic_stats.t_stat if ic_stats else None,
                'p_value': ic_stats.p_value if ic_stats else None,
                'is_significant': ic_stats.is_significant if ic_stats else False
            },
            'decay': {
                'current_ic': decay.current_ic if decay else None,
                'historical_ic': decay.historical_ic if decay else None,
                'decay_rate': decay.decay_rate if decay else None,
                'trend': decay.trend if decay else None,
                'recommendation': decay.recommendation if decay else None
            } if decay else None
        }
    
    def get_selected_factors_summary(self) -> Dict:
        """获取当前选中因子汇总"""
        summary = {
            'total_factors_tracked': len(self.historical_ic),
            'ic_history_counts': {k: len(v) for k, v in self.historical_ic.items()},
            'categories': {}
        }
        
        for category in FactorCategory:
            factors = self.engine.get_factors_by_category(category)
            summary['categories'][category.value] = {
                'count': len(factors),
                'factors': factors
            }
        
        return summary


# 全局实例
_factor_selector: Optional[FactorSelector] = None


def get_factor_selector() -> FactorSelector:
    """获取全局因子选择器实例"""
    global _factor_selector
    if _factor_selector is None:
        from .base_factors import factor_engine
        _factor_selector = FactorSelector(factor_engine)
    return _factor_selector


def select_best_factors(
    factor_values: Dict[str, float], 
    forward_returns: Dict[str, float],
    top_n: int = 10
) -> Dict[str, float]:
    """便捷函数：选择最优因子"""
    selector = get_factor_selector()
    return selector.select_top_factors(factor_values, forward_returns, top_n)
