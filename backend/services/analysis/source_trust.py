# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R3] **信号源信任桥**：把 signal_review 的复盘结论真正接到决策端。

## 为什么需要（已实测的断链）
`backend/data/agents/latest_signal_review.json`（2026-09-24 08:00，30 天窗口，4765 个已评分信号）
已经算出：

| 源 | n | 命中率 | 平均超额 | 复盘结论 |
|---|---|---|---|---|
| `dual:event_impact` | 4403 | 0.4819 | −62.36bp | **disable（严重度 4）** |
| `dual:trend_chart_review` | 309 | 0.3269 | −109.66bp | **disable（严重度 4）** |
| `dual:daily_brief` | 34 | 0.4118 | −22.32bp | scale_down（严重度 2） |

但这三条建议的 `applied` **全部 = False**，`apply_note` = "observe 模式：仅记录不执行"；
`.env` 里 `AGENT_MODE_SIGNAL_REVIEW` 是**注释掉的**（走默认 observe），
且代码注释说明 `advise` 模式也只是"写成 proposed 实验卡"——**根本没有执行端**。
⇒ **"算了但没人用"**：系统自己判定了负边际源，却继续让它们掌握阻断权。

## 本模块做什么
在**消费端**加一道可回滚的检查：决策代码在采用某个 `dual:*` 源之前问一句
`source_allowed(source)`；若该源在**最新复盘**里被判 `disable`，则不采用。

## 安全设计（四道）
1. **总开关** `SIGNAL_SOURCE_TRUST_ENABLED`（默认 true）；
2. **新鲜度闸**：复盘文件超过 `SIGNAL_SOURCE_TRUST_MAX_AGE_H`（默认 36h）未更新 ⇒ **不做任何事**
   （fail-open 回现状），避免用陈旧结论永久压制；
3. **安全地板**：若一次复盘要把**超过 `SIGNAL_SOURCE_TRUST_MAX_DISABLE_FRAC`（默认 0.8）**
   的源都禁掉，视为异常 ⇒ **整体不生效**并告警（防止"评估口径坏了 ⇒ 把所有源都关掉"）；
4. **只认最强判据**：只对 `disable` 结论生效；`scale_down` / `observe` 不改变行为（留待后续按权重接）。

回滚：`SIGNAL_SOURCE_TRUST_ENABLED=false`。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_REVIEW_PATH = Path("backend/data/agents/latest_signal_review.json")
_CACHE_TTL_S = 300.0
_cache: Dict[str, object] = {"ts": 0.0, "disabled": set(), "note": "", "all": {}}


def _enabled() -> bool:
    return os.getenv("SIGNAL_SOURCE_TRUST_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def _max_age_h() -> float:
    try:
        return max(1.0, float(os.getenv("SIGNAL_SOURCE_TRUST_MAX_AGE_H", "36") or 36))
    except (TypeError, ValueError):
        return 36.0


def _max_disable_frac() -> float:
    try:
        return min(1.0, max(0.1, float(os.getenv("SIGNAL_SOURCE_TRUST_MAX_DISABLE_FRAC", "0.8") or 0.8)))
    except (TypeError, ValueError):
        return 0.8


def _min_sources_for_floor() -> int:
    """安全地板生效所需的最少源数量（默认 3）。

    源太少时"禁掉比例"没有统计意义：复盘只列 1 个源且它确实为负 ⇒ 1/1=100%
    会误触发地板、反而不生效。故 n < 该值时不做比例判定。
    """
    try:
        return max(2, int(os.getenv("SIGNAL_SOURCE_TRUST_MIN_SOURCES", "3") or 3))
    except (TypeError, ValueError):
        return 3


def _load_review() -> Tuple[Dict[str, str], str]:
    """返回 (source -> 归一化结论, 说明)。任何异常都返回空 dict（fail-open）。"""
    try:
        raw = json.loads(_REVIEW_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"review_unreadable:{type(exc).__name__}"
    # 新鲜度
    ts_ms = raw.get("ts_ms")
    try:
        age_h = (time.time() * 1000 - float(ts_ms)) / 3600000.0 if ts_ms else 1e9
    except (TypeError, ValueError):
        age_h = 1e9
    if age_h > _max_age_h():
        return {}, f"review_stale:{age_h:.1f}h>{_max_age_h():.0f}h"
    out: Dict[str, str] = {}
    for src in (raw.get("findings") or {}).get("sources") or []:
        name = str(src.get("source") or "").strip()
        if not name:
            continue
        v = src.get("verdict")
        if isinstance(v, dict):
            label = str(v.get("action") or v.get("label") or v.get("status") or "").lower()
        else:
            label = str(v or "").lower()
        out[name] = label
    if not out:
        return {}, "review_no_sources"
    return out, f"ok(age={age_h:.1f}h,n={len(out)})"


def _refresh() -> None:
    now = time.time()
    if now - float(_cache.get("ts") or 0) < _CACHE_TTL_S:
        return
    all_v, note = _load_review()
    disabled = {s for s, lab in all_v.items() if lab == "disable"}
    # 安全地板：只在源数量足够时才按比例判定（源太少时比例没有统计意义：
    # 例如复盘只列了 1 个源且它确实为负 ⇒ 1/1=100% 会误触发，反而不生效）。
    _min_n = _min_sources_for_floor()
    if len(all_v) >= _min_n and all_v and len(disabled) / len(all_v) > _max_disable_frac():
        logger.warning(
            "[SourceTrust] 复盘要禁掉 %d/%d 个源（>%.0f%%）⇒ 视为评估异常，**本次不生效**（note=%s）",
            len(disabled), len(all_v), _max_disable_frac() * 100, note,
        )
        disabled = set()
        note = f"aborted_by_floor:{note}"
    _cache.update({"ts": now, "disabled": disabled, "note": note, "all": all_v})
    if disabled:
        logger.info("[SourceTrust] 依复盘结论停用负边际源：%s（%s）", sorted(disabled), note)


def source_allowed(source: str) -> bool:
    """该 `dual:*` 源当前是否允许被决策端采用。fail-open：任何异常都返回 True。"""
    try:
        if not _enabled():
            return True
        _refresh()
        if str(source) in (_cache.get("disabled") or set()):
            return False
        return True
    except Exception:
        return True


def describe() -> str:
    """一行摘要（供状态报告/日志）。"""
    try:
        _refresh()
        return "enabled=%s disabled=%s note=%s" % (
            _enabled(), sorted(_cache.get("disabled") or []), _cache.get("note"))
    except Exception as exc:  # pragma: no cover
        return f"err:{exc}"


def _reset_cache_for_test() -> None:
    _cache.update({"ts": 0.0, "disabled": set(), "note": "", "all": {}})
