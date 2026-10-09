# -*- coding: utf-8 -*-
"""[桥 22:58] 加速版每小时复核:七项 + 裁决历史 + 车道状态。"""
import importlib.util
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"


def L(p):
    try:
        return json.loads(open(ROOT + "\\" + p, encoding="utf-8").read())
    except Exception:
        return None


_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

print("== F 24/7 滚动验收 ==")
with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    for hrs in (1, 4, 12, 24):
        cur.execute(
            "SELECT count(*), AVG(net_bp), SUM(net_bp*notional)/10000 FROM lane_ledger"
            " WHERE lane_id='mm_asterdex' AND event='fill'"
            " AND ts > now() - make_interval(hours => %s)", (hrs,))
        n, v, u = cur.fetchone()
        print(f"  近 {hrs:>2}h: n={n:>3} 每腿 {float(v or 0):+7.2f}bp 净 {float(u or 0):+7.3f}U")

k = L("data/markout_kpi_last.json") or {}
print(f"== E KPI == {k.get('adverse_capture_ratio')} {k.get('verdict')} "
      f"(markout {k.get('markout_bp')} 捕获 {k.get('capture_bp')}) ts={time.strftime('%H:%M', time.localtime(k.get('ts', 0)))}")
a = L("data/fill_audit_last.json") or {}
print(f"== G 成交审计 == 合法率 {a.get('strict_rate')} 每腿 {a.get('strict_per_leg_bp')}bp "
      f"n={a.get('checked')} 判定 {a.get('verdict')}")

print("== H 学习吞吐 ==")
v = L("data/vol_top20.json") or {}
print(f"  vol_top20: {time.strftime('%H:%M', time.localtime(v.get('ts', 0)))} 池 {v.get('universe_size')} 宽价差 {len(v.get('wide') or [])}")
w = L("data/watchdog_symbols_last.json") or {}
print(f"  watchdog: {time.strftime('%H:%M', time.localtime(w.get('ts', 0)))} symbols={len(w.get('symbols') or [])} depth={len(w.get('depth') or [])}")
pend = L("logs/self_tuner_pending.json") or []
due = [e for e in pend if float(e.get("verdict_at") or 0) < time.time()]
print(f"  pending: {len(pend)} 条, 已到期 {len(due)} 条: {[e['param'] for e in due]}")
pb = L("data/self_tuner_playbook.json") or {}
print(f"  playbook 定律: {len(pb.get('laws') or [])} 条")

print("== 裁决历史(最近 10 条) ==")
hist = open(ROOT + "/logs/self_tuner_history.jsonl", encoding="utf-8").read().strip().splitlines()
for line in hist[-10:]:
    o = json.loads(line)
    print(f"  {time.strftime('%H:%M', time.localtime(o.get('ts', 0)))} "
          f"{o.get('param', ''):<26} {o.get('old')}->{o.get('new')} {o.get('verdict')}")

try:
    rv = open(ROOT + "/logs/self_tuner_review.json", encoding="utf-8").read()
    print("== A 自调请求 == 有:", rv[:150])
except Exception:
    print("== A 自调请求 == (无)")

d = json.loads(open(ROOT + "/logs/mm_lane_status.json", encoding="utf-8").read())
print(f"== 车道 == 腿速 {d.get('fills_per_hour')} 日亏 {d.get('day_pnl_usd')} 权益 {d.get('equity')}")
print(f"  lane_pause = {d.get('lane_pause_counts')} 宇宙 = {d.get('symbols')}")
