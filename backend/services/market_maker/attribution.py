# -*- coding: utf-8 -*-
"""[h651 2026-09-30] mm 车道共享归因模块:方向卡/判决脚本/宇宙评分的唯一事实源。

《方向判定_因子还是LLM_判定与设计_20260930》§9.1:
  · quote-time(挂单时刻)归因 = 三层共同口径,不再"各算各的";
  · 往返重建用**移动库存法**(h324 同口径)——position_id 每次 worker 重启
    被重置复用(mm:BTC:1 在 12h 覆盖 395 条腿,实测),不可作往返键;
  · 趋势回填用挂单时刻(h402 落盘)±45s 内 book ticker 中价,300s 趋势 bp。

消费者:
  · direction_card.py(方向卡取数 + markout 聚合)
  · scripts/d1_verdict.py(D1/各闸判决)
  · scripts/h329_selector_v5.py(宇宙评分与衰减检测,后续接入)
"""
from __future__ import annotations

import bisect
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[3]

LOOKBACK_SEC = 300.0     # trend_lookback=20 × 15s
BUCKET_15S = 15.0
TREND_TOL_SEC = 45.0

_VS_SUFFIX = {"BNB": "BNBUSDT", "NEAR": "NEARUSDT", "ARB": "ARBUSDT",
              "XRP": "XRPUSDT", "ENA": "ENAUSDT"}


def vs_symbol(sym: str) -> str:
    s = str(sym or "").upper()
    return _VS_SUFFIX.get(s, s + "USDT")


def _main_dsn() -> str:
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
    h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(h)  # type: ignore[union-attr]
    return h.read_env_dsn()


def _market_dsn() -> str:
    url = _main_dsn()
    head, _, _ = url.rpartition("/")
    return head + "/alpha_market"


# ── 取数 ──────────────────────────────────────────────────────

def fetch_legs(since_epoch: float, lane_id: str = "mm_asterdex",
               buffer_sec: float = 7200.0) -> List[Dict[str, Any]]:
    """取 since_epoch − buffer 起的全部 fill(qty 用于库存走账)。"""
    import psycopg
    from datetime import datetime, timezone
    since = datetime.fromtimestamp(since_epoch - float(buffer_sec), tz=timezone.utc)
    with psycopg.connect(_main_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, notional, net_bp, ts,"
            " COALESCE(meta_json->>'side','') AS side,"
            " COALESCE((meta_json->>'qty')::float8,0) AS qty,"
            " COALESCE((meta_json->>'quote_ts')::float8,0) AS quote_ts"
            " FROM lane_ledger"
            " WHERE lane_id=%s AND event='fill' AND ts >= %s"
            " ORDER BY ts", (lane_id, since))
        rows = cur.fetchall()
    return [
        {
            "symbol": str(r[0] or ""),
            "notional": float(r[1] or 0.0), "net_bp": float(r[2] or 0.0),
            "ts_epoch": float(r[3].timestamp()), "side": str(r[4] or ""),
            "qty": float(r[5] or 0.0), "quote_ts": float(r[6] or 0.0),
        }
        for r in rows
    ]


def fetch_open_legs(*, lane_id: str, minutes: float = 60.0) -> List[Dict[str, object]]:
    """最近 N 分钟的开仓腿(未带出口路径的 fill),用于方向卡。

    判据 = `COALESCE(meta_json->>'exit_path','') = ''`(F340/h402 之后每条 fill
    都带 exit_path,空串 = 加仓/普通配对腿)。失败抛错(调用方保留上一张卡)。
    """
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(text(
                "SELECT symbol,"
                " COALESCE(meta_json->>'side','') AS side,"
                " notional,"
                " COALESCE((meta_json->>'mid_px')::float8, 0.0) AS mid_px,"
                " COALESCE((meta_json->>'quote_ts')::float8, 0.0) AS quote_ts"
                " FROM lane_ledger"
                " WHERE lane_id = :lane AND event = 'fill'"
                " AND ts > now() - make_interval(secs => :secs)"
                " AND COALESCE(meta_json->>'exit_path','') = ''"
            ), {"lane": lane_id, "secs": float(minutes) * 60.0}).mappings().all()
    return [
        {
            "symbol": str(r["symbol"] or ""),
            "side": str(r["side"] or ""),
            "notional": float(r["notional"] or 0.0),
            "mid_px": float(r["mid_px"] or 0.0),
            "quote_ts": float(r["quote_ts"] or 0.0),
        }
        for r in rows
    ]


def fetch_mid_series(sym: str, t0: float, t1: float) -> Tuple[List[float], List[float]]:
    """book ticker 中价,15s 降采样,升序。"""
    import psycopg
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker"
            " WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s"
            " ORDER BY event_ts_ms",
            (vs_symbol(sym), int(t0 * 1000), int(t1 * 1000)))
        rows = cur.fetchall()
    t_series: List[float] = []
    m_series: List[float] = []
    last_bucket = -1
    for ms, b, a in rows:
        if not b or not a or float(a) <= float(b) or float(b) <= 0:
            continue
        t = float(ms) / 1000.0
        bucket = int(t // BUCKET_15S)
        mid = (float(b) + float(a)) / 2.0
        if bucket == last_bucket and m_series:
            m_series[-1] = mid
            t_series[-1] = t
        else:
            t_series.append(t)
            m_series.append(mid)
            last_bucket = bucket
    return t_series, m_series


# ── 纯函数 ────────────────────────────────────────────────────

def pair_roundtrips_chrono(legs: Sequence[Mapping[str, Any]],
                           since_epoch: float) -> List[Dict[str, Any]]:
    """按时序 + 库存符号把腿重建为往返(移动库存法,h324 同口径)。

    调用方须传入**早于 since_epoch 的缓冲腿**:缓冲段只走库存账,保证窗口起点
    inv 准确;只有开仓腿落在 [since_epoch, +∞) 的往返才产出。
    每条往返: {symbol, open_ts, quote_ts, side, n_legs, notional, net_bp(名义加权)}。
    """
    by_sym: Dict[str, List[Mapping[str, Any]]] = {}
    for lg in legs:
        sym = str(lg.get("symbol") or "").strip().upper()
        side = str(lg.get("side") or "").lower()
        if side in ("long", "b"):
            side = "buy"
        elif side in ("short", "s"):
            side = "sell"
        if not sym or side not in ("buy", "sell"):
            continue
        by_sym.setdefault(sym, []).append(dict(lg, _side=side))

    out: List[Dict[str, Any]] = []
    for sym, ls in by_sym.items():
        ls.sort(key=lambda x: float(x.get("ts_epoch") or 0.0))
        inv = 0.0
        cur: Optional[Dict[str, Any]] = None
        for lg in ls:
            qty = float(lg.get("qty") or 0.0)
            if qty <= 0:
                continue
            dq = qty if lg["_side"] == "buy" else -qty
            in_window = float(lg.get("ts_epoch") or 0.0) >= since_epoch
            if cur is None:
                cur = {
                    "symbol": sym, "open_ts": float(lg.get("ts_epoch") or 0.0),
                    "quote_ts": float(lg.get("quote_ts") or 0.0),
                    "side": lg["_side"], "legs": [lg],
                    "open_in_window": in_window,
                }
                inv += dq
                if inv == 0:
                    cur = None
                continue
            cur["legs"].append(lg)
            prev_inv = inv
            inv += dq
            if (prev_inv > 0 and inv < 0) or (prev_inv < 0 and inv > 0):
                if cur.get("open_in_window"):
                    out.append(_finalize(cur))
                cur = {
                    "symbol": sym, "open_ts": float(lg.get("ts_epoch") or 0.0),
                    "quote_ts": float(lg.get("quote_ts") or 0.0),
                    "side": lg["_side"], "legs": [lg],
                    "open_in_window": in_window,
                }
            elif inv == 0:
                if cur.get("open_in_window"):
                    out.append(_finalize(cur))
                cur = None
    return out


def _finalize(cur: Dict[str, Any]) -> Dict[str, Any]:
    tot_n = 0.0
    tot_w = 0.0
    for lg in cur["legs"]:
        n = float(lg.get("notional") or 0.0)
        if n <= 0:
            continue
        tot_n += n
        tot_w += float(lg.get("net_bp") or 0.0) * n
    return {
        "symbol": cur["symbol"], "open_ts": cur["open_ts"],
        "quote_ts": cur["quote_ts"], "side": cur["side"],
        "n_legs": len(cur["legs"]), "notional": tot_n,
        "net_bp": (tot_w / tot_n) if tot_n > 0 else 0.0,
    }


def mid_at(series_t: Sequence[float], series_mid: Sequence[float],
           t: float, tol: float = TREND_TOL_SEC) -> Optional[float]:
    """二分取 t 前后 tol 秒内最近的中价;没有则 None(数据缺口如实返回)。"""
    if not series_t or t <= 0:
        return None
    i = bisect.bisect_left(series_t, t)
    cand: Optional[float] = None
    best_gap = float("inf")
    for j in (i - 1, i):
        if 0 <= j < len(series_t):
            gap = abs(float(series_t[j]) - t)
            if gap <= tol and gap < best_gap:
                best_gap = gap
                cand = float(series_mid[j])
    return cand


def trend_bp_at(series_t: Sequence[float], series_mid: Sequence[float],
                quote_ts: float) -> Optional[float]:
    """挂单时刻的 300s 趋势(bp):(mid_t − mid_{t−300})/mid_{t−300}×1e4。"""
    if quote_ts <= 0:
        return None
    m0 = mid_at(series_t, series_mid, quote_ts)
    m1 = mid_at(series_t, series_mid, quote_ts - LOOKBACK_SEC)
    if not m0 or not m1 or m1 <= 0:
        return None
    return (m0 - m1) / m1 * 1e4


def bucket_side(side: str, trend_bp: Optional[float]) -> str:
    """顺势(with)/逆势(against)/未知(unknown)。涨势买、跌势卖 = 顺势。"""
    if trend_bp is None:
        return "unknown"
    if side == "buy":
        return "with" if trend_bp > 0 else "against"
    return "with" if trend_bp < 0 else "against"


def direction_rows_from_legs(
    legs: Iterable[Mapping[str, object]],
    mids: Mapping[str, float],
) -> List[Dict[str, object]]:
    """开仓腿 → 逐币逐边 (n, price_bp)。

    price_bp = 名义加权的「成交后至今 markout」:买腿 = (mid_now − mid_fill)/mid_fill×1e4,
    卖腿取负。正 = 顺向,负 = 这半边正被逆向选择。没有当前中价的币跳过。
    """
    agg: Dict[str, Dict[str, List[float]]] = {}
    for lg in legs:
        sym = str(lg.get("symbol") or "").upper()
        side = str(lg.get("side") or "").lower()
        if side in ("long", "b"):
            side = "buy"
        elif side in ("short", "s"):
            side = "sell"
        if not sym or side not in ("buy", "sell"):
            continue
        mid_now = float(mids.get(sym) or 0.0)
        mid_fill = float(lg.get("mid_px") or 0.0)
        notional = float(lg.get("notional") or 0.0)
        if mid_now <= 0 or mid_fill <= 0 or notional <= 0:
            continue
        sgn = 1.0 if side == "buy" else -1.0
        markout_bp = sgn * (mid_now - mid_fill) / mid_fill * 1e4
        bucket = agg.setdefault(sym, {}).setdefault(side, [0.0, 0.0, 0.0])
        bucket[0] += 1.0
        bucket[1] += notional
        bucket[2] += markout_bp * notional
    rows: List[Dict[str, object]] = []
    for sym in sorted(agg):
        for side in ("buy", "sell"):
            # [h665e 修复] 单边无样本时 agg[sym][side] 会 KeyError('sell'/'buy')
            # (新币/清淡币常态),方向卡整张被炸 ⇒ 前端回退"已停"。
            bucket = agg[sym].get(side)
            if not bucket:
                continue
            n, notional, wsum = bucket
            if notional <= 0:
                continue
            rows.append({
                "symbol": sym, "side": side,
                "n": int(n), "price_bp": wsum / notional,
            })
    return rows


def adaptive_min_n(per_side_rate_per_h: float, *, lo: int = 12, hi: int = 30) -> int:
    """[§9.3 候选] 方向卡 min_n 随该币该边腿频自适应(规则化,未启用)。

    min_n = clamp(round(0.25 × 腿频/小时), 12, 30):腿频高的币要更多样本才封边
    (减少滞后打脸),腿频低的币尽早动作。0.25 系数与上下界均为预注册值,
    上线须走独立单变量试跑(direction_min_n_adaptive)。
    """
    rate = float(per_side_rate_per_h or 0.0)
    return max(int(lo), min(int(hi), round(0.25 * rate)))


def welch_two_sided(a: Sequence[float], b: Sequence[float]) -> Dict[str, Any]:
    """H372 修复口径:双侧 Welch t 检验(scipy)。n<2 ⇒ inconclusive。"""
    from scipy import stats
    if len(a) < 2 or len(b) < 2:
        return {"t": None, "p": None, "note": "样本不足"}
    r = stats.ttest_ind(a, b, equal_var=False)
    return {"t": float(r.statistic), "p": float(r.pvalue),
            "note": "" if r.pvalue == r.pvalue else "nan"}
