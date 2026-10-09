# -*- coding: utf-8 -*-
"""选币考卷：新单排在已挂数量后面，买回也排队。过关才写入可开仓。

过关要同时满足：
  完整来回至少 30 笔；
  买回后平均超过 1 个基点；
  最典型的一笔也是赚的（避免少数大赚把平均拉正）；
  12 小时拆成前、中、后三段，三段平均都是赚的。
做多、做空分开考。都不过关就关着，位子可以空。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
import psycopg  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "h829_queue_split", ROOT / "scripts" / "h829_queue_split.py")
h829 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h829)

MIN_N = 30
MIN_MEAN_BP = 1.0


def bare(symbol: str) -> str:
    name = str(symbol or "").upper()
    if name.endswith("USDT"):
        return name[:-4]
    return name


def listed(conn, lo_ms):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT b.symbol FROM ("
            "  SELECT symbol, count(*) AS c FROM asterdex_book_ticker"
            "  WHERE event_ts_ms>%s GROUP BY symbol) b "
            "JOIN ("
            "  SELECT symbol, count(*) AS c FROM asterdex_trades"
            "  WHERE event_ts_ms>%s GROUP BY symbol) t "
            "ON b.symbol=t.symbol "
            "WHERE b.c>=500 AND t.c>=200 ORDER BY b.c DESC",
            (lo_ms, lo_ms),
        )
        return [r[0] for r in cur.fetchall()]


def _means(rows):
    ys = np.array([r[2] for r in rows], dtype=float)
    ys = ys[np.isfinite(ys)]
    if len(ys) == 0:
        return None, None, None
    return float(ys.mean()), float(np.median(ys)), float((ys > 0).mean())


def _folds(rows):
    if len(rows) < 3:
        return []
    times = np.array([r[0] for r in rows])
    c1, c2 = np.quantile(times, [0.33, 0.66])
    parts = [
        [r for r in rows if r[0] <= c1],
        [r for r in rows if c1 < r[0] <= c2],
        [r for r in rows if r[0] > c2],
    ]
    out = []
    for part in parts:
        mean, _, _ = _means(part)
        out.append(mean)
    return out


def grade(rows):
    mean, med, win = _means(rows)
    folds = _folds(rows)
    n = 0 if mean is None else int(len(rows))
    recent = folds[-1] if folds else None
    passed = (
        n >= MIN_N
        and mean is not None
        and mean > MIN_MEAN_BP
        and med is not None
        and med > 0.0
        and len(folds) == 3
        and all(m is not None and m > 0.0 for m in folds)
    )
    if n == 0:
        reason = "no_fill"
    elif n < MIN_N:
        reason = "too_few_roundtrips"
    elif med is None or med <= 0.0:
        reason = "median_not_positive"
    elif mean is None or mean <= MIN_MEAN_BP:
        reason = "mean_below_margin"
    elif not passed:
        reason = "fold_not_all_positive"
    else:
        reason = "queue_roundtrip_positive"
    return {
        "n": n,
        "mean": mean,
        "median": med,
        "win": win,
        "folds": folds,
        "recent": recent,
        "passed": passed,
        "reason": reason,
    }


def choose(sell, buy):
    """过关的那一边优先。都不过就留下成交更多的一边，当作没过的证据。"""
    winners = [g for g in (sell, buy) if g["passed"]]
    if winners:
        return max(winners, key=lambda g: g["mean"])
    scored = [g for g in (sell, buy) if g["n"] > 0]
    if not scored:
        return sell
    return max(scored, key=lambda g: g["n"])


def gate_entry(side_name, grade_row):
    mean = grade_row["mean"]
    entry = {
        "allow": bool(grade_row["passed"]),
        "side": side_name if grade_row["passed"] else None,
        "mu": None if mean is None else round(float(mean), 4),
        "max_hold_sec": 1.0,
        "oos": {
            "mean_y": None if mean is None else round(float(mean), 4),
            "n_eff": float(grade_row["n"]),
            "win_rate": None if grade_row["win"] is None else round(float(grade_row["win"]), 4),
        },
        "reason": grade_row["reason"],
    }
    if grade_row["recent"] is not None:
        entry["recent_mean_y"] = round(float(grade_row["recent"]), 4)
    if not grade_row["passed"] and mean is not None and mean > 0.0:
        # 平均被尾巴拉正、但考卷没过：不把这个正数交给选币。
        entry["oos"]["mean_y"] = 0.0
        entry["mu"] = 0.0
    return entry


def main():
    lo = int((time.time() - 12 * 3600) * 1000)
    detail = {}
    gates = {}
    with psycopg.connect(_market_dsn(), autocommit=True) as conn:
        names = listed(conn, lo)
        print(f"有盘口也有成交的合约 {len(names)} 个", flush=True)
        for name in names:
            pack = h829.load(conn, name, lo)
            if pack is None or len(pack[0]) < 500 or len(pack[5]) < 200:
                print(f"  {bare(name):<12} 数据不足", flush=True)
                continue
            sides = {}
            for side in ("sell", "buy"):
                _attempts, rows = h829.simulate(pack, side)
                sides[side] = grade(rows)
            key = bare(name)
            chosen = choose(
                {**sides["sell"], "side": "sell"},
                {**sides["buy"], "side": "buy"},
            )
            detail[key] = {
                "sell": {k: sides["sell"][k] for k in ("n", "mean", "median", "win", "folds", "passed", "reason")},
                "buy": {k: sides["buy"][k] for k in ("n", "mean", "median", "win", "folds", "passed", "reason")},
            }
            gates[key] = gate_entry(chosen["side"], chosen)
            mark = "过关" if gates[key]["allow"] else "不过"
            sm, bm = sides["sell"]["mean"], sides["buy"]["mean"]
            def fmt(v):
                return "  n/a" if v is None else f"{v:+6.2f}"
            print(f"  {key:<12} {mark}  做空 n={sides['sell']['n']:4d} {fmt(sm)}  "
                  f"做多 n={sides['buy']['n']:4d} {fmt(bm)}  {gates[key]['reason']}",
                  flush=True)
    doc = {
        "ts": time.time(),
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hours": 12,
        "protocol": "queue_cleared_exam",
        "gates": gates,
    }
    (ROOT / "data" / "flow_gate_last.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "data" / "flow_queue_exam_last.json").write_text(
        json.dumps({"ts": doc["ts"], "as_of": doc["as_of"], "coins": detail},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    opened = [k for k, v in gates.items() if v.get("allow")]
    print(f"考完 {len(gates)} 个，过关 {len(opened)} 个：{', '.join(opened) or '无'}", flush=True)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
