# -*- coding: utf-8 -*-
"""[h803 2026-10-04 用户"学习进化也要改,不是做市商逻辑了"]

主动流交易时代的核心学习指标:往返 P&L(round-trip)。
做市时代看"每腿净";流交易看"进场→离场"一个完整往返:
  · round_trip_net = 进场腿 + 离场腿 的净额(按 position_id 配对);
  · 胜率 / 平均往返净 / 平均持仓时长 / 各离场路径分布;
  · 进场腿的过路费(价差+手续费) vs 离场腿的漂移实现。
输出 data/flow_roundtrip_last.json;任务每 10 分钟跑;桥用它判
"跟流能不能赚",以及 SL/TP/阈值/持仓时限的调整方向。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def _dsn():
    _spec = importlib.util.spec_from_file_location(
        "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
    h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(h)
    return h.read_env_dsn()


def main() -> int:
    import psycopg

    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT position_id, ts, COALESCE(meta_json->>'exit_path','maker'),"
            " net_bp, notional, symbol"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
            " AND COALESCE(meta_json->>'exit_path','') IN"
            " ('flow_entry_taker','sl_taker','tp_taker','flow_flip_taker','max_hold_taker')"
            " AND ts > now() - interval '6 hours' ORDER BY ts", (LANE,))
        rows = cur.fetchall()
    # 按 position_id 分组往返
    trips = {}
    for pid, ts, path, nb, ntl, sym in rows:
        t = trips.setdefault(str(pid), {"sym": str(sym), "legs": []})
        t["legs"].append({"ts": float(ts.timestamp()), "path": str(path),
                          "nb": float(nb or 0), "ntl": float(ntl or 0)})
    done = []
    for pid, t in trips.items():
        entries = [l for l in t["legs"] if l["path"] == "flow_entry_taker"]
        exits = [l for l in t["legs"] if l["path"] != "flow_entry_taker"]
        if not entries or not exits:
            continue
        e = entries[0]
        x = exits[-1]
        net_bp = (e["nb"] * e["ntl"] + x["nb"] * x["ntl"]) / max(1e-9, e["ntl"])
        done.append({
            "symbol": t["sym"], "hold_sec": round(x["ts"] - e["ts"], 1),
            "entry_bp": round(e["nb"], 2), "exit_bp": round(x["nb"], 2),
            "exit_path": x["path"], "net_bp": round(net_bp, 2),
        })
    if not done:
        print("近 6h 无完整往返(样本不足)")
        out = {"ts": time.time(), "n": 0}
    else:
        wins = [d for d in done if d["net_bp"] > 0]
        by_exit = {}
        for d in done:
            b = by_exit.setdefault(d["exit_path"], [])
            b.append(d["net_bp"])
        out = {
            "ts": time.time(), "n": len(done),
            "win_rate": round(len(wins) / len(done), 3),
            "avg_net_bp": round(sum(d["net_bp"] for d in done) / len(done), 2),
            "avg_hold_sec": round(sum(d["hold_sec"] for d in done) / len(done), 1),
            "by_exit": {k: {"n": len(v),
                            "avg_net_bp": round(sum(v) / len(v), 2)}
                        for k, v in by_exit.items()},
            "detail": done[-12:],
        }
        print(f"== 流交易往返 KPI(近 6h,{len(done)} 个完整往返)== ")
        print(f"  胜率 {out['win_rate']*100:.0f}% | 平均往返净 {out['avg_net_bp']:+.2f}bp | "
              f"平均持仓 {out['avg_hold_sec']:.0f}s")
        for k, v in out["by_exit"].items():
            print(f"  {k:<18} n={v['n']:>3} 平均往返净 {v['avg_net_bp']:+.2f}bp")
        for d in done[-8:]:
            print(f"  {d['symbol']:<9} 持 {d['hold_sec']:>5.0f}s 进 {d['entry_bp']:+6.1f} "
                  f"出 {d['exit_bp']:+6.1f}({d['exit_path']}) 净 {d['net_bp']:+7.2f}bp")
    (ROOT / "data" / "flow_roundtrip_last.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  ✓ 已写 data/flow_roundtrip_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
