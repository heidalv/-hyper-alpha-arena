"""
test_signal_bus_deep — 统一信号总线深入测试

直接测试融合算法、共振/冲突检测、缓存机制、权重更新，
无需启动外部服务（通过 mock 隔离依赖）。
"""

import time
import math
import threading
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from backend.services.signal_engine.unified_signal import (
    SourceSignal, UnifiedSignal,
    SOURCE_FACTOR, SOURCE_INTEL, SOURCE_CONFIRM, SOURCE_FUSION,
    CONFLUENCE_STRONG_RESONANCE, CONFLUENCE_RESONANCE, CONFLUENCE_NEUTRAL,
    CONFLUENCE_CONFLICT, CONFLUENCE_STRONG_CONFLICT,
    ACTION_BUY, ACTION_SELL, ACTION_HOLD,
    direction_to_action, clamp, make_empty_signal,
)
from backend.services.signal_engine.signal_bus import (
    UnifiedSignalBus, DEFAULT_SOURCE_WEIGHTS,
)


# ════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════

def _make_source(source_id: str, direction: float, confidence: float,
                  strength: float = None, weight: float = None,
                  action: str = None, raw_data: dict = None) -> SourceSignal:
    """构造 SourceSignal"""
    if strength is None:
        strength = abs(direction)
    if weight is None:
        weight = DEFAULT_SOURCE_WEIGHTS.get(source_id, 0.25)
    if action is None:
        action = direction_to_action(direction)
    if raw_data is None:
        raw_data = {}

    names = {
        SOURCE_FACTOR: "因子引擎",
        SOURCE_INTEL: "情报汇流",
        SOURCE_CONFIRM: "三维确认",
        SOURCE_FUSION: "决策融合",
    }

    return SourceSignal(
        source_id=source_id,
        source_name=names.get(source_id, source_id),
        direction=direction,
        confidence=confidence,
        strength=strength,
        weight=weight,
        action=action,
        timestamp=time.time(),
        raw_data=raw_data,
    )


def _create_test_bus():
    """创建独立的测试用总线（避免单例干扰）"""
    bus = object.__new__(UnifiedSignalBus)
    bus._initialized = True
    bus._cache = {}
    bus._cache_ts = {}
    bus._cache_ttl = 45.0
    bus._max_cache_size = 200
    bus._source_weights = dict(DEFAULT_SOURCE_WEIGHTS)
    bus._detail_cache = {}
    bus._detail_cache_ts = {}
    return bus


# ════════════════════════════════════════════════════════
#  1. 融合算法测试
# ════════════════════════════════════════════════════════

class TestFusionAlgorithm:
    """融合算法核心逻辑测试"""

    def test_single_bullish_source(self):
        """单个看多源 → 方向为正"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9),
        }
        result = bus._fuse_signals("BTC", sources)
        assert result.direction > 0
        assert result.action == ACTION_BUY
        assert result.source_count == 1

    def test_single_bearish_source(self):
        """单个看空源 → 方向为负"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, -0.8, 0.9),
        }
        result = bus._fuse_signals("BTC", sources)
        assert result.direction < 0
        assert result.action == ACTION_SELL

    def test_single_neutral_source(self):
        """单个中性源 → hold"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.0, 0.5),
        }
        result = bus._fuse_signals("BTC", sources)
        assert result.action == ACTION_HOLD

    def test_weighted_direction_aggregation(self):
        """加权方向聚合：高权重源主导方向"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9, weight=0.50),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, -0.3, 0.6, weight=0.10),
        }
        result = bus._fuse_signals("BTC", sources)
        # 因子权重 0.50, 情报权重 0.10, 因子看多应主导
        assert result.direction > 0.3

    def test_confidence_floor_0_01(self):
        """confidence 的 0.01 下限确保零置信源仍贡献"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 1.0, 0.0, weight=0.35),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.0, 0.9, weight=0.30),
        }
        result = bus._fuse_signals("BTC", sources)
        # factor confidence=0 但 floor=0.01, 方向 1.0 * 0.35 * 0.01 = 0.0035
        # intel confidence=0.9, 方向 0.0
        # weighted_dir ≈ 0.0035 / (0.35*0.01 + 0.30*0.9) ≈ 0.013 → hold
        assert result.direction >= 0

    def test_direction_output_clamped(self):
        """输出方向被裁剪到 [-1, +1]"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 1.0, 1.0, weight=0.35),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 1.0, 1.0, weight=0.30),
            SOURCE_CONFIRM: _make_source(SOURCE_CONFIRM, 1.0, 1.0, weight=0.20),
            SOURCE_FUSION: _make_source(SOURCE_FUSION, 1.0, 1.0, weight=0.15),
        }
        result = bus._fuse_signals("BTC", sources)
        assert -1.0 <= result.direction <= 1.0

    def test_strength_output_clamped(self):
        """输出强度被裁剪到 [0, 1]"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.5, 0.8),
        }
        result = bus._fuse_signals("BTC", sources)
        assert 0.0 <= result.strength <= 1.0


# ════════════════════════════════════════════════════════
#  2. 共振 / 冲突检测测试
# ════════════════════════════════════════════════════════

class TestConfluenceDetection:
    """共振与冲突检测逻辑"""

    def test_all_four_agree_strong_resonance(self):
        """4 源全部看多 → strong_resonance"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.7, 0.8),
            SOURCE_CONFIRM: _make_source(SOURCE_CONFIRM, 0.6, 0.7),
            SOURCE_FUSION: _make_source(SOURCE_FUSION, 0.5, 0.6),
        }
        result = bus._fuse_signals("BTC", sources)
        assert result.confluence_level == CONFLUENCE_STRONG_RESONANCE
        assert result.agreeing_sources == 4
        assert result.conflicting_sources == 0

    def test_three_agree_one_neutral_resonance(self):
        """3 源看多 + 1 中性 → resonance"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.7, 0.8),
            SOURCE_CONFIRM: _make_source(SOURCE_CONFIRM, 0.6, 0.7),
            SOURCE_FUSION: _make_source(SOURCE_FUSION, 0.0, 0.5),  # 中性
        }
        result = bus._fuse_signals("BTC", sources)
        assert result.confluence_level == CONFLUENCE_RESONANCE

    def test_two_bullish_two_bearish_strong_conflict(self):
        """2 多 + 2 空 → strong_conflict"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.7, 0.8),
            SOURCE_CONFIRM: _make_source(SOURCE_CONFIRM, -0.6, 0.7),
            SOURCE_FUSION: _make_source(SOURCE_FUSION, -0.5, 0.6),
        }
        result = bus._fuse_signals("BTC", sources)
        assert result.confluence_level == CONFLUENCE_STRONG_CONFLICT
        assert result.agreeing_sources == 2
        assert result.conflicting_sources == 2

    def test_three_bullish_one_bearish_conflict(self):
        """3 多 + 1 空 → conflict"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.7, 0.8),
            SOURCE_CONFIRM: _make_source(SOURCE_CONFIRM, 0.6, 0.7),
            SOURCE_FUSION: _make_source(SOURCE_FUSION, -0.5, 0.6),
        }
        result = bus._fuse_signals("BTC", sources)
        assert result.confluence_level == CONFLUENCE_CONFLICT
        assert result.agreeing_sources == 3
        assert result.conflicting_sources == 1

    def test_single_source_neutral_confluence(self):
        """单个源 → neutral"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9),
        }
        result = bus._fuse_signals("BTC", sources)
        assert result.confluence_level == CONFLUENCE_NEUTRAL

    def test_resonance_boosts_confidence(self):
        """共振应提升置信度"""
        bus = _create_test_bus()
        # 4 源一致看多
        sources_resonance = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.8),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.7, 0.8),
            SOURCE_CONFIRM: _make_source(SOURCE_CONFIRM, 0.6, 0.8),
            SOURCE_FUSION: _make_source(SOURCE_FUSION, 0.5, 0.8),
        }
        result_res = bus._fuse_signals("BTC", sources_resonance)

        # 重建冲突场景
        bus2 = _create_test_bus()
        sources_conflict = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.8),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, -0.7, 0.8),
        }
        result_conf = bus2._fuse_signals("BTC", sources_conflict)

        # 同样置信度的源，共振场景置信度应高于冲突
        assert result_res.confidence > result_conf.confidence

    def test_conflict_reduces_confidence(self):
        """冲突应降低置信度"""
        bus = _create_test_bus()
        sources_agree = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.7),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.7, 0.7),
        }
        result_agree = bus._fuse_signals("BTC", sources_agree)

        bus2 = _create_test_bus()
        sources_conflict = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.7),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, -0.7, 0.7),
        }
        result_conf = bus2._fuse_signals("BTC", sources_conflict)

        assert result_agree.confidence > result_conf.confidence


# ════════════════════════════════════════════════════════
#  3. 缓存机制测试
# ════════════════════════════════════════════════════════

class TestCacheMechanism:
    """缓存 TTL、刷新、清理测试"""

    def test_cache_returns_same_object(self):
        """缓存命中返回同一对象"""
        bus = _create_test_bus()
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.5, 0.8),
        }
        with patch.object(bus, '_collect_all_sources', return_value=mock_sources):
            r1 = bus.get_unified_signal("BTC")
            r2 = bus.get_unified_signal("BTC")
            assert r1 is r2

    def test_force_refresh_bypasses_cache(self):
        """force_refresh=True 绕过缓存"""
        bus = _create_test_bus()
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.5, 0.8),
        }
        with patch.object(bus, '_collect_all_sources', return_value=mock_sources):
            r1 = bus.get_unified_signal("BTC")
            r2 = bus.get_unified_signal("BTC", force_refresh=True)
            assert r1 is not r2

    def test_cache_ttl_expiry(self):
        """缓存 TTL 过期后重新获取"""
        bus = _create_test_bus()
        bus._cache_ttl = 0.01  # 10ms TTL
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.5, 0.8),
        }
        with patch.object(bus, '_collect_all_sources', return_value=mock_sources):
            r1 = bus.get_unified_signal("BTC")
            time.sleep(0.02)  # 等待过期
            r2 = bus.get_unified_signal("BTC")
            assert r1 is not r2

    def test_cache_cleanup_on_overflow(self):
        """缓存超过 200 条时自动清理"""
        bus = _create_test_bus()
        bus._max_cache_size = 10
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.5, 0.8),
        }
        with patch.object(bus, '_collect_all_sources', return_value=mock_sources):
            for i in range(20):
                bus.get_unified_signal(f"SYM{i}")
            assert len(bus._cache) <= bus._max_cache_size

    def test_empty_sources_returns_empty_signal(self):
        """无信号源返回空信号"""
        bus = _create_test_bus()
        with patch.object(bus, '_collect_all_sources', return_value={}):
            result = bus.get_unified_signal("BTC")
            assert result.action == ACTION_HOLD
            assert result.direction == 0.0
            assert result.source_count == 0

    def test_update_weights_clears_cache(self):
        """更新权重后缓存清空"""
        bus = _create_test_bus()
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.5, 0.8),
        }
        with patch.object(bus, '_collect_all_sources', return_value=mock_sources):
            bus.get_unified_signal("BTC")
            assert len(bus._cache) == 1

        bus.update_source_weights({SOURCE_FACTOR: 0.50})
        assert len(bus._cache) == 0

    def test_update_weights_clamps_values(self):
        """权重值被裁剪到 [0, 1]"""
        bus = _create_test_bus()
        bus.update_source_weights({SOURCE_FACTOR: 2.0})
        assert bus._source_weights[SOURCE_FACTOR] == 1.0

        bus.update_source_weights({SOURCE_FACTOR: -1.0})
        assert bus._source_weights[SOURCE_FACTOR] == 0.0

    def test_update_weights_ignores_unknown_keys(self):
        """未知源ID被忽略"""
        bus = _create_test_bus()
        bus.update_source_weights({"unknown_source": 0.5})
        assert "unknown_source" not in bus._source_weights


# ════════════════════════════════════════════════════════
#  4. 推理文本生成测试
# ════════════════════════════════════════════════════════

class TestReasoningGeneration:
    """推理文本格式测试"""

    def test_reasoning_contains_action_cn(self):
        """推理包含中文动作"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.7, 0.8),
        }
        result = bus._fuse_signals("BTC", sources)
        assert "看多" in result.reasoning

    def test_reasoning_contains_source_names(self):
        """推理包含信号源名称"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, -0.5, 0.7),
        }
        result = bus._fuse_signals("BTC", sources)
        assert "因子引擎" in result.reasoning
        assert "情报汇流" in result.reasoning

    def test_reasoning_contains_confluence(self):
        """推理包含共振等级"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.7, 0.8),
        }
        result = bus._fuse_signals("BTC", sources)
        assert "共振" in result.reasoning

    def test_reasoning_bearish(self):
        """看空推理包含 '看空'"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, -0.8, 0.9),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, -0.7, 0.8),
        }
        result = bus._fuse_signals("BTC", sources)
        assert "看空" in result.reasoning


# ════════════════════════════════════════════════════════
#  5. Regime 提取测试
# ════════════════════════════════════════════════════════

class TestRegimeExtraction:
    """市场状态提取逻辑"""

    def test_regime_from_factor_source(self):
        """regime 从因子源的 raw_data 提取"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(
                SOURCE_FACTOR, 0.8, 0.9,
                raw_data={"regime": "breakout"},
            ),
        }
        result = bus._fuse_signals("BTC", sources)
        assert result.regime == "breakout"

    def test_regime_unknown_when_no_factor(self):
        """无因子源时 regime 为 unknown"""
        bus = _create_test_bus()
        sources = {
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.7, 0.8),
        }
        result = bus._fuse_signals("BTC", sources)
        assert result.regime == "unknown"

    def test_regime_unknown_when_no_raw_data(self):
        """因子源无 raw_data 时 regime 为 unknown"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9, raw_data=None),
        }
        result = bus._fuse_signals("BTC", sources)
        assert result.regime == "unknown"


# ════════════════════════════════════════════════════════
#  6. 并发安全测试
# ════════════════════════════════════════════════════════

class TestConcurrencySafety:
    """多线程并发访问安全测试"""

    def test_concurrent_reads(self):
        """多线程并发读取不崩溃"""
        bus = _create_test_bus()
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.5, 0.8),
        }
        errors = []

        def reader(sym):
            try:
                with patch.object(bus, '_collect_all_sources', return_value=mock_sources):
                    result = bus.get_unified_signal(sym)
                    assert result is not None
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader, args=(f"SYM{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_concurrent_weight_update_and_read(self):
        """并发更新权重和读取不崩溃"""
        bus = _create_test_bus()
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.5, 0.8),
        }
        errors = []

        def reader():
            try:
                for i in range(20):
                    with patch.object(bus, '_collect_all_sources', return_value=mock_sources):
                        bus.get_unified_signal(f"SYM{i % 5}")
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for i in range(10):
                    bus.update_source_weights({SOURCE_FACTOR: 0.3 + (i % 5) * 0.05})
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0


# ════════════════════════════════════════════════════════
#  7. get_signal_detail 测试
# ════════════════════════════════════════════════════════

class TestSignalDetail:
    """信号详细分解测试"""

    def test_detail_structure(self):
        """detail 返回结构完整"""
        bus = _create_test_bus()
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.7, 0.85),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.5, 0.7),
        }
        with patch.object(bus, '_collect_all_sources', return_value=mock_sources):
            detail = bus.get_signal_detail("BTC")

        assert "symbol" in detail
        assert "direction" in detail
        assert "confidence" in detail
        assert "action" in detail
        assert "confluence_level" in detail
        assert "sources" in detail
        assert "confluence_debug" in detail
        assert detail["symbol"] == "BTC"

    def test_detail_source_breakdown(self):
        """detail 包含各源分解数据"""
        bus = _create_test_bus()
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.7, 0.85),
        }
        with patch.object(bus, '_collect_all_sources', return_value=mock_sources):
            detail = bus.get_signal_detail("BTC")

        assert SOURCE_FACTOR in detail["sources"]
        factor_detail = detail["sources"][SOURCE_FACTOR]
        assert factor_detail["direction"] == 0.7
        assert factor_detail["confidence"] == 0.85

    def test_detail_confluence_debug(self):
        """detail 包含融合调试信息"""
        bus = _create_test_bus()
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.7, 0.85),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, -0.3, 0.6),
        }
        with patch.object(bus, '_collect_all_sources', return_value=mock_sources):
            detail = bus.get_signal_detail("BTC")

        debug = detail["confluence_debug"]
        assert debug["source_count"] == 2
        assert "source_weights" in debug

    def test_detail_active_triggers(self):
        """detail 包含 active_triggers"""
        bus = _create_test_bus()
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.5, 0.8),
        }
        with patch.object(bus, '_collect_all_sources', return_value=mock_sources), \
             patch.object(bus, '_get_active_triggers', return_value=[]):
            detail = bus.get_signal_detail("BTC")
        assert "active_triggers" in detail

    def test_detail_cached(self):
        """detail 结果被缓存"""
        bus = _create_test_bus()
        mock_sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.5, 0.8),
        }
        with patch.object(bus, '_collect_all_sources', return_value=mock_sources):
            d1 = bus.get_signal_detail("BTC")
            d2 = bus.get_signal_detail("BTC")
            # 缓存命中应是同一对象
            assert d1 is d2


# ════════════════════════════════════════════════════════
#  8. 数值精度与边界测试
# ════════════════════════════════════════════════════════

class TestFusionNumerics:
    """融合算法数值精度边界测试"""

    def test_all_sources_zero_confidence(self):
        """所有源 confidence=0 时仍能计算（floor=0.01）"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.0),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.7, 0.0),
        }
        result = bus._fuse_signals("BTC", sources)
        assert -1.0 <= result.direction <= 1.0
        assert 0.0 <= result.confidence <= 1.0

    def test_extreme_directions(self):
        """极端方向值（+1.0 和 -1.0）"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 1.0, 1.0),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, -1.0, 1.0),
            SOURCE_CONFIRM: _make_source(SOURCE_CONFIRM, 1.0, 1.0),
            SOURCE_FUSION: _make_source(SOURCE_FUSION, -1.0, 1.0),
        }
        result = bus._fuse_signals("BTC", sources)
        assert -1.0 <= result.direction <= 1.0
        assert -1.0 <= result.confidence <= 1.0

    def test_very_small_directions(self):
        """极小方向值 → hold"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.01, 0.5),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, 0.02, 0.5),
        }
        result = bus._fuse_signals("BTC", sources)
        # 方向 < 0.2 → hold
        assert result.action == ACTION_HOLD

    def test_source_with_zero_weight(self):
        """零权重源不影响结果"""
        bus = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.8, 0.9, weight=0.35),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, -0.8, 0.9, weight=0.0),
        }
        result = bus._fuse_signals("BTC", sources)
        # intel weight=0 但仍然参与 confluence 统计
        assert result.direction > 0  # factor 看多主导

    def test_default_weights_sum(self):
        """默认源权重之和 = 1.0"""
        total = sum(DEFAULT_SOURCE_WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=0.001)

    def test_fusion_deterministic(self):
        """相同输入产生相同输出"""
        bus1 = _create_test_bus()
        bus2 = _create_test_bus()
        sources = {
            SOURCE_FACTOR: _make_source(SOURCE_FACTOR, 0.65, 0.82),
            SOURCE_INTEL: _make_source(SOURCE_INTEL, -0.3, 0.55),
        }
        r1 = bus1._fuse_signals("BTC", sources)
        r2 = bus2._fuse_signals("BTC", sources)
        assert r1.direction == r2.direction
        assert r1.confidence == r2.confidence
        assert r1.action == r2.action
        assert r1.confluence_level == r2.confluence_level
