# -*- coding: utf-8 -*-
"""[F286 2026-09-16] 链路连续性度量：车道 tick 覆盖率与断点分钟数（客观口径）。

动机：此前判断"链路断没断"只能靠 `backend.pid*.log` 的重启时刻 + 人工推断，
既不准也说不清（进程在跑 ≠ 车道在 tick）。F283 起 ticker 是**独立 worker**、
每次心跳都会在 `logs/mm_lane_worker.log` 留一行 `n=<累计拍数>` ⇒ 可以直接算出：
  · 观测窗口内应有拍数（按 15s 间隔）vs 实际拍数 ⇒ **覆盖率%**；
  · 相邻心跳时间差 > 阈值（默认 3× 间隔）⇒ 记一段**断点**，累计断点分钟数；
  · 顺带列出当日后端启动次数（外部监管者 churn 的客观读数）。

用法：`python scripts/mm_link_coverage.py [--day YYYY-MM-DD] [--interval 15]`
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "mm_lane_worker.log"
LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[mm-worker\] n=(\d+)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--interval", type=float, default=15.0, help="tick 间隔秒")
    ap.add_argument("--gap-mult", type=float, default=3.0, help="超过 间隔×该值 记为断点")
    a = ap.parse_args()

    pts = []
    if LOG.exists():
        for ln in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            m = LINE.match(ln)
            if m and m.group(1).startswith(a.day):
                pts.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), int(m.group(2))))
    print(f"[F286] day={a.day} worker 心跳点={len(pts)}（每行=20 拍）")
    # [修正] `n=` 是**进程内**累计拍数 ⇒ 换一个 worker 就会回到 1。
    # 只有同一会话内的 (t, n) 可比：按 n 回退切分会话，逐会话算覆盖率再汇总。
    sessions, cur = [], []
    for t, n in pts:
        if cur and n <= cur[-1][1]:
            sessions.append(cur)
            cur = []
        cur.append((t, n))
    if cur:
        sessions.append(cur)
    tot_expect = tot_actual = tot_lost = 0
    thr = a.interval * a.gap_mult
    gaps = []
    for s in sessions:
        span = (s[-1][0] - s[0][0]).total_seconds()
        expect = span / a.interval + 1
        actual = s[-1][1] - s[0][1] + 1
        tot_expect += expect
        tot_actual += actual
        for (t0, n0), (t1, n1) in zip(s, s[1:]):
            dt = (t1 - t0).total_seconds()
            miss = max(0, int(round(dt / a.interval)) - (n1 - n0))
            tot_lost += miss
            if dt > thr or miss > 0:
                gaps.append((t0, dt, miss))
        print(f"  会话 {s[0][0]:%H:%M:%S}~{s[-1][0]:%H:%M:%S} 应有≈{expect:.0f}/实际 {actual} 拍"
              f" ⇒ {actual/expect*100 if expect else 0:.1f}%")
    cov = tot_actual / tot_expect * 100 if tot_expect else 0.0
    print(f"  合计：应有≈{tot_expect:.0f} 拍 / 实际 {tot_actual} 拍 ⇒ 覆盖率 {cov:.1f}%；"
          f"断点段={len(gaps)} 丢失≈{tot_lost} 拍（≈{tot_lost*a.interval/60:.1f} 分钟）")
    for t0, dt, miss in gaps[:6]:
        print(f"    {t0:%H:%M:%S} 之后停 {dt/60:.1f} 分钟（丢 {miss} 拍）")
    runs = sorted(p for p in (ROOT / "logs").glob("backend.pid*.log")
                  if datetime.fromtimestamp(p.stat().st_ctime).strftime("%Y-%m-%d") == a.day)
    print(f"  当日后端启动次数={len(runs)}（外部监管者 churn 读数）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
