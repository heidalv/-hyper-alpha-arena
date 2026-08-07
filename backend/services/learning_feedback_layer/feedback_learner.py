"""
Feedback Learner - 学习反馈系统

从交易结果中学习并生成改进建议：
1. 模式识别
2. 因子有效性分析
3. 市场状态学习
4. AI提示优化

Author: Hyper-Alpha-Arena
"""

import logging
import warnings
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import numpy as np

warnings.warn(
    "FeedbackLearner 已弃用，功能已由 UnifiedLearningService + trade_memory_miner 替代。"
    "请勿在新代码中使用此模块。",
    DeprecationWarning,
    stacklevel=2,
)

logger = logging.getLogger(__name__)


class InsightType(Enum):
    """洞察类型"""
    FACTOR_PERFORMANCE = "factor_performance"  # 因子表现
    MARKET_REGIME = "market_regime"  # 市场状态
    ENTRY_PATTERN = "entry_pattern"  # 入场模式
    EXIT_PATTERN = "exit_pattern"  # 出场模式
    RISK_PATTERN = "risk_pattern"  # 风险模式
    TIMING_PATTERN = "timing_pattern"  # 时机模式


@dataclass
class LearningInsight:
    """学习洞察"""
    insight_type: InsightType
    title: str
    description: str
    evidence: List[str]
    recommendation: str
    confidence: float
    supporting_trades: int
    created_at: datetime
    applicable: bool = True


@dataclass
class LearningRecommendation:
    """学习建议"""
    category: str
    priority: str  # high/medium/low
    action: str
    rationale: str
    expected_impact: str
    implementation: str


class FeedbackLearner:
    """
    反馈学习器
    
    分析交易结果，提取模式，生成改进建议
    """
    
    def __init__(self):
        self.insights: List[LearningInsight] = []
        self.recommendations: List[LearningRecommendation] = []
        
        self.trade_patterns: Dict[str, List[Dict]] = defaultdict(list)
        self.factor_performance: Dict[str, Dict] = {}
        self.regime_performance: Dict[str, Dict] = {}
        
        self.min_samples = 5
        self.learning_window_days = 30
    
    def analyze_trades(
        self,
        trades: List[Dict],
        reviews: List[Dict],
        factor_data: Optional[Dict] = None
    ) -> Tuple[List[LearningInsight], List[LearningRecommendation]]:
        """
        分析交易并生成学习洞察
        
        Args:
            trades: 交易记录
            reviews: 复盘记录
            factor_data: 因子数据
            
        Returns:
            (洞察列表, 建议列表)
        """
        logger.info(f"[FeedbackLearner] Analyzing {len(trades)} trades")
        
        self._analyze_factor_performance(trades, factor_data)
        self._analyze_regime_performance(trades)
        self._analyze_entry_patterns(trades, reviews)
        self._analyze_exit_patterns(trades, reviews)
        self._analyze_risk_patterns(trades, reviews)
        self._analyze_timing_patterns(trades, reviews)
        
        self._generate_insights()
        self._generate_recommendations()
        
        logger.info(f"[FeedbackLearner] Generated {len(self.insights)} insights and {len(self.recommendations)} recommendations")
        
        return self.insights, self.recommendations
    
    def _analyze_factor_performance(
        self,
        trades: List[Dict],
        factor_data: Optional[Dict]
    ):
        """分析因子表现"""
        if not factor_data:
            return
        
        for trade in trades:
            trade_id = trade.get('id') or trade.get('trade_id')
            factors = factor_data.get(trade_id, {})
            outcome = trade.get('pnl_pct', 0)
            
            for factor, value in factors.items():
                if factor not in self.factor_performance:
                    self.factor_performance[factor] = {
                        'values': [],
                        'outcomes': [],
                        'positive_trades': [],
                        'negative_trades': []
                    }
                
                self.factor_performance[factor]['values'].append(value)
                self.factor_performance[factor]['outcomes'].append(outcome)
                
                if outcome > 0:
                    self.factor_performance[factor]['positive_trades'].append(value)
                else:
                    self.factor_performance[factor]['negative_trades'].append(value)
    
    def _analyze_regime_performance(self, trades: List[Dict]):
        """分析市场状态表现"""
        for trade in trades:
            regime = trade.get('market_regime', 'unknown')
            outcome = trade.get('pnl_pct', 0)
            
            if regime not in self.regime_performance:
                self.regime_performance[regime] = {
                    'trades': [],
                    'wins': 0,
                    'losses': 0,
                    'pnl': 0,
                    'avg_pnl': 0
                }
            
            self.regime_performance[regime]['trades'].append(trade)
            self.regime_performance[regime]['pnl'] += outcome
            if outcome > 0:
                self.regime_performance[regime]['wins'] += 1
            else:
                self.regime_performance[regime]['losses'] += 1
        
        for regime, data in self.regime_performance.items():
            data['avg_pnl'] = data['pnl'] / len(data['trades']) if data['trades'] else 0
            data['win_rate'] = data['wins'] / len(data['trades']) if data['trades'] else 0
    
    def _analyze_entry_patterns(
        self,
        trades: List[Dict],
        reviews: List[Dict]
    ):
        """分析入场模式"""
        winning_trades = [t for t in trades if t.get('pnl_pct', 0) > 0]
        losing_trades = [t for t in trades if t.get('pnl_pct', 0) <= 0]
        
        if len(winning_trades) < self.min_samples:
            return
        
        for trade in trades:
            pattern = self._extract_entry_pattern(trade)
            if pattern:
                self.trade_patterns[pattern].append(trade)
    
    def _extract_entry_pattern(self, trade: Dict) -> Optional[str]:
        """提取入场模式"""
        patterns = []
        
        ai_confidence = trade.get('ai_confidence', 0)
        if ai_confidence >= 0.8:
            patterns.append("high_confidence")
        elif ai_confidence < 0.5:
            patterns.append("low_confidence")
        
        market_regime = trade.get('market_regime', '')
        if market_regime:
            patterns.append(f"regime_{market_regime}")
        
        holding_period = trade.get('holding_hours', 0)
        if holding_period < 1:
            patterns.append("scalp")
        elif holding_period > 48:
            patterns.append("swing")
        
        if patterns:
            return "_".join(patterns)
        return None
    
    def _analyze_exit_patterns(
        self,
        trades: List[Dict],
        reviews: List[Dict]
    ):
        """分析出场模式"""
        for trade in trades:
            exit_type = trade.get('exit_type', 'unknown')
            outcome = trade.get('pnl_pct', 0)
            
            if exit_type not in self.trade_patterns:
                self.trade_patterns[exit_type] = []
            self.trade_patterns[exit_type].append(trade)
    
    def _analyze_risk_patterns(
        self,
        trades: List[Dict],
        reviews: List[Dict]
    ):
        """分析风险模式"""
        for trade in trades:
            initial_stop = trade.get('initial_stop_pct', 0)
            actual_stop = trade.get('actual_stop_pct', 0)
            
            if initial_stop and actual_stop:
                stop_quality = "tight" if abs(actual_stop) <= abs(initial_stop) else "loose"
                pattern = f"stop_{stop_quality}"
                
                if pattern not in self.trade_patterns:
                    self.trade_patterns[pattern] = []
                self.trade_patterns[pattern].append(trade)
    
    def _analyze_timing_patterns(
        self,
        trades: List[Dict],
        reviews: List[Dict]
    ):
        """分析时机模式"""
        for trade in trades:
            entry_hour = trade.get('entry_hour')
            if entry_hour is not None:
                hour_period = "asian" if 0 <= entry_hour < 8 else "european" if 8 <= entry_hour < 16 else "american"
                pattern = f"time_{hour_period}"
                
                if pattern not in self.trade_patterns:
                    self.trade_patterns[pattern] = []
                self.trade_patterns[pattern].append(trade)
    
    def _generate_insights(self):
        """生成洞察"""
        self.insights.clear()
        
        self._generate_factor_insights()
        self._generate_regime_insights()
        self._generate_pattern_insights()
    
    def _generate_factor_insights(self):
        """生成因子洞察"""
        for factor, data in self.factor_performance.items():
            if len(data['outcomes']) < self.min_samples:
                continue
            
            positive_values = data['positive_trades']
            negative_values = data['negative_trades']
            
            if not positive_values or not negative_values:
                continue
            
            pos_mean = np.mean(positive_values)
            neg_mean = np.mean(negative_values)
            
            if pos_mean > neg_mean * 1.2:
                confidence = min(0.9, len(data['outcomes']) / 20)
                
                self.insights.append(LearningInsight(
                    insight_type=InsightType.FACTOR_PERFORMANCE,
                    title=f"{factor} 高值有利于盈利",
                    description=f"当 {factor} 处于较高水平时，盈利概率显著提高",
                    evidence=[
                        f"盈利交易平均 {factor}: {pos_mean:.4f}",
                        f"亏损交易平均 {factor}: {neg_mean:.4f}",
                        f"样本数: {len(data['outcomes'])}"
                    ],
                    recommendation=f"考虑在 {factor} 高于 {pos_mean:.4f} 时增加仓位",
                    confidence=confidence,
                    supporting_trades=len(positive_values),
                    created_at=datetime.now(timezone.utc)
                ))
            elif neg_mean > pos_mean * 1.2:
                confidence = min(0.9, len(data['outcomes']) / 20)
                
                self.insights.append(LearningInsight(
                    insight_type=InsightType.FACTOR_PERFORMANCE,
                    title=f"{factor} 低值有利于盈利",
                    description=f"当 {factor} 处于较低水平时，盈利概率显著提高",
                    evidence=[
                        f"盈利交易平均 {factor}: {pos_mean:.4f}",
                        f"亏损交易平均 {factor}: {neg_mean:.4f}"
                    ],
                    recommendation=f"考虑在 {factor} 低于 {neg_mean:.4f} 时增加仓位",
                    confidence=confidence,
                    supporting_trades=len(positive_values),
                    created_at=datetime.now(timezone.utc)
                ))
    
    def _generate_regime_insights(self):
        """生成市场状态洞察"""
        for regime, data in self.regime_performance.items():
            if len(data['trades']) < self.min_samples:
                continue
            
            win_rate = data['win_rate']
            avg_pnl = data['avg_pnl']
            
            if win_rate >= 0.6 and avg_pnl > 1:
                self.insights.append(LearningInsight(
                    insight_type=InsightType.MARKET_REGIME,
                    title=f"在 {regime} 状态下表现优异",
                    description=f"该市场状态下胜率和盈利率都较高",
                    evidence=[
                        f"胜率: {win_rate:.1%}",
                        f"平均盈亏: {avg_pnl:.2f}%",
                        f"交易数: {len(data['trades'])}"
                    ],
                    recommendation=f"在 {regime} 状态时可以考虑增加交易频率和仓位",
                    confidence=min(0.9, len(data['trades']) / 30),
                    supporting_trades=data['wins'],
                    created_at=datetime.now(timezone.utc)
                ))
            elif win_rate <= 0.4 and avg_pnl < -1:
                self.insights.append(LearningInsight(
                    insight_type=InsightType.MARKET_REGIME,
                    title=f"在 {regime} 状态下表现不佳",
                    description=f"该市场状态下应该减少交易或避免交易",
                    evidence=[
                        f"胜率: {win_rate:.1%}",
                        f"平均盈亏: {avg_pnl:.2f}%",
                        f"交易数: {len(data['trades'])}"
                    ],
                    recommendation=f"在 {regime} 状态时应该减少交易或观望",
                    confidence=min(0.9, len(data['trades']) / 30),
                    supporting_trades=len(data['trades']),
                    created_at=datetime.now(timezone.utc)
                ))
    
    def _generate_pattern_insights(self):
        """生成模式洞察"""
        for pattern, trades in self.trade_patterns.items():
            if len(trades) < self.min_samples:
                continue
            
            wins = [t for t in trades if t.get('pnl_pct', 0) > 0]
            win_rate = len(wins) / len(trades) if trades else 0
            avg_pnl = np.mean([t.get('pnl_pct', 0) for t in trades])
            
            if win_rate >= 0.65 and avg_pnl > 1.5:
                self.insights.append(LearningInsight(
                    insight_type=InsightType.ENTRY_PATTERN,
                    title=f"盈利模式: {pattern}",
                    description=f"该交易模式具有较高的盈利概率",
                    evidence=[
                        f"模式交易数: {len(trades)}",
                        f"胜率: {win_rate:.1%}",
                        f"平均盈亏: {avg_pnl:.2f}%"
                    ],
                    recommendation=f"可以更频繁地使用 {pattern} 模式",
                    confidence=min(0.85, len(trades) / 20),
                    supporting_trades=len(wins),
                    created_at=datetime.now(timezone.utc)
                ))
    
    def _generate_recommendations(self):
        """生成建议"""
        self.recommendations.clear()
        
        for insight in self.insights:
            if insight.confidence < 0.5:
                continue
            
            if insight.insight_type == InsightType.FACTOR_PERFORMANCE:
                self._generate_factor_recommendation(insight)
            elif insight.insight_type == InsightType.MARKET_REGIME:
                self._generate_regime_recommendation(insight)
            elif insight.insight_type == InsightType.ENTRY_PATTERN:
                self._generate_pattern_recommendation(insight)
    
    def _generate_factor_recommendation(self, insight: LearningInsight):
        """生成因子建议"""
        self.recommendations.append(LearningRecommendation(
            category="因子优化",
            priority="high" if insight.confidence > 0.7 else "medium",
            action=f"调整 {insight.title.split()[0]} 因子权重",
            rationale=insight.description,
            expected_impact="提升因子预测能力",
            implementation=f"在 factor_weighting.py 中调整 {insight.title.split()[0]} 的权重配置"
        ))
    
    def _generate_regime_recommendation(self, insight: LearningInsight):
        """生成市场状态建议"""
        is_positive = "盈利率" in insight.description or "增加" in insight.recommendation
        
        self.recommendations.append(LearningRecommendation(
            category="交易策略",
            priority="high" if insight.confidence > 0.7 else "medium",
            action=f"{'加强' if is_positive else '减少'} {insight.title.split()[1]} 状态的交易",
            rationale=insight.description,
            expected_impact=f"{'提升' if is_positive else '降低'} 整体交易表现",
            implementation=f"在 market_regime_service.py 中调整该状态的交易参数"
        ))
    
    def _generate_pattern_recommendation(self, insight: LearningInsight):
        """生成模式建议"""
        self.recommendations.append(LearningRecommendation(
            category="模式交易",
            priority="medium",
            action=f"识别并利用 {insight.title.split()[1]} 盈利模式",
            rationale=insight.description,
            expected_impact="增加盈利交易比例",
            implementation=f"在 ai_decision_service.py 中增加对 {insight.title.split()[1]} 模式的识别"
        ))
    
    def get_insights_by_type(self, insight_type: InsightType) -> List[LearningInsight]:
        """按类型获取洞察"""
        return [i for i in self.insights if i.insight_type == insight_type]
    
    def get_top_insights(self, limit: int = 5) -> List[LearningInsight]:
        """获取最佳洞察"""
        sorted_insights = sorted(
            self.insights,
            key=lambda x: (x.confidence, x.supporting_trades),
            reverse=True
        )
        return sorted_insights[:limit]
    
    def get_actionable_recommendations(self, priority: Optional[str] = None) -> List[LearningRecommendation]:
        """获取可操作的建议"""
        if priority:
            return [r for r in self.recommendations if r.priority == priority]
        return self.recommendations
    
    def export_learning_report(self) -> Dict:
        """导出学习报告"""
        return {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'insights_count': len(self.insights),
            'recommendations_count': len(self.recommendations),
            'top_insights': [
                {
                    'type': i.insight_type.value,
                    'title': i.title,
                    'confidence': i.confidence,
                    'supporting_trades': i.supporting_trades,
                    'recommendation': i.recommendation
                }
                for i in self.get_top_insights(10)
            ],
            'actionable_recommendations': [
                {
                    'category': r.category,
                    'priority': r.priority,
                    'action': r.action,
                    'rationale': r.rationale,
                    'implementation': r.implementation
                }
                for r in self.get_actionable_recommendations()
            ],
            'factor_performance_summary': {
                k: {
                    'sample_count': len(v['outcomes']),
                    'avg_positive': np.mean(v['positive_trades']) if v['positive_trades'] else 0,
                    'avg_negative': np.mean(v['negative_trades']) if v['negative_trades'] else 0
                }
                for k, v in self.factor_performance.items()
            },
            'regime_performance_summary': {
                k: {
                    'trades': len(v['trades']),
                    'win_rate': v['win_rate'],
                    'avg_pnl': v['avg_pnl']
                }
                for k, v in self.regime_performance.items()
            }
        }
    
    def clear_history(self):
        """清除历史数据"""
        self.insights.clear()
        self.recommendations.clear()
        self.trade_patterns.clear()
        self.factor_performance.clear()
        self.regime_performance.clear()
        logger.info("[FeedbackLearner] History cleared")


# 全局实例
_feedback_learner: Optional[FeedbackLearner] = None


def get_feedback_learner() -> FeedbackLearner:
    """获取全局反馈学习器"""
    global _feedback_learner
    if _feedback_learner is None:
        _feedback_learner = FeedbackLearner()
    return _feedback_learner


def analyze_and_learn(
    trades: List[Dict],
    reviews: List[Dict],
    factor_data: Optional[Dict] = None
) -> Tuple[List[LearningInsight], List[LearningRecommendation]]:
    """便捷函数：分析交易并学习"""
    learner = get_feedback_learner()
    return learner.analyze_trades(trades, reviews, factor_data)
