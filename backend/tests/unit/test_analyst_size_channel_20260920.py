# -*- coding: utf-8 -*-
"""[轮137 2026-09-20] 六分析师信号的**规模通道**：倾向只调规模，门槛永远看 LLM 原始置信度。

## 为什么不是"写回 conviction"（轮136 的血教训）
`llm_conviction` 同时是下游门槛的 confidence 输入（`[V5Gate] rule=confidence` ≥30%）。
把折减写回去 ⇒ 提案被**硬拦**（实测辩论 ×0.6 使 40→24，25~28% 撞门槛 20 次）。
所以：门槛看 LLM 原始置信度；分析师/辩论倾向走 `size_multiplier()`，作用于**规模**且有界 [0.80, 1.10]。

## 影子数据（转生效的依据）
60 条影子样本：38 条会变（63%），Δ 区间 [−2.36, +0.79]，平均 `analyst_score = −0.152`，
分域在场 macro 60 / flow 53 / technical 53 / sentiment 31 / fundamental 25 / **kline_deep 16**（轮134/135 后开始有值）。
⇒ 幅度温和，适合先上规模通道（而不是动门槛输入）。
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
    for k in ("ANALYST_BLEND_SIZE_ENABLED", "ANALYST_BLEND_SIZE_GAIN"):
        monkeypatch.delenv(k, raising=False)
    yield


def _fake_signals(score):
    return lambda *a, **k: [
        {"domain": "technical", "symbol": "BTC", "score": score, "confidence": 1.0,
         "data_quality": "ok", "n_samples": 1, "as_of": "", "evidence": {},
         "missing_sources": [], "reason": "", "ts": ""},
    ]


def test_multiplier_is_bounded_and_monotonic(monkeypatch):
    monkeypatch.setattr(SVC, "latest_signals", _fake_signals(1.0))
    hi, _ = SVC.size_multiplier("BTC")
    monkeypatch.setattr(SVC, "latest_signals", _fake_signals(-1.0))
    lo, _ = SVC.size_multiplier("BTC")
    monkeypatch.setattr(SVC, "latest_signals", _fake_signals(0.0))
    mid, _ = SVC.size_multiplier("BTC")
    assert hi == SVC.SIZE_MULT_MAX == 1.10, hi
    assert lo == SVC.SIZE_MULT_MIN == 0.80, lo
    assert mid == pytest.approx(1.0)
    assert lo < mid < hi, "单调性：分析师越看多，规模乘子不应更小"


def test_switch_off_returns_neutral(monkeypatch):
    monkeypatch.setenv("ANALYST_BLEND_SIZE_ENABLED", "false")
    monkeypatch.setattr(SVC, "latest_signals", _fake_signals(-1.0))
    m, note = SVC.size_multiplier("BTC")
    assert m == 1.0 and "停用" in note


def test_no_signals_returns_neutral(monkeypatch):
    monkeypatch.setattr(SVC, "latest_signals", lambda *a, **k: [])
    m, note = SVC.size_multiplier("BTC")
    assert m == 1.0 and "无可用域" in note, "没有信号时不得凭空缩仓"


def test_midlong_helpers_wires_size_channel_before_tranche_out():
    """接线 ratchet：规模通道必须在 `tranche_margin_pct` 下发**之前**并入 `_tranche_mult`。"""
    src = (ROOT / "backend/services/full_auto/midlong_helpers.py").read_text(
        encoding="utf-8", errors="replace")
    assert "size_multiplier as _analyst_size_mult" in src, "规模通道未接线"
    assert "[AnalystSize]" in src, "缺少可观测日志（乘子与理由）"
    tree = ast.parse(src)
    names = {n.func.attr if isinstance(n.func, ast.Attribute) else
             (n.func.id if isinstance(n.func, ast.Name) else "")
             for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "_analyst_size_mult" in names, "没有真的调用规模乘子"
    i_size = src.find("_analyst_size_mult(")
    i_out = src.find('"tranche_margin_pct": _tranche_mult')
    assert 0 < i_size < i_out, "规模通道必须在 tranche_margin_pct 下发之前生效"


def test_conviction_writeback_still_off_by_default(monkeypatch):
    """一致性：conviction 写回（ANALYST_BLEND_APPLY）默认必须仍是 false。"""
    monkeypatch.delenv("ANALYST_BLEND_APPLY", raising=False)
    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8", errors="replace")
    assert 'os.getenv("ANALYST_BLEND_APPLY", "false")' in src, \
        "混合打分又默认写回 conviction 了 —— 会与门槛冲突（轮136 教训）"
