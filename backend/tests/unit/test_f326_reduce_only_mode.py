# -*- coding: utf-8 -*-
"""[F326 2026-09-17] `side_mode="reduce_only_when_inv"`（路径 D）契约测试。

背景：本轮为修实盘亏损测试了五条路径，路径 D 是最后一条**代码级**假设：
  「库存存在时**停止加仓侧报价**，只留减仓侧（6bp 地板）被动流出」
理由是 F324/F325 显示"削弱 taker 平仓会毁掉 maker 腿"，我推测症结在于
**持有库存时仍在加仓** ⇒ 库存滚大 ⇒ 报价被抑制。

**结果：假设不成立**（见 `analyze/f326_path_d.py`）——
封掉加仓侧后 flatten 反而从 19 增到 27，net$ 更差（−17.57 vs −16.77）。
本文件锁定该模式的**行为正确性**，而不是它的经济性（经济性已被实验否决）。

为什么仍要保留这个模式与测试：
  · 它验证了一条**被明确否决**的假设，避免将来有人再提出同一个想法；
  · 模式是**纯新增、默认不启用**（`side_mode` 仍是 `counter_trend`）；
  · 行为契约（只封加仓侧、绝不封减仓侧）必须锁死 —— 否则将来有人误用它，
    会重演 F228 的事故（减仓侧被关死 ⇒ 库存只能等超时砸单）。

测试写法说明：本文件**不**用"从某个子串起截 N 字符"的窗口断言 ——
第一版就是这么写的，而窗口起点落在注释里 ⇒ 够不到代码 ⇒ 假失败（3 条）。
现在改为**按行定位可执行语句**再检查。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as R  # noqa: E402


def _mode_block() -> str:
    """取出模式的**可执行代码块**（从 `if _side_mode == "...":` 那一行开始，
    到下一个同级语句为止），不含上方注释。

    为什么不按子串截窗口：第一版从 `"reduce_only_when_inv"`（注释里先出现）
    起截 700 字符 ⇒ 截到的是注释 ⇒ 断言假失败。
    """
    src = inspect.getsource(R.plan_tick)
    lines = src.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith('if _side_mode == "reduce_only_when_inv"'):
            start = i
            break
    assert start is not None, "未找到 reduce_only_when_inv 的可执行分支"
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = [lines[start]]
    for ln in lines[start + 1:]:
        if not ln.strip():
            body.append(ln)
            continue
        ind = len(ln) - len(ln.lstrip())
        if ind <= indent:
            break
        body.append(ln)
    return "\n".join(body)


def test_mode_is_additive_and_not_default():
    """新模式必须是**纯新增**：不得改动现有默认值。"""
    from backend.services.market_maker.core import QuoteParams

    assert QuoteParams().side_mode == "both", (
        "QuoteParams 的默认 side_mode 必须保持 both（不得因本改动而变）")
    assert _mode_block(), "新模式分支必须存在于 plan_tick"


def test_reduce_only_blocks_only_the_adding_side():
    """契约：多头 ⇒ 封买（加仓侧）；空头 ⇒ 封卖（加仓侧）。**减仓侧永不受限。**

    判定必须**逐行**做：块里同时存在 `allow_buy` 与 `allow_sell`
    （一个在 if、一个在 elif），按"整块包含"判定会把两个兄弟分支混在一起
    ⇒ 误报（本文件第二版就是这个错）。
    """
    block = _mode_block()
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    conds = [ln for ln in lines if ln.startswith(("if ", "elif "))]
    assert conds, "未取到条件行"

    def has(pos: str, side: str) -> bool:
        """存在某一行同时含 (持仓方向, 报价侧)。"""
        return any((pos in ln) and (f"and {side}") in ln for ln in conds)

    assert has("_pos_d > 1e-12", "allow_buy"), "多头必须封**买**（加仓侧）"
    assert has("_pos_d < -1e-12", "allow_sell"), "空头必须封**卖**（加仓侧）"
    assert not has("_pos_d > 1e-12", "allow_sell"), (
        "**禁止**在多头时封卖 —— 那是减仓侧，会重演 F228 事故")
    assert not has("_pos_d < -1e-12", "allow_buy"), (
        "**禁止**在空头时封买 —— 那是减仓侧")


def test_skip_reason_is_distinct():
    """必须给出可区分的 skip 原因（便于巡检分辨是闸还是模式所致）。"""
    block = _mode_block()
    assert "reduce_only_inv" in block, "skip 原因必须可区分"
    for other in ("vol_pause", "trend_up", "trend_down", "ofi_toxic", "ct_trend"):
        assert other not in block, "不得借用其它闸的名字: %s" % other


def test_other_modes_unaffected():
    """counter_trend 的 F228 豁免必须仍在；不得为 both 新增封禁逻辑。"""
    src = inspect.getsource(R.plan_tick)
    assert "ct_trend_up" in src and "ct_trend_down" in src, (
        "counter_trend 的 F228 减仓侧豁免必须保留")
    # 不给 both 模式新增封侧分支（只允许既有的 skip_side = "both" 这种无关字符串）
    for bad in ('if _side_mode == "both"', "if _side_mode == 'both'"):
        assert bad not in src, "不应为 both 模式新增封禁逻辑"


def test_evidence_and_rollback_documented_in_source():
    """存在理由与被否决的结论必须写在源码注释里。

    防止"只留代码不留结论" —— 后来者会重复同一个假设。
    """
    src = inspect.getsource(R.plan_tick)
    i = src.index("F326")
    block = src[max(0, i - 300):i + 2600]
    assert "F324" in block and "F325" in block, "必须引用证据来源"
    assert "回滚" in block, "必须写明回滚方式"
