"""h531：**停摆体检**——腿速掉到 0 了吗？是市场原因还是闸门原因？

现场（2026-09-29 05:3xL）：最后一条腿是 04:32，worker 仍在 tick，
`skip_counts` 里趋势闸合计 447 次 vs `quoted_decisions` 426 次。
本脚本回答三个问题，**顺序不能颠倒**（先把"是不是真的停了"钉死，再谈原因）：
  1. 最近 N 分钟的腿速（按 5 分钟桶）——区分"完全停"与"变慢"；
  2. 同一窗口的市场状态（逐币 5m/15m 价格变动、成交额）——市场是否剧烈单边；
  3. 引擎自己报的拦截构成（skip_counts / lane_pause_counts）与持仓卡住情况。

判读规则（与判定框架一致）：
  · 若市场**剧烈单边**（|r| 大、波动高）⇒ 属"市场解释的停摆"，闸门在按设计工作；
  · 若市场**平静**而腿速为 0 ⇒ 是**闸门/状态缺陷**，属事故，需立即处理。

用法：python scripts/h531_stall_check.py [--minutes 90]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATUS = ROOT / "logs" / "mm_lane_status.json"
OUT = ROOT / "research_l1" / "out" / "h531_stall_check.json"


def read_env_dsn() -> str:
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=90)
    a = ap.parse_args()
    dsn = read_env_dsn()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT now()")
            now = cur.fetchone()[0]
            print(f"DB now() = {now:%Y-%m-%d %H:%M:%S}（{now.tzinfo}）")
            # 1. 5 分钟桶腿速
            cur.execute("""
                SELECT date_trunc('minute', ts) - (extract(minute from ts)::int %% 5)
                         * interval '1 minute' AS b,
                       count(*) AS legs,
                       count(DISTINCT symbol) AS coins
                FROM lane_ledger
                WHERE lane_id=%s AND ts > now() - make_interval(mins => %s)
                GROUP BY 1 ORDER BY 1""", (LANE, a.minutes))
            buckets = cur.fetchall()
            print(f"\n近 {a.minutes} 分钟腿速（5 分钟桶）")
            print("=" * 66)
            for b, n, nc in buckets:
                bar = "█" * min(60, n)
                print(f"  {b:%H:%M}  {n:4d} 腿 ({nc} 币)  {bar}")
            if not buckets:
                print("  （该窗口内 **一条腿都没有**）")
            # 最近一条腿距今多久
            cur.execute("SELECT max(ts) FROM lane_ledger WHERE lane_id=%s", (LANE,))
            last = cur.fetchone()[0]
            if last:
                print(f"\n最近一条腿：{last:%Y-%m-%d %H:%M:%S} ⇒ 距今 "
                      f"{(now - last).total_seconds()/60.0:.1f} 分钟")
            # 2. 市场状态：逐币近 5/15 分钟价格变动 + 成交额
            cur.execute("""
                WITH t AS (
                  SELECT symbol,
                         (extract(epoch from ts))::float8 AS e,
                         price::float8 AS p,
                         qty::float8 AS q
                  FROM asterdex_trades
                  WHERE ts > now() - make_interval(mins => %s)
                ), agg AS (
                  SELECT symbol, count(*) n, sum(p*q) AS vol,
                         min(e) e0, max(e) e1
                  FROM t GROUP BY symbol
                ), r AS (
                  SELECT t.symbol,
                         max(t.p) FILTER (WHERE t.e = a.e1) AS p_last,
                         max(t.p) FILTER (WHERE t.e = a.e0) AS p_first
                  FROM t JOIN agg a ON a.symbol = t.symbol GROUP BY t.symbol
                )
                SELECT a.symbol, a.n, a.vol, r.p_first, r.p_last,
                       CASE WHEN r.p_first > 0
                            THEN (r.p_last - r.p_first)/r.p_first*1e4 END AS move_bp
                FROM agg a JOIN r ON r.symbol = a.symbol
                ORDER BY a.vol DESC NULLS LAST""", (a.minutes,))
            print(f"\n市场状态（近 {a.minutes} 分钟逐笔）")
            print("=" * 66)
            print(f"{'币':>6s} {'笔数':>8s} {'成交额$':>13s} {'首价':>12s} {'末价':>12s} "
                  f"{'净变动bp':>10s}")
            mkt = []
            for s, n, vol, p0, p1, mv in cur.fetchall():
                print(f"{s:>6s} {n:8d} {(vol or 0):13.0f} {p0 if p0 else 0:12.6f} "
                      f"{p1 if p1 else 0:12.6f} {mv if mv is None else round(mv,1):10}")
                if mv is not None:
                    mkt.append(abs(mv))
            med_move = st.median(mkt) if mkt else 0.0
            print(f"\n逐币 |净变动| 中位 = {med_move:.1f}bp"
                  f"（>50bp 视为单边剧烈；<15bp 视为平静）")
    # 3. 引擎自报
    stt = json.loads(STATUS.read_text(encoding="utf-8"))
    sc = stt.get("skip_counts") or {}
    tot = sum(v for v in sc.values() if isinstance(v, (int, float)))
    print(f"\n引擎自报（ticks={stt.get('ticks')} fills={stt.get('fills')} "
          f"flattens={stt.get('flattens')} fills_per_hour={stt.get('fills_per_hour')}）")
    print("=" * 66)
    for k, v in sorted(sc.items(), key=lambda kv: -(kv[1] if isinstance(kv[1], (int, float)) else 0))[:12]:
        pct = 100.0 * v / tot if tot else 0.0
        print(f"  {k:34s} {v:8} ({pct:5.1f}%)")
    print(f"  {'【拦截合计】':34s} {tot:8}")
    for k in ("lane_pause_counts", "lane_pause_last", "reason", "day_pnl_usd",
              "quote_modes", "side_counts"):
        if k in stt:
            print(f"  {k} = {json.dumps(stt[k], ensure_ascii=False)[:200]}")
    verdict = ("**市场解释**（剧烈单边，闸门按设计工作）"
               if med_move >= 50 else
               "**非市场原因**（价格平静而腿速为 0 ⇒ 闸门/状态缺陷，属事故）"
               if (not buckets or sum(n for _b, n, _c in buckets) <= 2) else
               "腿速偏低但未完全停摆 ⇒ 需结合拦截构成判断")
    print(f"\n⇒ 裁决：{verdict}")
    OUT.write_text(json.dumps({
        "now": str(now), "minutes": a.minutes,
        "buckets": [{"bucket": str(b), "legs": n, "coins": nc} for b, n, nc in buckets],
        "last_leg": str(last) if last else None,
        "median_abs_move_bp": med_move,
        "skip_counts": sc, "verdict": verdict,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
