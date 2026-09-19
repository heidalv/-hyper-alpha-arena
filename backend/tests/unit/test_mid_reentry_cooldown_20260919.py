# -*- coding: utf-8 -*-
"""轮111 中线同币冷却回归测试（2026-09-19）。

## 依据（同一口径：opened_at 近 30 天、已平仓、entry/close 有效 = **158 笔**）

    全样本                均值 −0.119%   胜率 0.468
    0–2h 重开档  n=40     均值 **−0.389%**  胜率 **0.375**   ← 显著最差
    2–6h         n=29     −0.237%   0.517
    6–12h        n=22     +0.051%   0.455
    12–24h       n=13     −0.126%   0.462
    24h+         n=44     −0.080%   0.455
    首次(无前次)  n=10     **+0.774%**  胜率 **0.800**   ← 最好
    去掉 0–2h 档后 n=118  均值 **−0.027%**  胜率 0.500

⇒ ≈30 天避免亏损 $62.8 + 省手续费 $23.8 ≈ **+$87/30 天**，并少 40 笔换手。
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.full_auto.midlong_executor import reentry_cooldown_verdict  # noqa: E402

_NOW = datetime(2026, 9, 19, 12, 0, 0)


# ══════════════════════════════════════════════════════════════════════
# ① 纯判定：四类边界
# ══════════════════════════════════════════════════════════════════════

def test_blocks_inside_the_worst_bucket():
    """0–2h 那档是实测最差（胜率 0.351）→ 必须拦住。"""
    for hours in (0.0, 0.5, 1.0, 1.99):
        ok, why = reentry_cooldown_verdict(_NOW - timedelta(hours=hours), _NOW, 7200,
                                          symbol="XRP", tier="mid")
        assert ok is False, (hours, ok, why)
        assert "距上次平仓" in why


def test_allows_after_cooldown():
    for hours in (2.0, 2.5, 6.0, 48.0):
        ok, why = reentry_cooldown_verdict(_NOW - timedelta(hours=hours), _NOW, 7200)
        assert ok is True, (hours, ok, why)


def test_allows_when_no_prior_exit():
    """首次开仓是实测**最好**的一档（+0.774%/胜率 0.800）→ 绝不能拦。"""
    ok, why = reentry_cooldown_verdict(None, _NOW, 7200)
    assert ok is True and why == "no_prior_exit"


def test_zero_cooldown_is_off():
    ok, why = reentry_cooldown_verdict(_NOW, _NOW, 0)
    assert ok is True and why == "cooldown_off"


def test_fail_open_on_bad_timestamp():
    ok, _ = reentry_cooldown_verdict("not-a-time", _NOW, 7200)
    assert ok is True, "时间戳不可比时不得拦单（冷却不是风控闸）"


def test_clock_skew_fail_open():
    ok, why = reentry_cooldown_verdict(_NOW + timedelta(hours=1), _NOW, 7200)
    assert ok is True and why == "clock_skew"


# ══════════════════════════════════════════════════════════════════════
# ② 接线：只在 tier=mid 生效、在 writer 里、fail-open
# ══════════════════════════════════════════════════════════════════════

def _src() -> str:
    return open(os.path.join(_ROOT, "backend/services/full_auto/midlong_executor.py"),
                encoding="utf-8").read()


def test_gate_is_wired_only_for_mid_tier():
    src = _src()
    i = src.index("中线同币**冷却**")
    block = src[i: i + 2000]
    assert 'if str(tier or "").lower() == "mid":' in block
    assert "mid_reentry_wait_ok(db, sym_u, tier=\"mid\"" in block
    assert 'reason=reentry_cooldown' in block


def test_gate_fails_open_on_query_error():
    src = _src()
    i = src.index("中线同币**冷却**")
    block = src[i: i + 2000]
    assert "_wait_ok, _wait_why = True, \"\"" in block, "查询异常必须放行"


def test_default_cooldown_is_two_hours():
    from backend.config.settings import MIDLONG_MID_REENTRY_COOLDOWN_SEC as cd
    assert cd == pytest.approx(7200.0)


def test_registry_knows_the_switch():
    src = open(os.path.join(_ROOT, "backend/config/env_registry.py"), encoding="utf-8").read()
    assert "MIDLONG_MID_REENTRY_COOLDOWN_SEC" in src


# ══════════════════════════════════════════════════════════════════════
# ③ 算式自证
# ══════════════════════════════════════════════════════════════════════

def test_arithmetic_selfcheck():
    """算式自证：与 reports 同一口径（158 笔，去掉 0–2h 档）。"""
    n_all, mean_all = 158, -0.119
    n_cold, mean_cold = 40, -0.389
    n_rest = n_all - n_cold
    mean_rest = (n_all * mean_all - n_cold * mean_cold) / n_rest
    assert n_rest == 118
    assert mean_rest == pytest.approx(-0.027, abs=0.002), mean_rest
    assert mean_rest > mean_all
    # 经济量级：少亏 + 省手续费
    ntl = 433.3
    saved = n_cold * (mean_rest - mean_cold) / 100.0 * ntl
    assert saved == pytest.approx(62.8, abs=1.0), saved
    assert saved + n_cold * 0.594 == pytest.approx(86.6, abs=1.5)
