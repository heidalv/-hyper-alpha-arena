# -*- coding: utf-8 -*-
"""h636 — 逐币行情可用性 + 引擎每币运行态（只读）。

为什么需要：23:13 恢复 5 币宇宙并撤掉三处闸门后，心跳里
`skip_counts` 空 ✓、`gate_probe_counts` 空 ✓（没有闸门在拦），
但 `sigma_decisions.all = 0`、`side_counts = {both:0,one:0,none:0}`
⇒ **一条决策都没产生** ✗（此前 2 币时是 128 条决策 / 64 拍 = 每拍每币一条）。

唯一解释方向：**每币循环根本没进**（拿不到盘口就 continue）。
本脚本把两件事并排摆出来：
  1) 心跳 `states` 的键集合 + 每币挂单价/时刻（引擎自己认到的币）
  2) 这 5 个币在 book / depth / trades 三表的**近期行数与行龄**（两种符号写法）

安全约定（都是踩过的坑）：
  · R235：时长一律用**字面量** interval，绝不写 `interval '%s'`（静默 0 行 ✗）
  · `asterdex_book_ticker` 有 3.5 亿行 ⇒ 全表 `count(DISTINCT symbol)` 会挂住
    数分钟（曾阻塞后续所有检查 ✗）⇒ 一律**带时间窗 + LIMIT**，并设
    `statement_timeout=15s` 兜底 ✓
  · 行龄在 SQL 侧算（`extract(epoch from now())`），不在 Python 里硬编码 epoch ✗
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

# asterdex_* 行情表在 **MARKET_DATABASE_URL** 那个库（h533 开头就写着这条），
# 而 h425 的 read_env_dsn() 指向 lane 库（lane_ledger 在那里）⇒ 用错库会
# 打印「关系 asterdex_book_ticker 不存在」✗，看着像「没有行情」✗✗。
# 本脚本初版就踩了：7 个币 × 3 张表全报"表不存在"。
_spec533 = importlib.util.spec_from_file_location(
    "h533", ROOT / "scripts" / "h533_market_freshness.py")
m533 = importlib.util.module_from_spec(_spec533)
_spec533.loader.exec_module(m533)  # type: ignore[union-attr]

STATUS = ROOT / "logs" / "mm_lane_status.json"
TABLES = ("asterdex_book_ticker", "asterdex_depth_snapshots", "asterdex_trades")


def _market_dsn() -> str:
    env = m533.read_env()
    dsn = (m533.dsn_of(env, "MARKET_DATABASE_URL", "DATABASE_URL_MARKET",
                       "DB_MARKET_URL")
           or m533.dsn_of(env, "DATABASE_URL"))
    if not dsn:
        raise SystemExit("✗ .env 里找不到 MARKET_DATABASE_URL")
    print(f"[market] {dsn.split('@')[-1]}")
    return dsn


def main() -> int:
    import psycopg

    st = json.loads(STATUS.read_text(encoding="utf-8", errors="replace"))
    syms = list(st.get("symbols") or [])
    states = st.get("states") or {}
    print("=" * 92)
    print("h636 — 逐币行情可用性 + 引擎每币运行态")
    print("=" * 92)
    print(f"  心跳 symbols  = {syms}")
    print(f"  心跳 states 键 = {list(states.keys())}")
    miss = [s for s in syms if s not in states]
    if miss:
        print(f"  ✗ 心跳里没有运行态的币 = {miss}")
    for s in syms:
        d = states.get(s) or {}
        print(f"    {s:<6} qty={d.get('qty')!r:<8} bid={d.get('quote_bid')!r:<8} "
              f"ask={d.get('quote_ask')!r:<8} quote_ts={d.get('quote_ts')!r}")

    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute("SET statement_timeout = 15000")
        print("\n[1] 行情逐表：近 15 分钟行数 + 行龄（秒）")
        for tbl in TABLES:
            print(f"\n  {tbl}")
            for s in syms + ["BNB", "ETH"]:
                hit = False
                for form in (s, f"{s}USDT"):
                    try:
                        cur.execute(
                            f"SELECT count(*) AS n, "
                            f"  (extract(epoch from now())*1000 - max(event_ts_ms))/1000.0 AS age_s "
                            f"FROM {tbl} WHERE symbol = %s "
                            f"  AND event_ts_ms > (extract(epoch from now())*1000 - 900000)",
                            (form,))
                        n, age = cur.fetchone()
                    except Exception as e:
                        print(f"    {form:<10} ✗ {type(e).__name__}: {str(e)[:70]}")
                        hit = True
                        break
                    if n:
                        flag = "✓" if (age is not None and age < 60) else "✗ 陈旧"
                        print(f"    {form:<10} n={n:>6}  行龄={age:>7.1f}s {flag}")
                        hit = True
                        break
                if not hit:
                    print(f"    {s:<10} ✗ 近 15 分钟**零行**")

        print("\n[2] 近 15 分钟该表出现过哪些符号（LIMIT 60，带时间窗防全表扫）")
        for tbl in TABLES:
            try:
                cur.execute(
                    f"SELECT DISTINCT symbol FROM {tbl} "
                    f"WHERE event_ts_ms > (extract(epoch from now())*1000 - 900000) "
                    f"LIMIT 60")
                got = sorted(str(r[0]) for r in cur.fetchall())
                print(f"  {tbl:<28} {len(got)} 个: {got}")
            except Exception as e:
                print(f"  {tbl:<28} ✗ {type(e).__name__}: {str(e)[:70]}")

        print("\n[3] **引擎真正读的那张表**：runner.py:3527-3531 的 SQL（谓词逐字相同，"
              "只把 SQLAlchemy 的 `:e/:s` 绑参换成 psycopg 的 `%s`）")
        print("    谓词：exchange='asterdex' AND symbol=:s AND best_bid>0"
              " AND best_ask>best_bid ⇒ 查不到就 continue ⇒ 该币一条决策都不产生")
        for s in syms + ["BNB", "ETH"]:
            cur.execute(
                "SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots"
                " WHERE exchange=%s AND symbol=%s AND best_bid>0 AND best_ask>best_bid"
                " ORDER BY timestamp DESC LIMIT 1",
                ("asterdex", s))
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "SELECT count(*) FROM market_orderbook_snapshots"
                    " WHERE exchange=%s AND symbol=%s", ("asterdex", s))
                n_all = cur.fetchone()[0]
                print(f"    {s:<8} ✗ **引擎查询无行**（不加价差条件时 {n_all} 行）")
                continue
            ts, bb, ba = row
            cur.execute("SELECT (extract(epoch from now())*1000 - %s)/1000.0", (int(ts),))
            age = cur.fetchone()[0]
            flag = "✓" if age < 60 else "✗ 陈旧"
            print(f"    {s:<8} ✓ ts={ts} bid={bb} ask={ba} 行龄={age:.1f}s {flag}")

        print("\n[4] 该表里 asterdex 的符号写法（LIMIT 60）")
        try:
            cur.execute(
                "SELECT DISTINCT symbol FROM market_orderbook_snapshots"
                " WHERE exchange='asterdex' LIMIT 60")
            got = sorted(str(r[0]) for r in cur.fetchall())
            print(f"  {len(got)} 个: {got}")
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {str(e)[:70]}")

        print("\n[5] **逐币行龄全景**（该表全部 asterdex 符号，按行龄升序）")
        print("    引擎只按 (exchange,symbol) 取最新一行 ⇒ 行龄大 = 报价用的旧盘口 ✗")
        try:
            cur.execute(
                "SELECT DISTINCT symbol FROM market_orderbook_snapshots"
                " WHERE exchange='asterdex' LIMIT 60")
            allsym = sorted(str(r[0]) for r in cur.fetchall())
            rows = []
            for s in allsym:
                cur.execute(
                    "SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots"
                    " WHERE exchange=%s AND symbol=%s AND best_bid>0"
                    " AND best_ask>best_bid ORDER BY timestamp DESC LIMIT 1",
                    ("asterdex", s))
                r = cur.fetchone()
                if not r:
                    rows.append((float("inf"), s, None, None, None))
                    continue
                ts, bb, ba = r
                cur.execute("SELECT (extract(epoch from now())*1000 - %s)/1000.0",
                            (int(ts),))
                rows.append((cur.fetchone()[0], s, ts, bb, ba))
            rows.sort()
            blocked = {"BNB", "XRP"}
            for age, s, ts, bb, ba in rows:
                if ts is None:
                    print(f"    {s:<10} ✗ 无有效盘口行")
                    continue
                flag = "✓ 新鲜" if age < 120 else "✗ 陈旧"
                blk = "  ← 在 entry_block_symbols（只减仓）" if s in blocked else ""
                print(f"    {s:<10} 行龄={age:>8.1f}s  bid={bb} ask={ba}  {flag}{blk}")
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {str(e)[:70]}")

    print("\n" + "=" * 92)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
