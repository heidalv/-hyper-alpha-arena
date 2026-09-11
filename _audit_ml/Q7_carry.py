# -*- coding: utf-8 -*-
"""资金费 carry 可行性评估（第十轮）。

对 asterdex 近 30 天 funding 数据，按币计算：
  - 平均/中位 8h 费率、正费率占比、年均化
  - 连续负费率段（carry 反转风险）
  - 30 天净 carry（毛 - 4 腿执行成本 40bp）

用法：.venv\\Scripts\\python.exe _audit_ml/Q7_carry.py
"""
from __future__ import annotations

import statistics as st
import time
from collections import defaultdict

from sqlalchemy import create_engine, text

eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
CUTOFF = int((time.time() - 30 * 86400) * 1000)
ROUNDTRIP_COST_PCT = 0.40  # 4 腿 × 10bp（taker 5bp + 滑点 5bp）

CAND = ["XMR", "B", "XIAOMI", "ESPORTS", "AIO", "AI", "NATGAS",
        "TRADOOR", "ZCAT", "PAIR", "STONK", "MAX"]


def main() -> None:
    with eng.connect() as c:
        c.execute(text("set statement_timeout='600000'"))
        rows = c.execute(text(
            "select symbol, timestamp, funding_rate from perp_funding "
            "where exchange='asterdex' and symbol = any(:syms) and timestamp > :cut "
            "order by symbol, timestamp"
        ), {"syms": CAND, "cut": CUTOFF}).fetchall()

    by: dict[str, list[float]] = defaultdict(list)
    for s, _t, fr in rows:
        try:
            by[s].append(float(fr))
        except (TypeError, ValueError):
            continue

    print(f"{'币':<10}{'n':>6}{'均值%':>9}{'中位%':>9}{'正占比':>8}{'年均化%':>9}{'负段数':>8}{'最长负段':>9}")
    for s in CAND:
        v = by.get(s) or []
        if len(v) < 100:
            continue
        m, med = st.mean(v), st.median(v)
        pos = sum(1 for r in v if r > 0) / len(v)
        runs, cur = [], 0
        for r in v:
            if r < 0:
                cur += 1
            else:
                if cur:
                    runs.append(cur)
                cur = 0
        if cur:
            runs.append(cur)
        print(f"{s:<10}{len(v):>6}{m*100:>9.5f}{med*100:>9.5f}{pos:>8.3f}"
              f"{m*3*365*100:>9.2f}{len(runs):>8}{max(runs) if runs else 0:>9}")

    print("\n=== 30 天净 carry（持有 30 天，成本 4 腿 × 10bp = 40bp）===")
    print(f"{'币':<10}{'毛carry%':>10}{'净carry%':>10}{'成本占比%':>11}")
    for s in CAND:
        v = by.get(s) or []
        if len(v) < 100:
            continue
        m = st.mean(v)
        gross = m * 3 * 30 * 100
        net = gross - ROUNDTRIP_COST_PCT
        print(f"{s:<10}{gross:>10.2f}{net:>10.2f}"
              f"{(ROUNDTRIP_COST_PCT / gross * 100 if gross else 0):>11.1f}")

    print("\n=== 全市场（样本内所有币）===")
    allv = [x for v in by.values() for x in v]
    if allv:
        print(f"  均值 {st.mean(allv)*100:.5f}% / 中位 {st.median(allv)*100:.5f}% / "
              f"正占比 {sum(1 for x in allv if x>0)/len(allv):.3f}")


if __name__ == "__main__":
    main()
