# -*- coding: utf-8 -*-
"""[h713 2026-10-02] 机械层自进化:挂宽-捕获-成交率曲面滚动拟合。

依据 L12(预测不可为/结构可为):钱在机械层,不在行情预测。本脚本对每个币:
  1. 从盘口重建"触及概率曲线":挂宽 w 的报价,在 60s 内被 mid 触及的概率;
  2. 从账本(滚动 6h)实测"捕获 → 每腿净"的档位关系;
  3. 期望净收益/小时 ≈ 触及率(w) × 净(捕获(w)) —— 找乘积最大的 w;
  4. 对照当前挂宽,输出建议(仅报告,不落库;--apply 才写,走 Python API 避免
     dict 逗号分裂事故)。
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
WINDOW_H = 6.0
WIDTHS_BP = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
FILL_HORIZON_SEC = 60.0


def _market_dsn() -> str:
    from backend.services.market_maker.attribution import _market_dsn as f
    return f()


def _lane_dsn() -> str:
    _spec = importlib.util.spec_from_file_location("h425", ROOT / "scripts" / "h425_repair_trial.py")
    _h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    return _h.read_env_dsn()


def _touch_curve(tms: np.ndarray, mids: np.ndarray) -> Dict[str, np.ndarray]:
    """60s 前向窗口内的触及概率曲线(基于 2s 网格 mid)。"""
    p = np.array([1.0 + w / 1e4 for w in WIDTHS_BP])
    m = np.array([1.0 - w / 1e4 for w in WIDTHS_BP])
    n = len(tms)
    win = int(FILL_HORIZON_SEC / 2.0)
    touch = np.zeros(len(WIDTHS_BP))
    cnt = 0
    for i in range(0, n - win, 1):
        seg = mids[i:i + win + 1]
        m0 = mids[i]
        if m0 <= 0:
            continue
        hi = seg.max() >= m0 * p
        lo = seg.min() <= m0 * m
        touch += (hi | lo).astype(float)
        cnt += 1
    return {"probs": touch / max(1, cnt), "starts": cnt}


def main() -> int:
    import psycopg

    from backend.services import lane_registry as reg

    meta = (reg.get_lane(LANE) or {}).get("meta") or {}
    params = dict(meta.get("params") or {})
    symbols = list(meta.get("symbols") or [])
    print(f"宇宙 {symbols}")
    print(f"当前: spread_mult={params.get('spread_mult')} per_symbol={params.get('per_symbol_spread_mult')} "
          f"min_edge_frac={params.get('min_edge_frac')} max_width_bp={params.get('max_width_bp')}")

    now = time.time()
    win0 = now - WINDOW_H * 3600
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        book: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        hs: Dict[str, float] = {}
        for s in symbols:
            cur.execute(
                "SELECT event_ts_ms, (bid_px+ask_px)/2, (ask_px-bid_px)/((ask_px+bid_px)/2)*1e4"
                " FROM asterdex_book_ticker"
                " WHERE symbol=%s AND event_ts_ms >= %s AND bid_px>0 AND ask_px>bid_px"
                " ORDER BY event_ts_ms",
                (s + "USDT", int(win0 * 1000)))
            rows = cur.fetchall()
            if len(rows) < 1000:
                print(f"  {s}: 数据不足({len(rows)}),跳过")
                continue
            t = np.array([float(r[0]) / 1000.0 for r in rows])
            m = np.array([float(r[1]) for r in rows])
            hs[s] = float(np.median([float(r[2]) for r in rows[::7]]))
            # 2s 网格化(取桶内最后值);x 轴直接用 t[idx],不另建 grid,并 clip 兜底
            idx = np.searchsorted(t, np.arange(t[0], t[-1], 2.0), side="right") - 1
            idx = np.clip(idx, 0, len(t) - 1)
            book[s] = (t[idx], m[idx])
            print(f"  {s}: {len(rows)} 盘口行 → 2s 网格 {len(idx)} 点 | 半价差中位 {hs[s]:.2f}bp")

    with psycopg.connect(_lane_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, spread_bp, net_bp FROM lane_ledger"
            " WHERE lane_id=%s AND event='fill' AND ts >= %s"
            " AND COALESCE(meta_json->>'exit_path','') = ''",
            (LANE, time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(win0))))
        legs = [(str(r[0]).upper(), float(r[1]), float(r[2])) for r in cur.fetchall()]
    print(f"\n滚动 {WINDOW_H}h 入场腿 {len(legs)} 条")

    # 捕获 → 每腿净(实测分档,自校准)
    def band(cap: float) -> str:
        if cap < 0.5:
            return "a"
        if cap < 1.0:
            return "b"
        if cap < 2.0:
            return "c"
        return "d"

    net_band: Dict[str, float] = {}
    cnt_band: Dict[str, int] = {}
    for _s, cap, net in legs:
        b = band(cap)
        net_band[b] = net_band.get(b, 0.0) + net
        cnt_band[b] = cnt_band.get(b, 0) + 1
    for b in list(net_band):
        net_band[b] /= cnt_band[b]
    print(f"  捕获档净收益(每腿bp): " + "  ".join(
        f"{b}:{net_band[b]:+.1f}(n={cnt_band[b]})" for b in sorted(net_band)))

    # 实测"捕获/挂宽"比:捕获 ≈ ratio × 半宽。半宽 = max_width_bp/2
    # (compute_quote 里 max_width_bp 是**双边全宽**上限,单侧 ≤ cap/2;实测
    # 挂宽 1.65-1.77 = cap 3.0 的单侧 ✓)。此前误把全宽当半宽,ratio 被低估。
    caps = [cap for _s, cap, _n in legs]
    mean_cap = float(np.mean(caps)) if caps else 0.0
    cur_w = float(params.get("max_width_bp") or 3.0) / 2.0
    ratio = mean_cap / cur_w if cur_w > 0 else 0.46
    ratio = max(0.3, min(0.8, ratio))
    print(f"  当前实测捕获均值 {mean_cap:+.2f}bp | 半宽={cur_w:.1f} | 折算比 capture/半宽 = {ratio:.2f}")

    # 逐币:触及曲线 × 净收益曲线 → 最优 w
    print("\n== 逐币最优挂宽(期望净/小时 相对值)== ")
    out: List[dict] = []
    for s, (t, m) in book.items():
        r = _touch_curve(t, m)
        probs = r["probs"]
        # 捕获(w) = ratio × w(实测比,不再写死 0.75)
        cap_w = np.array([w * ratio for w in WIDTHS_BP])
        exp_net = np.array([probs[i] * net_band.get(band(cap_w[i]),
                                                   net_band.get("b", 0.0))
                            for i in range(len(WIDTHS_BP))])
        if exp_net.max() <= 0:
            print(f"  {s}: 全档期望为负 ⇒ 建议暂时不挂(或最窄档)")
            out.append({"symbol": s, "best_w": None, "exp": 0.0, "probs": probs.tolist()})
            continue
        ib = int(np.argmax(exp_net))
        cur_w = None
        print(f"  {s:<10} 触及率(0.5bp→5bp): "
              + " ".join(f"{p:.2f}" for p in probs)
              + f" | 最优 w={WIDTHS_BP[ib]}bp 期望 {exp_net[ib]:+.3f}")
        out.append({"symbol": s, "best_w": WIDTHS_BP[ib], "exp": float(exp_net[ib]),
                    "probs": probs.tolist()})
    (ROOT / "data" / "surface_fit_last.json").write_text(
        json.dumps({"ts": now, "universe": symbols, "mean_cap": mean_cap,
                    "net_band": net_band, "per_symbol": out},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n✓ 结果已写 data/surface_fit_last.json(仅报告;--apply 未实现,先人工/桥接确认)")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
