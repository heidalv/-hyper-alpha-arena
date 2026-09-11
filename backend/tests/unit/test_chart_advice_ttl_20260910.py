# -*- coding: utf-8 -*-
"""[P11 / §69 执行 2026-09-10] 图审 `position_advice` 立场型建议的独立有效期（TTL）。

实证（`_audit_ml/Z178`，48h 审计流）：
  * `chart_gate` 否决 **1,419** 条，其中 **1,392 条（98.1%）** 来自单条
    `position_advice=no_new_long`，仅 27 条来自"亏损后同向再开缺支持"；
  * 而**审计行里没有年龄字段** ⇒ 无法判断这条禁令是否新鲜（可追溯性缺口）。

修法（两半）：
  1. **时效**：给 `position_advice` 单独设 `MIDLONG_CHART_ADVICE_TTL_MIN`（默认 **180min**，
     短于通用信号上限 `MIDLONG_CHART_MAX_SIGNAL_AGE_MIN`=240min；0 = 不启用该特例）；
  2. **可追溯**：否决原因里写入 `age=<min>min ttl=<min>min`（漏斗按前缀归并，不受影响）。

本测试锁定：新鲜建议仍否决、过期立场建议放行、TTL=0 关闭特例、通用陈旧规则不受影响、
以及原因串里确实带上了年龄。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def _gate(monkeypatch, *, age_min: int, advice: str = "no_new_long", direction: int = 1, strength: float = 5.0):
    from backend.services.full_auto import midlong_chart_gate as g

    monkeypatch.setattr(g, "_enabled", lambda: True)
    monkeypatch.setattr(g, "_chart_required", lambda: False)
    now_ms = int(time.time() * 1000)
    monkeypatch.setattr(g, "_latest_chart_signal", lambda _s: {
        "direction": direction,
        "strength": strength,
        "created_ms": now_ms - age_min * 60_000,
        "payload": {"position_advice": advice},
    })
    return g


def test_fresh_advice_still_vetoes(monkeypatch):
    """新鲜立场建议仍否决。[2026-09-11] 方向一致性默认开启后，
    否决需要「建议与方向一致」：看空(-1) + no_new_long 才拦开多。"""
    monkeypatch.setenv("MIDLONG_CHART_ADVICE_TTL_MIN", "180")
    g = _gate(monkeypatch, age_min=1, direction=-1)
    ok, reason, detail = g.chart_gate_check("UNI", "buy", tier="mid")
    assert ok is False, reason
    assert "no_new_long" in reason
    assert "age=" in reason and "ttl=" in reason, f"原因串缺少年龄/时效: {reason}"
    assert detail["signal_age_min"] <= 2


def test_stale_stance_advice_does_not_veto(monkeypatch):
    """200min 前给的建议：超过立场 TTL(180) 但仍在通用上限(240) 之内 ⇒ 不否决。"""
    monkeypatch.setenv("MIDLONG_CHART_ADVICE_TTL_MIN", "180")
    g = _gate(monkeypatch, age_min=200, direction=-1)
    ok, reason, _ = g.chart_gate_check("UNI", "buy", tier="mid")
    assert ok is True, f"过期立场建议仍否决: {reason}"
    assert "立场建议陈旧" in reason and "fail-open" in reason


def test_advice_ttl_zero_disables_the_special_case(monkeypatch):
    """TTL=0 ⇒ 关闭特例：仍按通用上限判（200min < 240min ⇒ 否决）。
    [2026-09-11] 方向一致性默认开启会使「方向相同」的建议被忽略，
    故本用例显式用回滚档（=false）锁定旧口径。"""
    monkeypatch.setenv("MIDLONG_CHART_ADVICE_TTL_MIN", "0")
    monkeypatch.setenv("MIDLONG_CHART_ADVICE_DIRECTION_CONSISTENT", "false")
    g = _gate(monkeypatch, age_min=200)
    ok, reason, _ = g.chart_gate_check("UNI", "buy", tier="mid")
    assert ok is False, f"TTL=0 应关闭特例但实际放行: {reason}"
    assert "no_new_long" in reason


def test_general_signal_age_rule_still_applies(monkeypatch):
    """通用陈旧规则（默认 240min）仍在：250min 的信号一律不否决。"""
    monkeypatch.setenv("MIDLONG_CHART_ADVICE_TTL_MIN", "180")
    monkeypatch.setenv("MIDLONG_CHART_MAX_SIGNAL_AGE_MIN", "240")
    g = _gate(monkeypatch, age_min=250, direction=-1)
    ok, reason, _ = g.chart_gate_check("UNI", "buy", tier="mid")
    assert ok is True and "陈旧" in reason, reason


def test_short_direction_symmetric(monkeypatch):
    """对称性：[2026-09-11] 方向一致性下，看多(+1) + no_new_short 才拦开空。"""
    monkeypatch.setenv("MIDLONG_CHART_ADVICE_TTL_MIN", "180")
    g = _gate(monkeypatch, age_min=1, advice="no_new_short", direction=1)
    ok, reason, _ = g.chart_gate_check("UNI", "sell", tier="mid")
    assert ok is False and "no_new_short" in reason, reason


def test_advice_ttl_key_registered():
    """新键必须登记（否则 .env 改写会以"未知 flag"告警形式暴露，或更糟——无人察觉）。"""
    from backend.config.env_registry import KNOWN_FLAGS

    for k in ("MIDLONG_CHART_ADVICE_TTL_MIN", "MIDLONG_CHART_MAX_SIGNAL_AGE_MIN"):
        assert k in KNOWN_FLAGS, f"{k} 未登记到 env_registry.KNOWN_FLAGS"


def test_audit_reason_prefix_is_stable_for_funnel_grouping():
    """漏斗按 `chart_gate_veto` 前缀归并 —— 追加 age/ttl 不得改变前缀。"""
    from backend.services.mlto.midlong_direction_audit import summarize_decision_funnel  # noqa: F401

    g = _gate_instant = None  # 仅静态检查前缀（见下）
    src = (ROOT / "backend/services/full_auto/midlong_chart_gate.py").read_text(encoding="utf-8")
    assert '"chart_gate_veto: 图审 position_advice=no_new_long (age=' in src.replace("f", "")
    assert src.count("chart_gate_veto") >= 3
