# -*- coding: utf-8 -*-
"""[h717 阶段3 2026-10-02] 复利阶梯前置筛查:行情状态 × 规模档 × 每腿净。

背景:h710 现场证伪"规模放大=正收益"(compound 0.5 后每腿 −1.25bp),但那次
发生在慢牛行情(L11:趋势期 fade 腿全亏)。设计文档 §H5:复利阶梯必须**逐档验证**。
本脚本回答:规模放大的安全性是否**条件于行情状态**?
  - 用 h712 的特征重建(每腿入场时刻 trend900),把 3 天入场腿分"震荡/趋势";
  - 在每个行情状态下,按名义档算每腿净 bp;
  - 若"震荡行情里 40-80 档 > 20-40 档"成立 ⇒ 复利阶梯可以在**震荡行情**启动,
    趋势行情自动降档(联动 L11 趋势闸)。
输出 data/compound_ladder_prescreen.json。
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
TREND_TH_BP = 10.0   # |trend900| 阈值:震荡 vs 趋势


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
    from datetime import datetime, timedelta, timezone

    now = time.time()
    since = now - 3 * 86400
    with psycopg.connect(_lane_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, extract(epoch from ts)::double precision, net_bp, notional,"
            " meta_json->>'quote_ts'"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
            " AND ts >= to_timestamp(%s) AND COALESCE(meta_json->>'exit_path','') = ''",
            (LANE, since))
        legs = []
        for r in cur.fetchall():
            try:
                qts = float(r[4]) if r[4] else float(r[1])
            except Exception:
                qts = float(r[1])
            legs.append({"sym": str(r[0]).upper(), "ts": float(r[1]),
                         "net": float(r[2]), "notional": float(r[3]), "qts": qts})
    print(f"近 3 天入场腿 {len(legs)} 条")
    syms = sorted({l["sym"] for l in legs})

    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        book: Dict[str, Tuple[List[float], List[float]]] = {}
        for s in syms:
            cur.execute(
                "SELECT event_ts_ms, (bid_px+ask_px)/2 FROM asterdex_book_ticker"
                " WHERE symbol=%s AND event_ts_ms >= %s AND bid_px>0 AND ask_px>bid_px"
                " ORDER BY event_ts_ms",
                (s + "USDT", int((since - 1000) * 1000)))
            rows = cur.fetchall()
            book[s] = ([float(r[0]) / 1000.0 for r in rows],
                       [float(r[1]) for r in rows])
        print("盘口加载完成")

    def _trend900(tms: List[float], mids: List[float], t0: float) -> Optional[float]:
        i = bisect.bisect_right(tms, t0) - 1
        j = bisect.bisect_right(tms, t0 - 900.0) - 1
        if i <= j or i < 0 or j < 0 or mids[j] <= 0:
            return None
        return (mids[i] - mids[j]) / mids[j] * 1e4

    cells: Dict[str, Dict[str, list]] = {}
    n_assigned = 0
    for l in legs:
        tms, mids = book.get(l["sym"], ([], []))
        if not tms:
            continue
        t9 = _trend900(tms, mids, l["qts"])
        if t9 is None:
            continue
        n_assigned += 1
        regime = "chop" if abs(t9) < TREND_TH_BP else "trend"
        size = ("s<20" if l["notional"] < 20 else
                "s20-40" if l["notional"] < 40 else
                "s40-80" if l["notional"] < 80 else "s>=80")
        cell = cells.setdefault(regime, {}).setdefault(size, [])
        cell.append(l["net"])
    print(f"可归入行情状态的腿 {n_assigned} 条\n")

    out = {"ts": now, "trend_th_bp": TREND_TH_BP, "regimes": {}}
    print(f"  {'行情':<8}{'规模档':<8}{'n':>6}{'每腿净':>9}")
    for regime in ("chop", "trend"):
        r = cells.get(regime, {})
        rr = {}
        for size in ("s<20", "s20-40", "s40-80", "s>=80"):
            v = r.get(size)
            if v and len(v) >= 20:
                mean = sum(v) / len(v)
                print(f"  {regime:<8}{size:<8}{len(v):>6}{mean:>+9.2f}bp")
                rr[size] = {"n": len(v), "mean_bp": round(mean, 3)}
        out["regimes"][regime] = rr
    # 筛查结论:震荡里 s40-80 vs s20-40
    chop = out["regimes"].get("chop", {})
    if "s20-40" in chop and "s40-80" in chop:
        d = chop["s40-80"]["mean_bp"] - chop["s20-40"]["mean_bp"]
        out["conclusion"] = {
            "chop_s40-80_minus_s20-40_bp": round(d, 3),
            "verdict": ("震荡行情放大规模安全(>0)⇒ 复利阶梯可启动,趋势期联动降档"
                        if d > 0 else "震荡行情放大也不安全(<0)⇒ 复利阶梯暂缓"),
        }
        print(f"\n筛查:震荡行情 s40-80 − s20-40 = {d:+.2f}bp ⇒ {out['conclusion']['verdict']}")
    (ROOT / "data" / "compound_ladder_prescreen.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n✓ 已写 data/compound_ladder_prescreen.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
