"""
FactorBridge — 新旧因子系统桥接层

将新系统 (FactorCalculator / BaseFactor → pd.Series) 的因子结果
转换为旧系统 (FactorEngine → Dict[str, FactorValue]) 的格式，
使 FactorSignalGenerator / DynamicFactorWeighting 等旧消费者无需修改即可消费新因子。
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np

from .base_factors import FactorCategory, FactorValue

logger = logging.getLogger(__name__)

# ── 订单流注入缓存（2026-07-08 短线提速）──
# inject_orderflow_for_factors 每币要跑 5 次 Market DB 查询 + 1 次衍生品快照，
# 实测短线路径 8~14s/币，是单轮扫描最大黑马。订单流指标（OI/CVD/TAKER）本就是按
# 5m 周期聚合的，一根 5m 蜡烛内不会变——因此按「(symbol, timeframe, 5m蜡烛桶)」缓存：
# 同一根蜡烛内必命中（无论重访多少次），换蜡烛才重查。相比 TTL 缓存，避免了「单轮
# 耗时 > TTL 导致重访时缓存已过期、永远命中不了」的陷阱。
_OF_CACHE: Dict[str, Dict[str, Any]] = {}
_OF_CANDLE_SEC = int(os.getenv("SCALP_FACTOR_CANDLE_SEC", "300") or 300)


def _candle_bucket(now: float) -> int:
    """5m 蜡烛桶编号：同一根蜡烛内所有时刻返回同一个整数。"""
    _sec = _OF_CANDLE_SEC if _OF_CANDLE_SEC > 0 else 300
    return int(now // _sec)

# 新系统 category → 旧系统 FactorCategory 映射
_CATEGORY_MAP = {
    "technical": FactorCategory.MOMENTUM,
    "fundamental": FactorCategory.STRENGTH,
    "sentiment": FactorCategory.SENTIMENT,
    "behavioral": FactorCategory.BEHAVIORAL,
    "derivatives": FactorCategory.DERIVATIVES,
    "onchain": FactorCategory.ONCHAIN,
    "macro": FactorCategory.MACRO,
    "composite": FactorCategory.STRENGTH,
    "volume": FactorCategory.VOLUME,
    "trend": FactorCategory.TREND,
    "momentum": FactorCategory.MOMENTUM,
    "volatility": FactorCategory.VOLATILITY,
    "mean_reversion": FactorCategory.MEAN_REVERSION,
    "market_flow": FactorCategory.MARKET_FLOW,
    "funding": FactorCategory.FUNDING,
}

# 子分类 → FactorCategory 细化映射（优先于粗分类）
_SUBCATEGORY_MAP = {
    "trend": FactorCategory.TREND,
    "momentum": FactorCategory.MOMENTUM,
    "volatility": FactorCategory.VOLATILITY,
    "volume": FactorCategory.VOLUME,
    "mean_reversion": FactorCategory.MEAN_REVERSION,
    "market_flow": FactorCategory.MARKET_FLOW,
    "funding": FactorCategory.FUNDING,
    "sentiment": FactorCategory.SENTIMENT,
    "onchain": FactorCategory.ONCHAIN,
    "derivatives": FactorCategory.DERIVATIVES,
    "pattern": FactorCategory.PATTERN,
    "strength": FactorCategory.STRENGTH,
}


def _map_category(category: str, subcategory: Optional[str] = None) -> FactorCategory:
    """将新系统分类映射为旧系统 FactorCategory 枚举。"""
    # 优先使用子分类
    if subcategory and subcategory in _SUBCATEGORY_MAP:
        return _SUBCATEGORY_MAP[subcategory]
    if category in _CATEGORY_MAP:
        return _CATEGORY_MAP[category]
    if category in _SUBCATEGORY_MAP:
        return _SUBCATEGORY_MAP[category]
    # 兜底
    try:
        return FactorCategory(category.upper())
    except ValueError:
        return FactorCategory.MOMENTUM


def series_to_factor_values(
    series_results: Dict[str, pd.Series],
    category: str = "technical",
    subcategory: Optional[str] = None,
) -> Dict[str, FactorValue]:
    """
    将新系统的 pd.Series 因子结果转换为旧系统 FactorValue 格式。

    每个 Series 取最后一个有效值作为标量 FactorValue。

    Args:
        series_results: {factor_id: pd.Series} 新系统因子结果
        category: 因子大类
        subcategory: 因子子类

    Returns:
        {factor_id: FactorValue} 旧系统格式
    """
    fc = _map_category(category, subcategory)
    out: Dict[str, FactorValue] = {}

    for fid, series in series_results.items():
        if series is None or (isinstance(series, pd.Series) and series.empty):
            continue
        try:
            # 取最后一个有效值
            if isinstance(series, pd.Series):
                valid = series.dropna()
                if valid.empty:
                    continue
                raw = float(valid.iloc[-1])
            else:
                raw = float(series)

            if np.isnan(raw) or np.isinf(raw):
                continue

            # [fix] z-score 归一化（与 base_factors.compute_all_factors 一致），
            # 替代旧 tanh。symbol 用 _global_newsys（新系统因子路径，调用方未传 symbol）。
            from .base_factors import _factor_normalizer
            normalized = _factor_normalizer.normalize("_global_newsys", fid, raw)

            out[fid] = FactorValue(
                name=fid,
                category=fc,
                value=raw,
                normalized=normalized,
            )
        except (TypeError, ValueError, OverflowError):
            continue

    return out


def compute_new_factors_as_legacy(
    df: pd.DataFrame,
    factor_ids: Optional[List[str]] = None,
    symbol: str = "UNKNOWN",
    timeframe: str = "1h",
) -> Dict[str, FactorValue]:
    """
    使用新系统 FactorCalculator 计算因子并转换为旧格式。

    Args:
        df: K 线数据 DataFrame
        factor_ids: 要计算的因子 ID 列表（None=全部已注册因子）
        symbol: 交易对（日志用）
        timeframe: 时间周期

    Returns:
        {factor_id: FactorValue} 旧系统格式
    """
    try:
        from .factor_calculator import FactorCalculator
        from .factor_registry import registry

        df = inject_deribit_into_klines(df, symbol)

        calc = FactorCalculator()

        # 确定要计算的因子列表
        if factor_ids is None:
            factor_ids = list(registry._factors.keys())

        if not factor_ids:
            return {}

        # 新系统计算
        series_map = calc.calculate(
            factor_ids=factor_ids,
            data=df,
            symbol=symbol,
            timeframe=timeframe,
            use_cache=True,
        )

        if not series_map:
            return {}

        # 转换为旧格式
        return series_to_factor_values(series_map)

    except Exception as e:
        logger.debug(f"[FactorBridge] 新系统计算失败 ({symbol}/{timeframe}): {e}")
        return {}


def merge_factor_results(
    legacy: Dict[str, FactorValue],
    new_system: Dict[str, FactorValue],
    prefer_new: bool = True,
) -> Dict[str, FactorValue]:
    """
    合并旧系统和新系统的因子结果。

    Args:
        legacy: 旧系统 FactorEngine 结果
        new_system: 新系统转换后的 FactorValue 结果
        prefer_new: 当 key 冲突时是否优先使用新系统

    Returns:
        合并后的 FactorValue 字典
    """
    merged = dict(legacy)
    for k, v in new_system.items():
        if prefer_new or k not in merged:
            merged[k] = v
    return merged


def inject_deribit_into_klines(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """opt-in：把 Deribit 期权列注入 K 线 DF（#12 主路径接线）。

    仅 DERIBIT_OPTIONS_ENABLED=true 且币种有期权链（BTC/ETH）时生效；
    失败静默降级，返回原 DF。
    """
    if df is None or len(df) == 0:
        return df
    try:
        from backend.services.factor_engine.factors.derivatives.deribit_options import (
            get_deribit_options_service,
            symbol_to_currency,
        )

        svc = get_deribit_options_service()
        if not getattr(svc, "enabled", False):
            return df
        if symbol_to_currency(symbol) is None:
            return df
        out = df.copy()
        svc.inject_into_klines(out, symbol)
        return out
    except Exception as exc:
        logger.debug("[FactorBridge] Deribit 注入跳过 %s: %s", symbol, exc)
        return df


def inject_orderflow_for_factors(
    symbol: str,
    market_data: Optional[Dict],
    timeframe: str = "5m",
) -> Dict:
    """给因子引擎的 market_data 注入订单流/衍生品数据。

    背景：scalp 等独立路径只传 K线给 compute_all_factors，导致
    cvd_ratio / oi_delta / taker_ratio / funding_rate 4 个订单流因子
    全部返回 0（形同虚设）。本函数从已就绪的数据源补齐这些字段：
      - OI_DELTA / CVD / TAKER ← market_flow_indicators DB（Market 库）
      - funding_rate           ← derivatives_analytics_service（已采集，60s TTL）

    数据缺失则不注入对应键（因子 has_data=False 自动过滤，不报错）。
    逻辑照搬 full_auto_trading_service._run_v3_factor_pipeline 的注入段，
    提炼为公共函数供 scalp 路径与主循环统一复用。

    Args:
        symbol: 交易对（如 "BTC"）
        market_data: 原 market_data dict（会被原地补充并返回）
        timeframe: 衍生品指标周期（短线 "5m"）

    Returns:
        补充后的 market_data dict
    """
    md = market_data if isinstance(market_data, dict) else {}
    sym = (symbol or "").upper()

    # ── 缓存命中：同一根 5m 蜡烛桶内直接复用上次注入的订单流字段（避免重复查库）──
    _now = time.time()
    _bucket = _candle_bucket(_now)
    _ck = f"{sym}:{timeframe}"
    _hit = _OF_CACHE.get(_ck)
    if _hit and _hit.get("bucket") == _bucket:
        # 只补齐 md 里尚未存在的键（factor_v3 等上游可能已写入更准的值）
        for _k, _v in _hit["data"].items():
            md.setdefault(_k, _v)
        return md

    # 缓存未命中：查库，把结果先收集到 _of，再统一合并 + 落缓存
    _of: Dict[str, Any] = {}

    # 1. Market DB 订单流指标（OI_DELTA / CVD / TAKER / DEPTH / IMBALANCE）
    try:
        from backend.services.market_flow_indicators import get_indicator_value as _giv
        _oi_delta = _giv(None, sym, "OI_DELTA", timeframe)
        if _oi_delta is not None:
            _of["oi_delta_pct"] = float(_oi_delta)
            _of["oi"] = 1.0  # 占位，因子用 oi_delta_pct 更准
            _of["prev_oi"] = 1.0 / (1 + float(_oi_delta) / 100) if _oi_delta != 0 else 1.0
        _cvd = _giv(None, sym, "CVD", timeframe)
        if _cvd is not None:
            _of["cvd"] = float(_cvd)
            _of.setdefault("total_notional", abs(float(_cvd)) * 10 or 1.0)
        _taker = _giv(None, sym, "TAKER", timeframe)
        if _taker is not None:
            _taker_f = float(_taker)
            if _taker_f > 0:
                _of["buy_notional"] = _taker_f
                _of["sell_notional"] = 1.0
            elif _taker_f < 0:
                _of["buy_notional"] = 1.0
                _of["sell_notional"] = abs(_taker_f)
        _depth = _giv(None, sym, "DEPTH", timeframe)
        if _depth is not None:
            _of["depth_ratio"] = float(_depth)
        _imb = _giv(None, sym, "IMBALANCE", timeframe)
        if _imb is not None:
            _of["imbalance"] = float(_imb)
    except Exception as _e:
        logger.debug(f"[FactorBridge] {sym} 订单流指标注入跳过: {_e}")

    # 2. funding_rate ← derivatives_analytics_service（已采集，funding/OI/清算）
    # 用 get_cached_snapshot（只读缓存 + 后台异步刷新），绝不在 scalp 热路径里同步拉
    # Hyperliquid/Binance/Coinalyze（原 get_snapshot miss 时串行网络实测 ~12s/币，
    # 是短线单币扫描 20s+ 的主因）。缓存未命中时返回 None → 本轮跳过 funding 因子，
    # 后台线程刷新后下轮即命中。
    try:
        from backend.services.derivatives_analytics_service import derivatives_analytics
        _snap = derivatives_analytics.get_cached_snapshot(sym)
        if _snap is not None and getattr(_snap, "funding_rate", None) is not None:
            _of["funding_rate"] = float(_snap.funding_rate)
    except Exception as _e:
        logger.debug(f"[FactorBridge] {sym} funding_rate 注入跳过: {_e}")

    # 合并到 md（覆盖，保持与旧行为一致）+ 落缓存（记录蜡烛桶）
    md.update(_of)
    _OF_CACHE[_ck] = {"bucket": _bucket, "data": _of}
    return md
