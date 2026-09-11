"""中线因子池 · Regime 条件化自适应权重（2026-09-07 · 用户拍板「建」）。

治「池子过期」的本：
- 旧问题：factor_runtime_weights.json 缺失 → 中线 4 因子永远等权 1.0，
  过期因子不降权、新鲜因子不提权，只能手动换血（且候选已过期，换不动）。
- 本模块：每个活跃中线因子按【当前市场 regime】计算近端滚动 IC（价格口径，
  不依赖 scalp 已死导致的 SignalTradeFeedback 断流），写成权重文件。
  过期因子权重自动→地板（软退役），有效因子提权。regime 切换时权重自动重组。

权重语义（与消费端 resolve_combo_weights 匹配）：权重 = 因子投票乘数，
[0.1, 2.0]；0.1 = 本 regime 失信（软退役），2.0 = 本 regime 强有效。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_WEIGHTS_REL = os.path.join("backend", "data", "factor_runtime_weights.json")

# regime 判定阈值（4h 级别，纯规则无前视）
_EXTREME_VOL_MULT = 1.8   # 波动 > 1.8×中位 → 极端市
_TREND_SEP_PCT = 0.015    # |EMA20-EMA50|/价 > 1.5% → 趋势市
_FWD_4H = 6               # 4h 前瞻 6 根（=24h），与 FACTOR_SCORER_MIDLONG_FWD_4H 一致


def _weights_path() -> str:
    # 与读路径 factor_ic_evaluator.RUNTIME_WEIGHTS_FILE 一致：仓库根 data/（非 backend/data）。
    # 本文件在 backend/services/factor_engine/ → parents[3] = 仓库根。
    from pathlib import Path
    return str(Path(__file__).resolve().parents[3] / "data" / "factor_runtime_weights.json")


def _regime_of_bars(closes: np.ndarray) -> np.ndarray:
    """给每根 bar 打 regime 标签（trending/ranging/extreme），严格因果（只用截至当根数据）。"""
    n = len(closes)
    out = np.array(["ranging"] * n, dtype=object)
    if n < 60:
        return out
    import pandas as pd
    c = pd.Series(closes)
    ema20 = c.ewm(span=20, adjust=False).mean().to_numpy()
    ema50 = c.ewm(span=50, adjust=False).mean().to_numpy()
    ret = c.pct_change().to_numpy()
    # 滚动波动（20 根）与其中位数
    vol = pd.Series(ret).rolling(20).std().to_numpy()
    vol_med = pd.Series(vol).rolling(120, min_periods=30).median().to_numpy()
    for i in range(n):
        if not np.isfinite(vol[i]) or closes[i] <= 0:
            continue
        sep = abs(ema20[i] - ema50[i]) / closes[i] if np.isfinite(ema20[i]) and np.isfinite(ema50[i]) else 0.0
        vm = vol_med[i] if np.isfinite(vol_med[i]) and vol_med[i] > 0 else None
        if vm is not None and vol[i] > _EXTREME_VOL_MULT * vm:
            out[i] = "extreme"
        elif sep > _TREND_SEP_PCT:
            out[i] = "trending"
        else:
            out[i] = "ranging"
    return out


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman 秩相关（无 scipy 依赖）。"""
    if len(a) < 30:
        return 0.0
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean(); rb -= rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d > 0 else 0.0


def _factor_series(calc, registry_fid: str, df, sym: str, timeframe: str) -> Optional[np.ndarray]:
    """计算因子在某币的 4h 信号序列（复用 midlong_registry_factors 的富化/回退）。"""
    try:
        from backend.services.factor_engine.midlong_registry_factors import (
            _enrich_flow_history, _flow_series, _rolling_recompute,
        )
        df = _enrich_flow_history(df, sym, timeframe)
        flow = _flow_series(registry_fid, df)
        if flow is not None:
            return np.asarray(flow, dtype=float)
        series_map = calc.calculate([registry_fid], df, symbol=sym, timeframe=timeframe)
        series = series_map.get(registry_fid)
        v = np.asarray(series, dtype=float) if series is not None and len(series) else np.zeros(0)
        if int(np.isfinite(v).sum()) < max(60, int(len(df) * 0.05)):
            v = _rolling_recompute(calc, registry_fid, df, sym, timeframe, _FWD_4H)
        return v
    except Exception as exc:
        logger.debug("[RegimeWeights] %s/%s 序列计算失败: %s", registry_fid, sym, exc)
        return None


def compute_regime_weights(
    *,
    symbols: tuple = ("BTC", "ETH", "SOL"),
    timeframe: str = "4h",
    lookback_days: int = 14,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """对每个活跃中线因子算 per-regime 近端 IC，按当前 regime 写权重。

    返回 {factor_id: weight, ...} + 诊断。dry_run=True 只算不写。
    """
    from backend.services.factor_engine.custom_factor_store import custom_factor_store
    from backend.services.factor_engine.factor_calculator import FactorCalculator
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
    from backend.services.coin_select_platform_service import resolve_admin_tenant_id

    tid = resolve_admin_tenant_id()
    active = [
        r for r in custom_factor_store.list_active(tenant_id=tid)
        if str((r.get("extra") or {}).get("horizon") or "").lower() == "midlong"
        or "@" in str(r.get("factor_id") or "")
    ]
    if not active:
        return {"error": "无活跃中线因子", "weights": {}}

    # 加载根数：regime 判定的滚动统计（EMA50/波动中位）需要长热身，且 _load_klines
    # 最低 120 根、4h 中线口径 500 根。加载 500 根，IC 只用最近 lookback_days 窗口。
    lookback = 500
    recent_bars = int(lookback_days * 6)  # 4h 一天 6 根（近端 IC 窗口）
    calc = FactorCalculator()
    # 先定当前 regime（用 BTC 为基准）
    current_regime = "ranging"
    per_factor: Dict[str, Dict[str, Any]] = {}

    for rec in active:
        fid = str(rec.get("factor_id") or "")
        registry_fid = str((rec.get("extra") or {}).get("registry_factor_id") or fid.split("@")[0])
        tf = str((rec.get("extra") or {}).get("timeframe") or timeframe).lower()
        expected_sign = 1.0
        try:
            expected_sign = float((rec.get("scores") or {}).get("expected_sign") or 0) or (
                1.0 if float((rec.get("scores") or {}).get("ic_mean") or 0) >= 0 else -1.0
            )
        except Exception:
            expected_sign = 1.0

        regime_ics: Dict[str, List[float]] = {"trending": [], "ranging": [], "extreme": []}
        global_ics: List[float] = []
        latest_closes: Optional[np.ndarray] = None
        latest_regimes: Optional[np.ndarray] = None

        for sym in symbols:
            try:
                klines = factor_backtest_scorer._load_klines(sym, tf, lookback)
                if not klines or len(klines) < 120:
                    continue
                import pandas as pd
                df = pd.DataFrame(klines)
                vals = _factor_series(calc, registry_fid, df, sym, tf)
                if vals is None:
                    continue
                closes = df["close"].astype(float).to_numpy()
                n = min(len(vals), len(closes))
                vals, closes = vals[-n:], closes[-n:]
                if int(np.isfinite(vals).sum()) < 60:
                    continue
                regimes = _regime_of_bars(closes)
                # 前瞻收益（fwd 根）
                fwd_ret = np.full(n, np.nan)
                fwd_ret[:-_FWD_4H] = closes[_FWD_4H:] / closes[:-_FWD_4H] - 1.0
                # 有效区间：vals 有限 + fwd 有限 + 过 warmup。
                # 注：快照型因子（obv/vwap 等）滚动重算后序列稀疏（500 根仅 ~70 有限），
                # 近端 84 根窗口样本不足 → 用整个加载窗口（500 根≈83 天，已是"近期"），
                # regime 分桶保证状态条件化；每小时重算保证滚动新鲜。
                mask = np.isfinite(vals) & np.isfinite(fwd_ret) & (np.arange(n) > 60)
                if int(mask.sum()) < 30:
                    continue
                v_m, r_m, g_m = vals[mask], fwd_ret[mask], regimes[mask]
                global_ics.append(_spearman(v_m, r_m))
                for rg in ("trending", "ranging", "extreme"):
                    sub = g_m == rg
                    if int(sub.sum()) >= 30:
                        regime_ics[rg].append(_spearman(v_m[sub], r_m[sub]))
                if latest_closes is None:
                    latest_closes, latest_regimes = closes, regimes
            except Exception as exc:
                logger.debug("[RegimeWeights] %s/%s 评估失败: %s", fid, sym, exc)
                continue

        if not global_ics:
            continue
        # 当前 regime（用 BTC 序列末端）
        if latest_regimes is not None and len(latest_regimes):
            current_regime = str(latest_regimes[-1])
        g_ic = float(np.mean(global_ics))
        # 当前 regime 下的 IC（样本不足回退全局）
        r_ic_list = regime_ics.get(current_regime) or []
        r_ic = float(np.mean(r_ic_list)) if r_ic_list else g_ic

        # 权重：regime IC 与期望方向一致 → 按强度提权；背离 → 地板（软退役）
        aligned = (r_ic * expected_sign) > 0
        strength = min(1.9, abs(r_ic) / 0.05)  # IC 5% → 满权
        weight = round(float(np.clip(0.2 + strength, 0.1, 2.0)) if aligned else 0.1, 3)
        per_factor[fid] = {
            "weight": weight,
            "global_ic": round(g_ic, 4),
            "regime_ic": round(r_ic, 4),
            "regime": current_regime,
            "aligned": aligned,
            "expected_sign": expected_sign,
        }

    weights = {fid: v["weight"] for fid, v in per_factor.items()}
    result = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "lookback_days": lookback_days,
        "current_regime": current_regime,
        "weights": weights,
        "detail": per_factor,
    }
    if not dry_run and weights:
        try:
            # [2026-09-07] 合并写（非覆盖）：权重文件同时承载 scalp/1h 的 bare 键，
            # 中线 regime 权重用全 factor_id（obv@4h）键——resolve_combo_weights
            # 按 `fid in manual` 精确匹配，@4h 键才会生效。其余键原样保留。
            path = _weights_path()
            existing: Dict[str, Any] = {}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        existing = json.load(f) or {}
                except Exception:
                    existing = {}
            merged_weights = dict(existing.get("weights") or {})
            merged_weights.update(weights)  # 中线 @4h 键覆盖/新增
            existing["weights"] = merged_weights
            existing["updated_at"] = result["updated_at"]
            existing["midlong_regime"] = current_regime
            existing["midlong_detail"] = per_factor
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=1)
            logger.info(
                "[RegimeWeights] 中线权重已合并写入 regime=%s: %s",
                current_regime, json.dumps(weights, ensure_ascii=False),
            )
        except Exception as exc:
            logger.warning("[RegimeWeights] 权重写入失败: %s", exc)
    return result
