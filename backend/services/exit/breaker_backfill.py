# -*- coding: utf-8 -*-
"""[§79 执行 2026-09-11 / 决策 P21] 出场通道熔断滚动窗的 **DB 回填**（纯逻辑部分）。

背景（§78.2② / 缺陷 #63）：`source_attribution.record_close()` **覆盖不全** ⇒ 熔断器的
`_breaker[key]["recent"]` 长期欠预热（`mid|trend_broken` 实际 65 笔只记了 5 笔），
48 个通道里只有 5 个达到 `min(MIN_N, 30)=15` 的评估门槛 ⇒ **熔断对最该拦的通道结构上失效**。

修法（用户决策）：以 **DB 为真相源**回填滚动窗 —— 从 `paper_positions` 已平仓记录重建
每个 `tier|通道` 最近 `ROLLING_WINDOW` 笔的胜负序列，再据此重算 shadow 标志。

本模块只做**纯函数**（便于契约测试）；DB/文件 IO 在
`backend/scripts/backfill_exit_channel_breaker.py`。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

#: 与 source_attribution 保持一致的滚动窗长度
ROLLING_WINDOW = 30


def series_from_rows(rows: Iterable[Tuple[str, str, bool]], window: int = ROLLING_WINDOW
                     ) -> Dict[str, Dict[str, Any]]:
    """把 `(close_reason, tier, win)` 序列（按时间**升序**）折叠成 `breaker` 结构。

    返回 `{ "tier|channel": {"n": int, "wins": int, "recent": [0/1, ...]} }`，
    `recent` 只保留**最后 window 笔**（与 `record_close` 的窗口口径一致）。

    [§84 执行 2026-09-11 / 决策 P27-A] 同时支持 4 元组 `(reason, tier, win, ts_epoch)`：
    有 ts 时写入 `last_ts`（= 该通道**最新样本时间**），供"证据新鲜度约束"使用 ——
    没有它，闸门只能用过期证据抑制（缺陷 #69）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        reason, tier, win = row[0], row[1], row[2]
        ts = row[3] if len(row) > 3 else None
        from backend.services.source_attribution import normalize_reason

        t = str(tier or "?").strip().lower() or "?"
        ch = normalize_reason(str(reason or ""))
        # `normalize_reason("")` 会返回占位符 "?"；空通道不该在熔断表里造出 `tier|?` 这种假键
        # （回填时尤其重要：summary/historical 记录常带空 close_reason）。
        if not ch or ch == "?":
            continue
        key = f"{t}|{ch}"
        st = out.setdefault(key, {"n": 0, "wins": 0, "recent": []})
        st["n"] += 1
        st["wins"] += 1 if win else 0
        st["recent"].append(1 if win else 0)
        if len(st["recent"]) > window:
            st["recent"] = st["recent"][-window:]
        if ts is not None:
            try:
                _ts = float(ts)
                if _ts > float(st.get("last_ts") or 0.0):
                    st["last_ts"] = _ts
            except (TypeError, ValueError):
                pass
    return out


def merge_into_state(breaker: Dict[str, Any], series: Dict[str, Dict[str, Any]]
                     ) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """把回填序列并入既有 `breaker`。

    安全规则（两条，均为硬约束）：
      1. **计数只增不减**：`n`/`wins` 取 `max(既有, 回填)` —— 历史累计量不得被回填抹掉；
      2. **窗口以 DB 为准**：`recent` 直接替换为 DB 序列（DB 是平仓事实的真相源，
         内存里的窗口正是"漏记"的那个）。
    返回 `(新 breaker, 统计)`；不修改入参。
    """
    import copy

    new = copy.deepcopy(breaker or {})
    stats = {"keys_seen": len(series), "keys_added": 0, "keys_updated": 0}
    for key, ser in (series or {}).items():
        cur = new.get(key)
        if not isinstance(cur, dict):
            new[key] = {"n": int(ser["n"]), "wins": int(ser["wins"]),
                        "recent": list(ser["recent"])}
            if ser.get("last_ts"):
                new[key]["last_ts"] = float(ser["last_ts"])
            stats["keys_added"] += 1
            continue
        cur["n"] = max(int(cur.get("n") or 0), int(ser["n"]))
        cur["wins"] = max(int(cur.get("wins") or 0), int(ser["wins"]))
        cur["recent"] = list(ser["recent"])
        # [§84/P27-A] 时间戳**以 DB 为准**（取较新者）：它是"证据新鲜度"的判据来源。
        if ser.get("last_ts"):
            cur["last_ts"] = max(float(cur.get("last_ts") or 0.0), float(ser["last_ts"]))
        stats["keys_updated"] += 1
    return new, stats


def shadow_snapshot(breaker: Dict[str, Any], *, min_n: int = 15, max_wr: float = 0.40
                    ) -> Dict[str, Any]:
    """按生效阈值给出"回填后会被 shadow 的通道"预览（只读，不写任何状态）。"""
    win_n = min(int(min_n), ROLLING_WINDOW)
    shadowed: List[str] = []
    evaluable = 0
    for key, st in (breaker or {}).items():
        rec = (st or {}).get("recent") if isinstance(st, dict) else None
        if not isinstance(rec, list) or len(rec) < win_n:
            continue
        evaluable += 1
        wr = sum(1 for x in rec[-win_n:] if x) / win_n
        if wr < max_wr:
            shadowed.append(f"{key}({wr:.0%})")
    return {"evaluable": evaluable, "shadowed": sorted(shadowed)}
