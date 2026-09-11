# -*- coding: utf-8 -*-
"""[2026-09-10 第十九轮] 浮盈锁（mid 追踪 0.5%/0.15%）+ 减半幂等根修 契约测试。

## 数据依据（`_audit_ml/Y12~Y16`，近 30 天 184 笔真实成交，总 USD 口径）

- 「浮盈→大亏」模式：32 笔合计 **-$216.04**（其中 22 笔无部分平仓，-$172.80）；
  典型峰值仅 +1.2%，而 SL/失效位在 -5~-6% —— 赚小亏大的几何。
- mid 层加浮盈锁（峰值≥0.5% 后 SL 跟到 peak-0.15%）：总 USD -$63.71 → **-$28.54**，
  ≤-2% 笔数 24 → 13；long 层基线本已 +$55.41，加锁反而变差（故 long 不改）。
- 单独收紧 SL 更差（-$110 ~ -$187），必须「SL 宽度不变 + 浮盈锁」组合。

## 契约

1. `ExitPolicy.for_lane("mid")` 在 .env 覆盖下 = 追踪激活 0.5% / 回撤 0.15%；
2. `evaluate`：峰值 ≥ 激活且回撤 < callback → tighten_sl（锁定 peak-callback）；
3. `evaluate`：回撤 ≥ callback → close(trailing_callback)；
4. 防抖：SL 改善 < `EXIT_POLICY_TRAILING_MIN_STEP_PCT`（默认 0.05%）→ 不返回 tighten_sl；
5. 减半幂等：`manage_long_position` 读 DB 实时 `exit_state_json`（不再读陈旧快照）。
"""
import importlib
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

XP = "backend.services.exit.exit_policy"


def _policy(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    m = importlib.import_module(XP)
    m = importlib.reload(m)
    return m


def test_mid_lane_lock_params(monkeypatch):
    """[2026-09-10 第二十一轮回滚] mid 追踪回到 3.0/1.5。

    依据：learned 门上线后，门放行子集上任何浮盈锁都更差
    （`_audit_ml/Y24_gate_lock_interaction.py`：不锁 -$8.62 vs 锁 0.5/0.15 -$72.84；
    连 3.0/0.9 宽锁也 -$81.32）——门选动量延续，紧锁杀延续。
    """
    m = _policy(
        monkeypatch,
        EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT="3.0",
        EXIT_POLICY_MID_TRAILING_CALLBACK_PCT="1.5",
    )
    p = m.ExitPolicy.for_lane("mid")
    assert p.trailing_activation_pct == 3.0
    assert p.trailing_callback_pct == 1.5


def test_trailing_locks_profit_at_small_peak(monkeypatch):
    """（形态能力保留）峰值 0.6%、当前 0.5%（回撤 0.1 < 0.15）→ 收紧 SL 到 0.45%。

    注：生产 mid 当前不启用该紧锁（见上一用例），本用例只验证机制本身可用，
    供后续按车道/按 regime 条件化启用。
    """
    m = _policy(
        monkeypatch,
        EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT="0.5",
        EXIT_POLICY_MID_TRAILING_CALLBACK_PCT="0.15",
        EXIT_POLICY_TRAILING_MIN_STEP_PCT="0.05",
    )
    p = m.ExitPolicy.for_lane("mid")
    snap = m.ExitSnapshot(side="long", entry=100.0, current=100.5, elapsed_sec=3600,
                          peak_roi_pct=0.6, sl_price=94.0)
    v = m.evaluate(p, snap)
    assert v.action == "tighten_sl", v
    assert abs(v.new_sl - 100.45) < 1e-6, v.new_sl


def test_trailing_closes_on_giveback(monkeypatch):
    """峰值 0.6%、当前 0.4%（回撤 0.2 ≥ 0.15）→ close。"""
    m = _policy(
        monkeypatch,
        EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT="0.5",
        EXIT_POLICY_MID_TRAILING_CALLBACK_PCT="0.15",
    )
    p = m.ExitPolicy.for_lane("mid")
    snap = m.ExitSnapshot(side="long", entry=100.0, current=100.4, elapsed_sec=3600,
                          peak_roi_pct=0.6, sl_price=94.0)
    v = m.evaluate(p, snap)
    assert v.is_close and v.reason == "trailing_callback", v


def test_trailing_step_throttle(monkeypatch):
    """SL 已在 100.84（目标 100.85，改善 0.01% < 0.05%）→ 不下发 tighten_sl。"""
    m = _policy(
        monkeypatch,
        EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT="0.5",
        EXIT_POLICY_MID_TRAILING_CALLBACK_PCT="0.15",
        EXIT_POLICY_TRAILING_MIN_STEP_PCT="0.05",
    )
    p = m.ExitPolicy.for_lane("mid")
    snap = m.ExitSnapshot(side="long", entry=100.0, current=100.95, elapsed_sec=3600,
                          peak_roi_pct=1.0, sl_price=100.84)
    v = m.evaluate(p, snap)
    assert v.action == "hold", v
    # 关掉防抖（step=0）→ 恢复收紧到 100.85
    m2 = _policy(
        monkeypatch,
        EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT="0.5",
        EXIT_POLICY_MID_TRAILING_CALLBACK_PCT="0.15",
        EXIT_POLICY_TRAILING_MIN_STEP_PCT="0",
    )
    v2 = m2.evaluate(m2.ExitPolicy.for_lane("mid"), snap)
    assert v2.action == "tighten_sl" and abs(v2.new_sl - 100.85) < 1e-6, v2


def test_long_lane_untouched_by_lock(monkeypatch):
    """long 车道默认不用 trailing（structural_stop=chandelier）。"""
    m = _policy(monkeypatch)
    p = m.ExitPolicy.for_lane("long")
    assert p.trailing_activation_pct is None and p.trailing_callback_pct is None


def test_read_exit_state_flags_prefers_fresh_db_row(monkeypatch):
    """陈旧快照无标记、DB 行有 dd_halve_done=True → 读 DB 实时行。"""
    from backend.services.long_trend_v2 import _read_exit_state_flags

    class _Row:
        def __init__(self, v):
            self._v = v

        def __getitem__(self, i):
            return self._v

    class _Q:
        def __init__(self, v):
            self._v = v

        def filter(self, *a, **k):
            return self

        def first(self):
            return _Row(self._v)

    class _DB:
        def query(self, *cols):
            return _Q('{"dd_halve_done": true, "early_np_done": true}')

    pos = {"id": 999, "exit_state_json": "{}"}  # 陈旧快照：无标记
    flags = _read_exit_state_flags(_DB(), pos)
    assert flags["dd_halve_done"] is True
    assert flags["early_np_done"] is True
    assert flags["target_halve_done"] is False
    # 无 db → 回退快照
    flags2 = _read_exit_state_flags(None, {"id": 1, "exit_state_json": '{"dd_halve_done": true}'})
    assert flags2["dd_halve_done"] is True
