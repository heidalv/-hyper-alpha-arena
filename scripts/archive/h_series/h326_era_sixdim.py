# -*- coding: utf-8 -*-
"""H326 时代六维账本分解（spread / price / fee / funding / slippage）。

# 为什么需要它
   调参与出口优化的所有努力，最终都体现在这五个维度上：
     spread  价差捕获（被动挂单的正收益来源）
     price   价格逆行（逆选择成本 —— 30 天实测是 spread 的 2 倍，是真正的战场）
     fee     手续费（被动出口改造后已≈0）
     funding 资金费（账本目前不记录 ⇒ 盲区，但持仓中位 30~46s，跨 8h 结算点概率 <0.2%）
     slippage 滑点
   h324/h325 改动（闸门翻向 + 出口重标定）的目标就是让 price 项变浅。
   本脚本按时代（lane_registry.meta.stats_since）+ 可选历史窗口输出对比，
   让"改动有没有让 price 项变好"一眼可见。

# 用法
   python scripts/h326_era_sixdim.py              # 本时代 + 近24h + 近7天
   python scripts/h326_era_sixdim.py --hours 48   # 自定义历史窗口
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h326_sixdim.json"
DIMS = ["spread_bp", "price_bp", "fee_bp", "funding_bp", "slippage_bp", "net_bp"]


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
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args()

    import psycopg2

    c = psycopg2.connect(dsn())
    c.autocommit = True
    cur = c.cursor()
    cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry WHERE lane_id='mm_asterdex'")
    era = cur.fetchone()[0]
    print(f"时代起点: {era}\n")

    sel = ", ".join(f"round((sum({d}*notional)/1e4)::numeric,4) AS {d}" for d in DIMS)
    windows = [("本时代", f"ts >= '{era}'::timestamptz"),
               (f"近{a.hours:.0f}h", f"ts >= now() - interval '{a.hours} hours'"),
               ("近7天", "ts >= now() - interval '7 days'"),
               ("近30天", "ts >= now() - interval '30 days'")]
    out = {}
    print(f"{'窗口':<10} {'腿数':>7} {'spread':>10} {'price':>11} {'fee':>9} "
          f"{'funding':>8} {'slip':>6} {'净USD':>10} {'每腿bp':>8}")
    for label, cond in windows:
        cur.execute(f"""
            SELECT count(*), {sel}
            FROM lane_ledger
            WHERE lane_id='mm_asterdex' AND event='fill' AND {cond}
        """)
        row = cur.fetchone()
        n = row[0]
        vals = {d: float(row[i + 1] or 0) for i, d in enumerate(DIMS)}
        cur.execute(f"""
            SELECT round((sum(net_bp*notional)/NULLIF(sum(notional),0))::numeric,4)
            FROM lane_ledger
            WHERE lane_id='mm_asterdex' AND event='fill' AND {cond}
        """)
        per_leg = float(cur.fetchone()[0] or 0)
        out[label] = {"n": n, **{k: round(v, 4) for k, v in vals.items()}, "per_leg_bp": per_leg}
        print(f"{label:<10} {n:>7} {vals['spread_bp']:>+10.2f} {vals['price_bp']:>+11.2f} "
              f"{vals['fee_bp']:>+9.2f} {vals['funding_bp']:>+8.2f} {vals['slippage_bp']:>+6.2f} "
              f"{vals['net_bp']:>+10.2f} {per_leg:>+8.3f}")
    print("\n注：price 项（逆选择成本）是主要战场；fee ≈0 说明被动出口改造有效；"
          "funding 账本未记录（盲区，但持仓中位 30~46s ⇒ 影响可忽略）。")

    OUT.write_text(json.dumps({"era": era, "windows": out}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
