# -*- coding: utf-8 -*-
"""[P6 大轮回 2026-09-27] 摸底开窗冻结快照契约测试。"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from p6_freeze_snapshot import (  # noqa: E402
    declaration,
    frozen_params,
    params_sha,
)


def test_frozen_params_sorted_and_prefixed():
    params = frozen_params()
    keys = list(params.keys())
    assert keys == sorted(keys), "参数清单必须稳定排序"
    assert any(k.startswith("MIDLONG_") for k in keys)
    assert any(k.startswith("P1_") for k in keys), "一次定价参数必须在冻结清单"
    assert "MIDLONG_OPEN_SHORT_ENABLED" in params
    assert params["MIDLONG_OPEN_SHORT_ENABLED"] == "true", "空头解锁必须冻结在开窗状态"
    assert "MIDLONG_PULLBACK_LIMIT_ENABLED" in params, "回踩入场开关必须冻结"


def test_sha_stable_for_same_input():
    p1 = frozen_params()
    assert params_sha(p1) == params_sha(frozen_params()), "同输入必须同 SHA"


def test_sha_sensitive_to_change():
    p1 = dict(frozen_params())
    p2 = dict(p1)
    k = next(iter(p2))
    p2[k] = p2[k] + "_changed"
    assert params_sha(p1) != params_sha(p2)


def test_declaration_contains_acceptance():
    dec = declaration({"K": "1"}, "abcd1234")
    acc = dec["acceptance"]
    assert acc["window_days"] if "window_days" in acc else True
    assert "pass_line" in acc
    assert "anti_cheat" in acc
    assert dec["discipline"]["no_param_change"] is True
    assert dec["window_days"] == 30
    assert dec["params_sha"] == "abcd1234"
