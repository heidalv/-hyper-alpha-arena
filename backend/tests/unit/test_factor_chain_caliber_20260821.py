"""因子链口径刀回归（D6/D10/D11/D12，设计《因子挖掘算法升级…_20260820》§7 第一刀）。

- D10 DSR 三处口径对齐（默认 4 / fail-closed）；held-out 判决异常不再放行
- D6  发现预筛：真实 open、无 roll 环绕、前瞻与闸门同表、不再自宣 IC 门槛
- D11 registry 不再 A/B 直通 active：held-out 终审 + 去同质 + 活跃上限
- D12 种子/MCTS 算子池剔除被禁的 rank/cs_rank/scale
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


# ── D10 ─────────────────────────────────────────────────
class TestD10DsrAlignment:
    def test_three_symbols_fail_closed_with_default_4(self, monkeypatch):
        import backend.config.settings as _s
        import backend.services.factor_engine.factor_backtest_scorer as m
        monkeypatch.setattr(_s, "FACTOR_SCORER_DSR_MIN_SYMBOLS", 4, raising=False)
        ok, pbo = m.FactorBacktestScorer._dsr_pbo_gate([0.1, 0.2, 0.3], 900, 40)
        assert ok is False and pbo is None

    def test_docstring_and_fallback_say_four(self):
        import inspect
        import backend.services.factor_engine.factor_backtest_scorer as m
        doc = inspect.getsource(m.FactorBacktestScorer._dsr_pbo_gate)
        assert "默认 4" in doc
        src = inspect.getsource(m)
        assert '"FACTOR_SCORER_DSR_MIN_SYMBOLS", 4' in src

    def test_settings_comment_fail_closed(self):
        p = os.path.join(os.path.dirname(__file__), "..", "..", "config", "settings.py")
        src = open(p, encoding="utf-8").read()
        assert "fail-open 跳过并告警" not in src

    def test_heldout_exception_fail_closed(self):
        src = open(os.path.join(
            os.path.dirname(__file__), "..", "..",
            "services", "factor_engine", "factor_backtest_scorer.py",
        ), encoding="utf-8").read()
        assert "判决异常，放行训练段结果" not in src  # 旧日志文案必须消失
        assert "fail-closed 不放行" in src
        # 异常与拒绝同样留在 candidate 复验
        assert '_heldout_rec.get("verdict") in ("reject", "error")' in src


# ── D6 ─────────────────────────────────────────────────
class TestD6DiscoveryPrescreen:
    def test_source_no_roll_no_udp(self):
        import inspect
        from backend.services import factor_discovery as fd
        src = inspect.getsource(fd.FactorDiscoveryEngine._validate_factor)
        # 代码级模式（注释里说明历史问题不算残留）
        assert '"open": np.roll' not in src
        assert "np.roll(closes, -1)" not in src
        assert "UnifiedDataPool().get_kline_series" not in src
        assert "_period_fwd_bars" in src

    def test_pass_no_longer_requires_ic_threshold(self, monkeypatch):
        from backend.services import factor_discovery as fd
        eng = fd.FactorDiscoveryEngine.get_instance()

        rows = []
        base = 100.0
        rng = np.random.default_rng(7)
        for i in range(300):
            base *= 1 + 0.002 * rng.standard_normal()
            rows.append({"open": base, "high": base * 1.01, "low": base * 0.99,
                         "close": base, "volume": 10.0})

        import backend.services.factor_engine.factor_backtest_scorer as fbs
        monkeypatch.setattr(fbs.factor_backtest_scorer, "_load_klines",
                            lambda *a, **k: list(rows))
        # IC 全为 0（旧实现必拒）——新预筛只看可求值性
        monkeypatch.setattr(eng, "_calc_ic", lambda a, b: 0.0, raising=False)
        monkeypatch.setattr(eng, "_calc_rankic", lambda a, b: 0.0, raising=False)
        out = eng._validate_factor(
            {}, {"formula": "delta(close, 5) / (delay(close, 5) + 1e-9)"},
            ["BTC", "ETH"], interval="4h",
        )
        assert out["passed"] is True
        assert out["ic"] == 0

    def test_open_is_real_not_rolled(self, monkeypatch):
        from backend.services import factor_discovery as fd
        eng = fd.FactorDiscoveryEngine.get_instance()
        # close 恒定、open 递增：若 open=roll(close,1) 则因子恒 0 → 样本丢失
        rows = [{"open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
                 "close": 100.0, "volume": 5.0} for i in range(300)]
        import backend.services.factor_engine.factor_backtest_scorer as fbs
        monkeypatch.setattr(fbs.factor_backtest_scorer, "_load_klines",
                            lambda *a, **k: list(rows))
        out = eng._validate_factor(
            {}, {"formula": "delta(open, 1)"}, ["BTC", "ETH"], interval="4h",
        )
        # 可求值即通过（公式合法、样本够）
        assert out["passed"] is True


# ── D12 ─────────────────────────────────────────────────
class TestD12BannedOps:
    def test_mcts_pools_exclude_banned(self):
        from backend.services.evolution import mcts_miner as mm
        for banned in ("rank", "cs_rank", "scale"):
            assert banned not in mm._UNARY_OPS
            assert banned not in mm._BINARY_OPS
            assert banned not in mm._TERNARY_OPS

    def test_seed_no_rank_op(self):
        import inspect
        from backend.services.evolution import factor_evolution_loop as fel
        src = inspect.getsource(fel._mine_candidates)
        assert '"op": "rank"' not in src


# ── D11 ─────────────────────────────────────────────────
class TestD11RegistryGates:
    def test_series_corr_identical_and_independent(self):
        from backend.services.factor_engine.midlong_registry_factors import _series_corr
        a = np.sin(np.linspace(0, 20, 200))
        assert abs(_series_corr(a, a) - 1.0) < 1e-9
        b = np.roll(a, 50)
        c = _series_corr(a, b)
        assert c is None or abs(c) < 0.99  # 不完全同质

    def test_heldout_verdict_insufficient_fail_closed(self):
        from backend.services.factor_engine.midlong_registry_factors import (
            _registry_heldout_verdict,
        )
        ok, note = _registry_heldout_verdict({"vals": [1.0] * 10, "closes": [1.0] * 10}, "4h")
        assert ok is False

    def _patch_store(self, monkeypatch, cands, actives):
        import backend.services.factor_engine.custom_factor_store as cfs
        monkeypatch.setattr(cfs.custom_factor_store, "list_candidates", lambda **k: cands)
        monkeypatch.setattr(cfs.custom_factor_store, "list_active", lambda **k: actives, raising=False)

        calls = []
        monkeypatch.setattr(
            cfs.custom_factor_store, "update_scores",
            lambda fid, **k: calls.append((fid, k)), raising=False,
        )
        return calls

    def test_scan_ab_grade_blocked_by_heldout(self, monkeypatch):
        import backend.services.factor_engine.midlong_registry_factors as mrf
        cand = {"factor_id": "ai_reg1", "extra": {"kind": "registry", "timeframe": "4h"}}
        calls = self._patch_store(monkeypatch, [cand], [])
        monkeypatch.setattr(
            mrf, "_score_one_registry_factor",
            lambda fid, rid, tf: {
                "factor_id": fid, "timeframe": tf, "grade": "A", "admitted": True,
                "ic_mean": 0.06, "icir": 0.6, "reason": "", "_probe": None,
            },
        )
        monkeypatch.setattr(mrf, "_registry_heldout_verdict", lambda p, tf: (False, "探针样本不足"))
        out = mrf.scan_registry_midlong()
        assert out["promoted"] == 0
        fid, kw = calls[0]
        assert kw["status"] == "candidate"  # 终审未过留候选，不直通 active

    def test_scan_redundant_with_promoted_blocked(self, monkeypatch):
        import backend.services.factor_engine.midlong_registry_factors as mrf
        vals = np.sin(np.linspace(0, 40, 400))
        cands = [
            {"factor_id": "ai_reg1", "extra": {"kind": "registry", "timeframe": "4h"}},
            {"factor_id": "ai_reg2", "extra": {"kind": "registry", "timeframe": "4h"}},
        ]
        calls = self._patch_store(monkeypatch, cands, [])
        monkeypatch.setattr(mrf, "_registry_heldout_verdict", lambda p, tf: (True, "Sharpe=1.0"))
        monkeypatch.setattr(
            mrf, "_score_one_registry_factor",
            lambda fid, rid, tf: {
                "factor_id": fid, "timeframe": tf, "grade": "A", "admitted": True,
                "ic_mean": 0.06, "icir": 0.6, "reason": "",
                "_probe": {"vals": vals.copy(), "closes": np.linspace(1, 2, 400), "sym": "BTC"},
            },
        )
        out = mrf.scan_registry_midlong()
        assert out["promoted"] == 1  # 第二个与第一个 corr≈1 → 拒
        statuses = [kw["status"] for _, kw in calls]
        assert statuses == ["active", "candidate"]

    def test_scan_active_cap_blocks(self, monkeypatch):
        import backend.services.factor_engine.midlong_registry_factors as mrf
        cand = {"factor_id": "ai_reg1", "extra": {"kind": "registry", "timeframe": "4h"}}
        import backend.config.settings as _s
        monkeypatch.setattr(_s, "MIDLONG_ACTIVE_FACTOR_MAX", 1, raising=False)
        actives = [{"factor_id": f"a{i}", "extra": {"horizon": "midlong"}} for i in range(3)]
        calls = self._patch_store(monkeypatch, [cand], actives)
        monkeypatch.setattr(
            mrf, "_score_one_registry_factor",
            lambda fid, rid, tf: {
                "factor_id": fid, "timeframe": tf, "grade": "A", "admitted": True,
                "ic_mean": 0.06, "icir": 0.6, "reason": "", "_probe": None,
            },
        )
        monkeypatch.setattr(mrf, "_registry_heldout_verdict", lambda p, tf: (True, "Sharpe=1.0"))
        out = mrf.scan_registry_midlong()
        assert out["promoted"] == 0
        assert calls[0][1]["status"] == "candidate"
