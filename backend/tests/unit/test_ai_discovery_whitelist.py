# -*- coding: utf-8 -*-
"""AI 因子挖掘 AST 白名单语义回归（2026-08-25）。

背景：挖掘 LLM 生成的代码调用 sl_loss/timeout_loss/drawdown_loss 等未定义函数，
被 ast_whitelist_check 全部拒绝（当日 3/3 候选作废）。修复为提示词硬约束 +
本测试锁定白名单语义：提示词示例必须过闸，未定义函数调用必须被拒。
"""
from backend.services.factor_engine.code_safety import ast_whitelist_check


def test_prompt_example_passes_whitelist():
    """提示词中给出的正确示例必须能通过 AST 白名单（否则提示词在教 LLM 写废代码）。"""
    code = (
        "def calculate(self, data):\n"
        "    ret = data['close'].pct_change(20)\n"
        "    vol = data['close'].pct_change().rolling(20).std()\n"
        "    result = (ret / (vol + 1e-9)).clip(-1, 1)\n"
        "    return result"
    )
    ok, reason = ast_whitelist_check(code)
    assert ok, "提示词示例应通过白名单: %s" % reason


def test_undefined_helper_call_rejected():
    """调用未定义辅助函数（sl_loss 等）必须被拒——运行时必然 NameError。"""
    code = "def calculate(self, data):\n    return data['close'].pct_change() - sl_loss(data)"
    ok, reason = ast_whitelist_check(code)
    assert not ok
    assert "sl_loss" in reason


def test_import_rejected():
    ok, _ = ast_whitelist_check("import os\ndef f(x):\n    return x")
    assert not ok


def test_dunder_attribute_rejected():
    ok, _ = ast_whitelist_check("def f(x):\n    return x.__class__")
    assert not ok
