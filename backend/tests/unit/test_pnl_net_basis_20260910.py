# -*- coding: utf-8 -*-
"""[P3 / §70 执行 2026-09-10] PnL 净口径契约测试。

口径（`backend/services/mlto/pnl_basis.py` 是唯一真相源）：
    net = gross − fees − funding

锁定三件事：
  1. 算术与字段契约（`describe()` 里 `pnl == pnl_net`，且带 `pnl_basis` 标签）；
  2. 中长线健康视图必须**同时**给出毛/费/净，且 `pnl_net == gross − fees`（恒等式在真实数据上成立）；
  3. 费用**非零**（防"净口径"退化成毛口径——那正是 §43.2 的原始缺陷）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto import pnl_basis as pb  # noqa: E402


def test_net_arithmetic_and_none_handling():
    assert pb.net_pnl(100.0, 3.5) == pytest.approx(96.5)
    assert pb.net_pnl(-100.0, 3.5) == pytest.approx(-103.5)
    assert pb.net_pnl(100.0, 3.5, 1.5) == pytest.approx(95.0)
    assert pb.net_pnl(100.0, None, None) == pytest.approx(100.0)
    assert pb.net_pnl(None) == pytest.approx(0.0)


def test_describe_contract():
    d = pb.describe(-120.97, 9.57, 0.0)
    assert d["pnl"] == d["pnl_net"] == pytest.approx(-130.54, abs=1e-6)
    assert d["pnl_gross"] == pytest.approx(-120.97)
    assert d["fees"] == pytest.approx(9.57)
    assert d["pnl_basis"] == "net"
    # cost_ratio：费用/|毛|（毛为 0 时给 None，而不是 inf）
    assert d["cost_ratio"] == pytest.approx(9.57 / 120.97, abs=1e-3)
    assert pb.describe(0.0, 1.0)["cost_ratio"] is None
    assert "basis=net" in pb.label()


def test_health_view_exposes_net_and_identity_holds():
    from backend.services.full_auto.midlong_helpers import build_midlong_health_from_facts

    h = build_midlong_health_from_facts(lookback_days=14)
    if h.get("error"):
        pytest.skip(f"DB 不可用：{h['error'][:120]}")
    assert h["pnl_basis"] == "net", "健康视图未标注口径"
    t = h["totals"]
    for k in ("pnl", "pnl_gross", "pnl_net", "fees"):
        assert k in t, f"totals 缺字段 {k}"
    # 恒等式：净 = 毛 − 费（浮点容差 1e-3，因为两侧都做了 round(4)）
    assert t["pnl_net"] == pytest.approx(t["pnl_gross"] - t["fees"], abs=1e-3), t
    assert t["pnl"] == pytest.approx(t["pnl_net"], abs=1e-6), "兼容字段 pnl 必须等于净口径"
    for tier, row in (h.get("tiers") or {}).items():
        assert row["pnl_net"] == pytest.approx(row["pnl_gross"] - row["fees"], abs=1e-3), (tier, row)
        assert row["pnl"] == pytest.approx(row["pnl_net"], abs=1e-6), tier


def test_fee_drag_is_actually_nonzero():
    """防回归：净口径不得退化成毛口径（费用恒为 0 就说明口径没接上）。"""
    from backend.services.full_auto.midlong_helpers import build_midlong_health_from_facts

    h = build_midlong_health_from_facts(lookback_days=14)
    if h.get("error"):
        pytest.skip(f"DB 不可用：{h['error'][:120]}")
    assert h["totals"]["fees"] > 0, "14 天窗口内手续费为 0 —— 净口径未接上真实费用"
    assert h["totals"]["pnl_net"] < h["totals"]["pnl_gross"], "净口径应严格低于毛口径（费用为正）"
