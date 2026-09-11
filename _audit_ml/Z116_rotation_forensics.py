# -*- coding: utf-8 -*-
"""Z116: §52.6-A 取证 —— 漏斗审计被轮转切掉的历史到底有多大？（data/*.jsonl.1）

机制假设：`log_retention_service._force_rotate_huge_jsonl` 在 `data/` 下把
>2×AUDIT_JSONL_MAX_BYTES(默认 20MB→40MB) 的 .jsonl **整体移走并清空**；
读者（周报/审计脚本/本会话的分析）只读单一路径 ⇒ 轮转后历史"消失"。
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = ROOT / "data"
live = data / "midlong_direction_audit.jsonl"
old = data / "midlong_direction_audit.jsonl.1"


def scan(p: Path, label: str) -> dict:
    if not p.exists():
        print(f"{label}: 不存在")
        return {}
    n = 0
    bad = 0
    first = last = None
    oc = Counter()
    reasons = Counter()
    with p.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                r = json.loads(line)
            except Exception:
                bad += 1
                continue
            ts = r.get("epoch")
            if ts:
                t = datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
                first = first or t
                last = t
            oc[str(r.get("outcome") or "?")] += 1
            if str(r.get("outcome")) == "skip":
                reasons[str(r.get("reason") or "?").split("(")[0].split(":")[0][:48]] += 1
    mb = round(p.stat().st_size / 1e6, 2)
    print(f"\n=== {label}: {p.name} ({mb} MB, mtime={datetime.fromtimestamp(p.stat().st_mtime).isoformat(sep=' ')}) ===")
    print(f"  行数 {n}（解析失败 {bad}）")
    print(f"  UTC 范围 {first} → {last}")
    print(f"  outcome: {dict(oc)}")
    top = reasons.most_common(8)
    tot_skip = sum(reasons.values())
    print(f"  skip 原因 top（共 {tot_skip}）:")
    for k, v in top:
        print(f"     {v:>6}  {v/max(1,tot_skip):>6.1%}  {k}")
    return {"n": n, "mb": mb, "first": first, "last": last, "skips": tot_skip}


a = scan(old, "轮转备份（轮转前的历史）")
b = scan(live, "当前活动文件")

print("\n=== 合计 ===")
if a and b:
    print(f"  历史总量 ≈ {a['n'] + b['n']} 行（{a['mb'] + b['mb']} MB）；"
          f"当前可读仅 {b['n']} 行 ⇒ 读者可见比例 {b['n']/(a['n']+b['n']):.1%}")
print("\n=== 备份文件是否受 30 天清理影响 ===")
print("  log_retention: data 下 patterns=['*.jsonl.*'] keep_days=LOG_RETENTION_DAYS(=30)")
print(f"  备份 mtime = {datetime.fromtimestamp(old.stat().st_mtime).isoformat(sep=' ') if old.exists() else '-'}")
print("  ⇒ 该备份将在 mtime+30 天后被**静默删除**")
