# -*- coding: utf-8 -*-
"""[轮140 2026-09-20] `direction` 归一化补中文词 + 记录"LLM 自己在观望"这一事实。

## 实测（core.analysis_runs 最近 30 条 midlong_thesis）
- primary/arbiter 的 direction：**neutral 14 / bearish 4 / bullish 2**（另有 1 条 **中文「中性」**）；
- `recommend_open`：**False 18/18**（非空全 False）；
- `confidence` 0.32~0.55；`consensus_score` 中位 **0.31**（0.179~0.7）。

⇒ 「中线不成交」的最后一环不是闸门，而是**两个模型都判断观望**（ranging/信号分歧）。
顺带暴露一个**归一化漏洞**：中文方向词不在白名单 ⇒ 被静默当 0（中性），
与"模型明确说中性"无法区分。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.analysis.schemas import direction_to_int  # noqa: E402


def test_chinese_direction_words_normalized():
    assert direction_to_int("看多") == 1
    assert direction_to_int("偏多") == 1
    assert direction_to_int("看空") == -1
    assert direction_to_int("偏空") == -1
    assert direction_to_int("中性") == 0, "中文中性必须仍判 0（而不是未知）"


def test_english_and_numeric_still_work():
    for v, want in (("bullish", 1), ("long", 1), ("bearish", -1), ("short", -1),
                    ("neutral", 0), (1, 1), (-1, -1), (0, 0), (None, 0), ("什么鬼", 0)):
        assert direction_to_int(v) == want, f"{v!r} → {direction_to_int(v)}，期望 {want}"


def test_brain_map_direction_uses_schema():
    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8", errors="replace")
    assert "schemas.direction_to_int(raw)" in src, \
        "_map_direction 不再走 schemas.direction_to_int ⇒ 归一化修在此处无效"
