"""V3 因子管道 — 从 monolith _run_v3_factor_pipeline 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class V3FactorHost:
    v3_factor_cache: Dict[str, dict] = field(default_factory=dict)
    V3_FACTOR_CACHE_TTL: float = 90.0


def build_v3_factor_host(svc) -> V3FactorHost:
    return V3FactorHost(
        v3_factor_cache=getattr(svc, "_v3_factor_cache", None) or {},
        V3_FACTOR_CACHE_TTL=float(getattr(svc, "_V3_FACTOR_CACHE_TTL", 90) or 90),
    )


def run_v3_factor_pipeline(
    *,
    host: V3FactorHost,
    db: Session = None,
    session=None,
    symbols: List[str] = None,
    market_summary: Dict[str, Any] = None,
    unified_snapshot=None,
    force: bool = False,
) -> tuple:
    factor_signal_results: Dict[str, Any] = {}
    anomaly_reports: Dict[str, Any] = {}
    regime_classifications: Dict[str, Any] = {}

    if not symbols:
        return factor_signal_results, regime_classifications, anomaly_reports

    try:
        from backend.services.factor_engine import factor_engine as _fe
        from backend.services.factor_engine.factor_signal_generator import FactorSignalGenerator as _FSG
        from backend.services.market_regime import MarketRegimeClassifier as _MRC
        from backend.database.models import ATASFactorCache, MarketAnalysisSnapshot
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        import pandas as _pd

        _signal_gen = _FSG()
        _regime_clf = _MRC()
        _anom_det = None
        try:
            from backend.services.anomaly_detector import AnomalyDetector as _AD
            _anom_det = _AD()
        except Exception:
            pass

        klines_map: Dict[tuple, Any] = {}
        if unified_snapshot and getattr(unified_snapshot, "klines", None):
            klines_map = unified_snapshot.klines
        else:
            try:
                from backend.services.kline_data_service import kline_service
                # Fix 9: 按 tier 加载多 timeframe K线
                # 因子管道只服务短线(15m)+全局regime(1h)，4h/1d 由 agent 指标预加载按需获取
                for _sym in symbols:
                    _sym_u = _sym.upper()
                    # 15m: 短线因子计算 + 向后兼容（原主路径）
                    _raw_15m = kline_service.get_klines_from_db(_sym_u, "15m", 100)
                    if _raw_15m:
                        klines_map[(_sym, "15m")] = _pd.DataFrame(_raw_15m)
                    # 1h: 全局 regime 分类（比 15m 稳定，减少趋势/震荡误判抖动）
                    _raw_1h = kline_service.get_klines_from_db(_sym_u, "1h", 100)
                    if _raw_1h:
                        klines_map[(_sym, "1h")] = _pd.DataFrame(_raw_1h)
                    # 注: 4h/1d 不在因子管道加载 —— 中长线归 SwingAgent/TrendAgent 深度思考，
                    # agent 的指标预加载会按需查 1h/4h/1d（见 _need_agent_data 分支）
            except Exception as _kerr:
                logger.debug(f"[FullAuto][V3] DB K线回退失败: {_kerr}")

        _v3_start = time.time()
        _MAX_V3_SECONDS = 45
        _now_aware = _dt.now(_tz.utc)
        _now_dt = _now_aware.replace(tzinfo=None)
        _expire_dt = _now_dt + _td(minutes=15)
        _now_ms = int(_now_aware.timestamp() * 1000)

        def _jsonify_regime(_r):
            if _r is None:
                return "unknown"
            _raw = (
                getattr(_r, "regime", None)
                or (_r.get("regime") if isinstance(_r, dict) else None)
                or _r
            )
            if hasattr(_raw, "value"):
                return str(_raw.value)
            return str(_raw)

        def _safe_num(_v):
            if _v is None:
                return None
            if hasattr(_v, "value"):
                _v = _v.value
            try:
                if isinstance(_v, (int, float, str, bool)):
                    return _v
                return float(_v)
            except Exception:
                return str(_v)

        _persist_rows = []

        for _sym in symbols:
            if time.time() - _v3_start > _MAX_V3_SECONDS:
                logger.warning(f"[FullAuto][V3] 因子计算超时(>{_MAX_V3_SECONDS}s)，跳过剩余 symbol")
                break

            _cache_hit = host.v3_factor_cache.get(_sym.upper())
            if _cache_hit and (time.time() - _cache_hit.get("ts", 0)) < host.V3_FACTOR_CACHE_TTL:
                factor_signal_results[_sym] = _cache_hit.get("signal")
                regime_classifications[_sym] = _cache_hit.get("regime")
                continue

            try:
                _kdf = klines_map.get((_sym, "15m"))
                if _kdf is None:
                    _kdf = klines_map.get((_sym.upper(), "15m"))
                if _kdf is None or (hasattr(_kdf, "empty") and _kdf.empty):
                    # 最后防线：尝试从 API 即时拉取 K 线
                    try:
                        from backend.services.market_data import get_kline_data
                        _raw = get_kline_data(_sym, period="15m", count=100)
                        if _raw:
                            _kdf = _pd.DataFrame(_raw)
                    except Exception:
                        pass
                if _kdf is None or (hasattr(_kdf, "empty") and _kdf.empty):
                    continue

                # Fix 10: 组装衍生品市场数据，注入因子引擎（原缺失导致 funding/oi/cvd 因子全为 0）
                _mkt_info = market_summary.get(_sym, {}) if isinstance(market_summary, dict) else {}
                _factor_market_data = {}
                for _dk in ('funding_rate', 'oi', 'open_interest', 'prev_oi',
                            'cvd', 'total_notional', 'buy_notional', 'sell_notional',
                            'taker_buy_volume', 'atr', 'atr_value'):
                    _dv = _mkt_info.get(_dk)
                    if _dv is not None:
                        # 归一化字段名：atr_value → atr（因子引擎用 atr）
                        _norm_key = 'atr' if _dk == 'atr_value' else _dk
                        _factor_market_data[_norm_key] = _dv
                # 同时注入当前价格（供 ATR% 计算等因子使用）
                _cur_price = _mkt_info.get('current_price')
                if _cur_price:
                    _factor_market_data['price'] = float(_cur_price)

                # Fix 13: 从 Market DB 注入衍生品指标（因子策略需要的核心数据）
                # OI/CVD/TAKER/DEPTH/IMBALANCE 数据在 Market DB 有，但原从未注入因子引擎
                # 不注入 → cvd_ratio/oi_delta/taker_ratio 因子全部返回 0（形同虚设）
                try:
                    from backend.services.market_flow_indicators import get_indicator_value as _giv
                    _md_tf = "5m"  # 短线因子用 5m 周期的衍生品指标
                    _oi_delta = _giv(None, _sym, "OI_DELTA", _md_tf)
                    if _oi_delta is not None:
                        # oi_delta 是百分比变化，反推 oi/prev_oi 供因子引擎用
                        _factor_market_data['oi_delta_pct'] = float(_oi_delta)
                        _factor_market_data['oi'] = 1.0  # 占位，因子用 oi_delta_pct 更准
                        _factor_market_data['prev_oi'] = 1.0 / (1 + float(_oi_delta) / 100) if _oi_delta != 0 else 1.0
                    _cvd = _giv(None, _sym, "CVD", _md_tf)
                    if _cvd is not None:
                        _factor_market_data['cvd'] = float(_cvd)
                        # total_notional 用成交量近似（因子引擎只需比率）
                        _factor_market_data.setdefault('total_notional', abs(float(_cvd)) * 10 or 1.0)
                    _taker = _giv(None, _sym, "TAKER", _md_tf)
                    if _taker is not None:
                        # taker 是 buy/sell 比率，反推 buy_notional/sell_notional
                        _taker_f = float(_taker)
                        if _taker_f > 0:
                            _factor_market_data['buy_notional'] = _taker_f
                            _factor_market_data['sell_notional'] = 1.0
                        elif _taker_f < 0:
                            _factor_market_data['buy_notional'] = 1.0
                            _factor_market_data['sell_notional'] = abs(_taker_f)
                    _depth = _giv(None, _sym, "DEPTH", _md_tf)
                    if _depth is not None:
                        _factor_market_data['depth_ratio'] = float(_depth)
                    _imb = _giv(None, _sym, "IMBALANCE", _md_tf)
                    if _imb is not None:
                        _factor_market_data['imbalance'] = float(_imb)
                except Exception as _md_err:
                    logger.debug(f"[FullAuto][V3] {_sym} 衍生品指标注入跳过: {_md_err}")

                # Fix 15a: 链上/宏观/情绪数据注入（因子策略需要 active_addresses/btc_dominance/fear_greed 等）
                # OnchainDataCollector 已有完整采集器(CoinGecko/Blockchain.info/Mempool/Etherscan)，
                # 但原从未接入 V3 因子管道 → 链上/宏观因子全返回默认值
                try:
                    from services.onchain_data_collector import onchain_collector as _oc_col
                    _oc_data = _oc_col.collect_all([_sym]) if _sym else {}
                    _oc_sym = _oc_data.get(_sym, {}) if isinstance(_oc_data, dict) else {}
                    if isinstance(_oc_sym, dict):
                        for _oc_key in ('active_addresses', 'exchange_net_flow', 'whale_tx_count',
                                        'whale_tx_volume', 'tvl', 'btc_dominance', 'fear_greed'):
                            _oc_val = _oc_sym.get(_oc_key)
                            if _oc_val is not None and _oc_val != 0:
                                _factor_market_data[_oc_key] = float(_oc_val)
                except Exception as _oc_err:
                    logger.debug(f"[FullAuto][V3] {_sym} 链上/宏观数据注入跳过: {_oc_err}")

                # Fix 15b: 期权数据注入（Deribit API: options_skew/iv_term_structure/put_call_ratio）
                # 只有 BTC/ETH 有 Deribit 期权，其他币种自动跳过
                try:
                    from backend.services.options_data_collector import get_options_for_symbol as _gof
                    _opt_data = _gof(_sym)
                    if _opt_data:
                        for _opt_key in ('options_skew', 'iv_term_structure', 'put_call_ratio'):
                            _opt_val = _opt_data.get(_opt_key)
                            if _opt_val is not None:
                                _factor_market_data[_opt_key] = float(_opt_val)
                except Exception as _opt_err:
                    logger.debug(f"[FullAuto][V3] {_sym} 期权数据注入跳过: {_opt_err}")

                # 没有衍生品数据时传 None（向后兼容，技术因子不受影响）
                _md = _factor_market_data if _factor_market_data else None
                # [fix] 标记 timeframe，z-score 归一化按 symbol+timeframe 隔离（主循环15m）
                if _md and isinstance(_md, dict):
                    _md.setdefault("timeframe", "15m")

                # Fix 16a: 把外部数据注入 K线 DataFrame 列（新体系100+因子读 df['col'] 而非 market_data dict）
                # 不注入 → cloud/external/derivatives 因子全部读空列返回默认值
                if _md and hasattr(_kdf, 'assign'):
                    try:
                        _enrich_cols = {}
                        for _col_name, _col_val in _md.items():
                            if _col_name in ('price',):  # price 不注入(与 close 重复)
                                continue
                            if _col_name not in _kdf.columns and isinstance(_col_val, (int, float)):
                                _enrich_cols[_col_name] = float(_col_val)
                        if _enrich_cols:
                            _kdf = _kdf.assign(**_enrich_cols)
                    except Exception:
                        pass

                # [2026-08-14 P0-2] 精选白名单灰度入口：SCALP_VETTED_IN_V3=1 时主 V3
                # 路径与 scalp_loop/Router 回退路径共用同一套 allowlist/exclude（三路径
                # 口径统一）；默认 0 保持旧行为（全量计算），灰度观察后切 1。
                _use_vetted = False
                try:
                    _use_vetted = bool(
                        str(os.environ.get("SCALP_VETTED_IN_V3", "0")).strip().lower()
                        in ("1", "true", "yes", "on")
                    )
                except Exception:
                    _use_vetted = False
                if _use_vetted:
                    try:
                        from backend.services.scalp.scalp_factor_exclude import (
                            get_scalp_factor_allowlist,
                            get_scalp_factor_exclude_categories,
                        )
                        _fvals = _fe.compute_all_factors(
                            _kdf, market_data=_md,
                            exclude_categories=get_scalp_factor_exclude_categories(),
                            allowlist=get_scalp_factor_allowlist(),
                        )
                    except Exception:
                        _fvals = _fe.compute_all_factors(_kdf, market_data=_md)
                else:
                    _fvals = _fe.compute_all_factors(_kdf, market_data=_md)
                _regime_tag = "unknown"
                _reg = None
                try:
                    # Fix 9: regime 分类优先用 1h K线（比 15m 更稳定，减少噪音误判）
                    # 15m regime 噪声大 → 频繁在 trending/ranging 间抖动，影响 tier 门槛
                    _kdf_1h = klines_map.get((_sym, "1h")) or klines_map.get((_sym.upper(), "1h"))
                    _regime_kdf = _kdf_1h if (_kdf_1h is not None and hasattr(_kdf_1h, 'empty') and not _kdf_1h.empty) else _kdf
                    _reg = _regime_clf.classify(_regime_kdf)
                    regime_classifications[_sym] = _reg
                    _regime_tag = _jsonify_regime(_reg)
                except Exception as _rge:
                    logger.debug(f"[FullAuto][V3] {_sym} regime 分类失败: {_rge}")

                if _fvals:
                    # Fix 11/22c: 数据不足保护 — 严重不足直接跳过，不给假信号
                    _kline_n = len(_kdf) if hasattr(_kdf, '__len__') else 0
                    if _kline_n < 30:
                        # K线严重不足（如新上线币 LAYER 只有几根）→ 因子值全不可靠 → 跳过
                        logger.warning(
                            f"[FullAuto][V3] {_sym} K线仅{_kline_n}根(<30)，跳过因子信号（数据不足不交易）"
                        )
                        # 标记数据不足，下游决策不交易该 symbol
                        factor_signal_results[_sym] = None
                        continue
                    _data_penalty = 0.0
                    if _kline_n < 50:
                        _data_penalty = 0.5   # 数据不足，置信度打5折（原0.3→0.5，更保守）
                        logger.info(f"[FullAuto][V3] {_sym} K线仅{_kline_n}根(<50)，因子置信度大幅降权")
                    elif _kline_n < 80:
                        _data_penalty = 0.15  # 数据偏少，轻微降权
                    # M7: IC 闭环产出的因子权重（胜率差的因子自动降权）
                    _ic_weights = None
                    try:
                        from backend.services.factor_ic_evaluator import (
                            load_runtime_factor_weights,
                        )
                        _ic_w = load_runtime_factor_weights()
                        if _ic_w:
                            _ic_weights = {
                                name: _ic_w.get(name, 1.0) for name in _fvals
                            }
                    except Exception:
                        _ic_weights = None
                    _sig = _signal_gen.generate_signals(
                        _fvals, weights=_ic_weights,
                        regime=str(_regime_tag), symbol=_sym, timeframe="15m",
                    )
                    # Fix 11: 应用数据不足惩罚（降低 confidence，让 gate 门槛更严）
                    if _data_penalty > 0 and hasattr(_sig, 'confidence'):
                        try:
                            _orig_conf = float(_sig.confidence or 0)
                            _sig.confidence = max(0, _orig_conf * (1 - _data_penalty))
                        except Exception:
                            pass
                    factor_signal_results[_sym] = _sig
                    host.v3_factor_cache[_sym.upper()] = {
                        "ts": time.time(), "signal": _sig, "regime": _reg,
                    }

                # 回退 A: 长线因子计算已移除 —— 中长线决策归 SwingAgent/TrendAgent 深度思考，
                # 不由因子管道代劳。因子管道只服务短线（15m）。
                # agent 的多周期上下文注入见 compact_report_text / analyst_report_builder。

                if _anom_det:
                    _mkt = market_summary.get(_sym, {})
                    anomaly_reports[_sym] = _anom_det.detect(
                        _sym, _kdf, _mkt, factor_signals=_fvals
                    )

                if _sym in factor_signal_results:
                    _sig = factor_signal_results[_sym]
                    _dir_raw = getattr(_sig, "direction", None)
                    try:
                        _dir_num = float(_dir_raw) if _dir_raw is not None else 0.0
                    except (TypeError, ValueError):
                        _dir_num = 0.0
                    if _dir_num > 0.2:
                        _dir_label = "long"
                    elif _dir_num < -0.2:
                        _dir_label = "short"
                    else:
                        _dir_label = "neutral"
                    _regime_conf = None
                    if _reg is not None:
                        _regime_conf = (
                            getattr(_reg, "confidence", None)
                            or (_reg.get("confidence") if isinstance(_reg, dict) else None)
                        )
                    _summary_payload = {
                        "schema_version": 2,
                        "factor_count": _safe_num(getattr(_sig, "contributing_factors", None)),
                        "signal_score": _safe_num(getattr(_sig, "strength", None)),
                        "direction": _dir_num,
                        "direction_label": _dir_label,
                        "confidence": _safe_num(getattr(_sig, "confidence", None)),
                        "regime": _regime_tag,
                    }
                    _persist_rows.append((
                        _sym, _summary_payload, _dir_label, _regime_conf, _regime_tag,
                    ))
            except Exception as _fsym_err:
                logger.warning(
                    f"[FullAuto][V3] {_sym} 因子计算失败: {type(_fsym_err).__name__}: {_fsym_err}"
                )

        # 批量落库（主 db 会话，单次 commit）
        # MarketAnalysisSnapshot 属于 AnalyticsBase，需通过 AnalyticsSessionLocal 写入
        if _persist_rows:
            _ana_db = None
            try:
                from backend.database.connection import AnalyticsSessionLocal
                _ana_db = AnalyticsSessionLocal()
                for _sym, _payload, _dir_str, _regime_conf, _regime_tag in _persist_rows:
                    _cache_key = f"{_sym}_15m_composite"
                    _existing = db.query(ATASFactorCache).filter_by(cache_key=_cache_key).first()
                    if _existing:
                        _existing.value = _payload
                        _existing.calculated_at = _now_dt
                        _existing.expires_at = _expire_dt
                    else:
                        db.add(ATASFactorCache(
                            cache_key=_cache_key,
                            factor_id="composite_v3",
                            symbol=_sym,
                            timeframe="15m",
                            value=_payload,
                            calculated_at=_now_dt,
                            expires_at=_expire_dt,
                        ))
                    _ana_db.add(MarketAnalysisSnapshot(
                        symbol=_sym,
                        timestamp=_now_ms,
                        period="15m",
                        regime_type=str(_regime_tag),
                        regime_direction=_dir_str,
                        regime_confidence=_safe_num(_regime_conf),
                        indicator_snapshot=_payload,
                        price=float(market_summary.get(_sym, {}).get("current_price", 0) or 0) or None,
                    ))
                    market_summary.setdefault(_sym, {})["factor_v3"] = _payload
                from backend.services.full_auto.db_session_helpers import safe_commit
                safe_commit(db, "v3_factor_batch", session=session)
                _ana_db.commit()
                logger.info(f"[FullAuto][V3] 因子快照批量落库: {len(_persist_rows)} symbols")
            except Exception as _persist_err:
                try:
                    db.rollback()
                except Exception:
                    pass
                try:
                    if _ana_db is not None:
                        _ana_db.rollback()
                except Exception:
                    pass
                logger.warning(
                    f"[FullAuto][V3] 因子批量落库失败: {type(_persist_err).__name__}: {_persist_err}"
                )
            finally:
                if _ana_db is not None:
                    try:
                        _ana_db.close()
                    except Exception:
                        pass

        if factor_signal_results:
            logger.info(f"[FullAuto][V3] 因子信号: {len(factor_signal_results)} symbols")
        if anomaly_reports:
            _crit = [s for s, r in anomaly_reports.items() if any(e.is_critical for e in r.events)]
            if _crit:
                logger.warning(f"[FullAuto][V3] 异常告警: {_crit}")
            for _sym, _arpt in anomaly_reports.items():
                if _sym in market_summary:
                    market_summary[_sym]["anomaly_score"] = _arpt.total_anomaly_score
                    market_summary[_sym]["anomaly_action"] = _arpt.recommended_action
                    market_summary[_sym]["anomaly_events"] = [
                        {
                            "type": (
                                e.anomaly_type.value
                                if hasattr(e.anomaly_type, "value")
                                else str(e.anomaly_type)
                            ),
                            "severity": e.severity,
                            "z_score": e.z_score,
                            "desc": e.description[:80],
                        }
                        for e in _arpt.events[:5]
                    ]
                    if _arpt.recommended_action == "trade_opportunity":
                        market_summary[_sym]["has_anomaly_opportunity"] = True

    except Exception as _v3e:
        logger.warning(f"[FullAuto][V3] 因子管道跳过: {type(_v3e).__name__}: {_v3e}")

    return factor_signal_results, regime_classifications, anomaly_reports

    # ══════════════════════════════════════════════════
    #  核心循环 — 健康检查
    # ══════════════════════════════════════════════════
