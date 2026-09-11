# -*- coding: utf-8 -*-
"""影子因子（role=paper / PAPER）实盘排除策略（2026-09-03 审查修正 A）。

背景：held-out 判决没过的 A/B 级因子以 role=paper 进 active、权重封顶 0.5，但
active 集合实盘与模拟共用 → 判决失败的因子以半权重参与实盘决策；且短线优先消费
的 V3 路径从未封顶。本用例锁定：
1. 统一入口 apply_paper_policy：live 归零、paper 封顶、开关关闭回退旧行为；
2. 中线因子路由：live 不让影子因子投票也不计入 min_active，paper 保持原口径；
3. 短线 pipeline 权重：market_data.trading_mode=live 时影子因子权重为 0。
"""
import numpy as np
import pytest

from backend.services.factor_engine import paper_factor_policy as pol


@pytest.fixture(autouse=True)
def _fixed_paper_ids(monkeypatch):
    """固定影子因子集合，不碰 DB/目录文件。"""
    monkeypatch.setattr(pol, "paper_factor_ids", lambda refresh=False: {"shadow_a", "evo_s5m_x"})
    monkeypatch.setattr(pol, "paper_weight_cap", lambda: 0.5)
    pol._last_log["ts"] = 0.0
    yield


class TestApplyPaperPolicy:
    def test_live_excludes_shadow(self, monkeypatch):
        monkeypatch.setattr(pol, "live_exclude_enabled", lambda: True)
        w = {"shadow_a": 1.0, "evo_s5m_x": 0.9, "real_f": 1.0}
        out = pol.apply_paper_policy(w, "live", where="t")
        assert out["shadow_a"] == 0.0 and out["evo_s5m_x"] == 0.0
        assert out["real_f"] == 1.0, "非影子因子不受影响"

    def test_paper_caps_not_excludes(self, monkeypatch):
        monkeypatch.setattr(pol, "live_exclude_enabled", lambda: True)
        w = {"shadow_a": 1.0, "evo_s5m_x": 0.3, "real_f": 1.0}
        out = pol.apply_paper_policy(w, "paper", where="t")
        assert out["shadow_a"] == 0.5, "paper 会话按 cap 封顶"
        assert out["evo_s5m_x"] == 0.3, "低于 cap 的权重原样保留"
        assert out["real_f"] == 1.0

    def test_switch_off_restores_legacy(self, monkeypatch):
        monkeypatch.setattr(pol, "live_exclude_enabled", lambda: False)
        w = {"shadow_a": 1.0}
        out = pol.apply_paper_policy(w, "live", where="t")
        assert out["shadow_a"] == 0.5, "开关关闭 → 实盘也回到 08-22 的半权重行为"

    def test_mode_none_treated_as_non_live(self, monkeypatch):
        monkeypatch.setattr(pol, "live_exclude_enabled", lambda: True)
        assert pol.paper_factor_excluded(None) is False
        assert pol.paper_factor_excluded("LIVE") is True, "大小写不敏感"

    def test_effective_weight_helper(self, monkeypatch):
        monkeypatch.setattr(pol, "live_exclude_enabled", lambda: True)
        assert pol.effective_paper_weight(0.8, "live") == 0.0
        assert pol.effective_paper_weight(0.8, "paper") == 0.5
        assert pol.effective_paper_weight(None, "paper") == 0.5
        assert pol.effective_paper_weight(0.0, "paper") == 0.0, "显式 0 不能被 or 陷阱还原"


class TestMidlongRoute:
    """路由层：影子因子 live 不投票、不计入 min_active。"""

    def _rows(self):
        return [
            {"factor_id": "shadow_a", "extra": {"role": "paper"},
             "scores": {"ic_mean": 0.08, "expected_sign": 1}, "runtime_weight": 0.5},
            {"factor_id": "shadow_b", "extra": {"role": "paper"},
             "scores": {"ic_mean": 0.07, "expected_sign": 1}, "runtime_weight": 0.5},
            {"factor_id": "real_f", "extra": {},
             "scores": {"ic_mean": 0.06, "expected_sign": 1}, "runtime_weight": 1.0},
        ]

    @pytest.fixture
    def route(self, monkeypatch):
        from backend.services.factor_engine import midlong_factor_route as mr
        from backend.services.factor_engine import midlong_active_factor_set as mas
        monkeypatch.setattr(mas.midlong_active_factor_set, "get_active_factors", lambda: self._rows())
        # 每个因子给一段"最新值明显偏高"的历史 → z>0 → 投看多票
        monkeypatch.setattr(mr, "_factor_history", lambda rec, sym: np.array([0.0] * 30 + [3.0]))
        monkeypatch.setattr(mr, "_dynamic_sl_tp", lambda sym: (0.05, 0.10, "static"))
        # 路由的 _cfg 读的是 settings 模块属性（不是环境变量）
        from backend.config import settings as _s
        monkeypatch.setattr(_s, "FACTOR_ROUTE_MIN_ACTIVE_FACTORS", 2, raising=False)
        monkeypatch.setattr(_s, "FACTOR_ROUTE_IC_ABS_MIN", 0.02, raising=False)
        monkeypatch.setattr(pol, "live_exclude_enabled", lambda: True)
        return mr

    def _ms(self):
        return {"BTC": {"current_price": 100.0, "data_reliable": True}}

    def test_paper_mode_all_three_vote(self, route):
        dec = route.factor_route_decide("BTC", market_summary=self._ms(), trading_mode="paper")
        assert "paper_excluded" not in dec
        assert len(dec["votes"]) == 3, dec

    def test_live_mode_drops_shadows_and_fails_min_active(self, route):
        dec = route.factor_route_decide("BTC", market_summary=self._ms(), trading_mode="live")
        assert dec["paper_excluded"] == 2
        assert dec["action"] == "hold"
        assert dec["reason"].startswith("insufficient_active(1<2)"), (
            "实盘只剩 1 个真因子，达不到 min_active → 不开仓（fail-closed）")

    def test_live_mode_with_enough_real_factors_ignores_shadows(self, route, monkeypatch):
        rows = self._rows() + [{"factor_id": "real_g", "extra": {},
                                "scores": {"ic_mean": 0.05, "expected_sign": 1},
                                "runtime_weight": 1.0}]
        from backend.services.factor_engine import midlong_active_factor_set as mas
        monkeypatch.setattr(mas.midlong_active_factor_set, "get_active_factors", lambda: rows)
        dec = route.factor_route_decide("BTC", market_summary=self._ms(), trading_mode="live")
        assert dec["paper_excluded"] == 2
        assert set(dec["votes"].keys()) == {"real_f", "real_g"}, dec["votes"]

    def test_switch_off_live_behaves_like_paper(self, route, monkeypatch):
        monkeypatch.setattr(pol, "live_exclude_enabled", lambda: False)
        dec = route.factor_route_decide("BTC", market_summary=self._ms(), trading_mode="live")
        assert "paper_excluded" not in dec and len(dec["votes"]) == 3


class TestPipelineWeights:
    """短线 pipeline：market_data.trading_mode 决定影子因子权重。"""

    def _fv(self):
        class _FV:
            has_data = True
            is_directional = True
            value = 0.3
            normalized = 0.3
        return {"shadow_a": _FV(), "real_f": _FV()}

    @pytest.fixture
    def pipe(self, monkeypatch):
        from backend.services.factor_engine.factor_evaluation_pipeline import factor_pipeline as fp
        # 隔离 regime 权重 / 学习权重 / IC 运行时权重，只看影子因子策略这一层
        monkeypatch.setattr(fp, "_weighting", None, raising=False)
        monkeypatch.setattr(fp, "_learned", None, raising=False)
        monkeypatch.setattr(fp, "_resolve_learned", lambda: None, raising=False)
        monkeypatch.setattr(pol, "live_exclude_enabled", lambda: True)
        return fp

    def test_live_zeroes_shadow_weight(self, pipe):
        w = pipe._compute_weights(self._fv(), {"symbol": "BTC", "trading_mode": "live"})
        assert w["shadow_a"] == 0.0, w
        assert w["real_f"] > 0

    def test_paper_keeps_capped_shadow_weight(self, pipe):
        w = pipe._compute_weights(self._fv(), {"symbol": "BTC", "trading_mode": "paper"})
        assert w["shadow_a"] > 0, "paper 会话影子因子仍参与（封顶后归一化）"
        assert w["shadow_a"] < w["real_f"], "封顶 0.5 后应低于真因子"
