"""坐实 ARB 那笔 -$0.1384（-79.5bp）强平：到底是"零头被固定成本压垮"还是"真实的持仓期间中价下跌"。

背景（2026-09-20 19:20）：MM 近 24h 总净额 -$0.2085，其中
    98 笔被动开仓腿   -$0.0708   （≈ -$0.0007/笔，基本打平）
     3 笔强平腿       -$0.1377   （ARB 一笔就 -$0.1384）
ARB 那笔名义只有 $17.40，bp 却是 -79.5 —— 远大于 4bp 手续费 + 半个点差。
两种可能，含义完全不同：
  (A) "零头残仓 + 固定成本" ⇒ 修引擎（设最小强平门槛）
  (B) "持仓期间中价真的跌了" ⇒ 引擎没错，是持仓风险兑现，只能靠止损/缩短持有
本脚本用**真实盘口快照**还原那 8735 秒里 ARB 中价的实际走势，直接判定。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_hft_arb_flatten.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

DSN = os.getenv("DATABASE_URL") or ""
for _p in ("+psycopg2", "+psycopg", "+asyncpg"):
    DSN = DSN.replace(_p, "")
if not DSN:
    raise SystemExit("DATABASE_URL 缺失")


def main() -> int:
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(DSN)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ① ARB 的全部账本行
    cur.execute(
        """
        SELECT ts, symbol, notional, spread_bp, price_bp, fee_bp, net_bp,
               position_id, meta_json
          FROM lane_ledger
         WHERE lane_id = 'mm_asterdex' AND symbol = 'ARB'
           AND ts > now() - interval '24 hours'
         ORDER BY ts
        """,
    )
    rows = cur.fetchall()
    print(f"[1] ARB 账本（近 24h）  {len(rows)} 行")
    if not rows:
        print("    无数据")
        return 0
    print("    %-22s %10s %9s %9s %8s %9s  %s"
          % ("ts", "notional", "spread", "price", "fee", "net_bp", "position_id"))
    for r in rows:
        print("    %-22s %10.4f %+9.3f %+9.3f %+8.2f %+9.3f  %s"
              % (str(r["ts"])[:22], float(r["notional"] or 0), float(r["spread_bp"] or 0),
                 float(r["price_bp"] or 0), float(r["fee_bp"] or 0), float(r["net_bp"] or 0),
                 r["position_id"]))

    t0 = min(r["ts"] for r in rows)
    t1 = max(r["ts"] for r in rows)
    print(f"\n    时间跨度: {t0} .. {t1}  ({(t1 - t0).total_seconds():.0f}s)")

    # ② 该窗口内 ARB 的真实盘口中价走势（判断是否真有大幅下跌）
    #
    # ⚠️ 市场数据表（`market_orderbook_snapshots` 等）在 **alpha_market** 库，
    #    不在业务库 alpha_arena ⇒ 必须换 session（`MarketSessionLocal`）。
    #    直接用上面那个业务库连接会 UndefinedTable。
    print("\n[2] 同时段 ARB 真实盘口中价（market_orderbook_snapshots @ alpha_market）")
    from backend.core.tenant import system_identity  # noqa: E402
    from backend.database.connection import MarketSessionLocal  # noqa: E402
    from sqlalchemy import text as _t  # noqa: E402

    with system_identity():
        with MarketSessionLocal() as mdb:
            obs = mdb.execute(_t(
                "SELECT timestamp, best_bid, best_ask,"
                "       (best_bid + best_ask) / 2.0 AS mid"
                "  FROM market_orderbook_snapshots"
                " WHERE exchange = 'asterdex' AND symbol = 'ARB'"
                "   AND best_bid > 0 AND best_ask > best_bid"
                "   AND timestamp BETWEEN :a AND :b"
                " ORDER BY timestamp"
            ), {"a": int(t0.timestamp() * 1000), "b": int(t1.timestamp() * 1000)}).mappings().all()
    print(f"    快照 {len(obs)} 条")
    if obs:
        mids = [float(o["mid"]) for o in obs]
        lo, hi, first, last = min(mids), max(mids), mids[0], mids[-1]
        print("    首 %.6f  末 %.6f  (%.2f bp)" % (first, last, (last - first) / first * 1e4))
        print("    最低 %.6f  最高 %.6f  全幅 %.1f bp" % (lo, hi, (hi - lo) / lo * 1e4))
        # 若期间中价确实跌了 ~80bp，则 ARB 的亏损是真实持仓风险，不是引擎零头问题
        fall = (lo - first) / first * 1e4
        print("    从首价到最低点: %+.1f bp" % fall)
        print("\n[3] 判定")
        if abs(fall) >= 40:
            print("    ⇒ 期间中价确实从首价下跌 %.1f bp。" % abs(fall))
            print("      **-79.5bp 不是「零头被固定成本压垮」，而是持仓期间的真实价格下跌**。")
            print("      ⇒ 引擎记账无误；这是持仓风险兑现。修法只能是止损/缩短持有/减小腿量，")
            print("        不是「设最小强平门槛」。")
        else:
            print("    ⇒ 期间中价仅波动 %.1f bp，不足以解释 -79.5bp。" % abs(fall))
            print("      ⇒ 亏损应来自固定成本（手续费+穿越点差）压在小名义上，")
            print("        属于引擎口径问题，需设最小强平名义门槛。")

    # ③ 该窗口的成交桶（看是否有大额单边成交推动）—— 同样在 alpha_market
    with system_identity():
        with MarketSessionLocal() as mdb:
            tr = mdb.execute(_t(
                "SELECT timestamp, low_price, high_price, taker_sell_volume, taker_buy_volume"
                "  FROM market_trades_aggregated"
                " WHERE exchange = 'asterdex' AND symbol = 'ARB'"
                "   AND timestamp BETWEEN :a AND :b"
                " ORDER BY timestamp"
            ), {"a": int(t0.timestamp() * 1000), "b": int(t1.timestamp() * 1000)}).mappings().all()
    print(f"\n[4] 同时段 ARB 成交桶 {len(tr)} 条")
    if tr:
        sv = sum(float(r["taker_sell_volume"] or 0) for r in tr)
        bv = sum(float(r["taker_buy_volume"] or 0) for r in tr)
        print("    taker_sell 累计 %.2f   taker_buy 累计 %.2f   净 %+.2f"
              % (sv, bv, bv - sv))

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
