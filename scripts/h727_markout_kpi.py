# -*- coding: utf-8 -*-
"""[h727 P3 2026-10-02] 成交后逆向/捕获比 KPI(滚动 2h)。

依据 h726 可行性:P3 口径必须用**成交后** markout(成交前逆向实测 ≈0)。
  markout10s(买腿) = (mid[t+10s] − fill_px)/fill_px × 1e4
  比值 = mean(markout)/mean(capture);<0.7 = 健康(h724b 实测 0.41~0.45)。
输出 data/markout_kpi_last.json;每 30 分钟计划任务刷新;/evolution 端点读取。
"""
from __future__ import annotations

import bisect
import importlib.util
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"
HORIZON_SEC = 10.0
WINDOW_H = 2.0


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
    since = now - WINDOW_H * 3600
    with psycopg.connect(_lane_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, meta_json->>'side', extract(epoch from ts)::double precision,"
            " meta_json->>'fill_px', spread_bp"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
            " AND ts >= to_timestamp(%s) AND COALESCE(meta_json->>'exit_path','') = ''",
            (LANE, since))
        legs = []
        for r in cur.fetchall():
            try:
                fpx = float(r[3]) if r[3] else None
            except Exception:
                fpx = None
            if fpx:
                legs.append((str(r[0]).upper(), r[1], float(r[2]), fpx, float(r[4] or 0)))
    print(f"近 {WINDOW_H}h 入场腿 {len(legs)} 条")
    syms = sorted({l[0] for l in legs})
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        book = {}
        for s in syms:
            cur.execute(
                "SELECT event_ts_ms, (bid_px+ask_px)/2 FROM asterdex_book_ticker"
                " WHERE symbol=%s AND event_ts_ms >= %s AND bid_px>0 ORDER BY event_ts_ms",
                (s + "USDT", int((since - 30) * 1000)))
            rows = cur.fetchall()
            book[s] = ([float(x[0]) / 1000.0 for x in rows],
                       [float(x[1]) for x in rows])
    mos, caps = [], []
    for sym, side, fts, fpx, cap in legs:
        tms, mids = book.get(sym, ([], []))
        if not tms:
            continue
        j = bisect.bisect_right(tms, fts + HORIZON_SEC) - 1
        i = bisect.bisect_right(tms, fts) - 1
        if i < 0 or j <= i or mids[i] <= 0:
            continue
        d = 1.0 if side == "buy" else -1.0
        mo = (mids[j] - fpx) / fpx * 1e4 * d
        mos.append(mo)
        caps.append(cap)
    n = len(mos)
    # [h901 用户"空值被说成证据不足?"] 样本再少也**给尽力值**:
    # markout/capture/ratio 用现有样本直接算,verdict 带 "preliminary" 前缀。
    _mean_mo = sum(mos) / n if n else 0.0
    _mean_cap = sum(caps) / n if n else 0.0
    _adverse = max(0.0, -_mean_mo)
    _ratio = _adverse / _mean_cap if _mean_cap > 0 else 0.0
    out = {"ts": now, "window_h": WINDOW_H, "horizon_sec": HORIZON_SEC, "n": n,
           "markout_bp": round(_mean_mo, 3), "capture_bp": round(_mean_cap, 3),
           "ratio": round(_ratio, 3),
           "verdict": ("preliminary_insufficient" if n < 20 else
                       ("healthy(<0.7)" if _ratio < 0.7
                        else "warn(0.7-1.2)" if _ratio <= 1.2
                        else "toxic(>1.2)"))}
    if n >= 20:
        verdict = out["verdict"]
        print(f"  markout {_mean_mo:+.2f}bp / 捕获 {_mean_cap:+.2f}bp"
              f" ⇒ 逆向/捕获 = {_ratio:.2f}({verdict})")
    else:
        print(f"  样本 n={n} ⇒ 尽力值 markout={_mean_mo:+.2f}bp(置信不足)")
    (ROOT / "data" / "markout_kpi_last.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✓ 已写 data/markout_kpi_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
