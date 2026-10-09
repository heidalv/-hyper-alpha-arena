# -*- coding: utf-8 -*-
"""[F377 2026-09-18] 睡眠巩固规则：**`days` 参数被忽略** + `min_samples=3` + 文本无不确定性标注。

## 三个事实（读源码即可复现，`mlto/episodic_memory.py:320-393`）

1. **`days` 参数从未参与查询**：函数签名 `consolidate_daily(*, days: int = 7, min_samples: int = 3)`，
   docstring 写"回放**近 N 天**带结局的情景"，而查询是
   ```python
   .filter(MltoEpisode.opened == 1, MltoEpisode.outcome_pct.isnot(None))
   .order_by(MltoEpisode.created_at.desc()).limit(2000)
   ```
   —— **没有任何时间过滤**；`days` 只被原样写进产物 `"days": days`。
   调用方 `evolution_scheduler.py:1631` 传的就是 `days=7`。
   ⇒ 产物自称"近 7 天"，实际是"最近 2000 条（不限时间）"——**读数骗人**。
2. **`min_samples` 默认 3**：`n < 3` 才跳过 ⇒ **3 笔的格子会被当成"长期规则"蒸馏出来**
   （运行态实测原文里就有"长线·震荡市·低波动·做空：**3 笔** 胜率67% 均盈+1.78%"）。
   3 笔的胜率 95% 置信区间约 [9%, 99%]，这个数字是噪声。
3. **文本层无不确定性标注**：`lessons_text` 只有"N 笔 胜率X% 均盈Y%"，没有 CI、没有"样本不足"标记；
   而主脑读的**正是这段文本**（`read_consolidated_lessons()` 只回传 `lessons_text[:800]`）。

⇒ 与 F376（`backtest_wisdom` 样本内极值）**同一类**：数字算得出，但被当作"规则"喂给决策者。
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto import episodic_memory as EM  # noqa: E402


def test_days_parameter_is_not_used_in_query():
    """`days` 不参与查询 ⇒ docstring 的"近 N 天"与产物 `days` 字段都是假的。"""
    src = inspect.getsource(EM.consolidate_daily)
    # 查询段：filter/order_by/limit
    assert "MltoEpisode.created_at.desc()" in src and "limit(2000)" in src
    assert "days" in src, "签名里有 days"
    # 但查询里不得出现基于 days 的时间比较
    query_zone = src[src.find("db.query(MltoEpisode)"):src.find("# 按 (tier, regime")]
    assert "days" not in query_zone, "days 出现在查询里 ⇒ 行为已变，请更新报告 §33"
    assert "timedelta" not in query_zone, "查询里出现 timedelta ⇒ 可能已加时间过滤（更新 §33）"


def test_recorded_days_field_is_declared_not_actual():
    """产物记录 `"days": days`（声明值），而非实际跨度 ⇒ 读数不可用于判断窗口。"""
    src = inspect.getsource(EM.consolidate_daily)
    assert '"days": days' in src, "产物 days 字段口径变了 ⇒ 更新 §33"


def test_min_samples_default_is_three():
    """`min_samples=3` ⇒ 3 笔的格子会进"规则"（更新 §33 前不要改这个默认值）。"""
    sig = inspect.signature(EM.consolidate_daily)
    assert sig.parameters["min_samples"].default == 3


def test_lessons_text_has_no_uncertainty_annotation():
    """渲染文本无 CI / 无"样本不足"标记 —— 而主脑读的就是这段文本。"""
    src = inspect.getsource(EM.consolidate_daily)
    seg = src[src.find("lines.append("):src.find('"lessons_text"')]
    for kw in ("CI", "置信", "样本不足", "存疑", "±"):
        assert kw not in seg, f"文本渲染出现『{kw}』⇒ 已加不确定性标注，请更新报告 §33"


def test_read_side_only_returns_text():
    """读侧只回传 lessons_text（结构化 rules 里的 n 不进入主脑视野）。"""
    src = inspect.getsource(EM.read_consolidated_lessons)
    assert "lessons_text" in src and "rules" not in src
    assert "[:800]" in src, "截断长度变了 ⇒ 复核 §33"


def test_runtime_artifact_shows_small_n_cells_if_present():
    """运行态旁证：产物里若存在 n<10 的规则，说明小样本确实被当规则下发。

    产物不存在时跳过（不构成本条断言的对象）。
    """
    p = ROOT / EM._CONSOLIDATION_PATH
    if not p.exists():
        pytest.skip(f"尚无巩固产物: {p}")
    import json
    d = json.loads(p.read_text(encoding="utf-8"))
    rules = d.get("rules") or []
    small = [r for r in rules if int(r.get("n") or 0) < 10]
    txt = str(d.get("lessons_text") or "")
    # 只要产物里存在小样本格子，文本里就不该缺少提示；这里只做**记录性断言**
    if small and txt:
        assert "存疑" not in txt and "样本不足" not in txt, (
            "文本已带不确定性标注 ⇒ 请更新报告 §33 与待办 B16")
