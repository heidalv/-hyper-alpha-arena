"""T1 改动前后对照：按"引擎重启时刻"切窗口。

重启时刻从 logs/mm_lane_worker.log 里最后一条 `start lane=mm_asterdex` 解析。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"


def restart_ts():
    """从 worker 日志取最后一次启动时刻（本地时区字符串）。"""
    log = ROOT / "logs/mm_lane_worker.log"
    last = None
    pat = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[mm-worker\] start lane=mm_asterdex")
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat.match(line)
        if m:
            last = m.group(1)
    return last


def main() -> int:
    ts = restart_ts()
    print(f"引擎最后一次重启时刻（本地时区）= {ts}")
    with psycopg.connect(DSN, autocommit=True) as conn:
        cur = conn.cursor()
        for label, cond, params in (
            ("改动前（重启前 3 小时）", "ts < %s::timestamptz AND ts > %s::timestamptz - interval '3 hours'", (ts, ts)),
            ("改动后（重启至今）", "ts >= %s::timestamptz", (ts,)),
        ):
            print("\n" + "=" * 84)
            print(f"{label}   窗口起点={ts}")
            print("=" * 84)
            cur.execute(f"""
                SELECT coalesce(meta_json->>'exit_path','(none)') ep,
                       CASE WHEN coalesce(fee_bp,0) < -0.5 THEN 'TAKER' ELSE 'maker' END cls,
                       count(*) n,
                       round(avg(coalesce(net_bp,0))::numeric,2) avg_net,
                       round(sum(notional*net_bp/1e4)::numeric,3) net_usd,
                       round(sum(notional*fee_bp/1e4)::numeric,3) fee_usd
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND event='fill' AND {cond}
                GROUP BY 1,2 ORDER BY n DESC LIMIT 15
            """, params)
            rows = cur.fetchall()
            print(f"{'exit_path':<28}{'cls':<7}{'n':>6}{'avgNet':>9}{'net$':>10}{'fee$':>9}")
            for r in rows:
                print(f"{str(r[0])[:27]:<28}{r[1]:<7}{r[2]:>6}{str(r[3]):>9}{str(r[4]):>10}{str(r[5]):>9}")
            cur.execute(f"""
                SELECT count(*) n,
                       round(sum(notional*net_bp/1e4)::numeric,3) net_usd,
                       count(*) FILTER (WHERE coalesce(fee_bp,0) < -0.5) taker_legs,
                       round(sum(notional*fee_bp/1e4)::numeric,3) fee_usd,
                       count(*) FILTER (WHERE meta_json->>'flatten'='true') flat_n,
                       round(avg(coalesce(net_bp,0))::numeric,2) avg_net
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND event='fill' AND {cond}
            """, params)
            r = cur.fetchone()
            print("-" * 84)
            print(f"合计: 腿数={r[0]}  净额=${r[1]}  taker腿={r[2]}  费=${r[3]}  "
                  f"强平腿={r[4]}  均net_bp={r[5]}")
            # 每小时速率
            cur.execute(f"""
                SELECT EXTRACT(EPOCH FROM (max(ts)-min(ts)))/3600.0
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND event='fill' AND {cond}
            """, params)
            hrs = float(cur.fetchone()[0] or 0)
            if hrs > 0 and r[1] is not None:
                print(f"      跨度={hrs:.2f}h  ⇒ 速率 = ${float(r[1])/hrs:+.3f}/小时  "
                      f"（${float(r[1])/hrs*24:+.2f}/天）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
