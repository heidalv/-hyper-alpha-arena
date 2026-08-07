"""
参数域扩展器 — 阶段2(S2-10b)

学习三通道之二：把 Hermes L1 实盘归因产出的高置信参数模式，
反哺到 GA 进化搜索域 —— 参数域随验证证据动态扩展：

- ``outcome='improved'`` + ``direction='increase'`` → 搜索域上界 ×1.2（向更高探索）
- ``outcome='improved'`` + ``direction='decrease'`` → 搜索域下界 ×0.8（向更低探索）
- 同一参数多条高置信模式（不同 market_condition）→ 系数累乘（1.2^n）
- 总扩展封顶 max_ratio（默认 1.5 倍），防止域失控

闭环：实盘归因 → param_effect_patterns 模式库 → 搜索域扩展 →
下一轮 GA 进化能在被验证有效的一侧继续探索，形成参数级自进化。

接入点：``evolution_scheduler._get_full_param_ranges()`` 在返回基础域后调用
``apply_domain_expansion()``；无高置信模式 / 任何异常时原样返回基础域。
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 默认配置（settings.py 未注册时兜底）──
PARAM_DOMAIN_EXPAND_ENABLED = True
PARAM_DOMAIN_EXPAND_RATIO = 1.2        # 单条高置信模式扩展系数
PARAM_DOMAIN_EXPAND_MAX = 1.5          # 相对基础域的总扩展上限
PARAM_DOMAIN_MIN_SAMPLES = 3           # 高置信模式最低样本数
PARAM_DOMAIN_MIN_CONFIDENCE = 0.5      # 最低归因置信度
PARAM_DOMAIN_CACHE_TTL_SEC = 1800      # 模式读取缓存（秒）

# 模块级缓存：Hermes SQLite 读取便宜，但每次 GA 调度都查会放大耗时。
# 缓存"模式聚合结果"（base 无关），重放时按当前 base 重新计算扩展。
_DOMAIN_CACHE: Dict[str, Any] = {"ts": 0.0, "agg": None}


def _settings_cfg() -> Dict[str, Any]:
    """读 S2-10b 配置（带缺省，settings 缺失不炸）。"""
    try:
        from backend.config.settings import (
            PARAM_DOMAIN_EXPAND_ENABLED,
            PARAM_DOMAIN_EXPAND_RATIO,
            PARAM_DOMAIN_EXPAND_MAX,
            PARAM_DOMAIN_MIN_SAMPLES,
            PARAM_DOMAIN_MIN_CONFIDENCE,
            PARAM_DOMAIN_CACHE_TTL_SEC,
        )
        return {
            "enabled": bool(PARAM_DOMAIN_EXPAND_ENABLED),
            "expand_ratio": float(PARAM_DOMAIN_EXPAND_RATIO),
            "expand_max": float(PARAM_DOMAIN_EXPAND_MAX),
            "min_samples": int(PARAM_DOMAIN_MIN_SAMPLES),
            "min_confidence": float(PARAM_DOMAIN_MIN_CONFIDENCE),
            "cache_ttl": float(PARAM_DOMAIN_CACHE_TTL_SEC),
        }
    except Exception:
        return {
            "enabled": PARAM_DOMAIN_EXPAND_ENABLED,
            "expand_ratio": PARAM_DOMAIN_EXPAND_RATIO,
            "expand_max": PARAM_DOMAIN_EXPAND_MAX,
            "min_samples": PARAM_DOMAIN_MIN_SAMPLES,
            "min_confidence": PARAM_DOMAIN_MIN_CONFIDENCE,
            "cache_ttl": PARAM_DOMAIN_CACHE_TTL_SEC,
        }


def load_improved_patterns(
    min_samples: Optional[int] = None,
    min_confidence: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """读取 param_effect_patterns 中 outcome='improved' 的高置信模式。

    Hermes L1 的 update_pattern_library 已做 EMA 平滑 + 时间衰减，
    这里只做质量过滤：样本数 + 置信度双门槛。
    """
    cfg = _settings_cfg()
    min_samples = min_samples if min_samples is not None else cfg["min_samples"]
    min_confidence = min_confidence if min_confidence is not None else cfg["min_confidence"]

    try:
        from backend.services.hermes_db import hermes_fetchall
        rows = hermes_fetchall(
            """SELECT param_key, direction, sample_count,
                      avg_pnl_impact, confidence_avg
               FROM param_effect_patterns
               WHERE outcome = 'improved'
                 AND sample_count >= ?
                 AND confidence_avg >= ?
               ORDER BY ABS(avg_pnl_impact) * confidence_avg DESC""",
            (min_samples, min_confidence),
        )
        return rows or []
    except Exception as e:
        logger.warning("[ParamDomain] 读取高置信模式失败: %s", e)
        return []


def apply_domain_expansion(
    base_ranges: Dict[str, Tuple[float, float]],
    *,
    use_cache: bool = True,
) -> Tuple[Dict[str, Tuple[float, float]], List[Dict[str, Any]]]:
    """按高置信模式扩展搜索域。

    返回 ``(expanded_ranges, changes)``：
    - expanded_ranges：扩展后的域（无模式时与原基础域相同）；
    - changes：变更记录列表 ``[{param_key, direction, old, new, n_patterns}]``，
      供日志 / 报告展示"哪些参数域被智慧证据扩展了"。

    扩展规则：
    - increase → 上界 ×ratio（n 条模式 → ×ratio^n），封顶 ×max_ratio；
    - decrease → 下界 /ratio^n，封顶 /max_ratio（即 ×(1/max_ratio)）；
    - 整数域（lo/hi 均为整数）扩展后保留整数，避免 GA 搜索非预期粒度。
    """
    cfg = _settings_cfg()
    if not cfg["enabled"]:
        return dict(base_ranges), []

    ratio = max(1.0, cfg["expand_ratio"])
    max_ratio = max(ratio, cfg["expand_max"])

    # 缓存命中：模式库变化缓慢（EMA 聚合，小时级），且聚合与 base 无关
    agg: Optional[Dict[str, Dict[str, int]]] = None
    if use_cache and _DOMAIN_CACHE["agg"] is not None:
        if time.time() - _DOMAIN_CACHE["ts"] < cfg["cache_ttl"]:
            agg = _DOMAIN_CACHE["agg"]
    if agg is None:
        agg = _load_pattern_aggregation()
        _DOMAIN_CACHE["ts"] = time.time()
        _DOMAIN_CACHE["agg"] = agg

    return _expand_with_agg(base_ranges, agg, ratio, max_ratio)


def _load_pattern_aggregation() -> Dict[str, Dict[str, int]]:
    """读取高置信模式并按 param_key 聚合方向计数。"""
    patterns = load_improved_patterns()
    # 聚合：param_key → {increase: n, decrease: n}
    agg: Dict[str, Dict[str, int]] = {}
    for p in patterns:
        key = str(p.get("param_key") or "").strip()
        direction = str(p.get("direction") or "").strip().lower()
        if not key or direction not in ("increase", "decrease"):
            continue
        bucket = agg.setdefault(key, {"increase": 0, "decrease": 0})
        bucket[direction] += 1
    return agg


def _expand_with_agg(
    base_ranges: Dict[str, Tuple[float, float]],
    agg: Dict[str, Dict[str, int]],
    ratio: float,
    max_ratio: float,
) -> Tuple[Dict[str, Tuple[float, float]], List[Dict[str, Any]]]:
    """按聚合模式扩展搜索域（base 无关，缓存重放安全）。"""
    expanded: Dict[str, Tuple[float, float]] = {}
    changes: List[Dict[str, Any]] = []
    for key, (lo, hi) in base_ranges.items():
        lo, hi = float(lo), float(hi)
        bucket = agg.get(key)
        new_lo, new_hi = lo, hi
        if bucket:
            n_up = bucket.get("increase", 0)
            n_down = bucket.get("decrease", 0)
            if n_up:
                candidate_hi = hi * (ratio ** n_up)
                new_hi = min(candidate_hi, hi * max_ratio)
            if n_down:
                candidate_lo = lo / (ratio ** n_down)
                new_lo = max(candidate_lo, lo / max_ratio)

        # 整数域保留整数粒度
        if float(lo).is_integer() and float(hi).is_integer():
            new_lo, new_hi = float(int(round(new_lo))), float(int(round(new_hi)))

        if abs(new_hi - hi) > 1e-12 or abs(new_lo - lo) > 1e-12:
            expanded[key] = (round(new_lo, 6), round(new_hi, 6))
            changes.append({
                "param_key": key,
                "direction": "increase" if new_hi > hi else "decrease",
                "old": (round(lo, 6), round(hi, 6)),
                "new": (round(new_lo, 6), round(new_hi, 6)),
                "n_patterns": (bucket["increase"] + bucket["decrease"]) if bucket else 0,
            })
        else:
            expanded[key] = (lo, hi)

    return expanded, changes


def reset_domain_cache() -> None:
    """清空模块级缓存（测试 / 手动刷新用）。"""
    _DOMAIN_CACHE["ts"] = 0.0
    _DOMAIN_CACHE["agg"] = None
