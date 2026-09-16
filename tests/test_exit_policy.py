# -*- coding: utf-8 -*-
"""ExitPolicy（backend/services/exit/exit_policy.py）纯逻辑单测（不连库）。

覆盖：车道默认与环境覆盖、序列化往返、固定优先级评估（结构失效价 → SL → TP → time_limit → 递减 ROI → trailing → hold）、
trailing 只朝有利方向收紧、长线车道"唯一出场 = L1/Chandelier"（无 TP/time_limit/递减 ROI）、MFE 分位标定。
"""
from __future__ import annotations

import os
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.exit import exit_policy as xp  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("EXIT_POLICY_"):
            monkeypatch.delenv(k, raising=False)
    yield


def _snap(side="long", entry=100.0, current=100.0, elapsed=0.0, peak=None, sl=None, tp=None, structural=None):
    roi = (current - entry) / entry * 100.0
    roi = roi if side == "long" else -roi
    return xp.ExitSnapshot(side=side, entry=entry, current=current, elapsed_sec=elapsed,
                           peak_roi_pct=(peak if peak is not None else roi), sl_price=sl, tp_price=tp,
                           structural_stop_price=structural)


def _policy(**kw) -> xp.ExitPolicy:
    base = dict(lane="mid", sl_pct=3.0, tp_pct=6.0, time_limit_sec=3600, trailing_activation_pct=2.0,
                trailing_callback_pct=1.0, structural_stop="price", min_roi=((1800, 0.5),), tp_stages=(2.0, 3.5, 5.5))
    base.update(kw)
    return xp.ExitPolicy(**base)


def test_defaults_per_lane_and_long_is_trend_only():
    short = xp.ExitPolicy.for_lane("short")
    assert short.time_limit_sec == 5400 and short.sl_pct == pytest.approx(0.6)
    long = xp.ExitPolicy.for_lane("long")
    assert long.structural_stop == "chandelier"
    assert long.tp_pct is None and long.time_limit_sec is None and long.min_roi == ()
    assert long.trailing_activation_pct is None
    # 未知车道 → mid
    assert xp.ExitPolicy.for_lane("weird").lane == "mid"


def test_env_override_and_off(monkeypatch):
    monkeypatch.setenv("EXIT_POLICY_SHORT_TIME_LIMIT_SEC", "7200")
    monkeypatch.setenv("EXIT_POLICY_SHORT_TP_PCT", "off")
    monkeypatch.setenv("EXIT_POLICY_SHORT_MIN_ROI", "600:0.5,1200:0.1")
    monkeypatch.setenv("EXIT_POLICY_SHORT_TP_STAGES", "0.4,0.8,1.5")
    p = xp.ExitPolicy.for_lane("short")
    assert p.time_limit_sec == 7200 and p.tp_pct is None
    assert p.min_roi == ((600, 0.5), (1200, 0.1))
    assert p.tp_stages == (0.4, 0.8, 1.5)
    monkeypatch.setenv("EXIT_POLICY_SHORT_MIN_ROI", "off")
    assert xp.ExitPolicy.for_lane("short").min_roi == ()
    monkeypatch.setenv("EXIT_POLICY_SHORT_ENABLED", "false")
    assert xp.ExitPolicy.for_lane("short").enabled is False


def test_roundtrip_dict():
    p = xp.ExitPolicy.for_lane("mid")
    d = p.to_dict()
    p2 = xp.ExitPolicy.from_dict(d)
    assert p2 == p
    # exit_state_json 里的声明优先于当前默认
    p3 = xp.policy_from_exit_state({"exit_policy": {**d, "tp_pct": 9.9}}, "mid")
    assert p3.tp_pct == pytest.approx(9.9)
    assert xp.policy_from_exit_state("not json", "short").lane == "short"


def test_precedence_structural_then_sl_then_tp():
    p = _policy()
    # 结构失效价先于一切
    v = xp.evaluate(p, _snap(current=97.5, sl=96.0, structural=98.0))
    assert v.is_close and v.reason == "structural_invalidation"
    # chandelier 模式不看 structural_stop_price（价在 sl_price 上，由 SL 判）
    v2 = xp.evaluate(_policy(structural_stop="chandelier"), _snap(current=97.5, sl=96.0, structural=98.0))
    assert v2.action == "hold"
    # SL 价
    v3 = xp.evaluate(p, _snap(current=95.9, sl=96.0))
    assert v3.is_close and v3.reason == "sl"
    # 声明 sl_pct 兜底（无 sl_price）
    v4 = xp.evaluate(p, _snap(current=96.9))
    assert v4.is_close and v4.reason == "sl_pct"
    # [2026-09-07] 有附着硬 SL 时，禁止声明 sl_pct 抢先软平
    # （硬 SL 在 95.5≈-4.5%，ROI -3.1% 未触及硬单 → hold，不得 sl_pct）
    v4b = xp.evaluate(p, _snap(current=96.9, sl=95.5))
    assert v4b.action == "hold", v4b
    # TP 价 / tp_pct
    v5 = xp.evaluate(p, _snap(current=104.0, tp=103.5))
    assert v5.is_close and v5.reason == "tp"
    v6 = xp.evaluate(p, _snap(current=106.5))
    assert v6.is_close and v6.reason == "tp_pct"
    # 空头方向对称
    v7 = xp.evaluate(p, _snap(side="short", current=103.1))
    assert v7.is_close and v7.reason == "sl_pct"
    # 空头：有硬 SL 时同样不软平
    v7b = xp.evaluate(p, _snap(side="short", current=103.1, sl=104.5))
    assert v7b.action == "hold", v7b
    v8 = xp.evaluate(p, _snap(side="short", current=93.9))
    assert v8.is_close and v8.reason == "tp_pct"


def test_mid_lane_round6_recalibration():
    """[2026-09-16 验收轮6] 中线出场档位按 9/14-9/16 实测重标定。

    两日 23 笔平仓 peak 合计 ~296 美元、giveback ~302.88（ASTER 峰值+0.21%→SL
    -38.66、VIRTUAL +0.54%→SL -38.45）：旧档位（TP1=2%、trail 4%/1.5%）按「价格
    走 2%+」校准，而探针仓峰值只有 0.2~0.5% → 止盈永不触发、止损必然吃到。
    新口径：trail 1.0/0.5、TP 档 0.8/1.6/3.0、min_roi 12h<0.5%→24h<0.0 强平、
    time_limit 48h（对齐 TIER_MID_MAX_HOLD_SEC=172800）。回滚 = 改回旧值。
    """
    mid = xp.ExitPolicy.for_lane("mid")
    assert mid.trailing_activation_pct == pytest.approx(1.0)
    assert mid.trailing_callback_pct == pytest.approx(0.5)
    assert mid.tp_stages == (0.8, 1.6, 3.0)
    assert mid.min_roi == ((43200, 0.5), (86400, 0.0))
    assert mid.time_limit_sec == 172800


def test_time_limit_and_min_roi_decay():
    p = _policy()
    # time_limit
    v = xp.evaluate(p, _snap(current=100.5, elapsed=3600))
    assert v.is_close and v.reason == "time_limit"
    # 递减 ROI：30 分钟后 roi<0.5% → close；≥0.5% → 继续
    v2 = xp.evaluate(p, _snap(current=100.2, elapsed=1800))
    assert v2.is_close and v2.reason == "min_roi_decay"
    v3 = xp.evaluate(p, _snap(current=100.6, elapsed=1800))
    assert v3.action == "hold"
    # 未到时间不触发
    v4 = xp.evaluate(p, _snap(current=99.5, elapsed=1000))
    assert v4.action == "hold"
    # 多档取已到达的最后一档
    p2 = _policy(min_roi=((600, 1.0), (1200, 0.2)), time_limit_sec=None)
    assert xp.evaluate(p2, _snap(current=100.5, elapsed=700)).reason == "min_roi_decay"
    assert xp.evaluate(p2, _snap(current=100.5, elapsed=1300)).action == "hold"


def test_trailing_activation_callback_and_lock_only_favourable():
    p = _policy(time_limit_sec=None, min_roi=())
    # 未激活（峰值 1.5% < 2%）→ hold
    assert xp.evaluate(p, _snap(current=101.5)).action == "hold"
    # 激活（峰值 3%），当前 2.5%：回撤 0.5% < 1% → tighten_sl 到 peak−cb = 2% → 102.0
    v = xp.evaluate(p, _snap(current=102.5, peak=3.0, sl=97.0))
    assert v.action == "tighten_sl" and v.new_sl == pytest.approx(102.0)
    # 已有更高 SL（102.2 > 锁价 102.0）→ 不回退
    v2 = xp.evaluate(p, _snap(current=102.5, peak=3.0, sl=102.2))
    assert v2.action == "hold"
    # 回撤 ≥ 1% → close
    v3 = xp.evaluate(p, _snap(current=101.9, peak=3.0, sl=97.0))
    assert v3.is_close and v3.reason == "trailing_callback"
    # 空头对称：峰值 3%（价 97），当前 97.5（roi 2.5%）→ 锁 2% → SL 98.0
    v4 = xp.evaluate(p, _snap(side="short", current=97.5, peak=3.0, sl=103.0))
    assert v4.action == "tighten_sl" and v4.new_sl == pytest.approx(98.0)


def test_long_lane_lets_profit_run():
    p = xp.ExitPolicy.for_lane("long")
    # 巨大浮盈、很长持仓 → 仍 hold（唯一出场是 L1/Chandelier，价在 sl_price）
    assert xp.evaluate(p, _snap(current=180.0, elapsed=86400 * 120)).action == "hold"
    # Chandelier（sl_price）被打穿 → close
    v = xp.evaluate(p, _snap(current=149.0, sl=150.0, peak=80.0))
    assert v.is_close and v.reason == "sl"


def test_disabled_and_bad_inputs():
    assert xp.evaluate(_policy(enabled=False), _snap(current=50.0)).action == "hold"
    assert xp.evaluate(_policy(), _snap(current=0.0)).action == "hold"


def test_calibrate_tp_stages():
    assert xp.calibrate_tp_stages([1.0] * 10) == ()
    mfes = [i / 10.0 for i in range(1, 101)]  # 0.1 .. 10.0
    st = xp.calibrate_tp_stages(mfes)
    assert len(st) == 3 and st[0] < st[1] < st[2]
    assert st[0] == pytest.approx(5.0, abs=0.2) and st[2] == pytest.approx(8.5, abs=0.2)
    # 全相同 → 强制递增
    st2 = xp.calibrate_tp_stages([2.0] * 40)
    assert st2[0] < st2[1] < st2[2]


def test_describe():
    d = xp.describe()
    assert set(d["lanes"]) == set(xp.LANES) and d["enforce"] is True
