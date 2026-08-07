"""
Trade Review - 交易复盘系统

提供交易复盘功能：
1. 交易评分
2. 维度分析
3. 问题识别
4. 改进建议

Author: Hyper-Alpha-Arena
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
import numpy as np

logger = logging.getLogger(__name__)


class ReviewStatus(Enum):
    """复盘状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FLAGGED = "flagged"


class ReviewDimension(Enum):
    """复盘维度"""
    ENTRY_QUALITY = "entry_quality"  # 入场质量
    EXIT_QUALITY = "exit_quality"  # 出场质量
    RISK_MANAGEMENT = "risk_management"  # 风险管理
    MARKET_REGIME = "market_regime"  # 市场状态
    TIMING = "timing"  # 时机把握
    POSITION_SIZING = "position_sizing"  # 仓位管理
    EMOTION_CONTROL = "emotion_control"  # 情绪控制
    DISCIPLINE = "discipline"  # 纪律执行


@dataclass
class DimensionScore:
    """维度评分"""
    dimension: ReviewDimension
    score: float  # 0-10
    weight: float  # 权重
    weighted_score: float  # 加权分数
    comments: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


@dataclass
class TradeReview:
    """交易复盘结果"""
    trade_id: str
    symbol: str
    side: str  # long/short
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    pnl_pct: float
    status: ReviewStatus
    
    # 复盘维度评分
    dimensions: Dict[ReviewDimension, DimensionScore] = field(default_factory=dict)
    
    # 综合评分
    overall_score: float = 0.0
    max_score: float = 10.0
    
    # 交易详情
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    initial_stop_pct: Optional[float] = None
    actual_stop_pct: Optional[float] = None
    
    # 市场状态
    market_regime_entry: Optional[str] = None
    market_regime_exit: Optional[str] = None
    regime_change: bool = False
    
    # AI决策信息
    ai_confidence: Optional[float] = None
    ai_reasoning: Optional[str] = None
    factor_weights: Dict[str, float] = field(default_factory=dict)
    
    # 复盘结论
    conclusion: str = ""
    lessons_learned: List[str] = field(default_factory=list)
    improvement_actions: List[str] = field(default_factory=list)
    
    # 元数据
    reviewed_at: Optional[datetime] = None
    reviewer: str = "auto"


class TradeReviewer:
    """
    交易复盘器
    
    对每笔交易进行多维度复盘分析，
    识别问题并提供改进建议
    """
    
    def __init__(self):
        self.reviews: Dict[str, TradeReview] = {}
        self.avg_scores: Dict[ReviewDimension, float] = {}
        
        self._init_scoring_criteria()
    
    def _init_scoring_criteria(self):
        """初始化评分标准"""
        self.dimension_weights = {
            ReviewDimension.ENTRY_QUALITY: 0.20,
            ReviewDimension.EXIT_QUALITY: 0.20,
            ReviewDimension.RISK_MANAGEMENT: 0.20,
            ReviewDimension.MARKET_REGIME: 0.15,
            ReviewDimension.TIMING: 0.10,
            ReviewDimension.POSITION_SIZING: 0.10,
            ReviewDimension.EMOTION_CONTROL: 0.025,
            ReviewDimension.DISCIPLINE: 0.025,
        }
        
        self.score_thresholds = {
            'excellent': 8.5,
            'good': 7.0,
            'acceptable': 5.0,
            'poor': 3.0,
        }
    
    def review_trade(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        entry_time: datetime,
        exit_time: datetime,
        market_data: Optional[Dict] = None,
        ai_data: Optional[Dict] = None,
        **kwargs
    ) -> TradeReview:
        """
        复盘单笔交易
        
        Args:
            trade_id: 交易ID
            symbol: 交易品种
            side: 方向
            entry_price: 入场价格
            exit_price: 出场价格
            quantity: 数量
            entry_time: 入场时间
            exit_time: 出场时间
            market_data: 市场数据
            ai_data: AI决策数据
            
        Returns:
            TradeReview对象
        """
        pnl = (exit_price - entry_price) * quantity if side == 'long' else (entry_price - exit_price) * quantity
        pnl_pct = (exit_price - entry_price) / entry_price * 100 if side == 'long' else (entry_price - exit_price) / entry_price * 100
        
        review = TradeReview(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=quantity,
            entry_time=entry_time,
            exit_time=exit_time,
            pnl=pnl,
            pnl_pct=pnl_pct,
            status=ReviewStatus.IN_PROGRESS,
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            initial_stop_pct=kwargs.get('initial_stop_pct'),
            actual_stop_pct=kwargs.get('actual_stop_pct'),
            market_regime_entry=market_data.get('regime_entry') if market_data else None,
            market_regime_exit=market_data.get('regime_exit') if market_data else None,
            regime_change=market_data.get('regime_changed', False) if market_data else False,
            ai_confidence=ai_data.get('confidence') if ai_data else None,
            ai_reasoning=ai_data.get('reasoning') if ai_data else None,
            factor_weights=ai_data.get('factor_weights', {}) if ai_data else {},
            reviewed_at=datetime.now(timezone.utc)
        )
        
        self._calculate_all_dimensions(review, market_data, ai_data)
        self._calculate_overall_score(review)
        self._generate_conclusion(review)
        
        review.status = ReviewStatus.COMPLETED
        self.reviews[trade_id] = review
        
        return review
    
    def _calculate_all_dimensions(
        self,
        review: TradeReview,
        market_data: Optional[Dict],
        ai_data: Optional[Dict]
    ):
        """计算所有维度评分"""
        
        # 入场质量
        review.dimensions[ReviewDimension.ENTRY_QUALITY] = self._score_entry_quality(review, ai_data)
        
        # 出场质量
        review.dimensions[ReviewDimension.EXIT_QUALITY] = self._score_exit_quality(review)
        
        # 风险管理
        review.dimensions[ReviewDimension.RISK_MANAGEMENT] = self._score_risk_management(review)
        
        # 市场状态
        review.dimensions[ReviewDimension.MARKET_REGIME] = self._score_market_regime(review)
        
        # 时机把握
        review.dimensions[ReviewDimension.TIMING] = self._score_timing(review, market_data)
        
        # 仓位管理
        review.dimensions[ReviewDimension.POSITION_SIZING] = self._score_position_sizing(review)
        
        # 情绪控制
        review.dimensions[ReviewDimension.EMOTION_CONTROL] = self._score_emotion_control(review)
        
        # 纪律执行
        review.dimensions[ReviewDimension.DISCIPLINE] = self._score_discipline(review)
    
    def _score_entry_quality(self, review: TradeReview, ai_data: Optional[Dict]) -> DimensionScore:
        """入场质量评分"""
        score = 5.0
        comments = []
        issues = []
        suggestions = []
        
        # AI置信度
        if review.ai_confidence and review.ai_confidence >= 0.8:
            score += 2.0
            comments.append(f"AI高置信度信号: {review.ai_confidence:.0%}")
        elif review.ai_confidence and review.ai_confidence < 0.5:
            score -= 2.0
            issues.append("AI置信度过低仍执行交易")
            suggestions.append("低置信度时应减少仓位或观望")
        
        # 因子权重
        if review.factor_weights:
            top_factors = sorted(review.factor_weights.items(), key=lambda x: x[1], reverse=True)[:3]
            if top_factors[0][1] > 0.3:
                score += 1.5
                comments.append(f"主要因子: {top_factors[0][0]} ({top_factors[0][1]:.0%})")
        
        # 入场价格合理性
        if review.pnl_pct > 5:
            score += 1.0
            comments.append("入场后快速盈利")
        elif review.pnl_pct < -3:
            score -= 1.5
            issues.append("入场即被套")
            suggestions.append("等待更清晰的信号再入场")
        
        score = max(0, min(10, score))
        
        return DimensionScore(
            dimension=ReviewDimension.ENTRY_QUALITY,
            score=score,
            weight=self.dimension_weights[ReviewDimension.ENTRY_QUALITY],
            weighted_score=score * self.dimension_weights[ReviewDimension.ENTRY_QUALITY],
            comments=comments,
            issues=issues,
            suggestions=suggestions
        )
    
    def _score_exit_quality(self, review: TradeReview) -> DimensionScore:
        """出场质量评分"""
        score = 5.0
        comments = []
        issues = []
        suggestions = []
        
        # 盈利交易
        if review.pnl > 0:
            if review.actual_stop_pct and review.actual_stop_pct < 0:
                score += 2.0
                comments.append("成功移动止损保护盈利")
            
            # 是否吃到大部分趋势
            if review.pnl_pct > 10:
                score += 1.5
                comments.append(f"捕捉大趋势: +{review.pnl_pct:.1f}%")
            elif review.pnl_pct > 0 and review.pnl_pct < 2:
                score -= 1.0
                issues.append("盈利过早了结")
                suggestions.append("考虑使用移动止损让利润奔跑")
        
        # 亏损交易
        else:
            if review.actual_stop_pct and abs(review.actual_stop_pct) <= abs(review.initial_stop_pct or 0.05):
                score += 1.0
                comments.append("在止损位正常退出")
            else:
                score -= 2.0
                issues.append("亏损超出预期")
                suggestions.append("严格执行止损纪律")
        
        score = max(0, min(10, score))
        
        return DimensionScore(
            dimension=ReviewDimension.EXIT_QUALITY,
            score=score,
            weight=self.dimension_weights[ReviewDimension.EXIT_QUALITY],
            weighted_score=score * self.dimension_weights[ReviewDimension.EXIT_QUALITY],
            comments=comments,
            issues=issues,
            suggestions=suggestions
        )
    
    def _score_risk_management(self, review: TradeReview) -> DimensionScore:
        """风险管理评分"""
        score = 5.0
        comments = []
        issues = []
        suggestions = []
        
        # 止损设置
        if review.initial_stop_pct:
            if review.initial_stop_pct <= 0.03:
                score += 1.5
                comments.append(f"合理止损设置: {review.initial_stop_pct:.1%}")
            elif review.initial_stop_pct > 0.08:
                score -= 1.0
                issues.append("止损过宽")
                suggestions.append("考虑收窄止损以提高风险回报比")
        
        # 风险回报比
        if review.initial_stop_pct and review.pnl_pct > 0:
            rr = review.pnl_pct / (review.initial_stop_pct * 100)
            if rr >= 2.0:
                score += 2.0
                comments.append(f"优秀风险回报比: 1:{rr:.1f}")
            elif rr < 1.0:
                score -= 1.5
                issues.append(f"风险回报比过低: 1:{rr:.1f}")
                suggestions.append("只接受1:2以上的机会")
        
        score = max(0, min(10, score))
        
        return DimensionScore(
            dimension=ReviewDimension.RISK_MANAGEMENT,
            score=score,
            weight=self.dimension_weights[ReviewDimension.RISK_MANAGEMENT],
            weighted_score=score * self.dimension_weights[ReviewDimension.RISK_MANAGEMENT],
            comments=comments,
            issues=issues,
            suggestions=suggestions
        )
    
    def _score_market_regime(self, review: TradeReview) -> DimensionScore:
        """市场状态评分"""
        score = 5.0
        comments = []
        issues = []
        suggestions = []
        
        if review.market_regime_entry:
            favorable_regimes = ['breakout', 'continuation']
            unfavorable_regimes = ['noise', 'exhaustion']
            
            if review.market_regime_entry in favorable_regimes:
                score += 2.0
                comments.append(f"有利市场状态: {review.market_regime_entry}")
            elif review.market_regime_entry in unfavorable_regimes:
                score -= 2.0
                issues.append(f"不利市场状态入场: {review.market_regime_entry}")
                suggestions.append("在震荡或衰竭市场减少交易")
            
            if review.regime_change:
                if review.pnl > 0:
                    score += 1.0
                    comments.append("成功把握状态转换")
                else:
                    score -= 1.0
                    issues.append("状态转换时未能及时调整")
        
        score = max(0, min(10, score))
        
        return DimensionScore(
            dimension=ReviewDimension.MARKET_REGIME,
            score=score,
            weight=self.dimension_weights[ReviewDimension.MARKET_REGIME],
            weighted_score=score * self.dimension_weights[ReviewDimension.MARKET_REGIME],
            comments=comments,
            issues=issues,
            suggestions=suggestions
        )
    
    def _score_timing(self, review: TradeReview, market_data: Optional[Dict]) -> DimensionScore:
        """时机把握评分"""
        score = 5.0
        comments = []
        issues = []
        suggestions = []
        
        # 通过入场后价格走势判断
        if review.pnl_pct > 5:
            score += 2.0
            comments.append("精准把握入场时机")
        elif review.pnl_pct < -5:
            score -= 2.0
            issues.append("入场时机偏差较大")
            suggestions.append("等待更明确的信号确认")
        
        score = max(0, min(10, score))
        
        return DimensionScore(
            dimension=ReviewDimension.TIMING,
            score=score,
            weight=self.dimension_weights[ReviewDimension.TIMING],
            weighted_score=score * self.dimension_weights[ReviewDimension.TIMING],
            comments=comments,
            issues=issues,
            suggestions=suggestions
        )
    
    def _score_position_sizing(self, review: TradeReview) -> DimensionScore:
        """仓位管理评分"""
        score = 5.0
        comments = []
        issues = []
        suggestions = []
        
        # 基于置信度的仓位调整
        if review.ai_confidence and review.pnl > 0:
            if review.ai_confidence >= 0.8:
                comments.append("高置信度对应合理仓位")
            elif review.ai_confidence < 0.5:
                issues.append("低置信度时仓位可能偏大")
        
        score = max(0, min(10, score))
        
        return DimensionScore(
            dimension=ReviewDimension.POSITION_SIZING,
            score=score,
            weight=self.dimension_weights[ReviewDimension.POSITION_SIZING],
            weighted_score=score * self.dimension_weights[ReviewDimension.POSITION_SIZING],
            comments=comments,
            issues=issues,
            suggestions=suggestions
        )
    
    def _score_emotion_control(self, review: TradeReview) -> DimensionScore:
        """情绪控制评分"""
        score = 5.0
        comments = []
        issues = []
        suggestions = []
        
        # 假设正常执行的交易情绪控制良好
        comments.append("交易按计划执行")
        
        return DimensionScore(
            dimension=ReviewDimension.EMOTION_CONTROL,
            score=score,
            weight=self.dimension_weights[ReviewDimension.EMOTION_CONTROL],
            weighted_score=score * self.dimension_weights[ReviewDimension.EMOTION_CONTROL],
            comments=comments,
            issues=issues,
            suggestions=suggestions
        )
    
    def _score_discipline(self, review: TradeReview) -> DimensionScore:
        """纪律执行评分"""
        score = 5.0
        comments = []
        issues = []
        suggestions = []
        
        if review.actual_stop_pct and review.initial_stop_pct:
            if abs(review.actual_stop_pct - review.initial_stop_pct) < 0.01:
                score += 2.0
                comments.append("严格执行止损纪律")
            else:
                score -= 1.0
                issues.append("止损执行有偏差")
        
        return DimensionScore(
            dimension=ReviewDimension.DISCIPLINE,
            score=score,
            weight=self.dimension_weights[ReviewDimension.DISCIPLINE],
            weighted_score=score * self.dimension_weights[ReviewDimension.DISCIPLINE],
            comments=comments,
            issues=issues,
            suggestions=suggestions
        )
    
    def _calculate_overall_score(self, review: TradeReview):
        """计算综合评分"""
        total_weighted = sum(d.weighted_score for d in review.dimensions.values())
        review.overall_score = total_weighted * (review.max_score / 10)
    
    def _generate_conclusion(self, review: TradeReview):
        """生成复盘结论"""
        if review.overall_score >= self.score_thresholds['excellent']:
            review.conclusion = "优秀交易 - 各个方面都处理得很好"
            review.status = ReviewStatus.COMPLETED
        elif review.overall_score >= self.score_thresholds['good']:
            review.conclusion = "良好交易 - 可继续优化"
            review.status = ReviewStatus.COMPLETED
        elif review.overall_score >= self.score_thresholds['acceptable']:
            review.conclusion = "一般交易 - 存在改进空间"
            review.status = ReviewStatus.COMPLETED
        elif review.overall_score >= self.score_thresholds['poor']:
            review.conclusion = "需要改进的交易 - 建议复盘学习"
            review.status = ReviewStatus.FLAGGED
        else:
            review.conclusion = "问题交易 - 需要重点分析"
            review.status = ReviewStatus.FLAGGED
        
        for dim_score in review.dimensions.values():
            review.lessons_learned.extend(dim_score.comments)
            review.improvement_actions.extend(dim_score.suggestions[:2])
    
    def batch_review(
        self,
        trades: List[Dict],
        market_data_map: Optional[Dict] = None,
        ai_data_map: Optional[Dict] = None
    ) -> List[TradeReview]:
        """批量复盘"""
        reviews = []
        for trade in trades:
            trade_id = trade.get('id') or trade.get('trade_id')
            review = self.review_trade(
                trade_id=trade_id,
                symbol=trade['symbol'],
                side=trade['side'],
                entry_price=trade['entry_price'],
                exit_price=trade['exit_price'],
                quantity=trade['quantity'],
                entry_time=trade['entry_time'],
                exit_time=trade['exit_time'],
                market_data=market_data_map.get(trade_id) if market_data_map else None,
                ai_data=ai_data_map.get(trade_id) if ai_data_map else None,
                stop_loss=trade.get('stop_loss'),
                take_profit=trade.get('take_profit'),
                initial_stop_pct=trade.get('initial_stop_pct')
            )
            reviews.append(review)
        
        self._update_average_scores(reviews)
        return reviews
    
    def _update_average_scores(self, reviews: List[TradeReview]):
        """更新平均分数"""
        if not reviews:
            return
        
        for dim in ReviewDimension:
            scores = [r.dimensions.get(dim, DimensionScore(dim, 0, 1, 0)).score for r in reviews if r.dimensions.get(dim)]
            self.avg_scores[dim] = np.mean(scores) if scores else 0
    
    def get_review_summary(self) -> Dict:
        """获取复盘汇总"""
        if not self.reviews:
            return {'total_reviews': 0}
        
        scores = [r.overall_score for r in self.reviews.values()]
        pnl_values = [r.pnl for r in self.reviews.values()]
        
        return {
            'total_reviews': len(self.reviews),
            'avg_overall_score': np.mean(scores),
            'score_distribution': {
                'excellent': sum(1 for s in scores if s >= self.score_thresholds['excellent']),
                'good': sum(1 for s in scores if self.score_thresholds['good'] <= s < self.score_thresholds['excellent']),
                'acceptable': sum(1 for s in scores if self.score_thresholds['acceptable'] <= s < self.score_thresholds['good']),
                'poor': sum(1 for s in scores if s < self.score_thresholds['acceptable']),
            },
            'total_pnl': sum(pnl_values),
            'avg_pnl': np.mean(pnl_values),
            'win_rate': sum(1 for p in pnl_values if p > 0) / len(pnl_values) if pnl_values else 0,
            'dimension_averages': {dim.value: score for dim, score in self.avg_scores.items()}
        }
    
    def get_improvement_priorities(self) -> List[Tuple[str, float, List[str]]]:
        """获取改进优先级"""
        priorities = []
        
        for dim, avg_score in self.avg_scores.items():
            dim_score = self.dimensions.get(dim, DimensionScore(dim, 5, 0.1, 0))
            issues = [issue for d in self.reviews.values() 
                     if (d_dim := d.dimensions.get(dim)) for issue in d_dim.issues]
            
            priority_score = (10 - avg_score) * dim.value
            priorities.append((dim.value, priority_score, issues[:3]))
        
        priorities.sort(key=lambda x: x[1], reverse=True)
        return priorities


# 全局实例
_trade_reviewer: Optional[TradeReviewer] = None


def get_trade_reviewer() -> TradeReviewer:
    """获取全局交易复盘器"""
    global _trade_reviewer
    if _trade_reviewer is None:
        _trade_reviewer = TradeReviewer()
    return _trade_reviewer
