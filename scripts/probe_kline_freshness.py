# -*- coding: utf-8 -*-
"""行情数据新鲜度体检（只读）：主脑分析的标的是否有陈旧/缺失 K 线。

背景：根因查询中发现 `factor_route reason=no_price`（VIRTUAL）、
`[KlineAgg] … 基准所 K 线过期…拒绝聚合返回`、`[DataCenter] … 数据过期 stale=2604s`。
若被分析的标的普遍数据陈旧，则"方向判断"本身建立在坏数据上。
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "logs" / "backend.log"
with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - 25_000_000))
    raw = f.read().decode("utf-8", errors="replace").splitlines()

marks = [ln[:19] for ln in raw if "Application startup complete." in ln]
RS = marks[-1] if marks else ""
post = [ln for ln in raw if ln[:19] >= RS] if RS else raw
print(f"窗口 = {RS} → {raw[-1][:19] if raw else '?'}  行数={len(post):,}")

stale_sym = Counter()
stale_tf = Counter()
reject = Counter()
no_price = Counter()
dc_stale = Counter()

for ln in post:
    if "K 线过期" in ln or "K线过期" in ln:
        m = re.search(r"\[KlineAgg\]\s+(\S+?)/(\S+?)@", ln)
        if m:
            stale_sym[m.group(1)] += 1
            stale_tf[m.group(2)] += 1
        if "拒绝聚合返回" in ln:
            reject[m.group(1) if m else "?"] += 1
    if "no_price" in ln:
        m = re.search(r"symbol=(\S+)", ln)
        if m:
            no_price[m.group(1)] += 1
    if "数据过期" in ln or "stale=" in ln:
        m = re.search(r"(\S+?)/(\S+?)@", ln)
        if m:
            dc_stale[m.group(1)] += 1

print(f"\n[1] KlineAgg 过期告警：涉及 {len(stale_sym)} 个标的，合计 {sum(stale_sym.values())} 条")
for s, n in stale_sym.most_common(20):
    print(f"    {s:12s} {n}")
print(f"\n[2] 按周期：{dict(stale_tf.most_common(8))}")
print(f"\n[3] '拒绝聚合返回' 的标的：{dict(reject.most_common(12))}")
print(f"\n[4] factor_route reason=no_price：{dict(no_price.most_common(12))}")
print(f"\n[5] DataCenter 数据过期：{dict(dc_stale.most_common(12))}")

# 主脑最近分析过的标的 vs 数据坏的标的
brainsyms = Counter()
for ln in post:
    if "出进程分析" in ln or "MidLongBrain" in ln:
        for m in re.finditer(r"symbol=(\S+)", ln):
            brainsyms[m.group(1)] += 1
print(f"\n[6] 主脑窗口内涉及的标的：{dict(brainsyms.most_common(15))}")
overlap = sorted(set(brainsyms) & set(stale_sym))
print(f"    **与'数据过期'重合的标的**：{overlap}")
