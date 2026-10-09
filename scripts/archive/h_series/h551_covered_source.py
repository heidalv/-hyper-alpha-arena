"""h551：**"可交易时长"该用哪张表**——`asterdex_trades` 还是 `market_trades_aggregated`？

背景：R34 给判定框架加了"可交易时长"口径（外部停摆的小时不该算进频率分母），
实现时先用了 `asterdex_trades`（与 `_market_activity` 同源）。但实测**基线窗覆盖率
报 100%**，而该窗（09-28 21:48→09-29 09:48）**明明含 04:32–09:09 的停机** ⇒ 说明
`asterdex_trades` 在停机期间仍有写入（其它写入方/回填），而**引擎真正读的是
`market_trades_aggregated`** ⇒ 用前者会把"引擎其实拿不到数据"的时间算成可交易 ✗。

本脚本按分钟统计两张表在**停机区间**内有数据（且有该币数据）的分钟数，直接对比。

用法：python scripts/h551_covered_source.py
"""
from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE_SYMS = ["BNB", "NEAR", "ARB", "XRP", "ENA"]


def main() -> int:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    dsn = env.get("MARKET_DATABASE_URL") or env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        dsn = dsn.replace(j, "")
    pairs = [s + "USDT" for s in LANE_SYMS]
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT now()")
            print(f"now = {cur.fetchone()[0]:%Y-%m-%d %H:%M:%S}")
            # 停机区间（本地时间 04:32–09:09 = UTC 20:32–01:09 前一天）
            cur.execute("""
                SELECT
                  (SELECT count(DISTINCT date_trunc('minute',
                            to_timestamp(event_ts_ms/1000.0)))
                     FROM asterdex_trades
                    WHERE symbol = ANY(%s)
                      AND event_ts_ms > 1790600000000
                      AND event_ts_ms <= 1790625000000) AS mins_raw,
                  (SELECT count(DISTINCT date_trunc('minute', to_timestamp(timestamp/1000.0)))
                     FROM market_trades_aggregated
                    WHERE symbol = ANY(%s)
                      AND timestamp > 1790600000000
                      AND timestamp <= 1790625000000) AS mins_agg
            """, (pairs, [s for s in LANE_SYMS]))
            r = cur.fetchone()
            print(f"\n停机区间内（约 04:33–09:10）有数据的分钟数（车道 5 币）：")
            print(f"  asterdex_trades          : {r[0]} 分钟")
            print(f"  market_trades_aggregated : {r[1]} 分钟   ← **引擎实际读的表**")
            # 全窗对照（近 12h）
            cur.execute("""
                SELECT
                  (SELECT count(DISTINCT date_trunc('minute',
                            to_timestamp(event_ts_ms/1000.0)))
                     FROM asterdex_trades WHERE symbol = ANY(%s)
                      AND event_ts_ms > (extract(epoch from now())-43200)*1000) AS a,
                  (SELECT count(DISTINCT date_trunc('minute', to_timestamp(timestamp/1000.0)))
                     FROM market_trades_aggregated WHERE symbol = ANY(%s)
                      AND timestamp > (extract(epoch from now())-43200)*1000) AS b
            """, (pairs, [s for s in LANE_SYMS]))
            r2 = cur.fetchone()
            print(f"\n近 12h（最多 720 分钟）：")
            print(f"  asterdex_trades          : {r2[0]} 分钟（{r2[0]/60:.1f}h）")
            print(f"  market_trades_aggregated : {r2[1]} 分钟（{r2[1]/60:.1f}h）")
            print("\n⇒ 判读：若两值在停机区间差异明显，则**可交易时长必须用聚合表**"
                  "（引擎读它；用逐笔表会把引擎其实拿不到数据的时间算成可交易）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
