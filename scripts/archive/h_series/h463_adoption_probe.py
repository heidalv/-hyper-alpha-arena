"""h463 采纳验证 + 前提量化（只读）：用心跳里的 `timeout_maker_only` 速率。

原理（runner.py:1289-1327）：
  每当「持仓年龄 > `_effective_hold_sec()`」成立，本 tick 会
  `dec.skip = "timeout_maker_only"`（只封锁加仓侧、不 taker），
  心跳里的 `skip_counts.timeout_maker_only` 是**累计**计数。
  ⇒ 该计数的**每 tick 增速** = "在仓且已超有效持有期"的时间占比。

有效持有期：P1→`p1_hold_sec`=60s，P45→`p45_hold_sec`=300s，无标记→`max_one_side_seconds`。
把 `max_one_side_seconds` 由 45 改 90 ⇒ **无标记仓在 45~90s 这段不再计入** ⇒
增速应出现一个**可测的下降**，下降幅度即 h463 实际触及的时间占比。

本脚本解析 `logs/mm_lane_worker.log` 的心跳序列，输出：
  1. 逐小时：ticks 增量、`timeout_maker_only` 增量、每 100 tick 的速率；
  2. 部署时刻（2026-09-28T18:20Z = 本地 02:20）前后的速率对比。

用法：python scripts/h463_adoption_probe.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "mm_lane_worker.log"
OUT = ROOT / "research_l1" / "out" / "h463_adoption_probe.json"
DEPLOY_UTC = dt.datetime(2026, 9, 28, 18, 20, 35, tzinfo=dt.timezone.utc)

# 心跳样例行：
# 2026-09-29 01:23:27 [mm-worker] n=261 ok=True reason= ticks=261 fills=90
#   last_tick=... skip={'trend_down': 325, 'trend_only_flat': 184, ...}
LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[mm-worker\] .*?"
    r"ticks=(?P<ticks>\d+).*?skip=\{(?P<skip>.*)\}\s*$")
KV = re.compile(r"'([^']+)':\s*(\d+)")


def main() -> int:
    if not LOG.exists():
        print("无 worker 日志")
        return 1
    rows = []
    unparsed = 0
    for ln in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(ln.strip())
        if not m:
            if "[mm-worker]" in ln:
                unparsed += 1
            continue
        skip = {k: int(v) for k, v in KV.findall(m.group("skip"))}
        ts = dt.datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=dt.timezone(dt.timedelta(hours=8)))  # 日志为本地时间 UTC+8
        rows.append({"ts": ts.astimezone(dt.timezone.utc), "ticks": int(m.group("ticks")),
                     "tmo": skip.get("timeout_maker_only", 0), "skip": skip})
    print(f"解析心跳 {len(rows)} 条（未解析 {unparsed} 条）")
    if len(rows) < 3:
        return 1
    print(f"覆盖 {rows[0]['ts']:%Y-%m-%d %H:%M}Z → {rows[-1]['ts']:%Y-%m-%d %H:%M}Z")
    print("=" * 84)
    print(f"{'本地时刻':>17s} {'Δtick':>7s} {'Δ超时封锁':>10s} {'每100tick':>10s} "
          f"{'Δ成交':>7s} {'阶段':>8s}")
    hourly = []
    for a, b in zip(rows, rows[1:]):
        dtick = b["ticks"] - a["ticks"]
        dtmo = b["tmo"] - a["tmo"]
        if dtick <= 0:
            continue
        rate = 100.0 * dtmo / dtick
        phase = "post-h463" if a["ts"] >= DEPLOY_UTC else "pre-h463"
        hourly.append({"ts": a["ts"].isoformat(), "dticks": dtick, "dtmo": dtmo,
                       "per100": round(rate, 2), "phase": phase})
        print(f"{a['ts'].astimezone(dt.timezone(dt.timedelta(hours=8))):%m-%d %H:%M:%S} "
              f"{dtick:7d} {dtmo:10d} {rate:10.2f} "
              f"{b['skip'].get('__fills', 0) - a['skip'].get('__fills', 0):7d} {phase:>8s}")
    pre = [h for h in hourly if h["phase"] == "pre-h463"]
    post = [h for h in hourly if h["phase"] == "post-h463"]
    def agg(v):
        t = sum(x["dticks"] for x in v)
        m = sum(x["dtmo"] for x in v)
        return t, m, (100.0 * m / t if t else float("nan"))
    print("=" * 84)
    for name, v in (("pre-h463 ", pre), ("post-h463", post)):
        t, m, r = agg(v)
        print(f"{name}: 区间数={len(v):3d} ticks={t:8d} 超时封锁={m:8d} "
              f"每100tick={r:6.2f}")
    if pre and post:
        _t1, _m1, r1 = agg(pre)
        _t2, _m2, r2 = agg(post)
        drop = r1 - r2
        print(f"⇒ 速率变化 {r1:.2f} → {r2:.2f}（每100tick），"
              f"绝对 {drop:+.2f}，相对 {100.0*drop/r1 if r1 else float('nan'):+.1f}%")
        print("   解读：h463 把无标记仓的封锁年龄 45→90s，"
              "故 45~90s 那段不再计入 ⇒ 速率下降幅度 ≈ h463 触及的在仓时间占比。")
    OUT.write_text(json.dumps(
        {"rows": len(rows), "hourly": hourly[-48:],
         "pre": agg(pre), "post": agg(post)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
