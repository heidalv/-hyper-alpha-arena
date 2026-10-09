# -*- coding: utf-8 -*-
"""重置后基线锚定检查。

# 为什么要单独做

2026-09-22 10:22:37 用户重置了模拟账户资金。重置之后：

  · 权益回到 $300（干净）
  · `lane_registry.meta.stats_since` 被更新为重置时刻 ⇒ 账本统计口径也跟着重锚

但**有一个容易被忽略的风险**：运行态里可能仍有**重置前开出的持仓**。
若如此，它们的未实现盈亏会混进新基准 ⇒ 之后所有"这个时代赚亏多少"
都建立在一个被污染的起点上。

本脚本回答三个问题：
  1. 当前 `stats_since` 是什么（时代的唯一起点）
  2. 账户权益与"起始本金"差多少
  3. 每个持仓的 `opened_ts` 是**在时代起点之前还是之后**

# 单位陷阱（我为此错过两次）

`SymbolState.opened_ts` 是 **epoch 毫秒**（不是秒）。
心跳里 `states.<sym>.opened_ts` 直接透传该值。
用秒去减会得到荒谬的"2980 万分钟前"。
⇒ 本脚本统一用 `datetime.fromtimestamp(ms/1000)` 转换，不做手算。
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone

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

    hb = {}
    try:
        hb = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ✗ 读心跳失败: {e}")
        return 1

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            since_s = (cur.fetchone() or [None])[0]
            cur.execute(
                "SELECT total_equity, available_balance, realized_pnl"
                " FROM arbitrage_paper_accounts WHERE name LIKE 'MM%' ORDER BY id LIMIT 1")
            acct = cur.fetchone()
            cur.execute(
                "SELECT state_json FROM lane_runtime_state WHERE lane_id=%s", (LANE,))
            r = cur.fetchone()

    since = datetime.fromisoformat(since_s) if since_s else None
    print("=" * 92)
    print("重置后基线锚定检查")
    print("=" * 92)
    print(f"  现在            {datetime.now().astimezone():%Y-%m-%d %H:%M:%S}")
    print(f"  stats_since     {since:%Y-%m-%d %H:%M:%S %Z}" if since else "  stats_since     (读不到)")
    if acct:
        print(f"  账户 total_eq   ${float(acct[0] or 0):.2f}"
              f"   avail ${float(acct[1] or 0):.2f}   realized ${float(acct[2] or 0):.4f}")
    print(f"  心跳 equity     ${float(hb.get('equity') or 0):.2f}")
    print(f"  ok={hb.get('ok')}  ticks={hb.get('ticks')}  fills={hb.get('fills')}")

    # ── 持仓时间核对（单位：epoch 毫秒）──
    print("\n  持仓（opened_ts 为 epoch 毫秒）:")
    states = hb.get("states") or {}
    if not states:
        states = {}
        if r and r[0]:
            st = r[0] if isinstance(r[0], dict) else json.loads(r[0] or "{}")
            states = st.get("states") or {}
    any_pos = False
    for sym, sv in (states.items() if isinstance(states, dict) else []):
        if not isinstance(sv, dict):
            continue
        q = float(sv.get("qty") or 0.0)
        if abs(q) <= 1e-9:
            continue
        any_pos = True
        ot = float(sv.get("opened_ts") or 0.0)
        if ot > 1e11:          # 毫秒
            t = datetime.fromtimestamp(ot / 1000, tz=timezone.utc).astimezone()
            age_min = (datetime.now().astimezone() - t).total_seconds() / 60.0
            flag = ""
            if since:
                flag = ("  ⚠️ **开于重置之前**" if t < since else "  ✓ 开于重置之后")
            print(f"    {sym:<8} qty={q:>12.3f}  开于 {t:%H:%M:%S}（{age_min:.1f} 分钟前）{flag}")
        else:
            print(f"    {sym:<8} qty={q:>12.3f}  opened_ts={ot:.0f}（看起来不是毫秒，需人工确认）")
    if not any_pos:
        print("    （空仓）")

    print("\n  判据：")
    print("    · 若无持仓，或全部持仓都开在 stats_since 之后 ⇒ 基准干净，可直接开始测量")
    print("    · 若有持仓开在 stats_since 之前 ⇒ 它的未实现盈亏会污染新基准，")
    print("      应先让它出库（或手动平掉）再开始算")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
