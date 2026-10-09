# -*- coding: utf-8 -*-
"""[h726 2026-10-02] P1/P2/P3 可行性研究(数据驱动,不做任何改动)。

P1 逐币成交强度拟合:用 h713 触及率曲线转 λ(δ)=−ln(1−p)/60,拟合 lnλ=lnΛ−kδ
   ⇒ 每币 k/Λ/R²;再用 λ(δ)×账本净(捕获档) 找 A-S 式最优半宽,与现行 3bp 对比。
P2 延迟逆向成本:从账本 quote_ts→fill 的实测逆向漂移 α = side×(mid_fill−mid_quote)
   (bp);再按 σ√Δt 公式反推 c;估算"撤单延迟减半"的潜在节省。
P3 逆向/捕获比 KPI:用 P2 的 α 与实测捕获算当前比值(健康区 <0.7)。
"""
from __future__ import annotations

import bisect
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


def _market_dsn() -> str:
    from backend.services.market_maker.attribution import _market_dsn as f
    return f()


def _lane_dsn() -> str:
    _spec = importlib.util.spec_from_file_location("h425", ROOT / "scripts" / "h425_repair_trial.py")
    _h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    return _h.read_env_dsn()


def main() -> int:
    import psycopg

    now = time.time()
    since = now - 2 * 86400
    # ===== P1: 触及率 → λ(δ) 拟合 =====
    print("== P1 逐币成交强度 k 拟合(λ=Λe^(−kδ))== ")
    try:
        surf = json.loads((ROOT / "data" / "surface_fit_last.json").read_text(encoding="utf-8"))
    except Exception:
        print("  无 surface_fit_last.json(先跑 h713)")
        surf = None
    widths = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    p1_rows = []
    if surf:
        for s in surf.get("per_symbol") or []:
            probs = s.get("probs")
            if not probs or len(probs) != len(widths):
                continue
            lam = [-np.log(1 - max(0.01, min(0.99, p))) / 60.0 for p in probs]
            # ln λ = lnΛ − kδ
            x = np.array(widths)
            y = np.log(np.array(lam))
            mask = np.isfinite(y)
            if mask.sum() < 4:
                continue
            A = np.vstack([np.ones(mask.sum()), x[mask]]).T
            coef, res, *_ = np.linalg.lstsq(A, y[mask], rcond=None)
            lnL, negk = coef
            pred = A @ coef
            ss_tot = float(((y[mask] - y[mask].mean()) ** 2).sum())
            ss_res = float(((y[mask] - pred) ** 2).sum())
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
            k = float(-negk)
            Lam = float(np.exp(lnL))
            p1_rows.append((s.get("symbol"), k, Lam, r2))
    else:
        print("  (跳过,无触及率数据)")
    if p1_rows:
        print(f"  {'币':<8}{'k(1/bp)':>9}{'Λ(填/s)':>10}{'R²':>7}{'半宽1/k(bp)':>12}")
        for sym, k, Lam, r2 in sorted(p1_rows, key=lambda r: -r[3]):
            print(f"  {sym:<8}{k:>9.3f}{Lam:>10.4f}{r2:>7.2f}{1/max(k,1e-6):>12.1f}")
        # A-S 式最优:max λ(δ)×net(0.4δ)。net 用账本捕获档净(6h 滚动,从 surf)
        nb = surf.get("net_band") or {}
        def net_of(delta):
            cap = 0.4 * delta
            if cap < 0.5:
                return nb.get("a", 0.0)
            if cap < 1.0:
                return nb.get("b", 0.0)
            if cap < 2.0:
                return nb.get("c", 0.0)
            return nb.get("d", 0.0)
        print(f"\n  账本捕获档净(6h): {nb}")
        print(f"  {'币':<8}{'最优半宽bp':>10}{'λ(填/h)':>9}{'期望(相对)':>12}{'vs 现行3bp':>12}")
        for sym, k, Lam, r2 in p1_rows:
            best, bestv = None, -1e18
            for d in np.linspace(0.5, 6.0, 24):
                v = Lam * np.exp(-k * d) * 3600 * net_of(d)
                if v > bestv:
                    best, bestv = d, v
            v3 = Lam * np.exp(-k * 3.0) * 3600 * net_of(3.0)
            print(f"  {sym:<8}{best:>10.1f}{Lam*np.exp(-k*best)*3600:>9.1f}"
                  f"{bestv:>12.2f}{('+' if bestv > v3 else '') + str(round((bestv/v3-1)*100, 0)) + '%' if v3 > 0 else 'n/a':>12}")

    # ===== P2: 实测逆向漂移(quote_ts → fill)=====
    print("\n== P2 延迟逆向成本实测 ==")
    with psycopg.connect(_lane_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, meta_json->>'side', meta_json->>'quote_ts',"
            " extract(epoch from ts)::double precision,"
            " meta_json->>'fill_px', meta_json->>'mid_px'"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
            " AND ts >= to_timestamp(%s) AND COALESCE(meta_json->>'exit_path','') = ''",
            (LANE, since))
        legs = []
        for r in cur.fetchall():
            sym, side, qts, ts, fpx, mpx = r
            try:
                qts = float(qts) if qts else None
                fpx = float(fpx) if fpx else None
            except Exception:
                qts, fpx = None, None
            if qts and fpx:
                legs.append((str(sym).upper(), side, qts, float(ts), fpx))
    print(f"  有 quote_ts+fill_px 的入场腿 {len(legs)} 条")
    syms = sorted({l[0] for l in legs})
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        book = {}
        for s in syms:
            cur.execute(
                "SELECT event_ts_ms, (bid_px+ask_px)/2 FROM asterdex_book_ticker"
                " WHERE symbol=%s AND event_ts_ms >= %s AND bid_px>0 ORDER BY event_ts_ms",
                (s + "USDT", int((since - 120) * 1000)))
            rows = cur.fetchall()
            book[s] = ([float(r[0]) / 1000.0 for r in rows],
                       [float(r[1]) for r in rows])
    adv, dts = [], []
    for sym, side, qts, fts, fpx in legs:
        tms, mids = book.get(sym, ([], []))
        if not tms:
            continue
        i = bisect.bisect_right(tms, qts) - 1
        j = bisect.bisect_right(tms, fts) - 1
        if i < 0 or j <= i or mids[i] <= 0:
            continue
        d = 1.0 if side == "buy" else -1.0
        a = (mids[j] - mids[i]) / mids[i] * 1e4 * d   # 买:中价下跌为逆向(+)
        adv.append(a)
        dts.append(max(0.1, fts - qts))
    if adv:
        mean_a = float(np.mean(adv))
        mean_dt = float(np.mean(dts))
        # σ(每币 5s 网格收益标准差,换算 bp/√s 的粗估)
        sigmas = []
        for sym, side, qts, fts, fpx in legs:
            tms, mids = book.get(sym, ([], []))
            if not tms:
                continue
            rets = np.diff(mids) / mids[1:]
            s = float(np.std(rets))
            if s > 0:
                sigmas.append(s)
        sigma = float(np.mean(sigmas)) if sigmas else 0.0
        c_ell = mean_a / (sigma * np.sqrt(mean_dt)) if sigma > 0 else 0.0
        save_half = mean_a * (1 - 1 / np.sqrt(2))   # Δt 减半的节省
        print(f"  实测每腿逆向漂移 α = {mean_a:+.3f}bp(n={len(adv)})")
        print(f"  挂单→成交平均年龄 {mean_dt:.1f}s | 平均 σ={sigma*1e4*100:.2f}bp(5s 网格)")
        print(f"  α=c·σ·√Δt ⇒ 反推 c={c_ell:.2f}")
        print(f"  **撤单延迟减半可省 ≈ {save_half:+.3f}bp/腿**(占捕获 ~{save_half/1.2*100:.0f}%)")
        # ===== P3 =====
        ratio = mean_a / 1.2   # 当前捕获约 0.4×3bp=1.2bp
        print(f"\n== P3 逆向/捕获比 ==\n  当前 ≈ {ratio:.2f}"
              f"({'健康(<0.7)' if ratio < 0.7 else '告警(≥0.7,该收缩/停挂)'})")
    else:
        print("  样本不足(quote_ts 缺失的腿多)")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
