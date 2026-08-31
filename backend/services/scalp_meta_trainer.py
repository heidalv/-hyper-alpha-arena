"""ScalpMetaTrainer v2 — 短线信号"真假过滤器"（元标签）自动训练 + 验证例程。

v2 变更（2026-08-25,依据 tools/scalp_signal_edge_audit.py 实证）
=====================================================================
实证发现（35 万已结算信号, tools/scalp_signal_edge_audit.py）:
- factor_score 与胜率零相关（校准 no_edge,任何分数桶 <42% 保本;
  仅用 v1 特征集训练:OOS AUC 0.519,≈抛硬币）;
- 币种级滚动状态是全流最强预测器:roll_fwd20>0 桶胜率 67.4% 净 +0.35%,
  roll_fwd20<0 桶胜率 12.9% 净 -0.66%;加滞后后仍保持 50.6% vs 27.6% 的分化;
- v1 训练(OOS AUC 0.533,usable=false)缺少 regime 上下文,无法自证可用。

v2 关键改动:
1. 新增币种级滚动 regime 特征(用 settle_ts 做"成熟期过滤",严格无前视):
   roll_wr20 / roll_fwd20 / roll_rsi10 / roll_cvd10 / sec_since_sig + hour/weekday。
   行 i 只纳入 j<i 且 settle_ts[j] <= signal_ts[i] 的前序信号(结果在行 i 时刻
   必然已结算)。训练时在全流上计算滚动特征,再对训练行去重;推理时读
   data/scalp_regime_state.json(小时级刷新),predict_win_prob 自动合并。
2. 去重窗口默认放宽到 300s(原 1800s,过激压缩 35 万 → 9583)。
3. 标签可选:SCALP_META_LABEL=fwd 用 fwd_ret>0 替代 TP/SL win 二值。
4. usable 门控与报告格式保持不变(前端/arbiter 兼容)。
回滚:SCALP_META_REGIME_FEATURES=false 即回退 v1 特征集。
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
_MODEL_PATH = os.path.join(_DATA_DIR, "scalp_meta_model.pkl")
_REPORT_PATH = os.path.join(_DATA_DIR, "scalp_meta_report.json")
_REGIME_STATE_PATH = os.path.join(_DATA_DIR, "scalp_regime_state.json")


def _truthy(name: str, default: str) -> bool:
    return (os.getenv(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


def _regime_features_enabled() -> bool:
    return _truthy("SCALP_META_REGIME_FEATURES", "true")


def _label_mode() -> str:
    return (os.getenv("SCALP_META_LABEL", "win") or "win").strip().lower()


def _min_samples() -> int:
    try:
        return int(os.getenv("SCALP_META_MIN_SAMPLES", "5000") or 5000)
    except Exception:
        return 5000


def _min_per_class() -> int:
    try:
        return int(os.getenv("SCALP_META_MIN_PER_CLASS", "1500") or 1500)
    except Exception:
        return 1500


def _n_folds() -> int:
    try:
        return int(os.getenv("SCALP_META_N_FOLDS", "5") or 5)
    except Exception:
        return 5


def _gate_min_auc() -> float:
    try:
        return float(os.getenv("SCALP_META_GATE_AUC", "0.60") or 0.60)
    except Exception:
        return 0.60


def _feature_freq_min() -> float:
    try:
        return float(os.getenv("SCALP_META_FEATURE_FREQ_MIN", "0.3") or 0.3)
    except Exception:
        return 0.3


def _dedup_sec() -> int:
    try:
        v = int(os.getenv("SCALP_META_DEDUP_SEC", "0") or 0)
        if v > 0:
            return v
    except Exception:
        pass
    return 300  # v2: 放宽到 300s(原 max(300,horizon)=1800)


def _regime_win_span() -> int:
    try:
        return int(os.getenv("SCALP_META_REGIME_WIN_SPAN", "20") or 20)
    except Exception:
        return 20


def _load_settled_rows() -> List[Dict[str, Any]]:
    from sqlalchemy import text as _text
    from backend.database.connection import SessionLocal
    db = SessionLocal()
    try:
        rows = db.execute(_text(
            "SELECT id, signal_ts, settle_ts, created_at, symbol, direction, factor_score, win, "
            "fwd_ret, net_ret, horizon_sec, features_json "
            "FROM scalp_signal_log WHERE settled = true AND win IS NOT NULL "
            "AND created_at >= NOW() - INTERVAL '60 days' ORDER BY created_at"
        )).fetchall()
        out = []
        for r in rows:
            try:
                # [2026-08-31 perf] orjson 解码释放 GIL（std json C 解码全程持 GIL，
                # 大块 features_json 会在调度线程里堵住所有 HTTP 请求数百 ms）；
                # 失败回退 std json，语义等价（均产出 dict/str 键）。
                import orjson as _oj
                feats = _oj.loads(r.features_json) if r.features_json else {}
            except Exception:
                try:
                    feats = json.loads(r.features_json) if r.features_json else {}
                except Exception:
                    feats = {}
            if not isinstance(feats, dict):
                feats = {}
            # signal_ts/settle_ts 为 UTC epoch 秒(created_at 为北京时间,相差 8h)
            sts = int(r.signal_ts or 0)
            if sts <= 0:
                sts = int(r.created_at.timestamp())
            settle = int(r.settle_ts or 0)
            if settle <= 0:
                settle = sts + int(r.horizon_sec or 1800)
            out.append({
                "ts": sts, "settle": settle, "created_at": r.created_at,
                "symbol": str(r.symbol or ""), "direction": str(r.direction or ""),
                "factor_score": float(r.factor_score or 0), "win": 1 if r.win else 0,
                "fwd_ret": float(r.fwd_ret or 0), "net_ret": float(r.net_ret or 0),
                "horizon": int(r.horizon_sec or 1800), "feats": feats,
            })
        return out
    finally:
        db.close()


def _dedup_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按币贪心去重:只保留与上一条保留信号间隔 ≥ 去重窗口的样本(近似非重叠)。"""
    win = _dedup_sec()
    if win <= 0:
        return rows
    last_kept: Dict[str, int] = {}
    kept: List[Dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: (x["symbol"], x["ts"])):
        sym = r["symbol"]
        if sym not in last_kept or (r["ts"] - last_kept[sym]) >= win:
            kept.append(r)
            last_kept[sym] = r["ts"]
    kept.sort(key=lambda x: x["ts"])
    return kept


def _numeric(v: Any) -> Optional[float]:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# 币种级滚动 regime 特征(严格无前视:settle_ts[j] <= signal_ts[i])
# ─────────────────────────────────────────────────────────────
REGIME_COLS = ("roll_wr20", "roll_fwd20", "roll_rsi10", "roll_cvd10",
               "sec_since_sig", "hour", "weekday")

# 15m K线趋势特征(审计实证: base+kline OOS AUC 0.527→0.606,top30% 净收益转正)
KLINE_COLS = ("ret_15m", "ret_1h", "ret_4h", "ema_slope", "rsi15m",
              "atr_pct", "vol_1h", "below_ema20")

# [2026-09-16 插针行情训练] 影线/插针特征——与 ScalpExecutionGate._check_wick_manipulation
# 同源公式(wick_ratio=max(upper,lower)/(|close-open|+1e-10)),让元模型学习"插针环境里
# 什么样的方向/形态能赢",而不是只会一刀切避开(那属于执行门职责,不产生样本)。
# wick_density_20 即执行门同一判定量(>3.0 占比),训练侧据此对插针样本加权。
WICK_COLS = ("wick_density_20", "last_wick_ratio", "upper_wick_5",
             "lower_wick_5", "spike_mag_20")

# 方向×趋势交互(规则实证: 顺势多头 49.4% vs 逆势 33.4%;顺势空头 43.7% vs 逆势 31.2%)
# dir_x_wick_asym: 方向 × 影线不对称(下影强=买方防守,顺势多+下影强=支撑有效)
KLINE_INTER_COLS = ("dir_x_ema", "dir_x_ret1h", "dir_x_ret4h", "dir_x_wick_asym")

_RSI_KEYS = ("rsi",)
_CVD_KEYS = ("of_cvd", "cvd")


def _feat_float(feats: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    for k in keys:
        v = feats.get(k)
        f = _numeric(v)
        if f is not None:
            return f
    return None


_KLINE_DB_CACHE: Dict[str, Any] = {}


def kline_feats_from_db_cached(symbol: str, ttl: int = 120) -> Dict[str, float]:
    """实盘兜底: market_data 无 15m K线(数据中心判过期返回空)时直查
    alpha_market.crypto_klines。120s 缓存(15m bar 粒度足够),避免热路径逐笔查库。
    与训练同源: 同一张表、同样的多交易所合并口径。"""
    try:
        import time as _time
        key = str(symbol).upper()
        ent = _KLINE_DB_CACHE.get(key)
        if ent and (_time.time() - ent[0]) < ttl:
            return ent[1]
        import psycopg
        import pandas as pd
        with psycopg.connect(
            "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open_price, high_price, low_price, close_price, volume "
                    "FROM crypto_klines WHERE period='15m' AND symbol=%s "
                    "ORDER BY timestamp DESC LIMIT 80", (key,))
                rows = cur.fetchall()
        if not rows:
            _KLINE_DB_CACHE[key] = (_time.time(), {})
            return {}
        df = pd.DataFrame(
            rows, columns=["ts", "open", "high", "low", "close", "volume"]
        ).iloc[::-1].reset_index(drop=True)
        feats = compute_kline_feats(df)
        _KLINE_DB_CACHE[key] = (_time.time(), feats)
        return feats
    except Exception as e:
        logger.debug(f"[ScalpMeta] kline DB 兜底失败: {e}")
        return {}


def compute_kline_feats(klines_df: Any) -> Dict[str, float]:
    """从 15m K线 DataFrame(需 high/low/close 列,≥40 根)计算趋势特征。

    推理侧调用:scalp_loop 已有 market_data["klines_15m"],零 DB 开销。
    返回特征字典;数据不足时返回空 dict(对应特征置 0,与训练缺省一致)。
    """
    try:
        import pandas as pd
        if klines_df is None:
            return {}
        df = klines_df if isinstance(klines_df, pd.DataFrame) else pd.DataFrame(klines_df)
        if len(df) < 40 or not {"high", "low", "close"}.issubset(df.columns):
            return {}
        closes = df["close"].astype(float).values
        highs = df["high"].astype(float).values
        lows = df["low"].astype(float).values
        n = len(closes)
        c = closes[-1]
        if c <= 0:
            return {}
        # 1h/4h 收益
        ret_15m = c / closes[-2] - 1 if n >= 2 else 0.0
        ret_1h = c / closes[-5] - 1 if n >= 5 else 0.0
        ret_4h = c / closes[-17] - 1 if n >= 17 else 0.0
        # EMA20 斜率
        ema20 = float(pd.Series(closes).ewm(span=20, adjust=False).mean().iloc[-1])
        ema_slope = (c - ema20) / ema20 if ema20 > 0 else 0.0
        # RSI(14)
        d = pd.Series(closes).diff()
        up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        rsi15m = float((100 - 100 / (1 + up.iloc[-1] / max(dn.iloc[-1], 1e-9))))
        # ATR(14) / close
        tr = np.maximum(highs[1:] - lows[1:],
                        np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
        atr_pct = float(pd.Series(tr).rolling(14).mean().iloc[-1] / c) if len(tr) >= 14 else 0.0
        # 1h 波动率
        seg = closes[-5:]
        vol_1h = float(np.std(np.diff(seg)) / c) if len(seg) >= 2 else 0.0
        # ── 插针/影线特征(与 ScalpExecutionGate._check_wick_manipulation 同源公式) ──
        o20 = df["open"].astype(float).tail(20).to_numpy()
        h20 = df["high"].astype(float).tail(20).to_numpy()
        l20 = df["low"].astype(float).tail(20).to_numpy()
        c20 = df["close"].astype(float).tail(20).to_numpy()
        body20 = np.abs(c20 - o20)
        upper20 = h20 - np.maximum(o20, c20)
        lower20 = np.minimum(o20, c20) - l20
        wr20 = np.maximum(upper20, lower20) / (body20 + 1e-10)
        wick_density_20 = float((wr20 > 3.0).mean())
        last_wick_ratio = float(np.clip(wr20[-1], 0.0, 50.0))
        upper_wick_5 = float(np.clip(np.mean(upper20[-5:] / (body20[-5:] + 1e-10)), 0.0, 20.0))
        lower_wick_5 = float(np.clip(np.mean(lower20[-5:] / (body20[-5:] + 1e-10)), 0.0, 20.0))
        spike_mag_20 = float(np.clip(np.max(np.maximum(upper20, lower20)) / c, 0.0, 0.5))
        return {
            "ret_15m": float(ret_15m), "ret_1h": float(ret_1h), "ret_4h": float(ret_4h),
            "ema_slope": float(ema_slope), "rsi15m": float(rsi15m),
            "atr_pct": float(atr_pct), "vol_1h": float(vol_1h),
            "below_ema20": 1.0 if c < ema20 else 0.0,
            "wick_density_20": wick_density_20, "last_wick_ratio": last_wick_ratio,
            "upper_wick_5": upper_wick_5, "lower_wick_5": lower_wick_5,
            "spike_mag_20": spike_mag_20,
        }
    except Exception as e:
        logger.debug(f"[ScalpMeta] kline 特征计算失败: {e}")
        return {}


def _load_kline_frame(symbols: List[str]) -> Dict[str, Any]:
    """从 alpha_market.crypto_klines 加载各币 15m K线(供训练期特征构建)。"""
    import psycopg
    try:
        with psycopg.connect(
            "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, timestamp, open_price, high_price, low_price, close_price, volume "
                    "FROM crypto_klines WHERE period='15m' AND symbol = ANY(%s) "
                    "ORDER BY symbol, timestamp",
                    (list(symbols),))
                out: Dict[str, List] = {}
                for sym, ts, o, h, l, c, v in cur.fetchall():
                    out.setdefault(sym, []).append(
                        (int(ts), float(o or 0), float(h or 0), float(l or 0), float(c or 0), float(v or 0)))
        for sym in out:
            out[sym] = np.array(out[sym], dtype=np.float64)
        return out
    except Exception as e:
        logger.warning(f"[ScalpMeta] kline 帧加载失败: {e}")
        return {}


def _build_kline_frame(rows: List[Dict[str, Any]], kl: Dict[str, Any]) -> Dict[int, Dict[str, float]]:
    """按信号 ts 从 15m K线取入场时刻趋势特征(仅用 ts 之前的 bar,无前视)。"""
    import pandas as pd
    out: Dict[int, Dict[str, float]] = {}
    if not kl:
        return out
    by_sym: Dict[str, List[int]] = {}
    for i, r in enumerate(rows):
        by_sym.setdefault(r["symbol"], []).append(i)
    for sym, idxs in by_sym.items():
        arr = kl.get(sym)
        if arr is None or len(arr) < 40:
            continue
        kts = arr[:, 0]
        closes_all = arr[:, 4]
        ema20_all = pd.Series(closes_all).ewm(span=20, adjust=False).mean().values
        d = pd.Series(closes_all).diff()
        up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean().values
        dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean().values
        rsi_all = 100 - 100 / (1 + up / np.where(dn <= 0, 1e-9, dn))
        tr_all = np.maximum(arr[1:, 2] - arr[1:, 3],
                            np.maximum(np.abs(arr[1:, 2] - arr[:-1, 4]),
                                       np.abs(arr[1:, 3] - arr[:-1, 4])))
        atr_all = np.concatenate([[np.nan], pd.Series(tr_all).rolling(14).mean().values])
        for i in idxs:
            ts_i = rows[i]["ts"]
            j = int(np.searchsorted(kts, ts_i, side="right")) - 1
            if j < 39:
                continue
            c = closes_all[j]
            if c <= 0:
                continue
            seg = closes_all[j - 4:j + 1]
            # ── 插针/影线特征(与推理侧 compute_kline_feats 同源,仅用 ts 之前的 bar,无前视) ──
            _o20 = arr[j - 19:j + 1, 1]
            _h20 = arr[j - 19:j + 1, 2]
            _l20 = arr[j - 19:j + 1, 3]
            _c20 = arr[j - 19:j + 1, 4]
            _body20 = np.abs(_c20 - _o20)
            _upper20 = _h20 - np.maximum(_o20, _c20)
            _lower20 = np.minimum(_o20, _c20) - _l20
            _wr20 = np.maximum(_upper20, _lower20) / (_body20 + 1e-10)
            _wick_feats = {
                "wick_density_20": float((_wr20 > 3.0).mean()),
                "last_wick_ratio": float(np.clip(_wr20[-1], 0.0, 50.0)),
                "upper_wick_5": float(np.clip(
                    np.mean(_upper20[-5:] / (_body20[-5:] + 1e-10)), 0.0, 20.0)),
                "lower_wick_5": float(np.clip(
                    np.mean(_lower20[-5:] / (_body20[-5:] + 1e-10)), 0.0, 20.0)),
                "spike_mag_20": float(np.clip(
                    np.max(np.maximum(_upper20, _lower20)) / c, 0.0, 0.5)),
            }
            out[i] = {
                "ret_15m": float(c / closes_all[j - 1] - 1),
                "ret_1h": float(c / closes_all[j - 4] - 1),
                "ret_4h": float(c / closes_all[j - 16] - 1),
                "ema_slope": float((c - ema20_all[j]) / ema20_all[j]) if ema20_all[j] > 0 else 0.0,
                "rsi15m": float(rsi_all[j]),
                "atr_pct": float(atr_all[j] / c) if not np.isnan(atr_all[j]) else 0.0,
                "vol_1h": float(np.std(np.diff(seg)) / c),
                "below_ema20": 1.0 if c < ema20_all[j] else 0.0,
                **_wick_feats,
            }
    return out


def _build_regime_frame(rows: List[Dict[str, Any]]) -> Optional[Dict[int, Dict[str, float]]]:
    """全流逐币计算滚动状态(按 rows 的当前下标索引输出)。

    无前视约束:行 i 只纳入 j<i 且 settle[j] <= ts[i] 的前序信号。
    复杂度 O(n log n)(searchsorted),35 万行 ~ 秒级。
    """
    if not rows:
        return None
    order = sorted(range(len(rows)), key=lambda i: (rows[i]["symbol"], rows[i]["ts"]))
    out: Dict[int, Dict[str, float]] = {}
    span = _regime_win_span()

    for sym in sorted({r["symbol"] for r in rows}):
        idx = [i for i in order if rows[i]["symbol"] == sym]
        ts = np.array([rows[i]["ts"] for i in idx], dtype=np.float64)
        settle = np.array([rows[i]["settle"] for i in idx], dtype=np.float64)
        wins = np.array([rows[i]["win"] for i in idx], dtype=np.float64)
        fwd = np.array([rows[i]["fwd_ret"] for i in idx], dtype=np.float64)
        rsi = np.array([_feat_float(rows[i]["feats"], _RSI_KEYS) for i in idx], dtype=np.float64)
        cvd = np.array([_feat_float(rows[i]["feats"], _CVD_KEYS) for i in idx], dtype=np.float64)
        # settle_ts 非单调(快止盈单结算早于更早开仓的亏损单),不能用 searchsorted。
        # 改为有界回溯 + 布尔掩码:只看最近 K 条前序信号中已结算者,取其中最近 span 条。
        _lookback = int(os.getenv("SCALP_META_REGIME_LOOKBACK", "400") or 400)
        for i in range(len(idx)):
            if i == 0:
                continue
            lo_i = max(0, i - _lookback)
            mature_mask = settle[lo_i:i] <= ts[i]
            n_mature = int(mature_mask.sum())
            if n_mature < 5:
                continue
            win_slice = wins[lo_i:i][mature_mask][-span:]
            fwd_slice = fwd[lo_i:i][mature_mask][-span:]
            rsi_slice = rsi[lo_i:i][mature_mask]
            cvd_slice = cvd[lo_i:i][mature_mask]
            rsi_slice = rsi_slice[~np.isnan(rsi_slice)][-10:]
            cvd_slice = cvd_slice[~np.isnan(cvd_slice)][-10:]
            feats: Dict[str, float] = {
                "roll_wr20": float(win_slice.mean()),
                "roll_fwd20": float(fwd_slice.mean()),
            }
            if len(rsi_slice) >= 5:
                feats["roll_rsi10"] = float(rsi_slice.mean())
            if len(cvd_slice) >= 5:
                feats["roll_cvd10"] = float(cvd_slice.mean())
            feats["sec_since_sig"] = float(ts[i] - ts[i - 1])
            out[idx[i]] = feats
    return out


def _refresh_regime_state() -> Dict[str, Any]:
    """轻量任务:从 DB 刷新币种级最新滚动状态,写 data/scalp_regime_state.json。

    供 predict_win_prob 在推理时合并 regime 特征(文件读取,零 DB 开销)。
    运行频次由调度方控制(建议小时级;状态基于 settle_ts,天然无前视)。
    """
    rows = _load_settled_rows()
    frame = _build_regime_frame(rows)
    state: Dict[str, Any] = {"updated_ts": int(time.time()), "symbols": {}}
    if frame:
        for i, feats in frame.items():
            sym = rows[i]["symbol"]
            state["symbols"][sym] = {
                k: round(float(v), 6) for k, v in feats.items()
                if k in ("roll_wr20", "roll_fwd20", "roll_rsi10", "roll_cvd10")
            }
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_REGIME_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[ScalpMeta] regime 状态写入失败: {e}")
    return state


def _build_matrix(rows: List[Dict[str, Any]], frame: Optional[Dict[int, Dict[str, float]]],
                   kline_frame: Optional[Dict[int, Dict[str, float]]] = None) -> Tuple:
    """把不定键的因子快照对齐成统一特征矩阵。返回 X, y, ts, net, feature_cols。

    rows 已去重;frame/kline_frame 按 rows 当前下标索引(由调用方重映射)。
    """
    n = len(rows)
    key_count: Dict[str, int] = {}
    for r in rows:
        seen = set()
        for k, v in r["feats"].items():
            if _numeric(v) is not None and k not in seen:
                key_count[k] = key_count.get(k, 0) + 1
                seen.add(k)
    freq_min = _feature_freq_min()
    # [2026-08-29 P2.6] 噪声特征剔除：30 天 29.3 万已结算信号校准证明
    # factor_score/composite 对费后净收益无区分力（全桶负、无单调性），
    # 且 importance 榜首全是市场状态特征。默认从特征集中移除，防止模型
    # 把噪声当信号。SCALP_META_KEEP_NOISE_FEATURES=true 回滚（保留旧特征集）。
    _keep_noise = (os.getenv("SCALP_META_KEEP_NOISE_FEATURES", "false") or "").strip().lower() in (
        "1", "true", "yes", "on")
    _drop = set() if _keep_noise else {"factor_score", "composite"}
    snap_cols = sorted([k for k, c in key_count.items() if c / n >= freq_min and k not in _drop])
    feature_cols = (
        (["factor_score"] if "factor_score" not in _drop else []) + ["dir_sign"] + snap_cols
    )
    if _regime_features_enabled():
        feature_cols = feature_cols + list(REGIME_COLS)
    feature_cols = feature_cols + list(KLINE_COLS)
    feature_cols = feature_cols + list(WICK_COLS)
    feature_cols = feature_cols + list(KLINE_INTER_COLS)

    X = np.zeros((n, len(feature_cols)), dtype=np.float64)
    y = np.zeros(n, dtype=int)
    ts = np.zeros(n, dtype=np.int64)
    net = np.zeros(n, dtype=np.float64)
    _idx_score = feature_cols.index("factor_score") if "factor_score" in feature_cols else None
    _idx_dir = feature_cols.index("dir_sign")
    _snap_idx = {k: feature_cols.index(k) for k in snap_cols}
    for i, r in enumerate(rows):
        if _idx_score is not None:
            X[i, _idx_score] = r["factor_score"]
        X[i, _idx_dir] = 1.0 if r["direction"] == "long" else (-1.0 if r["direction"] == "short" else 0.0)
        for k, j in _snap_idx.items():
            fv = _numeric(r["feats"].get(k))
            X[i, j] = fv if fv is not None else 0.0
        if _regime_features_enabled() and frame and i in frame:
            for k, v in frame[i].items():
                if k in REGIME_COLS:
                    X[i, feature_cols.index(k)] = v
        if kline_frame and i in kline_frame:
            for k, v in kline_frame[i].items():
                if k in KLINE_COLS or k in WICK_COLS:
                    X[i, feature_cols.index(k)] = v
            _kf = kline_frame[i]
            if "dir_x_ema" in feature_cols:
                X[i, feature_cols.index("dir_x_ema")] = X[i, _idx_dir] * _kf.get("ema_slope", 0.0)
            if "dir_x_ret1h" in feature_cols:
                X[i, feature_cols.index("dir_x_ret1h")] = X[i, _idx_dir] * _kf.get("ret_1h", 0.0)
            if "dir_x_ret4h" in feature_cols:
                X[i, feature_cols.index("dir_x_ret4h")] = X[i, _idx_dir] * _kf.get("ret_4h", 0.0)
            if "dir_x_wick_asym" in feature_cols:
                _wick_asym = _kf.get("lower_wick_5", 0.0) - _kf.get("upper_wick_5", 0.0)
                X[i, feature_cols.index("dir_x_wick_asym")] = X[i, _idx_dir] * _wick_asym
        ca = r.get("created_at")
        if ca is not None:
            if "hour" in feature_cols:
                X[i, feature_cols.index("hour")] = float(ca.hour)
            if "weekday" in feature_cols:
                X[i, feature_cols.index("weekday")] = float(ca.weekday())
        if _label_mode() == "fwd":
            y[i] = 1 if r["fwd_ret"] > 0 else 0
        else:
            y[i] = r["win"]
        ts[i] = r["ts"]
        net[i] = r["net_ret"]
    return X, y, ts, net, feature_cols


# ============================================================
# 训练 + 样本外验证
# ============================================================
def train_and_validate() -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "ts": int(time.time()), "usable": False,
        "v2_regime_features": _regime_features_enabled(), "label": _label_mode(),
    }
    try:
        rows = _load_settled_rows()
    except Exception as e:
        logger.warning(f"[ScalpMeta] 读取信号失败: {e}")
        report.update({"status": "error", "error": str(e)})
        _write_report(report)
        return report

    n_raw = len(rows)
    # v2: 先在全流上计算 regime 帧(滚动上下文完整),再对训练行去重并重映射下标
    try:
        full_frame = _build_regime_frame(rows) if _regime_features_enabled() else None
    except Exception as e:
        logger.warning(f"[ScalpMeta] regime 帧计算失败(降级为 v1 特征): {e}")
        full_frame = None
    if full_frame is not None:
        for i, r in enumerate(rows):
            r["_orig_idx"] = i
        rows = _dedup_rows(rows)
        frame: Optional[Dict[int, Dict[str, float]]] = {}
        for new_i, r in enumerate(rows):
            oi = r.get("_orig_idx")
            if oi is not None and oi in full_frame:
                frame[new_i] = full_frame[oi]
    else:
        rows = _dedup_rows(rows)
        frame = None

    n = len(rows)
    report["n_settled_raw"] = n_raw
    report["n_settled"] = n
    report["dedup_sec"] = _dedup_sec()
    need = _min_samples()
    if n < need:
        report.update({"status": "insufficient", "have": n, "need": need,
                       "note": f"真实结算信号 {n} 条 < 门槛 {need}，继续采集中"})
        logger.info(f"[ScalpMeta] 样本不足({n}/{need})，跳过训练，继续采集")
        _write_report(report)
        return report

    # v3: 加载 15m K线趋势帧(入场时刻,无前视)
    try:
        kline_frame = _build_kline_frame(rows, _load_kline_frame(sorted({r["symbol"] for r in rows})))
    except Exception as e:
        logger.warning(f"[ScalpMeta] kline 帧构建失败(降级): {e}")
        kline_frame = None

    X, y, ts, net, feature_cols = _build_matrix(rows, frame, kline_frame=kline_frame)
    # [2026-09-16 插针行情训练] 插针环境样本加权: wick_density_20 越高权重越大,
    # 让 LightGBM 优先拟合插针行情下的胜率结构(而不是被海量震荡市样本淹没)。
    # 只作用于训练拟合;验证/OOS 指标不加权,usable 门槛仍按真实分布判定。
    # SCALP_META_WICK_SAMPLE_WEIGHT=0 关闭。权重公式: 1 + W * wick_density_20。
    _wick_w = float(os.getenv("SCALP_META_WICK_SAMPLE_WEIGHT", "3.0") or 3.0)
    sw = None
    if _wick_w > 0 and "wick_density_20" in feature_cols:
        sw = 1.0 + _wick_w * X[:, feature_cols.index("wick_density_20")]
    report["wick_sample_weight"] = _wick_w
    pos, neg = int(y.sum()), int((1 - y).sum())
    report["pos"], report["neg"] = pos, neg
    mpc = _min_per_class()
    if pos < mpc or neg < mpc:
        report.update({"status": "imbalanced", "have_pos": pos, "have_neg": neg,
                       "need_per_class": mpc,
                       "note": f"某类样本不足(赢{pos}/亏{neg}，各需≥{mpc})，跳过"})
        logger.info(f"[ScalpMeta] 类别不足(赢{pos}/亏{neg})，跳过训练")
        _write_report(report)
        return report

    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import roc_auc_score
        import lightgbm as lgb
    except Exception as e:
        report.update({"status": "no_deps", "error": str(e)})
        _write_report(report)
        return report

    n_folds = _n_folds()
    edges = np.quantile(ts, np.linspace(0, 1, n_folds + 2))
    lgb_aucs, log_aucs = [], []
    fi = np.zeros(len(feature_cols))
    oos_p, oos_y, oos_net = [], [], []
    base_wr = float(y.mean())
    base_ev = float(net.mean())

    def _mk():
        return lgb.LGBMClassifier(
            n_estimators=400, learning_rate=0.02, num_leaves=16, max_depth=4,
            min_child_samples=60, subsample=0.8, colsample_bytree=0.7,
            reg_lambda=5.0, reg_alpha=1.0, random_state=42, n_jobs=-1, verbose=-1)

    for k in range(n_folds):
        tr = ts < edges[k + 1]
        te = (ts >= edges[k + 1]) & (ts < edges[k + 2])
        if tr.sum() < max(200, need // (n_folds + 2)) or te.sum() < 80:
            continue
        if len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 2:
            continue
        clf = _mk().fit(X[tr], y[tr], sample_weight=(None if sw is None else sw[tr]))
        p = clf.predict_proba(X[te])[:, 1]
        fi += clf.feature_importances_
        lgb_aucs.append(roc_auc_score(y[te], p))
        try:
            sc = StandardScaler().fit(X[tr])
            log = LogisticRegression(max_iter=1000, C=0.5).fit(sc.transform(X[tr]), y[tr])
            log_aucs.append(roc_auc_score(y[te], log.predict_proba(sc.transform(X[te]))[:, 1]))
        except Exception:
            pass
        oos_p.append(p); oos_y.append(y[te]); oos_net.append(net[te])

    if not oos_p:
        report.update({"status": "no_valid_folds",
                       "note": "有效折不足（数据时间跨度太窄），继续采集"})
        _write_report(report)
        return report

    p = np.concatenate(oos_p); yy = np.concatenate(oos_y); nn = np.concatenate(oos_net)
    oos_auc = float(np.mean(lgb_aucs))
    lin_auc = float(np.mean(log_aucs)) if log_aucs else None

    def _filt(q):
        thr = np.quantile(p, q)
        m = p >= thr
        if m.sum() < 20:
            return None
        return {"coverage": float(m.mean()), "win_rate": float(yy[m].mean()),
                "net_ret": float(nn[m].mean()), "n": int(m.sum())}

    filt30 = _filt(0.70)
    filt15 = _filt(0.85)

    fis = sorted(zip(feature_cols, (fi / max(1, len(oos_p))).tolist()), key=lambda x: -x[1])
    tot = sum(v for _, v in fis) + 1e-12
    top_importance = [{"name": nm, "importance": round(v / tot, 4)} for nm, v in fis[:20]]

    report.update({
        "status": "trained",
        "features": len(feature_cols),
        "feature_cols": feature_cols,
        "oos_auc_lgbm": round(oos_auc, 4),
        "oos_auc_linear": round(lin_auc, 4) if lin_auc is not None else None,
        "baseline": {"win_rate": round(base_wr, 4), "net_ret": round(base_ev, 6)},
        "filter_top30pct": filt30,
        "filter_top15pct": filt15,
        "top_importance": top_importance,
    })

    gate_auc = _gate_min_auc()
    usable = False
    reasons = []
    if oos_auc < gate_auc:
        reasons.append(f"AUC {oos_auc:.3f} < 门槛 {gate_auc}")
    ref = filt30 or filt15
    if ref is None:
        reasons.append("无有效过滤样本")
    else:
        if ref["net_ret"] <= base_ev:
            reasons.append(f"过滤后净收益 {ref['net_ret']:.4%} 未超基线 {base_ev:.4%}")
        if ref["net_ret"] <= 0:
            reasons.append(f"过滤后净收益仍为负 {ref['net_ret']:.4%}")
    if not reasons:
        usable = True
    report["usable"] = usable
    report["gate_reasons"] = reasons

    try:
        import joblib
        final = _mk().fit(X, y, sample_weight=sw)
        os.makedirs(_DATA_DIR, exist_ok=True)
        joblib.dump({
            "model": final, "feature_cols": feature_cols,
            "meta": {"trained_ts": report["ts"], "n": n, "usable": usable,
                     "oos_auc": oos_auc, "gate_reasons": reasons,
                     "regime_features": _regime_features_enabled()},
        }, _MODEL_PATH)
        report["model_path"] = _MODEL_PATH
    except Exception as e:
        logger.warning(f"[ScalpMeta] 保存模型失败: {e}")
        report["model_save_error"] = str(e)

    # v2: 训练完成后顺手刷新 regime 状态文件(推理用)
    if _regime_features_enabled():
        try:
            _refresh_regime_state()
            report["regime_state_updated"] = True
        except Exception as e:
            logger.warning(f"[ScalpMeta] regime 状态刷新失败: {e}")

    _write_report(report)
    if filt30:
        _f30 = "{:.1%}/{:.4%}".format(filt30["win_rate"], filt30["net_ret"])
    else:
        _f30 = "-"
    _reason_str = ("原因:" + ";".join(reasons)) if reasons else ""
    logger.info(
        "[ScalpMeta] 训练完成 n=%d OOS_AUC=%.3f 基线胜率=%.1f%% 过滤前30%%(胜率/净收益)=%s usable=%s %s",
        n, oos_auc, base_wr * 100, _f30, usable, _reason_str,
    )
    return report


def _write_report(report: Dict[str, Any]) -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.debug(f"[ScalpMeta] 写报告失败: {e}")


def sample_progress() -> Dict[str, Any]:
    """实时查询"离达标还差多少"（去重后的独立样本数），不训练。供前端进度条。"""
    try:
        rows = _load_settled_rows()
    except Exception as e:
        return {"error": str(e)}
    raw = len(rows)
    dedup = _dedup_rows(rows)
    have = len(dedup)
    pos = sum(1 for r in dedup if r["win"] == 1)
    neg = have - pos
    need = _min_samples()
    return {
        "raw": raw, "have": have, "need": need, "pos": pos, "neg": neg,
        "need_per_class": _min_per_class(), "dedup_sec": _dedup_sec(),
        "percent": round(min(100.0, 100.0 * have / need), 1) if need else None,
        "ready": have >= need and pos >= _min_per_class() and neg >= _min_per_class(),
    }


def get_report() -> Dict[str, Any]:
    try:
        if os.path.exists(_REPORT_PATH):
            with open(_REPORT_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"status": "no_report"}


# ============================================================
# 推理接口
# ============================================================
_MODEL_CACHE: Dict[str, Any] = {"mtime": 0, "obj": None}
_REGIME_CACHE: Dict[str, Any] = {"mtime": 0, "obj": {}}


def _regime_features_for_symbol(symbol: str) -> Dict[str, float]:
    """读取 regime 状态文件,返回该币的滚动特征(无则空)。"""
    try:
        if not os.path.exists(_REGIME_STATE_PATH):
            return {}
        mt = os.path.getmtime(_REGIME_STATE_PATH)
        if _REGIME_CACHE["obj"] and mt == _REGIME_CACHE["mtime"]:
            state = _REGIME_CACHE["obj"]
        else:
            with open(_REGIME_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            _REGIME_CACHE["obj"] = state
            _REGIME_CACHE["mtime"] = mt
        syms = state.get("symbols") or {}
        if time.time() - float(state.get("updated_ts") or 0) > 7200:
            return {}  # 状态过期,不注入(安全降级)
        return {k: float(v) for k, v in (syms.get(str(symbol).upper()) or {}).items()}
    except Exception:
        return {}


def meta_model_usable() -> Optional[bool]:
    """模型自评是否可用（OOS AUC/净利润门槛），供仲裁层决策。
    复用 _MODEL_CACHE（mtime 失效重载）；模型不存在返回 None。"""
    try:
        if not os.path.exists(_MODEL_PATH):
            return None
        mt = os.path.getmtime(_MODEL_PATH)
        if _MODEL_CACHE["obj"] is None or mt != _MODEL_CACHE["mtime"]:
            import joblib
            _MODEL_CACHE["obj"] = joblib.load(_MODEL_PATH)
            _MODEL_CACHE["mtime"] = mt
        _meta = (_MODEL_CACHE["obj"].get("meta") or {}) if isinstance(_MODEL_CACHE["obj"], dict) else {}
        return bool(_meta.get("usable"))
    except Exception:
        return None


def predict_win_prob(
    features: Dict[str, Any],
    *,
    require_usable: bool = True,
    kline_feats: Optional[Dict[str, float]] = None,
) -> Optional[float]:
    """给单个信号的因子快照打"会赢"概率。模型不存在/不可用则返回 None。

    v2: 若模型特征集包含 regime 特征,自动从状态文件合并币种滚动特征;
    状态缺失时对应特征置 0(与训练缺省一致)。
    """
    try:
        if not os.path.exists(_MODEL_PATH):
            return None
        mt = os.path.getmtime(_MODEL_PATH)
        if _MODEL_CACHE["obj"] is None or mt != _MODEL_CACHE["mtime"]:
            import joblib
            _MODEL_CACHE["obj"] = joblib.load(_MODEL_PATH)
            _MODEL_CACHE["mtime"] = mt
        bundle = _MODEL_CACHE["obj"]
        meta = bundle.get("meta") or {}
        if require_usable and not bool(meta.get("usable")):
            return None
        cols = bundle["feature_cols"]
        symbol = str(features.get("symbol") or "")
        reg_feats = _regime_features_for_symbol(symbol) if meta.get("regime_features") else {}
        kf = kline_feats if isinstance(kline_feats, dict) else {}
        x = np.zeros((1, len(cols)), dtype=np.float64)
        # [2026-08-29 P2.6] dir_sign 下标命名化：噪声特征剔除后 factor_score
        # 可能不在列首，旧的 x[0,1] 硬编码会错位。
        _dir_j = cols.index("dir_sign") if "dir_sign" in cols else None
        for j, c in enumerate(cols):
            if c == "factor_score":
                x[0, j] = _numeric(features.get("factor_score")) or 0.0
            elif c == "dir_sign":
                d = str(features.get("direction") or "")
                x[0, j] = 1.0 if d == "long" else (-1.0 if d == "short" else 0.0)
            elif c in reg_feats:
                x[0, j] = reg_feats[c]
            elif c in kf:
                x[0, j] = _numeric(kf.get(c)) or 0.0
            elif c == "dir_x_ema":
                x[0, j] = (x[0, _dir_j] if _dir_j is not None else 0.0) * (_numeric(kf.get("ema_slope")) or 0.0)
            elif c == "dir_x_ret1h":
                x[0, j] = (x[0, _dir_j] if _dir_j is not None else 0.0) * (_numeric(kf.get("ret_1h")) or 0.0)
            elif c == "dir_x_ret4h":
                x[0, j] = (x[0, _dir_j] if _dir_j is not None else 0.0) * (_numeric(kf.get("ret_4h")) or 0.0)
            elif c == "dir_x_wick_asym":
                _wick_asym = (_numeric(kf.get("lower_wick_5")) or 0.0) - (_numeric(kf.get("upper_wick_5")) or 0.0)
                x[0, j] = (x[0, _dir_j] if _dir_j is not None else 0.0) * _wick_asym
            else:
                x[0, j] = _numeric(features.get(c)) or 0.0
        return float(bundle["model"].predict_proba(x)[0, 1])
    except Exception as e:
        logger.debug(f"[ScalpMeta] predict 跳过: {e}")
        return None


def refresh_regime_state() -> Dict[str, Any]:
    """对外刷新接口:供调度任务小时级调用。"""
    return _refresh_regime_state()