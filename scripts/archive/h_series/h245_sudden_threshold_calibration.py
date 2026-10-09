# -*- coding: utf-8 -*-
"""H245 突发事件阈值的量级校准：按**引擎 tick 周期**（不是秒）算触发率。

# 为什么必须单独校准

F342 的 `sudden_move_bp` 判据是 `sudden_move_bp(mid_hist, k)`，
而 `mid_hist` 是**每个快照追加一条**（`last_mid_src_ms` 去重），
实测 tick 周期 **18~20 秒**（F334 之后 market_flow 变慢）。

⇒ `k=1` 测的是"最近 ~19 秒的净移动"，**不是 1 秒**。
H244 用的 W=10s 窗口 ⇒ 与引擎的实际窗口**不一致**，
直接拿它的 50bp 当阈值会**触发率对不上**。

本脚本用**真实 tick 网格**（按 19s 重采样）算：

    逐 tick 的 |净移动| 分布（P50/P90/P99/P99.9/max）
    各候选阈值下的**触发次数/天**

⇒ 给出"每天触发几次"这个唯一能判断阈值是否合理的量。

# 用法

    python scripts/h245_sudden_threshold_calibration.py --hours 24 --tick-sec 19
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h245_sudden_threshold.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
CANDS = [15, 20, 30, 40, 50, 60, 80, 100, 120]


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def q(v, p):
    if not v:
        return 0.0
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--tick-sec", type=float, default=19.0,
                    help="引擎 tick 周期（实测 18~20s）")
    ap.add_argument("--cooldown-sec", type=float, default=120.0,
                    help="F342 的 sudden_move_cooldown_sec（用于算有效触发次数）")
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, event_ts_ms, (bid_px + ask_px) / 2.0
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND symbol = ANY(%s) AND bid_px > 0 AND ask_px > bid_px
                ORDER BY symbol, event_ts_ms ASC
            """, (str(float(a.hours) + 0.2), CUR))
            rows = cur.fetchall()
    if not rows:
        print("无数据")
        return 1

    # 按 tick-sec 网格取末值（模拟引擎的 mid_hist 追加节奏）
    g = {}
    for sym, ms, mid in rows:
        g.setdefault(str(sym), {})[int(ms / 1000.0 / a.tick_sec)] = float(mid)
    series = {}
    for s, d in g.items():
        ks = sorted(d)
        series[s] = (ks, [d[k] for k in ks])

    print("=" * 100)
    print("H245  突发事件阈值校准（按引擎 tick 周期）")
    print("=" * 100)
    print(f"\n  窗口 {a.hours:g}h　tick 网格 {a.tick_sec:g}s（模拟 mid_hist 追加）"
          f"　冷却 {a.cooldown_sec:g}s")

    per_sym = {}
    allsteps = []
    for sym, (ks, px) in sorted(series.items()):
        steps = []
        for t in range(1, len(px)):
            if px[t - 1] > 0:
                steps.append(abs(px[t] - px[t - 1]) / px[t - 1] * 1e4)
        per_sym[sym] = steps
        allsteps += steps
        # ⚠️ `ks` 存的是**桶号**（`int(ms/1000/tick_sec)`），不是秒！
        # 初版写成 `(ks[-1]-ks[0])/3600` ⇒ 少乘了 tick 周期 ⇒ 跨度被低估 19 倍
        # （实测报 1.27h，而真值是 24.2h）⇒ **触发率被高估 19 倍**。
        span_h = (ks[-1] - ks[0]) * a.tick_sec / 3600.0 if len(ks) > 1 else 0
        print(f"  {sym:12} {len(steps):>6} 个 tick 步　跨度 {span_h:.1f}h")
    if not allsteps:
        print("无 tick 步")
        return 1

    print(f"\n{'━'*100}\n  一、逐 tick |净移动| 分布（tick 周期 {a.tick_sec:g}s）\n{'━'*100}")
    print(f"\n  {'币':<14}{'P50':>9}{'P90':>9}{'P99':>9}{'P99.9':>9}{'max':>10}"
          f"{'P99.9/P50':>11}")
    for sym, steps in sorted(per_sym.items()):
        if not steps:
            continue
        p50, p999 = q(steps, 50), q(steps, 99.9)
        print(f"  {sym:<14}{p50:>9.3f}{q(steps,90):>9.3f}{q(steps,99):>9.3f}"
              f"{p999:>9.3f}{max(steps):>10.2f}"
              f"{(p999/p50 if p50 else 0):>11.1f}×")

    print(f"\n{'━'*100}\n  二、候选阈值的**触发率**（这是判断阈值合理性的唯一量）\n{'━'*100}")
    total_h = sum((v[0][-1] - v[0][0]) * a.tick_sec / 3600.0
                  for v in series.values() if len(v[0]) > 1)
    print(f"\n  总观测时长（跨币累加）= {total_h:.1f} 币·小时")
    print(f"\n  {'阈值bp':>8}{'原始命中次数':>14}{'命中率':>9}{'次/天/币':>11}"
          f"{'**有效触发/天**':>16}  说明")
    for thr in CANDS:
        raw = 0
        eff = 0
        for sym, (ks, px) in series.items():
            last = -1e18
            for t in range(1, len(px)):
                if px[t - 1] <= 0:
                    continue
                mv = abs(px[t] - px[t - 1]) / px[t - 1] * 1e4
                if mv >= thr:
                    raw += 1
                    if ks[t] - last >= a.cooldown_sec:
                        eff += 1
                        last = ks[t]
        rate = raw / max(1, len(allsteps)) * 100
        per_day_sym = raw / total_h * 24 if total_h else 0
        eff_day = eff / total_h * 24 if total_h else 0
        note = ("太频繁（几乎每 tick）" if per_day_sym > 200 else
                "频繁" if per_day_sym > 50 else
                "合理" if per_day_sym > 3 else
                "**太稀疏（几乎不触发）**")
        print(f"  {thr:>8}{raw:>14}{rate:>8.3f}%{per_day_sym:>11.1f}"
              f"{eff_day:>16.1f}  {note}")

    print(f"\n{'━'*100}\n  三、建议\n{'━'*100}")
    ok = []
    for thr in CANDS:
        eff = 0
        for sym, (ks, px) in series.items():
            last = -1e18
            for t in range(1, len(px)):
                if px[t - 1] <= 0:
                    continue
                if abs(px[t] - px[t - 1]) / px[t - 1] * 1e4 >= thr:
                    if ks[t] - last >= a.cooldown_sec:
                        eff += 1
                        last = ks[t]
        d = eff / total_h * 24 if total_h else 0
        if 1.0 <= d <= 30.0:
            ok.append((thr, d))
    if ok:
        print(f"\n  「有效触发 1~30 次/天」的阈值：")
        for thr, d in ok:
            print(f"    sudden_move_bp = {thr}  ⇒  **{d:.1f} 次/天**")
        print(f"\n  ⇒ 建议取**中间偏保守**的一个（触发太少则保护不足，太多则等于常关）")
    else:
        print(f"\n  ⚠️ 没有阈值落在 1~30 次/天 ⇒ 需要调整冷却期或重新评估")

    print(f"\n  ⚠️ 口径提醒：")
    print(f"     · 本脚本用 `tick-sec={a.tick_sec:g}s` 网格**近似** mid_hist，")
    print(f"       而引擎实际只在**快照更新时**追加（有去重）⇒ 真实步长可能更长，")
    print(f"       同样阈值下**真实触发率会更低**。上线后以 `skip_counts.sudden_move` 实测为准。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": a.hours, "tick_sec": a.tick_sec, "cooldown_sec": a.cooldown_sec,
        "total_symbol_hours": round(total_h, 2),
        "steps_n": len(allsteps),
        "p50": round(q(allsteps, 50), 4), "p90": round(q(allsteps, 90), 4),
        "p99": round(q(allsteps, 99), 4), "p999": round(q(allsteps, 99.9), 4),
        "max": round(max(allsteps), 3),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
