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


# ── [2026-09-24 新目标 R7] 因子批量计算的**公平轮转**游标 ──
_ROTATE_STATE: Dict[str, int] = {"cursor": 0}


# [新目标·调度 R2] **游标持久化**：`_ROTATE_STATE` 是模块级内存态，
# 每次后端重启清零 ⇒ 轮转从头开始、尾部标的重新挨饿（实测本轮会话内多次重启，
# 健康检查 8~11 分钟的规律节奏被打断成 26/62 分钟，且每次重启都重付首触注入费）。
# 这里把游标落盘，重启后接着转。
def _rotate_persist_enabled() -> bool:
    return (os.getenv("V3_FACTOR_ROTATE_PERSIST", "true") or "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _rotate_state_path() -> str:
    return os.getenv("V3_FACTOR_ROTATE_STATE_PATH") or "data/v3_factor_rotate_state.json"


def _load_rotate_cursor() -> None:
    if not _rotate_persist_enabled():
        return
    try:
        import json as _json
        _p = _rotate_state_path()
        if os.path.exists(_p):
            with open(_p, "r", encoding="utf-8") as _f:
                _d = _json.load(_f)
            _ROTATE_STATE["cursor"] = int(_d.get("cursor", 0) or 0)
    except Exception:
        pass


def _save_rotate_cursor() -> None:
    if not _rotate_persist_enabled():
        return
    try:
        import json as _json
        _p = _rotate_state_path()
        _dir = os.path.dirname(_p)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        _tmp = _p + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            _json.dump({"cursor": int(_ROTATE_STATE.get("cursor", 0))}, _f)
        os.replace(_tmp, _p)
    except Exception:
        pass


_load_rotate_cursor()


def _rotate_enabled() -> bool:
    return os.getenv("V3_FACTOR_ROTATE_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


# [新目标 R2] 每标的"外部注入"的 TTL 缓存：
# 实测（probe_v3_per_symbol_cost_20260925.py）：onchain_collector.collect_all 每标的
# **11~14s**（BTC 20.4s 合计）⇒ 45s 预算 ÷ ~15s = 每轮 3~5 个标的就超时。
# 这些是慢变外部数据（fear_greed/tvl/active_addresses/期权偏斜），逐轮逐标的实拉纯属浪费。
# 只缓存"注入新增的键"（基础 market_summary 数据仍每轮新取）。
_V3_INJECT_CACHE = {}


def _v3_inject_cache_enabled() -> bool:
    import os
    return (os.getenv("V3_FACTOR_INJECT_CACHE_ENABLED", "true") or "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _v3_inject_ttl_sec() -> int:
    import os
    # [新目标·调度 R7] 600→3600→7200→**14400（4h）**：时间 TTL 在任意低吞吐下都无法保证
    # "第二圈必命中"（几何属性测试证伪过 7200：每轮 5 标的 ⇒ 一圈 22 轮×10min=13200s）。
    # 14400 覆盖修复后的吞吐下限（每轮 ≥12 ⇒ 一圈 ≤9 轮×10min=5400s）并留 2× 余量。
    # 残留限制（如实）：若吞吐跌破 ~5 标的/轮（网络重度异常），仍需自适应 TTL（已登记，未实现）。
    # 代价：orderflow/OI 类注入键可能旧至 4h（因子输入慢变上下文，不影响行情/决策新鲜度）。
    try:
        return int(os.getenv("V3_FACTOR_INJECT_CACHE_TTL_SEC", "14400") or 14400)
    except (TypeError, ValueError):
        return 14400


def _reset_inject_cache_for_test() -> None:
    _V3_INJECT_CACHE.clear()


def _rotate_symbols(symbols):
    """按游标轮转起点，避免"超时 break ⇒ 尾部永久饿死"。

    原实现每轮都从 `symbols[0]` 开始、超预算就 break ⇒ 列表尾部的币**永远**拿不到因子
    （实测 LINK 的 15m 复合因子已 5 天未重算、XRP 9 小时）。
    轮转保证每个币在若干轮内必被算到。开关 V3_FACTOR_ROTATE_ENABLED，回滚=false。
    """
    try:
        _lst = list(symbols or [])
    except TypeError:
        return symbols
    if not _rotate_enabled() or len(_lst) <= 1:
        return _lst
    _k = int(_ROTATE_STATE.get("cursor", 0)) % len(_lst)
    return _lst[_k:] + _lst[:_k]


def _advance_rotate_cursor(total: int, computed: int) -> None:
    """本轮算了 `computed` 个（其余的因超时被跳过）⇒ 起点前移 `computed`（至少 1）。"""
    try:
        if total <= 1 or not _rotate_enabled():
            _ROTATE_STATE["cursor"] = 0
            return
        _step = max(1, int(computed))
        _ROTATE_STATE["cursor"] = (int(_ROTATE_STATE.get("cursor", 0)) + _step) % int(total)
        _save_rotate_cursor()
    except Exception:
        _ROTATE_STATE["cursor"] = 0


def _reset_rotate_cursor_for_test() -> None:
    _ROTATE_STATE["cursor"] = 0


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
        # [2026-09-24 新目标 R7] 预算改为可配（默认 45s = 旧行为）。
        # 实测：最近 13 个周期里 **11 个超时**，每轮只算完 2~5 个币就被掐断。
        try:
            _MAX_V3_SECONDS = float(os.getenv("V3_FACTOR_MAX_SECONDS", "45") or 45)
        except (TypeError, ValueError):
            _MAX_V3_SECONDS = 45.0
        # [2026-09-24 新目标 R7] **轮转起点**：原实现按 `symbols` 固定顺序跑、超时即 break
        # ⇒ 列表**尾部永远被饿死**（实测 LINK 的因子已 5 天没重算、XRP 9 小时）。
        # 现每轮从上次停下的位置接着跑（round-robin），保证每个币最终都能被算到。
        # 开关 V3_FACTOR_ROTATE_ENABLED（默认 true）；回滚=false 即恢复固定顺序。
        symbols = _rotate_symbols(symbols)
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
                # [2026-08-15 消费端验收] 改为直接复用 factor_bridge 的统一注入
                # （真实 OI 绝对值对 + 真实吃单流 + 落库 funding），与 scalp 路径
                # 同源同口径——消除此前两条路径 funding 来源不一致、cvd/taker
                # 合成伪值的分叉。
                # [新目标 R2] 三个外部注入（订单流/链上/期权）**每标的合计 11~20s**（实测），
                # 是"4-5 个标的就吃光 45s 预算"的真凶。现加 TTL 缓存（默认 600s）：
                # 命中则只合并上次"注入新增键"，网络调用整段跳过。
                # 回滚：V3_FACTOR_INJECT_CACHE_ENABLED=false。
                _inj_cache = _V3_INJECT_CACHE if _v3_inject_cache_enabled() else None
                _inj_added = None
                if _inj_cache is not None:
                    _hit = _inj_cache.get(_sym.upper())
                    if _hit and (time.time() - _hit[0]) < _v3_inject_ttl_sec():
                        _inj_added = _hit[1]
                if _inj_added is not None:
                    _factor_market_data.update(_inj_added)
                else:
                    _before_keys = set(_factor_market_data.keys())
                    try:
                        from backend.services.factor_engine.factor_bridge import inject_orderflow_for_factors
                        inject_orderflow_for_factors(_sym, _factor_market_data, "5m")
                    except Exception as _md_err:
                        logger.debug(f"[FullAuto][V3] {_sym} 衍生品指标注入跳过: {_md_err}")

                    # Fix 15a: 链上/宏观/情绪数据注入（因子策略需要 active_addresses/btc_dominance/fear_greed 等）
                    # OnchainDataCollector 已有完整采集器(CoinGecko/Blockchain.info/Mempool/Etherscan)，
                    # 但原从未接入 V3 因子管道 → 链上/宏观因子全返回默认值
                    try:
                        # [2026-08-15 修复] 原 `from services.onchain_data_collector` 缺
                        # backend. 前缀：生产以仓库根启动 uvicorn 时 `services.*` 不可导入，
                        # 必 ImportError 被 except 吞掉 → 链上/宏观注入从未生效
                        #（审查 4.5 #24 同类问题残留）。现改为 backend. 前缀。
                        from backend.services.onchain_data_collector import onchain_collector as _oc_col
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

                    if _inj_cache is not None:
                        _added = {k: v for k, v in _factor_market_data.items() if k not in _before_keys}
                        _inj_cache[_sym.upper()] = (time.time(), _added)

                # 没有衍生品数据时传 None（向后兼容，技术因子不受影响）
                _md = _factor_market_data if _factor_market_data else None
                # [fix] 标记 timeframe，z-score 归一化按 symbol+timeframe 隔离（主循环15m）
                if _md and isinstance(_md, dict):
                    _md.setdefault("timeframe", "15m")

                # Fix 16a: 把外部数据注入 K线 DataFrame 列（新体系100+因子读 df['col'] 而非 market_data dict）
                # 不注入 → cloud/external/derivatives 因子全部读空列返回默认值
                # [2026-08-15 消费端验收] 订单流/衍生品键（oi/prev_oi/cvd/taker/funding/
                # liquidation 等）**不再**作为常数序列注入 df：常数列会让时间序列因子
                #（delta(oi,5)/ema(oi,12)）读到恒 0 的误导值。这些键仅经 market_data
                # dict 供 base_factors 消费；需真实序列的因子由 dataset_builder 离线
                # 富化提供。仅保留语义为「快照标量上下文」的键（情绪/宏观/期权/社交）。
                _SNAPSHOT_SCALAR_KEYS = {
                    'fear_greed', 'btc_dominance', 'social_score', 'news_sentiment',
                    'discussion_volume', 'tvl', 'options_skew', 'iv_term_structure',
                    'put_call_ratio',
                }
                if _md and hasattr(_kdf, 'assign'):
                    try:
                        _enrich_cols = {}
                        for _col_name, _col_val in _md.items():
                            if _col_name in ('price',):  # price 不注入(与 close 重复)
                                continue
                            if _col_name not in _SNAPSHOT_SCALAR_KEYS:
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
                    # [2026-09-03 审查修正 A] 影子因子策略：此前 V3 路径只传 IC 权重，
                    # PAPER/role=paper 因子既不封顶也不排除——而短线循环**优先**消费
                    # 的正是这条路径的 factor_v3。现与 pipeline/中线路由同一入口：
                    # live 会话权重归零，paper 会话封顶 PAPER_FACTOR_WEIGHT_CAP。
                    try:
                        from backend.services.factor_engine.paper_factor_policy import (
                            apply_paper_policy,
                        )
                        if _ic_weights is None:
                            _ic_weights = {name: 1.0 for name in _fvals}
                        apply_paper_policy(
                            _ic_weights,
                            getattr(session, "trading_mode", None) or "paper",
                            where="v3_pipeline",
                        )
                    except Exception as _pp_err:
                        logger.debug(f"[FullAuto][V3] 影子因子策略跳过: {_pp_err}")
                    _sig = _signal_gen.generate_signals(
                        _fvals, weights=_ic_weights,
                        regime=str(_regime_tag), symbol=_sym, timeframe="15m",
                    )
                    # [2026-09-24 新目标 R17] **被归零因子的前向影子**：只记录、不改变信号。
                    # 由来：IC 学习把 82/226 个因子打成 w=0，其中 48% 的 |t|<1（与 0 无法区分），
                    # 而被归零因子的投票**从未落盘**（快照只存入选因子）⇒ 事后无法做反事实。
                    # 影子攒够样本后才能可证伪地回答"给地板权会更好还是更差"。
                    # 开关 FACTOR_ZERO_SHADOW_ENABLED（默认 false）。
                    try:
                        from backend.services.factor_zero_shadow import record as _zero_shadow
                        _zero_shadow(_sym, _ic_weights, getattr(_sig, "signals", None),
                                     regime=str(_regime_tag))
                    except Exception as _zs_err:
                        logger.debug(f"[FullAuto][V3] 归零因子影子跳过: {_zs_err}")
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
                        "schema_version": 3,
                        "factor_count": _safe_num(getattr(_sig, "contributing_factors", None)),
                        "signal_score": _safe_num(getattr(_sig, "strength", None)),
                        "direction": _dir_num,
                        "direction_label": _dir_label,
                        "confidence": _safe_num(getattr(_sig, "confidence", None)),
                        "regime": _regime_tag,
                    }
                    # [2026-09-02 P1.2] 逐因子归因随 payload 下传（schema 2→3）。
                    # 原先只有聚合后的 direction/confidence 出栈，逐因子明细在此
                    # 蒸发，导致 scalp_signal_log 里 141 个因子对应的只有一个
                    # composite 分 —— 亏损无法归因到因子，进化闭环缺输入。
                    # 合成本身只取 |direction| 最强的 top-15，故这里体量有界。
                    try:
                        _attr = getattr(_sig, "attribution", None) or []
                        if _attr:
                            _summary_payload["factor_contrib"] = [
                                {
                                    "f": a.factor_id,
                                    "d": a.direction,
                                    "w": a.weight,
                                    "c": a.contrib,
                                    "cat": a.category,
                                }
                                for a in _attr[:15]
                            ]
                    except Exception as _attr_err:
                        logger.debug(
                            f"[FullAuto][V3] {_sym} 因子归因打包跳过: {_attr_err}"
                        )
                    _persist_rows.append((
                        _sym, _summary_payload, _dir_label, _regime_conf, _regime_tag,
                    ))
            except Exception as _fsym_err:
                logger.warning(
                    f"[FullAuto][V3] {_sym} 因子计算失败: {type(_fsym_err).__name__}: {_fsym_err}"
                )

        # [2026-09-24 新目标 R7] 本轮结束后把起点前移，下一轮从没算到的币接着来。
        _advance_rotate_cursor(len(symbols), len(_persist_rows))

        # 批量落库（主 db 会话，单次 commit）
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
