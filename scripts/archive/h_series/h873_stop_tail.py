# -*- coding: utf-8 -*-
"""[h873] 剩余的止损是"深跳":哪些币、跳多深、振幅多少。"""
import importlib.util
import io
import sys
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import json  # noqa: E402
import psycopg  # noqa: E402

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT symbol, count(*), AVG(net_bp), MIN(net_bp), SUM(net_bp*notional)/10000.0"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='taker_stop'"
        " AND ts > now() - interval '3 hours' GROUP BY 1 ORDER BY 5 LIMIT 12")
    print("== 近 3h 止损腿 按币 ==")
    for r in cur.fetchall():
        print(f"  {str(r[0]):<10} n={r[1]:>3} 平均 {float(r[2] or 0):+7.1f}bp "
              f"最差 {float(r[3] or 0):+7.1f}bp 合计 {float(r[4] or 0):+8.2f}U")
    cur.execute(
        "SELECT count(*) FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='taker_stop'"
        " AND ts > now() - interval '1 hour' AND net_bp < -60")
    print(f"  近 1h 止损中深于 −60bp 的:{cur.fetchone()[0]} 条")

vt = json.loads((ROOT / "data" / "vol_top20.json").read_text(encoding="utf-8"))
amp = {str(d.get("symbol") or "").upper(): (float(d.get("range_pct_24h") or 0),
        bool(d.get("gap_prone"))) for d in (vt.get("detail") or [])}
print("== 振幅/跳空标记 ==")
for sym in ("GTC", "LYN", "US", "PUMP", "QNT", "PLAY", "PONS", "NEAR", "SEI", "ONE"):
    a, g = amp.get(sym, (0.0, False))
    print(f"  {sym:<8} 振幅 {a*100:>4.1f}% gap_prone={g}")
