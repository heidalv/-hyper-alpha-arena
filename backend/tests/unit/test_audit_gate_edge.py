# -*- coding: utf-8 -*-
"""[2026-09-10 第二十二轮] learned 门样本外验证器的聚合/bootstrap 契约测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.scripts.audit_gate_edge import aggregate, bootstrap_diff  # noqa: E402


def _row(mon, tier, allow, usd, pct, pattern=False):
    return {"mon": mon, "tier": tier, "allow": allow, "usd": usd, "pct": pct,
            "big": 1.0 if pct <= -2 else 0.0, "pattern": pattern}


def test_aggregate_cells():
    rows = [
        _row("2026-07", "mid", True, +5.0, +1.0),
        _row("2026-07", "mid", True, -3.0, -3.0, pattern=True),
        _row("2026-07", "mid", False, -10.0, -5.0, pattern=True),
        _row("2026-08", "long", False, +2.0, +0.5),
    ]
    rep = aggregate(rows)
    cell = rep["2026-07|mid|allow"]
    assert cell["n"] == 2 and cell["usd"] == 2.0
    assert cell["win_rate"] == 0.5 and cell["big_loss_n"] == 1
    assert cell["pattern_n"] == 1 and cell["pattern_rate"] == 0.5
    blocked = rep["2026-07|mid|block"]
    assert blocked["n"] == 1 and blocked["pattern_rate"] == 1.0
    assert rep["2026-08|long|block"]["n"] == 1


def test_bootstrap_diff_significance():
    # 放行恒 +1、拦截恒 -1 → 差异显著为正
    a = [{"pct": 1.0} for _ in range(30)]
    b = [{"pct": -1.0} for _ in range(30)]
    d = bootstrap_diff(a, b, "pct", n_boot=500)
    assert d["significant"] is True
    assert d["obs"] == 2.0
    assert d["lo"] > 0
    # 两组同分布 → 不显著
    a2 = [{"pct": 0.5}, {"pct": -0.5}] * 20
    b2 = [{"pct": 0.5}, {"pct": -0.5}] * 20
    d2 = bootstrap_diff(a2, b2, "pct", n_boot=500)
    assert d2["significant"] is False
    assert abs(d2["obs"]) < 1e-9
    # 空样本 → None
    assert bootstrap_diff([], b, "pct") is None


def test_bootstrap_diff_deterministic_seed():
    a = [{"pct": float(i % 5)} for i in range(25)]
    b = [{"pct": float((i * 3) % 7)} for i in range(25)]
    d1 = bootstrap_diff(a, b, "pct", n_boot=300, seed=42)
    d2 = bootstrap_diff(a, b, "pct", n_boot=300, seed=42)
    assert d1 == d2
