# -*- coding: utf-8 -*-
"""[h712 2026-10-02] 统一规律研究:每腿净 bp = f(入场时刻的行情状态)?

用户批评:"来回调数值只是对过去的修改,对未来的预判没有帮助。找到这一时段的
统一规律,不断进化和迭代。"

方法(严格样本外验证):
  1. 取 10-02 全部开仓腿(ts/symbol/net_bp/capture/side);
  2. 从原始盘口/逐笔重建每条腿入场时刻的状态特征:
       trend300_bp / trend900_bp(300s/900s 中价漂移)
       ofi60(前 60s 主动买-卖量占比)
       vpin300(前 300s 桶流失衡,15s 桶)
       sigma300(前 300s 5s 收益标准差)
  3. OLS:net_bp ~ trend300 + trend900 + ofi60 + vpin300 + sigma300 + capture
     —— **上午(00:00-11:59)拟合,下午(12:00-17:00)预测**;
  4. 判据:OOS 符号准确率 + 方向一致性 vs 用"全时段平均"当预测的基线。
"""
from __future__ import annotations

import bisect
import importlib.util
import io
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    from datetime import datetime, timezone

    day0 = datetime.fromisoformat("2026-10-02T00:00:00+08:00").timestamp()
    now = time.time()
    # 1) 开仓腿
    with psycopg.connect(_lane_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, extract(epoch from ts)::double precision, net_bp, spread_bp,"
            " meta_json->>'side', meta_json->>'quote_ts'"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
            " AND ts >= '2026-10-02 00:00+08' AND ts < '2026-10-02 17:00+08'"
            " AND COALESCE(meta_json->>'exit_path','') = ''", (LANE,))
        legs = []
        for r in cur.fetchall():
            sym, ts, netbp, cap, side, qts = r
            try:
                qts = float(qts) if qts else ts
            except Exception:
                qts = ts
            legs.append({"sym": str(sym).upper(), "ts": float(ts),
                         "net": float(netbp), "cap": float(cap or 0),
                         "side": str(side or ""), "qts": float(qts)})
    print(f"开仓腿 {len(legs)} 条")
    syms = sorted({l["sym"] for l in legs})
    print(f"涉及币: {syms}")

    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        book: Dict[str, Tuple[List[float], List[float]]] = {}
        for s in syms:
            cur.execute(
                "SELECT event_ts_ms, (bid_px+ask_px)/2 FROM asterdex_book_ticker"
                " WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s"
                " AND bid_px>0 AND ask_px>bid_px ORDER BY event_ts_ms",
                (s + "USDT", int(day0 * 1000), int(now * 1000)))
            rows = cur.fetchall()
            tms = [float(r[0]) / 1000.0 for r in rows]
            mids = [float(r[1]) for r in rows]
            book[s] = (tms, mids)
            print(f"  {s}: {len(rows)} 盘口行")
        trades: Dict[str, List[Tuple[float, float]]] = {}
        for s in syms:
            cur.execute(
                "SELECT event_ts_ms, qty, is_buyer_maker FROM asterdex_trades"
                " WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s"
                " ORDER BY event_ts_ms",
                (s + "USDT", int(day0 * 1000), int(now * 1000)))
            rows = cur.fetchall()
            trades[s] = [(float(r[0]) / 1000.0,
                          float(r[1]) if r[2] else -float(r[1])) for r in rows]
            print(f"  {s}: {len(rows)} 逐笔")

    def _trend(tms: List[float], mids: List[float], t0: float, look: float) -> Optional[float]:
        i = bisect.bisect_right(tms, t0) - 1
        j = bisect.bisect_right(tms, t0 - look) - 1
        if i <= j or i < 0 or j < 0:
            return None
        m0, m1 = mids[i], mids[j]
        if m1 <= 0:
            return None
        return (m0 - m1) / m1 * 1e4

    def _sigma(tms: List[float], mids: List[float], t0: float, look: float) -> Optional[float]:
        i = bisect.bisect_right(tms, t0) - 1
        j = bisect.bisect_right(tms, t0 - look) - 1
        if i - j < 20:
            return None
        rets = [mids[k + 1] - mids[k] for k in range(j, i) if mids[k] > 0]
        if len(rets) < 20:
            return None
        m = sum(rets) / len(rets)
        var = sum((x - m) ** 2 for x in rets) / len(rets)
        return (var ** 0.5) / (mids[i] or 1.0) * 1e4

    def _ofi(tr: List[Tuple[float, float]], t0: float, look: float) -> Optional[float]:
        j = bisect.bisect_right(tr, (t0 - look, 1e18)) - 1
        i = bisect.bisect_right(tr, (t0, 1e18)) - 1
        if i <= j:
            return None
        sgn = sum(x[1] for x in tr[j + 1:i + 1])
        tot = sum(abs(x[1]) for x in tr[j + 1:i + 1])
        return sgn / tot if tot > 0 else None

    def _vpin(tr: List[Tuple[float, float]], t0: float, look: float) -> Optional[float]:
        j = bisect.bisect_right(tr, (t0 - look, 1e18)) - 1
        i = bisect.bisect_right(tr, (t0, 1e18)) - 1
        if i <= j:
            return None
        seg = tr[j + 1:i + 1]
        if not seg:
            return None
        buy = sum(max(0.0, x[1]) for x in seg)
        sell = sum(max(0.0, -x[1]) for x in seg)
        tot = buy + sell
        return abs(buy - sell) / tot if tot > 0 else None

    feats: List[dict] = []
    for l in legs:
        tms, mids = book.get(l["sym"], ([], []))
        tr = trades.get(l["sym"], [])
        if not tms:
            continue
        f = {
            "ts": l["ts"], "sym": l["sym"], "net": l["net"], "cap": l["cap"],
            "side": l["side"],
            "t300": _trend(tms, mids, l["qts"], 300.0),
            "t900": _trend(tms, mids, l["qts"], 900.0),
            "sig": _sigma(tms, mids, l["qts"], 300.0),
            "ofi": _ofi(tr, l["qts"], 60.0),
            "vp": _vpin(tr, l["qts"], 300.0),
        }
        if f["t300"] is None or f["t900"] is None:
            continue
        feats.append(f)
    print(f"\n特征齐全的腿 {len(feats)} 条")

    def _ols(rows: List[dict], keys: List[str]):
        n = len(rows)
        X = [[1.0] + [r[k] or 0.0 for k in keys] for r in rows]
        y = [r["net"] for r in rows]
        # 正规方程求解(小样本够用)
        import statistics
        XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(len(keys) + 1)]
               for a in range(len(keys) + 1)]
        Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(len(keys) + 1)]
        # 高斯消元
        m = len(XtX)
        for col in range(m):
            piv = max(range(col, m), key=lambda r: abs(XtX[r][col]))
            XtX[col], XtX[piv] = XtX[piv], XtX[col]
            Xty[col], Xty[piv] = Xty[piv], Xty[col]
            for r in range(col + 1, m):
                f = XtX[r][col] / XtX[col][col]
                for cc in range(col, m):
                    XtX[r][cc] -= f * XtX[col][cc]
                Xty[r] -= f * Xty[col]
        beta = [0.0] * m
        for r in range(m - 1, -1, -1):
            beta[r] = (Xty[r] - sum(XtX[r][cc] * beta[cc] for cc in range(r + 1, m))) / XtX[r][r]
        return beta

    KEYS = ["t300", "t900", "ofi", "vp", "sig", "cap"]
    am = [r for r in feats if r["ts"] < day0 + 12 * 3600]
    pm = [r for r in feats if r["ts"] >= day0 + 12 * 3600]
    print(f"上午(拟合) {len(am)} 腿 | 下午(预测) {len(pm)} 腿")
    beta = _ols(am, KEYS)
    print("\n== 上午拟合系数(每腿净 bp) ==")
    print("  intercept      %+.3f" % beta[0])
    for k, b in zip(KEYS, beta[1:]):
        print(f"  {k:<14} {b:+.4f}")
    # 下午预测
    def _pred(r, beta, keys):
        return beta[0] + sum(beta[i + 1] * (r[k] or 0.0) for i, k in enumerate(keys))
    pm_pred = [_pred(r, beta, KEYS) for r in pm]
    pm_true = [r["net"] for r in pm]
    # 符号准确率(预测净 bp 与真实净 bp 同号)
    hit = sum(1 for p, t in zip(pm_pred, pm_true) if (p > 0) == (t > 0))
    base = sum(1 for t in pm_true if t > 0)
    print(f"\n== 下午样本外 ==")
    print(f"  符号准确率: {hit}/{len(pm)} = {hit/len(pm)*100:.1f}%")
    print(f"  (全时段平均基线: {max(base, len(pm)-base)}/{len(pm)} = {max(base, len(pm)-base)/len(pm)*100:.1f}%)")
    import statistics
    # 相关性
    mp_ = statistics.fmean(pm_pred)
    mt_ = statistics.fmean(pm_true)
    cov = sum((a - mp_) * (b - mt_) for a, b in zip(pm_pred, pm_true)) / len(pm)
    sp_ = (sum((a - mp_) ** 2 for a in pm_pred) / len(pm)) ** 0.5
    st_ = (sum((b - mt_) ** 2 for b in pm_true) / len(pm)) ** 0.5
    print(f"  预测-真实相关系数: {cov/(sp_*st_ or 1):+.3f}")
    print(f"  若按预测>0.3bp 才做、否则不做:")
    sel = [(p, t) for p, t in zip(pm_pred, pm_true) if p > 0.3]
    if sel:
        print(f"    选取 {len(sel)}/{len(pm)} 腿,平均净 {statistics.fmean(t for _, t in sel):+.2f}bp"
              f" vs 全部 {statistics.fmean(pm_true):+.2f}bp")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
