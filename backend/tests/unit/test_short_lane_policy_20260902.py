# -*- coding: utf-8 -*-
"""做空车道闸门（2026-09-02 P2.4）。

依据：triple-barrier 口径 5.5 万条已结算信号 —— 做空 32880 条胜率 30.3%、
净 -34.14bp（总贡献 -112 万 bp）；叠加 pwin>=0.55 仍为 -14.15bp。同期做多
22507 条净 +12.82bp、pwin>=0.55 时 +28.73bp。

本用例锁定四条契约：
1. 默认关闭（不依赖 .env 是否写了开关）；
2. 缺证据（pwin 缺失/非法）时不放行 —— 负期望结论是在有 pwin 的样本上得出的；
3. 闸门位于 log_signal 之后 —— 做空样本必须继续落库供学习，否则等于把做空从
   闭环里删掉，永远无法判断何时可以放开；
4. 只拦开仓，不影响退出。
"""
import re
from pathlib import Path

import pytest

from backend.services.scalp.short_lane_policy import (
    short_lane_enabled,
    short_min_pwin,
    short_min_score,
    short_open_allowed,
)

_LOOP = (Path(__file__).resolve().parents[3] / "backend" / "services"
         / "full_auto" / "loops" / "scalp_loop.py")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """与生产 .env 解耦：验证的是机制默认值，不是当前配置。"""
    for k in ("SCALP_SHORT_ENABLED", "SCALP_SHORT_MIN_PWIN",
              "SCALP_SHORT_MIN_SCORE"):
        monkeypatch.delenv(k, raising=False)


class TestDefaultClosed:
    def test_lane_disabled_by_default(self):
        assert short_lane_enabled() is False

    def test_min_pwin_defaults_to_all_closed(self):
        assert short_min_pwin() >= 999.0, "默认须等价全关"

    def test_no_score_gate_by_default(self):
        """factor_score 与净收益反向，不该拿它当做空准入条件。"""
        assert short_min_score() == 0.0

    def test_short_rejected_regardless_of_quality(self):
        for pw in (None, 0.3, 0.55, 0.7, 0.99):
            ok, why = short_open_allowed(pwin=pw, factor_score=80)
            assert ok is False, f"默认配置下 pwin={pw} 也不得放行"
            assert why == "short_lane_off"


class TestExperimentSlit:
    """可配的实验缝隙：开关+门槛都显式给出时才放行。"""

    def test_requires_both_switch_and_threshold(self, monkeypatch):
        monkeypatch.setenv("SCALP_SHORT_ENABLED", "true")
        ok, why = short_open_allowed(pwin=0.9)
        assert ok is False, "只开总开关、未设 pwin 门槛时仍应关闭"
        assert why == "short_pwin_gate_closed"

    def test_allows_above_threshold(self, monkeypatch):
        monkeypatch.setenv("SCALP_SHORT_ENABLED", "true")
        monkeypatch.setenv("SCALP_SHORT_MIN_PWIN", "0.60")
        ok, why = short_open_allowed(pwin=0.62)
        assert ok is True
        assert "short_allowed" in why

    def test_blocks_below_threshold(self, monkeypatch):
        monkeypatch.setenv("SCALP_SHORT_ENABLED", "true")
        monkeypatch.setenv("SCALP_SHORT_MIN_PWIN", "0.60")
        ok, why = short_open_allowed(pwin=0.55)
        assert ok is False
        assert "below_min" in why

    def test_boundary_is_inclusive(self, monkeypatch):
        monkeypatch.setenv("SCALP_SHORT_ENABLED", "true")
        monkeypatch.setenv("SCALP_SHORT_MIN_PWIN", "0.60")
        assert short_open_allowed(pwin=0.60)[0] is True

    def test_optional_score_gate(self, monkeypatch):
        monkeypatch.setenv("SCALP_SHORT_ENABLED", "true")
        monkeypatch.setenv("SCALP_SHORT_MIN_PWIN", "0.60")
        monkeypatch.setenv("SCALP_SHORT_MIN_SCORE", "50")
        assert short_open_allowed(pwin=0.7, factor_score=45)[0] is False
        assert short_open_allowed(pwin=0.7, factor_score=55)[0] is True


class TestMissingEvidence:
    """无 pwin 证据时不得放行 —— 负期望结论建立在有 pwin 的样本上。"""

    def test_missing_pwin_blocked(self, monkeypatch):
        monkeypatch.setenv("SCALP_SHORT_ENABLED", "true")
        monkeypatch.setenv("SCALP_SHORT_MIN_PWIN", "0.60")
        ok, why = short_open_allowed(pwin=None)
        assert ok is False and why == "short_pwin_missing"

    def test_invalid_pwin_blocked(self, monkeypatch):
        monkeypatch.setenv("SCALP_SHORT_ENABLED", "true")
        monkeypatch.setenv("SCALP_SHORT_MIN_PWIN", "0.60")
        for bad in ("abc", [], {}):
            ok, why = short_open_allowed(pwin=bad)
            assert ok is False, f"非法 pwin {bad!r} 不得放行"

    def test_malformed_threshold_falls_back_to_closed(self, monkeypatch):
        monkeypatch.setenv("SCALP_SHORT_ENABLED", "true")
        monkeypatch.setenv("SCALP_SHORT_MIN_PWIN", "not-a-number")
        assert short_min_pwin() >= 999.0
        assert short_open_allowed(pwin=0.9)[0] is False


class TestWiring:
    """接线契约：拦截点必须在信号落库之后、下单之前。"""

    @staticmethod
    def _src() -> str:
        return _LOOP.read_text(encoding="utf-8")

    def test_gate_present_in_loop(self):
        assert "short_open_allowed" in self._src()

    def test_gate_after_log_signal(self):
        """核心契约：做空样本必须先落库，闸断只影响下单。

        若拦在 log_signal 之前，做空样本会从 scalp_signal_log 消失，
        triple-barrier 标签无从积累 —— 那就永远无法判断做空何时可以放开。
        """
        src = self._src()
        i_log = src.find("tp_pct=float(_sig.tp_pct or 0) or None")
        i_gate = src.find("short_open_allowed")
        assert i_log > 0 and i_gate > 0
        assert i_log < i_gate, "做空闸门必须在 log_signal 之后"

    def test_gate_before_order_routing(self):
        src = self._src()
        i_gate = src.find("short_open_allowed")
        i_live = src.find('if (_trade_mode or "").lower() == "live":')
        assert i_gate > 0 and i_live > 0
        assert i_gate < i_live, "做空闸门必须在下单分支之前"

    def test_gate_is_fail_closed(self):
        """闸门异常时不得放行做空。"""
        src = self._src()
        seg = src[src.find("short_open_allowed"):]
        seg = seg[:seg.find('if (_trade_mode or "").lower() == "live":')]
        assert "_sh_ok, _sh_why = False" in seg, (
            "except 分支须置 _sh_ok=False（fail-closed）"
        )

    def test_gate_only_targets_short_direction(self):
        src = self._src()
        i_gate = src.find("short_open_allowed")
        head = src[max(0, i_gate - 900):i_gate]
        assert 'lower() == "short"' in head, "闸门须仅对 short 方向生效"

    def test_gate_does_not_touch_exit_paths(self):
        """退出相关模块不应引用开仓闸门。"""
        for name in ("position_exit_orchestrator.py", "paper_trading_engine.py"):
            p = (Path(__file__).resolve().parents[3] / "backend" / "services" / name)
            if p.exists():
                assert "short_open_allowed" not in p.read_text(encoding="utf-8"), (
                    f"{name} 不应引用开仓闸门 —— 平仓必须畅通"
                )

    def test_block_reason_is_counted(self):
        """闸断须计入阻塞统计，面板上要能区分"被闸断"与"没信号"。"""
        src = self._src()
        seg = src[src.find("short_open_allowed"):]
        assert re.search(r'_bump_block\("short_lane_disabled"\)', seg)


class TestStatsHelper:
    def test_stats_uses_tb_label(self):
        """放开决策必须用与执行一致的 TB 标签，不能用旧的固定 horizon 标签。"""
        src = (Path(__file__).resolve().parents[3] / "backend" / "services"
               / "scalp" / "short_lane_policy.py").read_text(encoding="utf-8")
        assert "tb_settled" in src and "tb_net_ret" in src
        assert "tb_win" in src

    def test_stats_returns_shape_on_error(self, monkeypatch):
        import backend.services.scalp.short_lane_policy as mod
        monkeypatch.setattr(
            "backend.database.connection.SessionLocal",
            lambda: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        out = mod.short_lane_stats(days=7)
        assert out["days"] == 7
        assert out["ok"] is False
