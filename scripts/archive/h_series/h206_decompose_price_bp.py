# -*- coding: utf-8 -*-
"""H206 拆开 price_bp：它到底是"逆向选择"还是"我们记了不存在的成交"。

# 问题

本仓库已确立未解的核心约束：

    基线 regime：maker 腿  spread +0.2146  price −0.2008  net +0.0138
    ⇒ 点差捕获与逆向选择**几乎完全抵消**，net ≈ 0

要让 net 转正，必须改善 `price_bp`。而在动手之前必须回答：
**这个 −0.20bp 是什么构成的？**

# 两个候选机制（后果完全不同）

## 机制 A：真实的逆向选择
成交后价格确实继续朝不利方向走 —— 这是做市商的结构性成本，
研究充分（Glosten-Milgrom 1985、Kyle 1985、Ho-Stoll 1981）。
对策：**择时**（在有信息优势时不挂、在毒性高时撤）。

## 机制 B：我们记了"不存在的成交"
`core.fill_side` 的判定是：

    hit_buy = seg_taker_sell > 0 and seg_low < quote_bid

即"**15 秒桶内的最低价顺带穿过我们的挂单价**"就算成交，
而成交价记作**我们的挂单价**。

F257 的注释已经指出：实测 **47.7% 的引擎成交，其记录的成交价在
±20s / ±2bp 窗口内的真实逐笔里找不到对应**（偏差中位 5.26bp、max 28.6bp）。

⇒ 若 price_bp 的负值主要来自机制 B，那它不是"逆向选择成本"，
而是**记账口径**产生的数字：我们记了一个当时市场里不存在的价，
而"成交后价格继续走"是必然的（因为那个价位本来就没被真正打穿）。

# 判据：账本里有现成的字段

`meta_json.px_exact_hit`（bool，F257 落盘）：桶内是否真有成交落在
`quote_px ± px_hit_tol_bp` 内。

    · 若 `px_exact_hit=true` 的子集 price_bp 明显好于 `false`
      ⇒ 机制 B 是主因 ⇒ **修判定口径**，不是修信号
    · 若两者 price_bp 相近 ⇒ 机制 A 是主因 ⇒ **需要信号层面的解**

# 用法

    python scripts/h206_decompose_price_bp.py
    python scripts/h206_decompose_price_bp.py --hours 48
"""
from __future__ import annotations

import argparse
import pathlib
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
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()

    import psycopg

    print("=" * 100)
    print("H206  拆开 price_bp：逆向选择 vs 记账口径")
    print("=" * 100)
    print(f"  窗口：近 {a.hours:.0f} 小时")

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 180000")

            # ── ① px_exact_hit 的覆盖率 ──
            cur.execute("""
                SELECT count(*) FILTER (WHERE meta_json ? 'px_exact_hit'),
                       count(*),
                       count(*) FILTER (WHERE meta_json->>'px_exact_hit' = 'true'),
                       count(*) FILTER (WHERE meta_json->>'px_exact_hit' = 'false')
                FROM lane_ledger WHERE lane_id=%s
                  AND ts > now() - make_interval(hours => %s::int)
                  AND meta_json->>'flatten' NOT IN ('true','True')
            """, (LANE, a.hours))
            has, tot, t, f = cur.fetchone()
            print(f"\n  ① `px_exact_hit` 覆盖率（maker 腿）")
            print(f"     有该字段 {int(has or 0):>6} / {int(tot or 0):>6}"
                  f"   （{100.0*int(has or 0)/max(int(tot or 0),1):.1f}%）")
            print(f"     true  {int(t or 0):>6}    false {int(f or 0):>6}")
            if not has:
                print("\n     ⚠️ 该字段尚未落盘（F257 可能未启用）⇒ 换用代理判据：")
                print("        `seg_low`/`seg_high` 是否与挂单价有明确穿越关系")

            # ── ② 按 px_exact_hit 分组的 price_bp ──
            cur.execute("""
                SELECT coalesce(meta_json->>'px_exact_hit', '(无字段)') AS hit,
                       count(*) n,
                       round((sum(spread_bp*notional)/NULLIF(sum(notional),0))::numeric,4) wsp,
                       round((sum(price_bp*notional)/NULLIF(sum(notional),0))::numeric,4) wpx,
                       round((sum(net_bp*notional)/NULLIF(sum(notional),0))::numeric,4) wnet,
                       round(sum(net_bp*notional/1e4)::numeric,4) usd,
                       round((sum(notional)/count(*))::numeric,1) avg_notl
                FROM lane_ledger WHERE lane_id=%s
                  AND ts > now() - make_interval(hours => %s::int)
                  AND meta_json->>'flatten' NOT IN ('true','True')
                GROUP BY 1 ORDER BY 2 DESC
            """, (LANE, a.hours))
            rows = cur.fetchall()
            print(f"\n  ② 按 `px_exact_hit` 分组（这是核心判据）")
            print(f"     {'组':<12}{'腿数':>7}{'加权spread':>11}{'加权price':>11}"
                  f"{'加权net':>10}{'净额$':>10}{'笔均名义':>9}")
            print("     " + "-" * 72)
            for hit, n, wsp, wpx, wnet, usd, an in rows:
                print(f"     {str(hit)[:11]:<12}{int(n):>7}{float(wsp or 0):>11.4f}"
                      f"{float(wpx or 0):>11.4f}{float(wnet or 0):>10.4f}"
                      f"{float(usd or 0):>+10.4f}{float(an or 0):>9.1f}")

            # ── ③ seg_low / seg_high 的穿越深度（代理判据，不依赖 px_exact_hit）──
            cur.execute("""
                SELECT
                  count(*) FILTER (WHERE (meta_json->>'seg_low')::float > 0
                                     AND (meta_json->>'seg_high')::float > 0) AS has_seg,
                  count(*) AS n
                FROM lane_ledger WHERE lane_id=%s
                  AND ts > now() - make_interval(hours => %s::int)
                  AND meta_json->>'flatten' NOT IN ('true','True')
            """, (LANE, a.hours))
            hs, n = cur.fetchone()
            print(f"\n  ③ `seg_low/seg_high`（穿越深度的原始依据）可用性"
                  f"：{int(hs or 0)}/{int(n or 0)}")

            if hs and int(hs) > 0:
                # 穿越深度：买单看 seg_low 比挂单价低多少（即市场打穿了多深）
                cur.execute("""
                    WITH d AS (
                      SELECT (meta_json->>'fill_px')::float AS fpx,
                             (meta_json->>'seg_low')::float  AS lo,
                             (meta_json->>'seg_high')::float AS hi,
                             lower(meta_json->>'side') AS side,
                             price_bp, spread_bp, net_bp, notional
                      FROM lane_ledger WHERE lane_id=%s
                        AND ts > now() - make_interval(hours => %s::int)
                        AND meta_json->>'flatten' NOT IN ('true','True')
                        AND (meta_json->>'seg_low')::float > 0
                        AND (meta_json->>'seg_high')::float > 0
                        AND (meta_json->>'fill_px')::float > 0
                    )
                    SELECT CASE WHEN side='buy'
                                THEN (fpx - lo)/fpx*1e4    -- 买：桶内最低价比我们买单低多少
                                ELSE (hi - fpx)/fpx*1e4 END AS pierce_bp,
                           price_bp, net_bp, notional
                    FROM d
                """, (LANE, a.hours))
                pts = cur.fetchall()
                if pts:
                    pierce = sorted(float(p[0]) for p in pts)
                    m = len(pierce)
                    print(f"     穿越深度分布（bp，n={m}）:"
                          f" p10={pierce[int(0.1*m)]:+.3f}"
                          f" p50={pierce[int(0.5*m)]:+.3f}"
                          f" p90={pierce[int(0.9*m)]:+.3f}")
                    # 相关性：穿越越深 ⇒ price_bp 越负？
                    xs = [float(p[0]) for p in pts]
                    ys = [float(p[1] or 0) for p in pts]
                    mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
                    num = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
                    dx = sum((x-mx)**2 for x in xs) ** 0.5
                    dy = sum((y-my)**2 for y in ys) ** 0.5
                    r = num/(dx*dy) if dx > 0 and dy > 0 else 0.0
                    print(f"     corr(穿越深度, price_bp) = {r:+.3f}")
                    if r < -0.3:
                        print("     ⇒ 穿越越深、price_bp 越负 ⇒ **支持机制 B**（记账口径）")
                    elif abs(r) <= 0.3:
                        print("     ⇒ 关系弱 ⇒ 更支持**机制 A**（真实逆向选择）"
                              " ⇒ 需要信号层面的解")

    print("\n  判读总结：")
    print("    · 机制 B（记账口径）⇒ 修 `core.fill_side` 的成交判定，")
    print("      区分「桶内极值穿过」与「真有成交落在我们价位」")
    print("    · 机制 A（真实逆向选择）⇒ 需要信号：用订单流/微价格等")
    print("      预测量化毒性，在毒性高时撤单（对应论文见 H207）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
