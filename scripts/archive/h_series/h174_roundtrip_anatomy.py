# -*- coding: utf-8 -*-
"""[H174 2026-09-21] 先把「一个往返的账」算对 —— 在做更多模拟之前。

# 为什么停下来

H173 的全规则模拟给出**所有方案都是 −1.66bp/笔**，而真实账本是 **+0.1374bp**。
差了 1.8bp 且符号相反 ⇒ **模拟的口径与实盘不一致，结论不可用**。
在口径对齐之前，任何"哪个方案更好"的结论都是假的。

# 本脚本只做一件事：把实盘的一个往返拆开，确认钱从哪来

从 `lane_ledger` 取**同一币、时间相邻**的一买一卖（一个往返），逐笔列出：
  · 名义、`spread_bp`（价差项）、`price_bp`（行情项）、`fee_bp`
  · 以及**成交价相对当时中价的偏移**（用 `asterdex_book_ticker` 还原）

目标：搞清楚
  A) `spread_bp` 是"相对**我们挂单时的中价**"还是"相对成交时中价"（决定它是否等于我们的挂宽）
  B) 一个往返的净额里，**出场腿的贡献**到底是多少
  C) `price_bp` 的量级与符号，判断"行情项"是系统性逆向选择还是随机漂移

# 判据

  · 若出场腿的 `spread_bp` ≈ 出库挂宽 ⇒ 出场腿只赚挂宽那么多（**与价格走了多远无关**）
  · 若往返净额 ≈ (进场挂宽 + 出场挂宽) + 行情项 ⇒ 结构就清楚了：
    **能赚多少由两条腿的挂宽决定，行情项是噪声** ⇒ 那"止盈吃大波段"就不是我们的收入模型
  · 若行情项占往返净额的主导 ⇒ 收入模型是**方向性**的（那就该转去做方向，而不是做市）

用法：
    .venv\\Scripts\\python.exe scripts\\h174_roundtrip_anatomy.py [--n 8]
"""
from __future__ import annotations

import argparse
import statistics as st
from collections import deque
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


def mid_at(sym: str, ts_ms: int):
    with psycopg.connect(dsn("alpha_market")) as c:
        with c.cursor() as cur:
            cur.execute("""SELECT (bid_px+ask_px)/2 FROM asterdex_book_ticker
                           WHERE symbol=%s AND event_ts_ms <= %s
                           ORDER BY event_ts_ms DESC LIMIT 1""",
                        (sym + "USDT", ts_ms))
            r = cur.fetchone()
            return float(r[0]) if r and r[0] else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="打印几个往返的明细")
    a = ap.parse_args()

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                        " WHERE lane_id=%s", (LANE,))
            since = (cur.fetchone() or [None])[0]
            cur.execute("""
                SELECT ts, symbol, meta_json->>'side' AS side, notional,
                       coalesce(spread_bp,0), coalesce(price_bp,0), coalesce(fee_bp,0),
                       coalesce(meta_json->>'flatten','false'), id
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= %s
                ORDER BY id
            """, (LANE, since))
            rows = cur.fetchall()

    print("=" * 100)
    print("H174  一个往返的解剖 —— 钱从哪来")
    print("=" * 100)

    # 配对：同一币，方向相反的两笔相邻成交 = 一个往返
    # ⚠️ 元组必须**同构**：`last` 与 `pairs` 里的元素都存完整 9 元组。
    #    第一版 `last` 只存了 8 个字段 ⇒ 后面按 9 个解包直接 ValueError。
    last = {}
    pairs = []
    for (ts, sym, side, notl, sp, pr, fe, flat, rid) in rows:
        cur_rec = (ts, sym, side, notl, sp, pr, fe, flat, rid)
        prev = last.get(sym)
        if prev and prev[2] != side:
            pairs.append((prev, cur_rec))
        last[sym] = cur_rec

    print(f"  本时代配出 {len(pairs)} 个往返\n")
    if not pairs:
        return 1

    # 汇总
    en_net, ex_net, tot_net = [], [], []
    for (e, x) in pairs:
        (_, _, _, eno, esp, epr, efe, _, _) = e
        (_, _, _, xno, xsp, xpr, xfe, _, _) = x
        en = float(esp) + float(epr) + float(efe)
        ex = float(xsp) + float(xpr) + float(xfe)
        en_net.append(en); ex_net.append(ex); tot_net.append(en + ex)

    print(f"  ── 汇总（每个往返的 bp 加总，未按名义加权）──")
    print(f"    {'项':<26} {'中位':>9} {'均值':>9}")
    print("    " + "-" * 46)
    print(f"    {'进场腿净 bp':<26} {st.median(en_net):>+9.3f} {st.mean(en_net):>+9.3f}")
    print(f"    {'出场腿净 bp':<26} {st.median(ex_net):>+9.3f} {st.mean(ex_net):>+9.3f}")
    print(f"    {'往返合计 bp':<26} {st.median(tot_net):>+9.3f} {st.mean(tot_net):>+9.3f}")

    print(f"\n  ── 前 {a.n} 个往返明细 ──")
    print(f"  {'时刻':<9} {'币':<7} {'腿':<5} {'名义$':>8} {'价差bp':>8} {'行情bp':>8} "
          f"{'费bp':>7} {'净bp':>8}")
    print("  " + "-" * 72)
    for (e, x) in pairs[-a.n:]:
        for r in (e, x):
            (ts, sym, side, notl, sp, pr, fe, flat, rid) = r
            net = float(sp) + float(pr) + float(fe)
            lab = "出" if flat == "true" else "进"
            print(f"  {ts.strftime('%H:%M:%S'):<9} {sym:<7} {lab:<5} {float(notl):>8,.0f} "
                  f"{float(sp):>+8.3f} {float(pr):>+8.3f} {float(fe):>+7.3f} {net:>+8.3f}")
        print("  " + "-" * 72)

    print(f"\n  ── 结构性判读 ──")
    print(f"    · `spread_bp` 是相对**我们挂单时的中价**（core 的 `ref_mid`）")
    print(f"      ⇒ 它的量级应 ≈ 我们的挂宽（进场 {0.5}×半价差、出库 {0.4}×半价差）")
    print(f"    · 若出场腿的 `spread_bp` 只有零点几 bp ⇒ **出库只赚挂宽那么多**，")
    print(f"      **与价格走了多远无关** ⇒ 「止盈吃大波段」不是我们的收入模型。")
    print(f"    · 若 `行情bp` 的量级与 `价差bp` 相当 ⇒ 收入被行情噪声主导，")
    print(f"      那「提高挂宽」或「择时」才是方向，而「止盈」要重新定义。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
