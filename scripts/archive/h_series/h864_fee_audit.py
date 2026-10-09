# -*- coding: utf-8 -*-
"""[h864] 挂单离场腿为什么带手续费?逐个 fee_bp 分布 + 该腿的元数据。"""
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
import psycopg  # noqa: E402

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT COALESCE(fee_bp,0), count(*), COALESCE(meta_json->>'flatten','?'),"
        " COALESCE(meta_json->>'source','?')"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='flow_exit_maker'"
        " AND ts > now() - interval '6 hours' GROUP BY 1,3,4 ORDER BY 2 DESC")
    print("== flow_exit_maker 腿的 fee_bp 分布 ==")
    for r in cur.fetchall():
        print(f"  fee_bp={float(r[0]):+.2f} n={r[1]:>3} flatten={r[2]} source={r[3]}")
    cur.execute(
        "SELECT COALESCE(fee_bp,0), count(*) FROM lane_ledger"
        " WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='flow_entry_maker'"
        " AND ts > now() - interval '6 hours' GROUP BY 1 ORDER BY 2 DESC")
    print("== flow_entry_maker 腿的 fee_bp 分布 ==")
    for r in cur.fetchall():
        print(f"  fee_bp={float(r[0]):+.2f} n={r[1]:>3}")
    # 谁在写 fee:看 flow_exit_maker 且有费的那些腿的 exit_action
    cur.execute(
        "SELECT COALESCE(meta_json->>'exit_action','?'),"
        " COALESCE(meta_json->>'exit_reason','?'), fee_bp, count(*)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='flow_exit_maker'"
        " AND COALESCE(fee_bp,0) <> 0 AND ts > now() - interval '6 hours'"
        " GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 6")
    print("== 带费的 flow_exit_maker 腿(按 exit_action/reason)==")
    for r in cur.fetchall():
        print(f"  action={str(r[0])[:24]:<24} reason={str(r[1])[:20]:<20} "
              f"fee={float(r[2]):+.2f} n={r[3]}")
