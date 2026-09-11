# -*- coding: utf-8 -*-
"""M4 影子早毕业四象限单测。"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import backend.services.evolution.factor_evolution_loop as fe


def _mk(sharpe, days):
    return SimpleNamespace(
        paper_sharpe=sharpe, paper_days=days,
        icir=0.8, pbo=0.1, dsr_significant=True,
        factor_id="t",
    )


def _judge(to_state="SMALL_LIVE"):
    return SimpleNamespace(decision=SimpleNamespace(to_state=to_state))


def test_early_graduation_strong_sharpe_10d():
    # sharpe≥2.0 且 days≥10 → 早毕业
    m = _mk(sharpe=2.2, days=10)
    assert fe._auto_oversight_approve(m, _judge()) is True


def test_no_graduation_weak_sharpe_10d():
    # sharpe<2.0 且 days<20 → 不批
    m = _mk(sharpe=1.6, days=10)
    assert fe._auto_oversight_approve(m, _judge()) is False


def test_normal_path_strong_sharpe_20d():
    # sharpe≥1.5 且 days≥20 → 常规毕业
    m = _mk(sharpe=1.6, days=20)
    assert fe._auto_oversight_approve(m, _judge()) is True


def test_no_graduation_strong_sharpe_short_days():
    # sharpe 1.8（不足2.0）days=15（不足20）→ 不批
    m = _mk(sharpe=1.8, days=15)
    assert fe._auto_oversight_approve(m, _judge()) is False
