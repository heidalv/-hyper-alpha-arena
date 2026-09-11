# -*- coding: utf-8 -*-
"""[§89 契约 2026-09-11] 置换检验**能红也能绿**（否则"证据不足"的结论不可信）。

`Z259_entry_profile.perm_p()` 用来判断"分组间 peak<2% 占比差"是否超出随机。
本轮它给出的结论是**否**（symbol p≈0.099、hour p≈0.825）⇒ 不能据此加筛选。
若检验本身写错（例如永远返回 1.0），"证据不足"就成了橡皮图章 —— 故双向验证。
"""
from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def z259():
    spec = importlib.util.spec_from_file_location("z259", ROOT / "_audit_ml/Z259_entry_profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _items(peaks):
    return [{"peak": p} for p in peaks]


def test_perm_detects_strong_separation(z259):
    """**能红**：一组全 never-worked、另一组全 worked ⇒ p 应很小。"""
    random.seed(7)
    a = _items([0.001] * 30)
    b = _items([0.05] * 30)
    p = z259.perm_p([a, b], iters=500)
    assert p < 0.05, f"强分离却没被检出（p={p}）"


def test_perm_is_silent_for_identical_groups(z259):
    """**能绿**：两组分布相同 ⇒ 不显著（p 大）。"""
    random.seed(11)
    same = [0.001, 0.05] * 15
    p = z259.perm_p([_items(list(same)), _items(list(same))], iters=500)
    assert p > 0.2, f"无差异却报显著（p={p}）"


def test_perm_ignores_tiny_groups(z259):
    """少于 3 笔的组不参与比较（避免单笔噪音造出"显著"）。"""
    random.seed(3)
    big1 = _items([0.001] * 20)
    big2 = _items([0.05] * 20)
    tiny = _items([0.001])
    p = z259.perm_p([big1, big2, tiny], iters=300)
    assert p < 0.05            # tiny 被忽略，剩下的强分离仍被检出
