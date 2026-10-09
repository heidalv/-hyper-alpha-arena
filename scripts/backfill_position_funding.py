"""[2026-09-20] 资金费**落库 + 回填**（幂等；口径与 `estimate_position_funding_20260920.py` 完全一致）。

口径（用户 2026-09-20 批准：三车道全补、口径统一、回填已平仓）：
  结算网格 UTC 00/08/16（每日 3 次，可用 FUNDING_SETTLE_HOURS_UTC 改）；
  结算时刻费率取 `alpha_market.perp_funding` 该时刻前最近一条（容忍 6h，表是 5 分钟轮询快照）；
  名义 = size × mark_price；**多头付、空头收**；持仓跨结算时刻才算。

只加不改：新建 `position_funding_events`（逐笔可审计，`(position_id, funding_ts)` 唯一 ⇒ 幂等）
        + `paper_positions.funding_paid / funding_received`（由事件表重算，可重复执行）。
不触碰任何交易决策、不改手续费口径。

用法：
  .venv\\Scripts\\python.exe scripts/backfill_position_funding.py --days 240            # 干跑
  .venv\\Scripts\\python.exe scripts/backfill_position_funding.py --days 240 --apply    # 落库
"""
from __future__ import annotations

import argparse
import bisect
import io
import os
import sys
from typing import Dict, List

import psycopg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from estimate_position_funding_20260920 import (  # noqa: E402
    ARENA, MARKET, CST, UTC, EXCH_PREF, TOL_H, load_funding_series, load_positions, settle_times,
)

DDL = [
    """create table if not exists position_funding_events (
           id bigserial primary key,
           position_id integer not null,
           account_id integer,
           symbol varchar(32),
           exchange varchar(16),
           funding_ts timestamptz not null,
           funding_rate double precision,
           mark_price double precision,
           notional double precision,
           amount_usd double precision,
           side varchar(8),
           created_at timestamptz default now(),
           constraint uq_pfe_pos_ts unique (position_id, funding_ts)
       )""",
    "create index if not exists ix_pfe_position on position_funding_events(position_id)",
    "alter table paper_positions add column if not exists funding_paid double precision default 0",
    "alter table paper_positions add column if not exists funding_received double precision default 0",
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="240")
    ap.add_argument("--apply", action="store_true", help="不加则只干跑打印")
    a = ap.parse_args(argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    pos = load_positions(a.days)
    span: Dict[str, List[int]] = {}
    for p in pos:
        o = int(p["opened_at"].replace(tzinfo=CST).timestamp()) - 7200
        c = int(p["closed_at"].replace(tzinfo=CST).timestamp()) + 3600
        b = span.setdefault(p["symbol"], [o, c])
        b[0], b[1] = min(b[0], o), max(b[1], c)
    events, n_pos_hit, n_miss = [], 0, 0
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        series_cache = {s: load_funding_series(cur, s, lo, hi) for s, (lo, hi) in span.items()}
    for p in pos:
        o_utc = p["opened_at"].replace(tzinfo=CST).astimezone(UTC)
        c_utc = p["closed_at"].replace(tzinfo=CST).astimezone(UTC)
        sts = settle_times(o_utc, c_utc)
        ser = series_cache.get(p["symbol"]) or {}
        series, exch = None, None
        for ex in EXCH_PREF:
            if ex in ser and ser[ex][0]:
                series, exch = ser[ex], ex
                break
        sign = 1.0 if str(p["side"]).lower().startswith("l") else -1.0
        got = 0
        for t in sts:
            if series is None:
                n_miss += 1
                continue
            te = int(t.timestamp())
            j = bisect.bisect_right(series[0], te) - 1
            if j < 0 or (te - series[0][j]) > TOL_H * 3600:
                n_miss += 1
                continue
            mark = series[2][j] or float(p["entry_price"])
            notional = float(p["size"]) * mark
            rate = series[1][j]
            events.append((p["id"], p["account_id"], p["symbol"], exch, t, rate, mark,
                           notional, notional * rate * sign, "long" if sign > 0 else "short"))
            got += 1
        if got:
            n_pos_hit += 1
    tot = sum(e[8] for e in events)
    print("干跑：%d 笔持仓 → %d 个结算事件；命中仓 %d 笔；漏取结算点 %d 个；资金费合计 %+.4f USD"
          % (len(pos), len(events), n_pos_hit, n_miss, tot))
    if not a.apply:
        print("（未加 --apply，未写库）")
        return 0
    with psycopg.connect(ARENA, autocommit=True) as ac:
        cur = ac.cursor()
        cur.execute("SET app.is_admin='on'")
        for stmt in DDL:
            cur.execute(stmt)
        ins = """insert into position_funding_events
                 (position_id, account_id, symbol, exchange, funding_ts, funding_rate, mark_price,
                  notional, amount_usd, side)
                 values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 on conflict (position_id, funding_ts) do nothing"""
        cur.executemany(ins, events)
        cur.execute("""update paper_positions p set
                         funding_paid = coalesce(f.paid, 0),
                         funding_received = coalesce(f.recv, 0)
                       from (select position_id,
                                    sum(case when amount_usd > 0 then amount_usd else 0 end) paid,
                                    sum(case when amount_usd < 0 then -amount_usd else 0 end) recv
                             from position_funding_events group by position_id) f
                       where f.position_id = p.id""")
        cur.execute("select count(*), round(sum(amount_usd)::numeric,4) from position_funding_events")
        print("落库后事件表：行数/合计 = %s" % (cur.fetchone(),))
        cur.execute("""select count(*) filter (where funding_paid <> 0 or funding_received <> 0),
                              round(sum(funding_paid)::numeric,4), round(sum(funding_received)::numeric,4)
                       from paper_positions""")
        print("持仓表：有资金费的仓数 / 付出合计 / 收到合计 = %s" % (cur.fetchone(),))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
