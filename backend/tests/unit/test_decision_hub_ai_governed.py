# -*- coding: utf-8 -*-
"""
T6 验证：decision_hub ai_governed 模式（v6 计划 6.3 第 1/2 项）。

AI 提案 + 安全网否决，paper 起步，env 开关（MLTO_AI_GOVERNED）默认关：
  1. direction = llm_qual 单调映射（≥0.55 多 / ≤0.45 空），框架禁止覆盖 AI 方向
  2. orch_bias 仅在 LLM 中性（0.45-0.55）时兜底
  3. composite 主由 llm_qual 决定（权重=灰度档位），framework 仅参照偏移（×0.4）
  4. consistency 惩罚删除（adjusted = composite）
  5. 灰度权重阶梯 0.40 → 0.60 → 1.0（0.60 档前须完成 confidence 校准）
  6. 安全网仍生效：readiness 档位（WAIT/NIBBLE/BUILD）否决权保留
  7. 默认关：现有 standard 行为完全不变（回归）

运行：.venv\\Scripts\\python.exe -m pytest backend\\tests\\unit\\test_decision_hub_ai_governed.py -q
"""
from __future__ import annotations

import pytest

from backend.services.mlto import decision_hub as dh
from backend.services.mlto.types import Signal


@pytest.fixture(autouse=True)
def _reset_mode():
    """每个测试后复位模块级开关（测试内用 monkeypatch 修改）。"""
    yield
    dh._AI_GOVERNED = False
    dh._AI_GOVERNED_WEIGHT = 0.40


def _sig_llm_fw(llm_val=0.70, fw_val=0.5, orch_val=None, fw_count=5):
    """构造 llm_qual + N 个 framework 信号（可选 orch_bias）。"""
    sigs = [
        Signal("llm_qual", float(llm_val), 0.9, "llm"),
    ]
    if orch_val is not None:
        sigs.append(Signal("orch_mid_bias", float(orch_val), 0.8, "framework"))
    for i in range(fw_count):
        sigs.append(Signal(
            ["quant_alignment", "entry_timing", "thesis_health",
             "analyst_consensus", "feedback_loop"][i],
            float(fw_val), 0.8, "framework"))
    return sigs


# ═══════════════════════════════════════════════════════════════════
# 1. 方向单调映射 + 框架禁止覆盖
# ═══════════════════════════════════════════════════════════════════

def test_llm_bullish_direction_survives_bearish_framework(monkeypatch):
    """llm=0.70（明确多）即使 framework 全部偏空，方向仍为 long。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    d = dh.fuse_signals(_sig_llm_fw(llm_val=0.70, fw_val=0.20), "mid", mode="paper")
    assert d.direction == "long", "框架偏空覆盖了 AI 看多方向"


def test_llm_bearish_direction_survives_bullish_framework(monkeypatch):
    """llm=0.30（明确空）即使 framework 全部偏多，方向仍为 short。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    d = dh.fuse_signals(_sig_llm_fw(llm_val=0.30, fw_val=0.80), "mid", mode="paper")
    assert d.direction == "short", "框架偏多覆盖了 AI 看空方向"


def test_llm_monotonic_mapping_boundaries(monkeypatch):
    """单调映射边界：0.55→long、0.45→short、0.50 中性。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    sigs = _sig_llm_fw(llm_val=0.55, fw_val=0.3)
    assert dh.fuse_signals(sigs, "mid", mode="paper").direction == "long"
    sigs = _sig_llm_fw(llm_val=0.45, fw_val=0.7)
    assert dh.fuse_signals(sigs, "mid", mode="paper").direction == "short"


# ═══════════════════════════════════════════════════════════════════
# 2. orch_bias 仅在 LLM 中性时兜底
# ═══════════════════════════════════════════════════════════════════

def test_neutral_llm_orb_bias_fallback_long(monkeypatch):
    """LLM 中性（0.50）+ orch 强多 → 兜底 long。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    d = dh.fuse_signals(_sig_llm_fw(llm_val=0.50, fw_val=0.5, orch_val=0.80),
                        "mid", mode="paper")
    assert d.direction == "long"


def test_neutral_llm_orb_bias_fallback_short(monkeypatch):
    """LLM 中性（0.50）+ orch 强空 → 兜底 short。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    d = dh.fuse_signals(_sig_llm_fw(llm_val=0.50, fw_val=0.5, orch_val=0.20),
                        "mid", mode="paper")
    assert d.direction == "short"


def test_neutral_llm_no_hint_falls_to_neutral(monkeypatch):
    """LLM 中性 + 无 orch + framework 中性 → neutral。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    monkeypatch.setattr(dh, "_nibble_probe_enabled", lambda: False)
    d = dh.fuse_signals(_sig_llm_fw(llm_val=0.50, fw_val=0.5), "mid", mode="paper")
    assert d.direction == "neutral"


def test_neutral_llm_framework_cannot_flip(monkeypatch):
    """ai_governed：LLM 中性时 framework 偏多也不得翻向（仅 orch 可兜底）。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    monkeypatch.setattr(dh, "_nibble_probe_enabled", lambda: False)
    d = dh.fuse_signals(_sig_llm_fw(llm_val=0.50, fw_val=0.90), "mid", mode="paper")
    assert d.direction == "neutral"
    assert d.dir_src == "llm_qual"


def test_nibble_probe_soft_direction_from_llm_band(monkeypatch):
    """Paper NIBBLE：llm 落在死区微偏（0.51）→ 探针给 long。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    monkeypatch.setattr(dh, "_nibble_probe_enabled", lambda: True)
    monkeypatch.setattr(dh, "_nibble_probe_quota_remaining", lambda: True)
    d = dh.fuse_signals(_sig_llm_fw(llm_val=0.51, fw_val=0.55), "long", mode="paper")
    assert d.action in ("NIBBLE", "BUILD")
    assert d.direction == "long"
    assert str(d.dir_src).startswith("nibble_probe")


def test_nibble_probe_build_neutral_also_leans(monkeypatch):
    """BUILD+neutral 也应探针（审计见 ETH BUILD/neutral 被 gate 拒）。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    monkeypatch.setattr(dh, "_nibble_probe_enabled", lambda: True)
    monkeypatch.setattr(dh, "_nibble_probe_quota_remaining", lambda: True)
    # 高 adj 走 BUILD，llm 仍中性微偏
    d = dh.fuse_signals(_sig_llm_fw(llm_val=0.54, fw_val=0.70), "long", mode="paper")
    assert d.action in ("NIBBLE", "BUILD")
    assert d.direction == "long"
    assert str(d.dir_src).startswith("nibble_probe")


def test_nibble_probe_disabled_keeps_neutral(monkeypatch):
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    monkeypatch.setattr(dh, "_nibble_probe_enabled", lambda: False)
    d = dh.fuse_signals(_sig_llm_fw(llm_val=0.51, fw_val=0.55), "long", mode="paper")
    assert d.direction == "neutral"


# ═══════════════════════════════════════════════════════════════════
# 3. composite 由 llm 主导 + framework 参照偏移
# ═══════════════════════════════════════════════════════════════════

def test_composite_llm_dominant_vs_standard(monkeypatch):
    """同信号下 ai_governed 的 llm 权重（0.40）> standard（0.30）且 framework 压缩。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    sigs = _sig_llm_fw(llm_val=0.80, fw_val=0.30)
    d_gov = dh.fuse_signals(sigs, "mid", mode="paper")
    monkeypatch.setattr(dh, "_AI_GOVERNED", False)
    d_std = dh.fuse_signals(sigs, "mid", mode="paper")
    assert d_gov.composite > d_std.composite, (
        f"ai_governed composite {d_gov.composite} 应高于 standard {d_std.composite}"
    )


def test_framework_only_shifts_tier_not_direction(monkeypatch):
    """框架只影响档位：llm 固定 0.70，框架极空 vs 极多 → 方向不变。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    d_weak = dh.fuse_signals(_sig_llm_fw(llm_val=0.70, fw_val=0.15), "mid", mode="paper")
    d_strong = dh.fuse_signals(_sig_llm_fw(llm_val=0.70, fw_val=0.90), "mid", mode="paper")
    assert d_weak.direction == "long" and d_strong.direction == "long"
    # 档位允许不同（参照偏移影响 NIBBLE/BUILD）——此处仅验证方向纪律
    assert d_strong.adjusted >= d_weak.adjusted


# ═══════════════════════════════════════════════════════════════════
# 4. consistency 惩罚删除
# ═══════════════════════════════════════════════════════════════════

def test_consistency_penalty_removed(monkeypatch):
    """大分歧下 ai_governed 无惩罚（cons=1.0, adjusted=composite）；standard 有惩罚。"""
    sigs = _sig_llm_fw(llm_val=0.90, fw_val=0.10)
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    d_gov = dh.fuse_signals(sigs, "mid", mode="paper")
    assert d_gov.consistency == 1.0, "ai_governed 下 consistency 惩罚应删除"
    assert d_gov.adjusted == d_gov.composite, "ai_governed 下 adjusted 应等于 composite"
    monkeypatch.setattr(dh, "_AI_GOVERNED", False)
    d_std = dh.fuse_signals(sigs, "mid", mode="paper")
    assert d_std.consistency < 1.0, "standard 模式大分歧惩罚应保留（回归）"


# ═══════════════════════════════════════════════════════════════════
# 5. 灰度权重阶梯
# ═══════════════════════════════════════════════════════════════════

def test_weight_ladder_increases_llm_impact(monkeypatch):
    """权重阶梯 0.40/0.60/1.0：llm 值对 composite 的边际影响递增。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    deltas = []
    for w in (0.40, 0.60, 1.0):
        monkeypatch.setattr(dh, "_AI_GOVERNED_WEIGHT", w)
        d_hi = dh.fuse_signals(_sig_llm_fw(llm_val=0.85, fw_val=0.5), "mid", mode="paper")
        d_lo = dh.fuse_signals(_sig_llm_fw(llm_val=0.15, fw_val=0.5), "mid", mode="paper")
        deltas.append(d_hi.composite - d_lo.composite)
    assert deltas[0] < deltas[1] < deltas[2], (
        f"权重阶梯应递增 LLM 影响力，实测 {deltas}"
    )


def test_weight_ladder_marked_in_decision(monkeypatch):
    """档位标记：ai_governed_weight 出现在 HubDecision。"""
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    d = dh.fuse_signals(_sig_llm_fw(), "mid", mode="paper")
    assert d.mode == "ai_governed"
    assert d.ai_governed_weight == 0.40
    assert "ai_governed" in d.reason_text
    assert "dir_src=" in d.reason_text
    monkeypatch.setattr(dh, "_AI_GOVERNED", False)
    d2 = dh.fuse_signals(_sig_llm_fw(), "mid", mode="paper")
    assert d2.mode == "standard"
    assert d2.ai_governed_weight is None


# ═══════════════════════════════════════════════════════════════════
# 6. 安全网否决保留
# ═══════════════════════════════════════════════════════════════════

def test_readiness_safety_net_still_vetoes(monkeypatch):
    """AI 方向明确（0.70）但 confidence 极低 + 框架弱偏空 → 整体 readiness 不足 → WAIT。

    注：composite 是加权平均，所有信号 confidence 同比例缩放不改变结果，
    必须让 LLM confidence（0.05）与 framework confidence（0.90）不同比例，
    才能让 llm 高值无法撑起 composite（≈0.13 < paper WAIT 0.28）。
    """
    monkeypatch.setattr(dh, "_AI_GOVERNED", True)
    sigs = [
        Signal("llm_qual", 0.70, 0.05, "llm"),  # AI 看多但自身信心极低
        Signal("quant_alignment", 0.10, 0.90, "framework"),
        Signal("entry_timing", 0.10, 0.90, "framework"),
    ]
    d = dh.fuse_signals(sigs, "mid", mode="paper")
    assert d.action == "WAIT", f"readiness 安全网应否决，实测 {d.action}"


def test_ai_governed_off_by_default():
    """默认（未设 env）ai_governed 关闭：standard 行为。"""
    assert dh._AI_GOVERNED is False
    d = dh.fuse_signals(_sig_llm_fw(llm_val=0.90, fw_val=0.10), "mid", mode="paper")
    assert d.mode == "standard"
    assert d.consistency < 1.0  # 惩罚仍在
