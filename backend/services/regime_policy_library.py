"""Regime 策略库 + 门控组合（ReCAP 简化版）— 2026-09-07。

对标 ReCAP（Regime-Adaptive Continual Learning for Portfolio Management, 2026）：
  - regime 检测切分市场状态（trend / ranging / extreme / unknown）
  - 每个 regime 学一套策略向量（这里是因子权重桶）存库
  - regime-gate：按当前市场状态自适应组合库中策略
  - 只更新当前 regime 的策略向量（保留其它 regime 的知识，防遗忘）

与 ReCAP 的差异（简化）：ReCAP 学的是 RL 策略向量；这里策略向量 = 因子权重
快照。当某 regime 下因子权重表现好（IC 评估权重），就把它存进该 regime 的桶；
regime 切换时优先用对应桶的权重，而非全局平均——让「趋势市有效的因子组合」
和「震荡市有效的因子组合」各自沉淀，而不是被时间平均抹平。

设计：
  - 库存 data/regime_policy_library.json：{regime: {factor_id: weight, ...}, _meta}
  - record_regime_weights(regime, weights, score)：把当前权重按表现分存进桶
    （指数移动平均融合，新样本权重随 score 增大）
  - get_regime_weights(regime)：取该 regime 的权重桶（无则 None）
  - gate_weights(current_regime, global_weights)：门控组合——当前 regime 有桶
    且样本足够时，按门控权重融合桶权重与全局权重；否则用全局权重。

fail-open：任何异常返回全局权重，不阻断交易。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LIB_PATH = os.path.join("data", "regime_policy_library.json")
_LOCK = __import__("threading").Lock()

#: 门控强度：当前 regime 桶权重占比（其余用全局）。0.65 = 桶 65% + 全局 35%。
_GATE_ALPHA = float(os.getenv("REGIME_GATE_ALPHA", "0.65") or 0.65)
#: 桶内因子最少样本数（少于则不用桶，回退全局）
_MIN_BUCKET_SAMPLES = int(os.getenv("REGIME_GATE_MIN_SAMPLES", "5") or 5)
#: 桶融合的学习率（指数移动平均）
_EMA_LR = 0.3


def _load() -> Dict[str, Any]:
    if not os.path.exists(_LIB_PATH):
        return {"buckets": {}, "meta": {}}
    try:
        with open(_LIB_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if "buckets" not in d:
            d = {"buckets": d, "meta": {}}
        return d
    except Exception:
        return {"buckets": {}, "meta": {}}


def _save(lib: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_LIB_PATH), exist_ok=True)
        tmp = _LIB_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lib, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _LIB_PATH)
    except Exception as exc:
        logger.debug("[RegimeLib] 落盘失败: %s", exc)


def record_regime_weights(
    regime: str,
    weights: Dict[str, float],
    *,
    score: float = 1.0,
) -> None:
    """把当前因子权重按 regime 存进策略库（EMA 融合）。

    regime: trend / ranging / extreme / unknown
    weights: {factor_id: weight}
    score: 该权重组合的表现分（如平均 IC），越高新样本权重越大。
    """
    regime = str(regime or "unknown").lower()
    if not weights:
        return
    with _LOCK:
        lib = _load()
        buckets = lib.setdefault("buckets", {})
        meta = lib.setdefault("meta", {})
        bucket = buckets.setdefault(regime, {})
        m = meta.setdefault(regime, {"n": 0, "updated_at": 0})
        lr = min(0.9, _EMA_LR * max(0.2, float(score)))
        for fid, w in weights.items():
            try:
                w = float(w)
            except (TypeError, ValueError):
                continue
            if fid in bucket:
                bucket[fid] = round(bucket[fid] * (1 - lr) + w * lr, 6)
            else:
                bucket[fid] = round(w, 6)
        m["n"] = int(m.get("n") or 0) + 1
        m["updated_at"] = time.time()
        _save(lib)


def get_regime_weights(regime: str) -> Optional[Dict[str, float]]:
    """取该 regime 的权重桶；样本不足或无桶返回 None。"""
    regime = str(regime or "unknown").lower()
    with _LOCK:
        lib = _load()
        bucket = (lib.get("buckets") or {}).get(regime) or {}
        m = (lib.get("meta") or {}).get(regime) or {}
        if not bucket or int(m.get("n") or 0) < _MIN_BUCKET_SAMPLES:
            return None
        return dict(bucket)


def gate_weights(
    current_regime: str,
    global_weights: Dict[str, float],
) -> Dict[str, float]:
    """regime-gate 门控组合：当前 regime 有成熟桶时融合桶权重与全局权重。

    融合：w = α·bucket + (1−α)·global（α=REGIME_GATE_ALPHA，默认 0.65）。
    桶里没有的因子用全局权重。任何异常回退全局权重（fail-open）。
    """
    if not global_weights:
        return global_weights
    if (os.getenv("REGIME_GATE_ENABLED", "1") or "1").strip().lower() in ("0", "false", "off"):
        return global_weights
    try:
        bucket = get_regime_weights(current_regime)
        if not bucket:
            return global_weights
        out: Dict[str, float] = {}
        for fid, gw in global_weights.items():
            bw = bucket.get(fid)
            if bw is not None:
                out[fid] = round(_GATE_ALPHA * bw + (1 - _GATE_ALPHA) * float(gw), 6)
            else:
                out[fid] = float(gw)
        logger.info(
            "[RegimeLib] 门控组合 regime=%s 桶因子=%d 全局=%d α=%.2f",
            current_regime, len(bucket), len(global_weights), _GATE_ALPHA,
        )
        return out
    except Exception as exc:
        logger.debug("[RegimeLib] 门控组合失败（回退全局）: %s", exc)
        return global_weights


def stats() -> Dict[str, Any]:
    """策略库概览（观测用）。"""
    with _LOCK:
        lib = _load()
        return {
            "regimes": {
                r: {"n_factors": len(b), "samples": int((lib.get("meta") or {}).get(r, {}).get("n") or 0)}
                for r, b in (lib.get("buckets") or {}).items()
            },
            "gate_alpha": _GATE_ALPHA,
            "min_samples": _MIN_BUCKET_SAMPLES,
        }
