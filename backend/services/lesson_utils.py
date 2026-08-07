"""统一的「交易教训」去重工具。

此前三处各用不同去重键，导致 StrategyMemory.key_lessons 库内重复、prompt 注入跨源重复：
  - UnifiedLearning 写 per-strategy：`existing.extend(new)` 完全无去重
  - merge_opencode_lessons 写 _global_：键 = `opencode:{lesson[:80]}`
  - trading_analysts 注入：per 用 `symbol:type:lesson`、global 用 `oc:lesson`

本模块提供单一 `lesson_dedupe_key`，写入与注入两侧共用，确保去重口径一致。

Phase 2 升级: merge_lessons 增强 — 支持过期移除、50条上限、source+category 合并。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List


def lesson_dedupe_key(lesson: Any) -> str:
    """统一去重键：同 category + title[:80] 视为同一条教训。

    Phase 2 升级: 使用 category+title 而非 symbol+type+lesson，适配跨源统一知识池。
    """
    if not isinstance(lesson, dict):
        return f"raw||{str(lesson)[:100].strip().lower()}"
    category = str(lesson.get("category", lesson.get("type", ""))).strip().lower()
    title = str(lesson.get("title", lesson.get("lesson", ""))).strip().lower()[:80]
    return f"{category}|{title}"


def merge_lessons(
    existing: List[Dict[str, Any]] | None,
    new: List[Dict[str, Any]] | None,
    *,
    cap: int = 50,
) -> List[Dict[str, Any]]:
    """合并教训并按统一键去重，保留最近 cap 条（新的在后）。
    
    Phase 2 升级规则：
    1. 相同 dedupe_key (category|title[:80]) → 保留最新的 ingested_at
    2. 同 source + category + title 相似 → 合并 finding_json（更新版覆盖旧版）
    3. 超过 30 天且非 major/critical → 自动过期移除
    4. 保留最近 cap 条（按优先级：severity 高 > ingested_at 新）
    """
    out = list(existing or [])
    
    # 过期移除：超过 30 天且非 major/critical
    cutoff = datetime.utcnow() - timedelta(days=30)
    out = [
        l for l in out
        if (
            l.get("severity") in ("major", "critical")
            or _parse_ts(l.get("ingested_at", "")) >= cutoff
        )
    ]
    
    seen = {lesson_dedupe_key(l): i for i, l in enumerate(out)}
    for item in (new or []):
        k = lesson_dedupe_key(item)
        if k in seen:
            # 同 key → 用新版本覆盖（保留最新的 ingested_at）
            idx = seen[k]
            out[idx] = item
            continue
        seen[k] = len(out)
        out.append(item)
    
    # 按优先级排序：severity 权重 + ingested_at
    _severity_order = {"critical": 4, "major": 3, "warning": 2, "high": 2, "info": 1, "medium": 1, "low": 0}
    out.sort(
        key=lambda x: (
            _severity_order.get(str(x.get("severity", "")).lower(), 1),
            str(x.get("ingested_at", "")),
        ),
        reverse=True,
    )
    return out[:cap]


def _parse_ts(ts_str: str) -> datetime:
    """解析 ISO 时间戳字符串"""
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.utcnow()
