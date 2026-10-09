# -*- coding: utf-8 -*-
"""H131：为什么只有 4 个槽 —— 拆解每个候选币被哪道闸挡住。

# 期望

用户的意图是「10 个槽位都是 AI 选币轮询，在 1 档交易对中（30 个）找寻最优的前十」。
现在只有 4 个（3 个实测为正 + 1 个新币），必须查清是"合格币本来就少"还是
"有闸门过严 / 数据没采集"。

# 三道闸（按顺序）

  1. `has_depth`  —— 近 2h 有 20 档深度快照
     ⚠️ 深度采集由 `services/aster_ws_ingest.py --depth-symbols` 的**硬编码列表**决定。
        列表外的币**永远拿不到深度** ⇒ 不是"它不合格"，是"我们没采"。
  2. `p25 点差 ≥ 0.8bp` —— 太窄往返净为负
  3. `p25 点差 ≤ 4.0bp` —— 实测 >4bp 的币每周期净额全为负

用法：
    .venv\\Scripts\\python.exe scripts\\h131_why_only_n_slots.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from h125_selector_v3 import FEE_FLOOR_BP, dsn, market_url_of, pool  # noqa: E402


def main() -> int:
    murl = market_url_of(dsn())
    cands = sorted(pool(murl), key=lambda c: c["spread_p25_bp"])

    print("=" * 104)
    print("H131  候选池逐币拆解：被哪道闸挡住")
    print("=" * 104)
    print(f"  闸门：有深度=True  且  p25点差 ∈ [{FEE_FLOOR_BP}, 4.0] bp\n")

    print(f"  {'币':<10} {'p25点差':>8} {'段宽bp':>8} {'盘口更新':>10} {'有深度':>7}  被哪道闸挡")
    print("  " + "-" * 76)

    stats = {"ok": 0, "no_depth": 0, "too_narrow": 0, "too_wide": 0}
    passed, blocked_depth = [], []
    for c in cands:
        p25 = c["spread_p25_bp"]
        if not c.get("has_depth"):
            why = "① 无深度（我们没采集）"
            stats["no_depth"] += 1
            blocked_depth.append(c["symbol"])
        elif p25 < FEE_FLOOR_BP:
            why = f"② 点差过窄（{p25:.2f} < {FEE_FLOOR_BP}）"
            stats["too_narrow"] += 1
        elif p25 > 4.0:
            why = f"③ 点差过宽（{p25:.2f} > 4.0）"
            stats["too_wide"] += 1
        else:
            why = "**通过**"
            stats["ok"] += 1
            passed.append(c["symbol"])
        print(f"  {c['symbol']:<10} {p25:>8.2f} {c['width_bp']:>8.2f} {c['book_n']:>10,} "
              f"{str(c.get('has_depth')):>7}  {why}")

    print("\n" + "=" * 104)
    print("结论")
    print("=" * 104)
    print(f"  候选池 {len(cands)} 个")
    print(f"    通过              {stats['ok']:>3}   {passed}")
    print(f"    无深度（没采集）  {stats['no_depth']:>3}   {blocked_depth}")
    print(f"    点差过窄          {stats['too_narrow']:>3}")
    print(f"    点差过宽          {stats['too_wide']:>3}")

    print(f"\n  ⇒ 若「无深度」是主要瓶颈，那不是筛选严，是**深度采集列表太短**：")
    print(f"     深度由 `services/aster_ws_ingest.py --depth-symbols` 的硬编码列表决定，")
    print(f"     列表外的币永远拿不到 20 档深度 ⇒ 引擎无法为它报价。")
    print(f"     这 {stats['no_depth']} 个币里 p25 点差合格的可以补进采集列表：")
    ok_depth = [c["symbol"] for c in cands
                if not c.get("has_depth") and FEE_FLOOR_BP <= c["spread_p25_bp"] <= 4.0]
    print(f"       {ok_depth}  （{len(ok_depth)} 个）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
