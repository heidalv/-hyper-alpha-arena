# -*- coding: utf-8 -*-
"""峰谷遥测（MFE/MAE）完整性（2026-09-02 P1.3）。

问题：`_sync_peak_state` 原先位于 `_run_v2_protection` 中 `max_hold_timeout` 与
`manager` 两处提前 return 的**后面**，任一命中就整 tick 跳过遥测更新。实测近 30 天
主账户短线 1713 笔：peak_pnl_pct 覆盖 73.4%，而 trough_pnl_pct 只有 10.1%；止损单
MAE 均值仅 -0.029%，而其止损距离为 1.11% —— 触损那一刻从未被记下。

遥测是纯观测量（min/max 幂等），阶段二用 MFE/MAE 校准 TP/SL 依赖它的完整性。
本用例锁定：同步必须在任何提前 return 之前、且极值语义单调。
"""
import re
from pathlib import Path

import pytest

_ENGINE = (Path(__file__).resolve().parents[3]
           / "backend" / "services" / "paper_trading_engine.py")


def _src() -> str:
    return _ENGINE.read_text(encoding="utf-8")


def _protection_body() -> str:
    """取 _run_v2_protection 定义起至下一个 def 的函数体。"""
    src = _src()
    m = re.search(
        r"def _run_v2_protection\(self.*?\n(.*?)\n    def ", src, re.S,
    )
    assert m, "未找到 _run_v2_protection 函数体（重构后请同步本用例）"
    return m.group(1)


class TestSyncOrdering:
    """性质一：遥测同步必须早于所有提前 return。"""

    def test_sync_present_in_protection(self):
        assert "_sync_peak_state" in _protection_body(), (
            "_run_v2_protection 内必须同步峰谷遥测"
        )

    def test_sync_before_max_hold_timeout(self):
        body = _protection_body()
        i_sync = body.find("_sync_peak_state")
        i_timeout = body.find("_enforce_max_hold_timeout")
        assert i_sync >= 0 and i_timeout >= 0
        assert i_sync < i_timeout, (
            "峰谷同步必须在 max_hold_timeout 之前 —— 超时 return 会吃掉当 tick 遥测"
        )

    def test_sync_before_manager_guard(self):
        body = _protection_body()
        i_sync = body.find("_sync_peak_state")
        i_mgr = body.find("if not manager")
        assert i_sync >= 0 and i_mgr >= 0
        assert i_sync < i_mgr, (
            "峰谷同步必须在 manager 未初始化的 return 之前"
        )

    def test_sync_is_failsafe(self):
        """遥测失败绝不能影响交易主流程。"""
        body = _protection_body()
        head = body[: body.find("_enforce_max_hold_timeout")]
        assert "try:" in head and "except" in head, (
            "遥测同步需包在 try/except 中，异常不得打断保护逻辑"
        )


class TestExtremumSemantics:
    """性质二：peak 只上推、trough 只下探，且对多空方向正确。"""

    @staticmethod
    def _pnl_pct(side, entry, mark):
        from backend.services.paper_trading_engine import PaperTradingEngine
        pos = type("P", (), {"entry_price": entry, "mark_price": mark, "side": side})()
        return PaperTradingEngine._position_pnl_pct(pos)

    def test_long_direction(self):
        assert self._pnl_pct("long", 100.0, 101.0) == pytest.approx(0.01)
        assert self._pnl_pct("long", 100.0, 99.0) == pytest.approx(-0.01)

    def test_short_direction(self):
        assert self._pnl_pct("short", 100.0, 99.0) == pytest.approx(0.01)
        assert self._pnl_pct("short", 100.0, 101.0) == pytest.approx(-0.01)

    def test_invalid_inputs_are_zero(self):
        assert self._pnl_pct("long", 0.0, 100.0) == 0.0
        assert self._pnl_pct("long", 100.0, 0.0) == 0.0

    def test_peak_uses_max_trough_uses_min(self):
        """源码层面确认极值方向没写反（写反会让 MFE/MAE 语义互换）。"""
        src = _src()
        m = re.search(r"def _sync_peak_state\(self.*?\n(.*?)\n    # ", src, re.S)
        assert m, "未找到 _sync_peak_state 函数体"
        body = m.group(1)
        peak_line = [l for l in body.splitlines() if "pos.peak_pnl_pct" in l and "=" in l]
        trough_asg = [l for l in body.splitlines() if "trough_pct = min(" in l]
        assert any("max(" in l for l in peak_line) or "max(" in body, "peak 必须取 max"
        assert trough_asg, "trough 必须取 min"


class TestCalibrationInputsAvailable:
    """性质三：阶段 2.2 需要的字段在模型上齐备。"""

    def test_model_has_mfe_mae_fields(self):
        from backend.database.models import PaperPosition
        cols = set(PaperPosition.__table__.columns.keys())
        for c in ("peak_pnl_pct", "trough_pnl_pct",
                  "peak_unrealized_pnl", "trough_unrealized_pnl"):
            assert c in cols, f"缺少遥测字段 {c}"

    def test_model_has_plan_tpsl_fields(self):
        """校准要对比"计划 TP/SL"与"实际 MFE"，两者都得能取到。"""
        from backend.database.models import PaperPosition
        cols = set(PaperPosition.__table__.columns.keys())
        for c in ("tp_price", "sl_price", "entry_price", "close_reason"):
            assert c in cols, f"缺少 TP/SL 校准所需字段 {c}"
