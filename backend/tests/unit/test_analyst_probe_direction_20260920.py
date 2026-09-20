# -*- coding: utf-8 -*-
"""[轮141 2026-09-20] LLM 中性时用**六域信号**给探针方向（路线 2）。

## 依据（轮140 实测，core.analysis_runs 最近 30 条 midlong_thesis）
neutral 14 / bearish 4 / bullish 2；`recommend_open` **False 18/18**；consensus 中位 **0.31**
⇒ 两个模型自己判断观望 ⇒ 中线整段不成交（**不是闸门问题**）。
用户架构要求"分析师数值化后作为一等 alpha 信号参与决策"，
故 LLM 给不出方向、regime 也判不出方向时，由六域一致方向做**有界小仓探针**。

## 规则（有界 + 可解释）
| 条件 | 默认 |
|---|---|
| 参与域数 | ≥ 2（`ANALYST_PROBE_MIN_DOMAINS`） |
| \\|blend score\\| | ≥ 0.20（`ANALYST_PROBE_MIN_ABS`） |
| 一致度 | 同向域 ≥ 2 且反向域 ≤ 1 |
| 总开关 | `ANALYST_PROBE_ENABLED`（默认 true） |

探针仍走 NIBBLE 小仓档 + 全部风控闸（V5/预算/组合/宪法/风控官），并写显式审计
`probe_entry:analyst_signal:…`。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.analysts import service as SVC  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("ANALYST_PROBE_ENABLED", "ANALYST_PROBE_MIN_ABS", "ANALYST_PROBE_MIN_DOMAINS"):
        monkeypatch.delenv(k, raising=False)
    yield


def _sig(domain, score, conf=1.0):
    return {"domain": domain, "symbol": "BTC", "score": score, "confidence": conf,
            "data_quality": "ok", "n_samples": 1, "as_of": "", "evidence": {},
            "missing_sources": [], "reason": "", "ts": ""}


def test_agreeing_signals_give_direction(monkeypatch):
    monkeypatch.setattr(SVC, "latest_signals", lambda *a, **k: [
        _sig("technical", -0.5), _sig("flow", -0.4), _sig("sentiment", -0.3),
    ])
    d, note = SVC.probe_direction("BTC")
    assert d == "short", f"三域同向看空应给 short：{d} {note}"
    assert "同向=" in note and "score=" in note


def test_long_direction_from_agreeing_signals(monkeypatch):
    monkeypatch.setattr(SVC, "latest_signals", lambda *a, **k: [
        _sig("technical", 0.4), _sig("flow", 0.3), _sig("macro", 0.25),
    ])
    d, _ = SVC.probe_direction("BTC")
    assert d == "long"


def test_threshold_blocks_weak_tilt(monkeypatch):
    monkeypatch.setenv("ANALYST_PROBE_MIN_ABS", "0.20")
    monkeypatch.setattr(SVC, "latest_signals", lambda *a, **k: [
        _sig("technical", -0.1), _sig("flow", -0.05),
    ])
    d, note = SVC.probe_direction("BTC")
    assert d == "" and "score" in note, f"弱倾向不得定方向：{note}"


def test_agreement_required(monkeypatch):
    """同向仅 1 域、反向 2 域 ⇒ 不得定方向（防一两票极端值绑架方向）。

    取一组**能过阈值**但同向只有 1 票的输入：technical −0.9（w .30）+ flow +0.2（w .25）
    + sentiment +0.1（w .20）⇒ 加权 ≈ −0.267（过 0.20），但同向={technical} 仅 1 票。
    """
    monkeypatch.setattr(SVC, "latest_signals", lambda *a, **k: [
        _sig("technical", -0.9), _sig("flow", 0.2), _sig("sentiment", 0.1),
    ])
    d, note = SVC.probe_direction("BTC")
    assert d == "" and "一致度" in note, f"一致度不足却给了方向：{d} {note}"


def test_min_domains_required(monkeypatch):
    monkeypatch.setattr(SVC, "latest_signals", lambda *a, **k: [_sig("technical", -0.9)])
    d, note = SVC.probe_direction("BTC")
    assert d == "" and "域数不足" in note


def test_switch_off(monkeypatch):
    monkeypatch.setenv("ANALYST_PROBE_ENABLED", "false")
    monkeypatch.setattr(SVC, "latest_signals", lambda *a, **k: [
        _sig("technical", -0.5), _sig("flow", -0.5),
    ])
    d, note = SVC.probe_direction("BTC")
    assert d == "" and "停用" in note


def test_brain_wires_probe_direction_in_sweep():
    """接线 ratchet：必须在 `probe_no_regime` 兜底**之前**调用，否则等于没接。"""
    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8", errors="replace")
    assert "probe_direction as _ap_dir" in src, "扫单未接六域探针方向"
    assert "analyst_signal:" in src, "探针原因未标 analyst_signal（不可复盘）"
    i_call = src.find("_ap_dir(sym, tier=tier_l)")
    i_skip = src.find('"probe_no_regime"')
    assert 0 < i_call < i_skip, "六域探针方向必须在 probe_no_regime 兜底之前"
    names = {n.func.attr if isinstance(n.func, ast.Attribute) else
             (n.func.id if isinstance(n.func, ast.Name) else "")
             for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)}
    assert "_ap_dir" in names, "没有真的调用（只是 import）"
