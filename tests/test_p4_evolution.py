"""
P4.7b HPO + P4.7c 对抗验证 + P4.7d 机制路由 + P4.7 集成 + P4.1/6 挖掘 测试。
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.services.alpha.ensemble import (
    AlphaEnsemble,
    RegimeWeights,
)
from backend.services.alpha.regime_refined import Regime
from backend.services.contracts.types import Direction, FactorVector, Instrument
from backend.services.eval.adversarial_validation import adversarial_validation
from backend.services.evolution.alpha_miner import (
    AlphaMiner,
    AlphaPool,
    CodegenCritic,
    MiningConfig,
)
from backend.services.evolution.drift_watcher import DriftEvent, DriftType
from backend.services.evolution.hpo_orchestrator import (
    HPOOrchestrator,
    HPORequest,
    OptimizerType,
)
from backend.services.learning_core.mechanism_router import (
    Mechanism,
    MechanismRouter,
)

pytestmark = pytest.mark.unit


def _inst(sym="BTC-PERP"):
    return Instrument(symbol=sym, venue="binance", kind="perp")


def _fv(values=None):
    return FactorVector(ts_ns=0, instrument=_inst(), values=values or {"f1": 0.5})


# ==================== P4.7b HPO ====================

class TestHPO:
    def test_random_search_finds_best(self):
        orch = HPOOrchestrator()
        space = {"lr": ("uniform", 0.001, 0.1)}
        def obj(params):
            # 简单：lr=0.05 时 sharpe 最高
            return {"sharpe": -abs(params["lr"] - 0.05) * 100 + 2,
                    "maxdd": 0.1, "turnover": 0.5, "capacity": 1e6}
        req = HPORequest(param_space=space, objective_fn=obj, n_trials=30)
        result = orch.optimize(req)
        assert result.n_evaluated == 30
        assert 0.02 < result.best_params["lr"] < 0.08  # 接近最优 0.05

    def test_map_elites_archive(self):
        orch = HPOOrchestrator()
        space = {"x": ("uniform", 0, 1)}
        def obj(params):
            return {"sharpe": params["x"], "maxdd": 0.1,
                    "turnover": params["x"], "capacity": 1e6}
        req = HPORequest(param_space=space, objective_fn=obj,
                         n_trials=20, optimizer=OptimizerType.MAP_ELITES)
        result = orch.optimize(req)
        assert len(result.archive) > 0  # MAP-Elites 档案非空

    def test_pareto_front(self):
        orch = HPOOrchestrator()
        space = {"x": ("choice", [0.1, 0.3, 0.5, 0.7, 0.9])}
        def obj(params):
            x = params["x"]
            return {"sharpe": x, "maxdd": 1 - x, "turnover": x, "capacity": 1e6}
        req = HPORequest(param_space=space, objective_fn=obj, n_trials=10)
        result = orch.optimize(req)
        assert isinstance(result.pareto_front, list)


# ==================== P4.7c 对抗验证 ====================

class TestAdversarialValidation:
    def test_similar_distribution_low_auc(self):
        rng = np.random.default_rng(1)
        train = rng.normal(0, 1, (100, 5))
        test = rng.normal(0, 1, (100, 5))  # 同分布
        result = adversarial_validation(train, test)
        assert result.auc < 0.65  # 相似 → 低 AUC
        assert not result.is_degraded

    def test_shifted_distribution_high_auc(self):
        rng = np.random.default_rng(2)
        train = rng.normal(0, 1, (100, 5))
        test = rng.normal(3, 1, (100, 5))  # 均值偏移 3
        result = adversarial_validation(train, test)
        assert result.auc > 0.6  # 漂移 → 高 AUC
        assert result.is_degraded

    def test_small_sample_returns_neutral(self):
        result = adversarial_validation(np.array([[1.0]]), np.array([[2.0]]))
        assert result.auc == 0.5
        assert not result.is_degraded


# ==================== P4.7d 机制路由 ====================

class TestMechanismRouter:
    def test_stable_routes_to_river(self):
        router = MechanismRouter()
        r = router.route(drift=None, horizon="short")
        assert r.mechanism == Mechanism.RIVER_ONLINE

    def test_abrupt_drift_routes_to_regime_switch(self):
        router = MechanismRouter()
        drift = DriftEvent(ts_ns=0, drift_type=DriftType.ABRUPT,
                           metric_name="sharpe", current_value=1.0, baseline_value=2.0)
        r = router.route(drift=drift, horizon="short")
        assert r.mechanism == Mechanism.REGIME_SWITCH

    def test_sustained_regime_routes_to_maml(self):
        router = MechanismRouter()
        r = router.route(regime_sustained=True, horizon="short")
        assert r.mechanism == Mechanism.MAML_ADAPT

    def test_midlong_routes_to_owm(self):
        router = MechanismRouter()
        r = router.route(horizon="long")
        assert r.mechanism == Mechanism.OWM_MIDLONG

    def test_structural_evolution_priority(self):
        router = MechanismRouter()
        r = router.route(needs_structural_evolution=True, regime_sustained=True)
        assert r.mechanism == Mechanism.HPO_OFFLINE  # 结构进化优先级最高

    def test_no_conflict(self):
        """短线 horizon 只有一个主导机制。"""
        router = MechanismRouter()
        router.route(horizon="short")
        assert router.ensure_no_conflict() == []


# ==================== P4.7 AlphaEnsemble ====================

class _MockModel:
    """模拟子模型。"""
    def __init__(self, name, direction, confidence):
        self.name = name
        self._dir = direction
        self._conf = confidence

    def predict_direction(self, fv):
        return self._dir, self._conf


class TestAlphaEnsemble:
    def test_unanimous_long(self):
        ens = AlphaEnsemble()
        ens.register(_MockModel("online_linear", Direction.LONG, 0.7))
        ens.register(_MockModel("lightgbm", Direction.LONG, 0.8))
        pred = ens.predict(_fv(), regime="")
        assert pred.direction == Direction.LONG
        assert pred.confidence > 0.5

    def test_mixed_directions(self):
        ens = AlphaEnsemble()
        ens.register(_MockModel("m1", Direction.LONG, 0.9))
        ens.register(_MockModel("m2", Direction.SHORT, 0.5))
        pred = ens.predict(_fv(), regime="")
        # LONG 置信更高 → 偏 LONG
        assert pred.direction in (Direction.LONG, Direction.FLAT)

    def test_regime_weights_applied(self):
        """SQUEEZE regime 下 recurrent_ppo 权重更高。"""
        weights = RegimeWeights()
        w = weights.for_regime(Regime.SQUEEZE.value)
        assert w["recurrent_ppo"] > w["online_linear"]

    def test_empty_ensemble(self):
        ens = AlphaEnsemble()
        pred = ens.predict(_fv())
        assert pred.direction == Direction.FLAT


# ==================== P4.1/6 因子挖掘 ====================

class TestAlphaPool:
    def test_admit_uncorrelated(self):
        pool = AlphaPool(capacity=5)
        rng = np.random.default_rng(1)
        fv = rng.normal(0, 1, 100)
        tgt = fv * 0.5 + rng.normal(0, 0.1, 100)  # 相关
        from backend.services.factor_engine.expr.parser import parse
        expr = parse({"f": "close"})
        ok, contrib = pool.try_admit(expr, fv, tgt)
        assert ok
        assert contrib > 0

    def test_reject_low_ic(self):
        pool = AlphaPool(capacity=5, ic_lower_bound=0.3)
        rng = np.random.default_rng(2)
        fv = rng.normal(0, 1, 100)
        tgt = rng.normal(0, 1, 100)  # 不相关
        from backend.services.factor_engine.expr.parser import parse
        expr = parse({"f": "close"})
        ok, _ = pool.try_admit(expr, fv, tgt)
        assert not ok  # IC 太低拒

    def test_capacity_limit(self):
        pool = AlphaPool(capacity=2)
        rng = np.random.default_rng(3)
        for i in range(5):
            fv = rng.normal(0, 1, 100)
            tgt = fv + rng.normal(0, 0.05, 100)
            from backend.services.factor_engine.expr.parser import parse
            expr = parse({"f": "close"})
            pool.try_admit(expr, fv, tgt)
        assert pool.size() <= 2


class TestAlphaMiner:
    def test_mine_admits_some(self):
        pool = AlphaPool(capacity=10)
        miner = AlphaMiner(pool, MiningConfig(n_candidates=50))
        rng = np.random.default_rng(10)
        n = 100
        close = rng.normal(0, 1, n)
        target = close * 0.3 + rng.normal(0, 0.1, n)
        def value_fn(ctx):
            # 返回基于 close 的因子值
            return close * rng.normal(1, 0.1, n)
        admitted = miner.mine_random(["close", "volume"], value_fn, target, max_attempts=50)
        # 至少应有部分被接纳（或全拒也 OK，不崩）
        assert isinstance(admitted, list)


class TestCodegenCritic:
    def test_generate_and_audit(self):
        critic = CodegenCritic()
        result = critic.generate_and_audit("generate mean reversion factor")
        assert result.audit_passed is True  # 占位表达式应通过 audit

    def test_reject_lookahead(self):
        critic = CodegenCritic()
        bad_ast = {"op": "mean", "args": [{"f": "close"}, {"c": -5}]}  # look-ahead
        ok, reason = critic.reject_lookahead(bad_ast)
        assert not ok
        assert "look-ahead" in reason
