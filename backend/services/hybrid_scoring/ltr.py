# -*- coding: utf-8 -*-
"""通道A模型 —— LightGBM lambdarank（Learning-to-Rank）+ IC 加权兜底。

训练纪律（设计 §3.1）：
    - 按日期时间切块 70/15/15（train/valid/test），禁随机行分割（防横截面同期泄题）；
    - 指标 = 逐日 RankIC（pred vs fwd_ret 的 Spearman 均值）+ NDCG@10；
    - 模型与特征表/指标落盘 model_meta.json，超期（默认14天）通道A降级 IC 加权。

兜底链：LTR模型(新鲜) → IC加权(训练段算静态权重) → 均匀分。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from backend.services.hybrid_scoring import config
from backend.services.hybrid_scoring.features import FEATURES

logger = logging.getLogger(__name__)


def _rank_ic(pred: np.ndarray, fwd: np.ndarray, dates: pd.Index) -> Tuple[float, int]:
    """逐日 Spearman 秩相关均值（与 factor_ic_evaluator 同构的手动实现，免 scipy）。"""
    df = pd.DataFrame({"p": pred, "y": fwd, "d": dates})
    ics = []
    for _, g in df.groupby("d"):
        if len(g) < 5:
            continue
        rp = g["p"].rank()
        ry = g["y"].rank()
        n = len(g)
        cov = rp.cov(ry)
        sd = rp.std() * ry.std()
        if sd and np.isfinite(sd) and sd > 0:
            ics.append(float(cov / (sd * (n - 1) / n if n > 1 else 1.0)))
    return (float(np.mean(ics)) if ics else 0.0), len(ics)


def _ndcg10(pred: np.ndarray, gain: np.ndarray, dates: pd.Index) -> float:
    df = pd.DataFrame({"p": pred, "g": gain, "d": dates})
    vals = []
    for _, g in df.groupby("d"):
        g = g.sort_values("p", ascending=False).head(10)
        if len(g) < 2:
            continue
        disc = 1.0 / np.log2(np.arange(2, len(g) + 2))
        dcg = float((g["g"].to_numpy() * disc).sum())
        ideal = np.sort(g["g"].to_numpy())[::-1][: len(g)]
        idcg = float((ideal * disc).sum())
        if idcg > 0:
            vals.append(dcg / idcg)
    return float(np.mean(vals)) if vals else 0.0


def train(panel: pd.DataFrame, *, seed: int = 42) -> Dict[str, float]:
    """时间切分训练 lambdarank。返回指标 dict 并落盘模型。"""
    import lightgbm as lgb

    if panel is None or len(panel) < 500:
        raise ValueError(f"面板样本不足（{0 if panel is None else len(panel)} < 500），拒绝训练")

    dates = panel.index.get_level_values("date").unique().sort_values()
    n = len(dates)
    tr_d, va_d, te_d = dates[: int(n * 0.70)], dates[int(n * 0.70): int(n * 0.85)], dates[int(n * 0.85):]
    if len(tr_d) < 30 or len(va_d) < 10 or len(te_d) < 5:
        raise ValueError(f"日期切分不足（{len(tr_d)}/{len(va_d)}/{len(te_d)}），数据太短")

    def _split(ds) -> Tuple[pd.DataFrame, pd.DataFrame]:
        mask = panel.index.get_level_values("date").isin(ds)
        X = panel.loc[mask, FEATURES].astype(float).fillna(0.0)
        y = panel.loc[mask, "label_gain"].astype(int)
        g = panel.loc[mask].groupby(level="date").size().to_numpy()
        idx = panel.index[mask]
        return X, y, g, idx

    Xtr, ytr, gtr, itr = _split(tr_d)
    Xva, yva, gva, iva = _split(va_d)
    Xte, yte, gte, ite = _split(te_d)

    model = lgb.LGBMRanker(
        objective="lambdarank",
        n_estimators=250,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.9,
        subsample_freq=1,
        colsample_bytree=0.9,
        random_state=seed,
        verbosity=-1,
    )
    model.fit(Xtr, ytr, group=gtr, eval_set=[(Xva, yva)], eval_group=[gva],
              eval_metric="ndcg", feature_name=FEATURES,
              callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)])

    pv = model.predict(Xva, num_iteration=model.best_iteration_ or None)
    pt = model.predict(Xte, num_iteration=model.best_iteration_ or None)
    va_ic, va_n = _rank_ic(pv, panel.loc[iva, "fwd_ret"].to_numpy(), iva.get_level_values("date"))
    te_ic, te_n = _rank_ic(pt, panel.loc[ite, "fwd_ret"].to_numpy(), ite.get_level_values("date"))
    metrics = {
        "n_rows": int(len(panel)),
        "n_dates": int(n),
        "n_symbols": int(panel.index.get_level_values("symbol").nunique()),
        "valid_ndcg10": round(_ndcg10(pv, yva.to_numpy(), iva.get_level_values("date")), 4),
        "valid_rank_ic": round(va_ic, 4),
        "valid_ic_days": va_n,
        "test_rank_ic": round(te_ic, 4),
        "test_ndcg10": round(_ndcg10(pt, yte.to_numpy(), ite.get_level_values("date")), 4),
        "test_ic_days": te_n,
        "best_iteration": int(model.best_iteration_ or model.n_estimators),
        "trained_at": time.time(),
    }

    # IC 加权兜底权重（train 段静态，供模型过期/缺失时用）
    ic_w = ic_weights_from_panel(panel.loc[itr])
    meta = {"features": FEATURES, "metrics": metrics, "ic_weights": ic_w, "period": config.PANEL_PERIOD}
    config.model_path().parent.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(config.model_path()))
    config.model_meta_path().write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[HybridScore.ltr] 训练完成 %s", json.dumps(metrics, ensure_ascii=False))
    return metrics


def ic_weights_from_panel(train_panel: pd.DataFrame) -> Dict[str, float]:
    """静态 IC 权重：每特征与 fwd_ret 的逐日秩相关均值 → softmax 归一（负 IC 弃用，先例 ic_weights.py）。"""
    w: Dict[str, float] = {}
    for c in FEATURES:
        try:
            ic, _ = _rank_ic(
                train_panel[c].to_numpy(dtype=float),
                train_panel["fwd_ret"].to_numpy(dtype=float),
                train_panel.index.get_level_values("date"),
            )
        except Exception:
            ic = 0.0
        w[c] = float(ic)
    pos = {k: max(0.0, v) for k, v in w.items()}
    s = sum(pos.values())
    if s <= 1e-9:
        k = 1.0 / len(FEATURES)
        return {c: k for c in FEATURES}
    return {k: v / s for k, v in pos.items()}


def load_meta() -> Optional[Dict]:
    try:
        return json.loads(config.model_meta_path().read_text(encoding="utf-8"))
    except Exception:
        return None


def _model_fresh() -> bool:
    """新鲜 + 稳定可用：未过期、valid/test RankIC 双正、best_iteration≥20。

    [2026-09-17 实测] 弱信号面板会产出 valid/test 反号、5 轮就早停的"噪声模型"——
    这种模型禁止上岗（RankIC 噪声级 ±0.04 翻号如翻硬币），走 IC 加权兜底。
    """
    meta = load_meta()
    if not meta or not config.model_path().exists():
        return False
    m = meta.get("metrics") or {}
    if (time.time() - float(m.get("trained_at") or 0)) >= config.model_max_age_days() * 86400:
        return False
    if float(m.get("test_rank_ic") or 0.0) <= 0.0:
        return False
    if float(m.get("valid_rank_ic") or 0.0) <= 0.0:
        return False
    return int(m.get("best_iteration") or 0) >= 20


def score(rows: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, object]]:
    """打分入口：模型新鲜→LTR；否则 meta 里的 IC 权重；都没有→均匀分。

    返回 {sym: {"score": float∈[0,1], "backend": "ltr"|"ic_weight"|"uniform"}}。
    """
    if not rows:
        return {}
    if _model_fresh():
        try:
            import lightgbm as lgb

            booster = lgb.Booster(model_file=str(config.model_path()))
            syms = list(rows.keys())
            mat = pd.DataFrame([rows[s] for s in syms])[FEATURES].astype(float).fillna(0.0)
            # 单日横截面数量小，复制为多行组会扭曲 lambdarank 输出——Booster.predict 对
            # lambdarank 模型输出相对分，直接在横截面内单调可比利，min-max 到 [0,1]。
            pred = booster.predict(mat)
            pr = np.asarray(pred, dtype=float)
            lo, hi = float(np.nanmin(pr)), float(np.nanmax(pr))
            norm = (pr - lo) / (hi - lo) if hi > lo + 1e-12 else np.full_like(pr, 0.5)
            return {s: {"score": float(np.clip(norm[i], 0.0, 1.0)), "backend": "ltr"}
                    for i, s in enumerate(syms)}
        except Exception as e:  # noqa: BLE001
            logger.warning("[HybridScore.ltr] LTR 打分失败，降级 IC 加权: %s", str(e)[:120])
    meta = load_meta()
    if meta and meta.get("ic_weights"):
        return ic_weight_score(rows, meta["ic_weights"])
    syms = list(rows.keys())
    return {s: {"score": 0.5, "backend": "uniform"} for s in syms}


def ic_weight_score(rows: Dict[str, Dict[str, float]], weights: Dict[str, float]) -> Dict[str, Dict[str, object]]:
    out = {}
    for s, r in rows.items():
        v = sum(float(weights.get(c, 0.0)) * float(r.get(c) or 0.0) for c in FEATURES)
        out[s] = {"score": float(np.clip(0.5 + v / 4.0, 0.0, 1.0)), "backend": "ic_weight"}
    return out
