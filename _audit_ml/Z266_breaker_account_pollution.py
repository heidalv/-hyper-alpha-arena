# -*- coding: utf-8 -*-
"""[§91 核查 2026-09-11] 熔断器的**证据窗是否被测试账户污染**（会不会改变 shadow 判定）？

线索（本轮）：30 天全层平仓 **1864 笔**里有 **400 笔（21.5%）**来自非 14 账户
（`#147/#149` 已归档测试残留、`#156`「150u」短期实验）——而 P21 的 DB 回填脚本当时
**没有按账户过滤**。熔断是**在线风控闸**，若它的滚动窗里混进测试数据，判定可能被改写。

本脚本逐通道对比「全账户窗」与「仅账户 14 窗」的有效窗口胜率（窗口 = min(MIN_N, 30)=15），
并检查**是否有通道的 shadow 判定发生翻转**（这才是"是否真缺陷"的判据）。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from _scope import DEFAULT_ACCOUNT_ID, describe_scope  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.exit.channel_breaker_gate import channel_of, is_protected  # noqa: E402
from sqlalchemy import text  # noqa: E402

NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")


def thresholds():
    try:
        min_n = int(float(os.environ.get("EXIT_CHANNEL_SHADOW_MIN_N", "30") or 30))
        max_wr = float(os.environ.get("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40") or 0.40)
    except (TypeError, ValueError):
        min_n, max_wr = 30, 0.40
    return min_n, max_wr, min(min_n, int(os.environ.get("SOURCE_ATTR_ROLLING_WINDOW", "30") or 30))


def main() -> int:
    min_n, max_wr, win = thresholds()
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select account_id, lower(coalesce(timeframe_tier,'?')), coalesce(close_reason,'?'),
                   ({NET}) > 0 as win, closed_at
            from paper_positions
            where status='closed' and closed_at >= now() - interval '30 day'
              and lower(coalesce(timeframe_tier,'')) in ('short','mid','long','research')
            order by closed_at
        """)).fetchall()
    finally:
        db.close()

    per = {"all": defaultdict(list), "live": defaultdict(list)}
    for acc, tier, reason, win_flag, _ts in rows:
        key = f"{tier}|{channel_of(reason)}"
        per["all"][key].append(1 if win_flag else 0)
        if int(acc) == DEFAULT_ACCOUNT_ID:
            per["live"][key].append(1 if win_flag else 0)

    st = json.loads((ROOT / "data" / "fusion_attribution.json").read_text(encoding="utf-8"))
    shadow = {k: bool(v) for k, v in (st.get("breaker_shadow") or {}).items()}

    print(describe_scope())
    print("=" * 104)
    print(f"熔断证据窗的账户污染核查（窗口={win} 笔；阈值 wr<{max_wr} 判 shadow）")
    print("=" * 104)
    print(f"{'通道':34s} {'全账户窗wr':>11} {'仅14窗wr':>10} {'样本(全/14)':>13} {'shadow(现)':>10} {'翻转?':>7}")
    flips = []
    for key in sorted(set(per["all"]) | set(per["live"])):
        a, l = per["all"].get(key, []), per["live"].get(key, [])
        if len(a) < win and len(l) < win:
            continue
        wr_a = (sum(a[-win:]) / win) if len(a) >= win else None
        wr_l = (sum(l[-win:]) / win) if len(l) >= win else None
        sh_a = (wr_a is not None and wr_a < max_wr)
        sh_l = (wr_l is not None and wr_l < max_wr)
        cur = shadow.get(key)
        flip = (sh_a != sh_l)
        if flip:
            flips.append((key, wr_a, wr_l, len(a), len(l), cur))
        tier, _, ch = key.partition("|")
        tag = "❗翻转" if flip else ""
        prot = "（保护）" if is_protected(ch) else ""
        print(f"{key:34s} {('—' if wr_a is None else f'{wr_a*100:.1f}%'):>11} "
              f"{('—' if wr_l is None else f'{wr_l*100:.1f}%'):>10} "
              f"{f'{len(a)}/{len(l)}':>13} {str(cur):>10} {tag:>7}{prot}")
    print("\n【判定】")
    if flips:
        print(f"  ❗ 有 {len(flips)} 个通道的 shadow 判定会因账户口径而**翻转** ⇒ 熔断确实被测试数据影响：")
        for k, wa, wl, na, nl, cur in flips:
            print(f"    {k}: 全账户 wr={wa*100:.1f}%（{na} 笔） vs 仅14 wr="
                  f"{'—' if wl is None else f'{wl*100:.1f}%'}（{nl} 笔）｜当前 shadow={cur}")
    else:
        print("  ✅ 没有任何通道的判定翻转 ⇒ 污染存在但**当前不改变**熔断行为（仍应修口径）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
