"""h585 — 查采集器**写哪些表**（只读，R101）：为"重建接口说明书"取证。

已知：`asterdex_trades`（原始逐笔）滞后 1s ✓、`market_trades_aggregated` 由
`backend.workers.market_data_center` 写 ✓。本脚本找出**深度/盘口**落在哪张表，
以及 `asterdex_stream_health` 的内容（3 行 = 3 条流 ✓）。

用法：python scripts/h585_market_tables.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import datetime as dt
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402


def main() -> int:
    mk = h.read_env_dsn().replace("/alpha_arena", "/alpha_market")
    with psycopg.connect(mk) as c, c.cursor() as cur:
        print("=" * 96)
        print("行情库里与 asterdex 相关的表（按名字筛），及各自最新写入时间")
        print("=" * 96)
        cur.execute(
            "SELECT table_schema, table_name FROM information_schema.tables"
            " WHERE table_name ILIKE '%%aster%%' OR table_name ILIKE '%%depth%%'"
            " OR table_name ILIKE '%%book%%' OR table_name ILIKE '%%stream%%'"
            " ORDER BY 1, 2")
        rows = cur.fetchall()
        for sch, t in rows:
            try:
                cur.execute(f'SELECT count(*) FROM "{sch}"."{t}"')
                n = cur.fetchone()[0]
            except Exception as exc:  # noqa: BLE001
                n = f"err({str(exc)[:30]})"
            # 时间列
            cur.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema=%s AND table_name=%s"
                " AND column_name IN ('recv_ts_ns','timestamp','ts','ingest_ts','updated_at',"
                "'event_ts_ms','last_recv_ns','created_at') ORDER BY ordinal_position",
                (sch, t))
            tcols = [r[0] for r in cur.fetchall()]
            age = ""
            if tcols and isinstance(n, int) and n:
                tc = tcols[0]
                cur.execute(f'SELECT max("{tc}") FROM "{sch}"."{t}"')
                mx = cur.fetchone()[0]
                if isinstance(mx, (int, float)) and mx > 1e12:
                    _ms = mx / 1e6 if mx > 1e15 else mx
                    age = f"  最新{tc} 滞后 {dt.datetime.now().timestamp() - _ms/1000.0:.0f}s"
                else:
                    age = f"  最新{tc}={mx}"
            print(f"  {sch}.{t:<34} 行数={str(n):>9}{age}")
        print("\n" + "=" * 96)
        print("`asterdex_stream_health` 内容（3 行 = 采集器的 3 条流）")
        print("=" * 96)
        cur.execute("SELECT stream, last_event_ms, last_recv_ns, msgs_total, reconnects,"
                    " updated_at FROM asterdex_stream_health ORDER BY stream")
        for r in cur.fetchall():
            _age = dt.datetime.now(dt.timezone.utc) - r[5] if r[5] else None
            print(f"  stream={r[0]:<20} msgs={r[3]:>10} reconnects={r[4]:<4}"
                  f" updated_at 滞后 {_age.total_seconds():.0f}s" if _age else f"  {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
