# -*- coding: utf-8 -*-
"""H225 exit_path 归因监控：F340 之后，强平到底走哪条出口、各亏多少。

# 为什么需要它（此前这个问题被答错了）

我此前用 `exit_reason`（取自 `dec.skip`）做出口分解，得到
"`trend_up` / `trend_down` / `ofi_toxic_*` 是主要出口"。
**这个结论是错的**，两条独立原因：

  ① `exit_reason` 是 F335（2026-09-22）才加的 ⇒ 旧行没有这个键；
  ② 真正的坑：`dec.skip` 会被**同 tick 早先的闸门**赋值
     （`trend_up` / `vol_pause` / `ofi_toxic_*`），
     于是"止损强平"会被记成"被趋势闸拦下"。

F340 引入 `exit_path`，由每条出口分支**显式命名自己**：

    stop_loss_taker / take_profit_taker / timeout_taker /
    ofi_flatten_taker / orphan_taker(*)

# 本脚本给出什么

1. F340 之后的 flatten 腿**逐出口**的腿数 / 净额 / 单腿成本；
2. `exit_path` 缺失率（若仍高 ⇒ 还有未命名的出口路径）；
3. F340 前后对比（同一口径下，"归因修复"是否让图景变了）；
4. **每条出口的单腿成本** —— 这是唯一有资格决定"该关哪条出口"的读数。

# 用法

    python scripts/h225_exit_path_board.py --hours 6
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

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
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT coalesce(meta_json->>'exit_path','') AS xp,
                       coalesce(meta_json->>'exit_reason','') AS xr,
                       count(*), coalesce(sum(net_bp*notional/1e4),0),
                       coalesce(avg(net_bp),0), coalesce(sum(notional),0),
                       coalesce(avg(fee_bp),0), coalesce(avg(price_bp),0)
                FROM lane_ledger
                WHERE lane_id=%s AND (meta_json->'flatten')::text='true'
                  AND ts >= now() - (%s || ' hours')::interval
                GROUP BY 1,2 ORDER BY 3 DESC
            """, (LANE, str(float(a.hours))))
            fl = cur.fetchall()
            cur.execute("""
                SELECT count(*) FILTER (WHERE meta_json ? 'exit_path') AS has_xp,
                       count(*) AS total,
                       count(*) FILTER (WHERE (meta_json->'flatten')::text='true') AS nflat
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' hours')::interval
                  AND meta_json IS NOT NULL
            """, (LANE, str(float(a.hours))))
            has_xp, total, nflat = cur.fetchone()
            # maker 基准
            cur.execute("""
                SELECT count(*), coalesce(sum(net_bp*notional/1e4),0),
                       coalesce(sum(notional),0)
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' hours')::interval
                  AND (meta_json->'flatten')::text='false'
                  AND coalesce(notional,0)>0
            """, (LANE, str(float(a.hours))))
            mkn, mku, mknl = cur.fetchone()

    print("=" * 100)
    print(f"H225  exit_path 归因监控（最近 {a.hours:g} 小时）")
    print("=" * 100)
    print(f"\n  总行 {total}　其中 flatten {nflat}（{nflat/total*100 if total else 0:.2f}%）")
    print(f"  含 `exit_path` 键的行 = {has_xp}"
          f"（{has_xp/total*100 if total else 0:.1f}%）")
    if has_xp == 0:
        print(f"\n  ⚠️ 窗口内没有任何行带 `exit_path` ⇒ F340 尚未产出样本")
        print(f"     （F340 部署后需等到下一次强平才会写入）")

    mku_w = mku / mknl * 1e4 if mknl else 0.0
    print(f"\n  maker 基准：{mkn} 腿　名义 ${float(mknl):,.0f}　"
          f"净额 ${float(mku):+.2f}　加权 {mku_w:+.3f} bp")

    print(f"\n{'━'*100}\n  一、flatten 腿按 exit_path 分解\n{'━'*100}")
    if not fl:
        print("\n  窗口内无 flatten 腿 ⇒ 无强平（或车道停摆）")
    else:
        print(f"\n  {'exit_path':<22}{'腿数':>7}{'净额$':>11}{'单腿$':>10}"
              f"{'均值bp':>10}{'fee':>8}{'price':>9}")
        tot_u = 0.0
        tot_n = 0
        for xp, xr, n, u, mbp, nl, fbp, pbp in fl:
            lab = xp or "(空 = 未命名出口)"
            print(f"  {lab:<22}{n:>7}{float(u):>+11.2f}{float(u)/n:>+10.4f}"
                  f"{float(mbp):>+10.2f}{float(fbp):>+8.2f}{float(pbp):>+9.2f}")
            tot_u += float(u)
            tot_n += n
        print(f"\n  ⇒ flatten 合计 {tot_n} 腿　净额 ${tot_u:+.2f}　"
              f"单腿 ${tot_u/tot_n if tot_n else 0:+.4f}")
        print(f"  ⇒ 与 maker 单腿 ${float(mku)/mkn if mkn else 0:+.4f} 相比，"
              f"**强平贵 "
              f"{abs(tot_u/tot_n) - abs(float(mku)/mkn) if tot_n and mkn else 0:.4f} $/腿**")
        unnamed = sum(n for xp, _xr, n, *_ in fl if not xp)
        if unnamed:
            print(f"\n  ⚠️ **{unnamed} 条仍未命名出口**"
                  f"（{unnamed/tot_n*100:.1f}%）⇒ 还有出口路径没设 `dec.exit_path`，")
            print(f"     或这些是 F340 部署**之前**的行（F340 只为新行写入）")
        else:
            print(f"\n  ✓ 全部 flatten 腿都已命名出口")

    # 判定
    print(f"\n{'━'*100}\n  二、判定\n{'━'*100}")
    if fl and nflat:
        u_total = sum(float(u) for _xp, _xr, _n, u, *_ in fl)
        print(f"\n  最近 {a.hours:g} 小时：maker 净额 ${float(mku):+.2f}　"
              f"flatten 净额 ${u_total:+.2f}　"
              f"合计 ${float(mku)+u_total:+.2f}")
        if abs(u_total) > abs(float(mku)) * 0.5 and u_total < 0:
            print(f"  ⇒ **强平仍是主要亏损源**（占 "
                  f"{abs(u_total)/(abs(u_total)+abs(float(mku)))*100:.1f}%）")
        elif u_total >= 0:
            print(f"  ⇒ 本窗口强平**不亏钱**（${u_total:+.2f}）—— 值得复核"
                  f"（可能是止盈主导，或行情对我们有利）")
        else:
            print(f"  ⇒ 强平亏损已不占主导")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
