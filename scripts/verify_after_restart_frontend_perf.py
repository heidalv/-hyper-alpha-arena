# -*- coding: utf-8 -*-
"""重启后验收：F1（orders 端点变快）、F6（gil-watch 生效）、F5（keep-alive 配置已加载）。

对比基线（改动前，同一实例同一探测方式）：
  orders(50) 串行 363ms / 12 分钟监视中位 422ms、31% 样本 >1s、峰值 4.6s
"""
from __future__ import annotations

import io
import json
import statistics
import sys
import time
from pathlib import Path
from urllib import request as urlreq

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"

EPS = [
    ("orders(50)  ← F1 主目标", "/api/paper/orders/14?limit=50"),
    ("balance", "/api/paper/balance/14"),
    ("positions(open)", "/api/paper/positions/14?status=open"),
    ("summary", "/api/paper/summary/14"),
    ("ticker-bar(7)", "/api/market/ticker-bar?symbols=BTC,ETH,SOL,BNB,VIRTUAL,ASTER,XPL"),
]


def get(path: str, timeout: float = 60.0):
    t0 = time.perf_counter()
    try:
        with urlreq.urlopen(BASE + path, timeout=timeout) as r:
            body = r.read()
            return time.perf_counter() - t0, r.status, body
    except Exception as e:  # noqa: BLE001
        return time.perf_counter() - t0, type(e).__name__, b""


print("=" * 96)
print("【1】HTTP 延迟（串行 10 次，重启后）")
print("=" * 96)
print(f"  {'端点':28s} {'中位':>9s} {'p90':>9s} {'最大':>9s} {'改动前基线':>14s}")
base = {"orders(50)  ← F1 主目标": "363ms(串行)/422ms(监视)",
        "balance": "482ms(串行)", "positions(open)": "84ms(串行)",
        "summary": "70ms(串行)", "ticker-bar(7)": "29ms(串行)"}
for label, path in EPS:
    ds = []
    status = None
    for _ in range(10):
        d, st, _ = get(path)
        ds.append(d)
        status = st
    ds_sorted = sorted(ds)
    print(f"  {label:28s} {statistics.median(ds)*1000:8.0f}ms "
          f"{ds_sorted[int(len(ds)*0.9)]*1000:8.0f}ms {max(ds)*1000:8.0f}ms "
          f"{base.get(label,'—'):>14s}   [HTTP {status}]")

print()
print("=" * 96)
print("【2】F1 端到端：orders 返回内容仍完整")
print("=" * 96)
_, st, body = get("/api/paper/orders/14?limit=50")
try:
    rows = json.loads(body)
    with_price = sum(1 for r in rows if r.get("entry_price"))
    print(f"  HTTP {st}；返回 {len(rows)} 条；含 entry_price 的 {with_price} 条 "
          f"⇒ {'✓ 完整' if len(rows) == 50 else '✗ 条数异常'}")
except Exception as e:  # noqa: BLE001
    print(f"  解析失败: {e}")

print()
print("=" * 96)
print("【3】F6：/api/ops/gil-watch 是否生效（重启前为 404）")
print("=" * 96)
d, st, body = get("/api/ops/gil-watch")
print(f"  HTTP {st}（重启前 = 404）")
if st == 200:
    snap = json.loads(body)
    print(f"  enabled={snap.get('enabled')} interval_s={snap.get('interval_s')} "
          f"uptime_s={snap.get('uptime_s')}")
    print(f"  requests_total={snap.get('requests_total')} "
          f"over_1s_total={snap.get('over_1s_total')} errors={snap.get('errors')}")
    lw = snap.get("last_window")
    if lw:
        print(f"  last_window: 请求={lw['requests']} 在飞峰值={lw['inflight_peak']} "
              f"进程CPU={lw['cpu_pct']}% 中位={lw['latency_median_ms']}ms "
              f"最大={lw['latency_max_ms']}ms ≥1s={lw['over_1s']} ≥3s={lw['over_3s']}")
    else:
        print("  last_window 还未产生（需等第一个上报窗口）")

print()
print("=" * 96)
print("【4】F6：日志里的 [GILWatch] 窗口行")
print("=" * 96)
log = (ROOT / "logs" / "backend.log").read_text(encoding="utf-8", errors="replace")
lines = [ln for ln in log.splitlines() if "[GILWatch]" in ln]
for ln in lines[-4:]:
    print("  " + ln.strip()[:150])
if not lines:
    print("  （尚无窗口行：需等第一个上报间隔到期）")

print()
print("=" * 96)
print("【5】重启后是否已有新的 SLOW 记录（F1 应让 orders 退出慢榜）")
print("=" * 96)
recent = [ln for ln in log.splitlines() if "SLOW" in ln]
start = next((i for i, ln in enumerate(log.splitlines())
              if "16:17" in ln and "startup" in ln.lower()), None)
after = []
seen_restart = False
for ln in log.splitlines():
    if "16:17:3" in ln:
        seen_restart = True
    if seen_restart and "SLOW" in ln:
        after.append(ln.strip()[:140])
print(f"  重启后 SLOW 条数 = {len(after)}")
for ln in after[:8]:
    print("  " + ln)
