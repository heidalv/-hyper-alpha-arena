# -*- coding: utf-8 -*-
"""Z119: L1 入场闸的「阈值 vs 文案」一致性取证（§58）。

背景：
  - 决策用 `_l1_up()`：`score >= LONG_V2_L1_UP_SCORE`（可配，默认 3，UI 可调 2..5）；
  - 文案用 `c['state']`：来自 `trend_layer.classify()` 的**硬编码** `_UP_SCORE=3`。
⇒ 两者在阈值 ≠ 3 时会打架，产出**自相矛盾**的审计文本。
本脚本用 stub 分类结果（不碰 DB/网络）枚举 score × 阈值，打印 gate 判决与文案。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import pandas as pd  # noqa: E402
import backend.services.long_trend_v2 as lv2  # noqa: E402


def _stub(score: float, state: str):
    def _f(symbol):
        return pd.DataFrame({"close": [1.0]}), {"state": state, "score": score, "close": 1.0}
    return _f


def run(thr: int, score: float, state: str):
    os.environ["LONG_V2_L1_UP_SCORE"] = str(thr)
    lv2._get_l1_classification = _stub(score, state)  # type: ignore[assignment]
    ok, why = lv2.entry_gate("BTC", "buy")
    sig = lv2.entry_signal("BTC")
    return ok, why, sig.get("hold_reason", "")


print("=== 阈值 × score → 判决与文案（state 取 trend_layer 硬编码 ±3 的判定）===")
cases = [
    (3, 3.0, "up", "阈值=硬编码（当前生产）"),
    (2, 2.0, "sideways", "把阈值放宽到 2：判决变了，文案呢？"),
    (2, 1.0, "sideways", "阈值 2、score 1 → 应拒"),
    (4, 3.0, "up", "把阈值收紧到 4：state=up 但应拒 → 文案矛盾？"),
    (4, 4.0, "up", "阈值 4、score 4 → 应放行"),
]
for thr, score, state, note in cases:
    ok, why, hold = run(thr, score, state)
    print(f"\n-- 阈值={thr} score={score} state={state}  （{note}）")
    print(f"   entry_gate: allowed={ok}")
    print(f"      reason: {why}")
    print(f"   entry_signal.hold_reason: {hold}")
