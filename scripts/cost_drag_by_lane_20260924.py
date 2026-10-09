# -*- coding: utf-8 -*-
"""[2026-09-24 第16轮] **成本拖累（手续费 + 资金费）分车道核算**（09-15 后样本）。

回答："小盈利大亏损"里，成本吃掉多少？中线和长线的成本结构是否不同？
口径：
  · 毛盈亏 = `paper_positions`（closed/liquidated 用 unrealized_pnl 存档值；open 用当前浮动）
  · 手续费 = `paper_orders.fee`（按 trade_nature 归到车道；开仓+平仓都算）
  · 资金费 = `paper_funding_ledger.payment`（按 tier 归；缺失时用 position_id 回查）
只读。
"""
from __future__ import annotations

import collections
import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE_OF_NATURE = {
    "trend_follow": "long", "position": "long",
    "swing": "mid", "intraday": "mid", "scalp": "short",
}


def main() -> int:
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        # 1) 毛盈亏（按 tier）
        cur.execute(
            """select timeframe_tier, count(*) n,
                      coalesce(sum(case when status in ('closed','liquidated')
                                        then unrealized_pnl else unrealized_pnl end),0) gross,
                      sum(case when status='open' then 1 else 0 end) n_open
               from paper_positions
               where account_id=14 and opened_at > timestamp '2026-09-15 00:00:00'
               group by 1 order by 1""")
        pos = {r[0]: {"n": r[1], "gross": float(r[2] or 0), "n_open": int(r[3] or 0)} for r in cur.fetchall()}
        # 2) 手续费（按 nature → 车道）
        cur.execute(
            """select coalesce(trade_nature,'(none)') nature, count(*) n, coalesce(sum(fee),0) fee
               from paper_orders
               where account_id=14 and created_at > timestamp '2026-09-15 00:00:00' and fee is not null
               group by 1""")
        fees = collections.defaultdict(lambda: [0, 0.0])
        for nature, n, fee in cur.fetchall():
            lane = LANE_OF_NATURE.get(str(nature).lower(), "其它:%s" % nature)
            fees[lane][0] += n
            fees[lane][1] += float(fee or 0)
        # 3) 资金费（按 tier，缺失回查 position）
        cur.execute(
            """select coalesce(f.tier, p.timeframe_tier, '(none)') tier,
                      count(*) n, coalesce(sum(f.payment),0) pay
               from paper_funding_ledger f
               left join paper_positions p on p.id = f.position_id
               where f.account_id=14 and f.settled_at > timestamp '2026-09-15 00:00:00'
               group by 1""")
        fund = {r[0]: {"n": r[1], "pay": float(r[2] or 0)} for r in cur.fetchall()}

    print("== 09-15 后 分车道成本核算 ==")
    print("  %-6s %5s %10s %10s %10s %10s %10s" % ("车道", "笔数", "毛盈亏", "手续费", "资金费", "净额", "成本/毛%"))
    for lane in ("mid", "long", "short"):
        p = pos.get(lane, {"n": 0, "gross": 0.0, "n_open": 0})
        f_n, f_sum = fees.get(lane, [0, 0.0])
        fu = fund.get(lane, {"n": 0, "pay": 0.0})
        net = p["gross"] - f_sum + fu["pay"]
        drag = (f_sum + abs(fu["pay"])) / abs(p["gross"]) * 100 if p["gross"] else float("nan")
        print("  %-6s %5d %+10.2f %10.2f %10.2f %+10.2f %9.0f%%"
              % (lane, p["n"], p["gross"], f_sum, fu["pay"], net, drag))
        print("         （在仓 %d 笔；订单 %d 条；资金费事件 %d 次）" % (p["n_open"], f_n, fu["n"]))
    # 其它 nature（未被映射的）
    for lane, (n, s) in sorted(fees.items()):
        if lane not in ("mid", "long", "short"):
            print("  %-6s 订单 %d 条 手续费 %.2f（未映射 nature）" % (lane, n, s))
    print("\n== 参照：全账户 ==")
    tot_gross = sum(v["gross"] for v in pos.values())
    tot_fee = sum(v[1] for v in fees.values())
    tot_fund = sum(v["pay"] for v in fund.values())
    print("  毛盈亏 %+.2f | 手续费 -%.2f | 资金费 %+.2f | 净 %+.2f"
          % (tot_gross, tot_fee, tot_fund, tot_gross - tot_fee + tot_fund))
    print("  每笔手续费: mid %.2f / long %.2f"
          % (fees.get("mid", [1, 0])[1] / max(1, pos.get("mid", {"n": 1})["n"]),
             fees.get("long", [1, 0])[1] / max(1, pos.get("long", {"n": 1})["n"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
