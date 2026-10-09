"""Reconcile data/flow_roundtrip_log.jsonl against lane_ledger.

Two instruments disagree about the current era:
  · roundtrip log : 66 roundtrips, mean y_bp = -16.32  (t=-3.49)
  · ledger        : A-band 250 legs, avg net_bp = -5.03
Both cannot describe the same thing. This finds out which is which, and
whether the log covers the same population.

Checks:
  1. time range + per-hour count of the log
  2. how many roundtrips the LEDGER implies in the same window
  3. paired roundtrip return computed FROM THE LEDGER, same window
  4. mean y_bp from each instrument, side by side
"""
from __future__ import annotations

import io
import json
import math
import statistics as st
import sys
import time

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE = "mm_asterdex"
ERA = "2026-10-05 15:57:56+08"


def main() -> int:
    rts = []
    for ln in open(ROOT + r"\data\flow_roundtrip_log.jsonl",
                   encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        try:
            rts.append((float(d["ts"]), float(d["hold_sec"]), float(d["y_bp"]),
                        str(d.get("why", "")), d.get("era", "")))
        except (KeyError, TypeError, ValueError):
            continue

    print("=" * 94)
    print("(1) roundtrip log 的时间覆盖")
    print("=" * 94)
    t0, t1 = min(r[0] for r in rts), max(r[0] for r in rts)
    print(f"  条数      : {len(rts)}")
    print(f"  时间范围  : {time.strftime('%m-%d %H:%M', time.localtime(t0))}"
          f"  →  {time.strftime('%m-%d %H:%M', time.localtime(t1))}")
    print(f"  跨度      : {(t1-t0)/3600:.1f} 小时")
    era_counts: dict = {}
    for r in rts:
        era_counts[r[4] or "(none)"] = era_counts.get(r[4] or "(none)", 0) + 1
    print(f"  era 分布  : {era_counts}")

    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT extract(epoch FROM min(ts)), extract(epoch FROM max(ts)),
                   count(*)
            FROM lane_ledger WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '{ERA}'::timestamptz
        """)
        lmin, lmax, ln_ = cur.fetchone()
        print()
        print("=" * 94)
        print("(2) 账本的时间覆盖（同一 ERA 窗口）")
        print("=" * 94)
        print(f"  腿数      : {int(ln_)}")
        print(f"  时间范围  : {time.strftime('%m-%d %H:%M', time.localtime(float(lmin)))}"
              f"  →  {time.strftime('%m-%d %H:%M', time.localtime(float(lmax)))}")

        # roundtrips implied by the ledger: pair each exit with its predecessor
        cur.execute(f"""
            SELECT ts, symbol, notional, coalesce(net_bp,0),
                   coalesce(meta_json->>'flatten','false')='true' AS is_exit
            FROM lane_ledger WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '{ERA}'::timestamptz ORDER BY ts
        """)
        rows = cur.fetchall()

    print()
    print("=" * 94)
    print("(3) 账本隐含的往返数 vs 日志条数")
    print("=" * 94)
    n_exit = sum(1 for r in rows if r[4])
    print(f"  账本里 flatten=true 的腿（≈出场腿）: {n_exit}")
    print(f"  日志条数                          : {len(rts)}")
    print("  说明：一次往返 = 1 进场腿 + 1 出场腿；")
    print(f"        若日志只记部分出场路径，就会少于账本出场腿数。")

    # exit path composition of ledger vs log 'why'
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT coalesce(meta_json->>'exit_path','(entry)'), count(*)
            FROM lane_ledger WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '{ERA}'::timestamptz
              AND coalesce(meta_json->>'flatten','false')='true'
            GROUP BY 1 ORDER BY 2 DESC
        """)
        lp = cur.fetchall()
    log_why: dict = {}
    for r in rts:
        log_why[r[3]] = log_why.get(r[3], 0) + 1
    print()
    print("  账本出场路径           次数 | 日志 why           次数")
    for i in range(max(len(lp), len(log_why))):
        a = f"{lp[i][0]:<22}{lp[i][1]:>5}" if i < len(lp) else " " * 27
        kb = sorted(log_why.items(), key=lambda x: -x[1])
        b = f"{kb[i][0]:<20}{kb[i][1]:>5}" if i < len(kb) else ""
        print(f"  {a} | {b}")

    print()
    print("=" * 94)
    print("(4) 同一窗口、同一口径的对比")
    print("=" * 94)
    cutoff = None
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT extract(epoch FROM max(ts)) FROM lane_ledger
            WHERE lane_id='{LANE}' AND event='fill' AND notional >= 1000
              AND ts >= '{ERA}'::timestamptz
        """)
        r0 = cur.fetchone()
        cutoff = float(r0[0]) if r0 and r0[0] else None
    recent = [r for r in rts if cutoff and r[0] >= cutoff]
    print(f"  小规模时代日志往返 n={len(recent)}")
    if recent:
        vals = [r[2] for r in recent]
        mu, sd = st.mean(vals), st.pstdev(vals)
        se = sd / math.sqrt(len(vals))
        print(f"    均值 {mu:+.2f}bp  SD {sd:.2f}  SE {se:.2f}  t={mu/se if se else 0:+.2f}")
        print(f"    中位 {st.median(vals):+.2f}bp   胜率 "
              f"{sum(1 for v in vals if v>0)/len(vals)*100:.0f}%")
        print(f"    总 y_bp 和 = {sum(vals):+.1f}bp（bp 之和，非美元）")
    print()
    print("  账本在**同一时间窗**的腿级净额：")
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT count(*), round(avg(notional)::numeric,0),
                   round(sum(notional*net_bp/1e4)::numeric,4),
                   round(avg(net_bp)::numeric,2)
            FROM lane_ledger WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= to_timestamp(%s)
        """, (cutoff,))
        n2, an2, s2, ab2 = cur.fetchone()
    print(f"    腿数 {int(n2)}  均名义 ${float(an2):,.0f}  "
          f"净额 ${float(s2):+.4f}  均 net_bp {float(ab2):+.2f}")
    print()
    print("  ⚠️ 关键区别：")
    print("     日志 y_bp = **每次往返的收益率**（简单平均，不看规模）")
    print("     账本 net_bp = **每腿的收益率**（一次往返含两条腿）")
    print("     若两者符号/量级不一致 ⇒ 有一方的口径理解有误，需查明。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
