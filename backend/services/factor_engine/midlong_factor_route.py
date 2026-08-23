"""midlong_factor_route — 中线因子路由（2026-08-15）。

把「通过 4h/1d 样本外闸门（A/B 级）的中长线活跃因子」直接变成中线入场决策，
替代已停用的旧 AI 中线（MIDLONG_MID_VIA_MLTO=false）。

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

import logging
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
) -> Dict[str, Any]:
    """因子路由入场决策。返回 {action, score, votes, reason, confidence, sl_pct, tp_pct}。"""
    sym = str(symbol or "").upper()
    min_active = int(_cfg("FACTOR_ROUTE_MIN_ACTIVE_FACTORS", 2))
    threshold = float(_cfg("FACTOR_ROUTE_ENTRY_THRESHOLD", 0.35))
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
            _neg_ic_n += 1
        w = abs(ic) * float(rec.get("runtime_weight") or 1.0)
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
    dec = factor_route_decide(sym, market_summary=market_summary)
    dec.setdefault("opened", False)
    dec.setdefault("gate", "")

    if dec.get("action") not in ("buy", "sell"):
        return dec

    # ── 融合仲裁（阶段1：FactorRoute × LLM thesis 对齐闸）──
    # 冲突→skip（不冻结）；LLM 无意见/弱反对→因子自决（fail-open）。
    try:
        from backend.services.decision_fusion_arbiter import decide_mid
        _ms_sym = (market_summary or {}).get(sym) or {}
        if not isinstance(_ms_sym, dict):
            _ms_sym = {}
        _orch_m = _ms_sym.get("orchestrator") if isinstance(_ms_sym.get("orchestrator"), dict) else {}
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
        logger.debug("[FusionMid] %s 仲裁异常(放行): %s", sym, _fm_err)

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
            logger.debug("[FactorRoute] 持仓检查跳过 %s: %s", sym, _pos_err)

        try:
            # [2026-08-15] 与长线趋势路径同款：注入多周期指标信封，否则 V5 提案闸
            # [StrictData] tier=mid missing=indicators_1h/4h/1d 会拦下所有因子路由开仓。
            try:
                host.inject_midlong_indicators(market_summary or {}, sym)
            except Exception as _inj_err:
                logger.debug("[FactorRoute] 指标注入跳过 %s: %s", sym, _inj_err)
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
                tranche_margin_pct=float(_s.FACTOR_ROUTE_TRANCHE_MARGIN_PCT),
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
