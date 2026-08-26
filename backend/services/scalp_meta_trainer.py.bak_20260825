"""ScalpMetaTrainer — 短线信号"真假过滤器"（元标签）自动训练 + 验证例程。

定位
====
在 `scalp_signal_log` 累积的【真实信号 + 真实结果】上，训练一个 LightGBM 元模型：
输入=信号发生时的因子快照，输出=这一单"会不会赢"的概率。用于将来给 scalp EV 闸门
做真假过滤。

核心纪律（防自欺）
------------------
1. 样本量不足 → 优雅跳过（记录 need/have），绝不硬训。
2. 只看【样本外】walk-forward 成绩；同时训逻辑回归做"树 vs 线性"对比。
3. 关键指标不是 AUC，而是【严格过滤后胜率/净收益 vs 照单全收基线】的提升。
4. usable 门控：只有样本外达标（AUC + 过滤净收益提升且转正）才标记"可用"；
   否则只训练、存报告，保持"影子"状态，绝不自动接入实盘决策。

产出（data/ 目录）
------------------
- scalp_meta_model.pkl   : joblib 保存 {model, feature_cols, meta}
- scalp_meta_report.json : 人类可读的验证报告（含 usable、AUC、过滤效果、因子重要性）

对外接口
--------
- train_and_validate() -> dict : 训练+验证，返回报告（调度调用）。
- get_report() -> dict          : 读取最近一次报告（供前端/日志）。
- predict_win_prob(features)     : 用已保存且 usable 的模型给单个信号打分（未接入决策）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
_MODEL_PATH = os.path.join(_DATA_DIR, "scalp_meta_model.pkl")
_REPORT_PATH = os.path.join(_DATA_DIR, "scalp_meta_report.json")


# ── 可调参数（env 门控）──
def _min_samples() -> int:
    try:
        return max(200, int(os.getenv("SCALP_META_MIN_SAMPLES", "800") or 800))
    except Exception:
        return 800


def _min_per_class() -> int:
    try:
        return max(50, int(os.getenv("SCALP_META_MIN_PER_CLASS", "200") or 200))
    except Exception:
        return 200


def _n_folds() -> int:
    try:
        return max(3, int(os.getenv("SCALP_META_FOLDS", "4") or 4))
    except Exception:
        return 4


def _gate_min_auc() -> float:
    try:
        return float(os.getenv("SCALP_META_GATE_AUC", "0.53") or 0.53)
    except Exception:
        return 0.53


def _feature_freq_min() -> float:
    """特征列入选门槛：在样本中出现频率 ≥ 此值才作为特征（缺失填0）。"""
    try:
        return float(os.getenv("SCALP_META_FEATURE_FREQ", "0.2") or 0.2)
    except Exception:
        return 0.2


# ============================================================
# 数据加载 + 特征矩阵构造
# ============================================================
def _load_settled_rows() -> List[Dict[str, Any]]:
    from backend.database.connection import SessionLocal
    from backend.database.models import ScalpSignalLog
    db = SessionLocal()
    try:
        rows = (db.query(ScalpSignalLog)
                .filter(ScalpSignalLog.settled == True,  # noqa: E712
                        ScalpSignalLog.win.isnot(None))
                .order_by(ScalpSignalLog.signal_ts.asc()).all())
        out = []
        for r in rows:
            try:
                feats = json.loads(r.features_json) if r.features_json else {}
            except Exception:
                feats = {}
            if not isinstance(feats, dict):
                feats = {}
            out.append({
                "ts": int(r.signal_ts or 0),
                "symbol": r.symbol,
                "dir_sign": 1.0 if r.direction == "long" else -1.0,
                "factor_score": float(r.factor_score or 0),
                "threshold": float(r.threshold or 0),
                "win": 1 if r.win else 0,
                "net_ret": float(r.net_ret if r.net_ret is not None else 0.0),
                "feats": feats,
            })
        return out
    finally:
        db.close()


def _dedup_sec() -> int:
    """去重窗口（秒）：同一币在此窗口内只保留一条信号，避免"同一setup每30s重记 +
    标签窗口重叠"造成的样本非独立 → 样本外成绩虚高。默认=结算周期(1800s)。"""
    try:
        v = int(os.getenv("SCALP_META_DEDUP_SEC", "0") or 0)
        if v > 0:
            return v
    except Exception:
        pass
    try:
        return max(300, int(os.getenv("SCALP_META_HORIZON_SEC", "1800") or 1800))
    except Exception:
        return 1800


def _dedup_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按币贪心去重：只保留与上一条保留信号间隔 ≥ 去重窗口的样本（近似非重叠）。"""
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
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except Exception:
        return None


def _build_matrix(rows: List[Dict[str, Any]]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[str]]:
    """把不定键的因子快照对齐成统一特征矩阵。返回 X, y, ts, net, feature_cols。"""
    n = len(rows)
    # 统计每个快照键的出现频率（只保留数值型）
    key_count: Dict[str, int] = {}
    for r in rows:
        seen = set()
        for k, v in r["feats"].items():
            if _numeric(v) is not None and k not in seen:
                key_count[k] = key_count.get(k, 0) + 1
                seen.add(k)
    freq_min = _feature_freq_min()
    snap_cols = sorted([k for k, c in key_count.items() if c / n >= freq_min])
    # 附加两个强特征：信号自身分数、方向
    feature_cols = ["factor_score", "dir_sign"] + snap_cols

    X = np.zeros((n, len(feature_cols)), dtype=np.float64)
    y = np.zeros(n, dtype=int)
    ts = np.zeros(n, dtype=np.int64)
    net = np.zeros(n, dtype=np.float64)
    for i, r in enumerate(rows):
        X[i, 0] = r["factor_score"]
        X[i, 1] = r["dir_sign"]
        for j, k in enumerate(snap_cols, start=2):
            fv = _numeric(r["feats"].get(k))
            X[i, j] = fv if fv is not None else 0.0
        y[i] = r["win"]
        ts[i] = r["ts"]
        net[i] = r["net_ret"]
    return X, y, ts, net, feature_cols


# ============================================================
# 训练 + 样本外验证
# ============================================================
def train_and_validate() -> Dict[str, Any]:
    report: Dict[str, Any] = {"ts": int(time.time()), "usable": False}
    try:
        rows = _load_settled_rows()
    except Exception as e:
        logger.warning(f"[ScalpMeta] 读取信号失败: {e}")
        report.update({"status": "error", "error": str(e)})
        _write_report(report)
        return report

    n_raw = len(rows)
    rows = _dedup_rows(rows)          # 去重：非重叠独立样本，避免样本外虚高
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

    X, y, ts, net, feature_cols = _build_matrix(rows)
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
        clf = _mk().fit(X[tr], y[tr])
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

    # 过滤效果（严格：取概率前 30%）
    def _filt(q):
        thr = np.quantile(p, q)
        m = p >= thr
        if m.sum() < 20:
            return None
        return {"coverage": float(m.mean()), "win_rate": float(yy[m].mean()),
                "net_ret": float(nn[m].mean()), "n": int(m.sum())}

    filt30 = _filt(0.70)  # 前30%
    filt15 = _filt(0.85)  # 前15%

    # 因子重要性
    fis = sorted(zip(feature_cols, (fi / max(1, len(oos_p))).tolist()), key=lambda x: -x[1])
    tot = sum(v for _, v in fis) + 1e-12
    top_importance = [{"name": nm, "importance": round(v / tot, 4)} for nm, v in fis[:20]]

    report.update({
        "status": "trained",
        "features": len(feature_cols),
        "oos_auc_lgbm": round(oos_auc, 4),
        "oos_auc_linear": round(lin_auc, 4) if lin_auc is not None else None,
        "baseline": {"win_rate": round(base_wr, 4), "net_ret": round(base_ev, 6)},
        "filter_top30pct": filt30,
        "filter_top15pct": filt15,
        "top_importance": top_importance,
    })

    # ── usable 门控：样本外 AUC 达标 且 严格过滤净收益 > 基线 且 转正 ──
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

    # ── 训练"最终模型"（全量数据）并保存 ──
    try:
        import joblib
        final = _mk().fit(X, y)
        os.makedirs(_DATA_DIR, exist_ok=True)
        joblib.dump({
            "model": final, "feature_cols": feature_cols,
            "meta": {"trained_ts": report["ts"], "n": n, "usable": usable,
                     "oos_auc": oos_auc, "gate_reasons": reasons},
        }, _MODEL_PATH)
        report["model_path"] = _MODEL_PATH
    except Exception as e:
        logger.warning(f"[ScalpMeta] 保存模型失败: {e}")
        report["model_save_error"] = str(e)

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
        "raw": raw,
        "have": have,
        "need": need,
        "pos": pos,
        "neg": neg,
        "need_per_class": _min_per_class(),
        "dedup_sec": _dedup_sec(),
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
# 推理接口（为将来接入 EV 闸门准备，当前不接入决策）
# ============================================================
_MODEL_CACHE: Dict[str, Any] = {"mtime": 0, "obj": None}


def predict_win_prob(
    features: Dict[str, Any],
    *,
    require_usable: bool = True,
) -> Optional[float]:
    """给单个信号的因子快照打"会赢"概率。模型不存在/不可用则返回 None。

    Args:
        features: 特征字典（与训练列对齐）。
        require_usable: True（默认）时仅 usable 模型返回概率，供 EV 软接入；
            False 时影子日志仍可拿到概率（即使 usable=false），不用于决策。
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
        x = np.zeros((1, len(cols)), dtype=np.float64)
        for j, c in enumerate(cols):
            if c == "factor_score":
                x[0, j] = _numeric(features.get("factor_score")) or 0.0
            elif c == "dir_sign":
                d = str(features.get("direction") or "")
                x[0, j] = 1.0 if d == "long" else (-1.0 if d == "short" else 0.0)
            else:
                x[0, j] = _numeric(features.get(c)) or 0.0
        return float(bundle["model"].predict_proba(x)[0, 1])
    except Exception as e:
        logger.debug(f"[ScalpMeta] predict 跳过: {e}")
        return None
