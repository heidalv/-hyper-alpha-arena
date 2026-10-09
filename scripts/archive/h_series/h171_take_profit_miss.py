# -*- coding: utf-8 -*-
"""[H171 2026-09-21] 止盈"漏抓"审计：到过 +12bp 却没落袋的比例与原因。

# 为什么要做

用户担心「连续止损」，但查实亏损主因是**14:20~14:40 的一次单边行情**（行情项 −$15.14）。
而**止盈本该在这种行情里救到** —— 它会把 +12bp 的浮盈落袋，而不是等价格回撤。

# 先做一个理论检查（可能推翻"宽限期太长"的假设）

H165 实测：触及 12bp 阈值的**中位耗时 81.7 秒**。
而 `take_profit_maker_grace_sec = 30` 秒 —— **触发时 30 秒早已过去**
⇒ 宽限期**不应该**是漏抓的原因（第一版它只在触发的那一刻才开始计时）。

⇒ 所以真正要查的是别的：**"到过 +12bp" 的仓位里，有多少真的被落了袋？**

# 量什么

对窗口内每个入场：
  1. 用真实盘口算该仓位在 180s 内的**最大有利偏移** MFE
  2. 若 MFE ≥ 12bp ⇒ 该仓位**本该**被止盈抓到（理论命中）
  3. 与账本里实际的 `take_profit` 触发数对比 ⇒ 差额就是"漏抓"
  4. 对漏抓的样本，进一步看：**从"首次触及 12bp"到"之后 30 秒内的最高点"**
     是否有回撤（若回撤严重 ⇒ 30s 宽限期确实在吃掉利润；若没有 ⇒ 是别的原因）

# 判据（事先定死）

  · 理论命中数 ≈ 实际触发数 ⇒ 止盈在正常工作，漏抓可忽略
  · 理论命中 ≫ 实际触发 ⇒ 有系统性漏抓，需定位（tick 粒度 / 闸门 / 宽限期）
  · "触及后 30s 内回撤"中位数 > 3bp ⇒ 宽限期在吃掉利润，应缩短

用法：
    .venv\\Scripts\\python.exe scripts\\h171_take_profit_miss.py
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
TP_BP = 12.0
GRACE_S = 30.0
TAKER_BP = 4.0


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=float, default=180.0)
    ap.add_argument("--limit", type=int, default=250)
    a = ap.parse_args()

    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    lim = j.get("limits") or {}
    tp = float(lim.get("take_profit_bp") or 0.0)
    grace = float(lim.get("take_profit_maker_grace_sec") or 0.0)

    print("=" * 96)
    print("H171  止盈漏抓审计")
    print("=" * 96)
    print(f"  take_profit_bp={tp}   grace={grace}s   （按实际生效值，不用常量）")
    if tp <= 0:
        print("  ⚠️ 止盈当前是关闭的（0），无法审计漏抓")
        return 1

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                        " WHERE lane_id=%s", (LANE,))
            since = (cur.fetchone() or [None])[0]
            cur.execute("""
                SELECT id, ts, symbol, meta_json->>'side', coalesce(notional,0),
                       coalesce(meta_json->>'flatten','false')
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s
                ORDER BY id
            """, (LANE, since))
            rows = cur.fetchall()
            cur.execute("""SELECT count(*) FROM lane_ledger
                           WHERE lane_id=%s AND event='fill' AND ts >= %s
                             AND coalesce(meta_json->>'flatten','false')='true'
                             AND coalesce(fee_bp,0) < 0""", (LANE, since))
            actual_taker = int(cur.fetchone()[0] or 0)

    entries = [(r[0], r[1], r[2], r[3]) for r in rows if r[5] == "false"][-a.limit:]
    print(f"  本时代入场 {len(entries)} 笔（取最近 {a.limit}）   "
          f"实际付 taker 的出库腿 {actual_taker} 笔")

    theory_hit = 0
    retreats = []          # 触及后 grace 秒内的回撤（bp）
    peak_after = []        # 触及后 grace 秒内的最高有利偏移
    checked = 0
    for i, (rid, ts, sym, side) in enumerate(entries, 1):
        t0 = ts.timestamp()
        with psycopg.connect(dsn("alpha_market")) as mc:
            with mc.cursor() as cur:
                cur.execute("""
                    SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                      AND ask_px > bid_px AND bid_px > 0
                    ORDER BY event_ts_ms
                """, (sym + "USDT", int(t0 * 1000),
                      int((t0 + a.horizon) * 1000)))
                seq = [(int(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
        if len(seq) < 5:
            continue
        mid0 = (seq[0][1] + seq[0][2]) / 2.0
        if mid0 <= 0:
            continue
        checked += 1
        sgn = 1.0 if side == "buy" else -1.0
        fav = [sgn * ((b + aa) / 2.0 - mid0) / mid0 * 1e4 for (_, b, aa) in seq]
        mfe = max(fav)
        if mfe < tp:
            continue
        theory_hit += 1
        # 首次触及 tp 的位置
        idx = next(k for k, v in enumerate(fav) if v >= tp)
        t_hit = (seq[idx][0] - seq[0][0]) / 1000.0
        # 触及后 grace 秒内的最高点
        lim_ms = seq[idx][0] + int(grace * 1000)
        window = [fv for (ms2, _, _), fv in zip(seq[idx:], fav[idx:]) if ms2 <= lim_ms]
        if window:
            pk = max(window)
            peak_after.append(pk)
            retreats.append(pk - tp)
        if i % 60 == 0:
            print(f"    回放进度 {i}/{len(entries)} …")

    print(f"\n  ── 结果 ──")
    print(f"    回放成功 {checked} 笔")
    print(f"    **理论命中**（MFE ≥ {tp}bp）：**{theory_hit}** 笔"
          f"（{theory_hit/max(checked,1)*100:.1f}%）")
    print(f"    **实际付 taker 的出库腿**：{actual_taker} 笔")
    gap = theory_hit - actual_taker
    print(f"    ⇒ 差额（漏抓）**{gap}** 笔")

    if retreats:
        print(f"\n  ── 触及 {tp}bp 后 {grace:.0f}s 内的表现（n={len(retreats)}）──")
        print(f"    最高有利偏移：中位 {st.median(peak_after):+.2f}bp")
        print(f"    相对 {tp}bp 的变化（回撤为负）：中位 **{st.median(retreats):+.2f}bp**")
        neg = [r for r in retreats if r < -3.0]
        print(f"    回撤 > 3bp 的占比：{len(neg)/len(retreats)*100:.1f}%")
        print(f"    ⚠️ 若中位回撤 ≈ 0 ⇒ **{grace:.0f}s 宽限期没有吃掉利润**，")
        print(f"       漏抓（若有）来自别处（tick 粒度 / 闸门 / 每周期只判一次）")

    print(f"\n  ── 判读 ──")
    if theory_hit and abs(gap) <= max(3, theory_hit * 0.25):
        print(f"    ⇒ 理论命中 ≈ 实际触发 ⇒ **止盈正常工作，漏抓可忽略**")
    elif gap > 0:
        print(f"    ⇒ 有系统性漏抓（{gap} 笔）⇒ 需定位：")
        print(f"       · 引擎每个 tick 只判一次（tick 间隔 15s）⇒ 15s 内的尖峰会被跳过")
        print(f"       · `_exit_ok`（min_hold_seconds=0 ⇒ 恒真，不是原因）")
        print(f"       · 加仓侧被闸挡时是否连带影响止盈判定")
    else:
        print(f"    ⇒ 实际触发多于理论命中 ⇒ 说明部分仓位在 MFE < {tp}bp 时也被平了")
        print(f"       （可能是 ①′ 止损或 ② 超时腿，需看 skip 归因）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
