"""P2 + 因子链第二刀回归（M12/M13/S12 + item12/item13，2026-08-21）。

- M12 test_swing_deprecated 重写为现码契约（本身在集成目录跑，此处只测头注释）
- M13 mid_view/mid_timing 族 DEPRECATED 标记（只标记不删）
- S12 山寨过滤净期望门槛：expectancy>0 或 PF≥1，净亏币对被滤
- item12 进化 4h/1d 前瞻与闸门对齐（6/3）
- item13 晋升写 expected_sign；combo_weights 与路由同一套符号规则
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


def _src(*parts):
    return open(os.path.join(os.path.dirname(__file__), "..", "..", *parts), encoding="utf-8").read()


# ── M13：deprecated 标记 ────────────────────────────────
class TestM13DeprecationMarkers:
    def test_markers_present(self):
        types_src = _src("services", "mlto", "types.py")
        assert "M13 2026-08-21" in types_src and "DEPRECATED" in types_src
        ql_src = _src("services", "mlto", "quant_layer.py")
        assert "M13 2026-08-21" in ql_src
        dh_src = _src("services", "mlto", "decision_hub.py")
        assert "M13 2026-08-21" in dh_src

    def test_orchestrator_mid_view_not_deprecated(self):
        # Family B（orchestrator TimeframeView.mid_view）是活代码，不得被误标
        src = _src("services", "multi_timeframe_orchestrator.py")
        assert "decision.mid_view" in src
        assert "M13 2026-08-21" not in src

    def test_midlong_loop_header_updated(self):
        src = _src("services", "full_auto", "loops", "midlong_loop.py")
        assert "M12 2026-08-21" in src
        assert "本循环现仅处理 long" not in src  # 过时描述已删


# ── S12：净期望门槛 ─────────────────────────────────────
class _SeqQuery:
    def __init__(self, results):
        self._results = list(results)

    def filter(self, *a, **k):
        return self

    def scalar(self):
        return self._results.pop(0) if self._results else 0


class _SeqDB:
    def __init__(self, results):
        self._q = _SeqQuery(results)

    def query(self, *a, **k):
        return self._q

    def close(self):
        pass


class TestS12ExpectancyGate:
    def _allowed(self, monkeypatch, results, symbol="PEPE"):
        import backend.database.connection as conn
        import backend.services.scalp_factor_router as sfr
        monkeypatch.setattr(sfr, "_universe_cache", {})
        monkeypatch.setattr(sfr, "_SCALP_UNIVERSE_ONLY", True)
        monkeypatch.setenv("SCALP_ALTCOIN_MIN_SETTLED", "100")
        monkeypatch.setenv("SCALP_ALTCOIN_MIN_WINRATE", "0.42")
        monkeypatch.setattr(conn, "SessionLocal", lambda: _SeqDB(results))
        return sfr._symbol_universe_allowed(symbol)

    def test_negative_expectancy_blocked(self, monkeypatch):
        # n=200, wins=100(wr=0.5), avg net=-0.001, sum_win=0.2, sum_loss=0.4 (PF=0.5)
        ok, note = self._allowed(monkeypatch, [200, 100, -0.001, 0.2, 0.4])
        assert ok is False
        assert "neg_expectancy" in note

    def test_positive_expectancy_allowed(self, monkeypatch):
        ok, note = self._allowed(monkeypatch, [200, 100, 0.0012, 0.4, 0.3])
        assert ok is True
        assert "exp=" in note

    def test_pf_ok_but_exp_zero_blocked_when_wr_low(self, monkeypatch):
        # 胜率不达标先拦（净期望检查在胜率之后）
        ok, note = self._allowed(monkeypatch, [200, 60, 0.001, 0.4, 0.3])
        assert ok is False
        assert "wr_too_low" in note


# ── item12：进化前瞻对齐 ────────────────────────────────
class TestItem12FwdAligned:
    def test_midlong_fwd_matches_gate(self):
        from backend.services.evolution.factor_evolution_loop import _PERIOD_FWD_BARS
        from backend.config.settings import (
            FACTOR_SCORER_MIDLONG_FWD_4H, FACTOR_SCORER_MIDLONG_FWD_1D,
        )
        assert _PERIOD_FWD_BARS["4h"] == int(FACTOR_SCORER_MIDLONG_FWD_4H)
        assert _PERIOD_FWD_BARS["1d"] == int(FACTOR_SCORER_MIDLONG_FWD_1D)
        assert _PERIOD_FWD_BARS["8h"] == int(FACTOR_SCORER_MIDLONG_FWD_4H)

    def test_scalp_periods_untouched(self):
        from backend.services.evolution.factor_evolution_loop import _PERIOD_FWD_BARS
        assert _PERIOD_FWD_BARS["1h"] == 2
        assert _PERIOD_FWD_BARS["15m"] == 6


# ── item13：expected_sign 同号规则 ──────────────────────
class TestItem13ExpectedSign:
    def test_promotion_writes_expected_sign(self):
        scorer_src = _src("services", "factor_engine", "factor_backtest_scorer.py")
        assert '"expected_sign": 1 if float(result.ic_mean or 0) >= 0 else -1' in scorer_src
        reg_src = _src("services", "factor_engine", "midlong_registry_factors.py")
        assert '"expected_sign": 1 if float(r.get("ic_mean") or 0) >= 0 else -1' in reg_src
        rc_src = _src("services", "factor_engine", "midlong_active_factor_set.py")
        assert '"expected_sign": 1 if float(sr.ic_mean or 0) >= 0 else -1' in rc_src

    def test_combo_consistent_reverse_factor_gets_weight(self):
        from backend.services.factor_engine.combo_weights import resolve_combo_weights
        records = [
            {"factor_id": "rev", "scores": {"icir": -0.4, "expected_sign": -1}},
            {"factor_id": "fwd", "scores": {"icir": 0.4, "expected_sign": 1}},
        ]
        w = resolve_combo_weights(records, {})
        assert w["rev"] == pytest.approx(w["fwd"])  # 同幅度反向因子等权

    def test_combo_inconsistent_sign_zero_weight(self):
        from backend.services.factor_engine.combo_weights import resolve_combo_weights
        records = [
            {"factor_id": "bad", "scores": {"icir": -0.4, "expected_sign": 1}},
            {"factor_id": "good", "scores": {"icir": 0.4, "expected_sign": 1}},
        ]
        w = resolve_combo_weights(records, {})
        assert w["bad"] == pytest.approx(0.0)
        assert w["good"] == pytest.approx(1.0)

    def test_combo_legacy_without_sign_unchanged(self):
        from backend.services.factor_engine.combo_weights import resolve_combo_weights
        records = [{"factor_id": "old", "scores": {"icir": -0.5}}]
        w = resolve_combo_weights(records, {})
        # 旧记录（无 expected_sign）保持旧语义：负 icir → 0 → 均分兜底
        assert w["old"] == pytest.approx(1.0)

    def test_route_orient_reads_expected_sign(self, monkeypatch):
        import backend.config.settings as _s
        import backend.services.factor_engine.midlong_factor_route as mrf
        import backend.services.factor_engine.midlong_active_factor_set as mafs
        import numpy as np

        class _FakeSet:
            def get_active_factors(self):
                return [{"factor_id": "f1",
                         "scores": {"ic_mean": 0.05, "expected_sign": -1},
                         "runtime_weight": 1.0}]

        monkeypatch.setattr(mafs, "midlong_active_factor_set", _FakeSet())
        monkeypatch.setattr(_s, "FACTOR_ROUTE_MIN_ACTIVE_FACTORS", 1, raising=False)
        monkeypatch.setattr(_s, "FACTOR_ROUTE_ENTRY_THRESHOLD", 0.2, raising=False)
        # 因子值强正 z（递增序列尾部）→ 无 expected_sign 时 vote=+；
        # 锁定 -1 后应为 -
        monkeypatch.setattr(
            mrf, "_factor_history",
            lambda rec, sym: np.linspace(0.0, 3.0, 200),
        )
        monkeypatch.setattr(mrf, "_dynamic_sl_tp", lambda s: (0.05, 0.10, "t"))
        out = mrf.factor_route_decide("BTC", market_summary={"BTC": {"price": 100.0}})
        # 强正 z × orient(-1) → 负分 → sell（若 orient 仍按 ic>=0 则为 buy）
        assert out["action"] == "sell"
        assert out["votes"]["f1"]["vote"] < 0
