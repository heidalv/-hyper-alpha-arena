# -*- coding: utf-8 -*-
"""[§83 契约 2026-09-11 / 目标①③] 两个"核验器"必须**能红**（否则是橡皮图章）。

被验证的两个纯判定：
  1. `Z235.classify_shadow_verdict` —— 熔断状态与净额口径是否一致（误伤/漏报/一致）；
  2. `Z236.classify_suppression_events` —— 抑制事件的安全判定
     （保护性通道被抑制 / 悬挂仓位 / 正常 / 无数据）。

为什么必须测：这两个脚本当前都输出"零缺陷"（误伤 0、抑制 0），
若判定逻辑本身写错（例如永远返回一致），真实缺陷也会被报成"通过"。
本文件用**合成数据**双向验证：造一个该红的输入 ⇒ 必须红；造正常输入 ⇒ 必须绿。
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def z235():
    return _load("z235", "_audit_ml/Z235_breaker_harm_check.py")


@pytest.fixture(scope="module")
def z236():
    return _load("z236", "_audit_ml/Z236_suppression_safety_monitor.py")


# ── ① 误伤/漏报判定 ────────────────────────────────────────────────────────

def test_harm_check_detects_mis_suppression(z235):
    """**能红**：shadow 但窗口净额为正 ⇒ 必须判为"误伤"。"""
    assert z235.classify_shadow_verdict(is_shadow=True, n_win=15, w=15,
                                        net_win=12.5, wr_win=0.33, wr_thr=0.40) == "误伤"


def test_harm_check_accepts_correct_suppression(z235):
    """**能绿**：shadow 且窗口净额为负 ⇒ "一致"。"""
    assert z235.classify_shadow_verdict(is_shadow=True, n_win=15, w=15,
                                        net_win=-21.7, wr_win=0.20, wr_thr=0.40) == "一致"


def test_harm_check_detects_missed_channel(z235):
    """**能红**：未 shadow、窗口胜率<阈值且净额为负 ⇒ "漏报"。"""
    assert z235.classify_shadow_verdict(is_shadow=False, n_win=20, w=15,
                                        net_win=-30.0, wr_win=0.35, wr_thr=0.40) == "漏报"


def test_harm_check_stays_silent_on_thin_samples(z235):
    """样本不足 ⇒ 不表态（"-"），避免用 3 笔数据下结论。"""
    assert z235.classify_shadow_verdict(is_shadow=True, n_win=3, w=15,
                                        net_win=5.0, wr_win=0.66, wr_thr=0.40) == "-"
    assert z235.classify_shadow_verdict(is_shadow=False, n_win=14, w=15,
                                        net_win=-99.0, wr_win=0.0, wr_thr=0.40) == "-"


def test_harm_check_does_not_flag_healthy_channel(z235):
    """未 shadow 且盈利 ⇒ 不表态（既不误伤也不漏报）。"""
    assert z235.classify_shadow_verdict(is_shadow=False, n_win=15, w=15,
                                        net_win=57.6, wr_win=1.0, wr_thr=0.40) == "-"


# ── ② 抑制事件安全判定 ────────────────────────────────────────────────────

def _ev(symbol="BTCUSDT", channel="trend_broken", hours_ago=1.0, resolved_h=None,
        resolved_channel="sl"):
    e = {
        "symbol": symbol, "channel": channel, "tier": "mid",
        "suppressed_at": datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    }
    if resolved_h is not None:
        e["resolved_at"] = e["suppressed_at"] + timedelta(hours=resolved_h)
        e["resolved_channel"] = resolved_channel
    return e


def test_suppression_monitor_flags_protected_channel(z236):
    """**能红**：抑制了一个保护性通道 ⇒ FAIL + violation（P19-B 的硬边界）。"""
    res = z236.classify_suppression_events([_ev(channel="sl", hours_ago=1.0)])
    assert res["verdict"] == "FAIL"
    assert res["violations"] and "保护性通道被抑制" in res["violations"][0]["why"]


def test_suppression_monitor_flags_dangling_position(z236):
    """**能红**：抑制后超过悬挂上限仍未离场 ⇒ FAIL + dangling。"""
    res = z236.classify_suppression_events([_ev(hours_ago=100.0)], hang_limit_h=48.0)
    assert res["verdict"] == "FAIL"
    assert res["dangling"] and "仍未离场" in res["dangling"][0]["why"]


def test_suppression_monitor_passes_when_resolved(z236):
    """**能绿**：抑制后由保护性通道离场 ⇒ PASS。"""
    res = z236.classify_suppression_events(
        [_ev(hours_ago=10.0, resolved_h=2.0, resolved_channel="sl")], hang_limit_h=48.0)
    assert res["verdict"] == "PASS"
    assert not res["violations"] and not res["dangling"]


def test_suppression_monitor_passes_within_hang_limit(z236):
    """抑制后仍持有但**未超**上限 ⇒ PASS（不是悬挂）。"""
    res = z236.classify_suppression_events([_ev(hours_ago=5.0)], hang_limit_h=48.0)
    assert res["verdict"] == "PASS"


def test_suppression_monitor_reports_no_data_honestly(z236):
    """没有事件 ⇒ NO_DATA（不假装通过 —— 当前线上正是此情形）。"""
    res = z236.classify_suppression_events([])
    assert res["verdict"] == "NO_DATA" and res["n"] == 0


def test_suppression_monitor_marks_resolution_channel(z236):
    """可追溯性：解决事件要能带出"最终由哪个通道离场"。"""
    res = z236.classify_suppression_events(
        [_ev(hours_ago=10.0, resolved_h=1.5, resolved_channel="max_hold_timeout")])
    assert res["ok"][0]["resolved_channel"] == "max_hold_timeout"


# ── ③ 反事实代理的符号约定（错一个符号就会把结论反过来）─────────────────────

@pytest.fixture(scope="module")
def z237():
    return _load("z237", "_audit_ml/Z237_suppression_counterfactual.py")


def test_counterfactual_sign_long(z237):
    """多头：离场后价格上涨 ⇒ 持有更好（delta>0）。"""
    d = z237.counterfactual_delta(size=1.0, side="long", p0=100.0, p1=101.0, actual_net=-5.0)
    assert d == pytest.approx(1.0 - (-5.0))          # 价格项 +1.0，实际 −5.0 ⇒ +6.0
    d2 = z237.counterfactual_delta(size=1.0, side="long", p0=100.0, p1=99.0, actual_net=-5.0)
    assert d2 == pytest.approx(-1.0 + 5.0)           # 价格项 −1.0 ⇒ +4.0（比实际少亏）


def test_counterfactual_sign_short(z237):
    """空头：离场后价格下跌 ⇒ 持有更好。"""
    d = z237.counterfactual_delta(size=2.0, side="short", p0=100.0, p1=98.0, actual_net=-10.0)
    assert d == pytest.approx(4.0 + 10.0)            # (98−100)×2×(−1) = +4
    d2 = z237.counterfactual_delta(size=2.0, side="sell", p0=100.0, p1=103.0, actual_net=1.0)
    assert d2 == pytest.approx(-6.0 - 1.0)           # 价格项 −6 ⇒ 持有更差


def test_counterfactual_delta_equal_when_price_unchanged(z237):
    """价格没动 ⇒ delta = −实际净额（只有已经发生的盈亏差）。"""
    assert z237.counterfactual_delta(size=1.0, side="long", p0=100.0, p1=100.0,
                                     actual_net=-3.0) == pytest.approx(3.0)


# ── ④ P27-A 咨询式降级的计数（不会进事件流，只能扫日志）────────────────────

def test_stale_advisory_counter_counts_by_channel(z236):
    """**能红也能绿**：识别 P27-A 的降级行并按通道计数，忽略其它行。"""
    lines = [
        "2026-09-11 17:00:00 [WARNING] [ExitChannelBreaker] 证据过期 ⇒ 本次不抑制（P27-A）："
        "mid|trend_broken 最新样本 13.8 天 > 7 天",
        "2026-09-11 17:01:00 [WARNING] [ExitChannelBreaker] 证据过期 ⇒ 本次不抑制（P27-A）："
        "mid|trend_broken 最新样本 13.9 天 > 7 天",
        "2026-09-11 17:02:00 [WARNING] [ExitChannelBreaker] 证据过期 ⇒ 本次不抑制（P27-A）："
        "mid|midlong 最新样本 7.8 天 > 7 天",
        "2026-09-11 17:03:00 [INFO] [UnifiedExit] 通道熔断抑制离场 BTCUSDT[mid] （命中 mid|midlong）",
    ]
    out = z236.count_stale_advisories(lines)
    assert out == {"mid|trend_broken": 2, "mid|midlong": 1}


def test_stale_advisory_counter_empty_when_no_lines(z236):
    """没有降级行 ⇒ 空 dict（不编造）。"""
    assert z236.count_stale_advisories(["随便一行", ""]) == {}
