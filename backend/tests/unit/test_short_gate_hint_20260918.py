# -*- coding: utf-8 -*-
"""[2026-09-18 根因修复·方向语义] `brain._short_gate_hint()` 的**行为**契约。

为什么要有这个文件：这段文案原先内联在 prompt 拼装里，只能做**源码文本断言**——
而"文本存在" ≠ "条件在真实分支下成立"。我第一版就把条件写成
`if _reg_hint and _reg_hint != "down"`，**漏掉了 `regime 未知（空）` 这一档**；
而闸门对未知态是 `fail-closed`（`midlong_short_regime_unknown`）⇒ 那一档同样
会产生"必然被拒的空头"，提示却不会注入（实测 SUI 因 1d K 线陈旧 102h ⇒ regime 为空，
它的 short 提案正落在这个漏洞里）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import backend.services.full_auto.midlong_circuit_gate as CG  # noqa: E402
from backend.services.mlto import brain as B  # noqa: E402


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(CG, "_short_mode", lambda: "regime_gated")
    return monkeypatch


def _hint(monkeypatch, reg: str, *, tier: str = "mid", sym: str = "ASTER") -> str:
    monkeypatch.setattr(CG, "_daily_regime", lambda s, _r=reg: _r)
    return B._short_gate_hint(sym, tier)


def test_non_down_regimes_inject_hard_constraint(gate):
    for reg in ("up", "chop", "ranging"):
        txt = _hint(gate, reg)
        assert txt, f"regime={reg} 时空头必被拒，必须注入"
        assert "必须写 neutral" in txt
        assert "可写 bearish" not in txt, "不得再出现旧的许可文案"
        assert reg in txt


def test_unknown_regime_also_injects(gate):
    """**本文件存在的理由**：regime 不可判时闸门 fail-closed，提示也必须注入。"""
    txt = _hint(gate, "")
    assert txt, "regime 未知时空头同样会被 midlong_short_regime_unknown 拦，必须注入"
    assert "必须写 neutral" in txt
    assert "不可判" in txt or "数据不足" in txt


def test_down_regime_does_not_inject(gate):
    assert _hint(gate, "down") == "", "down 时空头合法 ⇒ 不应注入限制"


def test_short_tier_is_exempt(gate):
    """日内波段(short)走自己的路径，不受此闸 ⇒ 不注入。"""
    assert _hint(gate, "up", tier="short") == ""


def test_other_short_modes_do_not_inject(gate):
    gate.setattr(CG, "_short_mode", lambda: "off")
    assert _hint(gate, "up") == ""
    gate.setattr(CG, "_short_mode", lambda: "conditional")
    assert _hint(gate, "up") == ""


def test_exception_is_fail_safe(monkeypatch):
    """判定异常时返回空串（不改行为、不炸主链路）。"""
    def _boom(_s):
        raise RuntimeError("boom")
    monkeypatch.setattr(CG, "_daily_regime", _boom)
    monkeypatch.setattr(CG, "_short_mode", lambda: "regime_gated")
    assert B._short_gate_hint("ASTER", "mid") == ""


def test_prompt_uses_the_helper():
    """prompt 拼装处必须真的调用它（否则函数再对也不生效）。"""
    src = (ROOT / "backend" / "services" / "mlto" / "brain.py").read_text(encoding="utf-8")
    assert '_hint_txt = _short_gate_hint(symbol, tier)' in src
    assert 'user += "\\n\\n" + _hint_txt' in src
