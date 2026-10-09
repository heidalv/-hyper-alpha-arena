# -*- coding: utf-8 -*-
"""[h739 2026-10-03] 腿速深度研究:成交漏斗逐层量化。

漏斗:
  L1 市场流(笔/h)→ L2 穿越我方挂单价的桶(15s 桶内有一笔 ≤ 我方买价 的 taker 卖,
     或 ≥ 我方卖价 的 taker 买)→ L3 我方实际成交(h739 用账本)。
我方挂单价口径:mid∓2.3bp(ev_width_cap_bp=2.5 的实际均值 2.29/2.37)。
输出逐币对比 + 漏斗缺口(覆盖率缺口 = L2−L3 的差额由挂单缺口/闸门/队列份额解释)。
"""
from __future__ import annotations

import importlib.util
import io
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SYMS = ["NEAR", "ARB", "WLD", "1000SHIB", "PENDLE", "VIRTUAL",
        "ENA", "LINK", "ADA", "SUI", "ONDO", "AAVE", "SEI"]


def _market_dsn() -> str:
    from backend.services.market_maker.attribution import _market_dsn as f
    return f()


def _lane_dsn() -> str:
    _spec = importlib.util.spec_from_file_location("h425", ROOT / "scripts" / "h425_repair_trial.py")
    _h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    return _h.read_env_dsn()


def main() -> int:
    import psycopg

    hours = 2.0
    since = time.time() - hours * 3600
    W_BP = 2.3
    print(f"== 腿速漏斗(近 {hours}h,我方挂单 ±{W_BP}bp) ==\n")
    print(f"  {'币':<10}{'市场笔/h':>9}{'打中桶/h':>9}{'我方腿/h':>9}{'我方/打中':>9}{'我方/市场':>9}")
    rows = []
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        for s in SYMS:
            sym = s + "USDT"
            cur.execute(
                "SELECT count(*) FROM asterdex_trades WHERE symbol=%s"
                " AND event_ts_ms >= %s", (sym, int(since * 1000)))
            n_tr = int(cur.fetchone()[0] or 0)
            cur.execute(
                "SELECT event_ts_ms, price FROM asterdex_trades WHERE symbol=%s"
                " AND event_ts_ms >= %s ORDER BY event_ts_ms",
                (sym, int(since * 1000)))
            trades = [(float(r[0]) / 1000.0, float(r[1])) for r in cur.fetchall()]
            # 每 15s 桶:是否有一笔 taker 卖 ≤ mid−2.3bp 或 taker 买 ≥ mid+2.3bp
            # mid 用桶内成交均价近似(或首笔价格);买/卖方向无标签,用"价格相对
            # 桶内中位"粗分:低于桶中位 = 卖压成交(打我方买价),高于 = 打我方卖价。
            # 更严谨:比较每笔价格与该时刻前一笔价格的关系不可行;采用桶极值法:
            #   桶最低价 ≤ 桶首价×(1−W/1e4) ⇒ 有打我方买价的动作(下行穿越)
            #   桶最高价 ≥ 桶首价×(1+W/1e4) ⇒ 有打我方卖价的动作(上行穿越)
            hit_buckets = 0
            bucket = {}
            for ts, px in trades:
                b = int(ts / 15.0)
                if b not in bucket:
                    bucket[b] = {"first": px, "lo": px, "hi": px, "n": 0}
                e = bucket[b]
                e["n"] += 1
                e["lo"] = min(e["lo"], px)
                e["hi"] = max(e["hi"], px)
            for e in bucket.values():
                if e["n"] < 1:
                    continue
                f = e["first"]
                if f <= 0:
                    continue
                if e["lo"] <= f * (1.0 - W_BP / 1e4) or e["hi"] >= f * (1.0 + W_BP / 1e4):
                    hit_buckets += 1
            rows.append((s, n_tr / hours, hit_buckets / hours, n_tr))
    with psycopg.connect(_lane_dsn(), autocommit=True) as c, c.cursor() as cur:
        for i, (s, tr_h, hit_h, _) in enumerate(rows):
            cur.execute(
                "SELECT count(*) FROM lane_ledger WHERE lane_id=%s AND symbol=%s"
                " AND event='fill' AND ts >= to_timestamp(%s)",
                ("mm_asterdex", s, since))
            our_h = int(cur.fetchone()[0] or 0) / hours
            ratio_hit = our_h / hit_h if hit_h > 0 else 0.0
            ratio_tr = our_h / tr_h if tr_h > 0 else 0.0
            rows[i] = (s, tr_h, hit_h, our_h, ratio_hit, ratio_tr)
            print(f"  {s:<10}{tr_h:>9.0f}{hit_h:>9.0f}{our_h:>9.1f}"
                  f"{ratio_hit*100:>8.0f}%{ratio_tr*100:>8.1f}%")
    tot_tr = sum(r[1] for r in rows)
    tot_hit = sum(r[2] for r in rows)
    tot_our = sum(r[3] for r in rows)
    print(f"\n  合计:市场 {tot_tr:.0f}/h | 打中桶 {tot_hit:.0f}/h | 我方 {tot_our:.0f}/h"
          f" | 我方/打中 = {tot_our/tot_hit*100 if tot_hit else 0:.0f}%"
          f" | 我方/市场 = {tot_our/tot_tr*100 if tot_tr else 0:.0f}%")
    print("""
  解读:我方/打中 的缺口 = 挂单未覆盖(单侧封锁/无挂单)+ 15s 桶内挂单时刻错位
       + 队列份额(0.30,但那是"量"的份额,腿事件只要覆盖+穿越就会记)。
  我方/市场 的极限由 打中桶 决定(约 50% 的桶有穿越动作,上限 ~打中桶数)。
""")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
