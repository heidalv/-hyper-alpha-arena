# -*- coding: utf-8 -*-
"""[2026-09-14 F96] 自进化**覆盖性**契约：候选不得因字典序截断而永远得不到评估。

现场：网格里明明有 `k_vol: [0.0, 0.3]`，但 `candidate_grid(...)[:12]` 按字典序截断，
12 个名额被 w_base / max_one_side / k_inv / frozen_* 吃满 ⇒ **k_vol 与 frozen_lookback
从未被评估过**。而实测 k_vol=0.3 在**两个窗口**上都不劣、净 bp +49%（+0.366~0.430 vs
+0.247）。同时「全窗 ≥ 在位」的硬判据会被复利路径噪声否掉真实改进（±12%）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import evolution as evo  # noqa: E402


def _cur():
    return {"w_base_bp": 6.0, "max_one_side_seconds": 900.0, "k_inv": 1.0,
            "frozen_max_move_bp": 8.0, "ofi_block_threshold": 0.5,
            "frozen_width_bp": 3.0, "frozen_lookback": 240, "k_vol": 0.0}


def test_every_grid_dimension_is_covered():
    """完整网格必须为**每个** GRID 维度至少产出一个变体（不漏维度）。"""
    cands = evo.candidate_grid(_cur())
    for k in evo.GRID:
        vals = {c.get(k) for c in cands}
        assert len(vals) >= 2, f"维度 {k} 没有任何变体被生成（vals={vals}）"
    assert evo.full_grid_size(_cur()) == len(cands)


def test_candidate_cap_covers_full_grid():
    """候选上限必须 ≥ 完整网格规模，否则又会按序截断。"""
    n = evo.full_grid_size(_cur())
    assert evo.MAX_CANDIDATES >= n, f"上限 {evo.MAX_CANDIDATES} < 网格 {n}"


def test_k_vol_and_frozen_lookback_reachable():
    """K 线尾部维度（k_vol / frozen_lookback）必须在候选里可达。"""
    cands = evo.candidate_grid(_cur())
    assert any(c.get("k_vol") == 0.3 for c in cands), "k_vol=0.3 必须出现在候选中"
    assert any(c.get("frozen_lookback") == 120 for c in cands)


def test_rotate_changes_truncation_start():
    """轮换必须真的改变截断起点（跨轮次公平覆盖）。"""
    a = evo.candidate_grid(_cur(), rotate=0)[:12]
    b = evo.candidate_grid(_cur(), rotate=7)[:12]
    sa = {tuple(sorted((k, str(c.get(k))) for k in evo.GRID)) for c in a}
    sb = {tuple(sorted((k, str(c.get(k))) for k in evo.GRID)) for c in b}
    assert sa != sb, "rotate 没有改变候选集合"


def test_rotate_is_deterministic_per_day():
    """同一天同一 rotate 必须结果一致（可复现）。"""
    a = evo.candidate_grid(_cur(), rotate=123)
    b = evo.candidate_grid(_cur(), rotate=123)
    assert [c for c in a] == [c for c in b]


def test_full_tolerance_is_small_but_nonzero():
    """全窗容忍带必须存在（否则噪声否掉真实改进）但足够小（不放纵回退）。"""
    assert 0.0 < evo.FULL_TOL <= 0.05
