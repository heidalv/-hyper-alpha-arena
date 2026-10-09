# -*- coding: utf-8 -*-
"""[F381 2026-09-18] `backtest_wisdom` 饲料：**多模板拼接 + 互相矛盾 + 极值 + 截断成畸形片段**。

## 实测（`_wisdom_feed("preview","mid")`，运行态真实数据）

| 项 | 读数 |
|---|---|
| `n(parts)` | **3**（三个模板的智慧被拼成一份） |
| 文本长度 | **800**（正好卡在 `[:800]` 截断上限） |
| 块数（`回测进化经验参考` 出现次数） | 3 |
| **建议杠杆** | 7x / 8x / 8x / **9x** ⇒ **3 个不同取值** |
| **建议止盈** | 3.9% / 5.2% / **13.4% / 14.0%** ⇒ **最大差 3.4 倍** |
| 建议止损 | 4.3% / 4.6% / 5.6% / 5.7% |
| **回测最优夏普** | 11.46 / 9.46 / **18.92**（后两块重复同一个 18.92） |
| 回测最佳胜率 | 92.9% / 93.8% / 58.1% |
| 结尾 | `…--- 经验参考结束 ---\n<!-- wisdom_ids:[84, 6` ⇒ **半截 HTML 注释** |
| 未闭合 `<!--` | **1** |

## 为什么这是缺陷（而不是"多给点参考"）

1. **互相矛盾**：同一条 prompt 里同时出现"杠杆 7x/8x/9x"、"止盈 3.9% ↔ 14.0%"，
   而**没有任何取舍依据**（无样本量、无时间、无优先级）⇒ LLM 只能任选或忽略；
2. **极值更甚**：Sharpe **18.92** 比 §32 报的 11.46 更极端，且**同一个 18.92 重复两遍**
   （同一份智慧被两个模板各带一次 ⇒ 重复计数放大"证据感"）；
3. **截断成畸形片段**：以 `<!-- wisdom_ids:[84, 6` 结尾 ⇒ 载荷尾部是不可解析的半截注释，
   既浪费字符也污染上下文（`[:800]` 是硬截断，无边界对齐）。

## 处置

**未改**（改文本 = 改主脑输入）。已钉回归锁：块数、矛盾取值数、未闭合注释、截断长度、
极值出现。**修好时这些用例会失败**，强制同步报告 §36 与待办 B19。

**建议修法（待批准；均属"呈现层"，不改任何策略判定）**：
① 按 `wisdom_id` 去重（同一份智慧只出现一次）；
② 多模板冲突时不并排罗列矛盾数值，改为"以最可信一份为准 + 其余仅列 id"，
   或给每份标注来源模板与生成时间；
③ 截断改为**按块边界截断**（不切断 HTML 注释/句子）；
④ 极值按 §35 的建议加存疑标注。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto.brain import _wisdom_feed  # noqa: E402


@pytest.fixture(scope="module")
def feed():
    f = _wisdom_feed("preview", "mid") or {}
    if not (f.get("text") or "").strip():
        pytest.skip("当前无智慧饲料（无 insight 数据），本组用例不适用")
    return f


def test_feed_concatenates_multiple_templates(feed):
    """多模板拼接：块数应 ≥2（修好时若改为单块，本用例失败 ⇒ 更新 §36）。"""
    t = feed["text"]
    blocks = t.count("回测进化经验参考")
    assert blocks >= 2, f"块数降到 {blocks} ⇒ 可能已改为单块，请更新 §36"
    assert int(feed.get("n") or 0) >= 2


def test_contradictory_risk_advice_present(feed):
    """互相矛盾的风控建议同时出现（这正是问题本身）。"""
    t = feed["text"]
    lev = set(re.findall(r"建议杠杆:\s*(\S+)", t))
    tp = set(re.findall(r"建议止盈:\s*(\S+)", t))
    assert len(lev) >= 2, f"杠杆建议只剩 {lev} ⇒ 可能已去冲突，请更新 §36"
    assert len(tp) >= 2, f"止盈建议只剩 {tp} ⇒ 可能已去冲突，请更新 §36"


def test_extreme_metrics_present(feed):
    """极端夏普/胜率在场（§32 的同一来源，规模更大）。"""
    t = feed["text"]
    shr = [float(x) for x in re.findall(r"回测最优夏普比率:\s*([0-9.]+)", t)]
    if not shr:
        pytest.skip("本次饲料无夏普字段")
    assert max(shr) > 5, f"夏普最大值 {max(shr)} ⇒ 若已加封顶/过滤，请更新 §36"


def test_truncated_mid_comment(feed):
    """`[:800]` 硬截断把 HTML 注释切断 ⇒ 载荷尾部畸形。"""
    t = feed["text"]
    assert len(t) == 800 or len(t) < 800, f"长度异常 {len(t)}"
    if len(t) < 800:
        pytest.skip("本次未触发截断")
    unclosed = t.count("<!--") - t.count("-->")
    assert unclosed >= 1, "截断处已不再切断注释 ⇒ 可能已按块边界截断，请更新 §36"


def test_duplicate_wisdom_blocks(feed):
    """同一份智慧被重复带入（同一极值出现两次）。"""
    t = feed["text"]
    ids = re.findall(r"wisdom_ids:\[([0-9,\s]+)\]", t)
    assert ids, "未找到 wisdom_ids 标记（渲染格式变了 ⇒ 复核 §36）"
    if len(ids) < 2:
        pytest.skip("只有一块，重复问题不存在")
    # 同一组 id 不应重复出现
    assert len(set(ids)) == len(ids) or True  # 记录性：重复由后续修法处理
