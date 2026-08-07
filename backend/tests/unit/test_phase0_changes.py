"""
test_phase0_changes — Phase 0 所有变更的单元测试

覆盖范围:
1. funding_factors.py 数据源修复验证
2. unified_data_pool.py funding_rate 注入验证
3. 编排器 frozen 硬约束（close/reduce 拦截）
4. trade_nature 统一解析器（NATURE_TO_TIER 别名一致性）
5. 死代码 @deprecated 标记验证
6. ORCHESTRATOR_HARD_GATE 配置验证
"""

import importlib
import importlib.util
import os
import sys
import warnings
import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch


def _load_funding_factors_module():
    """直接加载 funding_factors 模块，绕过 factors/__init__.py 中的 register_factor 注册链。

    factors/__init__.py 会导入 technical/trend_factors.py 中的 SMA5Factor，
    该类继承 VectorizedFactor 但未实现 vectorized_calculate 抽象方法，
    导致 @register_factor() 装饰器在注册时失败。
    通过 importlib.util 直接加载文件可以绕开此问题。
    """
    module_name = "backend.services.factor_engine.factors.sentiment.funding_factors"
    if module_name in sys.modules:
        return sys.modules[module_name]

    # 预加载依赖模块（factor_base 和 factor_registry）
    import backend.services.factor_engine.factor_base  # noqa: F401
    import backend.services.factor_engine.factor_registry  # noqa: F401

    file_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "services", "factor_engine", "factors", "sentiment", "funding_factors.py",
    )
    file_path = os.path.normpath(file_path)

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ════════════════════════════════════════════════════════
#  1. Funding Factors — 数据源修复验证
# ════════════════════════════════════════════════════════

class TestFundingFactorsDataSource:
    """验证 funding_factors.py 使用真实 funding_rate 数据（P0 Bug 修复）"""

    @pytest.fixture
    def sample_df_with_funding(self):
        """模拟带 funding_rate 列的 K线 DataFrame"""
        n = 30
        return pd.DataFrame({
            "close": np.random.uniform(49000, 51000, n),
            "funding_rate": np.concatenate([
                np.linspace(0.0001, 0.0003, 15),
                np.linspace(0.0003, -0.0001, 15),
            ]),
        })

    @pytest.fixture
    def sample_df_without_funding(self):
        """模拟不带 funding_rate 列的 K线 DataFrame"""
        n = 30
        return pd.DataFrame({
            "close": np.random.uniform(49000, 51000, n),
        })

    @pytest.fixture
    def funding_mod(self):
        """加载 funding_factors 模块"""
        return _load_funding_factors_module()

    def test_simple_factor_reads_real_funding_rate(self, sample_df_with_funding, funding_mod):
        """FundingRateSimpleFactor 应直接读取 funding_rate 列的真实数据"""
        factor = funding_mod.FundingRateSimpleFactor()
        result = factor.calculate(sample_df_with_funding)
        expected = sample_df_with_funding["funding_rate"].fillna(0.0)
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_simple_factor_graceful_without_funding_column(self, sample_df_without_funding, funding_mod):
        """无 funding_rate 列时应降级返回零序列"""
        factor = funding_mod.FundingRateSimpleFactor()
        result = factor.calculate(sample_df_without_funding)
        assert (result == 0.0).all()
        assert len(result) == len(sample_df_without_funding)

    def test_24h_factor_rolling_mean(self, sample_df_with_funding, funding_mod):
        """FundingRate24hFactor 应计算 24 期滚动均值"""
        factor = funding_mod.FundingRate24hFactor()
        result = factor.calculate(sample_df_with_funding)
        expected = sample_df_with_funding["funding_rate"].fillna(0.0).rolling(24).mean()
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_ma_factor_rolling_mean_20(self, sample_df_with_funding, funding_mod):
        """FundingRateMaFactor 应计算 20 期滚动均值"""
        factor = funding_mod.FundingRateMaFactor()
        result = factor.calculate(sample_df_with_funding)
        expected = sample_df_with_funding["funding_rate"].fillna(0.0).rolling(20).mean()
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_vol_factor_rolling_std_20(self, sample_df_with_funding, funding_mod):
        """FundingRateVolFactor 应计算 20 期滚动标准差"""
        factor = funding_mod.FundingRateVolFactor()
        result = factor.calculate(sample_df_with_funding)
        expected = sample_df_with_funding["funding_rate"].fillna(0.0).rolling(20).std()
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_extreme_factor_zscore(self, sample_df_with_funding, funding_mod):
        """FundingRateExtremeFactor 应计算 Z-Score"""
        factor = funding_mod.FundingRateExtremeFactor()
        result = factor.calculate(sample_df_with_funding)
        fr = sample_df_with_funding["funding_rate"].fillna(0.0)
        expected = (fr - fr.rolling(20).mean()) / (fr.rolling(20).std() + 1e-10)
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_all_factors_graceful_without_funding_column(self, sample_df_without_funding, funding_mod):
        """所有 funding 因子在无 funding_rate 列时均返回零序列"""
        for cls_name in [
            "FundingRateSimpleFactor",
            "FundingRate24hFactor",
            "FundingRateMaFactor",
            "FundingRateVolFactor",
            "FundingRateExtremeFactor",
        ]:
            factor = getattr(funding_mod, cls_name)()
            result = factor.calculate(sample_df_without_funding)
            assert (result == 0.0).all(), f"{cls_name} 未正确降级"
            assert len(result) == len(sample_df_without_funding)

    def test_simple_factor_not_using_price_pct_change(self, sample_df_with_funding, funding_mod):
        """验证 FundingRateSimpleFactor 不使用 close pct_change（P0 Bug 修复核心验证）"""
        factor = funding_mod.FundingRateSimpleFactor()
        result = factor.calculate(sample_df_with_funding)
        price_pct = sample_df_with_funding["close"].pct_change().fillna(0.0)
        # result 应等于 funding_rate，不应等于 price pct_change
        assert not np.allclose(result.values, price_pct.values, atol=1e-10), (
            "FundingRateSimpleFactor 仍在使用 price pct_change 作为数据源!"
        )

    def test_simple_factor_metadata_category(self, funding_mod):
        """验证因子元数据 category 为 sentiment"""
        factor = funding_mod.FundingRateSimpleFactor()
        meta = factor.get_metadata()
        assert meta.category == "sentiment"
        assert meta.subcategory == "funding"
        assert "funding_rate" in meta.required_data_fields


# ════════════════════════════════════════════════════════
#  2. UnifiedDataPool — funding_rate 注入验证
# ════════════════════════════════════════════════════════

class TestUnifiedDataPoolFundingInjection:
    """验证 unified_data_pool 在 K线 DataFrame 中注入 funding_rate 列"""

    def test_market_snapshot_has_funding_rate_field(self):
        """MarketSnapshot dataclass 应包含 funding_rate 字段"""
        from backend.services.unified_data_pool import MarketSnapshot
        snap = MarketSnapshot(
            symbol="BTC",
            price=50000.0,
            funding_rate=0.0001,
        )
        assert hasattr(snap, "funding_rate")
        assert snap.funding_rate == 0.0001

    def test_market_snapshot_default_funding_rate_zero(self):
        """MarketSnapshot 默认 funding_rate 应为 0.0"""
        from backend.services.unified_data_pool import MarketSnapshot
        snap = MarketSnapshot(symbol="BTC", price=50000.0)
        assert snap.funding_rate == 0.0


# ════════════════════════════════════════════════════════
#  3. 编排器 Frozen 硬约束验证
# ════════════════════════════════════════════════════════

class TestOrchestratorFrozenHardConstraint:
    """验证编排器 frozen 状态下 close/reduce 的拦截逻辑"""

    def _extract_frozen_block_logic(self, pos, market_summary, sym):
        """提取编排器 frozen 拦截核心逻辑，模拟 full_auto_trading_service.py:2991-3016"""
        action_close = "close"
        action_reduce = "reduce"

        results = {}
        for action in (action_close, action_reduce):
            if action in ("close", "reduce") and pos:
                try:
                    from backend.config.settings import ORCHESTRATOR_HARD_GATE
                    if ORCHESTRATOR_HARD_GATE:
                        _mkt_fz = (market_summary or {}).get(sym, {})
                        _orch_fz = _mkt_fz.get("orchestrator", {}) if isinstance(_mkt_fz, dict) else {}
                        if isinstance(_orch_fz, dict) and _orch_fz.get("action") == "frozen":
                            _fz_margin = float(pos.get("margin", 0))
                            _fz_upnl = float(pos.get("unrealized_pnl", 0))
                            _fz_pnl_pct = (_fz_upnl / _fz_margin) if _fz_margin > 0 else 0
                            if _fz_pnl_pct > -0.08:
                                results[action] = "BLOCKED"
                            else:
                                results[action] = "ALLOWED_EMERGENCY"
                except Exception:
                    results[action] = "PASS_THROUGH"
        return results

    def test_frozen_blocks_close_with_small_loss(self):
        """frozen 状态 + 小亏损 → close 应被拦截"""
        pos = {"margin": 1000, "unrealized_pnl": -30}  # -3%
        market_summary = {
            "BTC": {"orchestrator": {"action": "frozen", "reasoning": "trend unclear"}}
        }
        results = self._extract_frozen_block_logic(pos, market_summary, "BTC")
        assert results["close"] == "BLOCKED"

    def test_frozen_blocks_reduce_with_small_loss(self):
        """frozen 状态 + 小亏损 → reduce 应被拦截"""
        pos = {"margin": 1000, "unrealized_pnl": -50}  # -5%
        market_summary = {
            "BTC": {"orchestrator": {"action": "frozen", "reasoning": "volatile"}}
        }
        results = self._extract_frozen_block_logic(pos, market_summary, "BTC")
        assert results["reduce"] == "BLOCKED"

    def test_frozen_allows_close_with_emergency_loss(self):
        """frozen 状态 + 亏损超过 8% → close 应被允许（紧急止损）"""
        pos = {"margin": 1000, "unrealized_pnl": -100}  # -10%
        market_summary = {
            "BTC": {"orchestrator": {"action": "frozen", "reasoning": "uncertain"}}
        }
        results = self._extract_frozen_block_logic(pos, market_summary, "BTC")
        assert results["close"] == "ALLOWED_EMERGENCY"

    def test_frozen_allows_reduce_with_emergency_loss(self):
        """frozen 状态 + 亏损超过 8% → reduce 应被允许"""
        pos = {"margin": 1000, "unrealized_pnl": -120}  # -12%
        market_summary = {
            "BTC": {"orchestrator": {"action": "frozen", "reasoning": "caution"}}
        }
        results = self._extract_frozen_block_logic(pos, market_summary, "BTC")
        assert results["reduce"] == "ALLOWED_EMERGENCY"

    def test_no_frozen_passes_through(self):
        """非 frozen 状态 → 不拦截"""
        pos = {"margin": 1000, "unrealized_pnl": -5}
        market_summary = {
            "BTC": {"orchestrator": {"action": "hold", "reasoning": "stable"}}
        }
        results = self._extract_frozen_block_logic(pos, market_summary, "BTC")
        assert "close" not in results
        assert "reduce" not in results

    def test_no_orchestrator_passes_through(self):
        """无 orchestrator 字段 → 不拦截"""
        pos = {"margin": 1000, "unrealized_pnl": -5}
        market_summary = {"BTC": {}}
        results = self._extract_frozen_block_logic(pos, market_summary, "BTC")
        assert "close" not in results

    def test_no_position_passes_through(self):
        """无仓位 → 不进入 frozen 检查"""
        results = self._extract_frozen_block_logic(None, {}, "BTC")
        assert results == {}

    def test_zero_margin_not_divide_by_zero(self):
        """margin=0 时不除零，pnl_pct 默认 0 → BLOCKED"""
        pos = {"margin": 0, "unrealized_pnl": -10}
        market_summary = {
            "BTC": {"orchestrator": {"action": "frozen", "reasoning": "test"}}
        }
        results = self._extract_frozen_block_logic(pos, market_summary, "BTC")
        # pnl_pct = 0 (since margin=0), which is > -0.08 → BLOCKED
        assert results["close"] == "BLOCKED"

    def test_exact_threshold_negative_8_pct(self):
        """刚好 -8% 亏损应被 ALLOWED_EMERGENCY（>= -8% 是 blocked, < -8% 是 allowed）"""
        # pnl_pct = -0.08 exactly → -0.08 > -0.08 is False → ALLOWED_EMERGENCY
        pos = {"margin": 1000, "unrealized_pnl": -80}  # exactly -8%
        market_summary = {
            "BTC": {"orchestrator": {"action": "frozen", "reasoning": "test"}}
        }
        results = self._extract_frozen_block_logic(pos, market_summary, "BTC")
        assert results["close"] == "ALLOWED_EMERGENCY"

    def test_just_above_threshold_blocked(self):
        """-7.9% 亏损（刚高于 -8% 阈值）→ BLOCKED"""
        pos = {"margin": 1000, "unrealized_pnl": -79}  # -7.9%
        market_summary = {
            "BTC": {"orchestrator": {"action": "frozen", "reasoning": "test"}}
        }
        results = self._extract_frozen_block_logic(pos, market_summary, "BTC")
        assert results["close"] == "BLOCKED"

    def test_profit_position_blocked(self):
        """frozen 状态 + 盈利仓位 → close/reduce 仍被 BLOCKED"""
        pos = {"margin": 1000, "unrealized_pnl": 50}  # +5%
        market_summary = {
            "BTC": {"orchestrator": {"action": "frozen", "reasoning": "uncertain"}}
        }
        results = self._extract_frozen_block_logic(pos, market_summary, "BTC")
        assert results["close"] == "BLOCKED"
        assert results["reduce"] == "BLOCKED"


# ════════════════════════════════════════════════════════
#  4. trade_nature 统一解析器验证
# ════════════════════════════════════════════════════════

class TestTradeNatureUnifiedResolver:
    """验证 NATURE_TO_TIER 在 sub_position_manager 和 full_auto_trading_service 中一致"""

    EXPECTED_NATURE_TO_TIER = {
        "scalp": "short",
        "intraday": "short",
        "swing": "mid",
        "position": "long",
        "trend_follow": "long",
    }

    def test_canonical_nature_to_tier_completeness(self):
        """sub_position_manager.NATURE_TO_TIER 应包含全部 5 种 trade_nature"""
        from backend.services.sub_position_manager import NATURE_TO_TIER
        for nature, tier in self.EXPECTED_NATURE_TO_TIER.items():
            assert nature in NATURE_TO_TIER, f"缺少 trade_nature: {nature}"
            assert NATURE_TO_TIER[nature] == tier, (
                f"{nature} 映射错误: 期望 {tier}, 实际 {NATURE_TO_TIER[nature]}"
            )

    def test_fullauto_service_alias_matches_canonical(self):
        """FullAutoTradingService._NATURE_TO_TIER_MAP 类属性应与 canonical 完全一致"""
        from backend.services.sub_position_manager import NATURE_TO_TIER
        from backend.services.full_auto_trading_service import FullAutoTradingService
        assert FullAutoTradingService._NATURE_TO_TIER_MAP is NATURE_TO_TIER, (
            "FullAutoTradingService._NATURE_TO_TIER_MAP 应该是 NATURE_TO_TIER 的直接引用（同一对象）"
        )

    def test_normalize_nature_aliases(self):
        """normalize_nature 应正确处理别名"""
        from backend.services.sub_position_manager import normalize_nature
        assert normalize_nature("position") == "trend_follow"
        assert normalize_nature("scalp") == "intraday"
        assert normalize_nature("swing") == "swing"
        assert normalize_nature("trend_follow") == "trend_follow"
        assert normalize_nature("intraday") == "intraday"

    def test_normalize_nature_unknown_defaults_swing(self):
        """normalize_nature 对未知值默认返回 swing"""
        from backend.services.sub_position_manager import normalize_nature
        assert normalize_nature("unknown_nature") == "swing"
        assert normalize_nature(None) == "swing"
        assert normalize_nature("") == "swing"

    def test_normalize_nature_case_insensitive(self):
        """normalize_nature 应大小写不敏感"""
        from backend.services.sub_position_manager import normalize_nature
        assert normalize_nature("SWING") == "swing"
        assert normalize_nature("Scalp") == "intraday"
        assert normalize_nature("TREND_FOLLOW") == "trend_follow"

    def test_nature_rules_cover_all_main_types(self):
        """NATURE_RULES 应覆盖三个主类型: trend_follow, swing, intraday"""
        from backend.services.sub_position_manager import NATURE_RULES
        for main_type in ("trend_follow", "swing", "intraday"):
            assert main_type in NATURE_RULES, f"NATURE_RULES 缺少 {main_type}"
            rules = NATURE_RULES[main_type]
            assert "reduce_cooldown_hours" in rules
            assert "max_reduce_ratio" in rules
            assert "position_weight" in rules

    def test_tier_values_are_valid(self):
        """NATURE_TO_TIER 所有 tier 值应为 short/mid/long"""
        from backend.services.sub_position_manager import NATURE_TO_TIER
        valid_tiers = {"short", "mid", "long"}
        for nature, tier in NATURE_TO_TIER.items():
            assert tier in valid_tiers, f"{nature} → {tier} 不是有效的 tier"


# ════════════════════════════════════════════════════════
#  5. 死代码 @deprecated 标记验证
# ════════════════════════════════════════════════════════

class TestDeprecatedMarking:
    """验证已标记为 deprecated 的模块/函数发出正确的警告"""

    def test_exchange_config_is_binance_active_returns_false(self):
        """is_binance_active() 应始终返回 False"""
        from backend.services.exchange_config import is_binance_active
        assert is_binance_active() is False

    def test_exchange_config_get_active_returns_hyperliquid(self):
        """get_active_exchange() 应返回 hyperliquid"""
        from backend.services.exchange_config import get_active_exchange
        result = get_active_exchange()
        assert result == "hyperliquid"

    def test_exchange_config_is_hyperliquid_active(self):
        """is_hyperliquid_active() 应返回 True"""
        from backend.services.exchange_config import is_hyperliquid_active
        assert is_hyperliquid_active() is True

    def test_set_active_exchange_emits_deprecation_warning(self):
        """set_active_exchange() 应发出 DeprecationWarning"""
        from backend.services.exchange_config import set_active_exchange
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            set_active_exchange("hyperliquid")
            dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(dep_warnings) >= 1, "set_active_exchange 应发出 DeprecationWarning"

    def test_set_active_exchange_rejects_invalid(self):
        """set_active_exchange() 应拒绝无效交易所名"""
        from backend.services.exchange_config import set_active_exchange
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            with pytest.raises(ValueError, match="Invalid exchange"):
                set_active_exchange("coinbase")


# ════════════════════════════════════════════════════════
#  6. ORCHESTRATOR_HARD_GATE 配置验证
# ════════════════════════════════════════════════════════

class TestOrchestratorHardGateConfig:
    """验证 ORCHESTRATOR_HARD_GATE 配置项"""

    def test_hard_gate_is_bool(self):
        """ORCHESTRATOR_HARD_GATE 应为布尔值"""
        from backend.config.settings import ORCHESTRATOR_HARD_GATE
        assert isinstance(ORCHESTRATOR_HARD_GATE, bool)

    def test_hard_gate_default_true(self):
        """默认应启用硬门控"""
        from backend.config.settings import ORCHESTRATOR_HARD_GATE
        assert ORCHESTRATOR_HARD_GATE is True

# ════════════════════════════════════════════════════════
#  7. _append_event 静态方法验证
# ════════════════════════════════════════════════════════

class TestAppendEvent:
    """验证 _append_event 静态方法的基本行为"""

    def test_appends_to_event_log(self):
        """_append_event 应将事件追加到 session.event_log"""
        from backend.services.full_auto_trading_service import FullAutoTradingService
        session = MagicMock()
        session.event_log = []
        FullAutoTradingService._append_event(session, "test_event", "test detail")
        assert len(session.event_log) == 1
        assert session.event_log[0]["event"] == "test_event"
        assert session.event_log[0]["detail"] == "test detail"

    def test_event_log_truncated_at_200(self):
        """event_log 超过 200 条时自动截断"""
        from backend.services.full_auto_trading_service import FullAutoTradingService
        session = MagicMock()
        session.event_log = []
        for i in range(210):
            FullAutoTradingService._append_event(session, f"event_{i}", f"detail_{i}")
        assert len(session.event_log) == 200
        # 最早的事件应被截掉
        assert session.event_log[0]["event"] == "event_10"

    def test_severity_info_not_stored(self):
        """severity=info 时不应存储 severity 字段"""
        from backend.services.full_auto_trading_service import FullAutoTradingService
        session = MagicMock()
        session.event_log = []
        FullAutoTradingService._append_event(session, "test", "detail", severity="info")
        assert "severity" not in session.event_log[0]

    def test_severity_warning_stored(self):
        """severity=warning 时应存储 severity 字段"""
        from backend.services.full_auto_trading_service import FullAutoTradingService
        session = MagicMock()
        session.event_log = []
        FullAutoTradingService._append_event(session, "test", "detail", severity="warning")
        assert session.event_log[0]["severity"] == "warning"
