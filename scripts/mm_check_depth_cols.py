import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import MarketSessionLocal

with system_identity():
    with MarketSessionLocal() as db:
        r = db.execute(text(
            "SELECT symbol, timestamp, best_bid, best_ask, bid_depth_5, ask_depth_5, "
            "bid_depth_10, ask_depth_10, raw_levels IS NOT NULL AS has_raw, "
            "length(raw_levels) AS raw_len "
            "FROM market_orderbook_snapshots WHERE exchange='asterdex' AND symbol='BTC' "
            "ORDER BY timestamp DESC LIMIT 2"
        )).mappings().all()
        for x in r:
            print({k: (str(v)[:60] if k == "raw_levels" else v) for k, v in x.items()})
        # raw_levels 结构抽样
        rr = db.execute(text(
            "SELECT raw_levels FROM market_orderbook_snapshots WHERE exchange='asterdex' "
            "AND symbol='BTC' AND raw_levels IS NOT NULL ORDER BY timestamp DESC LIMIT 1"
        )).scalar()
        if rr:
            d = json.loads(rr) if isinstance(rr, str) else rr
            print("raw_levels keys:", list(d.keys()) if isinstance(d, dict) else type(d))
            for k in ("bids", "asks"):
                if isinstance(d, dict) and k in d:
                    print(f"  {k}[:3] =", d[k][:3])
        # 深度列非空比例（近 200 条）
        cnt = db.execute(text(
            "SELECT count(*) AS n, count(bid_depth_5) AS n_b5, count(raw_levels) AS n_raw "
            "FROM (SELECT bid_depth_5, raw_levels FROM market_orderbook_snapshots "
            "WHERE exchange='asterdex' AND symbol='BTC' ORDER BY timestamp DESC LIMIT 200) t"
        )).mappings().first()
        print("近 200 条: n=", cnt["n"], "bid_depth_5 非空=", cnt["n_b5"], "raw 非空=", cnt["n_raw"])
