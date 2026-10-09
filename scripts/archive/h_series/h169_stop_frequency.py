# -*- coding: utf-8 -*-
"""[H169 2026-09-21] 止损频率与"反复被止损"的真实规模 —— 回答"是不是亏没了"。

# 背景

用户反馈「连续止损，直接亏没了」。已查实：
  · 权益峰值 $306.53 → 现在 $295.18（**−$11.35**，−3.7%）
  · 深止损（price_bp ≤ −35bp）只有 2 笔、合计 −$1.86
  · 14:00 这一小时净 −$11.17，其中**行情项 −$15.836 占 89%**
⇒ 主因不是止损本身，而是**持仓期间行情单边走**。

# 但"连续止损"这个担忧必须正面量化

  1. 止损的**真实触发频率**：每小时几次？每多少笔成交一次？
  2. 按当前 `stop_loss_bp=40` 与 `compound_ratio=2.0`，**多少次连续止损**才会
     把账户打穿（权益 → 0，或触发 `daily_loss_stop_pct=80`）？
  3. 与"不止损"的对照：那些触发止损的仓位如果不平，会怎样？
     （用盘口回放：止损价之后价格是继续亏还是回摆？）
  4. σ 闸是否该起作用：`stop_loss_vol_min=0` 是**恒启用**，而原设计意图是
     "只在波动高的 regime 才划算"（F230 四场景）。当前市场 σ 是多少？

用法：
    .venv\\Scripts\\python.exe scripts\\h169_stop_frequency.py
"""
from __future__ import annotations

import json
import statistics as st
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn(db: str = "alpha_arena") -> str:
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
    return f"{url.rsplit('/', 1)[0]}/{db}"


def main() -> int:
    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    lim, par = j.get("limits") or {}, j.get("params") or {}
    eq = float(j.get("equity") or 0.0)
    leg = float(j.get("fill_notional") or 0.0)
    sl = float(lim.get("stop_loss_bp") or 0.0)

    print("=" * 96)
    print("H169  止损频率与规模 —— 「亏没了」到底有多远")
    print("=" * 96)
    print(f"  权益 ${eq:,.2f}   单腿 ${leg:,.2f}   stop_loss_bp={sl}")

    # ── 1) 打穿账户需要多少次连续止损 ──
    per_stop = sl * leg / 1e4
    print(f"\n  ── 1) 打穿账户需要多少次连续止损 ──")
    print(f"    每次止损 = {sl}bp × ${leg:,.0f} = **${per_stop:,.4f}**")
    print(f"    {'目标':<28} {'金额$':>10} {'需要次数':>10}")
    print("    " + "-" * 52)
    for lab, amt in (("回撤到起始 $300", 300.0 - eq if eq < 300 else 0),
                     ("日亏闸 80% 权益（停止交易）", eq * 0.80),
                     ("权益归零", eq)):
        n = amt / per_stop if per_stop else float("inf")
        print(f"    {lab:<28} {amt:>10,.2f} {n:>10.0f}")

    # ── 2) 实际止损频率 ──
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                        " WHERE lane_id=%s", (LANE,))
            since = (cur.fetchone() or [None])[0]
            cur.execute("""SELECT count(*) FROM lane_ledger
                           WHERE lane_id=%s AND event='fill' AND ts >= %s""",
                        (LANE, since))
            nfill = int(cur.fetchone()[0] or 0)
            cur.execute("""SELECT ts, symbol, coalesce(notional,0), coalesce(price_bp,0),
                                  coalesce(spread_bp,0), coalesce(fee_bp,0)
                           FROM lane_ledger
                           WHERE lane_id=%s AND event='fill' AND ts >= %s
                             AND coalesce(meta_json->>'flatten','false')='true'
                           ORDER BY id""", (LANE, since))
            flats = cur.fetchall()
            cur.execute("SELECT min(ts), max(ts) FROM lane_ledger"
                        " WHERE lane_id=%s AND event='fill' AND ts >= %s",
                        (LANE, since))
            t0, t1 = cur.fetchone()

    span_h = ((t1 - t0).total_seconds() / 3600.0) if (t0 and t1) else 0.0
    deep = [r for r in flats if float(r[3]) <= -35.0]
    print(f"\n  ── 2) 实际止损频率（本时代 {span_h:.2f} 小时）──")
    print(f"    成交 {nfill} 笔   出库腿 {len(flats)} 笔   深止损 **{len(deep)}** 笔")
    if span_h > 0:
        print(f"    ⇒ 深止损频率 **{len(deep)/span_h:.1f} 次/小时**"
              f"（每 {nfill/max(len(deep),1):.0f} 笔成交一次）")

    # ── 3) σ 当前水平 vs 闸门 ──
    sig = j.get("avg_sigma")
    sigall = j.get("avg_sigma_all")
    vmin = lim.get("stop_loss_vol_min")
    print(f"\n  ── 3) 波动闸设置 ──")
    print(f"    stop_loss_vol_min = {vmin}（0 = **恒启用**）")
    print(f"    实测 σ：决策时机 {sig}   全样本 {sigall}")
    print("    ⚠️ 原设计意图（core.py F231/F230）：`vol_min > 0` 时**只在高波动 regime**")
    print("       才启用止损，理由是「常数止损在正常日多亏（被震荡反复打止损、白付 taker）」。")
    print("       我们把它设成 0（恒启用）⇒ **正常日也会止损** ⇒ 正是用户观察到的现象。")

    # ── 4) 深止损后价格是继续亏还是回摆 ──
    if deep:
        print(f"\n  ── 4) 深止损之后价格怎么走（判断止损是「救命」还是「白亏」）──")
        print(f"    {'时刻':<10} {'币':<7} {'止损price_bp':>12} {'之后60s行情bp':>14} "
              f"{'结论':>10}")
        print("    " + "-" * 62)
        win = 0
        lose = 0
        for (ts, sym, notl, pbp, sbp, fbp) in deep:
            # 止损是"打对手价"，方向与持仓相反。之后价格若继续不利 ⇒ 止损对了
            with psycopg.connect(dsn("alpha_market")) as mc:
                with mc.cursor() as cur:
                    cur.execute("""
                        SELECT (bid_px+ask_px)/2 FROM asterdex_book_ticker
                        WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                          AND ask_px > bid_px AND bid_px > 0
                        ORDER BY event_ts_ms
                    """, (sym + "USDT", int(ts.timestamp() * 1000),
                          int((ts.timestamp() + 60) * 1000)))
                    mids = [float(r[0]) for r in cur.fetchall()]
            if len(mids) < 5:
                continue
            p0 = mids[0]
            # price_bp 是"持仓期间漂移"，为负说明价格逆着持仓走。
            # 止损后若价格**继续**逆着走 ⇒ 止损避免了更大亏损（对）
            after = (mids[-1] - p0) / p0 * 1e4
            sign = -1.0 if pbp < 0 else 1.0
            continued = (after * sign) < 0
            if continued:
                lose += 1
            else:
                win += 1
            print(f"    {ts.strftime('%H:%M:%S'):<10} {sym:<7} {float(pbp):>+12.2f} "
                  f"{after:>+14.2f} {'止损救到' if continued else '**白亏**':>10}")
        print(f"\n    ⇒ 止损后价格继续不利（止损有用）：{lose} 次")
        print(f"       止损后价格回摆（止损白亏）：**{win} 次**")
        print(f"       ⚠️ 样本只有 {lose+win} 笔，不足以定论，但方向可参考。")

    print(f"\n  ── 结论 ──")
    print(f"    1. 「亏没了」在**当前参数下不成立**：权益 ${eq:,.0f}，")
    print(f"       要 {eq/per_stop:.0f} 次连续止损才归零、{(eq*0.8)/per_stop:.0f} 次才触发日亏闸。")
    print(f"    2. 但**回撤是真的**：$306.53 → ${eq:,.2f}（−{306.53-eq:,.2f}，"
          f"−{(306.53-eq)/306.53*100:.1f}%），主因是行情项而非止损。")
    print(f"    3. **真正该改的是 `stop_loss_vol_min`**：设 0（恒启用）背离了")
    print(f"       原设计「只在高波动 regime 止损」的意图 ⇒ 正常日被反复打。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
