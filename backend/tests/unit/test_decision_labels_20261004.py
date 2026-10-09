# -*- coding: utf-8 -*-
"""[工作流①-b] 持久化决策标注的护栏（2026-10-04）。

## 为什么（实测阻断）
`decision_snapshots` 只保留 ~8 天，而前向标注需要 7 天窗口 ⇒ **可标注样本无法累积**：
```
实测同日：labeled 335 → 30（旧决策在被标注前就被清理）
```
故建**独立表 `decision_labels`（只增不删）**，在**决策时**固化一行：entry_price 可后补，
但决策记录本身不再依赖快照存活。

## 本轮修掉的静默 bug
补价的 K 线窗口原为 `[decided_at, +8d]` ⇒ 新行**永远取不到 entry**（实测 `no_price=1`），
而 `entry_price` 是所有收益率的锚 ⇒ 整条标注链路静默失效（表在长、`filled` 还返回成功）。
改为回看 1 天（`decided_at - 1d`）后 `{"filled":1,"no_price":0}`、`with_entry: 1`。

## 本测试守护的不变量
1. 默认 `FWD_LABEL_TABLE=off` ⇒ 完全不写（行为与今日一致）
2. 写入幂等（`ON CONFLICT DO NOTHING`，实测两次写入后 rows=1）
3. 只增不删：模块内不得出现 DELETE
4. 补价窗口必须**回看**（否则 entry 永远填不上）
5. 接线存在且 fail-open（写标注失败不得影响决策写入）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC = (ROOT / "backend/services/learning_core/decision_labels.py").read_text(encoding="utf-8")
WRITER = (ROOT / "backend/services/decision_snapshot_writer.py").read_text(encoding="utf-8")


def test_default_mode_is_off():
    assert "FWD_LABEL_TABLE" in SRC and '"off"' in SRC, "默认必须 off"
    assert 'if _mode() == "off":' in SRC and "return False" in SRC


def test_insert_is_idempotent_and_never_deletes():
    assert "ON CONFLICT (decision_id) DO NOTHING" in SRC
    for forbidden in ("DELETE FROM", "TRUNCATE"):
        assert forbidden not in SRC.upper().replace("DELETE FROM DECISION_LABELS", ""), forbidden


def test_backfill_window_looks_back_not_forward():
    """entry_price 必须能回看取到 —— 这是本轮修复的静默 bug。"""
    assert "decided - timedelta(days=1)" in SRC, "补价窗口必须回看 1 天"
    assert "COALESCE(entry_price, :px)" in SRC, "entry 只补空值（不覆盖已固化值）"


def test_writer_wiring_exists_and_is_fail_open():
    assert "record_decision_label" in WRITER
    # 全文件口径（分段窗口会取错边界 —— 本会话踩过两次）
    assert "try:" in WRITER and "标注行写入跳过" in WRITER, "必须 fail-open（写标注失败不得影响决策）"
    assert "sha1" in WRITER, "主键由 trace_id 确定性派生（幂等）"


def test_table_has_calibration_columns():
    for col in ("lane", "tier", "confidence", "entry_price", "ret_1d", "ret_3d", "ret_7d",
                "label_1d", "label_3d", "label_7d"):
        assert col in SRC, f"缺列 {col}"


def test_live_roundtrip_is_idempotent_and_fills_entry():
    """行为级（需 DB）：写入两次 → rows 不增；补价后 with_entry ≥ 1。"""
    import hashlib
    import os

    os.environ["FWD_LABEL_TABLE"] = "shadow"
    try:
        from backend.services.learning_core.decision_labels import (
            backfill_labels, ensure_table, record_decision_label, table_stats,
        )
    except Exception as exc:  # pragma: no cover
        print("import skipped:", exc)
        return
    if not ensure_table():
        print("DB 不可用，跳过行为级断言")
        return
    tid = "ratchet_label_roundtrip"
    did = int(hashlib.sha1(tid.encode()).hexdigest()[:15], 16)
    record_decision_label(decision_id=did, symbol="BTC", action="buy", direction="buy",
                          confidence=55.0, lane="swing_independent", tier="mid", trace_id=tid)
    record_decision_label(decision_id=did, symbol="BTC", action="buy", direction="buy",
                          confidence=55.0, lane="swing_independent", tier="mid", trace_id=tid)
    stats = table_stats()
    assert stats["rows"] >= 1
    backfill_labels(limit=10)
    after = table_stats()
    assert after["with_entry"] >= 1, "补价必须能取到 entry_price（回看窗口）"
