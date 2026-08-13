"""止盈止损网格训练（可训练出场）。

目标
====
用历史 OHLCV 网格搜索各周期最优 (tp_pct, sl_pct)，写入
``backend/data/tp_sl_learned/latest.json``，开仓时由
``compute_initial_tp_sl_prices`` 覆盖静态表。

覆盖维度
====
- tier：short / mid / long（长线含真实止盈网格，不再写死 TP=0）
- 形态 morph：trend / range / breakout（动量×波动分类）
- 波动带 band：low / mid / high / x-high（按币种波动档）

设计约束
====
- 不做 RL；固定百分比三重障碍（与纸盘触碰口径一致）
- 样本外（时间 holdout）选优，避免只拟合训练集
- 形态/波动带与总档一次网格扫描后聚合，避免重复扫盘
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DIR = _REPO_ROOT / "backend" / "data" / "tp_sl_learned"
_LATEST_NAME = "latest.json"

# 进程内缓存，mtime 变化时失效
_cache: Dict[str, Any] = {"mtime": None, "payload": None}

# 默认训练币：覆盖 low/mid/high 波动带（可用环境变量覆盖）
_DEFAULT_SYMBOLS = ("BTC", "ETH", "SOL", "BNB", "ASTER", "VIRTUAL", "XPL")

_MORPHS = ("trend", "range", "breakout")
_BANDS = ("low", "mid", "high", "x-high")

# tier → 训练超参
_TIER_SPECS: Dict[str, Dict[str, Any]] = {
    "short": {
        "period": "5m",
        "days": 45,
        "max_bars": 48,          # ~4h
        "entry_stride": 12,      # 每小时一个入场点
        "lookback": 12,
        "tp_grid": [0.012, 0.015, 0.018, 0.022, 0.025],
        "sl_grid": [0.008, 0.010, 0.012, 0.015, 0.018],
        "min_rr": 1.2,
        "cost_bps": 6.0,         # 往返费+滑点粗估
    },
    "mid": {
        "period": "1h",
        "days": 120,
        "max_bars": 72,          # 3 天
        "entry_stride": 6,
        "lookback": 12,
        "tp_grid": [0.04, 0.05, 0.06, 0.07, 0.09],
        "sl_grid": [0.02, 0.025, 0.03, 0.035, 0.045],
        "min_rr": 1.4,
        "cost_bps": 8.0,
    },
    "long": {
        "period": "4h",
        "days": 240,
        "max_bars": 90,          # ~15 天，给趋势空间
        "entry_stride": 3,
        "lookback": 8,
        # 长线趋势止盈：不再写死 0（原先只训 SL，面板显示 0% 止盈）
        "tp_grid": [0.08, 0.10, 0.12, 0.15, 0.18, 0.22, 0.28],
        "sl_grid": [0.04, 0.05, 0.06, 0.08, 0.10, 0.12],
        "min_rr": 1.5,
        "cost_bps": 10.0,
        # 默认表 long 为 0/0，用趋势参考档做采纳门槛
        "baseline_tp": 0.12,
        "baseline_sl": 0.06,
    },
}


def learned_dir() -> Path:
    raw = os.getenv("TP_SL_LEARNED_DIR", "").strip()
    p = Path(raw) if raw else _DEFAULT_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def latest_path() -> Path:
    return learned_dir() / _LATEST_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_learned(payload: Dict[str, Any], path: Optional[Path] = None) -> str:
    """写入 latest.json，并落一份带时间戳副本。"""
    out = path or latest_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload.setdefault("updated_at", _now_iso())
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    out.write_text(text, encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    (out.parent / f"tp_sl_{stamp}.json").write_text(text, encoding="utf-8")
    _cache["mtime"] = None
    _cache["payload"] = None
    logger.info("[TpSlTrain] saved %s keys=%s", out, list((payload.get("by_tier") or {}).keys()))
    return str(out)


def load_learned(force: bool = False) -> Optional[Dict[str, Any]]:
    """读 latest.json（带 mtime 缓存）。"""
    path = latest_path()
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
        if (
            not force
            and _cache["payload"] is not None
            and _cache["mtime"] == mtime
        ):
            return _cache["payload"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        _cache["mtime"] = mtime
        _cache["payload"] = payload
        return payload
    except Exception as e:
        logger.warning("[TpSlTrain] load_learned 失败: %s", e)
        return None


def get_learned_pct(
    tier: str,
    band: Optional[str] = None,
    morph: Optional[str] = None,
) -> Optional[Dict[str, float]]:
    """开仓读路径：返回 {tp_pct, sl_pct} 或 None。

    优先 ``tier|band`` → ``tier|morph`` → ``tier``。
    """
    if not _learned_apply_enabled():
        return None
    payload = load_learned()
    if not payload:
        return None
    by_tier = payload.get("by_tier") or {}
    keys: List[str] = []
    t = str(tier or "").lower()
    if band:
        keys.append(f"{t}|{str(band).lower()}")
    if morph:
        keys.append(f"{t}|{str(morph).lower()}")
    keys.append(t)
    for k in keys:
        row = by_tier.get(k)
        if not isinstance(row, dict):
            continue
        try:
            tp = float(row.get("tp_pct", 0) or 0)
            sl = float(row.get("sl_pct", 0) or 0)
        except (TypeError, ValueError):
            continue
        if sl <= 0:
            continue
        # 长线也要求有止盈（趋势训练）；仅当显式 tp=0 且标记 allow_zero_tp 时放行旧结果
        if tp <= 0 and not bool(row.get("allow_zero_tp")):
            continue
        out = {"tp_pct": tp, "sl_pct": sl}
        if row.get("source"):
            out["source"] = str(row["source"])
        if row.get("shape"):
            out["shape"] = str(row["shape"])
        return out
    return None


def _learned_apply_enabled() -> bool:
    try:
        from backend.services.compute.compute_config import get_value

        return bool(get_value("RISK_USE_LEARNED_TP_SL"))
    except Exception:
        return os.getenv("RISK_USE_LEARNED_TP_SL", "1").lower() not in (
            "0", "false", "off", "no",
        )

def _load_ohlcv(symbol: str, period: str, days: int) -> Optional[Dict[str, np.ndarray]]:
    """从 data_center 拉 OHLCV，失败返回 None。"""
    try:
        from backend.services.data_center import PERIOD_SECONDS, data_center
    except Exception as e:
        logger.warning("[TpSlTrain] data_center 不可用: %s", e)
        return None
    try:
        period_sec = int(PERIOD_SECONDS.get(period, 3600))
        count = max(120, int(days * 86400 / max(period_sec, 60)) + 20)
        df = data_center.get_klines_df(
            symbol, period, count=count, purpose="research",
        )
    except Exception as e:
        logger.warning("[TpSlTrain] get_klines_df(%s,%s) 失败: %s", symbol, period, e)
        return None
    if df is None or len(df) < 80:
        return None
    cols = {str(c).lower(): c for c in df.columns}
    # 常见别名
    rename = {}
    for a, b in (("high_price", "high"), ("low_price", "low"), ("close_price", "close")):
        if a in df.columns and b not in cols:
            rename[a] = b
    if rename:
        df = df.rename(columns=rename)
        cols = {str(c).lower(): c for c in df.columns}
    try:
        high = np.asarray(df[cols.get("high", "high")], dtype=float)
        low = np.asarray(df[cols.get("low", "low")], dtype=float)
        close = np.asarray(df[cols.get("close", "close")], dtype=float)
    except Exception:
        return None
    n = min(len(high), len(low), len(close))
    if n < 80:
        return None
    return {"high": high[-n:], "low": low[-n:], "close": close[-n:]}


def simulate_path(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    entry_i: int,
    side: int,
    tp_pct: float,
    sl_pct: float,
    max_bars: int,
    cost: float,
) -> Tuple[float, str]:
    """单笔路径模拟。side=+1 long / -1 short。返回 (net_ret, reason)。"""
    if entry_i < 0 or entry_i >= len(close) - 1:
        return 0.0, "bad_entry"
    entry = float(close[entry_i])
    if not math.isfinite(entry) or entry <= 0:
        return 0.0, "bad_entry"
    end = min(len(close) - 1, entry_i + max(1, int(max_bars)))
    for i in range(entry_i + 1, end + 1):
        hi = float(high[i])
        lo = float(low[i])
        if side > 0:
            if sl_pct > 0 and lo <= entry * (1.0 - sl_pct):
                return -float(sl_pct) - cost, "sl"
            if tp_pct > 0 and hi >= entry * (1.0 + tp_pct):
                return float(tp_pct) - cost, "tp"
        else:
            if sl_pct > 0 and hi >= entry * (1.0 + sl_pct):
                return -float(sl_pct) - cost, "sl"
            if tp_pct > 0 and lo <= entry * (1.0 - tp_pct):
                return float(tp_pct) - cost, "tp"
    last = float(close[end])
    raw = side * (last / entry - 1.0)
    if abs(raw) > 1.0:
        return 0.0, "anomaly"
    return float(raw) - cost, "timeout"


def _score_rets(rets: Sequence[float]) -> Dict[str, float]:
    if not rets:
        return {"n": 0, "avg": 0.0, "win_rate": 0.0, "score": -1e9, "expectancy": 0.0}
    arr = np.asarray(rets, dtype=float)
    n = int(arr.size)
    avg = float(arr.mean())
    wins = float((arr > 0).mean())
    # 期望收益 + 样本惩罚，避免 n 极小时虚高
    score = avg * math.sqrt(max(n, 1)) - 0.0005 * max(0, 40 - n)
    return {
        "n": n,
        "avg": round(avg, 6),
        "win_rate": round(wins, 4),
        "expectancy": round(avg, 6),
        "score": round(score, 6),
    }


def _vol_band_for_symbol(symbol: str) -> str:
    try:
        from backend.services.risk_band_resolver import get_vol_band

        return str(get_vol_band(symbol) or "mid")
    except Exception:
        return "mid"


def _classify_morph(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    i: int,
    lookback: int,
) -> str:
    """入场形态：trend / range / breakout（动量相对波动）。"""
    lb = max(2, int(lookback))
    if i < lb or i >= len(close):
        return "range"
    prev = float(close[i - lb])
    cur = float(close[i])
    if prev <= 0 or not math.isfinite(prev) or not math.isfinite(cur):
        return "range"
    mom = abs(cur / prev - 1.0)
    # 简易 ATR%
    start = max(0, i - lb)
    window_h = high[start : i + 1]
    window_l = low[start : i + 1]
    window_c = close[start : i + 1]
    if len(window_c) < 2:
        return "range"
    prev_c = np.roll(window_c, 1)
    prev_c[0] = window_c[0]
    tr = np.maximum(
        window_h - window_l,
        np.maximum(np.abs(window_h - prev_c), np.abs(window_l - prev_c)),
    )
    atr = float(np.mean(tr))
    atr_pct = atr / cur if cur > 0 else 0.0
    if atr_pct <= 1e-9:
        return "range"
    strength = mom / atr_pct
    if strength >= 2.2 and atr_pct >= 0.012:
        return "breakout"
    if strength >= 1.15:
        return "trend"
    return "range"


def _iter_entries(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    lookback: int,
    stride: int,
    start: int,
    end: int,
) -> List[Tuple[int, int, str]]:
    """生成 (entry_i, side, morph)。方向 = 近 lookback 动量符号。"""
    out: List[Tuple[int, int, str]] = []
    i = max(start, lookback)
    while i < end - 2:
        prev = float(close[i - lookback])
        cur = float(close[i])
        if prev <= 0 or not math.isfinite(prev) or not math.isfinite(cur):
            i += stride
            continue
        mom = cur / prev - 1.0
        if abs(mom) < 0.001:
            i += stride
            continue
        side = 1 if mom > 0 else -1
        morph = _classify_morph(high, low, close, i, lookback)
        out.append((i, side, morph))
        i += stride
    return out


def _eval_pair_on_book(
    book: Dict[str, np.ndarray],
    entries: List[Tuple[int, int, str]],
    tp: float,
    sl: float,
    max_bars: int,
    cost: float,
) -> List[Tuple[float, str]]:
    """返回 [(net_ret, morph), ...]。"""
    out: List[Tuple[float, str]] = []
    h, l, c = book["high"], book["low"], book["close"]
    for ei, side, morph in entries:
        ret, reason = simulate_path(h, l, c, ei, side, tp, sl, max_bars, cost)
        if reason == "anomaly" or reason == "bad_entry":
            continue
        out.append((ret, morph))
    return out


def _pick_best_trial(
    trials: List[Dict[str, Any]],
    baseline_oos: Optional[Dict[str, float]],
) -> Tuple[Optional[Dict[str, Any]], bool, Optional[str]]:
    if not trials:
        return None, False, "no_valid_trial"
    best = max(trials, key=lambda r: float(r["oos"]["score"]))
    adopted = True
    reject_reason = None
    if baseline_oos is not None and baseline_oos.get("n", 0) >= 15:
        if float(best["oos"]["score"]) + 1e-9 < float(baseline_oos["score"]):
            adopted = False
            reject_reason = "worse_than_default"
    return best, adopted, reject_reason


def _row_from_best(best: Dict[str, Any], *, period: str, shape: str = "all") -> Dict[str, Any]:
    return {
        "tp_pct": float(best["tp_pct"]),
        "sl_pct": float(best["sl_pct"]),
        "oos_avg": best["oos"].get("avg"),
        "oos_n": best["oos"].get("n"),
        "oos_win_rate": best["oos"].get("win_rate"),
        "source": "grid_oos",
        "period": period,
        "shape": shape,
    }


def grid_search_tier(
    tier: str,
    symbols: Optional[Sequence[str]] = None,
    holdout_frac: float = 0.3,
) -> Dict[str, Any]:
    """对单个 tier 做网格搜索：总档 + 形态 + 波动带。"""
    t = str(tier).lower()
    spec = _TIER_SPECS.get(t)
    if not spec:
        return {"tier": t, "ok": False, "error": "unknown_tier"}

    syms = list(symbols or _DEFAULT_SYMBOLS)
    env_syms = os.getenv("TP_SL_TRAIN_SYMBOLS", "").strip()
    if env_syms:
        syms = [s.strip().upper() for s in env_syms.split(",") if s.strip()]

    cost = float(spec["cost_bps"]) / 10000.0
    books: List[Dict[str, Any]] = []
    for sym in syms:
        b = _load_ohlcv(sym, spec["period"], int(spec["days"]))
        if b is None:
            continue
        b = dict(b)
        b["symbol"] = sym
        b["band"] = _vol_band_for_symbol(sym)
        books.append(b)
    if not books:
        return {"tier": t, "ok": False, "error": "no_ohlcv", "symbols": syms}

    # 为每个 book 切 train/oos 入场点（带形态标签）
    train_entries: List[Tuple[Dict[str, Any], List[Tuple[int, int, str]]]] = []
    oos_entries: List[Tuple[Dict[str, Any], List[Tuple[int, int, str]]]] = []
    for book in books:
        n = len(book["close"])
        split = int(n * (1.0 - holdout_frac))
        split = max(40, min(n - 20, split))
        tr = _iter_entries(
            book["high"], book["low"], book["close"],
            spec["lookback"], spec["entry_stride"], 0, split,
        )
        oo = _iter_entries(
            book["high"], book["low"], book["close"],
            spec["lookback"], spec["entry_stride"], split, n,
        )
        if tr:
            train_entries.append((book, tr))
        if oo:
            oos_entries.append((book, oo))

    if not train_entries or not oos_entries:
        return {"tier": t, "ok": False, "error": "insufficient_entries", "n_books": len(books)}

    min_rr = float(spec["min_rr"])
    # 基准：现网默认；long 默认 0/0 时用趋势参考档
    try:
        from backend.config.settings import TIER_TP_SL_DEFAULTS

        _def = TIER_TP_SL_DEFAULTS.get(t) or {}
        baseline_tp = float(_def.get("tp_pct") or 0)
        baseline_sl = float(_def.get("sl_pct") or 0)
    except Exception:
        baseline_tp, baseline_sl = 0.0, 0.0
    if baseline_sl <= 0 or (t != "long" and baseline_tp <= 0):
        baseline_tp = float(spec.get("baseline_tp") or baseline_tp or 0)
        baseline_sl = float(spec.get("baseline_sl") or baseline_sl or 0)

    def _eval_all(tp_f: float, sl_f: float, entries_pack) -> List[Tuple[float, str, str]]:
        """[(ret, morph, band), ...]。"""
        acc: List[Tuple[float, str, str]] = []
        for book, ents in entries_pack:
            band = str(book.get("band") or "mid")
            for ret, morph in _eval_pair_on_book(
                book, ents, tp_f, sl_f, spec["max_bars"], cost,
            ):
                acc.append((ret, morph, band))
        return acc

    baseline_oos: Optional[Dict[str, float]] = None
    if baseline_sl > 0 and (t == "long" or baseline_tp > 0):
        base_pairs = [
            (r, m) for r, m, _b in _eval_all(baseline_tp, baseline_sl, oos_entries)
        ]
        baseline_oos = _score_rets([r for r, _ in base_pairs])

    # 一次网格：同时累计 all / morph / band
    trials_all: List[Dict[str, Any]] = []
    trials_morph: Dict[str, List[Dict[str, Any]]] = {m: [] for m in _MORPHS}
    trials_band: Dict[str, List[Dict[str, Any]]] = {b: [] for b in _BANDS}

    for tp in spec["tp_grid"]:
        for sl in spec["sl_grid"]:
            tp_f, sl_f = float(tp), float(sl)
            if tp_f <= 0 or sl_f <= 0:
                continue
            if min_rr > 0 and (tp_f / sl_f) < min_rr:
                continue

            tr_all = _eval_all(tp_f, sl_f, train_entries)
            oo_all = _eval_all(tp_f, sl_f, oos_entries)

            tr_s = _score_rets([r for r, _m, _b in tr_all])
            oo_s = _score_rets([r for r, _m, _b in oo_all])
            if oo_s["n"] >= 15:
                trials_all.append({
                    "tp_pct": tp_f,
                    "sl_pct": sl_f,
                    "train": tr_s,
                    "oos": oo_s,
                })

            for morph in _MORPHS:
                tr_m = [r for r, m, _b in tr_all if m == morph]
                oo_m = [r for r, m, _b in oo_all if m == morph]
                tr_ms = _score_rets(tr_m)
                oo_ms = _score_rets(oo_m)
                if oo_ms["n"] < 12:
                    continue
                trials_morph[morph].append({
                    "tp_pct": tp_f,
                    "sl_pct": sl_f,
                    "train": tr_ms,
                    "oos": oo_ms,
                })

            for band in _BANDS:
                tr_b = [r for r, _m, b in tr_all if b == band]
                oo_b = [r for r, _m, b in oo_all if b == band]
                tr_bs = _score_rets(tr_b)
                oo_bs = _score_rets(oo_b)
                if oo_bs["n"] < 10:
                    continue
                trials_band[band].append({
                    "tp_pct": tp_f,
                    "sl_pct": sl_f,
                    "train": tr_bs,
                    "oos": oo_bs,
                })

    best, adopted, reject_reason = _pick_best_trial(trials_all, baseline_oos)
    if best is None:
        return {
            "tier": t,
            "ok": False,
            "error": "no_valid_trial",
            "n_books": len(books),
            "n_trials": len(trials_all),
            "baseline_oos": baseline_oos,
        }

    by_shape: Dict[str, Any] = {}
    for morph, trials in trials_morph.items():
        b, ad, rr = _pick_best_trial(trials, None)
        if not b:
            continue
        by_shape[morph] = {
            "best": b,
            "adopted": True,  # 形态档无静态默认，有样本即采纳
            "reject_reason": None,
            "n_trials": len(trials),
            "top3": sorted(trials, key=lambda r: r["oos"]["score"], reverse=True)[:3],
        }

    by_band: Dict[str, Any] = {}
    for band, trials in trials_band.items():
        b, ad, rr = _pick_best_trial(trials, None)
        if not b:
            continue
        by_band[band] = {
            "best": b,
            "adopted": True,
            "reject_reason": None,
            "n_trials": len(trials),
            "top3": sorted(trials, key=lambda r: r["oos"]["score"], reverse=True)[:3],
        }

    return {
        "tier": t,
        "ok": True,
        "adopted": adopted,
        "reject_reason": reject_reason,
        "period": spec["period"],
        "symbols": syms,
        "n_books": len(books),
        "bands_present": sorted({str(b.get("band")) for b in books}),
        "baseline": {
            "tp_pct": baseline_tp,
            "sl_pct": baseline_sl,
            "oos": baseline_oos,
        },
        "best": {
            "tp_pct": best["tp_pct"],
            "sl_pct": best["sl_pct"],
            "train": best["train"],
            "oos": best["oos"],
            "source": "grid_oos",
        },
        "n_trials": len(trials_all),
        "top3": sorted(trials_all, key=lambda r: r["oos"]["score"], reverse=True)[:3],
        "by_shape": by_shape,
        "by_band": by_band,
    }


def run_train_tp_sl(
    tiers: Optional[Sequence[str]] = None,
    symbols: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """训练入口：多 tier 网格（含形态/波动带）→ 写 latest.json。"""
    t0 = time.time()
    use_tiers = [t.lower() for t in (tiers or ("short", "mid", "long"))]
    by_tier: Dict[str, Any] = {}
    details: Dict[str, Any] = {}
    for t in use_tiers:
        if t not in _TIER_SPECS:
            continue
        logger.info("[TpSlTrain] grid tier=%s ...", t)
        res = grid_search_tier(t, symbols=symbols)
        details[t] = res
        if res.get("ok") and res.get("best") and res.get("adopted", True):
            by_tier[t] = _row_from_best(res["best"], period=str(res.get("period") or ""), shape="all")
        elif res.get("ok") and not res.get("adopted", True):
            logger.info(
                "[TpSlTrain] tier=%s 未采纳（%s），保留静态默认",
                t, res.get("reject_reason"),
            )
        # 形态分桶
        for morph, block in (res.get("by_shape") or {}).items():
            if not block.get("adopted", True) or not block.get("best"):
                continue
            key = f"{t}|{morph}"
            by_tier[key] = _row_from_best(
                block["best"], period=str(res.get("period") or ""), shape=morph,
            )
        # 波动带分桶
        for band, block in (res.get("by_band") or {}).items():
            if not block.get("adopted", True) or not block.get("best"):
                continue
            key = f"{t}|{band}"
            by_tier[key] = _row_from_best(
                block["best"], period=str(res.get("period") or ""), shape=f"band:{band}",
            )

    payload = {
        "updated_at": _now_iso(),
        "version": 2,
        "by_tier": by_tier,
        "details": {
            k: {
                "ok": v.get("ok"),
                "adopted": v.get("adopted"),
                "reject_reason": v.get("reject_reason"),
                "error": v.get("error"),
                "n_books": v.get("n_books"),
                "n_trials": v.get("n_trials"),
                "baseline": v.get("baseline"),
                "best": v.get("best"),
                "top3": v.get("top3"),
                "symbols": v.get("symbols"),
                "period": v.get("period"),
                "bands_present": v.get("bands_present"),
                "by_shape": {
                    sk: {
                        "adopted": sv.get("adopted"),
                        "best": sv.get("best"),
                        "n_trials": sv.get("n_trials"),
                    }
                    for sk, sv in (v.get("by_shape") or {}).items()
                },
                "by_band": {
                    bk: {
                        "adopted": bv.get("adopted"),
                        "best": bv.get("best"),
                        "n_trials": bv.get("n_trials"),
                    }
                    for bk, bv in (v.get("by_band") or {}).items()
                },
            }
            for k, v in details.items()
        },
        "elapsed_sec": round(time.time() - t0, 2),
        "note": (
            "长线含真实止盈；另按形态(trend/range/breakout)与波动带(low/mid/high/x-high)分桶。"
            "仅当样本外优于基准时写入总档；形态/波动带有足够样本即写入。"
        ),
    }
    path = save_learned(payload)
    payload["path"] = path
    payload["ok"] = bool(by_tier)
    return payload


def get_status() -> Dict[str, Any]:
    """API 状态：是否启用、最新结果摘要。"""
    enabled = _learned_apply_enabled()
    auto = auto_train_enabled()
    payload = load_learned()
    age_h = None
    try:
        p = latest_path()
        if p.exists():
            age_h = round((time.time() - p.stat().st_mtime) / 3600.0, 2)
    except Exception:
        pass
    return {
        "enabled": enabled,
        "auto_train": auto,
        "schedule": "每日 05:00（tp_sl_train_daily）；缺结果/超期时启动后补训",
        "path": str(latest_path()),
        "exists": latest_path().exists(),
        "updated_at": (payload or {}).get("updated_at"),
        "age_hours": age_h,
        "by_tier": (payload or {}).get("by_tier") or {},
        "elapsed_sec": (payload or {}).get("elapsed_sec"),
        "ok": bool((payload or {}).get("by_tier")),
        "last_auto": dict(_last_auto),
    }


def auto_train_enabled() -> bool:
    """是否开启自动训练（默认开）。"""
    try:
        from backend.services.compute.compute_config import get_value

        return bool(get_value("RISK_TP_SL_TRAIN_AUTO"))
    except Exception:
        return os.getenv("RISK_TP_SL_TRAIN_AUTO", "1").lower() not in (
            "0", "false", "off", "no",
        )


_last_auto: Dict[str, Any] = {
    "at": None,
    "source": None,
    "ok": None,
    "skipped": None,
    "reason": None,
    "by_tier": None,
}
_auto_lock = threading.Lock()
_auto_running = False


def scheduled_tp_sl_train(source: str = "cron") -> Dict[str, Any]:
    """定时/启动补训入口：尊重 RISK_TP_SL_TRAIN_AUTO，单飞。"""
    global _auto_running
    if not auto_train_enabled():
        out = {"skipped": True, "reason": "auto_disabled", "source": source}
        _last_auto.update({"at": _now_iso(), **out, "ok": None, "by_tier": None})
        logger.info("[TpSlTrain] skip auto (%s): auto_disabled", source)
        return out
    if not _auto_lock.acquire(blocking=False):
        out = {"skipped": True, "reason": "already_running", "source": source}
        _last_auto.update({"at": _now_iso(), **out, "ok": None, "by_tier": None})
        return out
    try:
        if _auto_running:
            out = {"skipped": True, "reason": "already_running", "source": source}
            _last_auto.update({"at": _now_iso(), **out, "ok": None, "by_tier": None})
            return out
        _auto_running = True
        logger.info("[TpSlTrain] auto train start source=%s", source)
        rep = run_train_tp_sl()
        out = {
            "skipped": False,
            "source": source,
            "ok": bool(rep.get("ok")),
            "by_tier": list((rep.get("by_tier") or {}).keys()),
            "elapsed_sec": rep.get("elapsed_sec"),
            "path": rep.get("path"),
        }
        _last_auto.update({"at": _now_iso(), "reason": None, **out})
        logger.info(
            "[TpSlTrain] auto train done source=%s ok=%s tiers=%s",
            source, out["ok"], out["by_tier"],
        )
        return {**rep, **out}
    except Exception as e:
        logger.exception("[TpSlTrain] auto train failed source=%s: %s", source, e)
        out = {
            "skipped": False,
            "source": source,
            "ok": False,
            "reason": str(e),
            "by_tier": None,
        }
        _last_auto.update({"at": _now_iso(), **out})
        return out
    finally:
        _auto_running = False
        try:
            _auto_lock.release()
        except Exception:
            pass


def maybe_startup_train(max_age_hours: float = 36.0) -> Dict[str, Any]:
    """启动后补训：自动开着，且没有结果或结果过旧时后台跑一轮。"""
    if not auto_train_enabled():
        return {"skipped": True, "reason": "auto_disabled"}
    path = latest_path()
    need = True
    reason = "missing"
    if path.exists():
        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        if age_h < max_age_hours:
            need = False
            reason = f"fresh_{age_h:.1f}h"
        else:
            reason = f"stale_{age_h:.1f}h"
    if not need:
        return {"skipped": True, "reason": reason}

    def _bg():
        # 等主服务就绪，避免抢启动带宽
        time.sleep(25)
        scheduled_tp_sl_train(source=f"startup:{reason}")

    threading.Thread(target=_bg, name="tp-sl-startup-train", daemon=True).start()
    return {"skipped": False, "queued": True, "reason": reason}
