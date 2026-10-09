# -*- coding: utf-8 -*-
"""[F257 2026-09-20] 成交判定依据的落盘契约测试。

## 背景（H28 现场发现）

在流动性充足的币上（ASTER/SOL/DOGE/XRP，279 笔引擎腿）：

    对得上      145 笔（52.2%）—— 真实逐笔里价格吻合（±2bp / ±20s）
    价格偏差    133 笔（47.7%）—— **记录的成交价在真实市场里找不到对应**
                                  偏差中位 5.26bp、p75 9.6bp、max 28.6bp

机制：`core.fill_side` 用 **15 秒桶内的最低价**判成交
（`seg_low < quote_bid` 且桶内有主动卖量），**成交价却记作我们的挂单价**。
⇒「桶内极值顺带穿过挂单价」与「真有成交发生在我们的价位上」被混为一谈。

## 为什么需要落盘（而不是只加内存字段）

`fill_notes` 是**内存环**（最近 60 条，重启即丢）。验证这个问题需要**历史**：
把当时的桶极值与我们的挂单价留下来，事后用真实逐笔重放判定
"这笔成交在当时存在吗"。本项目此前**不存任何历史报价流** ⇒
这个问题在账本里**永远看不出来**。

## 本文件锁什么

  1. `PlannedFill` 必须带 `seg_low` / `seg_high` / `px_exact_hit` 三字段；
  2. `_record_fills` 必须调用落盘函数，且落盘**不得抛异常**（不能影响交易链路）；
  3. `px_exact_hit` 在引擎侧**必须允许为 None** —— 我们只有桶级数据，
     拿不到桶内逐笔 ⇒ 写 True/False 都是编造。这是诚实性契约。
  4. 落盘路径必须在 `logs/` 下且文件名固定（离线分析器据此读取）。

**不锁**：成交模型什么时候改成队列消耗制（那是另一个改动）。
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as R  # noqa: E402


def _code_lines(path: Path) -> str:
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        if "#" in ln:
            ln = ln.split("#", 1)[0]
        out.append(ln)
    return "\n".join(out)


# ── 1. PlannedFill 必须带判定依据字段 ────────────────────────────

def test_planned_fill_has_basis_fields():
    import dataclasses
    names = {f.name for f in dataclasses.fields(R.PlannedFill)}
    for k in ("seg_low", "seg_high", "px_exact_hit"):
        assert k in names, (
            f"`PlannedFill` 缺少 `{k}` ⇒ 判定依据无法落盘，"
            "事后无法验证成交价在真实市场里是否存在（H28 的 47.7%）。"
        )


def test_px_exact_hit_defaults_none():
    """默认必须是 None（未知），不能是 False。

    我们只有桶级数据（low/high/taker_buy/taker_sell），拿不到桶内逐笔
    ⇒ 引擎**无法**判断"桶内是否真有成交落在我们价位上"。
    默认 False 会被误读成"已确认没有"，那是把未知伪装成结论。
    """
    f = R.PlannedFill(symbol="X", side="buy", qty=1.0, px=1.0, mid=1.0, ts=0.0)
    assert f.px_exact_hit is None, "`px_exact_hit` 默认必须是 None（未知），不能是 False"


# ── 2. 落盘函数存在、可调用、不抛 ───────────────────────────────

def test_persist_helper_exists():
    assert hasattr(R.ShadowRunner, "_persist_fill_basis"), (
        "缺少 `_persist_fill_basis` ⇒ 判定依据不会落盘。"
    )
    src = inspect.getsource(R.ShadowRunner._persist_fill_basis)
    assert "mm_fill_basis.jsonl" in src, "落盘文件名必须是 mm_fill_basis.jsonl（离线分析器据此读取）"
    assert "logs" in src, "落盘必须在 logs/ 目录下"


def test_persist_is_exception_safe():
    """落盘失败绝不能抛给交易链路（与 lane_ledger.record_fill 同一原则）。"""
    import textwrap

    src = inspect.getsource(R.ShadowRunner._persist_fill_basis)
    # ⚠️ `inspect.getsource` 返回的是**带方法缩进**的源码，
    #    直接 `ast.parse` 会 `IndentationError: unexpected indent`。
    #    必须先 dedent（实测踩到）。
    tree = ast.parse(textwrap.dedent(src))
    # 必须整体被 try 包住
    assert any(isinstance(n, ast.Try) for n in ast.walk(tree)), (
        "`_persist_fill_basis` 必须整体 try/except —— 落盘失败不得影响交易。"
    )
    assert "except Exception" in src, "必须捕获 Exception（IO 错误形态多）"


def test_record_fills_calls_persist():
    src = _code_lines(Path(R.__file__))
    assert "_persist_fill_basis" in src, (
        "`_record_fills` 没有调用落盘函数 ⇒ 字段加了但不会写出去（静默死字段）。"
    )


# ── 3. 池化：不得把未知写成结论 ─────────────────────────────────

def test_no_fabricated_px_exact_hit_in_engine():
    """引擎里不得出现凭空构造 `px_exact_hit=True/False` 的写法。"""
    src = _code_lines(Path(R.__file__))
    for bad in ("px_exact_hit=True", "px_exact_hit=False"):
        assert bad not in src, (
            f"引擎里出现了 `{bad}`。我们只有桶级数据 ⇒ 无法判定，"
            "必须写 None，否则是把猜测伪装成观测。"
        )


# ── 4. 落盘记录必须包含验证所需的最小字段集 ──────────────────────

def test_persisted_record_has_required_keys():
    src = inspect.getsource(R.ShadowRunner._persist_fill_basis)
    for k in ("ts", "symbol", "side", "qty", "fill_px", "engine_mid",
              "seg_low", "seg_high", "flatten", "position_id", "px_exact_hit"):
        assert f'"{k}"' in src, f"落盘记录缺少字段 `{k}`（离线验证需要它）"
