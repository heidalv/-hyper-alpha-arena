# -*- coding: utf-8 -*-
"""[h854] 吃单止损集中在哪:按币 / 按波动 / 按持仓时长拆。"""
import importlib.util
import io
import sys
import time
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
        "SELECT symbol, count(*), AVG(net_bp),"
        " SUM(net_bp*notional)/10000.0, AVG(notional)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='taker_stop'"
        " AND ts > now() - interval '3 hours' GROUP BY 1 ORDER BY 3 LIMIT 14")
    print("== 近 3h 吃单止损:按币 ==")
    for r in cur.fetchall():
        print(f"  {str(r[0]):<9} n={r[1]:>3} 每腿 {float(r[2] or 0):+7.1f}bp "
              f"合计 {float(r[3] or 0):+8.2f}U 均名义 ${float(r[4] or 0):.0f}")

    # 止损腿的 notional 与方向分布
    cur.execute(
        "SELECT meta_json->>'side', count(*), AVG(net_bp)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='taker_stop'"
        " AND ts > now() - interval '3 hours' GROUP BY 1")
    print(" 按方向:", [(r[0], r[1], round(float(r[2] or 0), 1)) for r in cur.fetchall()])

    # 与"最大不利偏移"对比:止损腿的 15s 前价格 vs 成交价(看是不是跳空)
    cur.execute(
        "SELECT count(*) FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='taker_stop'"
        " AND ts > now() - interval '3 hours' AND net_bp < -80")
    print(f" 其中超过 −80bp 的(深跳):{cur.fetchone()[0]} 条")

    # 持仓时长分布(用 position_id 配对:入场腿时间到止损腿时间)
    cur.execute("""
      WITH e AS (SELECT position_id, min(ts) t0 FROM lane_ledger
                 WHERE lane_id='mm_asterdex' AND event='fill'
                 AND COALESCE(meta_json->>'exit_path','')='flow_entry_maker'
                 AND ts > now() - interval '3 hours' GROUP BY 1),
           x AS (SELECT position_id, min(ts) t1 FROM lane_ledger
                 WHERE lane_id='mm_asterdex' AND event='fill'
                 AND COALESCE(meta_json->>'exit_path','')='taker_stop'
                 GROUP BY 1)
      SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (x.t1-e.t0))),
             count(*)
      FROM e JOIN x USING (position_id)""")
    r = cur.fetchone()
    print(f" 止损腿的持仓时长中位数:{float(r[0] or 0):.0f} 秒(n={r[1]})")
