# -*- coding: utf-8 -*-
"""[h853] 挂单止损阶梯上线后的前后对比(只看改动之后的窗口)。"""
import importlib.util
import io
import json
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

# 改动时刻 ≈ 00:52(重启 worker)
cut = time.time() - 900        # 近 15 分钟
with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT COALESCE(meta_json->>'exit_path','(空)') p, count(*),"
        " SUM(COALESCE(fee_bp,0)*notional)/10000.0, SUM(net_bp*notional)/10000.0,"
        " AVG(net_bp)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '15 minutes' GROUP BY 1 ORDER BY 4 DESC")
    print("== 改动后近 15 分钟(按出场路径)==")
    for r in cur.fetchall():
        print(f"  {str(r[0])[:20]:<20} n={r[1]:>3} 手续费 {float(r[2] or 0):+7.3f}U "
              f"净 {float(r[3] or 0):+8.3f}U 每腿 {float(r[4] or 0):+6.2f}bp")
    cur.execute(
        "SELECT count(*), SUM(COALESCE(fee_bp,0)*notional)/10000.0,"
        " SUM((COALESCE(spread_bp,0)+COALESCE(price_bp,0))*notional)/10000.0,"
        " SUM(net_bp*notional)/10000.0"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '15 minutes'")
    n, fee, gross, net = cur.fetchone()
    print(f"  合计 {n} 腿 | 手续费 {float(fee or 0):+.3f}U | 毛额 {float(gross or 0):+.3f}U "
          f"| 净 {float(net or 0):+.3f}U | 手续费/毛额 = "
          f"{(abs(float(fee or 0))/abs(float(gross))*100 if gross else 0):.0f}%")

# 往返日志:改动后的窗口
rows = [json.loads(x) for x in (ROOT / "data" / "flow_roundtrip_log.jsonl")
        .read_text(encoding="utf-8").splitlines() if x.strip()]
recent = [r for r in rows if float(r.get("ts") or 0) > time.time() - 1800]
if recent:
    mk = [r for r in recent if float(r.get("fee_bp") or 0) == 0]
    tk = [r for r in recent if float(r.get("fee_bp") or 0) > 0]
    print(f"\n== 往返(近 30 分钟)共 {len(recent)} 条 ==")
    if mk:
        ys = [float(r['y_bp']) for r in mk if r.get('y_bp') is not None]
        print(f"  挂单往返(0 费)n={len(mk):>3} 平均 {sum(ys)/len(ys):+7.2f}bp")
    if tk:
        ys = [float(r['y_bp']) for r in tk if r.get('y_bp') is not None]
        print(f"  吃单往返(4bp) n={len(tk):>3} 平均 {sum(ys)/len(ys):+7.2f}bp")
    print(f"  挂单占比 {len(mk)/len(recent)*100:.0f}%(改动前 6h 窗口是 50%)")
    print("  最近 6 条:", [(r.get('symbol'), r.get('why'), round(float(r.get('y_bp') or 0), 1))
                          for r in recent[-6:]])
