# -*- coding: utf-8 -*-
"""H203 运行态 vs 账本：逐币对账。

# 为什么需要

`lane_ledger` 的 docstring 明写"**账本是唯一事实源**"，
F301 的 `_reconcile_loaded_states` 也写着"分叉时以账本为准"。

但 2026-09-22 10:22 用户重置资金后出现一个可疑状态：
心跳里 HYPE 有 −3.214 的持仓，而账本按买减卖算出来的净额是 **0**。
若两者真分叉，那么：
  · 引擎的风险上限看到的是什么？
  · `_reconcile_loaded_states` 会不会把运行态的仓位抹掉？

# 口径（必须写清楚，否则对不上是正常的）

  · **账本净额** = Σ(买数量) − Σ(卖数量)，取自 `lane_ledger.meta_json` 的 `side`/`qty`
  · **运行态数量** = 心跳 `states.<sym>.qty`
  · 两者**应当相等**（账本是事实源，运行态是它的投影）

⚠️ 注意 `lane_ledger` **没有 side/qty 列**，它们只在 `meta_json` 里
（`record_fill` 会自动写入）。用错列会得到 `UndefinedColumn`。
"""
from __future__ import annotations

import json
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
    import psycopg
    import time

    hb = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    hb_ts = float(hb.get("ts") or 0.0)
    hb_age = (time.time() - hb_ts) if hb_ts else None
    live = {}
    for sym, sv in (hb.get("states") or {}).items():
        if isinstance(sv, dict):
            q = float(sv.get("qty") or 0.0)
            if abs(q) > 1e-9:
                live[sym] = q

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            since = (cur.fetchone() or [None])[0]
            cur.execute("""
                SELECT symbol,
                       sum(CASE WHEN meta_json->>'side'='buy'
                                THEN (meta_json->>'qty')::numeric ELSE 0 END) AS buys,
                       sum(CASE WHEN meta_json->>'side'='sell'
                                THEN (meta_json->>'qty')::numeric ELSE 0 END) AS sells,
                       count(*) AS legs, max(ts) AS last_ts
                FROM lane_ledger WHERE lane_id=%s GROUP BY symbol
            """, (LANE,))
            led = {r[0]: r[1:] for r in cur.fetchall()}

    print("=" * 94)
    print("H203  运行态 vs 账本 逐币对账")
    print("=" * 94)
    print(f"  stats_since = {since}")

    # ══ [F336 2026-09-22] **必须先检查心跳新鲜度** ══
    #
    # 现场：本工具报出 `SOL 运行态 +1.494 / 账本 0 ⇒ 分叉`，
    # 但账本明细显示
    #     10:33:05 buy  0.855  → 净 +1.494
    #     10:33:37 sell 1.494  → 净  0.000
    # ⇒ **心跳抓在 10:33:05 与 10:33:37 之间**，账本抓在其后。
    # 两边都对，只是**不是同一时刻的读数** —— 假分叉。
    #
    # 这是本项目反复出现的一类错误（把时点的投影当机制本身）：
    # 同一个错误今晚已犯 5 次（启动器父子对、毫秒唯一键、漏 exchange 维度、
    # 账本历史当持仓、以及这次）。
    #
    # ⇒ 任何"两个来源对不上"的结论，都必须先证明**两个读数是同时取的**。
    #    心跳有 `ts` 字段，账本有 `max(ts)` —— 两者相差超过一个 tick（15s）
    #    就必须拒绝下"分叉"结论。
    if hb_age is not None and hb_age > 20.0:
        print(f"\n  ⚠️⚠️ **心跳已过期 {hb_age:.0f} 秒**（>20s）")
        print(f"     ⇒ 运行态读数是 **{hb_age:.0f} 秒前**的快照，账本是**现在**的。")
        print(f"     ⇒ 两者的差异**极可能只是时间差**，不是分叉。")
        print(f"     ⇒ **本工具拒绝在此条件下判定分叉。** 请稍后重跑。")
        return 0
    print(f"  心跳年龄 = {hb_age:.1f}s（新鲜，可对账）" if hb_age is not None
          else "  心跳无 ts 字段，无法判断新鲜度 ⇒ 结论仅供参考")
    print(f"  心跳显示的非零持仓: {len(live)} 个")
    print()
    print(f"  {'币':<9}{'运行态qty':>12}{'账本买':>12}{'账本卖':>12}"
          f"{'账本净额':>12}{'差':>12}  判定")
    print("  " + "-" * 86)

    all_syms = sorted(set(live) | {s for s, v in led.items()
                                   if abs(float(v[0] or 0) - float(v[1] or 0)) > 1e-9})
    bad = 0
    for sym in all_syms:
        rq = live.get(sym)
        v = led.get(sym)
        if v is None:
            print(f"  {sym:<9}{(rq if rq is not None else 0):>12.3f}"
                  f"{'—':>12}{'—':>12}{'—':>12}{'—':>12}  ⚠️ 运行态有仓，账本里没有该币")
            bad += 1
            continue
        buys, sells, legs, last = float(v[0] or 0), float(v[1] or 0), int(v[2]), v[3]
        net = buys - sells
        live_q = rq if rq is not None else 0.0
        diff = live_q - net
        ok = abs(diff) < 1e-6
        if not ok:
            bad += 1
        verdict = "一致" if ok else "**分叉**"
        if not live_q and abs(net) < 1e-6:
            verdict = "都为空"
        print(f"  {sym:<9}{live_q:>12.3f}{buys:>12.3f}{sells:>12.3f}"
              f"{net:>12.3f}{diff:>+12.3f}  {verdict}")

    print()
    if bad == 0:
        print("  ✓ 全部一致：运行态 = 账本净额（账本确实是事实源）")
    else:
        print(f"  ✗ **{bad} 个币分叉** ⇒ 必须查清哪一边是对的")
        print()
        print("     为什么这要紧：")
        print("       · 引擎的风险上限（`max_net_directional_ratio` 等）读的是**共享账本**")
        print("       · `_reconcile_loaded_states`（F301）在分叉时**以账本为准**")
        print("       · 若运行态有仓而账本没有，那个仓位对风控**不可见**")
        print("         ⇒ 它既不计入敞口，也不会被计价进权益 ⇒ 静默漏仓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
