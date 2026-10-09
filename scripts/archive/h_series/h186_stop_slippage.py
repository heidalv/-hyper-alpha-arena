# -*- coding: utf-8 -*-
"""H186：止损的**实际兜底能力**审计 —— 触发线 40bp，成交却常在 45~65bp。

用户实测（本时代真实成交）：

    15:32:15  XRP  $873.59  price_bp **-46.01**  fee -4.00  净 -$4.4299
    （更早一笔）XRP  $1,375  price_bp **-53.64**          净 约 -$5.9

`price_bp` = **成交时的中价** 相对 **我们挂单时的中价**（`ref_mid`）的偏离。
所以它衡量的是"从报价到成交，行情走了多远"，而不是"止损线是多少"。

要回答
======

  1. `stop_loss_bp=40` 的实际成交偏离分布是多少？（p50 / p90 / 最差）
  2. 超出触发线的幅度（滑点）= 成交偏离 − 40bp，中位是多少？
  3. **成因**：止损在 40bp 触发、按 `mid ± half_spread` 平仓，
     但从"触发"到"成交"之间行情继续走 ⇒ 滑点。
     tick 间隔 15s ⇒ 最坏 15 秒的行情位移都可能被吃进去。
  4. 与"单腿 40bp 上界"的关系：实际单笔亏损是否真的被限制在
     `-(40bp + 费) × 名义`？实测明显不是 ⇒ **上界是软的**。

判据（事先定死）
================

  · 若滑点中位 ≤ 5bp ⇒ 止损有效，40bp 上界基本可信（软上界）
  · 若滑点中位 > 10bp ⇒ **上界严重失真**，计算"最坏损失"时必须用实测偏离而非 40bp
  · 给出实测的 `|price_bp|` p95，作为**真实**的单笔亏损上界估计

用法：
    .venv\\Scripts\\python.exe scripts\\h186_stop_slippage.py
"""
from __future__ import annotations

import json
import statistics as st
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
    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    lim = j.get("limits") or {}
    par = j.get("params") or {}
    sl = float(lim.get("stop_loss_bp") or 0.0)
    tp = float(lim.get("take_profit_bp") or 0.0)
    eq = float(j.get("equity") or 0.0)
    leg = float(j.get("fill_notional") or 0.0)

    print("=" * 96)
    print("H186  止损的实际兜底能力（触发线 vs 成交偏离）")
    print("=" * 96)
    print(f"  stop_loss_bp={sl}   take_profit_bp={tp}   权益 ${eq:,.2f}   单腿 ${leg:,.2f}")

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                        " WHERE lane_id=%s", (LANE,))
            since = (cur.fetchone() or [None])[0]

            # 所有付 taker 的出库腿（止损或止盈）
            cur.execute("""
                SELECT ts, symbol, coalesce(notional,0), coalesce(price_bp,0),
                       coalesce(fee_bp,0), coalesce(spread_bp,0)
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= %s
                  AND coalesce(meta_json->>'flatten','false')='true'
                  AND coalesce(fee_bp,0) < 0
                ORDER BY id
            """, (LANE, since))
            tks = cur.fetchall()

    if not tks:
        print("\n  本时代还没有付 taker 的出库腿")
        return 1

    stops = [r for r in tks if float(r[3]) < 0]      # price_bp < 0 = 不利方向 = 止损
    takes = [r for r in tks if float(r[3]) > 0]      # 有利方向 = 止盈

    print(f"\n  ── 全部 taker 出库腿 {len(tks)} 笔"
          f"（止损 {len(stops)} / 止盈 {len(takes)}）──")
    print(f"  {'时刻':<10} {'币':<7} {'名义$':>9} {'price_bp':>9} {'费bp':>7} "
          f"{'净$':>9} {'类型':>6}")
    print("  " + "-" * 66)
    for (ts, sym, n, pbp, fbp, sbp) in tks:
        n = float(n)
        usd = n * (float(pbp) + float(fbp)) / 1e4
        kind = "止损" if float(pbp) < 0 else "止盈"
        print(f"  {ts.strftime('%H:%M:%S'):<10} {sym:<7} {n:>9,.0f} {float(pbp):>+9.2f} "
              f"{float(fbp):>+7.2f} {usd:>+9.4f} {kind:>6}")

    if stops:
        dev = sorted(abs(float(r[3])) for r in stops)
        n = len(dev)
        slip = [d - sl for d in dev]
        print(f"\n  ── 止损成交偏离 |price_bp| 分布（n={n}）──")
        for q in (0.0, 0.25, 0.5, 0.75, 1.0):
            i = min(n - 1, int(q * (n - 1)))
            lab = {0.0: "最小", 0.25: "p25", 0.5: "**中位**", 0.75: "p75",
                   1.0: "**最大**"}[q]
            print(f"    {lab:<10} {dev[i]:>8.2f} bp")
        print(f"\n  ── 滑点 = 成交偏离 − 触发线({sl}bp) ──")
        print(f"    中位 **{st.median(slip):+.2f} bp**   均值 {st.mean(slip):+.2f} bp"
              f"   最大 {max(slip):+.2f} bp")
        print(f"    为负的（成交比触发线更好）占 "
              f"{sum(1 for x in slip if x < 0)/n*100:.0f}%")

    print(f"\n  ── 真实单笔亏损 vs 名义上界 ──")
    nominal_cap = (sl + 4.0) * leg / 1e4
    real = sorted(abs(float(r[2]) * (float(r[3]) + float(r[4])) / 1e4) for r in stops)
    print(f"    名义上界 = ({sl:.0f}bp + 4bp 费) × ${leg:,.0f} = **${nominal_cap:,.4f}**")
    if real:
        print(f"    实测单笔亏损：中位 ${st.median(real):,.4f}   "
              f"最大 **${max(real):,.4f}**")
        ratio = max(real) / nominal_cap if nominal_cap else 0
        print(f"    ⇒ 最大实测 / 名义上界 = **{ratio:.2f}×**")
        if ratio > 1.2:
            print(f"    ⚠️ **上界是软的**：实际最坏比名义上界大 {ratio:.1f} 倍。")
            print(f"       计算「三币同时打止损最坏损失」时必须用实测值，不能用 40bp。")

    print(f"\n  ── 成因 ──")
    print(f"    · 止损在 tick 里判定（间隔 15s）⇒ 触发瞬间 ~ 成交之间最多 15s 的行情位移")
    print(f"    · 成交价 = `mid ± half_spread`（打对手价）⇒ 还含半个价差")
    print(f"    · `stop_loss_vol_min=0`（恒启用）⇒ **任何波动下都会触发**，")
    print(f"      而低波动时 15s 的位移小、高波动时大 ⇒ 滑点随波动放大")
    print(f"    ⇒ 若要收紧上界，正确动作是**缩短 tick 间隔**或**用限价止损**，")
    print(f"      而不是继续下调 `stop_loss_bp`（那只会让它更频繁触发、付更多费）")

    print(f"\n  ── 三币同时打止损的真实最坏 ──")
    if real:
        worst_one = max(real)
        mdr = float(lim.get("max_net_directional_ratio") or 2.0)
        n_sym = 3
        print(f"    按实测最坏单笔 ${worst_one:,.4f} × {n_sym} 币 = "
              f"**${worst_one*n_sym:,.4f}**"
              f"（{worst_one*n_sym/eq*100:.2f}% 权益）")
        print(f"    ⚠️ 若持仓堆到单币上限（{mdr}× 权益 = ${mdr*eq:,.0f}），")
        print(f"       单笔最坏按比例放大到 ${worst_one*(mdr*eq/leg):,.4f}，")
        print(f"       三币合计 **${worst_one*(mdr*eq/leg)*n_sym:,.4f}**"
              f"（{worst_one*(mdr*eq/leg)*n_sym/eq*100:.1f}% 权益）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
