# -*- coding: utf-8 -*-
"""轮108 缩仓链地板回归测试（2026-09-19）。

## 背景（线上实测 13:00）

    [V5Gate] DOWNSIZE symbol=BNB tier=mid size×0.25
    [TrancheGate] DOWNSIZE symbol=BNB tier=mid stage→size×0.00
    [TrancheGate] DOWNSIZE symbol=UNI tier=mid stage→size×0.01
    [MidLongBrain] 开仓扫描 tier=mid 候选=3 成交=0      ← 连续数小时

多层独立缩仓（V5 / 层预算 / MTF / tranche）**相乘**后名义只剩 0.25%~1%，
既开不出有意义的仓，也不留可查原因（live 侧有 `[LiveDust]` 地板，paper 侧没有）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "backend/services/full_auto/proposal_execution.py",
)


def _src() -> str:
    return open(_SRC, encoding="utf-8").read()


def test_size_floor_present_after_all_multipliers():
    src = _src()
    i_tranche = src.index("[TrancheGate] DOWNSIZE")
    i_floor = src.index("[SizeFloor] BLOCK")
    i_paper = src.index("execute_paper_trade(db, session, strat, dec)")
    assert i_tranche < i_floor < i_paper, "地板必须落在所有缩仓乘子之后、下单之前"


def test_size_floor_reads_setting_and_is_rollbackable():
    src = _src()
    assert "MIDLONG_MIN_SIZE_MULT" in src
    assert "_min_sm > 0 and _size_mult_now < _min_sm" in src, "0 = 关闭地板（回滚语义）"


def test_size_floor_marks_block_for_funnel_audit():
    src = _src()
    assert '_mark_block("size_below_floor"' in src
    assert 'host.append_event(\n            session, "size_below_floor"' in src or \
           '"size_below_floor",' in src


def test_setting_default_is_five_percent():
    from backend.config.settings import MIDLONG_MIN_SIZE_MULT as m
    assert m == pytest.approx(0.05)


def test_floor_does_not_block_normal_sizes():
    """正常缩仓（0.30 / 0.25 / 0.12）不得被地板拦下。"""
    from backend.config.settings import MIDLONG_MIN_SIZE_MULT as m
    for normal in (0.30, 0.25, 0.2, 0.12, 0.10):
        assert normal >= m, normal
    # 线上实测的病态值必须被拦
    for patho in (0.004, 0.01):
        assert patho < m, patho


def test_ast_dedup_log_is_not_info_level():
    """轮108：去重日志曾是 INFO，每币每周期 3 条 → 降为 DEBUG。"""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
        "backend/services/factor_engine/midlong_active_factor_set.py")
    src = open(p, encoding="utf-8").read()
    i = src.index("AST 同族去重")
    window = src[max(0, i - 200): i + 200]
    assert "logger.debug(" in window, window
    assert "logger.info(" not in window, "去重日志不得回到 INFO（会按币×周期刷屏）"
