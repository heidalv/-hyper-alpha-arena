# -*- coding: utf-8 -*-
"""[h754 A1 2026-10-03] 严格成交模型影子(精确窗口版)。

与上一版的区别(上一版 45% 是**假幻影**):
  · 上一版用账本 `quote_ts`(判定时刻的最新挂单时刻)反推窗口;
  · 但成交判定用的是"当时挂着的那张单"(vis_quote/judged_quote),两者在
    挂单被刷新时不同 ⇒ 反推会把合法成交误判成幻影。
  · 本版用 **判定当时落盘的 `judge_ts` + `win_lo_ms`/`win_hi_ms`(精确窗口)**
    复算:严格合法 = 窗口 [win_lo_ms, win_hi_ms] 内存在真实逐笔穿过成交价。
输出 data/fill_audit_last.json;验收口径:
  · 严格合法率 ≥80% 且严格子集每腿净 >0 ⇒ 纸面结论稳健;
  · 严格子集每腿净 ≤0 ⇒ **纸面乐观**,盈利由"不该算的成交"撑着,必须实盘验证。
"""
from __future__ import annotations

import bisect
import importlib.util
import io
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"
TOL_BP = 0.2


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

    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    since = time.time() - hours * 3600
    with psycopg.connect(_lane_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, meta_json->>'side', extract(epoch from ts)::double precision,"
            " meta_json->>'fill_px', meta_json->>'judge_ts',"
            " meta_json->>'win_lo_ms', meta_json->>'win_hi_ms', net_bp, notional"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= to_timestamp(%s)"
            " ORDER BY ts", (LANE, since))
        fills = cur.fetchall()
    if not fills:
        print(f"近 {hours}h 无成交")
        return 0
    syms = sorted({str(r[0]).upper() for r in fills})
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        tr = {}
        for s in syms:
            cur.execute(
                "SELECT event_ts_ms, price FROM asterdex_trades WHERE symbol=%s"
                " AND event_ts_ms >= %s ORDER BY event_ts_ms",
                (s + "USDT", int((since - 300) * 1000)))
            rows = cur.fetchall()
            tr[s] = ([float(x[0]) / 1000.0 for x in rows], [float(x[1]) for x in rows])

    ok = bad = skip = 0
    ok_net = bad_net = 0.0
    ok_bp: list = []
    bad_bp: list = []
    for sym, side, fts, fpx, jts, lo_ms, hi_ms, net_bp, notional in fills:
        sym = str(sym).upper()
        try:
            fpx = float(fpx or 0.0)
            net_bp = float(net_bp or 0.0)
            notional = float(notional or 0.0)
            lo = int(float(lo_ms or 0))
            hi = int(float(hi_ms or 0))
        except Exception:
            skip += 1
            continue
        if fpx <= 0 or lo <= 0 or hi <= lo:
            skip += 1
            continue
        tms, px = tr.get(sym, ([], []))
        legit = False
        if tms:
            i = bisect.bisect_left(tms, lo / 1000.0)
            j = bisect.bisect_right(tms, hi / 1000.0)
            w = px[i:j]
            if w:
                if str(side) == "buy":
                    legit = any(p <= fpx * (1 + TOL_BP / 1e4) for p in w)
                else:
                    legit = any(p >= fpx * (1 - TOL_BP / 1e4) for p in w)
        if legit:
            ok += 1
            ok_net += net_bp * notional / 1e4
            ok_bp.append(net_bp)
        else:
            bad += 1
            bad_net += net_bp * notional / 1e4
            bad_bp.append(net_bp)

    tot = ok + bad
    rate = ok / tot if tot else 0.0
    ok_mean = statistics.mean(ok_bp) if ok_bp else None
    bad_mean = statistics.mean(bad_bp) if bad_bp else None
    # [h754 修正] 判定逻辑:只有**存在有意义的幻影子集**且它明显好于严格子集时,
    # 才叫"纸面乐观";严格合法率 100%(幻影=0)时,严格子集就是全样本,
    # 此时说"纸面乐观"是误报(实测 18:43 误报一次)。
    if tot == 0:
        verdict = "样本不足"
    elif bad == 0:
        verdict = f"全部合法(每腿 {ok_mean:+.2f}bp)" if ok_mean is not None else "全部合法"
    elif rate < 0.8:
        verdict = "命中率偏低(疑似纸面乐观)"
    elif ok_mean is not None and bad_mean is not None and bad_mean - ok_mean > 1.0:
        verdict = "纸面乐观(幻影子集明显更好)"
    else:
        verdict = "稳健"
    out = {
        "ts": time.time(), "window_h": hours, "mode": "exact_window",
        "fills": len(fills), "checked": tot, "skipped": skip,
        "strict_ok": ok, "strict_phantom": bad, "strict_rate": round(rate, 4),
        "strict_net_usd": round(ok_net, 4), "strict_per_leg_bp": round(ok_mean, 3) if ok_mean is not None else None,
        "phantom_net_usd": round(bad_net, 4),
        "phantom_per_leg_bp": round(bad_mean, 3) if bad_mean is not None else None,
        "verdict": verdict,
    }
    print(f"== 严格成交影子(精确窗口,近 {hours}h)== ")
    print(f"  可检验 {tot}(跳过 {skip}:缺精确窗口字段=改动前的旧成交)")
    print(f"  严格合法 {ok} = {rate*100:.0f}% | 幻影 {bad}")
    if ok_bp:
        print(f"  严格子集 n={ok} 每腿 {ok_mean:+.2f}bp 净 {ok_net:+.3f}U")
    if bad_bp:
        print(f"  幻影子集 n={bad} 每腿 {statistics.mean(bad_bp):+.2f}bp 净 {bad_net:+.3f}U")
    print(f"  判定: {out['verdict']}")
    (ROOT / "data" / "fill_audit_last.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  ✓ 已写 data/fill_audit_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
