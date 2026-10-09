# -*- coding: utf-8 -*-
"""[h714 阶段0b 2026-10-02] 每币 edge 后验 + 冷启动成本统计(选币 bandit 的数据基础)。

设计文档 §L3:选币 = 带切换成本的 bandit。本脚本从账本产出每币:
  1. 后验:近 7 天每腿净 bp(指数衰减,半衰期 24h)的均值/标准差/样本数;
  2. 冷启动成本:币龄分档(0-30min / 30-120min / 2-6h / >6h)每腿净;
  3. 切换成本估计 = 好档(2-6h) − 冷启动档(0-30min),有实测才给值。
输出 data/coin_posterior_last.json(供阶段2 bandit 与 DSH 桥复核)。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"
HALF_LIFE_H = 24.0
LOOKBACK_DAYS = 7


def _dsn() -> str:
    _spec = importlib.util.spec_from_file_location("h425", ROOT / "scripts" / "h425_repair_trial.py")
    _h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    return _h.read_env_dsn()


def main() -> int:
    import math
    import psycopg

    from backend.services import lane_registry as reg

    meta = (reg.get_lane(LANE) or {}).get("meta") or {}
    symbols = list(meta.get("symbols") or [])
    now = time.time()
    lam = math.log(2.0) / (HALF_LIFE_H * 3600.0)

    # 币龄窗口(从 ops_changes 重建,与 h708 同法)
    ops = [o for o in (meta.get("ops_changes") or [])
           if o.get("op") == "set_symbols" and o.get("after") and o.get("ts")]
    ops.sort(key=lambda o: o["ts"])
    from datetime import datetime
    active: dict = {}
    tenure: dict = {}
    for o in ops:
        t = datetime.fromisoformat(str(o["ts"]).replace("Z", "+00:00")).timestamp()
        after = {str(s).upper() for s in (o.get("after") or [])}
        for s in after:
            if s not in active:
                active[s] = t
        for s in list(active):
            if s not in after:
                tenure.setdefault(s, []).append((active.pop(s), t))
    for s, t0 in active.items():
        tenure.setdefault(s, []).append((t0, now))

    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, extract(epoch from ts)::double precision, net_bp"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
            " AND COALESCE(meta_json->>'exit_path','') = ''"
            " AND ts >= now() - interval '%s days'", (LANE, LOOKBACK_DAYS))
        rows = cur.fetchall()
    print(f"近 {LOOKBACK_DAYS} 天入场腿 {len(rows)} 条")

    out = {"ts": now, "half_life_h": HALF_LIFE_H, "per_coin": {}}
    for sym in sorted({str(r[0]).upper() for r in rows}):
        legs = [(float(r[1]), float(r[2])) for r in rows if str(r[0]).upper() == sym]
        # 指数衰减后验
        w_sum = ew_sum = 0.0
        vals = []
        for ts, net in legs:
            w = math.exp(-lam * (now - ts))
            w_sum += w
            ew_sum += w * net
            vals.append(net)
        if w_sum <= 0 or len(vals) < 10:
            continue
        mean = ew_sum / w_sum
        n_eff = len(vals)
        # 方差(权重化近似)
        var = sum(w * (v - mean) ** 2 for v in vals) / w_sum if len(vals) > 1 else 0.0
        # 币龄分档
        tb: dict = {}
        for ts, net in legs:
            ten = None
            for t0, t1 in tenure.get(sym, []):
                if t0 - 5 <= ts <= t1 + 5:
                    ten = (ts - t0) / 60.0
                    break
            if ten is None:
                continue
            b = "0-30min" if ten < 30 else "30-120min" if ten < 120 else "2-6h" if ten < 360 else ">6h"
            tb.setdefault(b, []).append(net)
        bands = {b: {"n": len(v), "mean_bp": round(sum(v) / len(v), 3)}
                 for b, v in tb.items() if len(v) >= 8}
        # 切换成本 = 2-6h − 0-30min(好档 − 冷启动档)
        cost = None
        if "2-6h" in bands and "0-30min" in bands:
            cost = round(bands["2-6h"]["mean_bp"] - bands["0-30min"]["mean_bp"], 3)
        out["per_coin"][sym] = {
            "n": n_eff, "mean_bp": round(mean, 3), "std_bp": round(var ** 0.5, 3),
            "bands": bands, "switch_cost_bp": cost,
            "in_universe": sym in symbols,
        }
    # 汇总
    inu = {k: v for k, v in out["per_coin"].items() if v["in_universe"]}
    costs = [v["switch_cost_bp"] for v in out["per_coin"].values() if v.get("switch_cost_bp") is not None]
    out["summary"] = {
        "coins_with_data": len(out["per_coin"]),
        "in_universe": len(inu),
        "switch_cost_median_bp": round(sorted(costs)[len(costs) // 2], 3) if costs else None,
    }
    p = ROOT / "data" / "coin_posterior_last.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 每币后验(均值 bp | n | 切换成本) ==")
    for s, v in sorted(out["per_coin"].items(), key=lambda kv: -kv[1]["mean_bp"]):
        flag = "★" if v["in_universe"] else " "
        print(f"  {flag} {s:<10} {v['mean_bp']:+.2f}bp n={v['n']:>4} σ={v['std_bp']:.2f}"
              + (f" 切换成本{v['switch_cost_bp']:+.1f}bp" if v.get("switch_cost_bp") is not None else ""))
    print(f"\n切换成本中位: {out['summary']['switch_cost_median_bp']} bp/腿")
    print("✓ 已写 data/coin_posterior_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
