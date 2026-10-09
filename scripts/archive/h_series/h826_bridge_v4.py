# -*- coding: utf-8 -*-
"""[h826 桥 19:58] 新协议复核:门 / 训练器 / 往返聚合 / 学习参数 / 车道。"""
import importlib.util
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")


def _j(name):
    try:
        return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


g = _j("flow_gate_last.json")
print(f"== A 门 == ts={g.get('as_of')} 协议={g.get('protocol')}")
n_open = 0
for k, v in (g.get("gates") or {}).items():
    if v.get("allow"):
        n_open += 1
        oos = v.get("oos") or {}
        print(f"  ✅ {k:<8} {v.get('side')} H={v.get('horizon_sec')}s mu={v.get('mu')} "
              f"OOS={oos.get('mean_y')}bp n_eff={oos.get('n_eff')} win={oos.get('win_rate')}")
print(f"  开门 {n_open}/{len(g.get('gates') or {})}")

# C 往返聚合(流规则自带的 window_stats)
rows = []
p = ROOT / "data" / "flow_roundtrip_log.jsonl"
if p.exists():
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
print(f"== C 往返日志 == {len(rows)} 条")
if rows:
    from backend.services.market_maker.flow_rules import window_stats  # noqa: E402
    now = time.time()
    for label, hours in (("1h", 1), ("4h", 4), ("24h", 24)):
        st = window_stats(rows, now - hours * 3600, now)
        if st["n"]:
            print(f"  {label:<4} n={st['n']:<3} 平均 y={st['mean_y']:+7.2f}bp "
                  f"吃单费占比={st['taker_fee_share']*100:.0f}% "
                  f"挂单成交率={st['fill_rate']*100:.0f}% 强平={st['liquidations']}")
    print("  最近 5 条往返:")
    for r in rows[-5:]:
        print(f"    {r.get('symbol'):<8} {str(r.get('why')):<14} y={r.get('y_bp'):+.2f}bp "
              f"fee={r.get('fee_bp')} maker={r.get('maker')}")
else:
    print("  (尚无往返)")

lp = _j("flow_learn_params.json")
print(f"== D 学习参数 == {lp if lp else '(空 = 全默认)'}")
try:
    pend = json.loads((ROOT / "logs" / "self_tuner_pending.json").read_text(encoding="utf-8"))
    flow_pend = [e for e in pend if str(e.get("era") or "") == "flow"]
    print(f"== E pending == 共 {len(pend)} 条,era=flow {len(flow_pend)} 条")
    for e in flow_pend[:3]:
        print(f"  {e.get('param')} {e.get('old')}→{e.get('new')} metric={e.get('verdict_metric')}")
except Exception:
    print("== E pending == (无)")

d = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
print(f"== F 车道 == ticks={d.get('ticks')} fills/h={d.get('fills_per_hour')} "
      f"宇宙={len(d.get('states') or {})} 币")
print(f"  skip 前 5: {dict(sorted((d.get('skip_counts') or {}).items(), key=lambda x: -x[1])[:5])}")
print(f"  挂单: {d.get('side_counts')}")

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg  # noqa: E402
with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT count(*), SUM(net_bp*notional)/10000.0 FROM lane_ledger"
        " WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '1 hour'")
    n, u = cur.fetchone()
    print(f"== G 近 1h == {n} 腿 净 {float(u or 0):+.3f}U")
    cur.execute(
        "SELECT count(*), SUM(net_bp*notional)/10000.0 FROM lane_ledger"
        " WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '4 hours'")
    n4, u4 = cur.fetchone()
    print(f"   近 4h == {n4} 腿 净 {float(u4 or 0):+.3f}U")
