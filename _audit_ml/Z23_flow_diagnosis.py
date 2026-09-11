# -*- coding: utf-8 -*-
"""Z23：流量瓶颈定位——决策漏斗 JSONL 的**逐原因**统计（§24 #25）。

周报只显示 TOP6，且 `evaluate_and_execute_returned_false` 把多个闸门混在一起。
本脚本直接读 `data/midlong_direction_audit.jsonl`，按 stage × reason × tier 拆开，
回答「到底是哪一道闸在拦流量」。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PATH = os.getenv("MIDLONG_DIRECTION_AUDIT_PATH",
                 str(ROOT / "data" / "midlong_direction_audit.jsonl"))


def load(days: int = 14):
    if not os.path.isfile(PATH):
        print(f"文件不存在：{PATH}")
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    rows = []
    with open(PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts = r.get("ts") or r.get("time") or r.get("created_at")
            t = None
            if isinstance(ts, (int, float)):
                t = float(ts) if ts < 1e11 else float(ts) / 1000.0
            elif isinstance(ts, str):
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    t = None
            if t is not None and t < cutoff:
                continue
            rows.append(r)
    return rows


def main() -> int:
    rows = load(14)
    print(f"文件={PATH}")
    print(f"近 14 天记录数={len(rows)}")
    if not rows:
        return 0
    print("\n=== 样例字段（首行）===")
    for k, v in list(rows[0].items()):
        print(f"  {k} = {str(v)[:120]}")

    print("\n=== outcome 分布 ===")
    for k, v in Counter(str(r.get("outcome") or "?") for r in rows).most_common():
        print(f"  {k:<22}{v}")

    print("\n=== 按 stage × outcome ===")
    c = Counter((str(r.get("stage") or "?"), str(r.get("outcome") or "?")) for r in rows)
    for (stg, oc), n in sorted(c.items(), key=lambda x: -x[1]):
        print(f"  {stg:<12}{oc:<14}{n}")

    print("\n=== skip 的 reason 前缀 TOP25 ===")
    skips = [r for r in rows if str(r.get("outcome") or "").lower() in ("skip", "blocked", "reject")]
    if not skips:
        skips = [r for r in rows if r.get("reason")]
    cnt = Counter()
    for r in skips:
        reason = str(r.get("reason") or "unknown")
        prefix = reason.split("(", 1)[0].split(":", 1)[0].strip()[:80]
        cnt[prefix] += 1
    for k, v in cnt.most_common(25):
        print(f"  {k:<48}{v}")

    print("\n=== 按 direction / tier 的 skip 原因（前 12）===")
    for key in ("direction", "dir", "tier", "trade_nature"):
        vals = Counter(str(r.get(key) or "") for r in rows)
        if len(vals) > 1:
            print(f"  [{key}] " + ", ".join(f"{k or '-'}={v}" for k, v in vals.most_common(8)))

    print("\n=== 各闸门「首个拦截」占比（按 reason 归类）===")
    groups = {
        "learned 门": ("midlong_long_learned_block", "learned_long"),
        "位置闸": ("location_gate_veto", "location_gate"),
        "图表闸": ("chart_gate_veto", "chart_gate"),
        "组合闸": ("midlong_portfolio_block",),
        "冷却": ("midlong_cooldown_block", "cooldown"),
        "hub 未确认": ("hub_wait_or_neutral",),
        "空头停": ("midlong_short_off",),
        "chop 空仓": ("midlong_chop_flat",),
        "执行通用失败": ("evaluate_and_execute_returned_false",),
    }
    tot = len(skips) or 1
    for name, pats in groups.items():
        n = sum(1 for r in skips
                if any(p in str(r.get("reason") or "") for p in pats))
        print(f"  {name:<16}{n:>6}  ({n/tot:.1%})")
    other = sum(1 for r in skips
                if not any(p in str(r.get("reason") or "")
                           for pats in groups.values() for p in pats))
    print(f"  {'其它':<16}{other:>6}  ({other/tot:.1%})")

    print("\n=== 逐日 skip 数（看节奏）===")
    byday = defaultdict(int)
    for r in skips:
        ts = r.get("ts") or r.get("time") or ""
        byday[str(ts)[:10]] += 1
    for d, n in sorted(byday.items())[-14:]:
        print(f"  {d}  {n:>5}  {'#' * min(n // 10, 60)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
