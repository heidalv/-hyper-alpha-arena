"""
ML 全激活编排服务（整改 #4 / #10 / #17 / #18 主路径接线）。

在 learning_loop 维护周期异步触发（G4：离峰线程，不阻塞交易热路径）：
  - ContinualTrainingPipeline（#10）滚动重训
  - LearnedFactorWeighting（#4）因子学习层重训
  - DDGDA reweight_training_data（#18）重训前样本预加权
  - EWC / ReplayAugmentedTrainer（#17）防遗忘回放

零风险：ML_PIPELINE_ENABLED=false 时整模块 no-op。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_ML_SYMBOLS = ("BTC", "ETH", "SOL")
_ML_TIERS = ("short", "mid")
_ML_TIMEFRAME = "15m"
# [2026-08-09 深度模型窗口扩充] 400 根 15m（≈4 天）对滚动窗口训练不足（train 90 天
# 需 ~8640 根），先扩到 2400 根（≈25 天）供 GRU 深度模型可用；后续数据管道加深后
# 再按 RollingWindowConfig 对齐到完整 90 天训练窗。
_KLINE_LIMIT = 2400
_FACTOR_STEP = 5  # 每 N 根 K 线采一次因子快照（维护线程可接受）

# 深度模型（pytorch_gru）独立维护通道：与 lightgbm 主通道同窗同特征并行训练/对照
_ML_DEEP_TIERS = ("short", "mid")

_stats: Dict[str, Any] = {
    "enabled": False,
    "last_tick_ts": 0.0,
    "last_run_ts": 0.0,
    "last_error": "",
    "continual_retrains": 0,
    "learned_retrains": 0,
    "deep_retrains": 0,
    "symbols_processed": 0,
    "in_flight": False,
}
_lock = threading.Lock()
_last_run_mono = 0.0

_pipeline = None
_learned = None
_deep_pipeline = None
_replay_buffer = None
_replay_trainer = None
_ewc_trainer = None


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def is_ml_activation_enabled() -> bool:
    return _env_bool("ML_PIPELINE_ENABLED", True)


def get_activation_stats() -> Dict[str, Any]:
    with _lock:
        out = dict(_stats)
    out["pipeline_enabled"] = is_ml_activation_enabled()
    out["enabled"] = out["pipeline_enabled"]
    return out


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from backend.services.ml.training_pipeline import ContinualTrainingPipeline

        _pipeline = ContinualTrainingPipeline()
    return _pipeline


def _get_learned():
    global _learned
    if _learned is None:
        from backend.services.factor_engine.learned_weighting import (
            LearnedFactorWeighting,
            LearnedWeightingConfig,
        )

        _learned = LearnedFactorWeighting(LearnedWeightingConfig())
    return _learned


def _get_deep_pipeline():
    """深度模型（pytorch_gru）独立重训管线：与 lightgbm 主通道并行维护。"""
    global _deep_pipeline
    if _deep_pipeline is None:
        from backend.services.ml.training_pipeline import ContinualTrainingPipeline

        _deep_pipeline = ContinualTrainingPipeline(model_type="pytorch_gru")
    return _deep_pipeline


def is_deep_model_enabled() -> bool:
    return _env_bool("ML_DEEP_MODEL_ENABLED", True)


def _get_continual_helpers():
    global _replay_buffer, _replay_trainer, _ewc_trainer
    if _replay_buffer is None:
        from backend.services.learning_core.continual_learning import (
            EWCTrainer,
            RegimeReplayBuffer,
            ReplayAugmentedTrainer,
            compute_fisher_from_importance,
            is_enabled as ewc_enabled,
        )

        _replay_buffer = RegimeReplayBuffer()
        _replay_trainer = ReplayAugmentedTrainer()
        _ewc_trainer = EWCTrainer()
    return _replay_buffer, _replay_trainer, _ewc_trainer


def _load_klines_df(symbol: str) -> Optional[pd.DataFrame]:
    try:
        from backend.services.kline_data_service import kline_service

        raw = kline_service.get_klines_from_db(
            symbol.upper(), _ML_TIMEFRAME, _KLINE_LIMIT, exchange="hyperliquid",
        )
        if not raw or len(raw) < 60:
            return None
        df = pd.DataFrame(raw)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            df = df.set_index("timestamp").sort_index()
        elif "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
            df = df.set_index("time").sort_index()
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["close"])
    except Exception as exc:
        logger.debug("[MLActivation] %s K线加载失败: %s", symbol, exc)
        return None


def _inject_deribit(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    try:
        from backend.services.factor_engine.factor_bridge import inject_deribit_into_klines

        return inject_deribit_into_klines(df, symbol)
    except Exception:
        return df


def build_ml_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """从 K 线构造 #10 监督学习特征（轻量、无全量因子扫描）。"""
    close = df["close"].astype(float)
    vol = df["volume"].astype(float) if "volume" in df.columns else pd.Series(1.0, index=df.index)
    ret1 = close.pct_change()
    ret5 = close.pct_change(5)
    ret20 = close.pct_change(20)
    vol_ma = vol.rolling(20, min_periods=5).mean().replace(0, np.nan)
    vol_ratio = (vol / vol_ma).fillna(1.0)
    ma20 = close.rolling(20, min_periods=5).mean()
    ma_ratio = (close / ma20 - 1.0).fillna(0.0)
    hi = df["high"].astype(float) if "high" in df.columns else close
    lo = df["low"].astype(float) if "low" in df.columns else close
    hl_range = ((hi - lo) / close.replace(0, np.nan)).fillna(0.0)
    out = pd.DataFrame(
        {
            "ret_1": ret1,
            "ret_5": ret5,
            "ret_20": ret20,
            "vol_ratio": vol_ratio,
            "ma_ratio": ma_ratio,
            "hl_range": hl_range,
            "options_skew": pd.to_numeric(df["options_skew"], errors="coerce").fillna(0.0)
            if "options_skew" in df.columns else 0.0,
            "iv_term_structure": pd.to_numeric(df["iv_term_structure"], errors="coerce").fillna(0.0)
            if "iv_term_structure" in df.columns else 0.0,
            "gamma_magnet": pd.to_numeric(df["gamma_magnet"], errors="coerce").fillna(0.0)
            if "gamma_magnet" in df.columns else 0.0,
        },
        index=df.index,
    )
    return out.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def build_factor_history(
    df: pd.DataFrame,
    symbol: str,
    *,
    step: int = _FACTOR_STEP,
) -> pd.DataFrame:
    """滚动采样因子矩阵（维护线程专用，步长降采样）。"""
    from backend.services.factor_engine.base_factors import factor_engine

    rows: List[Dict[str, Any]] = []
    idxs: List[Any] = []
    sym = symbol.upper()
    n = len(df)
    if n < 30:
        return pd.DataFrame()
    for i in range(29, n, max(1, step)):
        window = df.iloc[: i + 1].copy()
        md = {"symbol": sym, "timeframe": _ML_TIMEFRAME}
        try:
            fv_map = factor_engine.compute_all_factors(window, md)
        except Exception:
            continue
        if not fv_map:
            continue
        row = {k: float(getattr(v, "normalized", getattr(v, "value", 0.0)) or 0.0) for k, v in fv_map.items()}
        rows.append(row)
        idxs.append(df.index[i])
    if not rows:
        return pd.DataFrame()
    hist = pd.DataFrame(rows, index=idxs)
    return hist.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _forward_return_labels(close: pd.Series, horizon: int = 5) -> pd.Series:
    return close.shift(-horizon) / close - 1.0


def _ddgda_sample_weights(factor_hist: pd.DataFrame, symbol: str) -> np.ndarray:
    n = len(factor_hist)
    if n <= 0:
        return np.asarray([], dtype=float)
    drift_score = 0.0
    regime = "unknown"
    try:
        from backend.services.concept_drift_detector import concept_drift_detector

        drift = concept_drift_detector.detect(symbol, min_samples=20)
        drift_score = float(drift.get("drift_score") or drift.get("score") or 0.0)
        regime = str(drift.get("regime") or drift.get("level") or "unknown")
    except Exception:
        pass
    try:
        from backend.services.learning_core.distribution_forecaster import get_forecaster

        forecaster = get_forecaster()
        mat = factor_hist.values if len(factor_hist) else np.zeros((n, 1))
        forecast = forecaster.forecast_next_distribution(mat, drift_score, regime_hint=regime)
        return forecaster.reweight_training_data(n, forecast)
    except Exception as exc:
        logger.debug("[MLActivation] DDGDA 权重失败: %s", exc)
        return np.ones(n, dtype=float)


def _apply_sample_weights_to_training(
    features: pd.DataFrame,
    labels: pd.Series,
    weights: np.ndarray,
) -> Tuple[pd.DataFrame, pd.Series, Optional[pd.Series]]:
    """按 DDGDA 权重对训练行重采样（树模型无 sample_weight 时的通用兜底）。"""
    if weights is None or len(weights) != len(features):
        return features, labels, None
    w = np.asarray(weights, dtype=float)
    if not _env_bool("DDGDA_ENABLED", False) or np.allclose(w, 1.0):
        return features, labels, pd.Series(w, index=features.index)
    aligned = features.join(labels.rename("__y__"), how="inner").dropna(subset=["__y__"])
    if aligned.empty:
        return features, labels, None
    wi = w[-len(aligned):] if len(w) >= len(aligned) else np.ones(len(aligned))
    wi = wi / max(float(np.mean(wi)), 1e-9)
    counts = np.clip(np.round(wi * 2).astype(int), 1, 5)
    parts = []
    for i in range(len(aligned)):
        parts.extend([aligned.iloc[i]] * int(counts[i]))
    if not parts:
        return features, labels, pd.Series(wi, index=aligned.index)
    boosted = pd.DataFrame(parts)
    boosted.index = pd.RangeIndex(len(boosted))
    feat_cols = [c for c in features.columns if c in boosted.columns]
    return (
        boosted[feat_cols],
        boosted["__y__"],
        pd.Series(wi, index=aligned.index),
    )


def _run_continual_for_symbol(symbol: str, df: pd.DataFrame) -> bool:
    from backend.services.ml.training_pipeline import make_forward_return_label

    feat_df = build_ml_feature_frame(df)
    feature_cols = list(feat_df.columns)
    pipeline = _get_pipeline()
    ok = False
    for tier in _ML_TIERS:
        model = pipeline.check_and_retrain(
            symbol,
            tier,
            feat_df,
            feature_cols,
            make_forward_return_label(horizon=5),
            timeframe=_ML_TIMEFRAME,
        )
        if model is not None:
            ok = True
            with _lock:
                _stats["continual_retrains"] += 1
    return ok


def _run_deep_for_symbol(symbol: str, df: pd.DataFrame) -> bool:
    """深度因子模型（Qlib 风格 GRU，GPU 优先）滚动重训通道。

    与 lightgbm 主通道同窗同特征并行维护，产出自训练深度因子模型，
    供 Phase 1 样本外 IC/ICIR 对照（验收：GRU 不劣于 lightgbm）。
    """
    if not is_deep_model_enabled():
        return False
    from backend.services.ml.training_pipeline import make_forward_return_label

    # GPU 任务全局互斥（单卡：与 DRL 训练总串行）；被占用则本次跳过（下轮 12h 周期再试）
    from backend.services.resource_guard import gpu_training_operation

    with gpu_training_operation(f"ml-deep-{symbol}") as acquired:
        if not acquired:
            logger.info("[MLActivation] %s 深度模型重训跳过：GPU 训练互斥被占用", symbol)
            return False
        feat_df = build_ml_feature_frame(df)
        feature_cols = list(feat_df.columns)
        pipeline = _get_deep_pipeline()
        ok = False
        for tier in _ML_DEEP_TIERS:
            model = pipeline.check_and_retrain(
                symbol,
                tier,
                feat_df,
                feature_cols,
                make_forward_return_label(horizon=5),
                timeframe=_ML_TIMEFRAME,
            )
            if model is not None:
                ok = True
                with _lock:
                    _stats["deep_retrains"] += 1
                logger.info("[MLActivation] %s/%s 深度模型重训完成 device=%s",
                            symbol, tier, getattr(model, "_resolve_device", lambda: "?")())
        return ok


def _persist_factor_matrix(hist: pd.DataFrame, symbol: str) -> Optional[str]:
    """因子矩阵落库（CSV，data/factor_matrices/）供重复实验与 WFO 终审。

    与模型文件（data/ml_models/）同风格：不依赖 PostgreSQL，维护周期每次
    滚动重算后全量覆盖，保留最近一次窗口快照（含时间戳索引）。
    """
    if hist is None or hist.empty:
        return None
    try:
        out_dir = os.path.join(".", "data", "factor_matrices")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{symbol.upper()}_{_ML_TIMEFRAME}.csv")
        hist.to_csv(path)
        return path
    except Exception as exc:
        logger.debug("[MLActivation] 因子矩阵落库失败 %s: %s", symbol, exc)
        return None


def _run_learned_for_symbol(symbol: str, df: pd.DataFrame) -> bool:
    from backend.services.factor_engine.learned_weighting import LearnedWeightingConfig

    cfg = LearnedWeightingConfig()
    if not cfg.enabled:
        return False
    hist = build_factor_history(df, symbol)
    if hist.empty or len(hist) < 25:
        return False
    _persist_factor_matrix(hist, symbol)  # Phase 0：因子矩阵落库供重复实验
    labels = _forward_return_labels(df["close"].astype(float))
    labels = labels.reindex(hist.index)
    weights = _ddgda_sample_weights(hist, symbol)

    replay_buf, replay_trainer, ewc_trainer = _get_continual_helpers()
    from backend.services.learning_core.continual_learning import is_enabled as ewc_enabled

    # EWC 回放：把旧样本掺入训练矩阵
    train_hist = hist
    train_labels = labels
    if ewc_enabled():
        rows = [
            {"features": hist.iloc[i].to_dict(), "label": float(labels.iloc[i]), "regime": symbol}
            for i in range(len(hist))
            if pd.notna(labels.iloc[i])
        ]
        mixed = replay_trainer.mix_batch(rows, replay_buf)
        if len(mixed) > len(rows):
            extra_feats = [pd.Series(r["features"]) for r in mixed[len(rows):]]
            extra_labels = [r["label"] for r in mixed[len(rows):]]
            if extra_feats:
                extra_df = pd.DataFrame(extra_feats)
                extra_df.index = pd.RangeIndex(len(hist), len(hist) + len(extra_df))
                train_hist = pd.concat([hist, extra_df], axis=0)
                train_labels = pd.concat(
                    [labels, pd.Series(extra_labels, index=extra_df.index)],
                )

    feat_w, lab_w, _ = _apply_sample_weights_to_training(train_hist, train_labels, weights)
    learned = _get_learned()
    ok = learned.train(feat_w, lab_w)

    if ok and ewc_enabled():
        try:
            from backend.services.learning_core.continual_learning import compute_fisher_from_importance

            imp = {c: np.array([abs(float(train_hist[c].mean()))]) for c in train_hist.columns[:8]}
            theta = {k: v for k, v in imp.items()}
            ewc_trainer.consolidate(compute_fisher_from_importance(theta, imp, task_tag=symbol))
            for i in range(len(hist)):
                replay_buf.add(
                    {"features": hist.iloc[i].to_dict(), "label": float(labels.iloc[i])},
                    regime=symbol,
                )
        except Exception as exc:
            logger.debug("[MLActivation] EWC consolidate 跳过: %s", exc)

    if ok:
        with _lock:
            _stats["learned_retrains"] += 1
        try:
            from backend.services.promotion_scan_service import apply_promotion_stage, get_candidate_stage
            if get_candidate_stage("ml_learned_weighting") == "shadow":
                apply_promotion_stage("ml_learned_weighting", "shadow", domain="factor_weighting")
        except Exception:
            pass
    return ok


def _activation_worker(session_id: str, tick: int) -> None:
    global _last_run_mono
    from backend.services.resource_guard import assert_off_hot_path

    try:
        assert_off_hot_path("ml_activation")
    except RuntimeError:
        logger.debug("[MLActivation] 热路径 defer（应由 run_ml_activation_tick 异步触发）")
        return

    t0 = time.monotonic()
    processed = 0
    try:
        with _lock:
            _stats["in_flight"] = True
            _stats["last_run_ts"] = time.time()
            _stats["last_error"] = ""

        for sym in _ML_SYMBOLS:
            df = _load_klines_df(sym)
            if df is None or df.empty:
                continue
            df = _inject_deribit(df, sym)
            _run_continual_for_symbol(sym, df)
            _run_deep_for_symbol(sym, df)
            _run_learned_for_symbol(sym, df)
            processed += 1

        _last_run_mono = time.monotonic()
        logger.info(
            "[MLActivation] 维护 tick=%d session=%s 完成 symbols=%d elapsed=%.1fs",
            tick, (session_id or "")[:12], processed, time.monotonic() - t0,
        )
    except Exception as exc:
        with _lock:
            _stats["last_error"] = str(exc)[:200]
        logger.warning("[MLActivation] 激活线程异常: %s", exc)
    finally:
        with _lock:
            _stats["in_flight"] = False
            _stats["symbols_processed"] = processed


def run_ml_activation_tick(
    session_id: str = "",
    tick: int = 0,
    *,
    is_maintenance: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """learning_loop 调用入口：维护周期异步触发 ML 全激活。"""
    enabled = is_ml_activation_enabled()
    with _lock:
        _stats["enabled"] = enabled
        _stats["last_tick_ts"] = time.time()

    if not enabled:
        return {"ok": False, "skipped": True, "reason": "ML_PIPELINE_ENABLED=false"}

    debounce_sec = max(300, int(os.environ.get("ML_ACTIVATION_DEBOUNCE_SEC", "600")))
    if not force and (time.monotonic() - _last_run_mono) < debounce_sec and not is_maintenance:
        return {"ok": True, "skipped": True, "reason": "debounce"}

    from backend.services.resource_guard import guard_training_operation, is_on_hot_path
    if is_on_hot_path() and not force:
        # G4：热路径只排队，不阻塞
        pass
    elif not guard_training_operation("ml_activation") and not force:
        return {"ok": True, "skipped": True, "reason": "hot_path_guard"}

    with _lock:
        if _stats["in_flight"]:
            return {"ok": True, "skipped": True, "reason": "in_flight"}

    threading.Thread(
        target=_activation_worker,
        args=(session_id, tick),
        daemon=True,
        name="ml-activation",
    ).start()
    return {"ok": True, "started": True}


def get_learned_weighting_singleton():
    """供 factor_pipeline 读取已训练的学习层单例。"""
    if not is_ml_activation_enabled():
        return None
    try:
        lw = _get_learned()
        if lw.model is not None:
            return lw
    except Exception:
        pass
    return _get_learned() if _env_bool("LEARNED_WEIGHTING_ENABLED", True) else None
