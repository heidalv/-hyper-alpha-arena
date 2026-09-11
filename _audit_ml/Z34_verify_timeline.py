# -*- coding: utf-8 -*-
"""Z34：核验两件事——
(1) #4638 VIRTUAL 的 3 个平仓事件是否为重复平仓（金额是否只扣一次）；
(2) 门/豁免的配置时间线：哪些仓位开在门上线前、哪些开在 long 豁免生效后。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")


def main() -> int:
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        print("=== (1) #4638 VIRTUAL 的全部事件与持仓终值 ===")
        for r in c.execute(text("""
            select id, event_type, exit_channel, quantity, price, pnl, fee, close_ratio,
                   pnl_pct_at_event, metadata_json, created_at
            from position_exit_events where position_id=4638 order by created_at
        """)).fetchall():
            md = r[9]
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except Exception:
                    md = {}
            print(f"  {str(r[10])[:23]} {str(r[1]):<22}{str(r[2] or '-'):<22}"
                  f"qty={r[3]} px={r[4]} pnl={r[5]} fee={r[6]} ratio={r[7]}")
            if isinstance(md, dict):
                keep = {k: md.get(k) for k in
                        ("reason", "channel", "exit_source", "final_pnl", "partial_pnl",
                         "mfe_pnl_pct", "mae_pnl_pct", "mae_usd") if k in md}
                if keep:
                    print(f"      meta={keep}")
        for r in c.execute(text("""
            select id, status, size, original_size, close_price, close_reason,
                   unrealized_pnl, partial_realized_pnl, partial_fee_paid, reduce_count,
                   opened_at, closed_at
            from paper_positions where id=4638
        """)).fetchall():
            print(f"  持仓#{r[0]} status={r[1]} size={r[2]}/{r[3]} close={r[4]} "
                  f"reason={str(r[5])[:30]} uPnL={r[6]} partial={r[7]} fee={r[8]} "
                  f"reduce_count={r[9]}")
            print(f"    总USD={float(r[6] or 0)+float(r[7] or 0)-float(r[8] or 0):+.4f}")

        print("\n=== (1b) 其它仓位是否也有多平仓事件 ===")
        for r in c.execute(text("""
            select position_id, count(*) n,
                   count(distinct exit_channel) ch,
                   string_agg(distinct event_type, ',') types
            from position_exit_events
            where created_at >= '2026-09-09 00:00:00+08'
            group by 1 having count(*) > 1 order by 2 desc
        """)).fetchall():
            print(f"  #{r[0]} 事件数={r[1]} 通道数={r[2]} 类型={r[3]}")

        print("\n=== (2) 时间线：开仓时刻 vs 门/豁免配置 ===")
        for r in c.execute(text("""
            select id, symbol, timeframe_tier, trade_nature, opened_at,
                   case when opened_at < '2026-09-09 17:00:00+08' then '门上线前'
                        when opened_at < '2026-09-10 00:59:00+08' then '门覆盖 mid+long'
                        else 'long 豁免生效后' end phase
            from paper_positions
            where opened_at >= '2026-09-08 00:00:00+08' and opened_at <= '2026-09-10 10:00:00+08'
            order by opened_at
        """)).fetchall():
            print(f"  #{r[0]:<5}{str(r[1]):<10}{str(r[2]):<6}{str(r[3] or ''):<14}"
                  f"{str(r[4])[:19]}  {r[5]}")

        print("\n=== (2b) 该窗口内 mid/long 是否有仓位在「门覆盖 mid+long」期间开仓 ===")
        n = c.execute(text("""
            select count(*) from paper_positions
            where opened_at >= '2026-09-09 17:00:00+08'
              and opened_at < '2026-09-10 00:59:00+08'
              and trade_nature in ('swing','trend_follow','position')
        """)).scalar()
        print(f"  门全程覆盖期间（9/9 17:00–9/10 00:59）mid/long 新开 = {n} 笔")
        n2 = c.execute(text("""
            select count(*) from paper_positions
            where opened_at >= '2026-09-10 00:59:00+08'
              and trade_nature in ('swing','trend_follow','position')
        """)).scalar()
        print(f"  long 豁免生效后（9/10 00:59 起）mid/long 新开 = {n2} 笔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
