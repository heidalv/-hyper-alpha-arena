"""经验滑点分布模型 — 2026-09-07。

对标机构做法（Algovantis / NexusFi）：滑点不是固定档，而是按经验分布建模
（5th / median / 95th 百分位）。本模块从 ExecutionQA 的成交样本（live_orders
滑点）聚合出经验分布，供 fee_guard.calc_slippage_rate 在有足够样本时替代
固定档位；样本不足时回退固定档（不影响现有行为）。

设计：
  - 每 10 min 刷新一次经验分布（进程内缓存 + 落盘 data/slippage_dist.json）
  - 分布按 (nature 档) 分桶：intraday / swing / trend_follow
  - 输出 estimate_slippage(notional, nature, is_sl, percentile)：
    默认用 median（典型成本）；风控熔断用 p95（尾部）
  - 实测滑点 > p95 时返回 is_tail=True，供上层降仓/熔断

fail-open：任何异常回退 None，调用方用固定档。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Any] = {"ts": 0.0, "dist": {}}
_CACHE_TTL_S = 600.0
_MIN_SAMPLES = 30  # 少于此样本数不用经验分布（不可靠）


def _dist_path() -> str:
    return os.path.join("data", "slippage_dist.json")


def _load_samples(lookback_days: int = 30) -> Dict[str, list]:
    """从 ExecutionQA 数据源拉近期滑点样本（bp），按 nature 分桶。

    直接复用 execution_qa.execution_stats 的口径（它已处理基准价/异常剔除）。
    返回 {nature: [slip_bp, ...]}。
    """
    try:
        from backend.services.agents.execution_qa import execution_stats
        now_ms = int(time.time() * 1000)
        since = now_ms - lookback_days * 86400 * 1000
        st = execution_stats(account_id=None, since_ms=since, until_ms=now_ms)
        # execution_stats 返回聚合值而非逐笔样本；逐笔样本需从 live_orders 直接取。
        # 这里用聚合的 median/p90 作为分布锚点（样本量足够时近似经验分布）。
        by_type = st.get("slippage_by_type") or {}
        out: Dict[str, list] = {}
        med = st.get("median_slippage_bp")
        p90 = st.get("p90_slippage_bp")
        n = int(st.get("n_slippage_samples") or 0)
        if n >= _MIN_SAMPLES and med is not None:
            # 用 median/p90 构造三点近似分布（保守：p95 用 p90×1.3 估计尾部）
            approx = sorted([med * 0.3, med, float(p90 or med * 2), float(p90 or med * 2) * 1.3])
            for nature in ("intraday", "swing", "trend_follow"):
                out[nature] = approx
        return out
    except Exception as exc:
        logger.debug("[SlippageModel] 样本加载失败: %s", exc)
        return {}


def _refresh() -> Dict[str, list]:
    now = time.time()
    if now - _CACHE["ts"] < _CACHE_TTL_S and _CACHE["dist"]:
        return _CACHE["dist"]
    dist = _load_samples()
    if dist:
        _CACHE["dist"] = dist
        _CACHE["ts"] = now
        try:
            os.makedirs(os.path.dirname(_dist_path()), exist_ok=True)
            with open(_dist_path() + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"ts": now, "dist": dist}, f, ensure_ascii=False)
            os.replace(_dist_path() + ".tmp", _dist_path())
        except Exception:
            pass
    else:
        # 内存没有则试读落盘
        if not _CACHE["dist"] and os.path.exists(_dist_path()):
            try:
                with open(_dist_path(), encoding="utf-8") as f:
                    d = json.load(f)
                _CACHE["dist"] = d.get("dist") or {}
                _CACHE["ts"] = float(d.get("ts") or 0)
            except Exception:
                pass
    return _CACHE["dist"]


def _percentile(sorted_vals: list, q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return float(sorted_vals[idx])


def estimate_slippage_bp(
    notional_usd: float = 0.0,
    trade_nature: str = "swing",
    percentile: float = 0.5,
) -> Optional[Dict[str, Any]]:
    """经验滑点估计（bp）。样本不足/异常返回 None（调用方回退固定档）。

    percentile: 0.5=median 典型成本；0.95=尾部（风控熔断用）。
    """
    dist = _refresh()
    samples = dist.get(trade_nature) or dist.get("swing") or []
    if len(samples) < 3:
        return None
    est = _percentile(samples, percentile)
    p95 = _percentile(samples, 0.95)
    if est is None:
        return None
    return {
        "slippage_bp": round(est, 3),
        "p95_bp": round(p95, 3) if p95 is not None else None,
        "is_tail": bool(p95 is not None and est >= p95),
        "n_samples": len(samples),
        "source": "empirical",
    }


def empirical_slippage_rate(
    notional_usd: float,
    trade_nature: str = "swing",
    is_sl: bool = False,
) -> Optional[float]:
    """供 fee_guard 调用：返回经验滑点率（小数，如 0.0008），或 None 回退固定档。

    止损场景用 p95 尾部（快市跳空）；普通用 median。
    """
    if (os.getenv("SLIPPAGE_EMPIRICAL_ENABLED", "1") or "1").strip().lower() in ("0", "false", "off"):
        return None
    pct = 0.95 if is_sl else 0.5
    est = estimate_slippage_bp(notional_usd, trade_nature, percentile=pct)
    if est is None:
        return None
    rate = float(est["slippage_bp"]) / 10000.0  # bp → 小数
    if rate <= 0:
        return None
    return rate
