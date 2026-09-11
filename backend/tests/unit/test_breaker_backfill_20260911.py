# -*- coding: utf-8 -*-
"""[§79 / 决策 P21] 熔断滚动窗 DB 回填的契约测试。

背景（§78.2② / #63）：`record_close` 覆盖不全 ⇒ 窗口欠预热（`mid|trend_broken` 65 笔只记 5 笔）
⇒ 熔断对最该拦的通道结构上失效。用户决策：**从 DB 回填**。

锁定：
  1. `series_from_rows` 的折叠口径（按时间升序、窗口=最后 30 笔、n/wins 累计）；
  2. `merge_into_state` 的两条硬规则（**计数只增不减**、**窗口以 DB 为准**）；
  3. `shadow_snapshot` 用**生效阈值**（15/0.40）给出可评估/会被 shadow 的通道；
  4. 回填脚本存在、支持 `--dry-run`、且会先备份（源码级护栏）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.exit.breaker_backfill import (  # noqa: E402
    ROLLING_WINDOW,
    merge_into_state,
    series_from_rows,
    shadow_snapshot,
)


def test_series_folds_by_tier_and_channel():
    rows = [
        ("trend_broken: 结构破坏", "mid", False),
        ("trend_broken: 结构破坏", "mid", True),
        ("sl: 止损", "mid", False),
        ("", "mid", True),          # 空通道 ⇒ 跳过
    ]
    s = series_from_rows(rows)
    assert set(s) == {"mid|trend_broken", "mid|sl"}, s
    assert s["mid|trend_broken"] == {"n": 2, "wins": 1, "recent": [0, 1]}
    assert s["mid|sl"]["n"] == 1 and s["mid|sl"]["recent"] == [0]


def test_window_keeps_last_n():
    rows = [("midlong: x", "mid", i % 2 == 0) for i in range(50)]
    s = series_from_rows(rows)
    rec = s["mid|midlong"]["recent"]
    assert len(rec) == ROLLING_WINDOW, "窗口应截断到最后 30 笔"
    assert s["mid|midlong"]["n"] == 50, "累计计数不截断"
    # 最后 30 笔取自 i=20..49（偶数=win）⇒ 15 胜
    assert sum(rec) == 15


def test_merge_never_decreases_counters_and_replaces_window():
    existing = {"mid|trend_broken": {"n": 500, "wins": 100, "recent": [1, 0, 1]}}
    series = {"mid|trend_broken": {"n": 65, "wins": 13, "recent": [0] * 30}}
    merged, stats = merge_into_state(existing, series)
    e = merged["mid|trend_broken"]
    assert e["n"] == 500 and e["wins"] == 100, "计数只增不减（不得被回填抹掉）"
    assert e["recent"] == [0] * 30, "窗口以 DB 为准"
    assert stats == {"keys_seen": 1, "keys_added": 0, "keys_updated": 1}
    # 原对象不被修改
    assert existing["mid|trend_broken"]["recent"] == [1, 0, 1]


def test_merge_adds_missing_keys():
    merged, stats = merge_into_state({}, {"long|thesis_invalidation": {"n": 3, "wins": 0, "recent": [0, 0, 0]}})
    assert merged["long|thesis_invalidation"]["n"] == 3
    assert stats["keys_added"] == 1


def test_shadow_snapshot_uses_effective_thresholds():
    breaker = {
        "mid|trend_broken": {"n": 65, "wins": 13, "recent": [0] * 25 + [1] * 5},   # 5/30=17% ⇒ shadow
        "mid|breakeven_tp": {"n": 30, "wins": 30, "recent": [1] * 30},             # 100% ⇒ 不 shadow
        "long|thesis_invalidation": {"n": 3, "wins": 0, "recent": [0, 0, 0]},      # 样本不足 ⇒ 不计
    }
    snap = shadow_snapshot(breaker, min_n=15, max_wr=0.40)
    assert snap["evaluable"] == 2
    assert any(k.startswith("mid|trend_broken") for k in snap["shadowed"])
    assert not any(k.startswith("mid|breakeven_tp") for k in snap["shadowed"])
    assert not any("thesis_invalidation" in k for k in snap["shadowed"]), "样本不足不得被评估"


def test_backfill_script_has_safety_rails():
    src = (ROOT / "backend/scripts/backfill_exit_channel_breaker.py").read_text(encoding="utf-8")
    assert "--dry-run" in src
    assert "shutil.copy2" in src, "覆盖前必须备份"
    assert "os.replace" in src, "必须原子写入"
    assert "EXIT_CHANNEL_SHADOW_MIN_N" in src, "阈值必须取生效值"
