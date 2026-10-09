"""H14 · asterdex 可交易标的排行 + 做市可行性打分（为扩深度采集选币）

背景（用户要求）：对市值 top 前 30 的交易币种扫描，扩深度采集凑够「前 5 固定」。
    实测 `market_asset_metrics.day_notional_volume` **全为 NULL**，
    故改用实算口径从 `market_trades_aggregated`（30 天，已含买卖名义额）算 24h 成交额。

评分原则（依 L1 重构设计 §3.11）：
    做市往返净 ≈ **整个点差**，方向预测不产生净利润 ⇒ 选币量必须是**机械量**。
    score = spread_bp（毛收益） × 成交吞吐（λ） − 队列拥挤惩罚

数据源与实测粒度：
    · `market_trades_aggregated` 15s 桶，asterdex 19 币，30 天 → 24h 名义额
    · `asterdex_book_ticker` 36ms，32 币，3.9 天 → 点差中位 + 更新频率
    · `asterdex_depth_snapshots` 20 档 105ms，**10 币** → 是否已有深度
    · `asterdex_trades` 逐笔 1.5s，32 币 → 成交笔数

输出：research_l1/out/h14_symbol_ranking.json
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

import numpy as np  # noqa: E402
import psycopg2  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "out"
OUT.mkdir(parents=True, exist_ok=True)

# 已有 20 档深度采集的币（来自 aster_ws_ingest.py --depth-symbols）
HAS_DEPTH = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ASTERUSDT",
             "HYPEUSDT", "ZECUSDT", "ONDOUSDT", "ARBUSDT", "SEIUSDT"}


def base_sym(s: str) -> str:
    """归一化为**裸标的**（去 USDT 后缀）。

    ⚠️ 本仓库命名有三套并存（已踩 4 次）：
        market_trades_aggregated.symbol  = 裸标的（`BTC`）
        asterdex_book_ticker.symbol      = 带后缀（`BTCUSDT`）
        asterdex_trades.symbol           = 带后缀（`ETHUSDT`）
    不归一会让 join 全部落空，得到"候选为空"的假结论。
    """
    u = str(s or "").strip().upper()
    for suf in ("USDT", "USDC", "USD"):
        if u.endswith(suf) and len(u) > len(suf):
            return u[: -len(suf)]
    return u


def pg(db="alpha_market"):
    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + f"/{db}")
    cn.autocommit = True
    return cn


def main():
    c = pg()
    cur = c.cursor()

    # ---- ① 24h 名义成交额（asterdex，实算）----
    cur.execute("select max(timestamp) from market_trades_aggregated where exchange='asterdex'")
    tmax = int(cur.fetchone()[0])
    t0 = tmax - 24 * 3600 * 1000
    cur.execute(
        """
        select symbol,
               sum(coalesce(taker_buy_notional,0) + coalesce(taker_sell_notional,0)) as notional,
               sum(coalesce(taker_buy_count,0) + coalesce(taker_sell_count,0)) as n_trades,
               count(*) as buckets
        from market_trades_aggregated
        where exchange='asterdex' and timestamp >= %s and timestamp <= %s
        group by symbol order by notional desc
        """,
        (t0, tmax),
    )
    vol_rows = cur.fetchall()
    print(f"24h 窗口: {datetime.fromtimestamp(t0/1000, timezone.utc):%m-%d %H:%M} "
          f"-> {datetime.fromtimestamp(tmax/1000, timezone.utc):%m-%d %H:%M} UTC")
    print(f"asterdex 24h 有成交的币: {len(vol_rows)}")

    # ---- ② 点差 + book 更新频率（asterdex_book_ticker，最近 3.9 天）----
    cur.execute(
        """
        select symbol, count(*) n,
               percentile_disc(0.5) within group (
                   order by (ask_px - bid_px) / ((ask_px + bid_px)/2) * 1e4) as spread_med_bp,
               percentile_disc(0.25) within group (
                   order by (ask_px - bid_px) / ((ask_px + bid_px)/2) * 1e4) as spread_p25_bp,
               max(event_ts_ms) as last_ts
        from asterdex_book_ticker
        group by symbol
        """
    )
    book = {base_sym(r[0]): {"book_n": int(r[1]),
                             "spread_med_bp": float(r[2]) if r[2] is not None else None,
                             "spread_p25_bp": float(r[3]) if r[3] is not None else None,
                             "book_last_ts": int(r[4])} for r in cur.fetchall()}

    # ---- ③ 逐笔成交笔数（asterdex_trades）----
    cur.execute(
        "select symbol, count(*) n, max(event_ts_ms) from asterdex_trades group by symbol"
    )
    trd = {base_sym(r[0]): {"trade_n": int(r[1]), "trade_last_ts": int(r[2])}
           for r in cur.fetchall()}

    # ---- ④ 深度覆盖 ----
    cur.execute("select distinct symbol from asterdex_depth_snapshots")
    depth_syms = {base_sym(r[0]) for r in cur.fetchall()}
    c.close()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    rows = []
    for raw_sym, notional, n_tr, buckets in vol_rows:
        sym = base_sym(raw_sym)
        b = book.get(sym, {})
        t = trd.get(sym, {})
        spread = b.get("spread_med_bp")
        has_depth = sym in depth_syms or f"{sym}USDT" in HAS_DEPTH
        book_age_s = ((now_ms - b["book_last_ts"]) / 1000.0) if b.get("book_last_ts") else None
        trade_age_s = ((now_ms - t["trade_last_ts"]) / 1000.0) if t.get("trade_last_ts") else None
        # 机械分：点差是毛收益，成交额是吞吐代理；无点差不评分
        score = None
        if spread is not None and notional:
            score = spread * float(np.log10(max(notional, 1.0)))
        rows.append({
            "symbol": sym,
            "symbol_venue": f"{sym}USDT",
            "notional_24h_usd": float(notional or 0),
            "n_trades_24h": int(n_tr or 0),
            "buckets_24h": int(buckets or 0),
            "spread_med_bp": spread,
            "spread_p25_bp": b.get("spread_p25_bp"),
            "book_updates": b.get("book_n"),
            "book_age_s": book_age_s,
            "trade_n_total": t.get("trade_n"),
            "trade_age_s": trade_age_s,
            "has_depth": has_depth,
            "score": score,
            "book_stale": (book_age_s is None or book_age_s > 3600),
            "trade_stale": (trade_age_s is None or trade_age_s > 3600),
        })

    rows.sort(key=lambda r: -(r["notional_24h_usd"] or 0))
    for i, r in enumerate(rows, 1):
        r["rank_volume"] = i

    print(f"\n{'#':>3} {'symbol':<14}{'24h $':>16}{'笔数':>10}{'点差中位bp':>12}"
          f"{'深度':>6}{'book_age':>10}{'trade_age':>10}")
    for r in rows[:40]:
        ba = f"{r['book_age_s']:.0f}s" if r["book_age_s"] is not None else "-"
        ta = f"{r['trade_age_s']:.0f}s" if r["trade_age_s"] is not None else "-"
        sp = f"{r['spread_med_bp']:.3f}" if r["spread_med_bp"] is not None else "-"
        print(f"{r['rank_volume']:>3} {r['symbol']:<14}{r['notional_24h_usd']:>16,.0f}"
              f"{r['n_trades_24h']:>10,}{sp:>12}"
              f"{('✅' if r['has_depth'] else '—'):>6}{ba:>10}{ta:>10}")

    # ---- 候选推荐：有成交、点差达标、数据新鲜 ----
    print(f"\n=== 做市候选（点差 ≥ 0.8bp 且数据新鲜，按 score 排序）===")
    cand = [r for r in rows
            if r["spread_med_bp"] is not None and r["spread_med_bp"] >= 0.8
            and not r["book_stale"] and not r["trade_stale"]
            and r["notional_24h_usd"] >= 1_000_000]
    cand.sort(key=lambda r: -(r["score"] or 0))
    print(f"{'symbol':<14}{'点差bp':>9}{'24h $':>16}{'score':>9}{'已有深度':>10}")
    for r in cand[:20]:
        print(f"{r['symbol']:<14}{r['spread_med_bp']:>9.3f}{r['notional_24h_usd']:>16,.0f}"
              f"{r['score']:>9.3f}{('✅' if r['has_depth'] else '❌需扩'):>10}")

    p = OUT / "h14_symbol_ranking.json"
    p.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"t0": t0, "t1": tmax},
        "has_depth_now": sorted(HAS_DEPTH),
        "rows": rows,
        "candidates": cand,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
