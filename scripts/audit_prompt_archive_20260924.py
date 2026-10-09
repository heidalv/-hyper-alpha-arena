# -*- coding: utf-8 -*-
"""[续作 R2] 从归档原件体检"活主脑看到的数据有多新"。

归档由 `brain._archive_context_prompt` 写入 `data/prompt_archives/brain/<YYYYMMDD>/*.json`，
payload 含：ts / symbol / tier / prompt_chars / archived_chars / truncated / text。
`text` 尾部带 `__meta__：数据截止（UTC ms）=…；pack hash=…；缺失层/错误=…`。

本脚本回答目标 ④ 的"是否为空/截断/过期/口径错"四问中的**过期**一维。

⚠️ 两个已踩过的坑（务必保留这两条注释）：
 1. 归档 `ts` 是 `datetime.now().isoformat()` ⇒ **本地时间、无时区**。按 UTC 解析会凭空多出 8 小时。
 2. `__meta__` 里的 `数据截止（UTC ms）` 来自 `context_pack.py:434` 的 `kl_1d[-1]`（**最后一根日线**，4h 兜底）
    ⇒ 它是**日线口径**（当天 00:00 UTC），**不能用来判新鲜度**。
 真正可用的分钟级新鲜度信号是正文 JSON 里的 `"as_of":"YYYY-MM-DD HH:MM:SS"`（本地时间）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(r"D:\001Alpha\Hyper-Alpha-Arena\data\prompt_archives\brain")
META = re.compile(r"数据截止（UTC ms）=(\d+)")
HASH = re.compile(r"pack hash=([0-9a-f]+)")
MISS = re.compile(r"缺失层/错误=([^；]*)")
ASOF = re.compile(r'"as_of"\s*:\s*"(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)"')

rows = []
for p in sorted(BASE.rglob("*.json")):
    try:
        o = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  [跳过] {p.name}: {exc}")
        continue
    t = o.get("text") or ""
    m = META.search(t)
    cutoff_ms = int(m.group(1)) if m else None
    ts = o.get("ts") or ""
    # 分钟级新鲜度：as_of（本地）与 ts（本地）同为 naive 本地时间，可直接相减
    lag = None
    a = ASOF.search(t)
    as_of = a.group(1) if a else None
    if as_of and ts:
        try:
            lag = (datetime.fromisoformat(ts) - datetime.fromisoformat(as_of)).total_seconds() / 60.0
        except Exception:
            lag = None
    rows.append({
        "file": p.name,
        "symbol": o.get("symbol"), "tier": o.get("tier"), "ts": ts,
        "prompt_chars": o.get("prompt_chars"),
        "truncated": o.get("truncated"),
        "as_of": as_of,
        "lag_min": None if lag is None else round(lag, 1),
        "cutoff_day_utc": (
            datetime.fromtimestamp(cutoff_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            if cutoff_ms else None
        ),
        "pack_hash": (HASH.search(t).group(1) if HASH.search(t) else None),
        "missing": (MISS.search(t).group(1) if MISS.search(t) else None),
        "has_bar_array": "[[" in t,
        "ohlc_keys": sum(t.count(k) for k in ('"high"', '"low"', '"close"', '"ohlc"', '"ohlcv"')),
    })

print(f"归档文件数 = {len(rows)}  目录 = {BASE}")
if not rows:
    print("  （还没有归档；主脑批次跑过后再来看）")
else:
    print(f"{'symbol':9s} {'tier':5s} {'chars':>6s} {'截断':>4s} {'as_of':19s} {'滞后(分)':>8s} {'日线截止(UTC)':16s} {'缺失层'}")
    for r in rows:
        print(f"{str(r['symbol']):9s} {str(r['tier']):5s} {str(r['prompt_chars']):>6s} "
              f"{str(r['truncated']):>4s} {str(r['as_of']):19s} {str(r['lag_min']):>8s} "
              f"{str(r['cutoff_day_utc']):16s} {r['missing']}")
    lags = [r["lag_min"] for r in rows if r["lag_min"] is not None]
    if lags:
        print(f"\n分钟级滞后（as_of → 归档）: n={len(lags)} 最小={min(lags)} 最大={max(lags)} 平均={sum(lags)/len(lags):.1f} 分钟")
    print(f"K 线 bars 出现过的归档数 = {sum(1 for r in rows if r['has_bar_array'])} / {len(rows)}")
    print(f"含 OHLC 键的归档数     = {sum(1 for r in rows if r['ohlc_keys'])} / {len(rows)}")
    print(f"被截断的归档数         = {sum(1 for r in rows if r['truncated'])} / {len(rows)}")
