# -*- coding: utf-8 -*-
"""[F366 2026-09-18] MLTO 量化层**服从**因子衰减治理（默认关）——关闭单向街。

背景（本次复查 §15.1 查实）：中长线把复检 IC **写进**衰减监视器
（`midlong_active_factor_set.py:344` `record_ic`），却**从不读**惩罚
（`get_factor_weight_penalty`）⇒ "中长线喂养治理，却不服从治理"。
设计文档 §15 的最小接线③点名要把惩罚接进 `mlto/quant_layer.py:18`。

本文件锁三件事：
1. **默认关时逐字节保持旧行为**（`sum(expected_alpha)` 的等价性，含浮点顺序）；
2. 开启后惩罚**按因子粒度**生效（retire→0 贡献、reduce→降权）；
3. 取不到/异常值一律**不动该因子**（fail-safe），且惩罚真正生效时**留一条日志**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto import quant_layer as Q  # noqa: E402
from backend.services.mlto.types import PerceptionPacket, ThesisDTO  # noqa: E402


def _packet(symbol: str = "BTC", tier: str = "mid") -> PerceptionPacket:
    return PerceptionPacket(
        symbol=symbol, tier=tier, session_id="s1", ts=0.0, price=100.0,
        market_summary_sym={"indicators_4h": {"rsi": 50.0}},
        orchestrator={"mid_bias": "bullish", "mid_confidence": 0.5},
        quant_brief={}, analyst_reports={},
    )


def _thesis(symbol: str = "BTC", tier: str = "mid") -> ThesisDTO:
    return ThesisDTO(thesis_id="t1", session_id="s1", symbol=symbol, tier=tier)


class _FakeExposure:
    def __init__(self, rows):
        self._rows = rows

    def exposure(self, symbol, period, n):
        return self._rows


def _install(monkeypatch, rows, penalties=None, raise_on=None, tier="mid",
             extra_env=None):
    """装配：暴露矩阵 + （可选）假衰减监视器；返回 anchor 信号值或 None。"""
    monkeypatch.setenv("FEATURE_MIDLONG_FACTOR_ANCHOR_ENABLED", "1")
    for k, v in (extra_env or {}).items():
        monkeypatch.setenv(k, v)
    import backend.services.factor_engine.exposure_service as ES
    monkeypatch.setattr(ES, "factor_exposure_service", _FakeExposure(rows), raising=False)

    if penalties is not None:
        import backend.services.factor_engine.factor_decay_monitor as DM

        class _FakeMonitor:
            def get_factor_weight_penalty(self, fid):
                if raise_on and fid == raise_on:
                    raise RuntimeError("boom")
                return penalties.get(fid, 1.0)
        monkeypatch.setattr(DM, "decay_monitor", _FakeMonitor(), raising=False)

    packet = _packet(tier=tier)
    sigs = Q.compute(packet, _thesis(tier=tier))
    for s in sigs:
        if s.name.startswith("factor_anchor_"):
            return s.value, s.name
    return None, None


def _val(*a, **k):
    return _install(*a, **k)[0]


def _name_of(*a, **k):
    return _install(*a, **k)[1]


# ───────────────── ① 默认关：必须与旧行为逐字节一致 ─────────────────

def test_disabled_matches_legacy_sum_exactly(monkeypatch):
    """关闭时 = 旧实现 `sum(expected_alpha) * 20` 截断（含浮点求和顺序）。"""
    monkeypatch.delenv(Q._ENV_DECAY, raising=False)
    rows = [{"factor_id": "f1", "expected_alpha": 0.01},
            {"factor_id": "f2", "expected_alpha": -0.004},
            {"factor_id": "f3", "expected_alpha": 0.002}]
    got = _val(monkeypatch, rows, penalties={"f1": 0.0, "f2": 0.3, "f3": 1.0})
    legacy = max(-1.0, min(1.0, sum(float(r["expected_alpha"]) for r in rows) * 20.0))
    assert got == pytest.approx(legacy, abs=0.0), "默认关时必须与旧公式完全一致"


def test_disabled_does_not_touch_monitor(monkeypatch):
    """关闭时**不得**去调监视器（否则又变成隐式依赖）。"""
    monkeypatch.delenv(Q._ENV_DECAY, raising=False)
    rows = [{"factor_id": "f1", "expected_alpha": 0.01}]
    got = _val(monkeypatch, rows, penalties={}, raise_on="f1")
    assert got is not None, "关闭时不应因监视器异常而丢信号"


# ───────────────── ② 开启后：按因子粒度生效 ─────────────────

def test_enabled_applies_per_factor_penalty(monkeypatch):
    monkeypatch.setenv(Q._ENV_DECAY, "1")
    rows = [{"factor_id": "retired", "expected_alpha": 0.05},
            {"factor_id": "reduced", "expected_alpha": 0.02},
            {"factor_id": "healthy", "expected_alpha": 0.01}]
    got = _val(monkeypatch, rows,
               penalties={"retired": 0.0, "reduced": 0.3, "healthy": 1.0})
    expect_alpha = 0.0 * 0.05 + 0.3 * 0.02 + 1.0 * 0.01
    assert got == pytest.approx(max(-1.0, min(1.0, expect_alpha * 20.0)))
    # 与"只按旧式求和"相比必须**更小**（退役因子被剔除）
    assert got < max(-1.0, min(1.0, sum(r["expected_alpha"] for r in rows) * 20.0))


def test_enabled_all_retired_gives_zero(monkeypatch):
    """全部退役 ⇒ 锚值为 0（不是负、也不是旧式的正）。"""
    monkeypatch.setenv(Q._ENV_DECAY, "1")
    rows = [{"factor_id": "a", "expected_alpha": 0.05},
            {"factor_id": "b", "expected_alpha": 0.02}]
    got = _val(monkeypatch, rows, penalties={"a": 0.0, "b": 0.0})
    assert got == pytest.approx(0.0)


def test_enabled_unknown_factor_unchanged(monkeypatch):
    """监视器里查不到的因子保持 1.0（不因"未入评估"被误杀）。"""
    monkeypatch.setenv(Q._ENV_DECAY, "1")
    rows = [{"factor_id": "never_seen", "expected_alpha": 0.01}]
    got = _val(monkeypatch, rows, penalties={})
    assert got == pytest.approx(0.01 * 20.0)


def test_enabled_monitor_error_is_failsafe(monkeypatch):
    """监视器抛异常 ⇒ 该因子不动（fail-safe），信号仍产出。"""
    monkeypatch.setenv(Q._ENV_DECAY, "1")
    rows = [{"factor_id": "boom", "expected_alpha": 0.01}]
    got = _val(monkeypatch, rows, penalties={}, raise_on="boom")
    assert got == pytest.approx(0.01 * 20.0)


def test_enabled_bad_penalty_values_are_ignored(monkeypatch):
    """负数 / NaN 惩罚不得把锚值推向反方向。"""
    monkeypatch.setenv(Q._ENV_DECAY, "1")
    rows = [{"factor_id": "neg", "expected_alpha": 0.01}]
    got = _val(monkeypatch, rows, penalties={"neg": -3.0})
    assert got == pytest.approx(0.01 * 20.0)


# ───────────────── ③ 可见性与开关语义 ─────────────────

def test_decay_applied_is_logged_once(monkeypatch, caplog):
    import logging
    monkeypatch.setenv(Q._ENV_DECAY, "1")
    Q._DECAY_LOGGED.clear()
    rows = [{"factor_id": "x", "expected_alpha": 0.01}]
    with caplog.at_level(logging.INFO):
        _val(monkeypatch, rows, penalties={"x": 0.3})
        _val(monkeypatch, rows, penalties={"x": 0.3})
    msgs = [r.message for r in caplog.records if "因子衰减惩罚已作用" in r.message]
    assert len(msgs) == 1, f"同 symbol 只应告警一次，实测 {len(msgs)}"


def test_flag_default_is_off():
    assert Q._decay_enabled() is False
    assert Q._decay_penalty_fn()("anything") == 1.0


def test_no_exposure_means_no_signal(monkeypatch):
    monkeypatch.setenv(Q._ENV_DECAY, "1")
    assert _val(monkeypatch, [], penalties={}) is None


# ───────────────── ④ F367：长线/中线的**周期**必须接对 ─────────────────

def test_anchor_period_follows_tier(monkeypatch):
    """[F367] 原实现写死 4h：`TIER_CONFIG` 真值是 short=15m/mid=1h/long=4h。

    ⇒ mid 车道被喂了不属于它的 4h 锚；long 车道也永远只有 4h、拿不到 1d/1w。
    """
    monkeypatch.delenv("MLTO_FACTOR_ANCHOR_PERIOD", raising=False)
    assert Q._anchor_period("mid") == "1h"
    assert Q._anchor_period("long") == "4h"
    assert Q._anchor_period("short") == "15m"
    assert Q._anchor_period("") == "4h", "未知 tier 回退 4h"


def test_anchor_period_env_override(monkeypatch):
    monkeypatch.setenv("MLTO_FACTOR_ANCHOR_PERIOD", "1d")
    assert Q._anchor_period("mid") == "1d"


def test_mid_packet_emits_1h_anchor(monkeypatch):
    monkeypatch.delenv(Q._ENV_DECAY, raising=False)
    rows = [{"factor_id": "f1", "expected_alpha": 0.01}]
    name = _name_of(monkeypatch, rows, penalties={}, tier="mid")
    assert name == "factor_anchor_1h", f"mid 应为 1h 锚，实测 {name}"


def test_long_packet_emits_4h_anchor(monkeypatch):
    monkeypatch.delenv(Q._ENV_DECAY, raising=False)
    rows = [{"factor_id": "f1", "expected_alpha": 0.01}]
    name = _name_of(monkeypatch, rows, penalties={}, tier="long")
    assert name == "factor_anchor_4h"


def test_exposure_is_requested_with_tier_period(monkeypatch):
    """暴露矩阵必须**用该 tier 的周期**去取（此前恒 "4h"）。"""
    monkeypatch.delenv(Q._ENV_DECAY, raising=False)
    seen = []

    class _Rec:
        def exposure(self, symbol, period, n):
            seen.append(period)
            return [{"factor_id": "f1", "expected_alpha": 0.01}]

    monkeypatch.setenv("FEATURE_MIDLONG_FACTOR_ANCHOR_ENABLED", "1")
    import backend.services.factor_engine.exposure_service as ES
    monkeypatch.setattr(ES, "factor_exposure_service", _Rec(), raising=False)
    Q.compute(_packet(tier="long"), _thesis(tier="long"))
    Q.compute(_packet(tier="mid"), _thesis(tier="mid"))
    assert seen == ["4h", "1h"], seen


# ───────────────── ⑤ F367：生产者与消费者必须对上（否则落到 0.01 兜底） ─────────────────

def test_hub_recognizes_anchor_prefix_weight(monkeypatch):
    """`decision_hub` 必须按前缀给 `factor_anchor_*` 权重，否则 0.01 兜底 ≈ 没接。

    直接测**权重解析**（`_base_weight_for`）而不是整条 `fuse_signals`：
    后者有 `_FW_REFERENCE_SCALE` 参照偏移与 trend bonus，会把锚的差异淹没在噪声里，
    用它断言会得出"看起来没接"的假结论（我第一版就是这么写错的）。
    """
    from backend.services.mlto import decision_hub as H
    monkeypatch.delenv("MLTO_FACTOR_ANCHOR_WEIGHT", raising=False)
    assert H._factor_anchor_weight() == pytest.approx(0.08)
    for tier_w in (H.WEIGHTS_MID, H.WEIGHTS_LONG):
        for per in ("15m", "1h", "4h", "1d", "1w"):
            name = f"factor_anchor_{per}"
            assert name not in tier_w, f"{name} 不应被逐个登记（应走前缀）"
            assert H._base_weight_for(name, tier_w) == pytest.approx(0.08), name
            assert H._base_weight_for(name, tier_w) != 0.01
    # 旋钮置 0 ⇒ 等于关闭该锚
    monkeypatch.setenv("MLTO_FACTOR_ANCHOR_WEIGHT", "0")
    assert H._base_weight_for("factor_anchor_4h", H.WEIGHTS_LONG) == 0.0


def test_hub_unknown_signal_still_uses_fallback(monkeypatch):
    """未登记且非锚前缀的信号仍应为 0.01 兜底（不扩大改动面）。"""
    from backend.services.mlto import decision_hub as H
    monkeypatch.delenv("MLTO_FACTOR_ANCHOR_WEIGHT", raising=False)
    assert H._base_weight_for("some_new_signal", H.WEIGHTS_MID) == 0.01
    assert H._base_weight_for("factor_anchor_", H.WEIGHTS_MID) == pytest.approx(0.08)
    # 登记过的信号必须仍取登记值（不被前缀规则或兜底覆盖）
    assert H._base_weight_for("llm_qual", H.WEIGHTS_LONG) == H.WEIGHTS_LONG["llm_qual"]
    assert H._base_weight_for("entry_timing", H.WEIGHTS_MID) == H.WEIGHTS_MID["entry_timing"]
