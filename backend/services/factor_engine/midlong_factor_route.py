"""midlong_factor_route — 中线因子路由（2026-08-15）。

把「通过 4h/1d 样本外闸门（A/B 级）的中长线活跃因子」直接变成中线入场决策，
替代已停用的旧 AI 中线开仓职责（MIDLONG_MID_VIA_MLTO=true 时 thesis 影子并行观察）。

信号合成
========
对每个活跃因子：
  1. 在各自 timeframe（4h/1d）上取最近 lookback 根 K 线计算因子历史序列
     （公式因子直接向量化；legacy 快照型因子用滚动重算）。
  2. 最新值相对自身尾部（≤60 个有效点）做 z-score：z = (last - mean) / std。
  3. 方向：factor 的 OOS 方向由回测打分时的 IC 符号决定（负 IC 因子反向交易），
     orient = sign(ic_mean)；vote = orient * clip(z, -2, 2)。
  4. 权重 = |ic_mean| × runtime_weight；composite = Σ(w·vote) / Σw ∈ [-2, 2]。
  5. score = composite / 2 ∈ [-1, 1]；|score| ≥ 阈值 → buy/sell，否则 hold。

安全边界：活跃因子数不足、K 线不可靠、价格缺失 → hold（绝不因因子路由故障开仓）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_Z_WINDOW = 60          # z-score 用最近最多 60 个有效点
_LOOKBACK = 260         # 历史窗口（根）
_FWD_FALLBACK = 6


def _cfg(name: str, default):
    from backend.config import settings as _s
    return getattr(_s, name, default)


def _route_kline_exchange() -> str:
    """[M3 2026-08-21] 运行时因子路由的 K 线源。

    默认 "active" = 与成交同所（data_center trade 用途强制 active_exchange、
    closed_only 剔未收盘 bar）——因子在 A 所打分、在 B 所成交是中线因子失真
    根源之一（设计 M3）。可显式配置其它交易所（走 research 用途）。
    注意：**不改** `FACTOR_BACKTEST_KLINE_EXCHANGE`（回测/晋升默认 binance
    深历史），运行时与回测分离，两边语义都不污染。
    """
    try:
        raw = str(_cfg("FACTOR_ROUTE_KLINE_EXCHANGE", "active") or "active")
    except Exception:
        raw = "active"
    return raw.strip().lower() or "active"


def _load_df(symbol: str, timeframe: str, lookback: int) -> Optional[pd.DataFrame]:
    """[M3 2026-08-21] 路由专用加载：active 所优先，不足 60 根 → None（hold）。

    active 所数据不足时**禁止静默回退 binance**——回退会把「成交所上因子读数」
    悄悄换成「另一所的历史」，与 M3 同所原则相反；正确行为是 hold 并留日志，
    由覆盖率报告暴露问题（切换所后需重算 IC/活跃池）。
    """
    _ex = _route_kline_exchange()
    klines = None
    if _ex == "active":
        try:
            from backend.services.kline_data_service import kline_service
            klines = kline_service.get_klines_from_db(
                symbol.upper(), timeframe, lookback,
            ) or None
        except Exception as e:
            logger.warning("[FactorRoute] %s/%s active所 K线加载失败: %s", symbol, timeframe, e)
            klines = None
        if not klines or len(klines) < 60:
            logger.info(
                "[FactorRoute] %s/%s active所 K线不足(%s根<60) → hold（不回退 binance）",
                symbol, timeframe, len(klines) if klines else 0,
            )
            return None
    else:
        from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
        klines = factor_backtest_scorer._load_klines(symbol, timeframe, lookback)
        if not klines or len(klines) < 60:
            return None
    try:
        return pd.DataFrame(klines)
    except Exception:
        return None


def _eval_ast_on_df(ast: dict, df) -> Optional[np.ndarray]:
    """[item14 2026-08-21] 进化仓 DSL AST 在 K 线 DataFrame 上求值（复用 parser 审计）。"""
    try:
        from backend.services.factor_engine.expr.parser import parse as _parse_dsl
        from backend.services.alpha.factor_compute import kline_df_to_fields
        expr = _parse_dsl(ast)
        return np.asarray(expr.evaluate(kline_df_to_fields(df)), dtype=float)
    except Exception as e:
        logger.debug("[FactorRoute] AST 求值失败: %s", e)
        return None


def _factor_history(
    rec: Dict[str, Any],
    symbol: str,
) -> Optional[np.ndarray]:
    """返回该因子在最近窗口上的值序列（含最新值）。"""
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
    from backend.services.factor_engine.factor_calculator import FactorCalculator
    from backend.services.factor_engine.midlong_registry_factors import _rolling_recompute

    extra = rec.get("extra") or {}
    tf = str(extra.get("timeframe") or "4h").lower()
    fwd = int(_cfg("FACTOR_SCORER_MIDLONG_FWD_1D", 3)) if tf == "1d" \
        else int(_cfg("FACTOR_SCORER_MIDLONG_FWD_4H", 6))
    formula = str(rec.get("formula") or "").strip()

    df = _load_df(symbol, tf, _LOOKBACK)
    if df is None:
        return None
    try:
        if formula:
            arrays = factor_backtest_scorer._to_arrays(
                [dict(r) for r in df.to_dict("records")]
            )
            vals = factor_backtest_scorer._eval_formula(formula, arrays)
            return np.asarray(vals, dtype=float)
        # [item14 2026-08-21] AST 桥接：进化仓 DSL 表达式在路由 K 线上求值
        if str(extra.get("kind") or "") == "ast" and extra.get("expr_ast"):
            vals = _eval_ast_on_df(extra["expr_ast"], df)
            if vals is not None and int(np.isfinite(vals).sum()) >= 60:
                return vals
            return None
        registry_id = str(extra.get("registry_factor_id") or rec.get("factor_id") or "")
        calc = FactorCalculator()
        series_map = calc.calculate([registry_id], df, symbol=symbol, timeframe=tf)
        series = series_map.get(registry_id)
        if series is None or not len(series):
            return None
        vals = np.asarray(series, dtype=float)
        if int(np.isfinite(vals).sum()) < max(60, int(len(df) * 0.05)):
            # legacy 快照型 → 滚动重算（内部限定 legacy_compat 模块）
            vals = _rolling_recompute(calc, registry_id, df, symbol, tf, fwd)
        return vals
    except Exception as e:
        logger.debug("[FactorRoute] %s/%s 历史计算失败: %s", rec.get("factor_id"), tf, e)
        return None


def _zscore_last(vals: np.ndarray) -> Optional[float]:
    finite = vals[np.isfinite(vals)]
    if len(finite) < 20:
        return None
    tail = finite[-_Z_WINDOW:]
    std = float(np.std(tail))
    # 休眠因子（事件型，如 extreme_reversal 平时恒 0）不参与投票
    if std < 1e-12:
        return None
    return float((float(finite[-1]) - float(np.mean(tail))) / std)


def _dynamic_sl_tp(symbol: str) -> "tuple[float, float, str]":
    """[M4 2026-08-21] 因子仓动态 SL/TP。

    SL = clamp(max(20根结构摆动, SL_ATR_MULT×ATR14) , 0.01, SL_PCT 上限)
    TP = clamp(max(摆动, TP_ATR_MULT×ATR) 且 ≥ SL×1.8(V5 trend RR 门槛), …, TP_PCT 上限)
    ATR/摆动取自 4h K 线（路由主周期）。数据不足 → 回退静态 SL_PCT/TP_PCT。
    执行层另有 apply_structure_atr_floor(ATR_1d×1.5) 作二次下限，互不冲突。
    """
    sl_cap = float(_cfg("FACTOR_ROUTE_SL_PCT", 0.05))
    tp_cap = float(_cfg("FACTOR_ROUTE_TP_PCT", 0.10))
    sl_k = float(_cfg("FACTOR_ROUTE_SL_ATR_MULT", 1.5))
    tp_k = float(_cfg("FACTOR_ROUTE_TP_ATR_MULT", 3.0))
    try:
        df = _load_df(symbol, "4h", 120)
        if df is None or len(df) < 40:
            return sl_cap, tp_cap, "no_klines_static_fallback"
        high = df["high"].astype(float).to_numpy()
        low = df["low"].astype(float).to_numpy()
        close = df["close"].astype(float).to_numpy()
        price = float(close[-1]) or 0.0
        if price <= 0:
            return sl_cap, tp_cap, "no_price_static_fallback"
        prev_close = np.concatenate(([np.nan], close[:-1]))
        tr = np.maximum.reduce([
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ])
        atr_pct = float(np.nanmean(tr[-14:])) / price
        win = 20
        swing_pct = float(high[-win:].max() - low[-win:].min()) / price
        sl = max(swing_pct, sl_k * atr_pct)
        sl = float(np.clip(sl, 0.01, sl_cap))
        tp = max(swing_pct, tp_k * atr_pct, sl * 1.8)  # RR 不低于 V5 trend 门槛
        tp = float(np.clip(tp, sl * 1.2, tp_cap))
        return round(sl, 5), round(tp, 5), f"swing={swing_pct:.3f} atr={atr_pct:.3f}"
    except Exception as e:
        logger.debug("[FactorRoute] %s 动态SL/TP计算失败(回退静态): %s", symbol, e)
        return sl_cap, tp_cap, "calc_error_static_fallback"


def factor_route_decide(
    symbol: str,
    market_summary: Optional[dict] = None,
    trading_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """因子路由入场决策。返回 {action, score, votes, reason, confidence, sl_pct, tp_pct}。

    ``trading_mode``：[2026-09-03 审查修正 A] 实盘会话默认不让影子因子
    （held-out 未过 role=paper / 进化仓 PAPER）投票，也不计入 min_active；
    None/paper 保持原口径（半权重）。见 paper_factor_policy。
    """
    sym = str(symbol or "").upper()
    min_active = int(_cfg("FACTOR_ROUTE_MIN_ACTIVE_FACTORS", 2))
    # [2026-09-04] 阈值按 paper/live 分离：实盘"宁缺毋滥"，模拟盘要的是样本。
    # 同一个 0.35 同时管两边时，实测全 universe 只有 1/10 过门 → 中线整天零开仓。
    _thr_default = float(_cfg("FACTOR_ROUTE_ENTRY_THRESHOLD", 0.35))
    threshold = float(_cfg(
        "FACTOR_ROUTE_ENTRY_THRESHOLD_LIVE"
        if (trading_mode or "paper").strip().lower() == "live"
        else "FACTOR_ROUTE_ENTRY_THRESHOLD_PAPER",
        _thr_default,
    ))
    out = {
        "symbol": sym,
        "action": "hold",
        "score": 0.0,
        "votes": {},
        "reason": "",
        "confidence": 0,
        "sl_pct": float(_cfg("FACTOR_ROUTE_SL_PCT", 0.05)),
        "tp_pct": float(_cfg("FACTOR_ROUTE_TP_PCT", 0.10)),
        # [M3 2026-08-21] 决策日志携带 K 线源，与成交所不一致时可见
        "kline_exchange": _route_kline_exchange(),
    }

    # 数据可靠性：与中长线循环同口径（data_reliable / stale）
    ms = {}
    if isinstance(market_summary, dict):
        ms = market_summary.get(sym) or market_summary.get(symbol) or {}
        if not isinstance(ms, dict):
            ms = {}
    if ms:
        if not ms.get("data_reliable", True) or ms.get("data_stale"):
            out["reason"] = "data_unreliable"
            return out
    _price = float(ms.get("current_price") or ms.get("price") or 0)
    if _price <= 0:
        out["reason"] = "no_price"
        return out

    try:
        from backend.services.factor_engine.midlong_active_factor_set import (
            midlong_active_factor_set,
        )
        active = midlong_active_factor_set.get_active_factors()
    except Exception as e:
        logger.debug("[FactorRoute] 活跃因子读取失败: %s", e)
        active = []

    # [2026-09-03 审查修正 A] 实盘排除影子因子（不投票、不计入 min_active）。
    _paper_dropped = 0
    try:
        from backend.services.factor_engine.paper_factor_policy import (
            is_paper_record, paper_factor_excluded,
        )
        if paper_factor_excluded(trading_mode):
            _kept = [r for r in active if not is_paper_record(r)]
            _paper_dropped = len(active) - len(_kept)
            active = _kept
    except Exception as _pp_err:
        logger.debug("[FactorRoute] 影子因子策略跳过: %s", _pp_err)
    if _paper_dropped:
        out["paper_excluded"] = _paper_dropped

    if len(active) < min_active:
        out["reason"] = f"insufficient_active({len(active)}<{min_active})"
        return out

    weighted = 0.0
    weight_sum = 0.0
    usable = 0
    votes: Dict[str, Dict[str, Any]] = {}
    # [M9 2026-08-21] 弱 IC 不反手：|ic| < FACTOR_ROUTE_IC_ABS_MIN(默认0.02) 的
    # 因子跳过（orient=0），不再按 sign(ic) 反手——弱负 IC 反手是碎信号放大器。
    _ic_abs_min = float(_cfg("FACTOR_ROUTE_IC_ABS_MIN", 0.02))
    _neg_ic_n = 0
    for rec in active:
        fid = str(rec.get("factor_id") or "")
        scores = rec.get("scores") or {}
        ic = float(scores.get("ic_mean") or 0.0)
        if abs(ic) < _ic_abs_min:
            votes[fid] = {
                "z": None, "vote": None, "ic": round(ic, 4),
                "skip": "weak_ic" if abs(ic) >= 1e-6 else "zero_ic",
            }
            continue
        if ic < 0:
            # [2026-09-01 口径修正] 只统计"非反手"因子的负 IC：晋升时
            # expected_sign=-1 的反手因子（负 IC 按反向使用）是设计内行为，
            # 不应计入"方向一致性恶化"告警（否则反手因子池永久误报警）。
            _exp_sign = float(scores.get("expected_sign") or (1 if ic >= 0 else -1))
            if _exp_sign > 0:
                _neg_ic_n += 1
        # [2026-09-02 消除 fail-open] 原写法 `rec.get("runtime_weight") or 1.0`
        # 踩了 Python 的 falsy 陷阱：被 IC 判定为不可信而显式归零的权重（0.0）
        # 会被 `or` 还原成满权 1.0，归零因子照常参与投票。改为只在字段缺失
        # （None）时才用中性默认值 1.0，显式的 0 如实传递。
        _rw = rec.get("runtime_weight")
        w = abs(ic) * (1.0 if _rw is None else float(_rw))
        vals = _factor_history(rec, sym)
        if vals is None:
            votes[fid] = {"z": None, "vote": None, "skip": "no_history"}
            continue
        z = _zscore_last(vals)
        if z is None:
            votes[fid] = {"z": None, "vote": None, "skip": "dormant_or_thin"}
            continue
        # [item13 2026-08-21] orient 以晋升时锁定的 expected_sign 为准（无则回退
        # 当前 ic 符号）——与 combo_weights 权重同一套符号规则，防两处漂移。
        orient = float(scores.get("expected_sign") or (1 if ic >= 0 else -1))
        vote = orient * float(np.clip(z, -2.0, 2.0))
        votes[fid] = {"z": round(z, 3), "vote": round(vote, 3), "ic": round(ic, 4)}
        weighted += w * vote
        weight_sum += w
        usable += 1

    if usable < min_active:
        out["reason"] = f"insufficient_usable({usable}<{min_active})"
        out["votes"] = votes
        return out

    if weight_sum <= 0:
        out["reason"] = "no_valid_votes"
        out["votes"] = votes
        return out

    composite = weighted / weight_sum
    score = float(np.clip(composite / 2.0, -1.0, 1.0))
    out["score"] = round(score, 4)
    out["votes"] = votes
    # [M9] 多因子 IC 同为负 → 告警（不自动加仓/反向），周报跟踪
    if _neg_ic_n >= 3:
        logger.warning(
            "[FactorRoute] %s %d 个活跃因子 IC 为负——因子池方向一致性恶化，"
            "建议复检（不自动反向）", sym, _neg_ic_n,
        )
        out["neg_ic_warning"] = _neg_ic_n

    if score >= threshold:
        out["action"] = "buy"
    elif score <= -threshold:
        out["action"] = "sell"
    else:
        out["action"] = "hold"
    # [M4 2026-08-21] 动态 SL/TP：max(结构摆动, k×ATR@4h)，静态值只作上限夹幅。
    # 原死 5%/10% 与因子持有期/波动完全脱钩（适应度也不含 SL/TP 路径）。
    if out["action"] in ("buy", "sell"):
        _sl, _tp, _note = _dynamic_sl_tp(sym)
        out["sl_pct"] = _sl
        out["tp_pct"] = _tp
        out["sltp_note"] = _note
    out["confidence"] = int(np.clip(50 + abs(score) * 30, 0, 80))
    _vote_str = " ".join(
        "%s:%s" % (k, v.get("vote")) for k, v in votes.items() if v.get("vote") is not None
    )
    out["reason"] = "factor_route score=%+.3f n=%d votes=%s" % (score, len(votes), _vote_str)
    return out


def factor_route_open(
    *,
    host,
    session,
    symbol: str,
    market_summary: Optional[dict] = None,
    portfolio: Optional[dict] = None,
    trading_mode: str = "paper",
) -> Dict[str, Any]:
    """因子路由单币入场执行：decide → 去重/门禁 → execute_midlong_open。

    只在 authority=mlto（paper 默认）时放行开仓；已持有同币中长线仓位时跳过
    （持仓管理走既有模式B/主动退出链路，路由只负责新开）。
    """
    sym = str(symbol or "").upper()
    dec = factor_route_decide(sym, market_summary=market_summary, trading_mode=trading_mode)
    dec.setdefault("opened", False)
    dec.setdefault("gate", "")
    try:
        from backend.config.settings import midlong_brain_enabled
        if midlong_brain_enabled():
            dec["opened"] = False
            dec["gate"] = "brain_evidence_only"
            return dec
    except Exception:
        pass

    if dec.get("action") not in ("buy", "sell"):
        return dec

    # ── [U2-1 2026-08-25] 资金流一致性门：CVD/Taker 双背离 → hold（fail-open）──
    try:
        from backend.services.factor_engine.midlong_flow_gate import mid_flow_consistency_gate
        _fg_ok, _fg_reason = mid_flow_consistency_gate(sym, dec["action"], market_summary)
        if not _fg_ok:
            dec["action"] = "hold"
            dec["gate"] = f"flow_consistency:{_fg_reason}"
            logger.info("[FactorRoute] %s 资金流一致性门拦截: %s", sym, _fg_reason)
            return dec
    except Exception as _fg_err:
        logger.info("[FactorRoute] 资金流门跳过(fail-open): %s", _fg_err)

    # ── 融合仲裁（阶段1：FactorRoute × LLM thesis 对齐闸）──
    # 冲突→skip（不冻结）；LLM 无意见/弱反对→因子自决（fail-open）。
    try:
        from backend.services.decision_fusion_arbiter import decide_mid
        _ms_sym = (market_summary or {}).get(sym) or {}
        if not isinstance(_ms_sym, dict):
            _ms_sym = {}
        _orch_m = _ms_sym.get("orchestrator") if isinstance(_ms_sym.get("orchestrator"), dict) else {}
        # [2026-08-25 转正] thesis 方向门：优先用真实 mlto thesis（影子产出的方向论题），
        # conviction>=60 时否决冲突方向（THESIS_CONF_MIN=0.6）；无 thesis 回退 orchestrator。
        _mdir = None
        _mconf = 0.0
        try:
            from backend.services.mlto.thesis_store import get as _th_get
            _th = _th_get(str(getattr(session, "session_id", "") or ""), sym, "mid")
            if _th is not None and str(getattr(_th, "direction", "") or "").lower() in ("long", "short"):
                _mdir = str(_th.direction).lower()
                _mconf = float(getattr(_th, "llm_conviction", 0) or 0) / 100.0
        except Exception:
            pass
        if not _mdir:
            _mdir_raw = str(_orch_m.get("mid_bias") or "").strip().lower()
            _mdir = {"bullish": "long", "bearish": "short"}.get(_mdir_raw)
            try:
                _mconf = float(_orch_m.get("mid_confidence") or 0)
            except Exception:
                _mconf = 0.0
        _fmid = decide_mid(
            True,
            "long" if str(dec["action"]) == "buy" else "short",
            thesis_dir=_mdir,
            thesis_conf=_mconf,
        )
        dec["fusion"] = _fmid.to_dict()
        if not _fmid.allowed:
            dec["gate"] = f"fusion_thesis_{_fmid.reason}"
            logger.info("[FusionMid] %s %s 对齐闸拦截: %s", sym, dec["action"], _fmid.reason)
            return dec
    except Exception as _fm_err:
        # 仲裁异常 → fail-open（不因新代码 bug 停摆中线）
        logger.warning("[FusionMid] %s 仲裁异常(fail-open，放行): %s", sym, _fm_err)

    # ── R2 风控禁开（阶段3）：该币 24h 内单笔已实现亏损 >1.5% 权益 → 禁开 ──
    try:
        import os as _os_rb
        if _os_rb.getenv("FUSION_RISK_EVENT_BAN", "true").strip().lower() not in ("0", "false", "off"):
            from backend.services.symbol_penalty import is_risk_banned as _risk_banned_m
            if _risk_banned_m(sym):
                dec["gate"] = "fusion_risk_ban"
                logger.info("[FusionMid] %s 24h 风控禁开（单笔已实现亏损>1.5%%权益）", sym)
                return dec
    except Exception as _rb_err_m:
        logger.debug("[FusionMid] %s 风控禁开检查失败: %s", sym, _rb_err_m)

    from backend.config import settings as _s
    _acct = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)

    from backend.services.full_auto.midlong_executor import (
        execute_midlong_open,
        get_midlong_exec_authority,
    )
    _auth = get_midlong_exec_authority(trading_mode=trading_mode)
    if _auth != "mlto":
        dec["gate"] = f"authority_block(writer={_auth})"
        return dec

    from backend.database.connection import SessionLocal as _ExecDB
    _db = _ExecDB()
    try:
        try:
            from backend.services.full_auto.midlong_position_manager import (
                has_open_position_of_nature,
            )
            # [M2 2026-08-21] 互锁改同 nature：中线开仓只被已有 mid 仓（swing/
            # tier=mid）拦截——long 仓不再锁死 mid；同币 scalp 不参与判定。
            if _acct and has_open_position_of_nature(_db, _acct, sym, "mid"):
                dec["gate"] = "position_exists"
                return dec
        except Exception as _pos_err:
            logger.warning("[FactorRoute] 持仓检查跳过 %s(fail-open，未查持仓即放行): %s", sym, _pos_err)

        try:
            # [2026-08-15] 与长线趋势路径同款：注入多周期指标信封，否则 V5 提案闸
            # [StrictData] tier=mid missing=indicators_1h/4h/1d 会拦下所有因子路由开仓。
            try:
                host.inject_midlong_indicators(market_summary or {}, sym)
            except Exception as _inj_err:
                logger.debug("[FactorRoute] 指标注入跳过 %s: %s", sym, _inj_err)
            # [2026-08-25 转正] 委员会预算 hint → 真实控制面（pause 直接跳过开仓）
            # [2026-08-31 根治] 默认改 false（影子观察）：LLM 委员会"方向不明给
            # pause"的默认行为会冻结绝大多数 mid 开仓（实测 5/7 币 pause）。
            # 卡片仍写 brain_theses 供复盘；显式 COMMITTEE_CONTROL_ENABLED=true
            # 可重新接线。
            _cm_mult = 1.0
            try:
                if os.getenv("COMMITTEE_CONTROL_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
                    from sqlalchemy import text as _ct
                    from backend.database.connection import SessionLocal as _CSL
                    _cdb = _CSL()
                    try:
                        _crow = _cdb.execute(_ct(
                            "SELECT invalidation_json FROM brain_theses WHERE source='committee_shadow' "
                            "AND symbol=:s AND tier='mid' ORDER BY id DESC LIMIT 1"), {"s": sym}).first()
                        if _crow:
                            _card = json.loads(_crow[0] or "{}")
                            _hint = str(_card.get("budget_hint") or "keep").lower()
                            _tilt = str(_card.get("tilt") or "none").lower()
                            _cm_mult = {"increase": 1.2, "keep": 1.0, "reduce": 0.5, "pause": 0.0}.get(_hint, 1.0)
                            if _tilt in ("long", "short") and _tilt != ("long" if str(dec["action"]) == "buy" else "short"):
                                _cm_mult = min(_cm_mult, 0.5)
                            logger.info("[CommitteeControl] %s hint=%s tilt=%s -> mult=%.2f", sym, _hint, _tilt, _cm_mult)
                            if _cm_mult <= 0.0:
                                dec["action"] = "hold"
                                dec["gate"] = "committee_pause"
                                return dec
                    finally:
                        _cdb.close()
            except Exception as _cc_err:
                logger.debug("[CommitteeControl] 跳过: %s", _cc_err)

            _ok = execute_midlong_open(
                host=host,
                db=_db,
                session=session,
                source="factor_route",
                symbol=sym,
                action=str(dec["action"]),
                confidence=int(dec.get("confidence") or 0),
                sl_pct=float(dec.get("sl_pct") or 0.05),
                tp_pct=float(dec.get("tp_pct") or 0.10),
                market_summary=market_summary or {},
                session_mode=str(getattr(session, "status", "running") or "running"),
                tier="mid",
                trade_nature="swing",
                tranche_margin_pct=float(_s.FACTOR_ROUTE_TRANCHE_MARGIN_PCT) * _cm_mult,
                reason=(str(dec.get("reason") or ""))[:80],
                trading_mode=trading_mode,
            )
            dec["opened"] = bool(_ok)
            if _ok:
                # ── 融合归因（阶段2）：来源标签绑定新开仓位 ──
                try:
                    from backend.services.source_attribution import attribution as _attr_m
                    from sqlalchemy import text as _sa_text_m
                    _prow = _db.execute(
                        _sa_text_m(
                            "SELECT id FROM paper_positions WHERE account_id=:a AND symbol=:s "
                            "AND opened_at > now() - interval '120 seconds' ORDER BY id DESC LIMIT 1"
                        ),
                        {"a": int(_acct or 0), "s": sym},
                    ).first()
                    if _prow:
                        _attr_m.tag_position(
                            int(_prow[0]),
                            source=str((dec.get("fusion") or {}).get("source") or "factor"),
                            nature="swing", symbol=sym,
                            meta={"fusion": dec.get("fusion") or {}},
                        )
                except Exception as _tagm_err:
                    logger.debug("[FactorRoute] 归因标签绑定失败: %s", _tagm_err)
        except Exception as _open_err:
            logger.warning("[FactorRoute] 开仓异常 %s: %s", sym, _open_err, exc_info=True)
            dec["gate"] = f"open_error:{type(_open_err).__name__}"
        return dec
    finally:
        try:
            _db.close()
        except Exception:
            pass
