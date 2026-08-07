"""Legacy QAA tick + Agent handlers — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class QaaLegacyHost:
    market_scan_cache: Dict[str, Any]
    active_positions_cache: Any
    pre_screen_results: Any = None
    pre_screen_passed: Set[str] = field(default_factory=set)
    qaa_last_decision: Any = None
    qaa_agents_registered: bool = False
    last_unified_snapshot: Any = None
    risk_assessor: Any = None

    get_or_capture_unified_snapshot: Callable = field(repr=False, default=lambda *a, **k: None)
    run_with_timeout: Callable = field(repr=False, default=lambda *a, **k: None)
    run_v3_factor_pipeline: Callable = field(repr=False, default=lambda *a, **k: None)
    run_analyst_system: Callable = field(repr=False, default=lambda *a, **k: None)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)
    clear_master_strat_cache: Callable = field(repr=False, default=lambda: None)


def build_qaa_legacy_host(svc) -> QaaLegacyHost:
    return QaaLegacyHost(
        market_scan_cache=svc._market_scan_cache,
        active_positions_cache=svc._active_positions_cache,
        pre_screen_results=getattr(svc, "_pre_screen_results", None),
        pre_screen_passed=set(getattr(svc, "_pre_screen_passed", None) or []),
        qaa_last_decision=getattr(svc, "_qaa_last_decision", None),
        qaa_agents_registered=getattr(svc, "_qaa_agents_registered", False),
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        risk_assessor=getattr(svc, "_risk_assessor", None),
        get_or_capture_unified_snapshot=svc._get_or_capture_unified_snapshot,
        run_with_timeout=svc._run_with_timeout,
        run_v3_factor_pipeline=svc._run_v3_factor_pipeline,
        run_analyst_system=svc._run_analyst_system,
        safe_commit=svc._safe_commit,
        clear_master_strat_cache=svc._clear_master_strat_cache,
    )


def get_qaa_handler(agent_id: str, host: QaaLegacyHost):
    """映射 agent_id → handler(action, payload)。"""
    handlers = {
        "market_data": lambda a, p: qaa_market_data(a, p, host),
        "risk_control": lambda a, p: qaa_risk_control(a, p, host),
        "factor_engine": lambda a, p: qaa_factor_engine(a, p, host),
        "intel_signal": lambda a, p: qaa_intel_signal(a, p, host),
        "mt_orchestrator": lambda a, p: qaa_mt_orchestrator(a, p, host),
        "master_controller": lambda a, p: qaa_master_controller(a, p, host),
        "trade_execution": lambda a, p: qaa_trade_execution(a, p, host),
        "genetic_optimizer": lambda a, p: qaa_genetic_optimizer(a, p, host),
        "signal_bus": lambda a, p: qaa_signal_bus(a, p, host),
    }
    return handlers.get(agent_id)


def register_qaa_agents(host: QaaLegacyHost) -> None:
    if host.qaa_agents_registered:
        return
    host.qaa_agents_registered = True

    from backend.services.event_bus import event_bus
    from backend.services.qaa.cards import ALL_CARDS
    from backend.config.settings import QAA_ENABLED_AGENTS

    enabled = QAA_ENABLED_AGENTS.strip()
    enabled_set = set(enabled.split(",")) if enabled else set()

    for agent_id, card in ALL_CARDS.items():
        # 如果指定了启用列表，只注册列表中的 Agent
        if enabled_set and agent_id not in enabled_set:
            continue
        handler = get_qaa_handler(agent_id, host)
        if handler:
            event_bus.register_agent(agent_id, card, handler)

    logger.info(
        f"[FullAuto][QAA] Agent 注册完成: "
        f"{list(event_bus.qaa_stats.get('registered_agents', []))}"
    )


def build_qaa_snapshot(session_id: str, host: QaaLegacyHost):
    from backend.services.qaa.rule_router import MarketSnapshot

    snap = MarketSnapshot()

    try:
        # 有活跃持仓?
        snap.has_active_positions = bool(host.active_positions_cache)
        snap.open_position_count = len(host.active_positions_cache)

        # 波动率体制 — 从缓存的市场摘要推断
        _market = host.market_scan_cache
        if _market:
            for sym, data in _market.items():
                if isinstance(data, dict):
                    vol = data.get("volatility_24h", 0)
                    if vol > 0.08:
                        snap.volatility_regime = "EXTREME"
                        break
                    elif vol > 0.04:
                        snap.volatility_regime = "HIGH"

        # 持仓健康度
        if snap.has_active_positions:
            for pid, pdata in host.active_positions_cache.items():
                if isinstance(pdata, dict):
                    pnl_pct = pdata.get("unrealized_pnl_pct", 0)
                    if pnl_pct < -0.05:
                        snap.position_health = "danger"
                        break
                    elif pnl_pct < -0.02:
                        snap.position_health = "warning"

        # 交易对列表
        snap.symbols = list(host.market_scan_cache.keys())

    except Exception as e:
        logger.debug(f"[FullAuto][QAA] 构建快照失败, 使用默认值: {e}")

    return snap

def run_qaa_tick(session_id: str, host: QaaLegacyHost):
    register_qaa_agents(host)

    from backend.services.event_bus import event_bus
    from backend.services.qaa.rule_router import rule_router

    _t0 = time.time()

    try:
        # Step 1: 构建市场快照 (用于路由决策)
        snapshot = build_qaa_snapshot(session_id, host)

        # Step 2: 路由决策 (<1ms)
        calls = rule_router.route(snapshot)
        logger.info(
            f"[FullAuto][QAA] tick 路由: {len(calls)} calls, "
            f"vol={snapshot.volatility_regime}, "
            f"positions={snapshot.has_active_positions}"
        )

        # Step 3: 按优先级执行
        # Priority 0 (必须) — 串行
        p0_calls = [c for c in calls if c.priority == 0]
        p0_results = {}
        for call in p0_calls:
            result = event_bus.call_agent_sync(
                call.agent_id, call.action, call.payload,
                caller_id="qaa_tick",
            )
            p0_results[call.agent_id] = result

        # Priority 1 (重要) — 并行
        p1_calls = [c for c in calls if c.priority == 1]
        if p1_calls:
            p1_results = event_bus.call_agents_parallel_sync(
                p1_calls, caller_id="qaa_tick",
            )
        else:
            p1_results = []

        # Priority 2 (可选) — 串行 (如果前面没超时)
        elapsed = time.time() - _t0
        p2_calls = [c for c in calls if c.priority == 2]
        p2_results = []
        if p2_calls and elapsed < 60:  # 还剩 30s+ 才跑 LLM
            for call in p2_calls:
                result = event_bus.call_agent_sync(
                    call.agent_id, call.action, call.payload,
                    caller_id="qaa_tick",
                )
                p2_results.append(result)

        elapsed_total = time.time() - _t0
        logger.info(
            f"[FullAuto][QAA] tick 完成: {elapsed_total:.1f}s "
            f"(p0={len(p0_calls)} p1={len(p1_calls)} p2={len(p2_calls)})"
        )

        # ── 混合信号模式：预筛选触发 LLM 分析（旧版 QAA tick 路径）──
        try:
            from backend.config.settings import HYBRID_SIGNAL_MODE_ENABLED, PRESCREENER_ENABLED
            if HYBRID_SIGNAL_MODE_ENABLED and PRESCREENER_ENABLED:
                from backend.database.connection import SessionLocal
                from backend.database.models import FullAutoSession
                _db_ps = SessionLocal()
                try:
                    _sess_ps = _db_ps.query(FullAutoSession).filter(
                        FullAutoSession.session_id == session_id
                    ).first()
                    if _sess_ps and _sess_ps.status in ("running", "defensive"):
                        _symbols = list(_sess_ps.symbols or [])
                        if not _symbols:
                            logger.debug("[FullAuto][QAA][混合模式] 无交易标的，跳过")
                        else:
                            # 获取市场数据（轻量级，不依赖 unified_data_pool）
                            _ms = {}
                            # 1. 尝试从缓存获取
                            if host.market_scan_cache:
                                _ms = {s: host.market_scan_cache.get(s, {}) for s in _symbols if s in host.market_scan_cache}
                            # 2. 尝试从 session.last_market_summary 获取
                            if len(_ms) < len(_symbols):
                                try:
                                    _lms = _sess_ps.last_market_summary or {}
                                    if isinstance(_lms, dict):
                                        for s in _symbols:
                                            if s not in _ms and s in _lms:
                                                _ms[s] = _lms[s]
                                except Exception:
                                    pass
                            # 3. 从实时价格服务获取
                            if len(_ms) < len(_symbols):
                                try:
                                    from backend.services.market_price_service import get_price
                                    for s in _symbols:
                                        if s not in _ms:
                                            _price = get_price(s)
                                            if _price:
                                                _ms[s] = {"current_price": _price}
                                except Exception:
                                    pass
                            logger.debug(
                                f"[FullAuto][QAA][混合模式] 数据准备完成: "
                                f"{len(_ms)}/{len(_symbols)} symbols有数据"
                            )

                            # Fix 4: 短线信号用 5m K线（原 15m 时间错配，信号滞后）
                            # 5m 粒度匹配 RSI(7)/MACD(5/13/4) 等短周期指标，提升信号时效性
                            try:
                                from backend.services.kline_data_service import KlineDataService
                                _kline_svc = KlineDataService()
                                for _sym in _symbols:
                                    if _sym in _ms:
                                        _klines = _kline_svc.get_klines_from_db(_sym, "5m", count=60)
                                        if _klines and len(_klines) >= 30:
                                            _ms[_sym]["kline_data"] = _klines
                                _kline_count = sum(1 for s in _symbols if s in _ms and "kline_data" in _ms[s])
                                logger.debug(
                                    f"[FullAuto][QAA][混合模式] K线加载完成: "
                                    f"{_kline_count}/{len(_symbols)} symbols有K线"
                                )
                            except Exception as _kl_err:
                                logger.debug(f"[FullAuto][QAA][混合模式] K线加载失败: {_kl_err}")

                            from backend.services.signal_pre_screener import get_signal_pre_screener
                            from backend.services.signal_frequency_guard import get_signal_frequency_guard
                            _screener = get_signal_pre_screener()
                            _freq_guard = get_signal_frequency_guard()
                            _batch = _screener.screen_batch(_symbols, _ms, tier="short")
                            _guaranteed = _freq_guard.get_guaranteed_symbols("short", _symbols, _ms)
                            _ps_passed = set(_batch.passed_symbols + _guaranteed)
                            host.pre_screen_results = _batch
                            host.pre_screen_passed = _ps_passed

                            if _ps_passed:
                                logger.info(
                                    f"[FullAuto][QAA][混合模式] 预筛选通过 {len(_ps_passed)}/{len(_symbols)} "
                                    f"+ 频率保障 {len(_guaranteed)} → 触发LLM分析"
                                )
                                _active_ids = list(_sess_ps.active_strategy_ids or [])
                                host.run_analyst_system(_db_ps, _sess_ps, _active_ids, _ms)
                                logger.info(
                                    f"[FullAuto][QAA][混合模式] LLM 分析完成, "
                                    f"elapsed={time.time()-_t0:.1f}s"
                                )
                                # 关键：分析过程中 _append_event 只改内存，必须 commit 才写入 DB
                                try:
                                    from datetime import datetime, timezone
                                    _sess_ps.last_health_check_at = datetime.now(timezone.utc)
                                except Exception:
                                    pass
                                if not host.safe_commit(_db_ps, "qaa_hybrid_tick", session=_sess_ps):
                                    logger.error("[FullAuto][QAA][混合模式] event_log 落库失败")
                            else:
                                logger.info(
                                    f"[FullAuto][QAA][混合模式] 预筛选通过 0/{len(_symbols)}, "
                                    f"cache_keys={len(_ms)}"
                                )
                finally:
                    host.clear_master_strat_cache()
                    _db_ps.close()
        except Exception as _ps_err:
            logger.warning(f"[FullAuto][QAA][混合模式] 预筛选跳过(非致命): {_ps_err}")

    except Exception as e:
        logger.error(f"[FullAuto][QAA] tick 异常: {e}", exc_info=True)

def qaa_market_data(action: str, payload: dict, host: QaaLegacyHost):
    if action == "get_snapshot":
        symbols = payload.get("symbols", list(host.market_scan_cache.keys()))
        account_id = payload.get("account_id")
        include_klines = bool(payload.get("include_klines", False))
        snap = host.get_or_capture_unified_snapshot(
            symbols=symbols,
            account_id=account_id,
            include_klines=include_klines,
            light_mode=True,
            max_age=float(payload.get("max_age", 60)),
        )
        return snap
    return None

def qaa_risk_control(action: str, payload: dict, host: QaaLegacyHost):
    if action == "check":
        result = {"allowed": True, "reason": "", "warnings": []}
        try:
            # 1. 风控配置状态
            from backend.config.settings import (
                DEFENSIVE_TIERED_MODE,
                ENABLE_REDUCE_COOLDOWN,
            )
            result["defensive_mode"] = DEFENSIVE_TIERED_MODE
            result["reduce_cooldown"] = ENABLE_REDUCE_COOLDOWN

            # 2. 一致性门控 (方向翻转检测)
            if payload.get("symbol") and payload.get("action"):
                try:
                    from backend.services.decision_consistency_gate import (
                        DecisionConsistencyGate,
                    )
                    gate = DecisionConsistencyGate()
                    check_result = gate.check(
                        account_id=payload.get("account_id", 0),
                        symbol=payload["symbol"],
                        action=payload["action"],
                        confidence=payload.get("confidence", 50) / 100.0,
                        market_regime=payload.get("regime", "unknown"),
                    )
                    result["consistency_passed"] = check_result.passed
                    if not check_result.passed:
                        result["allowed"] = False
                        result["reason"] = check_result.reason
                except Exception:
                    pass

            # 3. 确定性风控规则
            try:
                from backend.services.risk_model import CompositeRiskAssessor
                # 简化的上下文
                context = {
                    "account_equity": payload.get("equity", 0),
                    "symbol": payload.get("symbol", ""),
                    "daily_pnl": payload.get("daily_pnl", 0),
                }
                # risk assess 如果有实例可用
                if host.risk_assessor is not None:
                    assessment = host.risk_assessor.assess(context)
                    if assessment.is_blocked:
                        result["allowed"] = False
                        result["reason"] = "; ".join(assessment.block_reasons)
                    result["warnings"] = assessment.warnings
                    result["risk_score"] = assessment.risk_score
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"[FullAuto][QAA] risk_control check error: {e}")

        return result
    return None

def qaa_factor_engine(action: str, payload: dict, host: QaaLegacyHost):
    if action in ("compute_full", "compute_basic"):
        try:
            return host.run_with_timeout(
                lambda: host.run_v3_factor_pipeline(force=action == "compute_full"),
                timeout_s=20 if action == "compute_basic" else 45,
                fallback=None,
                label=f"factor_{action}",
            )
        except Exception:
            return None
    elif action == "compute_signals":
        try:
            return host.run_with_timeout(
                lambda: qaa_compute_signals(payload, host),
                timeout_s=8, fallback=None, label="factor_signals",
            )
        except Exception:
            return None
    elif action == "compute_unified":
        try:
            return host.run_with_timeout(
                lambda: qaa_compute_unified(payload, host),
                timeout_s=25, fallback=None, label="factor_unified",
            )
        except Exception:
            return None
    return None

def qaa_compute_signals(payload: dict, host: QaaLegacyHost):
    from backend.services.factor_engine.base_factors import factor_engine
    from backend.services.factor_engine.factor_signal_generator import FactorSignalGenerator
    from backend.services.factor_engine.factor_weighting import DynamicFactorWeighting
    from backend.services.kline_data_service import kline_service
    import pandas as pd

    symbols = payload.get("symbols", list(host.market_scan_cache.keys()))
    results = {}
    for sym in symbols:
        try:
            raw = kline_service.get_klines_from_db(sym, "15m", 200)
            if not raw or len(raw) < 20:
                continue
            df = pd.DataFrame(raw)
            fv = factor_engine.compute_all_factors(df)
            if fv:
                sig = FactorSignalGenerator().generate_signals(fv, symbol=sym)
                fw = DynamicFactorWeighting(factor_engine=factor_engine)
                adp = fw.calculate_adaptive_weights(fv, None)
                results[sym] = {
                    "direction": sig.direction,
                    "strength": sig.strength,
                    "confidence": sig.confidence,
                    "regime": adp.regime.value,
                    "regime_confidence": adp.confidence,
                    "contributing_factors": sig.contributing_factors,
                }
        except Exception:
            continue
    return results

def qaa_compute_unified(payload: dict, host: QaaLegacyHost):
    from backend.services.factor_engine.factor_bridge import (
        compute_new_factors_as_legacy,
    )
    from backend.services.kline_data_service import kline_service
    import pandas as pd

    symbols = payload.get("symbols", list(host.market_scan_cache.keys()))
    results = {}
    for sym in symbols:
        try:
            df = None
            for tf in ("15m", "1h", "4h"):
                raw = kline_service.get_klines_from_db(sym, tf, 200)
                if raw and len(raw) >= 20:
                    df = pd.DataFrame(raw)
                    break
            if df is None or df.empty:
                continue
            # L4: 只调新注册表（已含 legacy 短名因子）
            factors = compute_new_factors_as_legacy(df, symbol=sym)
            results[sym] = {
                "total_count": len(factors),
            }
        except Exception:
            continue
    return results

def qaa_intel_signal(action: str, payload: dict, host: QaaLegacyHost):
    if action == "get_signals":
        try:
            from backend.services.intelligence_signal_engine import intel_signal_engine
            symbols = payload.get("symbols", list(host.market_scan_cache.keys()))
            signals = {}
            for sym in symbols:
                sig = intel_signal_engine.compute_trading_signal(sym)
                if sig:
                    signals[sym] = {
                        "direction": sig.direction,
                        "confidence": sig.confidence,
                        "risk_level": sig.risk_level,
                        "funding_signal": sig.funding.signal if sig.funding else None,
                        "oi_signal": sig.oi.quadrant if sig.oi else None,
                        "whale_direction": sig.whale_direction,
                        "news_sentiment": sig.news_sentiment,
                        "reasoning": sig.ai_reasoning or sig.to_prompt_text(),
                    }
            return signals
        except Exception:
            return None
    return None

def qaa_mt_orchestrator(action: str, payload: dict, host: QaaLegacyHost):
    try:
        from backend.services.multi_timeframe_orchestrator import mt_orchestrator
        snap = host.last_unified_snapshot
        symbols = payload.get("symbols", list(host.market_scan_cache.keys()))

        if action == "evaluate_portfolio" and snap:
            results = mt_orchestrator.evaluate_portfolio(symbols, snap)
            # 序列化为可 JSON 化的字典
            serialized = {}
            for sym, dec in results.items():
                serialized[sym] = {
                    "final_action": dec.final_action,
                    "final_side": dec.final_side,
                    "allowed_direction": dec.allowed_direction,
                    "position_scale": dec.position_scale,
                    "final_leverage": dec.final_leverage,
                    "recommended_nature": dec.recommended_nature,
                    "regime": dec.regime,
                    "long_confidence": dec.long_view.confidence,
                    "mid_confidence": dec.mid_view.confidence,
                    "short_confidence": dec.short_view.confidence,
                }
            return serialized

        elif action == "evaluate":
            sym = payload.get("symbol", symbols[0] if symbols else "")
            if sym:
                dec = mt_orchestrator.evaluate(sym, snap)
                if dec:
                    return {
                        sym: {
                            "final_action": dec.final_action,
                            "final_side": dec.final_side,
                            "allowed_direction": dec.allowed_direction,
                            "position_scale": dec.position_scale,
                            "recommended_nature": dec.recommended_nature,
                            "regime": dec.regime,
                        }
                    }
    except Exception:
        pass
    return None

def qaa_master_controller(action: str, payload: dict, host: QaaLegacyHost):
    if action == "synthesize":
        try:
            from backend.services.qaa.prompt_schema import SignalCompressor, CompressedSignal
            from backend.services.qaa.prompt_utils import (
                build_master_prompt_xml,
                prefill_decision,
            )

            # ── 1. 压缩信号 ──
            # Priority: use processor step output (has analyst directions)
            # Fallback: market_summary from payload (raw data, likely empty directions)
            market_summary = payload.get("market_summary", {})
            compressed = {}
            if market_summary:
                compressor = SignalCompressor()
                compressed = compressor.compress_from_dict(market_summary)
            # Debug: dump signal summary
            _sig_counts = {"bullish": 0, "bearish": 0, "neutral": 0}
            for _sk, _sv in compressed.items():
                _sd = _sv.model_dump() if hasattr(_sv, 'model_dump') else _sv
                _sig = _sd.get("signal", "neutral") if isinstance(_sd, dict) else "neutral"
                _sig_counts[_sig] = _sig_counts.get(_sig, 0) + 1
            logger.info(
                f"[QAA v3][SignalDump] compressed={len(compressed)} symbols, "
                f"bull={_sig_counts['bullish']} bear={_sig_counts['bearish']} "
                f"neutral={_sig_counts['neutral']}, "
                f"market_summary_keys={list(market_summary.keys())[:3] if market_summary else 'EMPTY'}, "
                f"payload_keys={list(payload.keys())[:10]}"
            )
            # V5 重构：废除"全中性时用涨跌幅加权打分硬造方向"的规则化旁路。
            # 全中性 = 无可交易信号 = 不交易（decision_core 是唯一决策入口，
            # 历史数据表明该旁路制造了大量低质量高频交易）。
            if _sig_counts["neutral"] == len(compressed) and len(compressed) > 0:
                logger.info(
                    "[QAA v3][SignalDump] ALL NEUTRAL — V5: no synthetic directions, "
                    "this tick will hold (rule-based bypass removed)."
                )

            # ── 2. Tier 3B: 从压缩信号 + snapshot 生成规则化决策 ──
            decision_action = "hold"
            decision_confidence = 50
            reasoning_parts = []

            # 2a. 从压缩信号计算方向
            bull_syms = bear_syms = neutral_syms = 0
            total_conf = 0
            for sig in compressed.values():
                sig_dict = sig.model_dump() if hasattr(sig, 'model_dump') else sig
                s = sig_dict.get("signal", "neutral")
                c = int(sig_dict.get("confidence", 50))
                if s == "bullish":
                    bull_syms += 1
                elif s == "bearish":
                    bear_syms += 1
                else:
                    neutral_syms += 1
                total_conf += c

            symbol_count = bull_syms + bear_syms + neutral_syms

            if symbol_count > 0:
                avg_conf = total_conf // max(symbol_count, 1)
                directional = bull_syms + bear_syms
                if directional > 0:
                    bull_ratio = bull_syms / directional
                    if bull_ratio >= 0.6:
                        decision_action = "execute"
                        decision_confidence = max(50, min(95, avg_conf + 10))
                        reasoning_parts.append(f"{bull_syms}/{directional}看多")
                    elif bull_ratio <= 0.4:
                        decision_action = "cancel"
                        decision_confidence = max(50, min(95, avg_conf + 10))
                        reasoning_parts.append(f"{bear_syms}/{directional}看空")
                    else:
                        reasoning_parts.append(f"方向分化(bull={bull_syms}/bear={bear_syms})")
                else:
                    reasoning_parts.append("无方向信号(全部中性)")

            # 2b. 从 workflow_snapshot 补充强度
            snap = payload.get("workflow_snapshot", {})
            if snap:
                severity = str(snap.get("severity_level", "")).upper()
                score = float(snap.get("current_score", 0))
                if severity in ("HIGH", "EXTREME") and abs(score) > 0.5:
                    decision_confidence = max(decision_confidence, 75)
                    reasoning_parts.append(f"severity={severity}(score={score:.2f})")
                has_alert = snap.get("has_critical_alert", False)
                if has_alert:
                    reasoning_parts.append("critical_alert")

            # 2c. 无持仓时: cancel → sell (新开空), execute → buy (新开多)
            has_positions = payload.get("has_active_items", False) or payload.get("has_position", False)
            if decision_action == "cancel" and not has_positions:
                decision_action = "sell"
                reasoning_parts.append("无持仓→新开sell(空)")
            elif decision_action == "execute" and not has_positions:
                decision_action = "buy"
                reasoning_parts.append("无持仓→新开buy(多)")

            reasoning = f"规则化: {', '.join(reasoning_parts)}" if reasoning_parts else "信号不足"

            logger.info(
                f"[QAA v3] decision_maker → action={decision_action}, "
                f"confidence={decision_confidence}, "
                f"symbols={symbol_count}(B={bull_syms}/S={bear_syms}/N={neutral_syms}), "
                f"id_host={id(host)}"
            )

            # 低置信度 (<70) 需要 LLM 增强; 高置信度可直接使用
            needs_llm = decision_confidence < 70
            status = "ready_for_llm" if needs_llm else "rule_decided"

            # ── 侧通道: 存入 service 实例供 post-tick 直接读取 ──
            # (QAA pipeline 的 step.output_data → reduce_outputs → run.decision 链路有传递问题)
            host.qaa_last_decision = {
                "action": decision_action,
                "confidence": decision_confidence,
                "reasoning": reasoning,
                "status": status,
                "symbol_count": symbol_count,
                "bull_count": bull_syms,
                "bear_count": bear_syms,
            }
            logger.debug(f"[QAA v3] 侧通道已设置: action={decision_action}, confidence={decision_confidence}")

            # ── 3. 预填充决策 ──
            symbol = payload.get("symbol", "UNKNOWN")
            tier = payload.get("tier", "mid")
            prefill = prefill_decision(
                symbol=symbol,
                tier=tier,
                current_price=payload.get("current_price", 0),
                has_position=has_positions,
                position_side=payload.get("position_side", ""),
                position_entry_price=payload.get("entry_price", 0),
            )

            compressed_dump = {s: sig.model_dump() for s, sig in compressed.items()}

            return {
                "action": decision_action,
                "confidence": decision_confidence,
                "reasoning": reasoning,
                "status": status,
                "compressed_signals": compressed_dump,
                "prefill": prefill.model_dump(),
                "reason": payload.get("reason", "qaa_synth"),
            }
        except Exception as e:
            logger.warning(f"[QAA v3] decision_maker 异常: {e}")
            return {
                "action": "hold",
                "confidence": 50,
                "reasoning": f"decision_maker error: {e}",
                "status": "error",
                "reason": payload.get("reason", "qaa_synth"),
            }
    return None

def qaa_trade_execution(action: str, payload: dict, host: QaaLegacyHost):
    if action in ("place_order", "close_position"):
        logger.info(f"[FullAuto][QAA] trade_execution: {action} payload={payload}")
        # 实际执行委托给现有的交易执行流程
        # (通过 _run_trading_cycle 中的执行逻辑完成)
        return {"status": "delegated", "action": action}
    return None

def qaa_genetic_optimizer(action: str, payload: dict, host: QaaLegacyHost):
    if action == "optimize":
        # 遗传优化需要 fitness_fn, 无法通过 QAA payload 传递
        # 仅返回状态, 实际优化由 evolution_scheduler 独立触发
        return {"status": "offline", "message": "genetic_optimizer runs via evolution_scheduler"}
    return None

def qaa_signal_bus(action: str, payload: dict, host: QaaLegacyHost):
    if action == "get_unified_signal":
        try:
            from backend.services.signal_engine import unified_signal_bus
            symbol = payload.get("symbol", "BTC")
            result = unified_signal_bus.get_unified_signal(symbol)
            return {
                "symbol": result.symbol,
                "direction": result.direction,
                "confidence": result.confidence,
                "strength": result.strength,
                "action": result.action,
                "confluence_level": result.confluence_level,
                "source_count": result.source_count,
                "agreeing_sources": result.agreeing_sources,
                "conflicting_sources": result.conflicting_sources,
                "sources": {
                    k: {
                        "source_name": v.source_name,
                        "direction": v.direction,
                        "confidence": v.confidence,
                        "strength": v.strength,
                        "action": v.action,
                        "weight": v.weight,
                    }
                    for k, v in result.sources.items()
                },
                "regime": result.regime,
                "reasoning": result.reasoning,
            }
        except Exception:
            return None
    elif action == "get_signal_detail":
        try:
            from backend.services.signal_engine import unified_signal_bus
            symbol = payload.get("symbol", "BTC")
            return unified_signal_bus.get_signal_detail(symbol)
        except Exception:
            return None
    return None

