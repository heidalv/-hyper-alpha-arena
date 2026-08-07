"""
因子 IC 有效性闭环（M7）

闭环：信号 →（开仓时 signal_trade_feedback 落因子快照）→ 成交 →
     （平仓时 update_trade_pnl 回填盈亏）→ 本模块评估 →
     factor_performance_logs 留痕 + 运行时权重文件 → composite 信号自动降权

评估口径：
  - 把因子原始值经 FactorSignalGenerator 的方向映射转为 [-1,+1]（修复
    record_entry_signals 用「值的正负」当方向的错误，如 RSI 恒为正）
  - long_equiv_pnl = pnl（做多）/ -pnl（做空）→ 多头等效收益
  - 方向胜率 = 因子方向与多头等效收益同号的比例（|方向|<0.2 的中性样本剔除）
  - IC = Pearson corr(因子方向, 多头等效收益)

降权规则（有 min_samples 个样本才生效）：
  胜率 < 40% → 权重 0.25；< 45% → 0.5；> 60% → 1.2（温和升权）；其余 1.0
"""

import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

RUNTIME_WEIGHTS_FILE = os.path.join("data", "factor_runtime_weights.json")
_weights_cache: dict = {"ts": 0.0, "data": {}}

MIN_SAMPLES = 30  # [P0-1 2026-07-30] 从8提高到30，避免小样本IC=1.0假象
NEUTRAL_DIRECTION_EPS = 0.2


def load_runtime_factor_weights() -> Dict[str, float]:
    """读取 IC 闭环产出的因子权重（60s 缓存；无文件返回空 dict=等权）。"""
    now = time.time()
    if now - _weights_cache["ts"] < 60:
        return _weights_cache["data"]
    data: Dict[str, float] = {}
    try:
        if os.path.exists(RUNTIME_WEIGHTS_FILE):
            with open(RUNTIME_WEIGHTS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            for k, v in (raw.get("weights") or {}).items():
                try:
                    # 边界保护：权重只允许 [0.1, 2.0]
                    data[str(k)] = max(0.1, min(2.0, float(v)))
                except (TypeError, ValueError):
                    continue
    except Exception as err:
        logger.warning(f"[FactorIC] 运行时权重读取失败: {err}")
    _weights_cache["ts"] = now
    _weights_cache["data"] = data
    return data


def _map_factor_direction(factor_name: str, raw_value: float) -> float:
    """用信号生成器的方向映射把因子原始值转为 [-1,+1] 方向。"""
    from backend.services.factor_engine.factor_signal_generator import (
        _default_direction,
    )

    gen = _get_signal_generator()
    mapper = gen._direction_mappers.get(factor_name)
    if mapper is None:
        # rsi_14 → rsi 这类带参数后缀的名字，按前缀匹配
        base = factor_name.split("_")[0]
        for key, fn in gen._direction_mappers.items():
            if factor_name.startswith(key) or base == key:
                mapper = fn
                break
    if mapper is None:
        mapper = _default_direction
    try:
        return max(-1.0, min(1.0, float(mapper(raw_value))))
    except (TypeError, ValueError, OverflowError):
        return 0.0


_signal_gen_singleton = None


def _get_signal_generator():
    global _signal_gen_singleton
    if _signal_gen_singleton is None:
        from backend.services.factor_engine.factor_signal_generator import (
            FactorSignalGenerator,
        )
        _signal_gen_singleton = FactorSignalGenerator()
    return _signal_gen_singleton


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    """Pearson IC（保留用于向后兼容，新逻辑用 _rank_ic）"""
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


def _rank_ic(xs: List[float], ys: List[float]) -> Optional[float]:
    """Rank IC（Spearman）— 对异常值更鲁棒，加密重尾分布下更可靠。

    [P0-1 2026-07-30] 替代 _pearson 作为主IC计算方法。
    文献依据：Alphalens/qlib标准用Rank IC；加密收益重尾分布(1909.04903)。
    """
    n = len(xs)
    if n < 30:  # 与MIN_SAMPLES对齐
        return None
    # 手动实现Spearman（避免scipy依赖）
    def _rank(values):
        sorted_vals = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(sorted_vals):
            j = i
            while j + 1 < len(sorted_vals) and values[sorted_vals[j + 1]] == values[sorted_vals[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[sorted_vals[k]] = avg_rank
            i = j + 1
        return ranks

    rx = _rank(xs)
    ry = _rank(ys)
    return _pearson(rx, ry)


def run_factor_ic_evaluation(db, lookback_days: int = 30) -> Dict[str, dict]:
    """
    评估各因子近 lookback_days 天的方向胜率与 IC：
      1. 写 factor_performance_logs（AnalyticsBase 留痕）
      2. 产出运行时权重文件 data/factor_runtime_weights.json

    [P0-1 2026-07-30] 修复IC计算bug：改为取全部历史样本（不只取最近一批），
    改用Rank IC（Spearman）替代Pearson，min_samples从8提高到30。

    Returns: {factor_name: {n, win_rate, ic, weight}}
    """
    from backend.database.models import SignalTradeFeedback

    # [P0-1] 取全部已配对样本（不只取lookback_days天的）
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = (
        db.query(SignalTradeFeedback)
        .filter(
            SignalTradeFeedback.signal_type.like("factor:%"),
            SignalTradeFeedback.trade_pnl.isnot(None),
            SignalTradeFeedback.created_at >= cutoff.replace(tzinfo=None),
        )
        .all()
    )
    if not rows:
        logger.info("[FactorIC] 无已配对的因子-盈亏样本，跳过本轮评估")
        return {}

    # 按因子聚合 (方向, 多头等效收益)
    samples: Dict[str, List[Tuple[float, float]]] = {}
    for r in rows:
        factor_name = str(r.signal_type or "")[len("factor:"):]
        if not factor_name:
            continue
        direction = _map_factor_direction(factor_name, float(r.signal_value or 0))
        if abs(direction) < NEUTRAL_DIRECTION_EPS:
            continue  # 中性信号不参与方向评估
        pnl = float(r.trade_pnl or 0)
        side = (r.trade_side or "").lower()
        long_equiv = pnl if side in ("long", "buy") else -pnl
        samples.setdefault(factor_name, []).append((direction, long_equiv))

    results: Dict[str, dict] = {}
    weights: Dict[str, float] = {}
    for name, pairs in samples.items():
        n = len(pairs)
        if n < 3:
            continue
        wins = sum(1 for d, p in pairs if (d > 0 and p > 0) or (d < 0 and p < 0))
        win_rate = wins / n
        ic = _rank_ic([d for d, _ in pairs], [p for _, p in pairs])

        weight = 1.0
        if n >= MIN_SAMPLES:
            if win_rate < 0.40:
                weight = 0.25
            elif win_rate < 0.45:
                weight = 0.5
            elif win_rate > 0.60:
                weight = 1.2
        weights[name] = weight
        results[name] = {
            "n": n,
            "win_rate": round(win_rate, 4),
            "ic": round(ic, 4) if ic is not None else None,
            "weight": weight,
        }

    if not results:
        logger.info("[FactorIC] 有效样本不足，跳过权重更新")
        return {}

    # ── 留痕 factor_performance_logs（AnalyticsBase）──
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.database.models import FactorPerformanceLog

        ana = AnalyticsSessionLocal()
        try:
            for name, stat in results.items():
                ana.add(FactorPerformanceLog(
                    factor_name=name[:50],
                    factor_category="composite_v3",
                    ic_value=stat["ic"],
                    decay_rate=None,
                    current_weight=stat["weight"],
                    market_regime=None,
                    symbol=None,
                    timeframe="15m",
                ))
            ana.commit()
        finally:
            ana.close()
    except Exception as err:
        logger.warning(f"[FactorIC] factor_performance_logs 写入失败: {err}")

    # ── 产出运行时权重 ──
    try:
        os.makedirs(os.path.dirname(RUNTIME_WEIGHTS_FILE), exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": lookback_days,
            "weights": weights,
            "stats": results,
        }
        with open(RUNTIME_WEIGHTS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        _weights_cache["ts"] = 0.0  # 失效缓存，下次读取拿新权重
    except Exception as err:
        logger.warning(f"[FactorIC] 运行时权重写入失败: {err}")

    downweighted = [k for k, v in weights.items() if v < 1.0]
    logger.info(
        f"[FactorIC] 评估完成: {len(results)} 个因子, "
        f"降权 {len(downweighted)} 个 {downweighted[:8]}"
    )
    return results


# nature → 代表性时间框架（用于 factor_performance_logs 标签与分流展示）
_NATURE_TF = {"scalp": "15m", "swing": "4h", "trend_follow": "1d", "position": "1d"}


def run_factor_ic_evaluation_segmented(db, lookback_days: int = 45) -> Dict[str, dict]:
    """按成交性质(scalp/swing/trend)分流评估因子 IC（S4-C）。

    把 `factor:%` 反馈行按其 trade_id 对应持仓的 `trade_nature` 分桶，分别计算方向
    胜率与 IC，写入 factor_performance_logs（timeframe 用 nature 代表周期），并返回
    分段统计供中长线健康视图消费。不改动全局运行时权重文件（避免与主评估冲突）。

    Returns: {nature: {factor_name: {n, win_rate, ic}}}
    """
    from backend.database.models import SignalTradeFeedback, PaperPosition

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = (
        db.query(SignalTradeFeedback)
        .filter(
            SignalTradeFeedback.signal_type.like("factor:%"),
            SignalTradeFeedback.trade_pnl.isnot(None),
            SignalTradeFeedback.trade_id.isnot(None),
            SignalTradeFeedback.created_at >= cutoff.replace(tzinfo=None),
        )
        .all()
    )
    if not rows:
        return {}

    # trade_id → trade_nature 映射（一次查全，避免 N+1）
    trade_ids = list({int(r.trade_id) for r in rows if r.trade_id is not None})
    nature_by_tid: Dict[int, str] = {}
    for i in range(0, len(trade_ids), 500):
        chunk = trade_ids[i:i + 500]
        try:
            for pid, nat in (
                db.query(PaperPosition.id, PaperPosition.trade_nature)
                .filter(PaperPosition.id.in_(chunk))
                .all()
            ):
                nature_by_tid[int(pid)] = (nat or "scalp").lower()
        except Exception as e:
            logger.debug(f"[FactorIC-Seg] 持仓性质查询跳过: {e}")

    # (nature, factor) → [(direction, long_equiv_pnl)]
    seg: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
    for r in rows:
        factor_name = str(r.signal_type or "")[len("factor:"):]
        if not factor_name:
            continue
        nat = nature_by_tid.get(int(r.trade_id), "scalp")
        bucket = "trend_follow" if nat in ("trend_follow", "position") else (
            "swing" if nat == "swing" else "scalp"
        )
        direction = _map_factor_direction(factor_name, float(r.signal_value or 0))
        if abs(direction) < NEUTRAL_DIRECTION_EPS:
            continue
        pnl = float(r.trade_pnl or 0)
        side = (r.trade_side or "").lower()
        long_equiv = pnl if side in ("long", "buy") else -pnl
        seg.setdefault(bucket, {}).setdefault(factor_name, []).append((direction, long_equiv))

    out: Dict[str, dict] = {}
    logs: List[Tuple[str, str, Optional[float]]] = []  # (factor, timeframe, ic)
    for bucket, factors in seg.items():
        out[bucket] = {}
        tf = _NATURE_TF.get(bucket, "15m")
        for name, pairs in factors.items():
            n = len(pairs)
            if n < 3:
                continue
            wins = sum(1 for d, p in pairs if (d > 0 and p > 0) or (d < 0 and p < 0))
            ic = _rank_ic([d for d, _ in pairs], [p for _, p in pairs])
            out[bucket][name] = {
                "n": n,
                "win_rate": round(wins / n, 4),
                "ic": round(ic, 4) if ic is not None else None,
            }
            logs.append((name, tf, ic))

    # 留痕 factor_performance_logs（按 nature 代表周期打 timeframe 标签）
    if logs:
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.database.models import FactorPerformanceLog
            ana = AnalyticsSessionLocal()
            try:
                for name, tf, ic in logs:
                    ana.add(FactorPerformanceLog(
                        factor_name=name[:50],
                        factor_category="segmented_ic",
                        ic_value=ic,
                        decay_rate=None,
                        current_weight=None,
                        market_regime=None,
                        symbol=None,
                        timeframe=tf,
                    ))
                ana.commit()
            finally:
                ana.close()
        except Exception as err:
            logger.warning(f"[FactorIC-Seg] 分段留痕失败: {err}")

    logger.info(
        "[FactorIC-Seg] 分流评估: "
        + ", ".join(f"{k}={len(v)}因子" for k, v in out.items())
    )
    return out
