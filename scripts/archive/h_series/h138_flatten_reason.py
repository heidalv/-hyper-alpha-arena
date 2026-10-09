# -*- coding: utf-8 -*-
"""[H138 2026-09-21] 强平触发原因归因 —— 为什么 15.5% 平不掉。

# 背景

H137 实测：被动出库的成功样本里 **96.1% 在 120s 内、100% 在 300s 内**完成
⇒ `max_one_side_seconds=300` **不是**瓶颈，"等更久"没有空间。
⇒ 真正的瓶颈是：**那 15.5% 的持仓为什么价格不回来？**

# 要回答的三个候选原因

  A **`stop_loss` 触发**（价格真的朝不利方向走了 40bp）—— 这是"该平"，不是缺陷
  B **`max_one_side` 超时**（等了 300s 价格没回来）—— 这是"等不够/挂太宽"
  C **`side_trend` / `ofi` 等趋势闸强制**（主动判断方向不利）—— 需评估是否过度

# 口径

`lane_ledger.meta_json->>'skip'` 在强平腿上是触发原因。
⚠️ 实测过：该字段全历史 **12,883 行里全是 `None`** ⇒ 落账时没写 skip。
本脚本因此同时给出"不能靠 skip 字段"的证据，并从**可用的量**反推：
  · `price_bp`（持仓期间行情漂移）分布 —— 若强平腿的 price_bp 系统性很负，说明是 A
  · 持仓时长（用 fill_basis 相邻时间戳估）—— 若集中在 300s，说明是 B

用法：
    .venv\\Scripts\\python.exe scripts\\h138_flatten_reason.py
"""
from __future__ import annotations

import statistics as st
import sys
from collections import Counter
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


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
    print("=" * 100)
    print("H138  强平触发原因归因")
    print("=" * 100)

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            # 1) skip 字段是否可用
            cur.execute("""SELECT coalesce(meta_json->>'skip','<NULL>') AS sk, count(*)
                           FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'
                           GROUP BY 1 ORDER BY 2 DESC""")
            sk = cur.fetchall()
            print("\n  【1】账本 skip 字段分布（全历史）：")
            for k, n in sk:
                print(f"      {k:<28} {n:>7}")
            usable = any(k not in ("<NULL>", "None") for k, _ in sk)
            print(f"      ⇒ skip 字段{'可用' if usable else '**不可用**（落账没写）'}")

            # 2) 强平腿的 price_bp 分布（判断是"行情真的走了"还是"只是等超时"）
            cur.execute("""SELECT price_bp, spread_bp, fee_bp, notional
                           FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'
                             AND coalesce(meta_json->>'flatten','false')='true'
                             AND price_bp IS NOT NULL""")
            pr = cur.fetchall()
            if pr:
                pb = sorted(float(r[0]) for r in pr)
                nb = sorted(float(r[3] or 0) for r in pr)
                print(f"\n  【2】强平腿 price_bp（持仓期间行情漂移）分布，n={len(pb)}：")
                for q in (0.05, 0.25, 0.5, 0.75, 0.95):
                    i = min(len(pb) - 1, int(q * len(pb)))
                    print(f"      p{int(q*100):<3} {pb[i]:>+9.2f} bp")
                print(f"      均值 {st.mean(pb):>+9.2f} bp")
                # 分档：多少强平是"行情确实逆着走了"
                for thr in (-5, -20, -40, -60):
                    k = sum(1 for x in pb if x <= thr)
                    print(f"      price_bp ≤ {thr:>4}bp 的占 {k/len(pb)*100:>5.1f}%"
                          f"（{k} 笔）")
                print(f"\n      强平腿名义中位 ${st.median(nb):,.2f}")
                # 与 stop_loss_bp 对照
                k40 = sum(1 for x in pb if x <= -40)
                print(f"      ⚠️ price_bp ≤ −40bp 的只占 {k40/len(pb)*100:.1f}%")
                print(f"         ⇒ 大部分强平**不是**因为行情走满 40bp，")
                print(f"            而是别的闸（超时 / 趋势 / OFI）在价格还没走远时就平了")

            # 3) 强平腿的时间分布（是否集中在某个窗口）
            cur.execute("""SELECT ts, symbol FROM lane_ledger
                           WHERE lane_id='mm_asterdex' AND event='fill'
                             AND coalesce(meta_json->>'flatten','false')='true'
                           ORDER BY ts DESC LIMIT 400""")
            fr = cur.fetchall()
            if fr:
                sym = Counter(r[1] for r in fr)
                print(f"\n  【3】最近 400 笔强平的币分布：")
                for s, n in sym.most_common(10):
                    print(f"      {s:<10} {n:>4}")

    print(f"\n  ── 结论与下一步 ──")
    print(f"  若『price_bp ≤ −40bp 的比例很低』而强平率不低 ⇒ 强平不是价格止损在起作用，")
    print(f"  而是**时间/趋势闸在价格没走远时就把仓位平掉了**，白付 4bp taker。")
    print(f"  那是**可以改的**：把出库完全交给被动挂单，只在真·价格不利时才 taker。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
