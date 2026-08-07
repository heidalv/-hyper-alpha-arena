"""
Meta-Learning Strategy Selector — 市场状态自适应策略选择 (F3-3)

基于当前市场状态自动选择最优策略子集：
- TrendingUp + LowVol → trend_follow (weight 1.3)
- Ranging → swing (weight 0.5, 缩仓)
- Crash → 全部暂停 (weight 0)
- HighVol → intraday/scalp (weight 0.7)

使用历史表现矩阵（MarketRegime × TradeNature）进行在线学习。
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# 市场状态 → 策略 nature 优先映射
REGIME_NATURE_PREFERENCE = {
    "trending_up": {
        "trend_follow": 1.3,
        "swing": 0.8,
        "position": 1.0,
        "intraday": 0.6,
        "scalp": 0.4,
    },
    "trending_down": {
        "trend_follow": 1.2,
        "swing": 0.7,
        "position": 0.8,
        "intraday": 0.7,
        "scalp": 0.5,
    },
    "ranging": {
        "swing": 1.0,
        "scalp": 0.5,
        "intraday": 0.5,
        "trend_follow": 0.3,
        "position": 0.3,
    },
    "crash": {
        "trend_follow": 0.1,
        "swing": 0.1,
        "position": 0.0,
        "intraday": 0.1,
        "scalp": 0.0,
    },
    "high_volatility": {
        "intraday": 1.0,
        "scalp": 1.2,
        "swing": 0.5,
        "trend_follow": 0.5,
        "position": 0.3,
    },
    "low_volatility": {
        "trend_follow": 1.2,
        "position": 1.3,
        "swing": 0.8,
        "intraday": 0.4,
        "scalp": 0.3,
    },
    "unknown": {
        "trend_follow": 0.6,
        "swing": 0.6,
        "position": 0.6,
        "intraday": 0.5,
        "scalp": 0.5,
    },
}


@dataclass
class StrategyRanking:
    """策略排序结果"""
    strategy_id: str
    template_id: str = ""
    symbol: str = ""
    tier: str = "mid"
    trade_nature: str = "swing"
    regime_score: float = 0.0       # 市场状态匹配分
    historical_score: float = 0.0   # 历史表现分
    composite_score: float = 0.0    # 综合分
    weight: float = 1.0             # 推荐权重
    recommendation: str = ""        # "strong_buy" | "buy" | "hold" | "pause"


@dataclass
class MetaSelection:
    """策略选择结果"""
    market_regime: str = "unknown"
    regime_confidence: float = 0.0
    volatility_ratio: float = 1.0
    selected_strategies: List[StrategyRanking] = field(default_factory=list)
    paused_strategies: List[str] = field(default_factory=list)
    summary: str = ""
    selected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class MetaStrategySelector:
    """市场状态自适应策略选择器

    选择逻辑:
    1. 确定当前市场状态 (regime)
    2. 查询各策略在该状态下的历史表现
    3. 按综合分排序: 0.5 × regime_match + 0.5 × historical
    4. 低于阈值的策略 → pause
    """

    MIN_WEIGHT_TO_ACTIVATE = 0.3
    HISTORICAL_WEIGHT = 0.5
    REGIME_WEIGHT = 0.5
    MIN_TRADES_FOR_HISTORICAL = 5

    def select(
        self,
        db: Session,
        market_context: Dict[str, Any],
        strategy_pool: List[Dict[str, Any]],
        active_strategy_ids: Optional[set] = None,
    ) -> MetaSelection:
        """基于当前市场状态选择最优策略子集

        Args:
            db: 数据库会话
            market_context: {
                "regime": str,
                "regime_confidence": float,
                "volatility_ratio": float,
                "adx": float,
            }
            strategy_pool: [{"strategy_id": str, "symbol": str, "tier": str, "trade_nature": str, ...}]
            active_strategy_ids: 当前活跃的策略 ID 集合（用于判断暂停）

        Returns:
            MetaSelection: 策略选择结果
        """
        regime = market_context.get("regime", "unknown")
        regime_conf = float(market_context.get("regime_confidence", 0.5))
        vol_ratio = float(market_context.get("volatility_ratio", 1.0))
        adx = float(market_context.get("adx", 0))

        # 细化市场状态
        refined_regime = self._refine_regime(regime, vol_ratio, adx)

        selection = MetaSelection(
            market_regime=refined_regime,
            regime_confidence=regime_conf,
            volatility_ratio=vol_ratio,
        )

        if refined_regime == "crash":
            selection.summary = "CRASH 状态 — 所有策略暂停"
            selection.paused_strategies = [
                s.get("strategy_id", "") for s in strategy_pool
            ]
            return selection

        preferences = REGIME_NATURE_PREFERENCE.get(
            refined_regime, REGIME_NATURE_PREFERENCE["unknown"]
        )

        rankings: List[StrategyRanking] = []
        for strat in strategy_pool:
            sid = str(strat.get("strategy_id", ""))
            nature = str(strat.get("trade_nature", "swing"))
            symbol = str(strat.get("symbol", strat.get("primary_symbol", "")))

            # 市场状态匹配分
            regime_score = preferences.get(nature, 0.5)

            # 历史表现分
            historical_score = self._get_historical_score(
                db, sid, refined_regime
            )

            # 综合分
            composite = (
                self.REGIME_WEIGHT * regime_score
                + self.HISTORICAL_WEIGHT * historical_score
            )

            weight = composite  # 1:1 映射为推荐权重

            if weight >= 0.8:
                recommendation = "strong_buy"
            elif weight >= self.MIN_WEIGHT_TO_ACTIVATE:
                recommendation = "buy"
            elif weight >= 0.15:
                recommendation = "hold"
            else:
                recommendation = "pause"

            rankings.append(StrategyRanking(
                strategy_id=sid,
                template_id=str(strat.get("template_id", "")),
                symbol=symbol,
                tier=str(strat.get("tier", "mid")),
                trade_nature=nature,
                regime_score=round(regime_score, 3),
                historical_score=round(historical_score, 3),
                composite_score=round(composite, 3),
                weight=round(weight, 3),
                recommendation=recommendation,
            ))

        # 按综合分降序
        rankings.sort(key=lambda r: r.composite_score, reverse=True)

        selection.selected_strategies = rankings
        selection.paused_strategies = [
            r.strategy_id for r in rankings if r.recommendation == "pause"
        ]

        # 生成摘要
        active = [r for r in rankings if r.recommendation != "pause"]
        paused = len(selection.paused_strategies)
        selection.summary = (
            f"Regime={refined_regime}(conf={regime_conf:.0%}) "
            f"vol_ratio={vol_ratio:.1f}x → "
            f"激活{len(active)}个策略, 暂停{paused}个"
        )

        logger.info(
            f"[MetaSelector] {selection.summary} "
            f"top3={[(r.strategy_id[:8], r.recommendation) for r in rankings[:3]]}"
        )
        return selection

    def _refine_regime(self, regime: str, vol_ratio: float, adx: float) -> str:
        """细化市场状态分类"""
        regime_lower = regime.lower()

        # Crash 优先
        if "crash" in regime_lower:
            return "crash"

        # 高波动独立分类
        if vol_ratio >= 2.0:
            return "high_volatility"

        # 低波动
        if vol_ratio <= 0.5 and adx < 20:
            return "low_volatility"

        # 标准分类
        for key in ("trending_up", "trending_down", "ranging"):
            if key in regime_lower:
                return key

        return "unknown"

    def _get_historical_score(
        self, db: Session, strategy_id: str, regime: str
    ) -> float:
        """从 StrategyMemory 获取策略在某市场状态下的历史表现分"""
        try:
            from backend.database.models import StrategyMemory
            memory = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()
            if not memory or not memory.performance_by_regime:
                return 0.5  # 无历史数据，返回中性分

            perf = memory.performance_by_regime
            if regime not in perf:
                return 0.5

            regime_stats = perf[regime]
            trades = regime_stats.get("trades", 0)
            if trades < self.MIN_TRADES_FOR_HISTORICAL:
                return 0.5

            wins = regime_stats.get("wins", 0)
            total_pnl = regime_stats.get("total_pnl", 0)

            # 计算历史表现分
            wr = wins / max(trades, 1)
            avg_pnl = total_pnl / max(trades, 1)

            # 归一化到 [0, 1]
            wr_score = min(1.0, max(0.0, wr))
            pnl_score = min(1.0, max(0.0, 0.5 + avg_pnl * 10))

            return round(0.6 * wr_score + 0.4 * pnl_score, 3)
        except Exception as e:
            logger.debug(f"[MetaSelector] 历史分查询失败 {strategy_id}: {e}")
            return 0.5

    def get_regime_strategy_map(
        self, db: Session, strategies: List[Dict[str, Any]]
    ) -> Dict[str, List[StrategyRanking]]:
        """为每种市场状态预计算最佳策略映射（离线计算）"""
        map_result: Dict[str, List[StrategyRanking]] = defaultdict(list)

        for regime in REGIME_NATURE_PREFERENCE:
            context = {
                "regime": regime,
                "regime_confidence": 1.0,
                "volatility_ratio": 1.0,
                "adx": 20,
            }
            selection = self.select(db, context, strategies)
            map_result[regime] = selection.selected_strategies

        return dict(map_result)


# 模块级单例
meta_selector = MetaStrategySelector()
