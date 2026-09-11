# -*- coding: utf-8 -*-
"""[2026-09-10 §56] 实盘「每日开单配额」只能在**开仓**路径扣减。

背景（§56.1 实证）：`_bump_live_open_quota()` 把 per-session 按日的 used +1，
而 `live_enforce_daily_open_cap()` 在**开仓前**用它拦单。此前它被挂在 6 个
**平仓/减仓**路径上（ai_take_profit / ai_cut_loss / close_to_sl / close_tiny /
`master_*_reduce` / unified_exit 执行）⇒ 语义退化成"每日**成交**配额"：
**关得越多、当天越不能开**。更要命的是它只在 live 生效 ⇒ paper 永远看不到这个问题。

本测试用 AST 定位每个 `_bump_live_open_quota()` 调用点，按**最近的动作句子**判定
它属于开仓还是平仓；平仓侧出现调用即失败。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "backend/services/full_auto/master_execution.py"
CLOSE_RX = re.compile(
    r"(close_position|partial_close|_close_tiny|_reduce|reduce_qty|unified_exit_executor\.execute|"
    r"ai_take_profit|ai_cut_loss|close_to_sl)", re.I)
OPEN_RX = re.compile(r"(execute_paper_trade|place_order|pyramid|dca|open)", re.I)


def _call_lines() -> list:
    lines = SRC.read_text(encoding="utf-8").splitlines()
    return [i for i, ln in enumerate(lines, 1)
            if "_bump_live_open_quota()" in ln and "def " not in ln]


def test_bump_is_not_called_on_close_or_reduce_paths():
    src = SRC.read_text(encoding="utf-8")
    lines = src.splitlines()
    offenders = []
    for ln in _call_lines():
        i = ln - 1
        seg = "\n".join(lines[max(0, i - 12): i + 3])
        close_hits = len(CLOSE_RX.findall(seg))
        open_hits = len(OPEN_RX.findall(seg))
        if close_hits and close_hits >= open_hits:
            offenders.append(f"master_execution.py:{ln} (close词={close_hits} open词={open_hits})")
    assert not offenders, (
        "平仓/减仓路径仍在扣减每日**开单**配额（会让『平得越多、开得越少』）: "
        + "; ".join(offenders)
    )


def test_bump_still_called_on_open_paths():
    """反向保护：别把开仓侧的扣减一并删掉（否则配额永不增长＝闸形同不存在）。"""
    src = SRC.read_text(encoding="utf-8")
    lines = src.splitlines()
    opens = 0
    for ln in _call_lines():
        i = ln - 1
        seg = "\n".join(lines[max(0, i - 12): i + 3])
        if OPEN_RX.search(seg) and not CLOSE_RX.search(seg):
            opens += 1
    assert opens >= 4, f"开仓侧扣减点过少（{opens}）——配额闸可能被误删"


def test_docstring_documents_open_only_semantics():
    src = SRC.read_text(encoding="utf-8")
    m = re.search(r"def _bump_live_open_quota\(\):(.{0,700})", src, re.S)
    assert m, "找不到 _bump_live_open_quota 定义"
    body = m.group(1)
    assert "只允许在开仓路径调用" in body, "缺少『只在开仓路径调用』的口径说明（防止后人再挂到平仓侧）"


def test_close_outcome_never_fabricates_profit_when_pnl_missing():
    """§56.2：实盘平仓不回传 pnl ⇒ 必须标未知，绝不能默认 0 当成『止盈』。"""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_close_outcome":
            fn = node
            break
    assert fn is not None, "缺少 _close_outcome 辅助函数"
    # 直接执行它的代码（闭包内定义，故注入到命名空间执行）
    mod = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns: dict = {}
    exec(compile(mod, "<close_outcome>", "exec"), ns)  # noqa: S102
    out = ns["_close_outcome"]  # type: ignore[index]
    assert out(None)[0] == "ai_close_pnl_unknown"
    assert out(0)[0] == "ai_take_profit"
    assert out(-3.5)[0] == "ai_cut_loss"
    assert "+3.50" in out(3.5)[3] and "-3.50" in out(-3.5)[3]
    assert "?" in out(None)[3]
