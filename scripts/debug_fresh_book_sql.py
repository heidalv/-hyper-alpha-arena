"""调试：F281 的 `_refresh_fresh_book` 到底抛了什么异常（我的 except 把它吞了）。"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)
LANE = os.getenv("MM_LANE_ID", "mm_asterdex")


def main():
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import MarketSessionLocal
    from backend.services.market_maker.runner import get_runner

    r = get_runner(LANE)
    if r is None:
        print("no runner")
        return 1
    syms = list(dict.fromkeys(list(r.symbols) + list(r.orphan_states())))
    print(f"symbols={r.symbols}")
    print(f"orphans={r.orphan_states()}")
    print(f"merged={syms}")
    ss = [f"{s}USDT" if not s.endswith("USDT") else s for s in syms]
    print(f"query param ss={ss}")

    with system_identity():
        with MarketSessionLocal() as db:
            print("\n--- 尝试 1：ANY(:ss) 传 list ---")
            try:
                rows = db.execute(text(
                    "SELECT DISTINCT ON (symbol) symbol, bid_px::float b, ask_px::float a"
                    "  FROM asterdex_book_ticker"
                    " WHERE symbol = ANY(:ss) AND bid_px > 0 AND ask_px > bid_px"
                    " ORDER BY symbol, event_ts_ms DESC"
                ), {"ss": ss}).mappings().all()
                print(f"OK -> {len(rows)} 行")
                for x in rows[:4]:
                    print("   ", dict(x))
            except Exception:
                print("FAILED:")
                traceback.print_exc()

            print("\n--- 尝试 2：IN 展开 ---")
            try:
                from sqlalchemy import bindparam
                st = text(
                    "SELECT DISTINCT ON (symbol) symbol, bid_px::float b, ask_px::float a"
                    "  FROM asterdex_book_ticker"
                    " WHERE symbol IN :ss AND bid_px > 0 AND ask_px > bid_px"
                    " ORDER BY symbol, event_ts_ms DESC"
                ).bindparams(bindparam("ss", expanding=True))
                rows = db.execute(st, {"ss": ss}).mappings().all()
                print(f"OK -> {len(rows)} 行")
                for x in rows[:4]:
                    print("   ", dict(x))
            except Exception:
                print("FAILED:")
                traceback.print_exc()

            print("\n--- 尝试 3：逐符号查询（最保守）---")
            try:
                n = 0
                for s in ss[:3]:
                    row = db.execute(text(
                        "SELECT symbol, bid_px::float b, ask_px::float a"
                        "  FROM asterdex_book_ticker WHERE symbol=:s"
                        "   AND bid_px>0 AND ask_px>bid_px"
                        " ORDER BY event_ts_ms DESC LIMIT 1"), {"s": s}).mappings().first()
                    print(f"   {s}: {dict(row) if row else None}")
                    n += 1 if row else 0
                print(f"OK -> {n}/3")
            except Exception:
                print("FAILED:")
                traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
