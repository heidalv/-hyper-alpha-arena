# -*- coding: utf-8 -*-
"""
S2-9 选币因子自适应：IC 加权 + 组合相关性去重 + LLM 组合决策
===========================================================

v6 阶段2（S2-9）三大能力，全部纯函数/可注入设计：

1. IC 加权（``compute_factor_ics`` / ``get_ic_weights``）
   样本源 = ``auto_coin_selections`` 的 injected 记录（S2-9a 新增的
   ``factor_snapshot_json`` + 已有 ``hit_24h`` 反馈闭环）。对每个因子维度
   计算 Spearman 秩相关 IC（因子分数 vs 24h 命中 0/1），归一化为权重；
   负 IC 因子弃用（权重 0），全部非正时回退静态权重（V3 AUTO_COIN_W_*）。

2. 组合相关性去重（``dedup_by_correlation``）
   候选币的因子向量两两做余弦相似度，贪心选取（综合分降序），保留与
   已选集合所有成员相关性 < 阈值的候选，避免同质币扎堆（如多枚同赛道
   高相关币同时注入）。

3. LLM 组合决策（``llm_compose``）
   把候选池表格交给 LLM，输出最终注入名单（JSON 数组）。失败/超时/
   解析失败返回 None，由调用方回退规则路径 —— LLM 是增强不是依赖。

DB 访问集中在 ``load_ic_samples`` / ``refresh_ic_weights``；权重带 TTL
缓存（模块级单例），避免每次选币循环重复查库。

Spearman 实现与 ``factor_ic_evaluator._rank_ic`` 同构（手动实现，避免
scipy 依赖），加密重尾分布下对异常值更鲁棒（Alphalens/qlib 标准）。
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 常量与配置缺省值（settings 覆盖，见 get_ic_weights）
# ─────────────────────────────────────────────────────────────

# 因子维度 —— 与 auto_coin_selector.scores_detail 键对齐：
# V3 开启时写入 base_score/flow_score/whale_score/news_score/sector_rs_score
# V3 关闭时写入五维 vol_score/trend_score/mom_score/vola_score/fund_score
FACTOR_KEYS: List[str] = [
    "base_score",
    "flow_score",
    "whale_score",
    "news_score",
    "sector_rs_score",
    "vol_score",
    "trend_score",
    "mom_score",
    "vola_score",
    "fund_score",
]

# V3 合成权重键映射：scores_detail 键 → _compose_v3_score weights 键
_V3_KEY_MAP: Dict[str, str] = {
    "base_score": "base",
    "flow_score": "flow",
    "whale_score": "whale",
    "news_score": "news",
    "sector_rs_score": "sector",
}

# 静态回退权重（与 auto_coin_selector._compose_v3_score 的默认值一致）
_DEFAULT_V3_WEIGHTS: Dict[str, float] = {
    "base": 0.55,
    "flow": 0.20,
    "whale": 0.10,
    "news": 0.10,
    "sector": 0.05,
}

_DEFAULT_MIN_SAMPLES = 30      # 与 factor_ic_evaluator 对齐
_DEFAULT_TTL_SEC = 900         # 权重缓存 15 分钟
_DEFAULT_CORR_THRESHOLD = 0.85  # 余弦相关性去重阈值
_DEFAULT_LOOKBACK_DAYS = 45    # 样本回看窗口


# ─────────────────────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────────────────────
@dataclass
class IcSample:
    """一条选币 IC 样本：因子快照 + 24h 命中结果。"""

    symbol: str
    factors: Dict[str, float]
    hit: bool  # hit_24h


@dataclass
class IcWeightsResult:
    """IC 权重结果（含诊断信息，便于决策链路视图展示）。"""

    weights: Dict[str, float]          # V3 键（base/flow/whale/news/sector）
    ics: Dict[str, float]              # 各因子 IC（scores_detail 键）
    n_samples: int                     # 有效样本数
    enabled: bool                      # 是否真实启用 IC（样本足够）
    note: str                          # 诊断说明
    computed_at: float = 0.0           # 计算时间戳


# ─────────────────────────────────────────────────────────────
# Spearman 秩相关（与 factor_ic_evaluator._rank_ic 同构，免 scipy）
# ─────────────────────────────────────────────────────────────
def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    return max(-1.0, min(1.0, cov / (vx * vy)))


def _rank(values: Sequence[float]) -> List[float]:
    """平均秩（平局取平均）。"""
    sorted_idx = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(sorted_idx):
        j = i
        while j + 1 < len(sorted_idx) and values[sorted_idx[j + 1]] == values[sorted_idx[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[sorted_idx[k]] = avg_rank
        i = j + 1
    return ranks


def rank_ic(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Rank IC（Spearman 秩相关）。样本不足返回 None。"""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    return _pearson(_rank(list(xs)), _rank(list(ys)))


# ─────────────────────────────────────────────────────────────
# 1. IC 加权
# ─────────────────────────────────────────────────────────────
def compute_factor_ics(
    samples: Sequence[IcSample],
    keys: Optional[Sequence[str]] = None,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
) -> Dict[str, float]:
    """对每个因子维度计算 Spearman IC（因子分 vs 24h 命中 0/1）。

    - 仅使用该维度非空样本（其他维度缺失不影响）；
    - 有效对 < min_samples 的维度返回 IC 0（视为无信息，不参与加权）。
    """
    keys = list(keys or FACTOR_KEYS)
    ics: Dict[str, float] = {}
    for k in keys:
        xs: List[float] = []
        ys: List[float] = []
        for s in samples:
            v = s.factors.get(k)
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(fv):
                continue
            xs.append(fv)
            ys.append(1.0 if s.hit else 0.0)
        if len(xs) < min_samples:
            ics[k] = 0.0
            continue
        ic = rank_ic(xs, ys)
        ics[k] = ic if ic is not None else 0.0
    return ics


def to_v3_weights(
    ics: Dict[str, float],
    fallback: Optional[Dict[str, float]] = None,
    v3_key_map: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, float], bool]:
    """IC → V3 合成权重：负 IC 弃用，正 IC 按相对大小归一化。

    Returns: (weights, enabled)
      - enabled=False 表示全部非正/无有效 IC，返回 fallback 静态权重。
    """
    mapping = v3_key_map or _V3_KEY_MAP
    acc: Dict[str, float] = {}
    for src_key, v3_key in mapping.items():
        ic = ics.get(src_key, 0.0)
        if ic > 0:
            acc[v3_key] = ic
    if not acc:
        return dict(fallback or _DEFAULT_V3_WEIGHTS), False
    total = sum(acc.values())
    norm = {k: v / total for k, v in acc.items()}
    return norm, True


# ─────────────────────────────────────────────────────────────
# 样本加载 + TTL 缓存（模块级单例）
# ─────────────────────────────────────────────────────────────
def _parse_snapshot(raw: Any) -> Optional[Dict[str, Any]]:
    """factor_snapshot_json 可能是 dict（PG JSONB）或 str（SQLite TEXT）。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _extract_factor_values(snapshot: Dict[str, Any]) -> Dict[str, float]:
    """从快照中提取数值因子（容忍嵌套与 None）。"""
    out: Dict[str, float] = {}
    for k in FACTOR_KEYS:
        v = snapshot.get(k)
        if v is None and isinstance(snapshot.get("parts"), dict):
            # V3 meta 未展开时尝试 parts（_compose_v3_score 返回的 used 键）
            v = snapshot.get("parts", {}).get(k)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv):
            out[k] = fv
    return out


def load_ic_samples(
    db,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    min_hit: int = 2,
) -> List[IcSample]:
    """从 auto_coin_selections 加载 IC 样本。

    过滤：action=injected + factor_snapshot_json 非空 + hit_24h 非空 +
    时间窗口。hit 样本数 < min_hit 视为反馈闭环未成形，返回 []（防误算）。
    """
    try:
        from backend.database.models import AutoCoinSelection
    except Exception:
        return []
    # 注意：created_at 是 PG current_timestamp 写入的 naive CST（会话时区 Asia/Shanghai），
    # 必须用本地时间做窗口基准，否则 UTC 与 CST 差 8h → 窗口错位/样本漏算。
    cutoff = datetime.now() - timedelta(days=lookback_days)
    try:
        rows = (
            db.query(AutoCoinSelection)
            .filter(
                AutoCoinSelection.action == "injected",
                AutoCoinSelection.factor_snapshot_json.isnot(None),
                AutoCoinSelection.hit_24h.isnot(None),
                AutoCoinSelection.created_at >= cutoff,
            )
            .limit(5000)
            .all()
        )
    except Exception as e:
        logger.warning("[CoinIC] 样本加载失败: %s", e)
        return []

    samples: List[IcSample] = []
    hit_n = 0
    for r in rows:
        snap = _parse_snapshot(getattr(r, "factor_snapshot_json", None))
        if not snap:
            continue
        factors = _extract_factor_values(snap)
        if not factors:
            continue
        hit = bool(getattr(r, "hit_24h", False))
        hit_n += 1 if hit else 0
        samples.append(IcSample(symbol=str(getattr(r, "symbol", "")), factors=factors, hit=hit))
    if hit_n < min_hit:
        logger.info("[CoinIC] 命中样本不足(%d/%d)，反馈闭环未成形，跳过 IC 计算", hit_n, min_hit)
        return []
    return samples


# 模块级缓存：{ts, lookback_days, weights, ics, n_samples, enabled, note}
_IC_CACHE: Dict[str, Any] = {"ts": 0.0, "key": None}


def _settings() -> Dict[str, Any]:
    """读 settings（带缺省，settings 缺失不炸）。"""
    try:
        from backend.config.settings import (
            AUTO_COIN_IC_WEIGHTS_ENABLED,
            AUTO_COIN_IC_MIN_SAMPLES,
            AUTO_COIN_IC_CACHE_TTL_SEC,
            AUTO_COIN_CORR_DEDUP_THRESHOLD,
            AUTO_COIN_LLM_COMPOSE_ENABLED,
        )
        return {
            "enabled": bool(AUTO_COIN_IC_WEIGHTS_ENABLED),
            "min_samples": int(AUTO_COIN_IC_MIN_SAMPLES),
            "ttl": int(AUTO_COIN_IC_CACHE_TTL_SEC),
            "corr_threshold": float(AUTO_COIN_CORR_DEDUP_THRESHOLD),
            "llm_enabled": bool(AUTO_COIN_LLM_COMPOSE_ENABLED),
        }
    except Exception:
        return {
            "enabled": True,
            "min_samples": _DEFAULT_MIN_SAMPLES,
            "ttl": _DEFAULT_TTL_SEC,
            "corr_threshold": _DEFAULT_CORR_THRESHOLD,
            "llm_enabled": False,
        }


def refresh_ic_weights(
    db,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    min_samples: Optional[int] = None,
    fallback: Optional[Dict[str, float]] = None,
) -> IcWeightsResult:
    """完整 IC 权重计算链：加载样本 → 各因子 IC → V3 权重归一化。"""
    cfg = _settings()
    min_n = min_samples if min_samples is not None else cfg["min_samples"]
    samples = load_ic_samples(db, lookback_days=lookback_days)
    if not samples:
        return IcWeightsResult(
            weights=dict(fallback or _DEFAULT_V3_WEIGHTS),
            ics={},
            n_samples=0,
            enabled=False,
            note="no_samples",
            computed_at=time.time(),
        )
    ics = compute_factor_ics(samples, min_samples=min_n)
    weights, enabled = to_v3_weights(ics, fallback=fallback)
    pos_n = sum(1 for v in ics.values() if v > 0)
    note = (
        f"ic_weights(n={len(samples)},pos_ic={pos_n})"
        if enabled
        else f"no_positive_ic(n={len(samples)})"
    )
    return IcWeightsResult(
        weights=weights,
        ics=ics,
        n_samples=len(samples),
        enabled=enabled,
        note=note,
        computed_at=time.time(),
    )


def get_ic_weights(db, force: bool = False) -> IcWeightsResult:
    """带 TTL 缓存的 IC 权重查询（模块级单例）。

    未启用（AUTO_COIN_IC_WEIGHTS_ENABLED=false）或样本不足时返回静态回退，
    调用方据此决定是否覆盖 _compose_v3_score 的静态权重。
    """
    cfg = _settings()
    if not cfg["enabled"]:
        return IcWeightsResult(
            weights=dict(_DEFAULT_V3_WEIGHTS),
            ics={},
            n_samples=0,
            enabled=False,
            note="disabled",
            computed_at=time.time(),
        )
    now = time.time()
    if not force and _IC_CACHE.get("ts", 0.0) and (now - _IC_CACHE["ts"]) < cfg["ttl"]:
        cached = _IC_CACHE.get("result")
        if cached is not None:
            return cached
    result = refresh_ic_weights(db)
    _IC_CACHE["ts"] = now
    _IC_CACHE["result"] = result
    return result


def reset_ic_cache() -> None:
    """清空模块级缓存（测试与 API 手动刷新用）。"""
    _IC_CACHE["ts"] = 0.0
    _IC_CACHE["result"] = None


# ─────────────────────────────────────────────────────────────
# 2. 组合相关性去重（余弦相似度贪心）
# ─────────────────────────────────────────────────────────────
def factor_vector(snapshot: Dict[str, Any]) -> Dict[str, float]:
    """从单个快照提取数值因子向量（供相关性去重用）。"""
    return _extract_factor_values(snapshot)


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    keys = set(a) & set(b)
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return dot / (na * nb)


def dedup_by_correlation(
    ranked: Sequence[Tuple[str, Dict[str, float]]],
    threshold: float = _DEFAULT_CORR_THRESHOLD,
) -> List[str]:
    """按综合分降序贪心去重：与已选集合任一成员相关性 >= 阈值则剔除。

    Args:
        ranked: [(symbol, factor_vector)]，已按综合分降序。
        threshold: 相关性阈值（默认 0.85）。<=0 或 >=1 时跳过去重。

    Returns:
        保留的 symbol 列表（保持输入顺序）。
    """
    if threshold is None or threshold <= 0.0 or threshold >= 1.0 or not ranked:
        return [s for s, _ in ranked]
    kept: List[str] = []
    kept_vecs: List[Dict[str, float]] = []
    skipped = 0
    for sym, vec in ranked:
        if not vec:
            kept.append(sym)
            kept_vecs.append(vec)
            continue
        if any(_cosine(vec, v) >= threshold for v in kept_vecs):
            skipped += 1
            continue
        kept.append(sym)
        kept_vecs.append(vec)
    if skipped:
        logger.info("[CoinIC] 相关性去重剔除 %d 个高相关候选", skipped)
    return kept


# ─────────────────────────────────────────────────────────────
# 3. LLM 组合决策（可注入 caller，失败回退 None）
# ─────────────────────────────────────────────────────────────
def _extract_symbol_list(text: str) -> Optional[List[str]]:
    """从 LLM 输出中提取 symbol 名单（JSON 数组，容忍前后噪声）。"""
    if not text:
        return None
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except Exception:
        return None
    if not isinstance(parsed, list):
        return None
    out: List[str] = []
    for item in parsed:
        if isinstance(item, str):
            s = item.strip().upper()
            if s:
                out.append(s)
    return out or None


def llm_compose(
    candidates: Sequence[Dict[str, Any]],
    llm_caller: Callable[[str], str],
    max_select: int = 5,
    extra_instruction: str = "",
) -> Optional[List[str]]:
    """LLM 组合决策：从候选池选出最终注入名单。

    Args:
        candidates: [{symbol, score, confidence, reason, factors}]
        llm_caller: (prompt) -> str 文本；selector 接入时传 _call_ai 包装。
        max_select: 最多选出数量。

    Returns:
        symbol 名单；LLM 失败/超时/解析失败返回 None（调用方回退规则路径）。
    """
    if not candidates:
        return None
    table_rows = []
    for i, c in enumerate(candidates, 1):
        factors = c.get("factors") or {}
        factor_str = ", ".join(f"{k}={v:.2f}" for k, v in sorted(factors.items()))
        table_rows.append(
            f"{i}. {c.get('symbol')} | score={c.get('score')} | "
            f"conf={c.get('confidence')} | reason={str(c.get('reason') or '')[:80]} | "
            f"factors: {factor_str}"
        )
    prompt = (
        "你是加密资产组合选币决策者。候选池按综合分降序排列，但高分币可能高度同质"
        "（高相关）。请从候选池中选出最多 "
        f"{max_select} 个币组成最终注入名单，要求：\n"
        "1. 优先保留综合分高的；\n"
        "2. 避免同时选入因子画像高度相似（同赛道/同风格）的币 —— 组合需要分散；\n"
        "3. 因子 IC 为正的维度（分数高者更优）优先。\n"
        "候选池：\n"
        + "\n".join(table_rows)
        + "\n只输出 JSON 数组（symbol 大写），不要任何解释。\n"
        + (extra_instruction + "\n" if extra_instruction else "")
    )
    try:
        raw = llm_caller(prompt)
    except Exception as e:
        logger.warning("[CoinIC] LLM 组合决策失败(回退规则路径): %s", e)
        return None
    picked = _extract_symbol_list(raw)
    if picked is None:
        logger.warning("[CoinIC] LLM 组合输出解析失败: %.200s", str(raw or "")[:200])
        return None
    valid = {str(c.get("symbol", "")).upper() for c in candidates}
    picked = [s for s in picked if s in valid][:max_select]
    logger.info("[CoinIC] LLM 组合决策: %s", picked)
    return picked or None
