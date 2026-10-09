# -*- coding: utf-8 -*-
"""H210 捕获/逆向选择 比率是否稳定 —— 决定"算术目标"能不能立。

# 为什么这一步决定一切

H209 得出：

    30 小时、10,557 笔 maker 腿：
        spread 捕获   +0.2391 bp
        逆向选择      −0.4421 bp
        比率 = 0.558        ← <1 ⇒ 结构性为负
        corr(spread, price) = +0.041   ← 两者独立

由此推出的动作是"把捕获做大"（比率升到 1 以上即转正）。

**但这个推理只有在比率本身是结构量时才成立。**
本会话已三次证明：看似漂亮的关系换个窗口就反向
（OFI、阈值扫描、成交规模）。
⇒ **必须先证明 0.558 在多个时间窗里稳定**，否则它也只是 regime 的产物，
   拿它当目标等于又一次过拟合。

# 判据

把 30 小时按腿数四等分，逐段算比率：

    · 若 4 段都在 0.5 附近 ⇒ **结构量** ⇒ "把捕获做大"是正确方向
    · 若各段差异很大（如 0.2 ~ 1.5）⇒ 比率本身随 regime 变 ⇒ 不可作目标
      ⇒ 应改为"逐 regime 决策"，而不是设一个全局目标

# 用法

    python scripts/h210_ratio_stability.py --hours 30
    python scripts/h210_ratio_stability.py --hours 30 --segments 6
"""
from __future__ import annotations

import argparse
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn() -> str:
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
    ap.add_argument("--hours", type=float, default=30.0)
    ap.add_argument("--segments", type=int, default=4)
    ap.add_argument("--symbol", default="")
    a = ap.parse_args()

    import psycopg

    k = int(a.segments)
    sym_clause = " AND symbol=%s" if a.symbol else ""
    base_args = [LANE, int(a.hours)] + ([a.symbol] if a.symbol else [])

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 240000")
            # ① 取时间界的**序号**（percentile_cont 不支持 timestamptz，改用排序后取第 n 条）
            cur.execute(f"""
                WITH f AS (
                  SELECT ts, row_number() OVER (ORDER BY ts) AS rn,
                         count(*) OVER () AS n
                  FROM lane_ledger
                  WHERE lane_id=%s AND ts > now() - make_interval(hours => %s::int)
                    AND meta_json->>'flatten' NOT IN ('true','True')
                    {sym_clause}
                )
                SELECT ts FROM f WHERE rn IN (
                  SELECT (n * i / {k})::bigint FROM generate_series(1, {k-1}) AS i
                ) ORDER BY ts
            """, base_args)
            bounds = [r[0] for r in cur.fetchall()]

            # ② 逐段聚合
            segs = []
            lo = None
            for i in range(k):
                hi = bounds[i] if i < len(bounds) else None
                cond = "AND ts > %s" if lo is not None else ""
                cond2 = "AND ts <= %s" if hi is not None else ""
                args = list(base_args) + ([lo] if lo is not None else []) + \
                       ([hi] if hi is not None else [])
                cur.execute(f"""
                    SELECT count(*),
                           coalesce(sum(spread_bp*notional)/NULLIF(sum(notional),0),0),
                           coalesce(sum(price_bp*notional)/NULLIF(sum(notional),0),0),
                           coalesce(sum(net_bp*notional)/NULLIF(sum(notional),0),0),
                           min(ts), max(ts)
                    FROM lane_ledger
                    WHERE lane_id=%s AND ts > now() - make_interval(hours => %s::int)
                      AND meta_json->>'flatten' NOT IN ('true','True')
                      {sym_clause} {cond} {cond2}
                """, args)
                n, wsp, wpx, wnet, t0, t1 = cur.fetchone()
                if n and int(n) > 0:
                    segs.append({"n": int(n), "sp": float(wsp or 0),
                                 "px": float(wpx or 0), "net": float(wnet or 0),
                                 "t0": t0, "t1": t1})
                lo = hi

    print("=" * 96)
    print("H210  捕获/逆向选择 比率的跨窗口稳定性")
    print("=" * 96)
    print(f"  窗口 {a.hours:.0f}h   切成 {k} 段"
          f"{'   币=' + a.symbol if a.symbol else ''}\n")
    print(f"  {'段':<22}{'腿数':>7}{'加权spread':>12}{'加权price':>12}"
          f"{'比率':>8}{'加权net':>10}")
    print("  " + "-" * 74)
    ratios = []
    for s in segs:
        r = (s["sp"] / abs(s["px"])) if s["px"] else float("nan")
        ratios.append(r)
        lab = f"{s['t0']:%m-%d %H:%M}→{s['t1']:%H:%M}"
        print(f"  {lab:<22}{s['n']:>7}{s['sp']:>+12.4f}{s['px']:>+12.4f}"
              f"{r:>8.3f}{s['net']:>+10.4f}")

    tot_n = sum(s["n"] for s in segs)
    if tot_n:
        # 全样本（名义加权需要重新算，这里用腿数近似加权不足；改为直接报各段跨度）
        pass

    print()
    valid = [r for r in ratios if r == r]        # 排除 nan
    if len(valid) >= 2:
        lo, hi = min(valid), max(valid)
        med = st.median(valid)
        spread = hi - lo
        print(f"  比率：中位 {med:.3f}   范围 [{lo:.3f}, {hi:.3f}]   跨度 {spread:.3f}")
        print()
        # 判据：跨度相对中位多大？符号是否一致？
        rel = spread / abs(med) if med else 999
        all_below1 = all(r < 1 for r in valid)
        print(f"  相对跨度 = {rel:.2f}（跨度/中位）")
        print(f"  各段是否都 < 1（即都结构性为负）：{'是' if all_below1 else '否'}")
        print()
        if rel <= 0.5 and all_below1:
            print("  ⇒ **比率是结构量**（各段接近、都 < 1）")
            print("     ⇒ '把捕获做大到超过逆向选择' 是正确方向，可以据此设定目标")
        elif all_below1:
            print("  ⇒ 各段**都 < 1**，但幅度差异较大")
            print("     ⇒ 方向确定（捕获不足），但**具体目标值不能取某一段**，")
            print("       应以'比率 > 1'为方向性判据，而不是某个精确阈值")
        else:
            print("  ⇒ **比率本身随窗口跨越 1** ⇒ 它不是结构量")
            print("     ⇒ 不可设全局目标；应改为逐 regime 决策")
    else:
        print("  段数不足，无法判断")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
