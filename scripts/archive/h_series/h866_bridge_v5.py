# -*- coding: utf-8 -*-
"""[h866 桥 04:58] 三核心读数 + **逐策略拆分**(路由上线后的第一份)。"""
import importlib.util
import io
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
rows = [json.loads(x) for x in (ROOT / "data" / "flow_roundtrip_log.jsonl")
        .read_text(encoding="utf-8").splitlines() if x.strip()]
rows = [r for r in rows if r.get("y_bp") is not None]
now = time.time()


def win(hrs):
    return [r for r in rows if float(r.get("ts") or 0) > now - hrs * 3600]


def stats(rs):
    if not rs:
        return None
    mk = [r for r in rs if float(r.get("fee_bp") or 0) == 0]
    tk = [r for r in rs if float(r.get("fee_bp") or 0) > 0]
    ys = [float(r["y_bp"]) for r in rs]
    mky = [float(r["y_bp"]) for r in mk]
    return {"n": len(rs), "maker_n": len(mk), "maker_pct": len(mk) / len(rs) * 100,
            "maker_y": float(np.mean(mky)) if mky else 0.0,
            "exp": float(np.mean(ys)), "total": float(np.sum(ys))}


print("== A 三核心读数 ==")
for h in (1, 4, 12, 24):
    st = stats(win(h))
    if st:
        print(f"  近 {h:>2}h: n={st['n']:>3} 挂单率 {st['maker_pct']:>3.0f}% | "
              f"挂单 y {st['maker_y']:+7.2f}bp | **组合期望 {st['exp']:+7.2f}bp/笔** "
              f"(合计 {st['total']:+.0f}bp)")

print("\n== 逐策略拆分(全部历史)== ")
by = defaultdict(list)
for r in rows:
    by[str(r.get("strategy") or "(未标)")].append(r)
for s, rs in sorted(by.items(), key=lambda x: -len(x[1])):
    st = stats(rs)
    hy = [float(r.get("hold_sec") or 0) for r in rs if r.get("hold_sec")]
    print(f"  {s:<7} n={st['n']:>3} 挂单率 {st['maker_pct']:>3.0f}% | "
          f"挂单 y {st['maker_y']:+7.2f}bp | 组合期望 {st['exp']:+7.2f}bp/笔 | "
          f"平均持有 {np.mean(hy) if hy else 0:.0f}s")

# 车道
d = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
sk = d.get("skip_counts") or {}
print("\n== F 车道 ==")
print(f"  fills/h={d.get('fills_per_hour')} | skip 前 6: "
      f"{dict(sorted(sk.items(), key=lambda x: -x[1])[:6])}")
print(f"  active_flow_error={sk.get('active_flow_error', 0)} | 挂单 {d.get('side_counts')}")
for f, label in (("flow_gate_last.json", "C 门"),
                 ("flow_learn_params.json", "D 学习参数"),
                 ("flow_fill_markout.json", "B 经验层")):
    p = ROOT / "data" / f
    if p.exists():
        doc = json.loads(p.read_text(encoding="utf-8"))
        age = (now - p.stat().st_mtime) / 60
        if f == "flow_gate_last.json":
            n_open = sum(1 for v in (doc.get("gates") or {}).values() if v.get("allow"))
            print(f"  {label}: {age:.0f} 分钟前 | 协议 {doc.get('protocol')} | "
                  f"开门 {n_open}/{len(doc.get('gates') or {})}")
        elif f == "flow_learn_params.json":
            print(f"  {label}: {json.dumps(doc, ensure_ascii=False)}")
        else:
            agg = doc.get("agg") or {}
            print(f"  {label}: " + " | ".join(
                f"{k} {v.get('mean_bp'):+.2f}bp(t={v.get('t'):+.2f})"
                for k, v in agg.items()))
_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg  # noqa: E402
with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    for mins in (30, 60):
        cur.execute(
            "SELECT count(*), SUM(net_bp*notional)/10000.0 FROM lane_ledger"
            " WHERE lane_id='mm_asterdex' AND event='fill'"
            " AND ts > now() - (%s * interval '1 minute')", (mins,))
        n, u = cur.fetchone()
        print(f"  G 近 {mins:>2} 分钟: {n:>4} 腿 净 **{float(u or 0):+8.3f}U**")
