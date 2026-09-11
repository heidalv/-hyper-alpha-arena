# -*- coding: utf-8 -*-
"""[F56] 车道注册表单测：登记/切换/晋升判定/路由契约。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import lane_registry as reg  # noqa: E402


class TestPromotionCriteria:
    """晋升判定是纯函数，先单测它（不依赖 DB）。"""

    def _edge(self, **kw):
        base = {"net_bp": 1.5, "n": 300, "max_dd_pct": 0.8, "fill_rate_ratio": 0.5,
                "source": "f59_replay",
                "folds": [{"net_bp": 1.2, "t": 3.1, "n": 80},
                          {"net_bp": 0.9, "t": 2.4, "n": 75},
                          {"net_bp": 2.1, "t": 4.0, "n": 70},
                          {"net_bp": 1.4, "t": 2.8, "n": 75}]}
        base.update(kw)
        return base

    def test_all_pass(self):
        p = reg.evaluate_promotion(self._edge())
        assert p["ready"] is True
        assert p["failed"] == []
        assert p["progress_pct"] == 100.0

    def test_unverified_source_fails_closed(self):
        """没有可信来源的 edge 即使数字漂亮也不得晋级。

        真实事故：一条人工写入的假 edge（n=250、四折 t=3.0、回撤 0.7%）留在
        生产 registry 里，让一条从未跑过的车道显示「晋级就绪」。
        """
        p = reg.evaluate_promotion(self._edge(source=None))
        assert p["ready"] is False
        assert "edge_verified" in p["failed"]

    def test_unknown_source_fails_closed(self):
        p = reg.evaluate_promotion(self._edge(source="manual_seed"))
        assert p["ready"] is False
        assert "edge_verified" in p["failed"]

    def test_paper_shadow_source_allowed(self):
        assert reg.evaluate_promotion(self._edge(source="paper_shadow"))["ready"] is True

    def test_live_source_allowed(self):
        assert reg.evaluate_promotion(self._edge(source="live"))["ready"] is True

    def test_hedged_backtest_source_allowed(self):
        """L2 对冲 carry 的回测来源（F70）也属可信来源。"""
        assert reg.evaluate_promotion(self._edge(source="hedged_backtest"))["ready"] is True

    def test_source_whitelist_is_explicit(self):
        assert set(reg.EDGE_SOURCES) == {"f59_replay", "paper_shadow", "live",
                                         "hedged_backtest"}

    def test_too_few_trades(self):
        p = reg.evaluate_promotion(self._edge(n=50))
        assert "min_trades" in p["failed"]

    def test_fold_t_below_threshold(self):
        p = reg.evaluate_promotion(self._edge(folds=[
            {"net_bp": 1.0, "t": 3.0, "n": 60}, {"net_bp": 1.0, "t": 1.2, "n": 60},
            {"net_bp": 1.0, "t": 2.5, "n": 60}, {"net_bp": 1.0, "t": 2.2, "n": 60}]))
        assert "fold_t" in p["failed"]

    def test_negative_fold_fails(self):
        p = reg.evaluate_promotion(self._edge(folds=[
            {"net_bp": 1.0, "t": 3.0, "n": 60}, {"net_bp": -0.4, "t": -1.0, "n": 60},
            {"net_bp": 1.0, "t": 2.5, "n": 60}, {"net_bp": 1.0, "t": 2.2, "n": 60}]))
        assert "folds_positive" in p["failed"]

    def test_drawdown_and_fill_rate(self):
        p = reg.evaluate_promotion(self._edge(max_dd_pct=2.5, fill_rate_ratio=0.1))
        assert "max_drawdown_pct" in p["failed"]
        assert "fill_rate_ratio" in p["failed"]

    def test_negative_net_fails(self):
        p = reg.evaluate_promotion(self._edge(net_bp=-0.5))
        assert "net_positive" in p["failed"]

    def test_missing_fields_fail_closed(self):
        p = reg.evaluate_promotion({})
        assert p["ready"] is False
        assert len(p["failed"]) >= 5

    def test_none_edge_fail_closed(self):
        assert reg.evaluate_promotion(None)["ready"] is False


class TestDefaultsContract:
    def test_default_lanes_cover_design(self):
        ids = {x["lane_id"] for x in reg.DEFAULT_LANES}
        assert ids == {"mm_asterdex", "carry_basis", "xvenue_spread",
                       "liq_reversal", "scalp_directional"}

    def test_scalp_directional_is_disabled_zero_budget(self):
        lane = next(x for x in reg.DEFAULT_LANES if x["lane_id"] == "scalp_directional")
        assert lane["mode"] == "disabled"
        assert lane["risk"]["budget_pct"] == 0.0

    def test_budget_sums_to_95(self):
        total = sum(x["risk"].get("budget_pct", 0.0) for x in reg.DEFAULT_LANES)
        assert total == 95.0   # 留 5% 现金缓冲（设计文档 §1.5）

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValueError):
            reg.register_lane("x", mode="bogus")


class TestRouterContract:
    def test_routes_present(self):
        from backend.api.lane_routes import router

        paths = {getattr(r, "path", "") for r in router.routes}
        for p in ("/api/trading/lanes", "/api/trading/lanes/{lane_id}",
                  "/api/trading/lanes/{lane_id}/mode",
                  "/api/trading/lanes/{lane_id}/status",
                  "/api/trading/lanes/{lane_id}/edge",
                  "/api/trading/lanes/{lane_id}/promotion"):
            assert p in paths, p
