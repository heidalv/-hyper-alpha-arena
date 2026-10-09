"""H15 · asterdex 全 catalog 做市可行性扫描（为扩深度采集找候选）

目的：现有深度采集只 13 币，其中达标的更少。要"继续扩到更多币"，必须先知道
      **哪些币有可评估的数据、且做市指标达标**。

数据（实测覆盖）：
  · `asterdex_book_ticker`  p50 36ms，覆盖一批币 → 点差 + 更新频率
  · `asterdex_trades`       逐笔 → 成交笔数
  · `market_trades_aggregated` 15s 桶，asterdex → 24h 名义额（**只覆盖部分币**）

⚠️ 命名三套并存（已踩 4 次），统一用 `base()` 归一化后比较：
     `market_trades_aggregated.symbol` = 裸标的（`BTC`）
     `asterdex_book_ticker.symbol`     = 带后缀（`BTCUSDT`）

输出：research_l1/out/h15_catalog_scan.json
"""
from __future__ import annotations

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

OUT = Path(__file__).resolve().parents[1] / "out"
OUT.mkdir(parents=True, exist_ok=True)

# 当前已在采深度的币（裸标的）
CURRENT_DEPTH = {"BTC", "ETH", "SOL", "XRP", "ASTER", "HYPE", "ZEC",
                 "ARB", "ONDO", "SEI", "BNB", "DOGE", "UNI"}


def base_sym(s) -> str:
    u = str(s or "").strip().upper()
    for suf in ("USDT", "USDC", "USD"):
        if u.endswith(suf) and len(u) > len(suf):
            return u[: -len(suf)]
    return u


def pg():
    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + "/alpha_market")
    cn.autocommit = True
    return cn


def main():
    c = pg()
    cur = c.cursor()

    # ---- ① book_ticker：点差 + 更新次数（近 24h 有效样本）----
    print("扫描 asterdex_book_ticker ...", flush=True)
    cur.execute(
        """
        select symbol, count(*) n,
               percentile_disc(0.5) within group (
                   order by (ask_px - bid_px) / nullif((ask_px + bid_px)/2, 0) * 1e4) as spr_med,
               percentile_disc(0.25) within group (
                   order by (ask_px - bid_px) / nullif((ask_px + bid_px)/2, 0) * 1e4) as spr_p25,
               max(event_ts_ms) as last_ts
        from asterdex_book_ticker
        group by symbol
        """
    )
    book = {}
    for sym, n, med, p25, last in cur.fetchall():
        book[base_sym(sym)] = {
            "book_n": int(n),
            "spread_med_bp": float(med) if med is not None else None,
            "spread_p25_bp": float(p25) if p25 is not None else None,
            "book_last_ts": int(last or 0),
        }

    # ---- ② trades：笔数 ----
    print("扫描 asterdex_trades ...", flush=True)
    cur.execute(
        "select symbol, count(*) n, max(event_ts_ms) from asterdex_trades group by symbol"
    )
    trd = {}
    for sym, n, last in cur.fetchall():
        trd[base_sym(sym)] = {"trade_n": int(n), "trade_last_ts": int(last or 0)}

    # ---- ③ 24h 名义额（market_trades_aggregated，只覆盖部分币）----
    print("扫描 market_trades_aggregated ...", flush=True)
    cur.execute("select max(timestamp) from market_trades_aggregated where exchange='asterdex'")
    row = cur.fetchone()
    tmax = int(row[0]) if row and row[0] else 0
    t0 = tmax - 24 * 3600 * 1000
    vol = {}
    if tmax:
        cur.execute(
            """
            select symbol,
                   sum(coalesce(taker_buy_notional,0) + coalesce(taker_sell_notional,0)) as notional,
                   sum(coalesce(taker_buy_count,0) + coalesce(taker_sell_count,0)) as n
            from market_trades_aggregated
            where exchange='asterdex' and timestamp >= %s and timestamp <= %s
            group by symbol
            """,
            (t0, tmax),
        )
        for sym, notional, n in cur.fetchall():
            vol[base_sym(sym)] = {"notional_24h": float(notional or 0), "n_24h": int(n or 0)}
    c.close()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    all_syms = sorted(set(book) | set(trd))

    rows = []
    for sym in all_syms:
        b = book.get(sym, {})
        t = trd.get(sym, {})
        v = vol.get(sym, {})
        book_age = ((now_ms - b["book_last_ts"]) / 1000.0) if b.get("book_last_ts") else None
        trade_age = ((now_ms - t["trade_last_ts"]) / 1000.0) if t.get("trade_last_ts") else None
        spr = b.get("spread_med_bp")
        notional = v.get("notional_24h")
        # 机械分：点差（毛收益）× log10(吞吐)。吞吐优先用 24h 名义额，缺失时退化为笔数
        throughput = notional if notional else (t.get("trade_n") or 0)
        score = None
        if spr is not None and throughput:
            import math
            score = spr * math.log10(max(float(throughput), 1.0))
        rows.append({
            "symbol": sym,
            "spread_med_bp": spr,
            "spread_p25_bp": b.get("spread_p25_bp"),
            "book_updates": b.get("book_n"),
            "book_age_s": book_age,
            "trades_total": t.get("trade_n"),
            "trade_age_s": trade_age,
            "notional_24h_usd": notional,
            "n_trades_24h": v.get("n_24h"),
            "has_depth": sym in CURRENT_DEPTH,
            "score": score,
            # ⚠️ 阈值不能用 5 分钟：实测 book_ticker/深度落库延迟约 **5.6 分钟**
            #    （批量 flush），300s 阈值会把**全部**币判成不新鲜（实测 fresh=0）。
            #    30 分钟才能把"采集停了"与"批量写入延迟"区分开。
            "book_fresh": (book_age is not None and book_age < 1800),
            "trade_fresh": (trade_age is not None and trade_age < 1800),
        })

    rows.sort(key=lambda r: -(r["score"] or -1e9))
    for i, r in enumerate(rows, 1):
        r["rank_score"] = i

    fresh = [r for r in rows if r["book_fresh"] and r["trade_fresh"]]
    print(f"\n总标的 {len(rows)}，其中 book+trade 都新鲜 {len(fresh)}")
    print(f"\n{'#':>3} {'symbol':<12}{'点差bp':>9}{'24h$':>15}{'笔数':>9}{'book_n':>10}{'深度':>5}")
    for r in fresh[:45]:
        sp = f"{r['spread_med_bp']:.3f}" if r["spread_med_bp"] is not None else "-"
        nt = f"{r['notional_24h_usd']:,.0f}" if r["notional_24h_usd"] else "-"
        print(f"{r['rank_score']:>3} {r['symbol']:<12}{sp:>9}{nt:>15}"
              f"{r['trades_total']:>9,}{r['book_updates']:>10,}"
              f"{('✅' if r['has_depth'] else '—'):>5}")

    # ---- 扩采集候选：新鲜 + 点差达标 + 尚无深度 ----
    print(f"\n=== 扩深度采集候选（点差 ≥ 0.8bp 且数据新鲜，按 score 排序）===")
    cand = [r for r in fresh
            if r["spread_med_bp"] is not None and r["spread_med_bp"] >= 0.8
            and not r["has_depth"] and (r["trades_total"] or 0) >= 2000]
    cand.sort(key=lambda r: -(r["score"] or 0))
    print(f"{'symbol':<12}{'点差bp':>9}{'24h$':>15}{'笔数':>9}{'book_n':>10}{'score':>9}")
    for r in cand[:25]:
        nt = f"{r['notional_24h_usd']:,.0f}" if r["notional_24h_usd"] else "-"
        print(f"{r['symbol']:<12}{r['spread_med_bp']:>9.3f}{nt:>15}"
              f"{r['trades_total']:>9,}{r['book_updates']:>10,}{r['score']:>9.2f}")

    p = OUT / "h15_catalog_scan.json"
    p.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_24h": [t0, tmax],
        "current_depth": sorted(CURRENT_DEPTH),
        "rows": rows,
        "expansion_candidates": cand,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
