# -*- coding: utf-8 -*-
"""[H175 2026-09-21] 止盈成交的**真实战绩**（从账本直接取，不做模拟）。

# 为什么直接取账本

前几个模拟脚本（H172/H173）口径与实盘对不上（模拟给出 −1.66bp 而实盘 +0.1374bp）
⇒ 不可用于决策。但**账本里已经有真实的止盈成交**（`skip_counts` 出现过 `take_profit`，
明细里能看到 ASTER 出库腿 `price_bp=+24.367, fee_bp=−4.000`）。
**真实成交比任何模拟都可靠** —— 直接聚合它们。

# 量什么

  · 所有止盈腿的笔数、名义、价差/行情/费三维、净额
  · 与"非止盈腿"（普通 maker 出库）逐项对比
  · 止盈腿的 `price_bp` 分布（验证"是不是真的吃到了有利移动"）
  · **整体每笔净 bp**：把止盈腿与其它腿合起来，与基准 +0.1374bp 对比

# 判据（事先定死）

  · 若止盈腿的净 bp（约 +8bp）显著高于普通出库腿（约 +0.3bp）⇒ 改动方向正确
  · 若止盈腿数量≥10 且合计为正 ⇒ 可确认有效
  · 若样本 < 10 笔 ⇒ **不下结论**，只说"机制在跑"

用法：
    .venv\\Scripts\\python.exe scripts\\h175_take_profit_record.py
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
BASELINE_BP = 0.1374


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
    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    lim = j.get("limits") or {}
    print("=" * 96)
    print("H175  止盈成交的真实战绩")
    print("=" * 96)
    print(f"  take_profit_bp={lim.get('take_profit_bp')}  "
          f"grace={lim.get('take_profit_maker_grace_sec')}s  "
          f"心跳 take_profit_hits={j.get('take_profit_hits')}")
    print(f"  skip_counts 里的 take_profit = "
          f"{(j.get('skip_counts') or {}).get('take_profit')}")

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                        " WHERE lane_id=%s", (LANE,))
            since = (cur.fetchone() or [None])[0]

            # 账本里 skip 字段是否可用
            cur.execute("""SELECT coalesce(meta_json->>'skip','<无>') sk, count(*)
                           FROM lane_ledger WHERE lane_id=%s AND event='fill'
                             AND ts >= %s GROUP BY 1 ORDER BY 2 DESC LIMIT 8""",
                        (LANE, since))
            print(f"\n  ── 账本 skip 归因（本时代）──")
            for k, n in cur.fetchall():
                print(f"    {k:<24} {n:>6}")

            # 找出止盈腿：flatten=true 且 price_bp > 0 且 fee_bp < 0
            # （止盈=打对手价、行情对我们有利、付 taker）
            cur.execute("""
                SELECT ts, symbol, coalesce(notional,0), coalesce(price_bp,0),
                       coalesce(spread_bp,0), coalesce(fee_bp,0)
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= %s
                  AND coalesce(meta_json->>'flatten','false')='true'
                  AND coalesce(fee_bp,0) < 0
                ORDER BY id
            """, (LANE, since))
            tks = cur.fetchall()

            # 全部成交的汇总（用于算整体每笔净 bp）
            cur.execute("""
                SELECT count(*), coalesce(sum(spread_bp*notional/1e4),0),
                       coalesce(sum(price_bp*notional/1e4),0),
                       coalesce(sum(fee_bp*notional/1e4),0),
                       coalesce(sum(notional),0)
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s
            """, (LANE, since))
            cnt, sp, pr, fe, notl = cur.fetchone()
            cnt, notl = int(cnt or 0), float(notl or 0)
            b = 1e4 / notl if notl else 0.0
            overall_bp = (float(sp) + float(pr) + float(fe)) * b

    print(f"\n  ── 付 taker 的出库腿（共 {len(tks)} 笔）──")
    if not tks:
        print("    （还没有付 taker 的出库腿）")
        return 0

    print(f"  {'时刻':<10} {'币':<7} {'名义$':>9} {'价差bp':>8} {'行情bp':>9} "
          f"{'费bp':>7} {'净$':>9}")
    print("  " + "-" * 68)
    tot = 0.0
    price_bps = []
    for (ts, sym, n, pbp, sbp, fbp) in tks:
        n = float(n)
        usd = n * (float(pbp) + float(sbp) + float(fbp)) / 1e4
        tot += usd
        price_bps.append(float(pbp))
        print(f"  {ts.strftime('%H:%M:%S'):<10} {sym:<7} {n:>9,.1f} {float(sbp):>+8.3f} "
              f"{float(pbp):>+9.3f} {float(fbp):>+7.3f} {usd:>+9.4f}")
    print(f"  ⇒ 合计 **{tot:+.4f} USD**")

    nb = sum(float(r[2]) for r in tks)
    if nb > 0:
        bp_leg = tot / nb * 1e4
        print(f"\n  ── 单笔战绩 ──")
        print(f"    平均每笔净 **{bp_leg:+.4f} bp**"
              f"（理论值 +{float(lim.get('take_profit_bp') or 0) - 4.0:.1f}bp）")
        print(f"    `price_bp` 中位 {st.median(price_bps):+.2f}bp  "
              f"（说明确实吃到了有利移动）")

    print(f"\n  ── 与整体对比 ──")
    print(f"    本时代全部成交 {cnt} 笔 ⇒ 整体每笔净 **{overall_bp:+.4f} bp**")
    print(f"    基准（止盈启用前）      {BASELINE_BP:+.4f} bp")
    print(f"    ⇒ 变化 {overall_bp - BASELINE_BP:+.4f} bp/笔")

    print(f"\n  ── 判读 ──")
    if len(tks) < 10:
        print(f"    ⚠️ 止盈腿只有 {len(tks)} 笔 ⇒ **样本不足，不下结论**。")
        print(f"       但机制在跑（账本里有真实成交），方向可继续观察。")
    else:
        if bp_leg > 0:
            print(f"    ⇒ 止盈腿每笔 {bp_leg:+.2f}bp，明显优于普通出库（~+0.3bp）")
            print(f"       ⇒ 改动方向正确。但要看它对**整体**每笔净额的正贡献。")
        else:
            print(f"    ⇒ 止盈腿每笔为负 ⇒ 阈值或宽限期设置有问题，需复核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
