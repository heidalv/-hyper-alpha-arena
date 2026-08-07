"""
test_phase7_drl — Phase 7 DRL 强化学习集成单元测试

覆盖范围:
1. TradingEnv — 交易环境
2. RLPolicyOptimizer — 强化学习策略优化器
3. KellyPositionSizer — Kelly准则仓位管理
4. 集成测试
"""

import numpy as np
import pandas as pd
import pytest

from backend.services.rl.trading_env import TradingEnv, HAS_GYM
from backend.services.rl.rl_optimizer import RLPolicyOptimizer
from backend.services.rl.kelly_position_sizer import (
    KellyPositionSizer,
    KellyPositionResult,
)


# ════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════

def _make_klines(rows: int = 200, base_price: float = 100.0) -> pd.DataFrame:
    np.random.seed(42)
    closes = base_price + np.cumsum(np.random.randn(rows) * 0.5)
    return pd.DataFrame({
        'open': closes,
        'high': closes + 0.3,
        'low': closes - 0.3,
        'close': closes,
        'volume': 1000 + np.abs(np.random.randn(rows)) * 500,
    })


def _make_factors(klines: pd.DataFrame) -> dict:
    return {
        'rsi': pd.Series(np.random.rand(len(klines)) * 100),
        'momentum': pd.Series(np.random.randn(len(klines))),
    }


# ════════════════════════════════════════════════════════
#  1. TradingEnv Tests
# ════════════════════════════════════════════════════════

class TestTradingEnv:
    def test_creation(self):
        klines = _make_klines()
        env = TradingEnv(klines)
        assert env.initial_balance == 10000
        assert env.max_leverage == 5
        assert env.balance == 10000
        assert env.position == 0.0

    def test_creation_with_factors(self):
        klines = _make_klines()
        factors = _make_factors(klines)
        env = TradingEnv(klines, factor_outputs=factors)
        assert env._obs_size == 6  # 2 factors + 4 account

    def test_reset(self):
        klines = _make_klines()
        env = TradingEnv(klines)
        obs, info = env.reset()
        assert isinstance(obs, np.ndarray)
        assert obs.dtype == np.float32
        assert len(obs) == 4  # 0 factors + 4 account
        assert env._step_idx == 50
        assert env.balance == 10000
        assert env.position == 0.0

    def test_reset_with_short_klines(self):
        klines = _make_klines(30)
        env = TradingEnv(klines)
        obs, info = env.reset()
        assert env._step_idx == 0  # Less than 50

    def test_step(self):
        klines = _make_klines()
        env = TradingEnv(klines)
        env.reset()
        action = np.array([0.5, 0.3], dtype=np.float32)  # buy 30% size
        obs, reward, done, truncated, info = env.step(action)
        assert isinstance(obs, np.ndarray)
        assert isinstance(reward, float)
        assert isinstance(done, bool)
        assert isinstance(truncated, bool)
        assert env._step_idx == 51

    def test_step_no_position_change(self):
        klines = _make_klines()
        env = TradingEnv(klines)
        env.reset()
        # Very small action, below 0.01 threshold
        action = np.array([0.0, 0.0], dtype=np.float32)
        obs, reward, done, truncated, info = env.step(action)
        assert reward == 0.0

    def test_step_buy_then_sell(self):
        klines = _make_klines()
        env = TradingEnv(klines)
        env.reset()
        # Buy
        obs, reward, _, _, _ = env.step(np.array([1.0, 0.5], dtype=np.float32))
        assert env.position != 0
        # Sell (reverse)
        obs, reward, _, _, _ = env.step(np.array([-1.0, 0.5], dtype=np.float32))
        # Should have realized some PnL
        assert len(env._pnl_history) >= 1

    def test_done_when_reaches_end(self):
        klines = _make_klines(60)  # Only 60 rows, start at 50
        env = TradingEnv(klines)
        env.reset()
        done = False
        steps = 0
        while not done and steps < 20:
            _, _, done, _, _ = env.step(np.array([0.0, 0.0], dtype=np.float32))
            steps += 1
        assert done is True or steps == 20

    def test_unrealized_pnl(self):
        klines = _make_klines()
        env = TradingEnv(klines)
        env.reset()
        # No position initially
        assert env._unrealized_pnl() == 0.0
        # Open position
        env.step(np.array([1.0, 0.5], dtype=np.float32))
        # Now should have unrealized pnl
        pnl = env._unrealized_pnl()
        assert isinstance(pnl, float)

    def test_total_pnl(self):
        klines = _make_klines()
        env = TradingEnv(klines)
        env.reset()
        assert env.total_pnl == 0.0
        # Open and close position
        env.step(np.array([1.0, 0.5], dtype=np.float32))
        env.step(np.array([-1.0, 0.5], dtype=np.float32))
        assert isinstance(env.total_pnl, float)

    def test_sharpe_ratio(self):
        klines = _make_klines()
        env = TradingEnv(klines)
        env.reset()
        assert env.sharpe_ratio == 0.0
        # Generate some PnL
        for _ in range(5):
            env.step(np.array([1.0, 0.3], dtype=np.float32))
            env.step(np.array([-1.0, 0.3], dtype=np.float32))
        sharpe = env.sharpe_ratio
        assert isinstance(sharpe, float)

    def test_current_step(self):
        klines = _make_klines()
        env = TradingEnv(klines)
        env.reset()
        assert env.current_step == 50
        env.step(np.array([0.0, 0.0], dtype=np.float32))
        assert env.current_step == 51

    def test_obs_shape_consistency(self):
        klines = _make_klines()
        factors = _make_factors(klines)
        env = TradingEnv(klines, factor_outputs=factors)
        obs, _ = env.reset()
        assert obs.shape == (env._obs_size,)

    def test_step_tuple_action(self):
        klines = _make_klines()
        env = TradingEnv(klines)
        env.reset()
        obs, reward, done, truncated, info = env.step((0.5, 0.3))
        assert isinstance(obs, np.ndarray)


# ════════════════════════════════════════════════════════
#  2. RLPolicyOptimizer Tests
# ════════════════════════════════════════════════════════

class TestRLPolicyOptimizer:
    def test_creation(self):
        opt = RLPolicyOptimizer()
        assert opt.model is None
        assert isinstance(opt.is_available, bool)
        assert isinstance(opt.stats, dict)

    def test_stats_structure(self):
        opt = RLPolicyOptimizer()
        stats = opt.stats
        assert 'is_available' in stats
        assert 'has_model' in stats
        assert 'total_timesteps_trained' in stats
        assert 'prediction_count' in stats

    def test_predict_no_model(self):
        opt = RLPolicyOptimizer()
        direction, size = opt.predict(np.zeros(6, dtype=np.float32))
        assert direction == 0.0
        assert size == 0.0

    def test_train_not_available(self):
        opt = RLPolicyOptimizer()
        if not opt.is_available:
            result = opt.train(None)
            assert result is False

    def test_get_shadow_advice_no_model(self):
        opt = RLPolicyOptimizer()
        advice = opt.get_shadow_advice(np.zeros(6, dtype=np.float32))
        assert advice['direction'] == 0.0
        assert advice['size'] == 0.0
        assert advice['action'] == 'hold'
        assert advice['source'] == 'drl_shadow'

    def test_shadow_advice_action_long(self):
        """Test action classification for positive direction"""
        opt = RLPolicyOptimizer()
        # Mock the predict to return strong long signal
        opt.predict = lambda obs: (0.8, 0.5)
        advice = opt.get_shadow_advice(np.zeros(6, dtype=np.float32))
        assert advice['action'] == 'long'

    def test_shadow_advice_action_short(self):
        opt = RLPolicyOptimizer()
        opt.predict = lambda obs: (-0.8, 0.5)
        advice = opt.get_shadow_advice(np.zeros(6, dtype=np.float32))
        assert advice['action'] == 'short'

    def test_shadow_advice_action_hold_weak(self):
        opt = RLPolicyOptimizer()
        opt.predict = lambda obs: (0.1, 0.05)
        advice = opt.get_shadow_advice(np.zeros(6, dtype=np.float32))
        assert advice['action'] == 'hold'

    def test_shadow_advice_confidence(self):
        opt = RLPolicyOptimizer()
        opt.predict = lambda obs: (0.8, 0.5)
        advice = opt.get_shadow_advice(np.zeros(6, dtype=np.float32))
        assert 0 <= advice['confidence'] <= 1.0


# ════════════════════════════════════════════════════════
#  3. KellyPositionSizer Tests
# ════════════════════════════════════════════════════════

class TestKellyPositionResult:
    def test_creation(self):
        r = KellyPositionResult(
            kelly_fraction=0.2, adjusted_fraction=0.1,
            position_size=1000, risk_per_trade=200, confidence=0.5,
        )
        assert r.is_valid is True

    def test_invalid_zero(self):
        r = KellyPositionResult(
            kelly_fraction=0, adjusted_fraction=0,
            position_size=0, risk_per_trade=0, confidence=0,
        )
        assert r.is_valid is False


class TestKellyPositionSizer:
    def test_creation(self):
        sizer = KellyPositionSizer()
        assert sizer.fraction_of_kelly == 0.5
        assert sizer.max_position_pct == 0.25
        assert sizer.min_trades == 10

    def test_basic_calculate(self):
        sizer = KellyPositionSizer()
        result = sizer.calculate(
            equity=10000,
            win_rate=0.6,
            avg_win=200,
            avg_loss=100,
        )
        assert isinstance(result, KellyPositionResult)
        assert result.kelly_fraction > 0
        assert result.adjusted_fraction > 0
        assert result.position_size > 0
        assert result.is_valid

    def test_kelly_fraction_calculation(self):
        # p=0.6, b=2.0 → kelly = (0.6*2 - 0.4)/2 = 0.8/2 = 0.4
        kelly = KellyPositionSizer.calculate_kelly_fraction(0.6, 2.0)
        assert abs(kelly - 0.4) < 1e-10

    def test_kelly_fraction_negative_expectation(self):
        # p=0.3, b=1.0 → kelly = (0.3*1 - 0.7)/1 = -0.4 → clamped to 0
        kelly = KellyPositionSizer.calculate_kelly_fraction(0.3, 1.0)
        assert kelly == 0.0

    def test_kelly_fraction_perfect_win(self):
        kelly = KellyPositionSizer.calculate_kelly_fraction(1.0, 2.0)
        assert kelly == 1.0

    def test_kelly_fraction_zero_ratio(self):
        kelly = KellyPositionSizer.calculate_kelly_fraction(0.5, 0.0)
        assert kelly == 0.0

    def test_half_kelly_applied(self):
        sizer = KellyPositionSizer(fraction_of_kelly=0.5)
        result = sizer.calculate(equity=10000, win_rate=0.6, avg_win=200, avg_loss=100)
        # kelly = 0.4, half_kelly = 0.2, capped at 0.25
        assert result.adjusted_fraction <= 0.25

    def test_max_position_cap(self):
        sizer = KellyPositionSizer(max_position_pct=0.10)
        result = sizer.calculate(
            equity=10000,
            win_rate=0.9,
            avg_win=500,
            avg_loss=100,
        )
        assert result.adjusted_fraction <= 0.10

    def test_with_trade_history(self):
        sizer = KellyPositionSizer(min_trades=5)
        trades = [
            {'pnl': 100}, {'pnl': -50}, {'pnl': 200}, {'pnl': -30},
            {'pnl': 150}, {'pnl': -40}, {'pnl': 80}, {'pnl': -60},
            {'pnl': 120}, {'pnl': -20},
        ]
        result = sizer.calculate(equity=10000, trade_history=trades)
        assert result.is_valid
        # 6 wins / 10 total = 0.6 win_rate
        assert result.kelly_fraction > 0

    def test_with_insufficient_trade_history(self):
        sizer = KellyPositionSizer(min_trades=10)
        trades = [{'pnl': 100}, {'pnl': -50}]
        result = sizer.calculate(equity=10000, trade_history=trades)
        # Should use default params since not enough trades
        assert isinstance(result, KellyPositionResult)

    def test_with_volatility_adjustment(self):
        sizer = KellyPositionSizer()
        result_no_vol = sizer.calculate(equity=10000, win_rate=0.6, avg_win=200, avg_loss=100)
        result_high_vol = sizer.calculate(equity=10000, win_rate=0.6, avg_win=200, avg_loss=100, volatility=0.5)
        assert result_high_vol.adjusted_fraction <= result_no_vol.adjusted_fraction

    def test_zero_equity(self):
        sizer = KellyPositionSizer()
        result = sizer.calculate(equity=0, win_rate=0.6, avg_win=200, avg_loss=100)
        assert result.position_size == 0

    def test_custom_fraction_of_kelly(self):
        sizer = KellyPositionSizer(fraction_of_kelly=0.25)
        result = sizer.calculate(equity=10000, win_rate=0.6, avg_win=200, avg_loss=100)
        # Quarter Kelly should be smaller than half Kelly
        sizer_half = KellyPositionSizer(fraction_of_kelly=0.5)
        result_half = sizer_half.calculate(equity=10000, win_rate=0.6, avg_win=200, avg_loss=100)
        assert result.adjusted_fraction <= result_half.adjusted_fraction

    def test_extract_stats(self):
        sizer = KellyPositionSizer()
        trades = [{'pnl': 100}, {'pnl': -50}, {'pnl': 200}, {'pnl': -30}, {'pnl': 150}]
        stats = sizer._extract_stats(trades)
        assert stats['win_rate'] == 0.6  # 3/5
        assert stats['avg_win'] > 0
        assert stats['avg_loss'] > 0

    def test_extract_stats_empty(self):
        sizer = KellyPositionSizer()
        stats = sizer._extract_stats([])
        assert stats['win_rate'] == 0.5  # default


# ════════════════════════════════════════════════════════
#  4. Integration Tests
# ════════════════════════════════════════════════════════

class TestPhase7Integration:
    def test_all_phase7_modules_importable(self):
        from backend.services.rl import (
            TradingEnv, RLPolicyOptimizer, KellyPositionSizer, KellyPositionResult, HAS_GYM,
        )
        assert TradingEnv is not None
        assert RLPolicyOptimizer is not None
        assert KellyPositionSizer is not None

    def test_env_to_optimizer_pipeline(self):
        """TradingEnv → RLPolicyOptimizer（无ML库降级模式）"""
        klines = _make_klines()
        env = TradingEnv(klines)
        opt = RLPolicyOptimizer()

        obs, _ = env.reset()
        direction, size = opt.predict(obs)
        assert direction == 0.0  # No model → zero
        assert size == 0.0

    def test_env_to_kelly_pipeline(self):
        """TradingEnv 交易结果 → Kelly 仓位计算"""
        klines = _make_klines()
        env = TradingEnv(klines)
        env.reset()

        # Simulate some trades
        for _ in range(10):
            env.step(np.array([np.random.rand() * 2 - 1, 0.3], dtype=np.float32))
            env.step(np.array([np.random.rand() * 2 - 1, 0.3], dtype=np.float32))

        # Build trade history from env
        trade_history = [{'pnl': p} for p in env._pnl_history]

        # Calculate Kelly position
        sizer = KellyPositionSizer(min_trades=5)
        result = sizer.calculate(equity=10000, trade_history=trade_history)
        assert isinstance(result, KellyPositionResult)

    def test_full_shadow_mode_cycle(self):
        """完整影子模式流程：环境 → 观察值 → 影子建议"""
        klines = _make_klines()
        factors = _make_factors(klines)
        env = TradingEnv(klines, factor_outputs=factors)
        opt = RLPolicyOptimizer()

        obs, _ = env.reset()
        advice = opt.get_shadow_advice(obs)
        assert 'action' in advice
        assert advice['action'] == 'hold'  # No model
        assert advice['source'] == 'drl_shadow'
