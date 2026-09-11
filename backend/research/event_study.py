# -*- coding: utf-8 -*-
"""event_study — 库内全部 market_events 类型的冲击回测（v3 方向 4，p1-event-study）。

对每类事件计算 t−24h … t+72h 相对 BTC 的超额收益路径、命中率、Wilson 95% CI、
最优持有期、按 regime 分层；显著性门：N ≥ 30 且均值超额的 95% 下界 > 往返成本（默认 14bp）。

数据：只读 `market_events` + `crypto_klines`（默认 binance 1h）。不并新闻/巨鲸原表。
全市场事件（symbol IS NULL）或标的=BTC：报告 BTC 自身收益（超额恒为 0，不能当超额门）。

CLI：
  python -m backend.research.event_study --all
  python -m backend.research.event_study --type funding.extreme --min-n 30
  python -m backend.research.event_study --json backend/data/event_study/latest.json
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

COST_THRESHOLD = 0.0014  # 往返 14bp（每边 5bp 费 + 2bp 滑点，与 E1 回测同口径）
DEFAULT_PRE_H = 24
DEFAULT_POST_H = 72
DEFAULT_MIN_N = 30
FIXED_HORIZONS = (1, 4, 8, 24, 48, 72)
Z95 = 1.96
HOUR = 3600

# 事件方向缺省：>0 预期标的跑赢 BTC（或自身上涨）；<0 预期跑输/下跌。0 = 不签名，只报无向均值。
TYPE_DEFAULT_SIGN: Dict[str, int] = {
    "announcement.listing": 0,
    "announcement.futures_listing": 0,
    "announcement.delisting": -1,
    "announcement.monitoring_tag": -1,
    "announcement.airdrop": 0,
    "announcement.launchpool": 0,
    "announcement.maintenance": 0,
    "announcement.collateral": 0,
    "announcement.other": 0,
    "liquidation.large": 0,
    "liquidation.cascade": -1,          # 同向级联后短窗均值回归，方向看 payload；缺省按下跌冲击
    "liquidation.market_cascade": -1,
    "funding.extreme": 0,               # 正负资金费都有，用 event.direction
    "position.oi_jump": 0,
    "news.high_impact": 0,              # 用 event.direction
    "whale.large": 0,
    "macro.scheduled": 0,
    "macro.released": 0,
}

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "event_study"


# --------------------------------------------------------------------------- math (pure)
def wilson_interval(k: int, n: int, z: float = Z95) -> Tuple[float, float]:
    """二项比例 Wilson 95% 区间。n=0 → (0,1)。"""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / den
    half = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / den
    return max(0.0, center - half), min(1.0, center + half)


def mean_se_interval(xs: Sequence[float], z: float = Z95) -> Tuple[float, float, float, float]:
    """返回 (mean, se, lo, hi)。n<2 时 se=0、区间退化为均值。"""
    n = len(xs)
    if n <= 0:
        return 0.0, 0.0, 0.0, 0.0
    m = sum(xs) / n
    if n < 2:
        return m, 0.0, m, m
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    se = math.sqrt(var / n)
    return m, se, m - z * se, m + z * se


def hours_axis(pre_h: int, post_h: int) -> List[int]:
    pre_h, post_h = abs(int(pre_h)), abs(int(post_h))
    return list(range(-pre_h, post_h + 1))


def align_path(
    ts_to_close: Dict[int, float],
    event_ts_s: int,
    hours: Sequence[int],
) -> Optional[List[Optional[float]]]:
    """按小时偏移取收盘价；缺 bar 为 None。event_ts 对齐到小时 floor。"""
    t0 = (int(event_ts_s) // HOUR) * HOUR
    out: List[Optional[float]] = []
    for h in hours:
        px = ts_to_close.get(t0 + int(h) * HOUR)
        out.append(float(px) if px is not None else None)
    return out


def path_returns(prices: Sequence[Optional[float]], *, t0_index: int) -> Optional[List[Optional[float]]]:
    """相对 t=0 的简单收益；p0 缺失则整条作废。"""
    if t0_index < 0 or t0_index >= len(prices):
        return None
    p0 = prices[t0_index]
    if p0 is None or p0 <= 0:
        return None
    out: List[Optional[float]] = []
    for p in prices:
        if p is None or p <= 0:
            out.append(None)
        else:
            out.append(p / p0 - 1.0)
    return out


def signed_excess(
    asset_ret: Optional[float],
    btc_ret: Optional[float],
    sign: int,
    *,
    asset_is_btc: bool,
) -> Optional[float]:
    """sign ∈ {-1,0,+1}。BTC 自身用 raw return；其它用 excess vs BTC。sign=0 不翻转。"""
    if asset_ret is None:
        return None
    raw = asset_ret if asset_is_btc or btc_ret is None else (asset_ret - btc_ret)
    if sign < 0:
        return -raw
    return raw


def event_sign(event_type: str, direction: Optional[float]) -> int:
    if direction is not None:
        try:
            d = float(direction)
        except (TypeError, ValueError):
            d = 0.0
        if d > 0.1:
            return 1
        if d < -0.1:
            return -1
    return int(TYPE_DEFAULT_SIGN.get(event_type, 0))


def optimal_hold(mean_by_h: Dict[int, float], post_h: int) -> Tuple[int, float]:
    """h>0 上均值超额最大的持有期；全空则 (1, 0)。"""
    best_h, best_v = 1, float("-inf")
    for h in range(1, post_h + 1):
        v = mean_by_h.get(h)
        if v is None:
            continue
        if v > best_v:
            best_h, best_v = h, v
    if best_v == float("-inf"):
        return 1, 0.0
    return best_h, best_v


# --------------------------------------------------------------------------- dataclasses
@dataclass
class EventStudyReport:
    event_type: str
    n: int
    n_used: int
    hours: List[int]
    mean_excess: List[Optional[float]]
    hit_rate: float
    wilson_lo: float
    wilson_hi: float
    optimal_hold_h: int
    mean_at_opt: float
    mean_lo_at_opt: float
    mean_hi_at_opt: float
    regime_split: Dict[str, Any] = field(default_factory=dict)
    significant: bool = False
    significant_horizons: List[int] = field(default_factory=list)
    fixed_horizons: Dict[str, Any] = field(default_factory=dict)
    cost_threshold: float = COST_THRESHOLD
    min_n: int = DEFAULT_MIN_N
    exchange: str = "binance"
    timeframe: str = "1h"
    notes: str = ""
    n_events_raw: int = 0
    skipped_no_price: int = 0
    asset_is_btc_share: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# --------------------------------------------------------------------------- DB
def _kline_base(symbol: Optional[str]) -> str:
    from backend.services.analysis.ledgers import _kline_base as _kb

    return _kb(symbol or "BTC") or "BTC"


def load_events(event_type: str, *, min_severity: int = 1, limit: int = 20000) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    from backend.database.connection import MarketSessionLocal
    from backend.services.events.market_events_store import ensure_schema

    ensure_schema()
    db = MarketSessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT id, event_type, symbol, ts_ms, severity, direction, source, title "
                "FROM market_events WHERE event_type = :t AND severity >= :sev "
                "ORDER BY ts_ms ASC LIMIT :lim"
            ),
            {"t": event_type, "sev": int(min_severity), "lim": int(limit)},
        ).fetchall()
    except Exception as exc:
        logger.warning("[event_study] load_events %s 失败: %s", event_type, exc)
        return []
    finally:
        db.close()
    return [
        {
            "id": r[0], "event_type": r[1], "symbol": r[2], "ts_ms": int(r[3]),
            "severity": int(r[4]), "direction": (float(r[5]) if r[5] is not None else None),
            "source": r[6], "title": r[7],
        }
        for r in rows
    ]


def load_hourly_closes(
    symbols: Sequence[str],
    start_s: int,
    end_s: int,
    *,
    exchange: str = "binance",
) -> Dict[str, Dict[int, float]]:
    """{symbol: {hour_ts: close}}。hour_ts 为秒、整点。"""
    from sqlalchemy import bindparam, text
    from backend.database.connection import MarketSessionLocal

    syms = sorted({_kline_base(s) for s in symbols if s})
    out: Dict[str, Dict[int, float]] = {s: {} for s in syms}
    if not syms:
        return out
    db = MarketSessionLocal()
    try:
        sql = (
            "SELECT symbol, timestamp, close_price FROM crypto_klines "
            "WHERE exchange = :ex AND period = '1h' AND symbol IN :syms "
            "AND timestamp BETWEEN :lo AND :hi ORDER BY symbol, timestamp"
        )
        rows = db.execute(
            text(sql).bindparams(bindparam("syms", expanding=True)),
            {"ex": exchange, "syms": list(syms), "lo": int(start_s), "hi": int(end_s)},
        ).fetchall()
    except Exception as exc:
        logger.warning("[event_study] load_hourly_closes 失败: %s", exc)
        return out
    finally:
        db.close()
    for sym, ts, px in rows:
        try:
            hour = (int(ts) // HOUR) * HOUR
            out.setdefault(str(sym).upper(), {})[hour] = float(px)
        except Exception:
            continue
    return out


def _btc_regime(btc: Dict[int, float], event_hour: int) -> str:
    """粗分：30d 收益符号 → trend_up/trend_down；数据不足 → unknown。"""
    p0 = btc.get(event_hour)
    p_ago = btc.get(event_hour - 30 * 24 * HOUR)
    if p0 is None or p_ago is None or p_ago <= 0:
        return "unknown"
    return "trend_up" if p0 >= p_ago else "trend_down"


# --------------------------------------------------------------------------- core
def study_from_events(
    event_type: str,
    events: Sequence[Dict[str, Any]],
    hourly: Dict[str, Dict[int, float]],
    *,
    pre_h: int = DEFAULT_PRE_H,
    post_h: int = DEFAULT_POST_H,
    min_n: int = DEFAULT_MIN_N,
    cost_threshold: float = COST_THRESHOLD,
    exchange: str = "binance",
    timeframe: str = "1h",
) -> EventStudyReport:
    hours = hours_axis(pre_h, post_h)
    t0_idx = hours.index(0)
    btc = hourly.get("BTC") or {}
    per_h_vals: Dict[int, List[float]] = {h: [] for h in hours}
    paths: List[List[Optional[float]]] = []
    regimes: List[str] = []
    skipped = 0
    btc_n = 0

    for ev in events:
        ts_s = int(ev.get("ts_ms") or 0) // 1000
        if ts_s <= 0:
            skipped += 1
            continue
        raw_sym = ev.get("symbol")
        asset_is_btc = (not raw_sym) or _kline_base(raw_sym) == "BTC"
        sym = "BTC" if asset_is_btc else _kline_base(raw_sym)
        series = hourly.get(sym) or {}
        px = align_path(series, ts_s, hours)
        btc_px = align_path(btc, ts_s, hours) if not asset_is_btc else px
        if px is None or px[t0_idx] is None:
            skipped += 1
            continue
        a_ret = path_returns(px, t0_index=t0_idx)
        b_ret = path_returns(btc_px, t0_index=t0_idx) if btc_px is not None else None
        if a_ret is None:
            skipped += 1
            continue
        sign = event_sign(event_type, ev.get("direction"))
        signed: List[Optional[float]] = []
        ok_any = False
        for i, h in enumerate(hours):
            br = b_ret[i] if b_ret is not None else None
            v = signed_excess(a_ret[i], br, sign, asset_is_btc=asset_is_btc)
            signed.append(v)
            if v is not None:
                per_h_vals[h].append(v)
                ok_any = True
        if not ok_any:
            skipped += 1
            continue
        paths.append(signed)
        if asset_is_btc:
            btc_n += 1
        eh = (ts_s // HOUR) * HOUR
        regimes.append(_btc_regime(btc, eh))

    n_used = len(paths)
    mean_excess: List[Optional[float]] = []
    mean_map: Dict[int, float] = {}
    for h in hours:
        xs = per_h_vals[h]
        if not xs:
            mean_excess.append(None)
        else:
            m = sum(xs) / len(xs)
            mean_excess.append(round(m, 6))
            mean_map[h] = m

    opt_h, _ = optimal_hold(mean_map, post_h)
    opt_idx = hours.index(opt_h) if opt_h in hours else t0_idx + 1
    hits = 0
    opt_sample: List[float] = []
    for p in paths:
        if opt_idx < len(p) and p[opt_idx] is not None:
            v = float(p[opt_idx])
            opt_sample.append(v)
            if v > 0:
                hits += 1

    n_opt = len(opt_sample)
    hit_rate = (hits / n_opt) if n_opt else 0.0
    w_lo, w_hi = wilson_interval(hits, n_opt)
    m, se, m_lo, m_hi = mean_se_interval(opt_sample)

    # 显著性只看预注册持有期（避免对 72 根里 argmax 做同一份样本检验）
    fixed: Dict[str, Any] = {}
    sig_hs: List[int] = []
    for fh in FIXED_HORIZONS:
        if fh not in hours:
            continue
        idx = hours.index(fh)
        sample = [float(p[idx]) for p in paths if idx < len(p) and p[idx] is not None]
        mm, _, lo, hi = mean_se_interval(sample)
        k = sum(1 for x in sample if x > 0)
        nn = len(sample)
        hr = (k / nn) if nn else 0.0
        wlo, whi = wilson_interval(k, nn)
        ok = bool(nn >= min_n and lo > cost_threshold)
        if ok:
            sig_hs.append(fh)
        fixed[str(fh)] = {
            "n": nn, "mean": round(mm, 6), "mean_lo": round(lo, 6), "mean_hi": round(hi, 6),
            "hit_rate": round(hr, 4), "wilson_lo": round(wlo, 4), "wilson_hi": round(whi, 4),
            "significant": ok,
        }
    significant = bool(sig_hs)

    # regime split at opt hold
    regime_split: Dict[str, Any] = {}
    by_reg: Dict[str, List[float]] = {}
    for p, rg in zip(paths, regimes):
        if opt_idx < len(p) and p[opt_idx] is not None:
            by_reg.setdefault(rg, []).append(float(p[opt_idx]))
    for rg, xs in sorted(by_reg.items()):
        mm, _, lo, hi = mean_se_interval(xs)
        k = sum(1 for x in xs if x > 0)
        regime_split[rg] = {
            "n": len(xs), "mean": round(mm, 6), "mean_lo": round(lo, 6),
            "hit_rate": round(k / len(xs), 4) if xs else 0.0,
        }

    notes = []
    if n_used < min_n:
        notes.append(f"样本 {n_used} < min_n={min_n}，不能过显著性门（历史深度不足属预期）")
    if btc_n and btc_n == n_used:
        notes.append("本类型全部是 BTC/全市场事件，路径为 BTC 自身收益而非超额")
    if event_sign(event_type, None) == 0:
        notes.append("缺省不签名；有 event.direction 的样本按方向翻转后再聚合")
    if not significant and n_used >= min_n:
        notes.append("预注册持有期（1/4/8/24/48/72h）均值 95% 下界均未高于成本")
    if significant:
        notes.append(f"过门持有期 {sig_hs}h（预注册，非 argmax）")

    return EventStudyReport(
        event_type=event_type,
        n=n_used,
        n_used=n_used,
        hours=hours,
        mean_excess=mean_excess,
        hit_rate=round(hit_rate, 4),
        wilson_lo=round(w_lo, 4),
        wilson_hi=round(w_hi, 4),
        optimal_hold_h=opt_h,
        mean_at_opt=round(m, 6),
        mean_lo_at_opt=round(m_lo, 6),
        mean_hi_at_opt=round(m_hi, 6),
        regime_split=regime_split,
        significant=significant,
        significant_horizons=sig_hs,
        fixed_horizons=fixed,
        cost_threshold=cost_threshold,
        min_n=min_n,
        exchange=exchange,
        timeframe=timeframe,
        notes="; ".join(notes),
        n_events_raw=len(events),
        skipped_no_price=skipped,
        asset_is_btc_share=round(btc_n / n_used, 3) if n_used else 0.0,
    )


def run_event_study(
    event_type: str,
    *,
    window_pre_h: int = DEFAULT_PRE_H,
    window_post_h: int = DEFAULT_POST_H,
    min_n: int = DEFAULT_MIN_N,
    exchange: str = "binance",
    timeframe: str = "1h",
    cost_threshold: float = COST_THRESHOLD,
    min_severity: int = 1,
) -> EventStudyReport:
    events = load_events(event_type, min_severity=min_severity)
    if not events:
        hours = hours_axis(window_pre_h, window_post_h)
        return EventStudyReport(
            event_type=event_type, n=0, n_used=0, hours=hours,
            mean_excess=[None] * len(hours), hit_rate=0.0, wilson_lo=0.0, wilson_hi=1.0,
            optimal_hold_h=1, mean_at_opt=0.0, mean_lo_at_opt=0.0, mean_hi_at_opt=0.0,
            significant=False, cost_threshold=cost_threshold, min_n=min_n,
            exchange=exchange, timeframe=timeframe, notes="库内无该类型事件",
        )
    ts_list = [int(e["ts_ms"]) // 1000 for e in events]
    start_s = min(ts_list) - abs(window_pre_h) * HOUR - 2 * HOUR
    end_s = max(ts_list) + abs(window_post_h) * HOUR + 2 * HOUR
    # 还要 30d BTC 做 regime
    start_s = min(start_s, min(ts_list) - 31 * 24 * HOUR)
    symbols = ["BTC"] + [e.get("symbol") or "BTC" for e in events]
    hourly = load_hourly_closes(symbols, start_s, end_s, exchange=exchange)
    return study_from_events(
        event_type, events, hourly,
        pre_h=window_pre_h, post_h=window_post_h, min_n=min_n,
        cost_threshold=cost_threshold, exchange=exchange, timeframe=timeframe,
    )


def _all_types() -> List[str]:
    from backend.services.events.market_events_store import ALL_EVENT_TYPES

    return list(ALL_EVENT_TYPES)


def run_all(**kwargs: Any) -> Dict[str, EventStudyReport]:
    out: Dict[str, EventStudyReport] = {}
    for et in _all_types():
        try:
            out[et] = run_event_study(et, **kwargs)
            logger.info(
                "[event_study] %s n=%d used=%d sig=%s opt=%dh mean=%.4f wilson=[%.2f,%.2f]",
                et, out[et].n_events_raw, out[et].n_used, out[et].significant,
                out[et].optimal_hold_h, out[et].mean_at_opt, out[et].wilson_lo, out[et].wilson_hi,
            )
        except Exception as exc:
            logger.warning("[event_study] %s 失败: %s", et, exc)
            hours = hours_axis(kwargs.get("window_pre_h", DEFAULT_PRE_H), kwargs.get("window_post_h", DEFAULT_POST_H))
            out[et] = EventStudyReport(
                event_type=et, n=0, n_used=0, hours=hours, mean_excess=[None] * len(hours),
                hit_rate=0.0, wilson_lo=0.0, wilson_hi=1.0, optimal_hold_h=1,
                mean_at_opt=0.0, mean_lo_at_opt=0.0, mean_hi_at_opt=0.0,
                notes=f"error: {exc}"[:240],
            )
    return out


def write_reports(reports: Dict[str, EventStudyReport], path: Optional[Path] = None) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts_ms": int(time.time() * 1000),
        "cost_threshold": COST_THRESHOLD,
        "min_n": DEFAULT_MIN_N,
        "types": {k: v.to_dict() for k, v in reports.items()},
        "significant": [k for k, v in reports.items() if v.significant],
        "summary": [
            {
                "event_type": k, "n_used": v.n_used, "n_raw": v.n_events_raw,
                "significant": v.significant, "significant_horizons": v.significant_horizons,
                "hit_rate": v.hit_rate,
                "wilson_lo": v.wilson_lo, "optimal_hold_h": v.optimal_hold_h,
                "mean_at_opt": v.mean_at_opt, "mean_lo_at_opt": v.mean_lo_at_opt,
                "notes": v.notes,
            }
            for k, v in reports.items()
        ],
    }
    dest = path or (DATA_DIR / "latest.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return dest


def latest_report() -> Optional[Dict[str, Any]]:
    p = DATA_DIR / "latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def scheduled_job() -> Dict[str, Any]:
    """每日非峰时跑全类型冲击报告（只读，不调 LLM）。"""
    reports = run_all()
    path = write_reports(reports)
    sig = [k for k, v in reports.items() if v.significant]
    return {
        "ok": True,
        "path": str(path),
        "n_types": len(reports),
        "significant": sig,
        "counts": {k: v.n_used for k, v in reports.items()},
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="market_events 冲击回测")
    p.add_argument("--type", dest="event_type", default="", help="单个 event_type")
    p.add_argument("--all", action="store_true")
    p.add_argument("--min-n", type=int, default=DEFAULT_MIN_N)
    p.add_argument("--pre", type=int, default=DEFAULT_PRE_H)
    p.add_argument("--post", type=int, default=DEFAULT_POST_H)
    p.add_argument("--exchange", default="binance")
    p.add_argument("--cost", type=float, default=COST_THRESHOLD)
    p.add_argument("--json", dest="json_path", default="")
    args = p.parse_args(list(argv) if argv is not None else None)

    kw = dict(
        window_pre_h=args.pre, window_post_h=args.post, min_n=args.min_n,
        exchange=args.exchange, cost_threshold=args.cost,
    )
    if args.all or not args.event_type:
        reports = run_all(**kw)
    else:
        reports = {args.event_type: run_event_study(args.event_type, **kw)}
    dest = Path(args.json_path) if args.json_path else (DATA_DIR / "latest.json")
    write_reports(reports, dest)
    for k, v in reports.items():
        flag = "PASS" if v.significant else "—"
        print(
            f"{flag:4} {k:32} n={v.n_used:4}/{v.n_events_raw:<4} "
            f"opt={v.optimal_hold_h:2}h mean={v.mean_at_opt:+.4%} "
            f"lo={v.mean_lo_at_opt:+.4%} hit={v.hit_rate:.2f} "
            f"Wilson[{v.wilson_lo:.2f},{v.wilson_hi:.2f}]  {v.notes}"
        )
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
