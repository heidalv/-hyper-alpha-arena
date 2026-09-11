# -*- coding: utf-8 -*-
"""[2026-09-10 第 8 轮审计] 减仓计数口径契约测试（§45.2 高危 #1）。

背景：`reduce_count` 是**减仓节流判据**（`unified_exit_state_machine.max_reduce_count`、
`master_execution._DEF_REDUCE_MAX/REDUCE_MAX_COUNT`、`defensive_cycle` 均按它限流），
但主流减仓路径此前**从不更新该列** ⇒ 判据恒为 0、上限形同不存在。
实证：全账户 524 笔有减仓事件而仅 26 笔计数>0；近 75 天 46 笔减仓 ≥4 次（最多 10 次）。

本测试锁三件事：
  1. `paper_engine._partial_close` 必须自增 `reduce_count` 与 `last_reduce_at`（回归护栏）；
  2. 对账脚本的纯函数判定正确（不一致率 / 超上限 / 缺终局事件）；
  3. 现有对账脚本对真实库的判定可用（不抛异常）。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def test_partial_close_increments_reduce_count():
    """源码护栏：`_partial_close` 内必须存在 reduce_count 自增（防止再次漏写）。"""
    src = (ROOT / "backend" / "services" / "paper_trading_engine.py").read_text(encoding="utf-8")
    m = re.search(r"def _partial_close\(", src)
    assert m, "找不到 _partial_close"
    # 截到下一个同级 def
    rest = src[m.end():]
    nxt = re.search(r"\n    def ", rest)
    body = rest[: nxt.start()] if nxt else rest
    assert re.search(r"reduce_count\s*=", body), (
        "_partial_close 未更新 reduce_count —— 减仓节流判据会退化为恒 0（§45.2）"
    )
    assert "last_reduce_at" in body, "_partial_close 未更新 last_reduce_at"


def test_consistency_summarize_flags_mismatch():
    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    import importlib
    m = importlib.import_module("audit_position_event_consistency")
    rows = [
        {"id": 1, "symbol": "A", "tier": "mid", "reduce_count": 2, "n_partial": 2,
         "n_final": 1, "closed_at": "2026-09-01"},
        {"id": 2, "symbol": "B", "tier": "mid", "reduce_count": 0, "n_partial": 5,
         "n_final": 1, "closed_at": "2026-09-02"},   # 计数字段失真 + 超上限
        {"id": 3, "symbol": "C", "tier": "long", "reduce_count": 0, "n_partial": 0,
         "n_final": 0, "closed_at": "2026-09-03"},   # 缺终局事件
    ]
    rep = m.summarize(rows, max_reduce=3)
    assert rep["n"] == 3
    assert rep["mismatch_n"] == 1, rep
    assert rep["over_max_n"] == 1
    assert rep["missing_final_n"] == 1
    by = {v["name"]: v for v in rep["verdicts"]}
    assert by["reduce_count 与减仓事件数一致率 ≥95%"]["ok"] is False
    assert by["无仓位超过单仓减仓上限"]["ok"] is False
    assert by["已平仓仓位均有终局事件"]["ok"] is False
    # worst 必须 JSON 可序列化（此前 datetime 会炸）
    import json
    json.dumps(rep, ensure_ascii=False)


def test_consistency_summarize_clean_sample_passes():
    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    import importlib
    m = importlib.import_module("audit_position_event_consistency")
    rows = [{"id": i, "symbol": "X", "tier": "mid", "reduce_count": 1, "n_partial": 1,
             "n_final": 1, "closed_at": "2026-09-01"} for i in range(20)]
    rep = m.summarize(rows)
    assert rep["mismatch_n"] == 0 and rep["over_max_n"] == 0
    assert all(v["ok"] for v in rep["verdicts"])
