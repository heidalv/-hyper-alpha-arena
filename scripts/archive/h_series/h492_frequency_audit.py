"""h492：**≥60 腿/h 硬约束的合规审计与阻塞归因**。

背景：用户硬约束是"≥60 腿/h"。实测近 15/30/60min = 60.0/62.0/60.0 腿/h
⇒ **恰好压在地板上**；而 h465 的小时表里夜间的 22:00(42)、23:00(50)、16:00(56)、
21:00(58) 等若干小时**本就在地板之下** ⇒ 这不是本次改动造成的，是常态。
这个事实直接决定 ③（ofi_confirm 0.5→0.9，保留 0.92× 腿量）的夜间风险。

本脚本回答两个问题（只读）：
  Q1 近 N 天里有多少小时 <60 腿/h？按**本地钟点**分布如何（安静窗口在哪）？
  Q2 那些安静小时里，**主要是哪道闸**在拦？（用 worker 心跳的累计 skip 计数做差分，
     按 tick 归一化 ⇒ "每 100 tick 被该闸拦下多少次"，可比不同时段）

⚠️ 心跳计数是**每进程累计**，重启会归零 ⇒ 本脚本按 Δ<0 断链，不跨重启求增量。

用法：python scripts/h492_frequency_audit.py [--hours 168]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
LOG = ROOT / "logs" / "mm_lane_worker.log"
OUT = ROOT / "research_l1" / "out" / "h492_frequency_audit.json"
LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[mm-worker\] .*?"
    r"ticks=(?P<ticks>\d+)(?:.*?fills=(?P<fills>\d+))?.*?skip=\{(?P<skip>.*)\}\s*$")
KV = re.compile(r"'([^']+)':\s*(\d+)")


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    a = ap.parse_args()
    # ── Q1 小时级腿速 ──
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT date_trunc('hour', ts) AS h, count(*) FROM lane_ledger "
                "WHERE lane_id=%s AND ts > now() - make_interval(hours => %s::int) "
                "GROUP BY 1 ORDER BY 1", (LANE, a.hours))
            rows = cur.fetchall()
    print(f"近 {a.hours:.0f}h 小时级腿速（lane={LANE}）：共 {len(rows)} 个整点小时")
    under = [(h, n) for h, n in rows if n < 60]
    print(f"  <60 腿/h 的小时 = {len(under)}/{len(rows)}"
          f"（{100.0*len(under)/max(len(rows),1):.0f}%）")
    print("=" * 92)
    print(f"{'本地钟点':>8s} {'小时数':>6s} {'≥60 的小时':>10s} {'<60 的小时':>10s} "
          f"{'腿速中位':>9s} {'最低':>6s}")
    by_clock: dict = {}
    for h, n in rows:
        local = h.astimezone() if h.tzinfo else h
        by_clock.setdefault(local.hour, []).append(int(n))
    for clock in sorted(by_clock):
        v = sorted(by_clock[clock])
        med = v[len(v) // 2]
        ge = sum(1 for x in v if x >= 60)
        print(f"{clock:>6d}:00 {len(v):6d} {ge:10d} {len(v)-ge:10d} "
              f"{med:9d} {v[0]:6d}")
    # ── Q2 阻塞归因（心跳差分）──
    print("=" * 92)
    if not LOG.exists():
        print("无 worker 日志，跳过阻塞归因")
    else:
        recs = []
        for ln in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            m = LINE.match(ln.strip())
            if not m:
                continue
            skip = {k: int(v) for k, v in KV.findall(m.group("skip"))}
            recs.append({"ts": m.group("ts"), "ticks": int(m.group("ticks")),
                         "skip": skip})
        intervals = []
        for p, q in zip(recs, recs[1:]):
            dt = q["ticks"] - p["ticks"]
            if dt <= 0 or dt > 200:        # 重启/断流 ⇒ 丢弃，不跨重启求增量
                continue
            d = {k: q["skip"].get(k, 0) - p["skip"].get(k, 0)
                 for k in set(p["skip"]) | set(q["skip"])}
            if any(v < 0 for v in d.values()):
                continue
            intervals.append({"clock": int(p["ts"][11:13]), "ticks": dt, "d": d})
        print(f"心跳区间（可用）= {len(intervals)}（已剔除跨重启/断流）")
        agg: dict = {}
        for it in intervals:
            cl = it["clock"]
            bucket = "安静(00-08)" if cl < 8 else ("活跃(08-20)" if cl < 20 else "夜(20-24)")
            g = agg.setdefault(bucket, {"ticks": 0, "d": {}})
            g["ticks"] += it["ticks"]
            for k, v in it["d"].items():
                g["d"][k] = g["d"].get(k, 0) + v
        for bucket, g in agg.items():
            t = max(g["ticks"], 1)
            top = sorted(g["d"].items(), key=lambda kv: -kv[1])[:8]
            print(f"\n{bucket}：{g['ticks']} tick 样本，每 100 tick 的拦截次数：")
            for k, v in top:
                print(f"    {k:22s} {100.0*v/t:6.1f}")
        OUT.write_text(json.dumps(
            {"hours": len(rows), "under_60": len(under),
             "by_clock": {str(k): v for k, v in by_clock.items()},
             "skip_by_bucket": {b: {"ticks": g["ticks"], "d": g["d"]}
                                for b, g in agg.items()}},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n已写:", OUT.relative_to(ROOT))
    # 结论
    share = 100.0 * len(under) / max(len(rows), 1)
    if share <= 10:
        print(f"\n⇒ 合规良好：仅 {share:.0f}% 的小时 <60 腿/h")
    elif share <= 40:
        print(f"\n⇒ **合规有缺口**：{share:.0f}% 的小时 <60 腿/h ⇒ 夜间常态性低于硬约束；"
              f"任何进一步压低腿速的改动（如 ③ 的 0.92× / 0.84×）都要按此折减后再评估风险")
    else:
        print(f"\n⇒ **合规不达标**：{share:.0f}% 的小时 <60 腿/h ⇒ 需要先解决产能，"
              f"而不是继续做「减腿换质量」的改动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
