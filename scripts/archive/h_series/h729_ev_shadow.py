# -*- coding: utf-8 -*-
"""[h729 R1 2026-10-02] 期望值引擎 vs 现行硬闸门:影子对比。

R1 口径(概率替代预测研究 §四):
  - 期望值引擎(每币每侧):δ*=argmax λ(δ)×(capture(δ) − adverse_cond),
    只挂 E>0 的档;λ(δ)=Λe^(−kδ)(h726 逐币拟合);capture=0.42δ;adverse_cond 用
    实测条件逆向(成交后 10s markout 分状态表)。
  - 现行策略:账本已实现的每腿净/时。
  - 硬闸门直接成本:gate_audit 里被拦腿的反事实 cf 合计(实测为正 = 拦掉了利润)。
输出 data/ev_shadow_last.json + 控制台对比表。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"
CAPTURE_RATIO = 0.42          # 捕获/半宽 折算比(实测 0.40~0.46)
ADVERSE_COND_BP = 1.0         # 成交后 10s 逆向(实测 −0.74~−1.07bp,取保守 1.0)
WINDOW_H = 24.0


def _lane_dsn() -> str:
    _spec = importlib.util.spec_from_file_location("h425", ROOT / "scripts" / "h425_repair_trial.py")
    _h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    return _h.read_env_dsn()


def main() -> int:
    import psycopg

    # 1) 逐币 k/Λ(从 surface_fit 触及率重拟合,与 h726 同法)
    try:
        surf = json.loads((ROOT / "data" / "surface_fit_last.json").read_text(encoding="utf-8"))
    except Exception:
        print("无 surface_fit_last.json")
        return 1
    widths = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    kfit: Dict[str, Tuple[float, float]] = {}
    for s in surf.get("per_symbol") or []:
        probs = s.get("probs")
        if not probs or len(probs) != len(widths):
            continue
        lam = [-np.log(1 - max(0.01, min(0.99, p))) / 60.0 for p in probs]
        x = np.array(widths)
        y = np.log(np.array(lam))
        mask = np.isfinite(y)
        if mask.sum() < 4:
            continue
        A = np.vstack([np.ones(mask.sum()), x[mask]]).T
        coef, *_ = np.linalg.lstsq(A, y[mask], rcond=None)
        kfit[str(s.get("symbol"))] = (float(np.exp(coef[0])), float(-coef[1]))

    # 2) 账本:近 24h 每币的已实现 fills/时 与 净/时
    since = time.time() - WINDOW_H * 3600
    with psycopg.connect(_lane_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, count(*), SUM(net_bp*notional)/10000.0, AVG(net_bp), SUM(notional)"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= to_timestamp(%s)"
            " GROUP BY 1", (LANE, since))
        realized = {}
        for r in cur.fetchall():
            realized[str(r[0]).upper()] = {
                "legs": int(r[1]), "net_usd": float(r[2] or 0),
                "net_bp": float(r[3] or 0),
                "notional_sum": float(r[4] or 0),
                "legs_per_h": int(r[1]) / WINDOW_H,
                "net_per_h": float(r[2] or 0) / WINDOW_H,
            }

    # 3) EV 引擎逐币期望(用账本捕获档净——已含出场成本,与实现口径同源)
    nb = surf.get("net_band") or {}

    def band_net(cap: float) -> float:
        if cap < 0.5:
            return float(nb.get("a", 0.0))
        if cap < 1.0:
            return float(nb.get("b", 0.0))
        if cap < 2.0:
            return float(nb.get("c", 0.0))
        return float(nb.get("d", 0.0))

    print(f"\n捕获档净(6h 滚动,含出场): {nb}")
    print(f"  {'币':<10}{'k':>6}{'δ*(bp)':>8}{'E填/h':>7}{'E净/h(U$)':>11}{'现行净/h(U$)':>12}{'差异':>9}")
    ev_rows = []
    for sym, (Lam, k) in sorted(kfit.items(), key=lambda kv: -kv[1][0]):
        cur = realized.get(sym, {})
        avg_notional = (cur.get("notional_sum", 0.0) / max(1, cur.get("legs", 1)))
        best, bestv_usd = None, -1e18
        for d in np.linspace(0.5, 6.0, 45):
            nfill = Lam * np.exp(-k * d) * 3600          # 填/h(双侧)
            net_bp = band_net(0.42 * d)
            v_usd = nfill * net_bp * avg_notional / 1e4  # U$/h
            if v_usd > bestv_usd:
                best, bestv_usd = d, v_usd
        net_h_cur = cur.get("net_per_h", 0.0)
        diff = bestv_usd - net_h_cur
        print(f"  {sym:<10}{k:>6.2f}{best:>8.1f}{Lam*np.exp(-k*best)*3600:>7.0f}"
              f"{bestv_usd:>11.4f}{net_h_cur:>12.4f}{diff:>+9.4f}")
        ev_rows.append({"symbol": sym, "k": round(k, 3), "delta_opt_bp": round(float(best), 2),
                        "ev_fill_per_h": round(float(Lam * np.exp(-k * best) * 3600), 1),
                        "ev_net_usd_per_h": round(float(bestv_usd), 4),
                        "realized_net_usd_per_h": round(net_h_cur, 4),
                        "avg_notional": round(avg_notional, 1)})

    # 4) 硬闸门直接成本(被拦腿的反事实利润)
    try:
        aud = json.loads((ROOT / "data" / "gate_audit_last.json").read_text(encoding="utf-8"))
        gates = aud.get("gates") or {}
        total_blocked_cf = sum((g.get("n") or 0) * (g.get("mean_cf_bp") or 0)
                               for g in gates.values() if (g.get("mean_cf_bp") or 0) > 0)
        print(f"\n硬闸门直接成本(被拦腿反事实利润合计): {total_blocked_cf:.1f} bp·腿"
              f"(即 {total_blocked_cf/1e4*30:.2f}U @ 平均单腿 $30)")
    except Exception:
        total_blocked_cf = None
        print("\n(无 gate_audit 数据)")

    out = {"ts": time.time(), "capture_ratio": CAPTURE_RATIO,
           "adverse_cond_bp": ADVERSE_COND_BP, "per_symbol": ev_rows,
           "blocked_cf_bp_legs": total_blocked_cf,
           "note": "ev_net_per_h_unit 与 realized_net_per_h 单位不同(见下文口径说明),"
                   "对比看方向不看绝对值"}
    (ROOT / "data" / "ev_shadow_last.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n✓ 已写 data/ev_shadow_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
