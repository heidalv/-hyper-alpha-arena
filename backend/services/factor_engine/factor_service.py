"""统一因子服务 FactorService — 全系统唯一因子计算入口（方案需求 5）

背景（重构前的分叉，已通过本服务收敛）：
  - /api/factors/values      走新 FactorRegistry（compute_new_factors_as_legacy）
  - /api/factors/signals     走旧 factor_engine.compute_all_factors
  - /api/analytics/factors   又走旧 factor_engine.compute_all_factors
三条路径归一化实现不同，导致同一 symbol 因子数量 / 归一化口径不一致。

本服务把"取 K 线 → 计算因子 → 合成信号 → IC 有效性检验"统一到一条 Registry 路径，
供假设引擎 / 进化 / RL / 在线学习 / 前端所有模块一致调用。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 默认 K 线回退顺序（与原三处端点保持一致）
_DEFAULT_TF_FALLBACK = ("1h", "4h")
_KLINE_COUNT = 200
_MIN_BARS = 20

# ── compute() 结果 TTL 缓存 ──
# 全量计算一次覆盖 380+ 因子，单次耗时可达数秒到数十秒；15m/1h K 线本身也不会
# 秒级变化。前端仪表盘、假设引擎、信号面板等多处会在短时间内对同一 symbol+timeframe
# 反复调用，命中缓存可避免重复的重计算拖慢整个后端（表现为"读取数据时几乎卡死"）。
_compute_cache: Dict[str, Dict[str, Any]] = {}
# 系统整体负载较重（调度器任务 + 大量因子注册 + 交易所行情拉取），单次全量计算
# 实测在 3~30s+ 间大幅波动；TTL 定得比轮询间隔更长，确保"缓存命中"能真正生效，
# 而不是每次都刚好因为上一次计算太慢而错过窗口、又触发一次重计算。
_COMPUTE_TTL_SECONDS = 60.0
# 同一 symbol+timeframe 的并发请求共享同一把锁：避免"缓存未命中"时被多个
# widget/轮询同时撞上，各自触发一次重计算、成倍放大负载（cache stampede）。
# 后来者会阻塞等待前者算完，直接复用结果，而不是各算各的。
_compute_locks: Dict[str, threading.Lock] = {}
_compute_locks_guard = threading.Lock()


def _get_compute_lock(key: str) -> threading.Lock:
    with _compute_locks_guard:
        lock = _compute_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _compute_locks[key] = lock
        return lock


class FactorService:
    """统一因子服务（单例 factor_service）。"""

    # ── K 线加载（去重原三处重复逻辑）──

    def _load_klines(self, symbol: str, timeframe: str):
        import pandas as pd
        from backend.services.kline_data_service import kline_service

        tried = []
        for tf in (timeframe, *[t for t in _DEFAULT_TF_FALLBACK if t != timeframe]):
            tried.append(tf)
            try:
                raw = kline_service.get_klines_from_db(symbol, tf, count=_KLINE_COUNT)
            except Exception as exc:
                logger.debug("[FactorService] 取 %s %s K线失败: %s", symbol, tf, exc)
                raw = None
            if raw and len(raw) >= _MIN_BARS:
                return pd.DataFrame(raw), tf
        logger.debug("[FactorService] %s 无足够 K 线（尝试 %s）", symbol, tried)
        return None, None

    def _ensure_registry_loaded(self) -> None:
        """确保云端 / 本地因子已加载到 Registry（best-effort，运行时导入避免循环依赖）。"""
        try:
            from backend.api.factor_sync_routes import _ensure_cloud_factors_loaded
            _ensure_cloud_factors_loaded()
        except Exception as exc:
            logger.debug("[FactorService] ensure_registry_loaded 跳过: %s", exc)

    # ── 核心：单一计算入口 ──

    def compute(
        self,
        symbol: str,
        timeframe: str = "15m",
        factor_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """计算某标的的全部（或指定）因子，返回 {factor_id: FactorValue}。

        统一走新 FactorRegistry 路径（含 legacy_compat 21 短名 + 新规范名因子）。
        短 TTL 缓存命中时直接返回，避免同一 symbol+timeframe 在缓存窗口内被并发/
        轮询重复触发全量重计算。
        """
        from backend.services.factor_engine.factor_bridge import compute_new_factors_as_legacy

        cache_key = f"{symbol}:{timeframe}:{','.join(factor_ids) if factor_ids else '*'}"

        def _cached() -> Optional[Dict[str, Any]]:
            hit = _compute_cache.get(cache_key)
            if hit and (time.time() - hit["ts"]) < _COMPUTE_TTL_SECONDS:
                return hit["data"]
            return None

        result = _cached()
        if result is not None:
            return result

        lock = _get_compute_lock(cache_key)
        with lock:
            # 双重检查：等锁期间，先到的请求可能已经把结果算好并写入缓存了
            result = _cached()
            if result is not None:
                return result

            df, _tf = self._load_klines(symbol, timeframe)
            if df is None or df.empty:
                return {}
            self._ensure_registry_loaded()
            result = compute_new_factors_as_legacy(
                df, factor_ids=factor_ids, symbol=symbol, timeframe=timeframe
            )
            _compute_cache[cache_key] = {"ts": time.time(), "data": result}
            return result

    def compute_as_list(self, symbol: str, timeframe: str = "15m") -> Dict[str, Any]:
        """API 友好格式：{factors: [...], symbol, total_count}。"""
        fv_map = self.compute(symbol, timeframe)
        if not fv_map:
            return {"factors": [], "symbol": symbol, "total_count": 0, "error": "insufficient kline data"}
        factors_list = [
            {
                "name": fv.name,
                "value": round(fv.value, 6),
                "normalized": round(fv.normalized, 4),
                "category": fv.category.value if hasattr(fv.category, "value") else str(fv.category),
            }
            for fv in fv_map.values()
        ]
        return {"factors": factors_list, "symbol": symbol, "total_count": len(factors_list)}

    # ── 信号合成（复用同一因子 map，消除与 values 的口径分叉）──

    def signals(self, symbol: str, timeframe: str = "15m") -> Dict[str, Any]:
        from backend.services.factor_engine.factor_signal_generator import FactorSignalGenerator

        fv_map = self.compute(symbol, timeframe)
        if not fv_map:
            return {"error": "insufficient kline data", "symbol": symbol,
                    "direction": 0.0, "strength": 0.0, "confidence": 0.0, "factors": 0}

        gen = FactorSignalGenerator()
        signal = gen.generate_signals(fv_map, symbol=symbol, timeframe=timeframe)
        return {
            "symbol": symbol,
            "direction": signal.direction,
            "strength": signal.strength,
            "confidence": signal.confidence,
            "contributing_factors": signal.contributing_factors,
            "regime": signal.regime,
            "factor_details": {
                name: {
                    "direction": sig.direction,
                    "strength": sig.strength,
                    "category": sig.category,
                }
                for name, sig in signal.signals.items()
            },
        }

    # ── IC 因子有效性检验（供全模块一致调用）──

    def evaluate_ic(
        self,
        symbol: str,
        timeframe: str = "15m",
        top_n: int = 30,
        forward_period: int = 5,
    ) -> Dict[str, Any]:
        """对该标的因子做 IC/ICIR/衰减/评级批量评估。

        [2026-08-19 修复] 此前对 380+ 全注册因子全量重算（calc 无缓存），
        接口 60s+ 超时导致因子报告卡恒空白。现改为：
        ① 与 /values 同口径的可用因子集（self.compute 带 60s TTL 缓存）；
        ② calc.calculate 开 use_cache；
        ③ 报告结果加 60s TTL 缓存（同 compute 防 stampede 模式）。
        """
        from backend.services.factor_engine.factor_calculator import FactorCalculator
        from backend.services.factor_engine.factor_evaluator import FactorEvaluator

        cache_key = f"ic:{symbol}:{timeframe}:{forward_period}"
        lock = _get_compute_lock(cache_key)
        with lock:
            hit = _compute_cache.get(cache_key)
            if hit and (time.time() - hit["ts"]) < _COMPUTE_TTL_SECONDS:
                return hit["data"]

            df, used_tf = self._load_klines(symbol, timeframe)
            if df is None or df.empty:
                return {"symbol": symbol, "reports": [], "error": "insufficient kline data"}
            if "close" not in df.columns:
                return {"symbol": symbol, "reports": [], "error": "kline missing close column"}

            self._ensure_registry_loaded()
            # 与 /values 同口径的可用因子集（已含 60s 缓存），避免全注册因子重算
            try:
                fv_map = self.compute(symbol, timeframe)
                factor_ids = [k for k in (fv_map or {}) if fv_map[k] is not None]
            except Exception:
                factor_ids = []
            if not factor_ids:
                return {"symbol": symbol, "reports": [], "error": "no registered factors"}

            calc = FactorCalculator()
            try:
                series_map = calc.calculate(
                    factor_ids, df, symbol=symbol, timeframe=used_tf or timeframe,
                    use_cache=True,
                )
            except Exception as exc:
                logger.warning("[FactorService] IC 计算因子序列失败: %s", exc)
                return {"symbol": symbol, "reports": [], "error": str(exc)[:200]}

            evaluator = FactorEvaluator(forward_period=forward_period)
            reports = evaluator.evaluate_batch(series_map, df["close"], top_n=top_n)
            result = {
                "symbol": symbol,
                "timeframe": used_tf or timeframe,
                "reports": [
                    {
                        "factor_id": r.factor_id,
                        "ic_mean": round(r.ic_mean, 5),
                        "icir": round(r.icir, 4),
                        "ic_positive_pct": round(r.ic_positive_pct, 4),
                        "ic_decay_halflife": r.ic_decay_halflife,
                        "turnover": round(r.turnover, 4),
                        "monotonicity": round(r.monotonicity, 4),
                        "grade": r.grade,
                        "data_points": r.data_points,
                    }
                    for r in reports
                ],
            }
            _compute_cache[cache_key] = {"ts": time.time(), "data": result}
            return result

    # ── 注册表访问 ──

    def registry(self):
        from backend.services.factor_engine.factor_registry import registry
        return registry

    def registered_factor_ids(self) -> List[str]:
        try:
            return list(self.registry()._factors.keys())
        except Exception:
            return []


# 单例
factor_service = FactorService()
