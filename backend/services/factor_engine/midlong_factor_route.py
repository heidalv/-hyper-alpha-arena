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


def _load_df(symbol: str, timeframe: str, lookback: int) -> Optional[pd.DataFrame]:
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
    klines = factor_backtest_scorer._load_klines(symbol, timeframe, lookback)
    if not klines or len(klines) < 60:
        return None
    try:
        return pd.DataFrame(klines)
    except Exception:
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
    for rec in active:
        fid = str(rec.get("factor_id") or "")
        scores = rec.get("scores") or {}
        ic = float(scores.get("ic_mean") or 0.0)
        if abs(ic) < 1e-6:
            continue
        w = abs(ic) * float(rec.get("runtime_weight") or 1.0)
        vals = _factor_history(rec, sym)
        if vals is None:
            votes[fid] = {"z": None, "vote": None, "skip": "no_history"}
            continue
        z = _zscore_last(vals)
        if z is None:
            votes[fid] = {"z": None, "vote": None, "skip": "dormant_or_thin"}
            continue
        orient = 1.0 if ic >= 0 else -1.0
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

    if score >= threshold:
        out["action"] = "buy"
    elif score <= -threshold:
        out["action"] = "sell"
    else:
        out["action"] = "hold"
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
                has_open_midlong_position,
            )
            if _acct and has_open_midlong_position(_db, _acct, sym):
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
        except Exception as _open_err:
            logger.warning("[FactorRoute] 开仓异常 %s: %s", sym, _open_err, exc_info=True)
            dec["gate"] = f"open_error:{type(_open_err).__name__}"
        return dec
    finally:
        try:
            _db.close()
        except Exception:
            pass
