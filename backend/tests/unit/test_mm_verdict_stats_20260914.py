# -*- coding: utf-8 -*-
"""[2026-09-14 F147] 「稳定正收益」判定的统计口径契约。

为什么必须换口径：实测每笔净 bp 的标准差 ≈ **9.7bp**、均值 ≈ **+0.19bp** ⇒
均值 t 检验要达到 |t|=2.5 需要 **~1.6 万笔（>80 小时）** ✗✗ —— 那不是"能不能赚钱"
的问题，而是**厚尾把均值检验的效力吃光了** ✗。
而"典型成交是否赚钱"用**符号检验**（正收益笔数占比）与**中位数 bootstrap CI**，
几百笔即可判定 ✓✓（厚尾只影响均值，不影响中位数/符号）。

契约：
  ① `sign_test` 的 z/p 与二项正态近似一致，且 n<20 时不做判定；
  ② `bootstrap_median_ci` 在明显正偏移的数据上给出不含 0 的区间、在对称数据上含 0；
  ③ `ttest_stats` 的 need_n 随 sd/mean 的平方增长（厚尾 ⇒ 需求爆炸，这正是要点）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "mm_paired_verdict", ROOT / "scripts" / "mm_paired_verdict.py")
mpv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mpv)


def test_sign_test_matches_binomial_normal_approx():
    vals = [1.0] * 61 + [-1.0] * 39          # 61% 为正，n=100
    st = mpv.sign_test(vals)
    assert st["n"] == 100 and st["pos"] == 61
    # z = (0.61-0.5)/(0.5/10) = 2.2
    assert abs(st["z"] - 2.2) < 1e-6
    assert st["p"] < 0.05, "61% 正收益在 n=100 时应显著"


def test_sign_test_requires_min_samples():
    st = mpv.sign_test([1.0] * 10)
    assert st["n"] == 10 and st["p"] == 1.0, "小样本不得给出显著性"


def test_bootstrap_median_ci_detects_positive_shift():
    vals = [5.0] * 120 + [-20.0] * 40         # 中位 +5，左尾厚
    ci = mpv.bootstrap_median_ci(vals, iters=400)
    assert ci is not None and ci[0] > 0, f"正偏移数据的中位 CI 应不含 0：{ci}"


def test_bootstrap_median_ci_contains_zero_for_symmetric():
    vals = [1.0, -1.0] * 100
    ci = mpv.bootstrap_median_ci(vals, iters=400)
    assert ci is not None and ci[0] <= 0 <= ci[1], f"对称数据应含 0：{ci}"


def test_ttest_needs_explode_with_thick_tails():
    """厚尾 ⇒ 均值检验所需样本量爆炸（这是换用符号检验的根本原因）。"""
    thin = [0.2] * 1000 + [-0.1] * 0                    # sd 小
    thick = [5.0] * 600 + [-10.0] * 400                 # 同样均值量级、sd 大
    a = mpv.ttest_stats(thin)
    b = mpv.ttest_stats(thick)
    assert b["sd"] > a["sd"]
    if a["need_n"] and b["need_n"]:
        assert b["need_n"] > a["need_n"] * 10, (
            f"厚尾应显著抬高所需样本量：thin={a['need_n']} thick={b['need_n']}")
