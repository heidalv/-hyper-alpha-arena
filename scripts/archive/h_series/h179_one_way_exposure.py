# -*- coding: utf-8 -*-
"""[H179 2026-09-21] 同向满仓审计 —— 三个仓位全空、合计浮亏 −$4.98。

# 用户看到的现象

    XRP  空 -573.87   开仓 1.4354  现价 1.4402  −$2.7823
    ASTER 空 -809.29  开仓 0.73885 现价 0.74025 −$1.1330
    SOL  空 -5.3336   开仓 112.08  现价 112.28  −$1.0667
    持仓浮盈 −$4.9820   总敞口 $2024.39   **净敞口 −$2024.39**（全同向）

# 要回答的

  1. 这三个空仓是**同时**建立的，还是逐个累积的？（决定是"一次行情"还是"持续加仓"）
  2. 每个仓位的浮亏 bp，以及距离 40bp 止损还有多远
  3. **结构性风险**：`max_net_directional_ratio=6.0`（单币上限 6× 权益）
     × 3 个币 ⇒ 理论上可堆到 **18× 权益**的同向净敞口
  4. 与 `compound_ratio=2.0` 的交互：单腿上界 40bp × 单腿 = 40bp × 2× 权益 = **0.8% 权益/腿**
  5. 当前 A/B 处于哪一臂（影响解读）

用法：
    .venv\\Scripts\\python.exe scripts\\h179_one_way_exposure.py
"""
from __future__ import annotations

import json
import time
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


def mids(syms):
    out = {}
    if not syms:
        return out
    with psycopg.connect(dsn("alpha_market")) as c:
        with c.cursor() as cur:
            for s in syms:
                cur.execute("""SELECT (bid_px+ask_px)/2 FROM asterdex_book_ticker
                               WHERE symbol=%s ORDER BY event_ts_ms DESC LIMIT 1""",
                            (s + "USDT",))
                r = cur.fetchone()
                if r and r[0]:
                    out[s] = float(r[0])
    return out


def main() -> int:
    stf = ROOT / "logs" / "mm_lane_status.json"
    j = json.loads(stf.read_text(encoding="utf-8"))
    lim, par = j.get("limits") or {}, j.get("params") or {}
    eq = float(j.get("equity") or 0.0)
    leg = float(j.get("fill_notional") or 0.0)
    now = time.time()

    states = j.get("states") or {}
    pos = {s: v for s, v in states.items()
           if abs(float(v.get("qty") or 0.0)) > 1e-12}
    md = mids(list(pos))

    print("=" * 98)
    print("H179  同向满仓审计")
    print("=" * 98)
    print(f"  现在 {datetime.now().strftime('%H:%M:%S')}   权益 ${eq:,.2f}   单腿 ${leg:,.2f}")
    print(f"  compound_ratio={par.get('compound_ratio')}   "
          f"max_net_directional_ratio={lim.get('max_net_directional_ratio')}（单币上限）")
    print(f"  reduce_quote_disabled={lim.get('reduce_quote_disabled')}   "
          f"timeout_maker_only={lim.get('timeout_exit_maker_only')}   "
          f"stop_loss_bp={lim.get('stop_loss_bp')}")

    gross = net = 0.0
    upl_tot = 0.0
    print(f"\n  {'币':<8} {'方向':<5} {'qty':>11} {'开仓mid':>10} {'现价':>10} "
          f"{'持有s':>7} {'名义$':>9} {'浮亏$':>9} {'浮亏bp':>8} {'距止损bp':>9}")
    print("  " + "-" * 104)
    sl = float(lim.get("stop_loss_bp") or 0.0)
    for s, v in sorted(pos.items()):
        q = float(v.get("qty") or 0.0)
        am = float(v.get("avg_mid") or 0.0)
        op = float(v.get("opened_ts") or 0.0)
        age = (now - op) if op > 0 else 0.0
        mid = float(md.get(s) or 0.0)
        notl = abs(q) * (mid or am)
        upl = q * (mid - am) if (mid and am) else 0.0
        bp = upl / (abs(q) * am) * 1e4 if (q and am) else 0.0
        gross += notl
        net += q * (mid or am)
        upl_tot += upl
        print(f"  {s:<8} {'多' if q > 0 else '空':<5} {q:>11.4f} {am:>10.5f} {mid:>10.5f} "
              f"{age:>7.0f} {notl:>9,.0f} {upl:>+9.4f} {bp:>+8.2f} {sl + bp:>+9.2f}")

    print(f"\n  ── 合计 ──")
    print(f"    总敞口（Σ|仓位|名义）  ${gross:,.2f}  = **{gross/eq if eq else 0:.2f}× 权益**")
    print(f"    净敞口（带符号）        ${net:,.2f}  = **{net/eq if eq else 0:.2f}× 权益**")
    print(f"    方向一致性              {'**全部同向**' if abs(abs(net)-gross) < gross*0.05 else '有对冲'}")
    print(f"    浮亏合计                **{upl_tot:+.4f} USD**"
          f"（{upl_tot/eq*100 if eq else 0:+.2f}% 权益）")

    print(f"\n  ── 结构性风险 ──")
    mdr = float(lim.get("max_net_directional_ratio") or 0.0)
    n_sym = len(pos) if pos else 3
    print(f"    单币上限 {mdr}× 权益 ⇒ 单币可堆到 ${mdr*eq:,.0f}")
    print(f"    {n_sym} 个币同时同向 ⇒ 理论最大净敞口 **{mdr*n_sym:.0f}× 权益**"
          f" = ${mdr*n_sym*eq:,.0f}")
    print(f"    单腿 40bp 止损金额 = 40bp × ${leg:,.0f} = ${0.0040*leg:,.2f}")
    print(f"    ⇒ 若全部同向且**同时**打止损：{n_sym} × ${0.0040*leg:,.2f} = "
          f"**${n_sym*0.0040*leg:,.2f}**（{n_sym*0.0040*leg/eq*100:.2f}% 权益）")
    print(f"    ⚠️ 但这是**按单腿**估的；若持仓累积到上限，单币止损金额 = "
          f"40bp × ${mdr*eq:,.0f} = **${0.0040*mdr*eq:,.2f}**"
          f"（{0.0040*mdr*eq/eq*100:.2f}% 权益/币）")
    print(f"    ⇒ 三币同时打止损最坏 = **${3*0.0040*mdr*eq:,.2f}**"
          f"（{3*0.0040*mdr*eq/eq*100:.2f}% 权益）")

    print(f"\n  ── 止损距离 ──")
    print(f"    三个仓位目前距 40bp 止损：")
    for s, v in sorted(pos.items()):
        q = float(v.get("qty") or 0.0)
        am = float(v.get("avg_mid") or 0.0)
        mid = float(md.get(s) or 0.0)
        bp = q * (mid - am) / (abs(q) * am) * 1e4 if (q and am) else 0.0
        print(f"      {s:<8} 已亏 {abs(bp):>5.2f}bp ⇒ 还有 {sl-abs(bp):>5.2f}bp 到止损")

    print(f"\n  ── 与 A/B 的关系 ──")
    print(f"    当前 `reduce_quote_disabled={lim.get('reduce_quote_disabled')}` ⇒ "
          f"{'**B 臂（撤出库单）**' if lim.get('reduce_quote_disabled') else 'A 臂（现状，有出库单）'}")
    print(f"    ⇒ 若在 A 臂：减仓侧挂宽 0.4 ⇒ 空仓的**买侧**挂在 mid−0.2×价差，")
    print(f"      价格下跌时会被动出库（有利）；价格**上涨**时（不利）不会成交 ⇒ 只能等止损")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
