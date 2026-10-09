# -*- coding: utf-8 -*-
"""[H162 2026-09-21] 单币库存累积审计 —— 做市怎么变成了方向性押注。

# 用户观察

    XRP 多 924.9362  开仓 1.4298  现价 1.4311   (+9.1bp)
    （此前同一仓位是 520.7162，开仓 1.4297）

⇒ **同一方向持仓在放大**。做市的预期是"库存围绕 0 摆动"，
而这里变成了"单边累积" ⇒ 机制上等同于**方向性押注**，只是建仓成本是挂单。

# 量什么

  · XRP 的逐笔成交序列：买/卖、数量、持仓累计 → 看它是"净加仓"还是"往返"
  · 该币的 `qty` 峰值与敞口上限的关系
  · 为什么减仓腿没成交（挂宽 / 库存偏斜方向是否反了）
  · 当前浮盈/浮亏与其占权益的比例

# 判据

  · 若买盘笔数/数量显著多于卖盘 ⇒ **净累积**（减仓腿成交不足）
  · 若持仓峰值接近 `max_net_directional_ratio × 权益` ⇒ 已顶到硬上限，
    再加仓会被 `symbol_exposure` 拦（此时表现为"卡在满仓"）

用法：
    .venv\\Scripts\\python.exe scripts\\h162_inventory_accum.py [--sym XRP]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default="XRP")
    ap.add_argument("--n", type=int, default=30)
    a = ap.parse_args()

    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    eq = float(j.get("equity") or 0.0)
    leg = float(j.get("fill_notional") or 0.0)
    lim = j.get("limits") or {}
    st = (j.get("states") or {}).get(a.sym) or {}

    qty = float(st.get("qty") or 0.0)
    avg = float(st.get("avg_mid") or 0.0)
    op = float(st.get("opened_ts") or 0.0)
    age = (datetime.now().timestamp() - op) if op > 0 else 0.0

    print("=" * 92)
    print(f"H162  单币库存累积审计  [{a.sym}]")
    print("=" * 92)
    print(f"  权益 ${eq:,.2f}   单腿名义 ${leg:,.2f}")
    print(f"  当前持仓 qty={qty:,.4f}  开仓mid={avg:.4f}  持有 {age:.0f}s")
    print(f"  该仓名义 ≈ ${abs(qty)*avg:,.2f} = "
          f"**{abs(qty)*avg/eq if eq else 0:.2f}× 权益**（单币上限 "
          f"{lim.get('max_net_directional_ratio')}× ⇒ "
          f"${float(lim.get('max_net_directional_ratio') or 0)*eq:,.0f}）")
    print(f"  止损上界 = 40bp × ${abs(qty)*avg:,.0f} = "
          f"**${0.0040*abs(qty)*avg:,.2f}**（占权益 "
          f"{0.0040*abs(qty)*avg/eq*100 if eq else 0:.1f}%）")

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT ts, meta_json->>'side' AS side, notional, spread_bp, price_bp,
                       coalesce(meta_json->>'flatten','false') AS flat
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND symbol=%s
                  AND ts >= now() - interval '30 minutes'
                ORDER BY id
            """, (LANE, a.sym))
            rows = cur.fetchall()

    print(f"\n  最近 30 分钟 {a.sym} 成交 {len(rows)} 笔：")
    print(f"  {'时刻':<10} {'side':<5} {'名义$':>9} {'价差bp':>8} {'行情bp':>8} {'flat':<6}")
    print("  " + "-" * 52)
    cum = 0.0
    for (ts, side, notl, sp, pr, flat) in rows:
        n = float(notl or 0.0)
        if side == "buy":
            cum += n
        else:
            cum -= n
        print(f"  {ts.strftime('%H:%M:%S'):<10} {str(side):<5} {n:>9,.1f} "
              f"{float(sp or 0):>+8.3f} {float(pr or 0):>+8.3f} {flat:<6}")

    buys = [r for r in rows if r[1] == "buy"]
    sells = [r for r in rows if r[1] == "sell"]
    nb = sum(float(r[2] or 0) for r in buys)
    ns = sum(float(r[2] or 0) for r in sells)
    print(f"\n  ── 买卖失衡 ──")
    print(f"    买 {len(buys)} 笔 / ${nb:,.1f}")
    print(f"    卖 {len(sells)} 笔 / ${ns:,.1f}")
    print(f"    净名义差额 **${nb-ns:+,.1f}**"
          f"（正 = 净买入 = 库存在累积）")

    print(f"\n  ── 机制判读 ──")
    if nb - ns > 0 and qty > 0:
        print(f"    ⇒ 该币**净买入**、持仓为多 ⇒ 减仓腿（卖）成交不足。")
        print(f"      原因通常是：")
        print(f"        ① 减仓侧挂宽偏大（`spread_mult_reduce` 现在 "
              f"{(j.get('params') or {}).get('spread_mult_reduce')}）⇒ 要等价格走上来才被吃")
        print(f"        ② 卖侧被 `check_side_allowed` 的闸挡掉（但减仓侧应恒允许）")
        print(f"        ③ 行情单边上涨 ⇒ 减仓单挂在中价上方，价格一路走高却不回头成交")
        print(f"      ⇒ 此时「库存在赚」，但**风险在累积**：这是**方向性敞口**，不是做市。")
    print(f"\n  ⚠️ 关键结构问题：`max_net_directional_ratio={lim.get('max_net_directional_ratio')}` "
          f"允许单币持仓到 {lim.get('max_net_directional_ratio')}× 权益")
    print(f"     ⇒ 做市可以在**单边**累积到 {float(lim.get('max_net_directional_ratio') or 0)*eq/leg:.0f} 条腿，")
    print(f"       而那期间的盈亏由**行情方向**决定，不由价差决定。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
