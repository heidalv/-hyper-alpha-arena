# -*- coding: utf-8 -*-
"""M0/M1/M2 修复回归测试（2026-08-22）。

覆盖：
- 校准 fail-closed（threshold=None → 999 关闸）
- 结构止损不封顶 1.8% 死区间（ATR 自适应生效）
- 期望值闸门替换伪 Sharpe
- tenant 自动填钩子对已声明列生效
- tp_sl_authority position 键
- 时区归一辅助函数
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeCalib:
    """构造一个临时 scalp_calibration.json 的上下文（写到工作区内，沙箱只读 TEMP）。"""
    def __init__(self, data):
        self.data = data
        _tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_calib_tmp")
        os.makedirs(_tmp_dir, exist_ok=True)
        self._path = os.path.join(_tmp_dir, "scalp_calibration.json")
        with open(self._path, "w", encoding="utf-8") as f:
            import json
            json.dump(data, f, ensure_ascii=False)

    def __enter__(self):
        import backend.services.scalp.scalp_score_calibration as m
        self._old = m._CALIB_FILE
        m._CALIB_FILE = self._path
        return m

    def __exit__(self, *a):
        import backend.services.scalp.scalp_score_calibration as m
        m._CALIB_FILE = self._old


def test_effective_threshold_blocks_when_no_profitable_bucket(monkeypatch):
    """[2026-08-23 改造更新] 校准 threshold=None（无盈利分桶）→ trend 回退静态门槛
    放行观察（新 TP/SL 参数需要新样本）；SCALP_CALIB_NOEDGE_BLOCK=1 回滚 999 全拦；
    ranging_mr 分类放行；有门槛 → 正常取 max。"""
    from backend.services.scalp import scalp_score_calibration as m
    monkeypatch.delenv("SCALP_CALIBRATED_THRESHOLD", raising=False)
    monkeypatch.delenv("SCALP_CALIB_NOEDGE_BLOCK", raising=False)
    with _FakeCalib({"enabled": True, "threshold": None, "high_score_ok": False}):
        assert m.effective_threshold(30) == 30                          # trend 回退静态门槛
        assert m.effective_threshold(30, kind="trend") == 30
        assert m.effective_threshold(30, kind="ranging_mr") == 30        # MR 分类放行 (PROFIT-2)
        monkeypatch.setenv("SCALP_CALIB_NOEDGE_BLOCK", "1")
        assert m.effective_threshold(30, kind="trend") == m.CALIBRATION_BLOCKED_THRESHOLD  # 回滚全拦
    with _FakeCalib({"enabled": True, "threshold": 62, "high_score_ok": True}):
        assert m.effective_threshold(30) == 62
        assert m.effective_threshold(70) == 70


def test_effective_threshold_static_override(monkeypatch):
    """管理员显式 SCALP_CALIBRATED_THRESHOLD 覆盖关闸。"""
    from backend.services.scalp import scalp_score_calibration as m
    monkeypatch.setenv("SCALP_CALIBRATED_THRESHOLD", "58")
    with _FakeCalib({"enabled": True, "threshold": None, "high_score_ok": False}):
        assert m.effective_threshold(30) == 58


def test_structure_stop_sl_adaptive_not_capped(monkeypatch):
    """[2026-08-23 改造A 更新] 高波动（atr 2.4%）时 SL 封顶 1.2%、TP=1.5×SL=1.5%。

    旧断言（M0-7：SL≈2.4% 不被压死）已随「TP/SL 对齐信号边际分布」改造作废：
    2.4% 止损对 30-45min 边际 ±0.3% 的信号是错配（SL 通道全历史 -197 最大出血）。
    """
    from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
    md = {
        "price": 100.0,
        "volatility_value": 0.024,   # 2.4% ATR
        "atr_pct": 0.024,
        "regime": {"name": "trending"},
        "klines": None,
    }
    sl_pct, tp_pct, sl_price, tp_price = structure_stop_calculator.compute_sl_tp(
        md, side="long", entry=100.0,
    )
    assert abs(sl_pct - 0.0115) < 1e-6, f"sl_pct={sl_pct} 应封顶 1.15%（对齐信号边际）"
    assert abs(tp_pct - 0.015) < 1e-6, f"tp_pct={tp_pct} 应为 1.5%（RR≈1.30 过 V5 闸）"


class _FakeMem:
    def __init__(self, total_trades, win_rate, avg_profit, avg_loss):
        self.total_trades = total_trades
        self.win_rate = win_rate
        self.avg_profit = avg_profit
        self.avg_loss = avg_loss


def test_memory_ev_gate_replaces_pseudo_sharpe():
    """期望值闸门：正期望放行、负期望拦截（与伪 Sharpe 无关）。"""
    from backend.services.memory_ev_gate import memory_ev_ok, memory_expected_value
    pos = _FakeMem(total_trades=30, win_rate=0.42, avg_profit=0.9, avg_loss=-0.5)
    assert memory_expected_value(pos) == pytest.approx(0.42 * 0.9 + 0.58 * (-0.5))
    assert memory_ev_ok(pos) is True   # EV=0.088 > 0
    neg = _FakeMem(total_trades=30, win_rate=0.42, avg_profit=0.3, avg_loss=-0.5)
    assert memory_ev_ok(neg) is False  # EV=-0.164 < 0
    small = _FakeMem(total_trades=5, win_rate=1.0, avg_profit=1.0, avg_loss=0)
    assert memory_ev_ok(small) is False  # 样本 <15 不放行


def test_tier_nature_authority_position_key():
    """position（long 层强共振输出）不再回退 scalp 参数。"""
    from backend.services.tp_sl_authority import NATURE_TP_SL, resolve_tp_sl_pct
    assert NATURE_TP_SL["position"] == NATURE_TP_SL["trend_follow"]
    # tier=None 仍回退 scalp（向后兼容）
    assert resolve_tp_sl_pct(None) == NATURE_TP_SL["scalp"]


def test_parse_db_naive_to_utc_beijing_semantics():
    """DB naive 时间按北京（+8）解读 → UTC。"""
    from datetime import datetime, timezone
    from backend.utils.db_datetime import parse_db_naive_to_utc
    dt = datetime(2026, 8, 21, 10, 0, 0)  # 北京钟面
    out = parse_db_naive_to_utc(dt)
    assert out is not None
    assert out == datetime(2026, 8, 21, 2, 0, 0, tzinfo=timezone.utc)


def test_auto_fill_tenant_hook_sees_declared_column():
    """_auto_fill_tenant_id 对模型声明的 tenant_id 列生效（hasattr→mapper 修复）。"""
    from backend.core.tenant import tenant_id_var
    from backend.database.connection import _auto_fill_tenant_id

    class _Mapped:
        """模拟已声明 tenant_id 的 ORM 对象（mapper 有 column_attrs）。"""
        class __mapper__:
            column_attrs = {"id": None, "tenant_id": None}  # dict-like 支持 .keys()

        def __init__(self):
            self.id = 1
            self.tenant_id = None

    class _MappedNoCol:
        class __mapper__:
            column_attrs = {"id": None}

        def __init__(self):
            self.id = 2

    class _NoMapper:
        def __init__(self):
            self.id = 3

    class _FakeSession:
        new = [_Mapped(), _MappedNoCol(), _NoMapper()]

    tenant_id_var.set(0)  # 0 表示"系统上下文缺省"
    try:
        fs = _FakeSession()
        _auto_fill_tenant_id(fs, None, None)
    finally:
        tenant_id_var.set(None)
    # [2026-08-22 M1-1b] 兜底与事务 GUC 对齐：AUTH_LOCAL_TENANT 优先，否则 1。
    # 旧行为恒填 1 与 RLS GUC(326) 分叉 → strategy_trades 后台写入被 RLS 拒绝。
    import os as _os
    _expected = 1
    try:
        _expected = int(str(_os.environ.get("AUTH_LOCAL_TENANT", "1") or "1"))
    except ValueError:
        _expected = 1
    assert fs.new[0].tenant_id == _expected  # 声明列 → 填充与 GUC 一致的兜底租户
    assert getattr(fs.new[1], "tenant_id", None) is None  # 未声明 → 不填（保持 None）
    assert not hasattr(fs.new[2], "tenant_id")
