"""方向门槛 A/B：`MM_FLOW_DIR_MIN` 对成交率与每腿质量的影响。

用法：
    python scripts/tools/ab_dir_gate.py --minutes 20 --label baseline
    python scripts/tools/ab_dir_gate.py --minutes 20 --label dirmin0

只读：把窗口内的关键读数打出来，供前后对照。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"


def read_heartbeat() -> dict:
    try:
        return json.loads((ROOT / "logs/mm_lane_status.json").read_text(
            encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=20)
    ap.add_argument("--label", default="window")
    args = ap.parse_args()

    with psycopg.connect(DSN, autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT count(*),
                   EXTRACT(EPOCH FROM (max(ts)-min(ts)))/60.0,
                   count(*) FILTER (WHERE fee_bp < -0.5),
                   coalesce(sum(notional*net_bp/1e4),0),
                   coalesce(sum(notional*fee_bp/1e4),0),
                   coalesce(avg(net_bp),0)
            FROM lane_ledger
            WHERE lane_id='mm_asterdex' AND event='fill'
              AND ts > now() - interval '{int(args.minutes)} minutes'
        """)
        legs, mins, taker, net_usd, fee_usd, avg_net = cur.fetchone()
        mins = float(mins or 0.0)

        print("=" * 76)
        print(f"[{args.label}]  最近 {args.minutes} 分钟")
        print("=" * 76)
        print(f"  腿数            : {legs}")
        print(f"  跨度            : {mins:.1f} 分钟")
        print(f"  成交率          : {legs / max(mins, 1e-9) * 60:.1f} 腿/小时")
        print(f"  taker 腿        : {taker}")
        print(f"  手续费          : ${float(fee_usd):.3f}")
        print(f"  净额            : ${float(net_usd):.3f}")
        print(f"  每小时净额      : ${float(net_usd) / max(mins / 60.0, 1e-9):.2f}")
        print(f"  每腿均 net_bp   : {float(avg_net):.2f}")

        hb = read_heartbeat()
        sk = hb.get("skip_counts") or {}
        sc = hb.get("side_counts") or {}
        total_dec = sum(v for v in sk.values() if isinstance(v, (int, float)))
        print()
        print("  心跳 skip_counts（占比）:")
        for k, v in sorted(sk.items(), key=lambda kv: -(kv[1] or 0))[:10]:
            pct = (v / total_dec * 100) if total_dec else 0
            print(f"    {k:<22}{v:>8}  {pct:>5.1f}%")
        print(f"  side_counts     : {sc}")
        print(f"  day_pnl_usd     : {hb.get('day_pnl_usd')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
