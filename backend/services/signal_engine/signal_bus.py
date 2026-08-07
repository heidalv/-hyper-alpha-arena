"""
UnifiedSignalBus — 统一信号总线

聚合 4 套信号源（因子引擎、情报汇流、三维确认、决策融合），
输出统一的 UnifiedSignal 格式，支持共振检测和冲突惩罚。

设计模式:
  - 线程安全单例 (双检锁)
  - 45 秒缓存 TTL
  - 延迟导入 (避免循环依赖)
"""

import time
import math
import logging
import threading
from typing import Any, Dict, List, Optional

from .unified_signal import (
    SourceSignal,
    UnifiedSignal,
    SOURCE_FACTOR, SOURCE_INTEL, SOURCE_CONFIRM, SOURCE_FUSION,
    ACTION_BUY, ACTION_SELL, ACTION_HOLD,
    CONFLUENCE_STRONG_RESONANCE, CONFLUENCE_RESONANCE,
    CONFLUENCE_NEUTRAL, CONFLUENCE_CONFLICT, CONFLUENCE_STRONG_CONFLICT,
    direction_to_action, clamp, make_empty_signal,
)
from .adapters import signal_adapter_manager

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  默认源权重
# ════════════════════════════════════════════════════════════

DEFAULT_SOURCE_WEIGHTS = {
    SOURCE_FACTOR: 0.35,   # 因子引擎 — 20+ 因子加权，权重最高
    SOURCE_INTEL: 0.30,    # 情报汇流 — 8 源汇流，权重次高
    SOURCE_CONFIRM: 0.20,  # 三维确认 — 跨维度验证
    SOURCE_FUSION: 0.15,   # 决策融合 — 已包含部分因子信息
}


class UnifiedSignalBus:
    """统一信号总线 — 聚合所有信号源为统一格式"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._cache: Dict[str, UnifiedSignal] = {}
        self._cache_ts: Dict[str, float] = {}
        self._cache_ttl: float = 45.0
        self._max_cache_size: int = 200

        self._source_weights: Dict[str, float] = dict(DEFAULT_SOURCE_WEIGHTS)

        self._detail_cache: Dict[str, Dict[str, Any]] = {}
        self._detail_cache_ts: Dict[str, float] = {}

        logger.info("[SignalBus] 统一信号总线已初始化")

    # ──────────────────────────────────────────────────────────
    #  公共接口
    # ──────────────────────────────────────────────────────────

    def get_unified_signal(
        self, symbol: str, force_refresh: bool = False
    ) -> UnifiedSignal:
        """获取统一融合信号（带缓存）"""
        now = time.time()

        if not force_refresh:
            cached = self._cache.get(symbol)
            cached_ts = self._cache_ts.get(symbol, 0)
            if cached and (now - cached_ts) < self._cache_ttl:
                return cached

        # 采集所有信号源
        sources = self._collect_all_sources(symbol)

        if not sources:
            empty = make_empty_signal(symbol)
            self._cache[symbol] = empty
            self._cache_ts[symbol] = now
            return empty

        # 融合信号
        result = self._fuse_signals(symbol, sources)

        # 更新缓存
        self._cache[symbol] = result
        self._cache_ts[symbol] = now
        self._cleanup_cache()

        return result

    def get_signal_detail(self, symbol: str) -> Dict[str, Any]:
        """获取各信号源详细分解"""
        now = time.time()
        cached = self._detail_cache.get(symbol)
        cached_ts = self._detail_cache_ts.get(symbol, 0)
        if cached and (now - cached_ts) < self._cache_ttl:
            return cached

        # 获取融合信号
        unified = self.get_unified_signal(symbol)

        # 构建详细数据
        detail = {
            "symbol": unified.symbol,
            "direction": unified.direction,
            "confidence": unified.confidence,
            "strength": unified.strength,
            "action": unified.action,
            "confluence_level": unified.confluence_level,
            "reasoning": unified.reasoning,
            "sources": {},
        }

        # 各源详细数据
        for sid, src in unified.sources.items():
            detail["sources"][sid] = {
                "source_name": src.source_name,
                "direction": src.direction,
                "confidence": src.confidence,
                "strength": src.strength,
                "weight": src.weight,
                "action": src.action,
                "raw_data": src.raw_data,
            }

        # 附加条件信号（来自 SignalDetectionService，不参与融合）
        detail["active_triggers"] = self._get_active_triggers(symbol)

        # 融合调试信息
        detail["confluence_debug"] = {
            "source_count": unified.source_count,
            "agreeing_sources": unified.agreeing_sources,
            "conflicting_sources": unified.conflicting_sources,
            "source_weights": dict(self._source_weights),
        }

        self._detail_cache[symbol] = detail
        self._detail_cache_ts[symbol] = now
        return detail

    def update_source_weights(self, weights: Dict[str, float]) -> None:
        """运行时更新源权重（供未来进化器使用）"""
        for k, v in weights.items():
            if k in self._source_weights:
                self._source_weights[k] = clamp(float(v), 0.0, 1.0)
        self._cache.clear()
        self._cache_ts.clear()
        self._detail_cache.clear()
        self._detail_cache_ts.clear()
        logger.info(f"[SignalBus] 源权重已更新: {self._source_weights}")

    # ──────────────────────────────────────────────────────────
    #  信号源采集（延迟导入）
    # ──────────────────────────────────────────────────────────

    def _collect_all_sources(self, symbol: str) -> Dict[str, SourceSignal]:
        """采集所有信号源，各源独立 try/except 隔离"""
        sources: Dict[str, SourceSignal] = {}

        # 1. 因子信号
        try:
            src = self._collect_factor(symbol)
            if src:
                src.weight = self._source_weights.get(SOURCE_FACTOR, 0.35)
                sources[SOURCE_FACTOR] = src
        except Exception as e:
            logger.debug(f"[SignalBus] factor 采集失败 {symbol}: {e}")

        # 2. 情报信号
        try:
            src = self._collect_intel(symbol)
            if src:
                src.weight = self._source_weights.get(SOURCE_INTEL, 0.30)
                sources[SOURCE_INTEL] = src
        except Exception as e:
            logger.debug(f"[SignalBus] intel 采集失败 {symbol}: {e}")

        # 3. 三维确认（需要 klines+derivatives）
        try:
            src = self._collect_confirm(symbol)
            if src:
                src.weight = self._source_weights.get(SOURCE_CONFIRM, 0.20)
                sources[SOURCE_CONFIRM] = src
        except Exception as e:
            logger.debug(f"[SignalBus] confirm 采集失败 {symbol}: {e}")

        # 4. 决策融合（需要 factor_values）
        try:
            src = self._collect_fusion(symbol, sources)
            if src:
                src.weight = self._source_weights.get(SOURCE_FUSION, 0.15)
                sources[SOURCE_FUSION] = src
        except Exception as e:
            logger.debug(f"[SignalBus] fusion 采集失败 {symbol}: {e}")

        return sources

    def _collect_factor(self, symbol: str) -> Optional[SourceSignal]:
        """因子信号采集: FactorEngine -> FactorSignalGenerator -> Adapter"""
        from backend.services.factor_engine.base_factors import factor_engine
        from backend.services.factor_engine.factor_signal_generator import FactorSignalGenerator
        from backend.services.kline_data_service import kline_service

        raw = None
        for tf in ("15m", "1h", "4h"):
            try:
                klines = kline_service.get_klines_from_db(symbol, tf, count=200)
                if klines and len(klines) >= 20:
                    import pandas as pd
                    raw = pd.DataFrame(klines)
                    break
            except Exception:
                continue

        if raw is None or raw.empty:
            return None

        fv_map = factor_engine.compute_all_factors(raw)
        if not fv_map:
            return None

        gen = FactorSignalGenerator()
        composite = gen.generate_signals(fv_map, symbol=symbol)
        return signal_adapter_manager.adapt(SOURCE_FACTOR, composite, symbol=symbol)

    def _collect_intel(self, symbol: str) -> Optional[SourceSignal]:
        """情报信号采集: IntelligenceSignalEngine -> Adapter"""
        from backend.services.intelligence_signal_engine import intel_signal_engine

        sig = intel_signal_engine.compute_trading_signal(symbol)
        if sig is None:
            return None
        return signal_adapter_manager.adapt(SOURCE_INTEL, sig)

    def _collect_confirm(self, symbol: str) -> Optional[SourceSignal]:
        """三维确认采集: SignalConfirmationEngine -> Adapter"""
        from backend.services.signal_confirmation_engine import signal_confirmation_engine
        from backend.services.unified_data_pool import unified_data_pool

        # 获取 klines 和 derivatives
        klines_1h = None
        try:
            klines_1h = unified_data_pool.get_klines(symbol, "1h", count=60)
        except Exception:
            pass

        derivatives = None
        try:
            derivatives = unified_data_pool.get_derivatives(symbol)
        except Exception:
            pass

        if klines_1h is None and derivatives is None:
            return None

        result = signal_confirmation_engine.evaluate(
            symbol=symbol,
            klines_1h=klines_1h,
            derivatives_data=derivatives,
        )
        return signal_adapter_manager.adapt(SOURCE_CONFIRM, result)

    def _collect_fusion(
        self, symbol: str, existing_sources: Dict[str, SourceSignal]
    ) -> Optional[SourceSignal]:
        """决策融合采集: DecisionFusionEngine -> Adapter"""
        from backend.services.factor_engine.decision_fusion_engine import DecisionFusionEngine

        # 复用已采集的因子数据
        factor_src = existing_sources.get(SOURCE_FACTOR)
        if factor_src is None or factor_src.raw_data is None:
            return None

        # 需要从因子引擎重新获取 FactorValue dict
        try:
            from backend.services.factor_engine.base_factors import factor_engine
            from backend.services.kline_data_service import kline_service
            import pandas as pd

            raw = None
            for tf in ("15m", "1h", "4h"):
                klines = kline_service.get_klines_from_db(symbol, tf, count=200)
                if klines and len(klines) >= 20:
                    raw = pd.DataFrame(klines)
                    break
            if raw is None:
                return None
            fv_map = factor_engine.compute_all_factors(raw)
            if not fv_map:
                return None

            engine = DecisionFusionEngine()
            decision = engine.fuse(fv_map, regime=factor_src.raw_data.get("regime", "unknown"))
            return signal_adapter_manager.adapt(SOURCE_FUSION, decision)
        except Exception:
            return None

    def _get_active_triggers(self, symbol: str) -> List[Dict[str, Any]]:
        """获取 SignalDetectionService 的激活信号（辅助元数据）"""
        try:
            from backend.services.signal_detection_service import signal_detection_service
            states = signal_detection_service.get_signal_states()
            active = []
            pool_states = states.get("pool_states", {})
            for key, state in pool_states.items():
                if symbol in key and state.get("is_active"):
                    active.append({"pool_key": key, "state": state})
            return active
        except Exception:
            return []

    # ──────────────────────────────────────────────────────────
    #  信号融合逻辑
    # ──────────────────────────────────────────────────────────

    def _fuse_signals(
        self, symbol: str, sources: Dict[str, SourceSignal]
    ) -> UnifiedSignal:
        """多源加权融合 + 共振检测 + 冲突惩罚"""
        now = time.time()

        if not sources:
            return make_empty_signal(symbol)

        # 1. 加权方向聚合
        weighted_dir_sum = 0.0
        weighted_conf_sum = 0.0
        for src in sources.values():
            w = src.weight
            c = max(src.confidence, 0.01)  # 避免 confidence=0 导致权重消失
            weighted_dir_sum += src.direction * w * c
            weighted_conf_sum += w * c

        composite_dir = weighted_dir_sum / weighted_conf_sum if weighted_conf_sum > 0 else 0.0
        composite_dir = clamp(composite_dir, -1.0, 1.0)

        # 2. 加权强度聚合
        weighted_str_sum = 0.0
        for src in sources.values():
            w = src.weight
            weighted_str_sum += src.strength * w
        composite_str = weighted_str_sum / sum(s.weight for s in sources.values())
        composite_str = clamp(composite_str, 0.0, 1.0)

        # 3. 共振 / 冲突检测
        directions = [s.direction for s in sources.values()]
        positive = sum(1 for d in directions if d > 0.05)
        negative = sum(1 for d in directions if d < -0.05)
        neutral = len(directions) - positive - negative

        agreeing = max(positive, negative)
        conflicting = min(positive, negative)
        total = len(directions)

        confluence_level = CONFLUENCE_NEUTRAL
        resonance_factor = 1.0

        if total > 0:
            if conflicting == 0 and agreeing >= 2:
                if agreeing == total:
                    confluence_level = CONFLUENCE_STRONG_RESONANCE
                    resonance_factor = 1.0 + 0.2 * (agreeing / total)
                else:
                    confluence_level = CONFLUENCE_RESONANCE
                    resonance_factor = 1.0 + 0.1 * (agreeing / total)
            elif conflicting > 0:
                if conflicting >= agreeing:
                    confluence_level = CONFLUENCE_STRONG_CONFLICT
                    resonance_factor = 1.0 - 0.3 * (conflicting / total)
                else:
                    confluence_level = CONFLUENCE_CONFLICT
                    resonance_factor = 1.0 - 0.15 * (conflicting / total)

        # 4. 置信度聚合
        weighted_conf = 0.0
        for src in sources.values():
            weighted_conf += src.confidence * src.weight
        weighted_conf /= sum(s.weight for s in sources.values())
        weighted_conf = clamp(weighted_conf * resonance_factor, 0.0, 1.0)

        # 5. 确定 regime（取因子源的 regime 或 "unknown"）
        regime = "unknown"
        factor_src = sources.get(SOURCE_FACTOR)
        if factor_src and factor_src.raw_data:
            regime = factor_src.raw_data.get("regime", "unknown")

        # 6. 生成推理文本
        action = direction_to_action(composite_dir, threshold=0.2)
        reasoning = self._build_reasoning(
            action, composite_dir, weighted_conf, confluence_level, sources
        )

        return UnifiedSignal(
            symbol=symbol,
            direction=round(composite_dir, 6),
            confidence=round(weighted_conf, 4),
            strength=round(composite_str, 4),
            action=action,
            confluence_level=confluence_level,
            source_count=len(sources),
            agreeing_sources=agreeing,
            conflicting_sources=conflicting,
            sources=sources,
            regime=regime,
            reasoning=reasoning,
            timestamp=now,
            cache_ttl=self._cache_ttl,
        )

    def _build_reasoning(
        self,
        action: str,
        direction: float,
        confidence: float,
        confluence_level: str,
        sources: Dict[str, SourceSignal],
    ) -> str:
        """生成人类可读的融合推理"""
        action_cn = {"buy": "看多", "sell": "看空", "hold": "观望"}.get(action, "观望")
        parts = [f"综合{action_cn}(dir={direction:+.2f}, conf={confidence:.0%})"]

        for sid, src in sources.items():
            dir_cn = "多" if src.direction > 0.05 else ("空" if src.direction < -0.05 else "中")
            parts.append(f"{src.source_name}:{dir_cn}({src.confidence:.0%})")

        conf_cn = {
            CONFLUENCE_STRONG_RESONANCE: "强共振",
            CONFLUENCE_RESONANCE: "共振",
            CONFLUENCE_NEUTRAL: "中性",
            CONFLUENCE_CONFLICT: "冲突",
            CONFLUENCE_STRONG_CONFLICT: "强冲突",
        }.get(confluence_level, "中性")
        parts.append(f"共振:{conf_cn}")

        return " | ".join(parts)

    # ──────────────────────────────────────────────────────────
    #  缓存管理
    # ──────────────────────────────────────────────────────────

    def _cleanup_cache(self) -> None:
        """缓存清理: 超 200 个 symbol 时清理最旧的 100 个"""
        if len(self._cache) <= self._max_cache_size:
            return
        sorted_keys = sorted(self._cache_ts.keys(), key=lambda k: self._cache_ts[k])
        remove_count = len(self._cache) - self._max_cache_size + 50  # 多清理 50 个
        for key in sorted_keys[:remove_count]:
            self._cache.pop(key, None)
            self._cache_ts.pop(key, None)
            self._detail_cache.pop(key, None)
            self._detail_cache_ts.pop(key, None)
        logger.debug(f"[SignalBus] 缓存清理: 移除 {remove_count} 个旧条目")


# ════════════════════════════════════════════════════════════
#  模块级单例
# ════════════════════════════════════════════════════════════

unified_signal_bus = UnifiedSignalBus()
