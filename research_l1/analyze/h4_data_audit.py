"""H4 · 数据审计 —— 为「逆向选择 / 反转」研究确定可用数据源与粒度。

只读脚本。输出 JSON 到 research_l1/out/h4_data_audit.json，同时打印摘要。

审计目标：
  1. market_orderbook_snapshots：粒度、字段、单位（symbol 是裸币名还是 XXXUSDT）
  2. asterdex_book_ticker / asterdex_depth_snapshots：是否有 top-of-book 双边量（imb 必需）
  3. asterdex_trades / market_trades_aggregated：是否有逐笔成交（测 OFI / 主动买卖方向）
  4. 各表的时间覆盖与新鲜度（决定能否做样本外）
  5. 结论：能否在自有数据上复现 "市价单吃挂单 = 逆向选择" 与 "reverse" 检验
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=False)

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "out"
OUT.mkdir(parents=True, exist_ok=True)


def conn(dbname=None):
    url = os.environ["DATABASE_URL"]
    # SQLAlchemy 风格 URL -> 纯 libpq DSN
    for drv in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(drv, "")
    if dbname:
        head, _, _ = url.rpartition("/")
        url = f"{head}/{dbname}"
    return psycopg2.connect(url)


def q(cur, sql, args=None):
    cur.execute(sql, args or ())
    return cur.fetchall()


def table_exists(cur, name):
    cur.execute("select 1 from information_schema.tables where table_name=%s", (name,))
    return cur.fetchone() is not None


def columns(cur, name):
    cur.execute(
        "select column_name, data_type from information_schema.columns "
        "where table_name=%s order by ordinal_position",
        (name,),
    )
    return [{"name": r[0], "type": r[1]} for r in cur.fetchall()]


TABLES = [
    "market_orderbook_snapshots",
    "asterdex_book_ticker",
    "asterdex_depth_snapshots",
    "asterdex_trades",
    "market_trades_aggregated",
    "crypto_klines",
]


def main():
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "databases": {}}
    for dbname in ("alpha_market", "alpha_analytics"):
        try:
            c = conn(dbname)
        except Exception as e:  # noqa: BLE001
            report["databases"][dbname] = {"error": str(e)}
            continue
        report["databases"][dbname] = audit_db(c, dbname)
        c.close()

    p = OUT / "h4_data_audit.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for dbname, rep in report["databases"].items():
        print(f"\n{'='*70}\nDB {dbname}")
        if "error" in rep:
            print("  ERROR:", rep["error"])
            continue
        for t, info in rep["tables"].items():
            if not info["exists"]:
                print(f"[--] {t}: NOT EXISTS")
                continue
            print(f"[OK] {t}  rows~{info.get('rows_estimate')} "
                  f"time={info.get('time_column')} symbol={info.get('symbol_column')}")
            for s in (info.get("by_symbol") or [])[:8]:
                print(f"     {s['symbol']:<14} n={s['rows']:<12} {s['min_ts'][:19]} -> {s['max_ts'][:19]}")
            for g in (info.get("sample_gap_sec_2h") or [])[:6]:
                print(f"     gap {g['symbol']:<14} p50={g['p50_gap_s']}s p90={g['p90_gap_s']}s n={g['n']}")
            if info.get("sample_row"):
                print("     sample:", json.dumps(info["sample_row"], ensure_ascii=False)[:400])
    print(f"\nwrote {p}")


def audit_db(c, dbname):
    rep = {"tables": {}}
    cur = c.cursor()
    for t in TABLES:
        info = {"exists": False}
        if not table_exists(cur, t):
            rep["tables"][t] = info
            continue
        info["exists"] = True
        info["columns"] = columns(cur, t)

        cur.execute("select reltuples::bigint from pg_class where relname=%s", (t,))
        row = cur.fetchone()
        info["rows_estimate"] = int(row[0]) if row and row[0] is not None and row[0] >= 0 else None
        try:
            cur.execute(f"select count(*) from {t}")
            info["rows_exact"] = int(cur.fetchone()[0])
        except Exception as e:  # noqa: BLE001
            c.rollback()
            info["rows_exact"] = None
            info["rows_exact_error"] = str(e)

        tcol = None
        for cand in ("ts", "timestamp", "time", "created_at", "event_ts", "open_time"):
            if any(col["name"] == cand for col in info["columns"]):
                tcol = cand
                break
        info["time_column"] = tcol

        scol = None
        for cand in ("symbol", "pair", "instrument", "coin"):
            if any(col["name"] == cand for col in info["columns"]):
                scol = cand
                break
        info["symbol_column"] = scol

        if tcol and scol:
            try:
                cur.execute(
                    f"select {scol}, count(*), min({tcol}), max({tcol}) from {t} "
                    f"group by {scol} order by count(*) desc limit 40"
                )
                info["by_symbol"] = [
                    {"symbol": r[0], "rows": int(r[1]),
                     "min_ts": str(r[2]), "max_ts": str(r[3])}
                    for r in cur.fetchall()
                ]
            except Exception as e:  # noqa: BLE001
                c.rollback()
                info["by_symbol_error"] = str(e)

        if tcol and scol:
            try:
                cur.execute(
                    f"""
                    with s as (
                      select {scol} as sym, {tcol} as ts,
                             lag({tcol}) over (partition by {scol} order by {tcol}) as prev
                      from {t} where {tcol} > now() - interval '2 hours'
                    )
                    select sym, count(*) as n,
                           percentile_disc(0.50) within group (order by extract(epoch from (ts-prev))) as p50_s,
                           percentile_disc(0.90) within group (order by extract(epoch from (ts-prev))) as p90_s
                    from s where prev is not null
                    group by sym order by n desc limit 15
                    """
                )
                info["sample_gap_sec_2h"] = [
                    {"symbol": r[0], "n": int(r[1]),
                     "p50_gap_s": float(r[2]) if r[2] is not None else None,
                     "p90_gap_s": float(r[3]) if r[3] is not None else None}
                    for r in cur.fetchall()
                ]
            except Exception as e:  # noqa: BLE001
                c.rollback()
                info["sample_gap_error"] = str(e)

        rep["tables"][t] = info

    for t in ("market_orderbook_snapshots", "asterdex_book_ticker", "asterdex_trades"):
        if rep["tables"].get(t, {}).get("exists"):
            try:
                cur.execute(f"select * from {t} order by 1 desc limit 1")
                cols = [d[0] for d in cur.description]
                row = cur.fetchone()
                rep["tables"][t]["sample_row"] = dict(zip(cols, [str(v) for v in row]))
            except Exception as e:  # noqa: BLE001
                c.rollback()
                rep["tables"][t]["sample_row_error"] = str(e)
    return rep


if __name__ == "__main__":
    main()
