"""
test_phase5_adaptive_evolution — Phase 5 自适应策略进化升级单元测试

覆盖范围:
1. NSGAIIOptimizer — 多目标遗传优化 (非支配排序、拥挤度、Pareto前沿)
2. MarketRegimeClassifier — 市场状态分类 + 策略映射
3. StrategyHypothesisGenerator — LLM驱动策略假设生成
4. 集成测试
"""

import asyncio
import json
from unittest.mock import MagicMock, AsyncMock

import pytest
import pandas as pd
import numpy as np

from backend.services.genetic_optimizer import (
    GeneticOptimizer, Individual, EvolutionResult,
    NSGAIIOptimizer, MultiObjectiveIndividual, ParetoFront,
)
from backend.services.market_regime import (
    MarketRegimeClassifier, MarketRegime, RegimeClassification,
    REGIME_STRATEGY_MAP,
)
# [2026-09-02] strategy_hypothesis_generator 已下线（模块不存在），原第 4 节
# StrategyHypothesisGenerator 用例与两条 hypothesis 集成用例随之移除。本文件保留
# genetic_optimizer / market_regime（均为活代码）的用例——此前整个文件因这条
# import 收集失败，连 NSGA-II 与市场状态分类的用例一起失去保护。


# ════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════

def _make_klines(rows: int = 120, base_price: float = 100.0, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    closes = base_price + np.cumsum(np.random.randn(rows) * 0.5)
    return pd.DataFrame({
        'open': closes,
        'high': closes + np.abs(np.random.randn(rows)) * 0.3,
        'low': closes - np.abs(np.random.randn(rows)) * 0.3,
        'close': closes,
        'volume': 1000 + np.abs(np.random.randn(rows)) * 500,
    })


def _make_trending_up_klines() -> pd.DataFrame:
    """构造平滑上升趋势K线（零噪声，确保 TRENDING_UP 识别）"""
    rows = 120
    closes = 100 + np.linspace(0, 30, rows)
    return pd.DataFrame({
        'open': closes,
        'high': closes + 0.1,
        'low': closes - 0.1,
        'close': closes,
        'volume': np.full(rows, 1000.0),
    })


def _make_trending_down_klines() -> pd.DataFrame:
    """构造平滑下降趋势K线（零噪声，确保 TRENDING_DOWN 识别）"""
    rows = 120
    closes = 130 - np.linspace(0, 30, rows)
    return pd.DataFrame({
        'open': closes,
        'high': closes + 0.1,
        'low': closes - 0.1,
        'close': closes,
        'volume': np.full(rows, 1000.0),
    })


def _make_crash_klines() -> pd.DataFrame:
    """构造崩盘K线：剧烈下跌+高波动"""
    rows = 120
    closes = 100 - np.linspace(0, 25, rows) + np.random.randn(rows) * 2.0
    return pd.DataFrame({
        'open': closes,
        'high': closes + 3,
        'low': closes - 3,
        'close': closes,
        'volume': np.full(rows, 5000.0),
    })


def _make_ranging_klines() -> pd.DataFrame:
    """构造震荡K线：价格在窄幅区间内随机波动，vol_percentile 应处于中间范围"""
    rows = 120
    np.random.seed(42)
    # 小幅随机波动围绕100，各段波动率一致 → vol_percentile ≈ 0.5
    closes = 100 + np.random.randn(rows) * 0.2
    return pd.DataFrame({
        'open': closes,
        'high': closes + 0.05,
        'low': closes - 0.05,
        'close': closes,
        'volume': np.full(rows, 1000.0),
    })


# ════════════════════════════════════════════════════════
#  1. MultiObjectiveIndividual & ParetoFront Tests
# ════════════════════════════════════════════════════════

class TestMultiObjectiveIndividual:
    def test_inherits_individual(self):
        ind = MultiObjectiveIndividual(genome={'a': 1})
        assert isinstance(ind, Individual)
        assert ind.rank == 0
        assert ind.crowding_distance == 0.0
        assert ind.objectives == {}

    def test_with_objectives(self):
        ind = MultiObjectiveIndividual(
            genome={'a': 1},
            objectives={'sharpe': 1.5, 'max_drawdown': -0.1, 'win_rate': 0.6},
            rank=1,
            crowding_distance=0.5,
        )
        assert ind.objectives['sharpe'] == 1.5
        assert ind.rank == 1


class TestParetoFront:
    def _make_individual(self, sharpe, dd, wr):
        return MultiObjectiveIndividual(
            genome={'x': sharpe},
            objectives={'sharpe': sharpe, 'max_drawdown': dd, 'win_rate': wr},
        )

    def test_empty_front(self):
        pf = ParetoFront(individuals=[], generation=10)
        assert pf.get_best_compromise() is None

    def test_single_individual(self):
        ind = self._make_individual(1.5, -0.1, 0.6)
        pf = ParetoFront(individuals=[ind], generation=10)
        best = pf.get_best_compromise()
        assert best is ind

    def test_best_compromise_picks_balanced(self):
        """折中解应接近各目标均衡的个体"""
        ind_a = self._make_individual(2.0, -0.3, 0.4)  # 高Sharpe但高DD低WR
        ind_b = self._make_individual(1.2, -0.05, 0.7)  # 均衡
        ind_c = self._make_individual(0.5, -0.01, 0.9)  # 低DD高WR但低Sharpe
        pf = ParetoFront(individuals=[ind_a, ind_b, ind_c], generation=10)
        best = pf.get_best_compromise()
        assert best is not None


# ════════════════════════════════════════════════════════
#  2. NSGAIIOptimizer Tests
# ════════════════════════════════════════════════════════

class TestNSGAIIDominates:
    def test_dominates_clearly_better(self):
        """a: sharpe更高, drawdown更小(绝对值), win_rate更高 → a dominates b"""
        opt = NSGAIIOptimizer()
        a = MultiObjectiveIndividual(
            genome={}, objectives={'sharpe': 2.0, 'max_drawdown': 0.05, 'win_rate': 0.7}
        )
        b = MultiObjectiveIndividual(
            genome={}, objectives={'sharpe': 1.0, 'max_drawdown': 0.15, 'win_rate': 0.5}
        )
        assert opt._dominates(a, b) is True

    def test_does_not_dominate_tradeoff(self):
        """Neither dominates: a has better sharpe, b has better dd and wr"""
        opt = NSGAIIOptimizer()
        a = MultiObjectiveIndividual(
            genome={}, objectives={'sharpe': 2.0, 'max_drawdown': 0.2, 'win_rate': 0.5}
        )
        b = MultiObjectiveIndividual(
            genome={}, objectives={'sharpe': 1.0, 'max_drawdown': 0.05, 'win_rate': 0.7}
        )
        assert opt._dominates(a, b) is False
        assert opt._dominates(b, a) is False

    def test_dominates_equal(self):
        opt = NSGAIIOptimizer()
        a = MultiObjectiveIndividual(
            genome={}, objectives={'sharpe': 1.0, 'max_drawdown': 0.1, 'win_rate': 0.5}
        )
        b = MultiObjectiveIndividual(
            genome={}, objectives={'sharpe': 1.0, 'max_drawdown': 0.1, 'win_rate': 0.5}
        )
        assert opt._dominates(a, b) is False


class TestNSGAIINonDominatedSort:
    def test_basic_sort(self):
        opt = NSGAIIOptimizer()
        pop = [
            MultiObjectiveIndividual(genome={'x': i}, objectives={'sharpe': float(i+1), 'max_drawdown': -0.1, 'win_rate': 0.5})
            for i in range(4)
        ]
        fronts = opt._non_dominated_sort(pop)
        assert len(fronts) >= 1
        # First front should have the best individual (sharpe=4.0)
        assert any(ind.objectives['sharpe'] == 4.0 for ind in fronts[0])

    def test_all_same_objectives(self):
        opt = NSGAIIOptimizer()
        pop = [
            MultiObjectiveIndividual(genome={'x': i}, objectives={'sharpe': 1.0, 'max_drawdown': -0.1, 'win_rate': 0.5})
            for i in range(3)
        ]
        fronts = opt._non_dominated_sort(pop)
        # All non-dominated → all in front 0
        assert len(fronts[0]) == 3

    def test_clear_ranking(self):
        opt = NSGAIIOptimizer()
        # A dominates B dominates C
        pop = [
            MultiObjectiveIndividual(genome={'x': 0}, objectives={'sharpe': 3.0, 'max_drawdown': -0.01, 'win_rate': 0.8}),
            MultiObjectiveIndividual(genome={'x': 1}, objectives={'sharpe': 1.5, 'max_drawdown': -0.1, 'win_rate': 0.5}),
            MultiObjectiveIndividual(genome={'x': 2}, objectives={'sharpe': 0.5, 'max_drawdown': -0.3, 'win_rate': 0.3}),
        ]
        fronts = opt._non_dominated_sort(pop)
        assert len(fronts) >= 1


class TestNSGAIICrowdingDistance:
    def test_small_front_gets_infinity(self):
        opt = NSGAIIOptimizer()
        pop = [
            MultiObjectiveIndividual(genome={'x': 0}, objectives={'sharpe': 1.0, 'max_drawdown': -0.1, 'win_rate': 0.5}),
            MultiObjectiveIndividual(genome={'x': 1}, objectives={'sharpe': 2.0, 'max_drawdown': -0.05, 'win_rate': 0.6}),
        ]
        opt._assign_crowding_distance([pop])
        for ind in pop:
            assert ind.crowding_distance == float('inf')

    def test_boundary_individuals_get_infinity(self):
        opt = NSGAIIOptimizer()
        pop = [
            MultiObjectiveIndividual(genome={'x': i}, objectives={'sharpe': float(i+1), 'max_drawdown': -0.1, 'win_rate': 0.5})
            for i in range(5)
        ]
        opt._assign_crowding_distance([pop])
        assert pop[0].crowding_distance == float('inf')  # boundary after sort
        assert pop[-1].crowding_distance == float('inf')


class TestNSGAIIEvolve:
    def test_evolve_multi_objective_basic(self):
        """基本的NSGA-II进化"""
        opt = NSGAIIOptimizer()
        param_ranges = {
            'stop_loss_pct': (0.01, 0.08),
            'take_profit_pct': (0.02, 0.20),
            'leverage': (1, 5),
        }

        def mock_fitness(genome):
            sl = genome['stop_loss_pct']
            tp = genome['take_profit_pct']
            return MultiObjectiveIndividual(
                genome=genome,
                objectives={
                    'sharpe': tp / (sl + 0.01),
                    'max_drawdown': -sl,
                    'win_rate': min(0.9, sl + 0.3),
                },
                fitness=tp / (sl + 0.01),
                sharpe=tp / (sl + 0.01),
            )

        result = opt.evolve_multi_objective(
            template_id="test_tpl",
            param_ranges=param_ranges,
            fitness_fn=mock_fitness,
            generations=5,
            population_size=10,
        )

        assert isinstance(result, ParetoFront)
        assert result.generation == 5
        assert len(result.individuals) > 0
        for ind in result.individuals:
            assert ind.rank == 0

    def test_evolve_preserves_genetic_optimizer_compat(self):
        """NSGAIIOptimizer 应继承 GeneticOptimizer 的 evolve 方法"""
        opt = NSGAIIOptimizer()
        assert hasattr(opt, 'evolve')
        assert hasattr(opt, 'should_promote')


# ════════════════════════════════════════════════════════
#  3. MarketRegimeClassifier Tests
# ════════════════════════════════════════════════════════

class TestMarketRegimeEnum:
    def test_all_regimes(self):
        assert len(MarketRegime) == 6
        assert MarketRegime.TRENDING_UP.value == "trending_up"
        assert MarketRegime.CRASH.value == "crash"


class TestRegimeClassification:
    def test_creation(self):
        rc = RegimeClassification(
            regime=MarketRegime.RANGING,
            confidence=0.7,
            features={'volatility': 0.5},
        )
        assert rc.regime == MarketRegime.RANGING
        assert rc.confidence == 0.7
        assert rc.transition_prob == {}


class TestMarketRegimeClassifier:
    def test_classify_trending_up(self):
        clf = MarketRegimeClassifier()
        klines = _make_trending_up_klines()
        result = clf.classify(klines)
        assert result.regime == MarketRegime.TRENDING_UP
        assert result.confidence > 0
        assert 'volatility' in result.features
        assert 'trend' in result.features

    def test_classify_trending_down(self):
        clf = MarketRegimeClassifier()
        klines = _make_trending_down_klines()
        result = clf.classify(klines)
        assert result.regime == MarketRegime.TRENDING_DOWN
        assert result.confidence > 0

    def test_classify_ranging(self):
        clf = MarketRegimeClassifier()
        klines = _make_ranging_klines()
        result = clf.classify(klines)
        # Ranging should be the default for sideways markets
        assert result.regime in (MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY)

    def test_classify_short_klines_defaults_ranging(self):
        clf = MarketRegimeClassifier()
        klines = _make_klines(50)  # less than lookback=100
        result = clf.classify(klines)
        assert result.regime == MarketRegime.RANGING
        assert result.confidence == 0.3

    def test_classify_none_klines(self):
        clf = MarketRegimeClassifier()
        result = clf.classify(None)
        assert result.regime == MarketRegime.RANGING
        assert result.confidence == 0.3

    def test_classify_features_populated(self):
        clf = MarketRegimeClassifier()
        klines = _make_klines(120)
        result = clf.classify(klines)
        assert 'volatility' in result.features
        assert 'trend' in result.features
        assert 'trend_strength' in result.features
        assert 'vol_percentile' in result.features

    def test_classify_confidence_range(self):
        clf = MarketRegimeClassifier()
        klines = _make_klines(120)
        result = clf.classify(klines)
        assert 0 <= result.confidence <= 1.0


class TestRegimeStrategyMap:
    def test_all_regimes_have_mapping(self):
        for regime in MarketRegime:
            assert regime in REGIME_STRATEGY_MAP

    def test_mapping_structure(self):
        for regime, config in REGIME_STRATEGY_MAP.items():
            assert 'preferred_nature' in config
            assert 'entry_factors' in config
            assert 'param_overrides' in config
            assert 'risk_multiplier' in config

    def test_crash_is_most_conservative(self):
        crash_config = REGIME_STRATEGY_MAP[MarketRegime.CRASH]
        assert crash_config['risk_multiplier'] == 0.3
        leverage_range = crash_config['param_overrides']['leverage']
        assert leverage_range == (1, 1)

    def test_trending_up_allows_higher_risk(self):
        config = REGIME_STRATEGY_MAP[MarketRegime.TRENDING_UP]
        assert config['risk_multiplier'] == 1.2


class TestGetStrategyParams:
    def test_returns_correct_mapping(self):
        clf = MarketRegimeClassifier()
        rc = RegimeClassification(regime=MarketRegime.CRASH, confidence=0.9)
        params = clf.get_strategy_params(rc)
        assert params['risk_multiplier'] == 0.3

    def test_defaults_to_ranging(self):
        clf = MarketRegimeClassifier()
        rc = RegimeClassification(regime=MarketRegime.RANGING, confidence=0.5)
        params = clf.get_strategy_params(rc)
        assert params['risk_multiplier'] == 1.0


# ════════════════════════════════════════════════════════
#  5. Integration Tests
# ════════════════════════════════════════════════════════

class TestPhase5Integration:
    def test_regime_to_nsga_pipeline(self):
        """MarketRegime → 策略参数 → NSGA-II 优化"""
        # 1. 分类市场状态
        clf = MarketRegimeClassifier()
        klines = _make_trending_up_klines()
        regime_result = clf.classify(klines)

        # 2. 获取策略参数建议
        strategy_config = clf.get_strategy_params(regime_result)
        assert 'param_overrides' in strategy_config
        param_overrides = strategy_config['param_overrides']

        # 3. 用参数范围执行NSGA-II优化
        param_ranges = {
            k: v for k, v in param_overrides.items()
            if isinstance(v, tuple) and len(v) == 2
            and isinstance(v[0], (int, float))
        }
        if param_ranges:
            opt = NSGAIIOptimizer()
            fitness_calls = [0]
            def mock_fitness(genome):
                fitness_calls[0] += 1
                return MultiObjectiveIndividual(
                    genome=genome,
                    objectives={
                        'sharpe': 1.0 + np.random.rand(),
                        'max_drawdown': -0.05 - np.random.rand() * 0.1,
                        'win_rate': 0.5 + np.random.rand() * 0.3,
                    },
                    fitness=1.5,
                )
            result = opt.evolve_multi_objective(
                template_id="integration_test",
                param_ranges=param_ranges,
                fitness_fn=mock_fitness,
                generations=3,
                population_size=8,
            )
            assert isinstance(result, ParetoFront)
            assert fitness_calls[0] > 0

    def test_all_phase5_modules_importable(self):
        """验证所有Phase 5模块可以被正常导入"""
        from backend.services.genetic_optimizer import (
            GeneticOptimizer, Individual, EvolutionResult,
            NSGAIIOptimizer, MultiObjectiveIndividual, ParetoFront,
        )
        from backend.services.market_regime import (
            MarketRegimeClassifier, MarketRegime, RegimeClassification,
            REGIME_STRATEGY_MAP,
        )
        assert NSGAIIOptimizer is not None
        assert MarketRegimeClassifier is not None
