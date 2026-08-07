"""市场扫描 — 从 monolith _scan_markets/_bg_market_scan 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class MarketScanHost:
    market_scan_cache: Dict[str, Any]
    market_scan_cache_ts: float
    market_scan_cache_ttl: float
    bg_scan_running: bool = False


def build_market_scan_host(svc) -> MarketScanHost:
    return MarketScanHost(
        market_scan_cache=svc._market_scan_cache,
        market_scan_cache_ts=svc._market_scan_cache_ts,
        market_scan_cache_ttl=svc._MARKET_SCAN_CACHE_TTL,
        bg_scan_running=getattr(svc, "_bg_scan_running", False),
    )


def run_scan_markets(db: Session, symbols: List[str], host: MarketScanHost) -> Dict[str, Any]:
    now = time.time()

    if (now - host.market_scan_cache_ts < host.market_scan_cache_ttl
            and host.market_scan_cache
            and set(symbols).issubset(host.market_scan_cache.keys())):
        logger.debug("[FullAuto] 使用市场扫描缓存")
        return {s: host.market_scan_cache[s] for s in symbols if s in host.market_scan_cache}

    # 2026-07-06 整改（unified_data_pool 全量整合 · 灰度切片）：
    # 默认关闭（COORDINATOR_CONSUME_SNAPSHOT_KLINES）。开启后，市场扫描复用主链
    # 已采集统一快照里的 K 线，与决策同一时点，消除 coordinator 自行重拉造成的时点漂移；
    # 快照缺失/过薄的周期由 _load_env_klines 自动回退实时拉取（correctness 不受影响）。
    # 回滚 = 关掉该开关即恢复旧行为。
    _snap_klines: Dict[str, dict] = {}
    try:
        if os.getenv("COORDINATOR_CONSUME_SNAPSHOT_KLINES", "false").lower() in ("1", "true", "yes", "on"):
            from backend.services.unified_data_pool import unified_data_pool
            _snap = unified_data_pool.get_snapshot(
                max_age=float(os.getenv("COORDINATOR_SNAPSHOT_MAX_AGE_SEC", "180") or 180)
            )
            if _snap is not None:
                for _s in symbols:
                    _kd = unified_data_pool.klines_for_coordinator(_s, _snap)
                    if _kd:
                        _snap_klines[_s] = _kd
                logger.debug(
                    "[FullAuto] 扫描复用快照 K 线: %d/%d symbols", len(_snap_klines), len(symbols)
                )
    except Exception as _e:
        logger.debug("[FullAuto] 快照 K 线复用不可用，回退实时拉取: %s", _e)
        _snap_klines = {}

    result = {}
    try:
        scan_time = datetime.now(timezone.utc).isoformat()

        def _scan_one(symbol: str) -> Tuple[str, Dict]:
            """独立线程扫描 — 价格独立获取，分析失败不丢价格"""
            from backend.database.connection import SessionLocal
            from backend.services.strategy_coordinator import StrategyCoordinator
            _db = SessionLocal()

            # D7: 先快速获取价格（独立于完整分析）
            _quick_price = 0.0
            _price_source = "unknown"
            try:
                from backend.services.exchange_config import get_active_exchange
                _quick_price = StrategyCoordinator._get_realtime_price_robust(symbol, get_active_exchange())
                _price_source = "realtime"
            except Exception:
                pass

            try:
                coordinator = StrategyCoordinator(_db)
                # 灰度切片：有快照 K 线则传入（时点一致）；无则 None（行为同旧版）
                env = coordinator.analyze_market_environment(
                    symbol, kline_data=_snap_klines.get(symbol)
                )
                _price = getattr(env, "current_price", 0) or _quick_price
                data_source = getattr(env, "data_source", "unknown")
                _ps = getattr(env, "price_source", "unknown") if _price > 0 else _price_source
                return symbol, {
                    "market_cycle": getattr(env, "market_cycle", "unknown"),
                    "cycle_confidence": getattr(env, "cycle_confidence", 0),
                    "trend_direction": getattr(env, "trend_direction", "neutral"),
                    "volatility_regime": getattr(env, "volatility_regime", "normal"),
                    "volatility_value": getattr(env, "volatility_value", 0),
                    "atr_value": getattr(env, "atr_value", 0),
                    "atr_1d_value": getattr(env, "atr_1d_value", 0),
                    "atr_1d_pct": getattr(env, "atr_1d_pct", 0),
                    "adapted_entry_threshold": getattr(env, "adapted_entry_threshold", 0.6),
                    "adapted_sl_multiplier": getattr(env, "adapted_sl_multiplier", 1.0),
                    "adapted_tp_multiplier": getattr(env, "adapted_tp_multiplier", 1.0),
                    "adapted_position_scale": getattr(env, "adapted_position_scale", 1.0),
                    "current_price": _price,
                    "risk_budget_pct": getattr(env, "risk_budget_pct", 0.5),
                    "trend_strength": getattr(env, "trend_strength", 0),
                    "liquidity_score": getattr(env, "liquidity_score", 1.0),
                    "scan_time": scan_time,
                    "sentiment_index": getattr(env, "sentiment_index", 50),
                    "sentiment_zone": getattr(env, "sentiment_zone", "neutral"),
                    "whale_direction": getattr(env, "whale_direction", 0),
                    "derivatives_signal": getattr(env, "derivatives_signal", "neutral"),
                    "funding_rate": getattr(env, "funding_rate", 0),
                    "news_impact": getattr(env, "news_impact", 0),
                    "news_top_event": getattr(env, "news_top_event", ""),
                    "fear_greed": getattr(env, "fear_greed", 50),
                    "data_source": data_source,
                    "price_source": _ps,
                    "data_reliable": data_source not in (
                        "default", "unknown", "insufficient_klines", "cache_miss",
                    ),
                    "price_stale_warning": getattr(env, "price_stale_warning", False),
                    "kline_count": getattr(env, "kline_count", 0),
                    "kline_age_hours": getattr(env, "kline_age_hours", 0),
                    # D7: 因子引擎字段
                    "factor_direction": getattr(env, "factor_direction", 0),
                    "factor_strength": getattr(env, "factor_strength", 0),
                    "factor_confidence": getattr(env, "factor_confidence", 0),
                    "factor_regime": getattr(env, "factor_regime", "unknown"),
                    "factor_regime_confidence": getattr(env, "factor_regime_confidence", 0),
                    # P0: 高阶K线衍生特征 (12个)
                    "body_ratio": getattr(env, "body_ratio", 0),
                    "upper_shadow_ratio": getattr(env, "upper_shadow_ratio", 0),
                    "lower_shadow_ratio": getattr(env, "lower_shadow_ratio", 0),
                    "doji_score": getattr(env, "doji_score", 0),
                    "volume_price_corr": getattr(env, "volume_price_corr", 0),
                    "volatility_skew": getattr(env, "volatility_skew", 0),
                    "trend_efficiency": getattr(env, "trend_efficiency", 0),
                    "volume_climax": getattr(env, "volume_climax", 1.0),
                    "price_acceleration": getattr(env, "price_acceleration", 0),
                    "ema_ribbon_width": getattr(env, "ema_ribbon_width", 0),
                    "rsi_divergence": getattr(env, "rsi_divergence", 0),
                    "volume_imbalance": getattr(env, "volume_imbalance", 0),
                    # P0: VPVR v2 成交量分布
                    "poc_price": getattr(env, "poc_price", 0),
                    "vah_price": getattr(env, "vah_price", 0),
                    "val_price": getattr(env, "val_price", 0),
                    "current_in_va": getattr(env, "current_in_va", False),
                    "nearest_hvn": getattr(env, "nearest_hvn", 0),
                    "nearest_lvn": getattr(env, "nearest_lvn", 0),
                    # P0: 因子融合信号
                    "fusion_mode": getattr(env, "fusion_mode", "ic_weighted"),
                    "fusion_direction": getattr(env, "fusion_direction", 0),
                    "fusion_strength": getattr(env, "fusion_strength", 0),
                    "fusion_confidence": getattr(env, "fusion_confidence", 0),
                    # P0: 多频率约束
                    "freq_4h_direction": getattr(env, "freq_4h_direction", 0),
                    "freq_1h_direction": getattr(env, "freq_1h_direction", 0),
                    "freq_15m_direction": getattr(env, "freq_15m_direction", 0),
                    "constraint_violated": getattr(env, "constraint_violated", False),
                    "constraint_reason": getattr(env, "constraint_reason", ""),
                    # P2: 多频率对齐
                    "multi_freq_alignment": getattr(env, "multi_freq_alignment", "unknown"),
                    "multi_freq_dominant": getattr(env, "multi_freq_dominant", "unknown"),
                    # 2026-07-06 整改：strategy_coordinator.MarketEnvironment 字段已改名为
                    # coordinator_alignment_score（与 QuantBrief 0-15 整数版 alignment_score
                    # 区分命名空间），此处同步跟进，否则 getattr 旧名会静默恒为 0。
                    "alignment_score": getattr(env, "coordinator_alignment_score", 0),
                    "entry_timing_score": getattr(env, "entry_timing_score", 0),
                }
            except Exception as e:
                logger.warning(f"[FullAuto] 扫描 {symbol} 失败: {e}")
                # D7: 即使分析失败，也返回价格数据
                if _quick_price > 0:
                    return symbol, {
                        "market_cycle": "unknown", "cycle_confidence": 0,
                        "trend_direction": "neutral", "volatility_regime": "normal",
                        "current_price": _quick_price,
                        "data_source": "fallback", "price_source": _price_source,
                        "data_reliable": True, "price_stale_warning": "",
                        "scan_time": scan_time,
                    }
                return symbol, {"error": str(e)}
            finally:
                _db.close()

        from concurrent.futures import ThreadPoolExecutor, as_completed
        _t0 = time.time()
        # [2026-07-11 修复] 8 路并行技术分析(pandas/numpy 计算)会在同一秒内跟事件循环
        # 线程疯狂抢 GIL，是接口"卡顿10s+"的直接触发点之一。降到 4 路：单次扫描慢
        # 1~2 秒，换来事件循环线程不被长时间饿死（配合 run_uvicorn_dev.py 里调小的
        # GIL 切换间隔一起生效）。
        with ThreadPoolExecutor(max_workers=min(len(symbols), 4)) as pool:
            futures = {pool.submit(_scan_one, s): s for s in symbols}
            for fut in as_completed(futures, timeout=60):
                try:
                    sym, data = fut.result(timeout=50)
                    result[sym] = data
                except Exception as e:
                    sym = futures[fut]
                    logger.warning(f"[FullAuto] 并行扫描 {sym} 超时/异常: {e}")
                    result[sym] = {"error": str(e)}
        logger.info(
            f"[FullAuto] 并行市场扫描完成: {len(result)}/{len(symbols)} 耗时{time.time()-_t0:.1f}s")

    except Exception as e:
        logger.warning(f"[FullAuto] 市场扫描初始化失败: {e}")

    if result:
        host.market_scan_cache = result
        host.market_scan_cache_ts = now

    return result


def run_bg_market_scan(symbols: List[str], host: MarketScanHost, scan_fn: Callable = run_scan_markets) -> None:
    try:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            scan_fn(db, symbols, host)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[FullAuto] 后台扫描失败: {e}")
    finally:
        host.bg_scan_running = False
