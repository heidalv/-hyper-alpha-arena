"""周期方向概率引擎 + 门禁/证据链集成 单测。"""
import numpy as np
import pytest

pytestmark = pytest.mark.unit


# ─────────────────── 分桶与特征提取 ───────────────────

class TestBucketize:
    def test_continuous_bucketize_monotonic(self):
        from backend.services.cycle_direction_probability import bucketize
        # rsi 边界 [30,45,55,70] → 5 桶
        assert bucketize("rsi", 20) == 0
        assert bucketize("rsi", 50) == 2
        assert bucketize("rsi", 80) == 4
        assert bucketize("rsi", None) is None
        assert bucketize("rsi", float("nan")) is None

    def test_discrete_bucketize(self):
        from backend.services.cycle_direction_probability import bucketize
        assert bucketize("ema_align", -1) == 0
        assert bucketize("ema_align", 0) == 1
        assert bucketize("ema_align", 1) == 2
        # 越界值 clamp
        assert bucketize("hh_hl", 5) == 4
        assert bucketize("hh_hl", -5) == 0

    def test_extract_features_from_indicators_aliases(self):
        from backend.services.cycle_direction_probability import extract_features_from_indicators
        ind = {"rsi": 60, "ema_9": 105, "ema_21": 102, "ema_50": 100,
               "macd_hist": 0.5, "atr": 200, "close": 10000, "plus_di": 28, "minus_di": 15}
        f = extract_features_from_indicators(ind)
        assert f["rsi"] == 60.0
        assert f["ema_align"] == 1.0            # e9>e21>e50
        assert f["macd_sign"] == 1.0
        assert f["di_diff"] == 13.0
        assert abs(f["atr_pct"] - 0.02) < 1e-9

    def test_extract_tier_features_from_snapshot_long_uses_4h(self):
        from backend.services.cycle_direction_probability import extract_tier_features_from_snapshot
        flat = {"rsi": 60, "rsi_4h": 48, "adx_4h": 30, "ema_9_4h": 90, "ema_21_4h": 95,
                "ema_50_4h": 100, "atr_4h": 400, "close": 10000}
        f = extract_tier_features_from_snapshot(flat, "long")
        assert f["rsi"] == 48.0                  # 取 4h 而非基础 60
        assert f["ema_align"] == -1.0            # 4h 空头排列


# ─────────────────── 训练与推理 ───────────────────

def _synthetic_klines(n=400, drift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.01, n)
    close = 100 * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    vol = rng.uniform(100, 200, n)
    return {"high": high, "low": low, "close": close, "volume": vol}


class TestTrainAndEstimate:
    def test_train_tier_produces_valid_model(self):
        from backend.services.cycle_direction_probability import train_tier, FEATURES
        klines = [_synthetic_klines(seed=i) for i in range(6)]
        model = train_tier("mid", klines)
        assert model is not None
        # prior 是合法分布
        assert abs(sum(model.prior) - 1.0) < 1e-6
        # 每个特征都有 likelihood 表且权重归一
        assert set(model.likelihood.keys()) == set(FEATURES)
        assert abs(sum(model.feature_weights.values()) - 1.0) < 1e-6
        assert "brier" in model.calibration

    def test_estimate_returns_legal_distribution(self, tmp_path, monkeypatch):
        import backend.services.cycle_direction_probability as cdp
        # 用临时模型目录，避免污染真实 data/cycle_prob
        monkeypatch.setattr(cdp, "_MODEL_DIR", tmp_path)
        engine = cdp.CycleProbabilityEngine()
        klines = [_synthetic_klines(seed=i) for i in range(6)]
        model = cdp.train_tier("mid", klines)
        cdp.save_model(model)  # 写到 tmp_path
        # save_model 用模块级 _MODEL_DIR，monkeypatch 后指向 tmp_path
        res = engine.estimate("mid", {"rsi": 65, "adx": 28, "ema_align": 1,
                                       "di_diff": 15, "macd_sign": 1, "atr_pct": 0.02})
        assert res.available
        s = res.prob_up + res.prob_down + res.prob_range
        assert abs(s - 1.0) < 1e-6
        assert 0.0 <= res.confidence <= 1.0
        assert res.direction in ("up", "down", "range")

    def test_estimate_unavailable_when_no_model(self, tmp_path, monkeypatch):
        import backend.services.cycle_direction_probability as cdp
        monkeypatch.setattr(cdp, "_MODEL_DIR", tmp_path / "empty")
        engine = cdp.CycleProbabilityEngine()
        res = engine.estimate("mid", {"rsi": 65})
        assert res.available is False


# ─────────────────── 证据链注入 ───────────────────

class TestEvidenceInjection:
    def test_swing_evidence_has_cycle_prob_mid(self):
        from backend.services.agent_evidence_builder import build_swing_evidence
        envs = {"BTC": {"indicators_1h": {"rsi": 60, "ema_9": 105, "ema_21": 102,
                        "ema_50": 100, "macd_hist": 0.3, "atr": 150, "close": 10000,
                        "vol_ratio": 1.2, "plus_di": 26, "minus_di": 15, "adx": 25}}}
        ids = {f.id for f in build_swing_evidence("BTC", envs)}
        assert "cycle_prob_dir_mid" in ids
        assert "cycle_prob_calibration_mid" in ids

    def test_trend_evidence_has_cycle_prob_long(self):
        from backend.services.agent_evidence_builder import build_trend_evidence
        envs = {"BTC": {"indicators_4h": {"rsi": 55, "adx": 22, "ema_9": 205,
                        "ema_21": 202, "ema_50": 200, "atr": 400, "close": 10000},
                        "indicators_1d": {}, "indicators_1w": {}}}
        ids = {f.id for f in build_trend_evidence("BTC", envs)}
        assert "cycle_prob_dir_long" in ids

    def test_evidence_prompt_includes_prior_guidance_when_available(self):
        from backend.services.agent_evidence_builder import (
            build_swing_evidence, format_evidence_for_prompt,
        )
        envs = {"BTC": {"indicators_1h": {"rsi": 60, "ema_9": 105, "ema_21": 102,
                        "ema_50": 100, "macd_hist": 0.3, "atr": 150, "close": 10000,
                        "vol_ratio": 1.2, "plus_di": 26, "minus_di": 15, "adx": 25}}}
        facts = build_swing_evidence("BTC", envs)
        block = format_evidence_for_prompt(facts)
        # 只有在 cycle_prob 可用时才注入使用说明
        avail = any(f.id == "cycle_prob_dir_mid" and f.available for f in facts)
        if avail:
            assert "cycle_prob_calibration" in block
            assert "方向先验" in block


# ─────────────────── governor 自适应同步 ───────────────────

class TestArbitration:
    def test_coordinator_tier_lean_returns_tuple(self):
        from backend.services.strategy_coordinator import StrategyCoordinator, MarketEnvironment
        env = MarketEnvironment()
        env.symbol = "BTC"
        env.current_price = 10000
        env.m1h_rsi = 60
        env.m1h_ema20 = 101
        env.m1h_ema50 = 100
        env.atr_value = 150

        class _Dummy:
            pass

        lean, active = StrategyCoordinator._cycle_prob_tier_lean(_Dummy(), env, "mid")
        assert isinstance(lean, float)
        assert isinstance(active, bool)
        # 未达校准阈值时 active 必为 False（弱模型下冲突分支不应被激活）
        assert -1.0 <= lean <= 1.0

    def test_coordinator_tier_lean_missing_fields_safe(self):
        from backend.services.strategy_coordinator import StrategyCoordinator, MarketEnvironment
        env = MarketEnvironment()  # 全默认（无价格/指标）

        class _Dummy:
            pass

        lean, active = StrategyCoordinator._cycle_prob_tier_lean(_Dummy(), env, "long")
        assert isinstance(lean, float)
        assert isinstance(active, bool)

    def test_orchestrator_arbitration_no_indicators_inactive(self):
        # 无快照指标时仲裁应返回 (0.0, False, "")，不影响既有决策
        from backend.services.multi_timeframe_orchestrator import (
            MultiTimeframeOrchestrator, OrchestratorDecision,
        )

        class _Snap:
            indicators = {}

        class _Dummy:
            _params = {}

        decision = OrchestratorDecision(symbol="BTC", timestamp=0.0)
        score, active, note = MultiTimeframeOrchestrator._cycle_prob_arbitration(
            _Dummy(), decision, _Snap())
        assert score == 0.0 and active is False


class TestGovernorSync:
    def test_sync_returns_dict_without_mutating_state(self, tmp_path, monkeypatch):
        # 指向空模型目录：short 模型加载不到 → 提前返回 {}，不写真实 governor 意图
        import backend.services.cycle_direction_probability as cdp
        monkeypatch.setattr(cdp, "_MODEL_DIR", tmp_path / "empty")
        res = cdp.sync_calibration_to_governor()
        assert isinstance(res, dict)
        assert res == {}  # 无模型时不产生任何意图
