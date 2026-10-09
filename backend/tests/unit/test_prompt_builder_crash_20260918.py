# -*- coding: utf-8 -*-
"""[F371 2026-09-18] 实盘 prompt 构造器**在真实传参形状下必崩** —— 修复与回归锁。

## 事实（运行时实证，非静态推断）

`ai_decision_service.py:2279`：`active_symbol_metadata = symbol_metadata or SUPPORTED_SYMBOLS`
三个实盘调用方传的分别是：
  - `{}`（`trading_commands.py:840` 初始化 / `:1043` 传入）→ `{} or SUPPORTED_SYMBOLS`
  - `None`（`trading_commands.py:2683`、`full_auto/ai_decisions.py:234` 未传）
  - 不传（同上，默认 `None`）
而 `SUPPORTED_SYMBOLS = {"BTC": "Bitcoin", …}`（**值是字符串**）。
旧代码 `normalized_symbol_metadata.get(s, {}).get("name")` 对字符串调 `.get`
⇒ **AttributeError**，被 `call_ai_for_decision_with_fallback` 捕获后降级规则引擎。

⇒ 结论：**修好 P0（`prompt_context/__init__.py` 0 字节）只是拆掉第一道拦路**；
车道在第二道（本处）依然不通 —— 「策略主脑从不调用 LLM」在 P0 修复后**仍然成立**。
这也是我自己的第 19 次自我更正：当时只验证了 `IMPORT OK`（导入），没验证 `build()` 能跑通。

## 修法

`_display_name(meta_value, symbol)`：dict → 取 `name`；str → 直接当展示名；
其它/缺失 → 回退 `SUPPORTED_SYMBOLS`。**纯崩溃修复**（必然异常 → 原本意图），
不改变任何数值或决策口径。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.prompt_context.builder import (  # noqa: E402
    PromptContextBuilder, _display_name,
)
from backend.services.prompt_context import BuildInput  # noqa: E402


# ───────────────── ① 展示名解析必须容忍三种真实形状 ─────────────────

def test_display_name_accepts_str_values():
    """`SUPPORTED_SYMBOLS` 的形状：值是字符串（这正是崩溃触发点）。"""
    assert _display_name("Bitcoin", "BTC") == "Bitcoin"


def test_display_name_prefers_dict_name():
    assert _display_name({"name": "比特币"}, "BTC") == "比特币"
    # dict 但没有 name → 回退 SUPPORTED_SYMBOLS
    assert _display_name({}, "BTC") == "Bitcoin"
    assert _display_name({"name": ""}, "BTC") == "Bitcoin"


def test_display_name_falls_back_for_none_and_unknown():
    assert _display_name(None, "BTC") == "Bitcoin"
    assert _display_name(None, "NOSUCHSYM") == "NOSUCHSYM"
    assert _display_name(12345, "NOSUCHSYM") == "NOSUCHSYM"


# ───────────────── ② 真实传参形状下 build() 必须成功（决定性回归锁） ─────────────────

class _Acct:
    id = 1
    name = "probe"
    model = "m"
    leverage = 3
    initial_capital = 10000.0
    current_capital = 10000.0
    environment = "mainnet"


@pytest.mark.parametrize("kind", ["none", "empty", "symbols"])
def test_build_succeeds_with_real_caller_shapes(kind):
    """**决定性用例**：`{}` / `None` / `SUPPORTED_SYMBOLS` 三种实盘形状都不得抛异常。

    修复前这三种都会在 `builder.py:95` 抛 AttributeError（运行时实证）。
    """
    from backend.services.ai_decision_service import SUPPORTED_SYMBOLS
    meta = {"none": None, "empty": {}, "symbols": SUPPORTED_SYMBOLS}[kind]
    active = meta or SUPPORTED_SYMBOLS          # 复刻 :2279
    res = PromptContextBuilder().build(BuildInput(
        account=_Acct(), portfolio={}, prices={},
        symbol_metadata=active,
        symbol_order=list(active.keys()) if isinstance(active, dict) else None,
    ))
    assert isinstance(res, dict) and len(res) >= 30


def test_normalize_symbols_builds_display_map_for_str_values():
    """断言直接打在**被修的那一行**：展示名映射必须从 str 值里取到可读名。

    （用整条 `build()` 的产出断言"含 BTC"是错的——展示名落在 `inp` 上，
    不一定成为结果键；我第一版就是这么写错的。）
    """
    from backend.services.ai_decision_service import SUPPORTED_SYMBOLS
    inp = BuildInput(account=_Acct(), symbol_metadata=SUPPORTED_SYMBOLS,
                     symbol_order=list(SUPPORTED_SYMBOLS.keys()))
    PromptContextBuilder()._normalize_symbols(inp)
    assert inp.ordered_symbols, "ordered_symbols 不应为空"
    assert inp.symbol_display_map["BTC"] == "Bitcoin"
    assert inp.symbol_display_map["ETH"] == "Ethereum"
    # dict 形状（带 name）也要正确
    inp2 = BuildInput(account=_Acct(), symbol_metadata={"BTC": {"name": "比特币"}},
                      symbol_order=["BTC"])
    PromptContextBuilder()._normalize_symbols(inp2)
    assert inp2.symbol_display_map["BTC"] == "比特币"


def test_build_succeeds_with_correct_shape():
    res = PromptContextBuilder().build(BuildInput(
        account=_Acct(), portfolio={}, prices={},
        symbol_metadata={"BTC": {"name": "Bitcoin"}}, symbol_order=["BTC"]))
    assert isinstance(res, dict) and res


# ───────────────── ③ F370 的运行时确认（构造器成功也不提供这些键） ─────────────────

def test_builder_success_still_lacks_news_and_factor_guidance():
    """构造成功≠变量齐全：`news_section`（22/22 模板都在用）与 `factor_guidance` 均缺失。

    ⇒ 实盘 prompt 里这两处会被 `SafeDict.__missing__` 静默填成 "N/A"（F370）。
    若哪天这里失败，说明有人补上了它们 —— 请同步报告 §27/§28 与待办 B11。
    """
    from backend.services.ai_decision_service import SUPPORTED_SYMBOLS
    res = PromptContextBuilder().build(BuildInput(
        account=_Acct(), portfolio={}, prices={},
        symbol_metadata=SUPPORTED_SYMBOLS,
        symbol_order=list(SUPPORTED_SYMBOLS.keys())))
    for k in ("news_section", "factor_guidance", "recent_trades_summary",
              "selected_symbols_count", "decision_chain"):
        assert k not in res, f"{k} 已被提供 ⇒ F370 结论需更新（报告 §27）"
