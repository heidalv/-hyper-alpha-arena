# -*- coding: utf-8 -*-
"""[h892] 三层影子环境的数据装载:把库里真实数据喂给统一内核。

  · HFT:asterdex_trades + asterdex_book_ticker(0.5~1s 特征回放)
  · MID:crypto_klines(1m → 15min 聚合)+ liquidation_ticks(清算反转信号)
  · LONG:crypto_klines(日线)+ perp_funding(资金费)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
import psycopg  # noqa: E402


def _conn():
    return psycopg.connect(_market_dsn(), autocommit=True)


# ══════════════════════════════════════════════════════════════════════
# HFT:真实特征回放(逐笔 + 一档盘口)
# ══════════════════════════════════════════════════════════════════════
def load_hft_features(symbols: List[str], hours: float = 3.0,
                      step_s: float = 1.0) -> Dict[str, Dict[str, np.ndarray]]:
    """每个币返回 {ts, obs(15 维), 对齐的往返 y(bp)} 三组数组。

    obs 维序与 HftShadowEnv.OBS_KEYS 一致。
    y 从 flow_roundtrip_log 按 (symbol, 就近 ts) 对齐 —— 即"历史规则集在此状态的实得"。
    """
    import json as _json
    import time as _time

    rt_path = ROOT / "data" / "flow_roundtrip_log.jsonl"
    rt_rows = []
    if rt_path.exists():
        try:
            rt_rows = [_json.loads(x) for x in
                       rt_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        except Exception:
            rt_rows = []
    rt_by_sym: Dict[str, List[dict]] = {}
    for r in rt_rows:
        if r.get("y_bp") is None:
            continue
        rt_by_sym.setdefault(str(r.get("symbol") or "").upper(), []).append(r)

    t0 = _time.time() - hours * 3600
    out: Dict[str, Dict[str, np.ndarray]] = {}
    with _conn() as c, c.cursor() as cur:
        for sym in symbols:
            cur.execute(
                "SELECT event_ts_ms/1000.0, price, qty, is_buyer_maker"
                " FROM asterdex_trades WHERE symbol=%s AND event_ts_ms>%s"
                " ORDER BY event_ts_ms", (sym, int(t0 * 1000)))
            tr = cur.fetchall()
            cur.execute(
                "SELECT event_ts_ms/1000.0, bid_px, ask_px, bid_qty, ask_qty"
                " FROM asterdex_book_ticker WHERE symbol=%s AND bid_px>0"
                " AND event_ts_ms>%s ORDER BY event_ts_ms",
                (sym, int(t0 * 1000)))
            bk = cur.fetchall()
            if len(tr) < 60 or len(bk) < 60:
                continue
            tts = np.array([float(r[0]) for r in tr])
            px = np.array([float(r[1]) for r in tr])
            qty = np.array([float(r[2]) for r in tr])
            is_buy = np.array([not r[3] for r in tr])
            bts = np.array([float(r[0]) for r in bk])
            bid = np.array([float(r[1]) for r in bk])
            ask = np.array([float(r[2]) for r in bk])
            bq = np.array([float(r[3]) + float(r[4]) for r in bk])
            ts_grid = np.arange(max(tts[0], bts[0]) + 120,
                                min(tts[-1], bts[-1]) - 30, step_s)
            obs_list: List[np.ndarray] = []
            y_list: List[float] = []
            for t in ts_grid:
                m = (tts > t - 5) & (tts <= t)
                if not m.any():
                    continue
                i = int(np.searchsorted(bts, t, side="right") - 1)
                if i < 0:
                    continue
                mid = (bid[i] + ask[i]) / 2.0
                vol = float(qty[m].sum()) + 1e-12
                ofi5 = float((qty[m & is_buy].sum() - qty[m & (~is_buy)].sum()) / vol)
                m60 = (tts > t - 60) & (tts <= t)
                vol60 = float(qty[m60].sum()) + 1e-12
                ofi60 = float((qty[m60 & is_buy].sum()
                               - qty[m60 & (~is_buy)].sum()) / vol60)
                m300 = (tts > t - 300) & (tts <= t)
                j = int(np.searchsorted(bts, t - 300, side="right") - 1)
                trend300 = float((mid / (bid[j] + ask[j]) * 2.0 - 1.0) * 1e4) if j >= 0 else 0.0
                j60 = int(np.searchsorted(bts, t - 60, side="right") - 1)
                trend60 = float((mid / (bid[j60] + ask[j60]) * 2.0 - 1.0) * 1e4) if j60 >= 0 else 0.0
                spread = float((ask[i] - bid[i]) / mid * 1e4)
                vol300 = float(np.std(px[m300]) / mid * 1e4) if m300.sum() >= 5 else 0.0
                obs = np.array([
                    (float(px[m][-1]) - mid) / mid * 1e4,   # mp_skew
                    ofi5, ofi60, trend60, trend300,
                    float(bid[i] * 0) + float(bq[i]),        # depth_bid(近似=总盘口量)
                    float(ask[i] * 0) + float(bq[i]),        # depth_ask
                    0.0, spread, vol300, 0.0, 0.0, 0.0, 0.0, 0.0,
                ], dtype=np.float32)
                obs_list.append(obs)
                # 对齐历史往返 y:该币、5 分钟内最近的一条
                # (往返日志的 symbol 无 USDT 后缀,如 BTC;trades 表带后缀 ⇒ 剥掉)
                _key = sym.upper()
                _key = _key.replace("USDT", "").replace("USD1", "").replace("USD", "")
                best = None
                for r in rt_by_sym.get(_key, []):
                    d = abs(float(r.get("ts") or 0.0) - t)
                    if d < 300 and (best is None or d < best[0]):
                        best = (d, float(r["y_bp"]))
                y_list.append(best[1] if best else 0.0)
            if len(obs_list) < 50:
                continue
            out[sym] = {
                "ts": ts_grid[:len(obs_list)],
                "obs": np.asarray(obs_list, dtype=np.float32),
                "y": np.asarray(y_list, dtype=np.float32),
            }
    return out


# ══════════════════════════════════════════════════════════════════════
# MID:1m K 线 → 15min 聚合 + 清算读数
# ══════════════════════════════════════════════════════════════════════
def load_mid_bars(symbols: List[str], days: float = 2.0,
                  bar_min: int = 15) -> Dict[str, Dict[str, np.ndarray]]:
    """每币返回 {ts, o, h, l, c, v, buy_v, liq_n, liq_notional} 15min 聚合。"""
    import time as _time
    t0 = _time.time() - days * 86400
    out: Dict[str, Dict[str, np.ndarray]] = {}
    with _conn() as c, c.cursor() as cur:
        for sym in symbols:
            syms = [f"{sym}USDT", f"{sym}/USDT", f"{sym}-USDT", sym]
            qs = "','".join(syms)
            cur.execute(
                f"SELECT timestamp, open_price, high_price, low_price, close_price,"
                f" volume, amount, period FROM crypto_klines"
                f" WHERE symbol IN ('{qs}') AND period='1m' AND timestamp>%s"
                f" ORDER BY timestamp", (t0,))
            rows = cur.fetchall()
            if len(rows) < 120:
                continue
            ts = np.array([float(r[0]) for r in rows])
            o = np.array([float(r[1]) for r in rows])
            h = np.array([float(r[2]) for r in rows])
            l = np.array([float(r[3]) for r in rows])
            cl = np.array([float(r[4]) for r in rows])
            v = np.array([float(r[5]) for r in rows])
            amt = np.array([float(r[6] or 0) for r in rows])
            step = bar_min
            n = len(rows) // step
            out[sym] = {
                "ts": ts[::step][:n],
                "o": o[::step][:n],
                "h": np.max(h[:n * step].reshape(n, step), axis=1),
                "l": np.min(l[:n * step].reshape(n, step), axis=1),
                "c": cl[(np.arange(n) + 1) * step - 1],
                "v": np.sum(v[:n * step].reshape(n, step), axis=1),
                "amt": np.sum(amt[:n * step].reshape(n, step), axis=1),
            }
    return out


def load_funding(symbols: List[str], hours: float = 48.0) -> Dict[str, np.ndarray]:
    import time as _time
    t0 = _time.time() - hours * 3600
    out: Dict[str, np.ndarray] = {}
    with _conn() as c, c.cursor() as cur:
        for sym in symbols:
            cur.execute(
                "SELECT timestamp, funding_rate FROM perp_funding"
                " WHERE symbol=%s AND timestamp>%s ORDER BY timestamp",
                (sym, t0))
            rows = cur.fetchall()
            if len(rows) < 4:
                continue
            out[sym] = np.array([(float(r[0]), float(r[1])) for r in rows])
    return out


def load_liquidations(symbols: List[str], hours: float = 48.0) -> Dict[str, np.ndarray]:
    import time as _time
    t0 = (_time.time() - hours * 3600) * 1000
    out: Dict[str, np.ndarray] = {}
    with _conn() as c, c.cursor() as cur:
        for sym in symbols:
            cur.execute(
                "SELECT ts_ms/1000.0, side, notional_usd FROM liquidation_ticks"
                " WHERE symbol=%s AND ts_ms>%s ORDER BY ts_ms", (sym, int(t0)))
            rows = cur.fetchall()
            if len(rows) < 4:
                continue
            out[sym] = np.array([
                (float(r[0]),
                 float(r[2]) if str(r[1]).lower() in ("long", "buy") else -float(r[2]))
                for r in rows])
    return out
