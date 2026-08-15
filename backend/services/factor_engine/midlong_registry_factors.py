"""midlong_registry_factors — registry Python 类因子接入中线弹药管道（2026-08-14）。

背景：中线因子化的第一轮弹药（23 个 alpha101 公式）全部未过样本外回测。
registry 里还有 22 个 ai_generated + 20 个 legacy_compat Python 类因子，
从未被中线 4h/1d 评分过。本模块把它们接入同一闸门引擎：

- seed_registry_candidates：以「引用记录」（kind=registry、无公式）登记进
  custom_factor_store，extra={horizon:midlong, timeframe:4h/1d}。
- scan_registry_midlong：逐因子在 4h/1d 上计算序列，复用
  FactorEvaluator（IC/ICIR/衰减/单调性）+ factor_backtest_scorer._walk_forward_backtest
  （严格样本外 OOS），按与 score_formula 相同的分级门槛写回 grade/status。
- active 生效路径：midlong_active_factor_set.build_snapshot → factor_service.compute
  （registry id 直接可算）→ 注入中长线决策 market_data。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_REGISTRY_CATEGORIES = ("ai_generated", "legacy_compat")
_SYMBOLS = ("BTC", "ETH", "SOL")


def _admin_tenant() -> Optional[int]:
    try:
        from backend.services.coin_select_platform_service import resolve_admin_tenant_id
        return resolve_admin_tenant_id()
    except Exception:
        return None


def _cfg(name: str, default):
    from backend.config import settings as _s
    return getattr(_s, name, default)


def list_registry_factor_ids() -> List[str]:
    """registry 中 ai_generated/legacy_compat 目录的 Python 类因子 id。

    按类所在模块路径精确匹配（metadata.category 是 technical/composite 等
    业务分类，不能用来区分来源目录；legacy_compat 是短名因子如 rsi/macd）。
    """
    from backend.services.factor_engine.factor_registry import registry
    from backend.services.factor_engine.factor_service import factor_service

    factor_service._ensure_registry_loaded()
    out: List[str] = []
    for fid, cls in list(registry._factors.items()):
        mod = str(getattr(cls, "__module__", "") or "")
        if ".ai_generated." in mod or ".legacy_compat." in mod:
            out.append(str(fid))
    return out


def seed_registry_candidates(timeframes: Optional[List[str]] = None) -> Dict[str, Any]:
    """把 registry 因子登记为中长线候选引用记录（幂等）。

    每个 (因子, timeframe) 一条记录：store factor_id = f"{fid}@{tf}"，
    extra.registry_factor_id 存真实 registry id（build_snapshot 计算用）。
    """
    from backend.services.factor_engine.custom_factor_store import custom_factor_store

    _tid = _admin_tenant()
    fids = list_registry_factor_ids()
    tfs = timeframes or ["4h", "1d"]
    registered = 0
    for fid in fids:
        for tf in tfs:
            res = custom_factor_store.register_reference(
                f"{fid}@{tf}", registry_factor_id=fid,
                tenant_id=_tid, horizon="midlong", timeframe=tf,
            )
            if res.get("ok"):
                registered += 1
    logger.info("[MidlongRegistry] 登记 registry 引用候选 %d 条（%d 因子 × %s）",
                registered, len(fids), ",".join(tfs))
    return {"factor_ids": fids, "registered": registered, "timeframes": tfs}


def _grade_from_metrics(
    ic_mean: float,
    icir: float,
    oos_sharpe: float,
    oos_net: float,
    avg_net_per_trade: float,
    cost: float,
    min_sharpe: float,
    min_net: float,
) -> str:
    """与 factor_backtest_scorer.score_formula 相同的分级门槛（不含 DSR/冗余）。"""
    from backend.config import settings as _s

    net_buffer = float(getattr(_s, "FACTOR_SCORER_NET_BUFFER", 0.0005))
    perf_ok = (
        oos_sharpe >= min_sharpe
        and oos_net > min_net
        and avg_net_per_trade > (cost + net_buffer)
    )
    abs_ic = abs(ic_mean)
    abs_icir = abs(icir)
    if abs_ic >= 0.05 and abs_icir > 0.5 and perf_ok:
        return "A"
    if abs_ic >= 0.03 and abs_icir > 0.3 and perf_ok:
        return "B"
    if abs_ic >= 0.015:
        return "C"
    return "D"


def _rolling_recompute(
    calc,
    registry_factor_id: str,
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    fwd: int,
    min_hist: int = 80,
) -> np.ndarray:
    """快照型（标量）因子 → 历史序列：滑动窗口逐点重算。

    legacy_compat 等因子只返回「序列末值」单点（`_LegacyBase.calculate`），
    全量计算 900 根只得到 1 个非 NaN，walk-forward 无法进行。
    本函数以 stride=fwd 在历史每个调仓点对 df[:t+1] 重算一次取末值，
    构造出可回测的因子序列。仅用 t 时刻前的数据，无未来信息。
    """
    n = len(df)
    out = np.full(n, np.nan)
    if n < min_hist:
        return out
    try:
        factor = calc.registry.get(registry_factor_id, params=None)
    except Exception as e:
        logger.debug("[MidlongRegistry] rolling 取因子失败 %s: %s", registry_factor_id, e)
        return out
    stride = max(1, int(fwd))
    for t in range(min_hist, n, stride):
        sub = df.iloc[: t + 1]
        try:
            factor.validate_data(sub)
            proc = factor.preprocess_data(sub)
            res = factor.calculate(proc)
            res = factor.postprocess_result(res)
        except Exception:
            continue
        if res is None or not len(res):
            continue
        v = np.asarray(res, dtype=float)[-1]
        if np.isfinite(v):
            out[t] = float(v)
    return out


def _score_one_registry_factor(
    fid: str,
    registry_factor_id: str,
    timeframe: str,
    symbols: tuple = _SYMBOLS,
) -> Optional[Dict[str, Any]]:
    """对一个 registry 因子在指定 timeframe 上打分（复用闸门引擎）。

    fid 为 store 记录 id（如 ai_gen_bsq@4h）；registry_factor_id 为真实 registry id
    （FactorCalculator 计算用）。
    """
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
    from backend.services.factor_engine.factor_calculator import FactorCalculator
    from backend.services.factor_engine.factor_evaluator import get_factor_evaluator

    lookback = int(_cfg("FACTOR_SCORER_MIDLONG_LOOKBACK", 900))
    fwd = int(_cfg("FACTOR_SCORER_MIDLONG_FWD_1D", 3)) if timeframe == "1d" \
        else int(_cfg("FACTOR_SCORER_MIDLONG_FWD_4H", 6))
    min_sharpe = float(_cfg("FACTOR_SCORER_MIDLONG_MIN_SHARPE", 0.4))
    min_net = float(_cfg("FACTOR_SCORER_MIN_NET_RETURN", 0.0))
    cost = float(_cfg("FACTOR_SCORER_COST", 0.0021))
    funding_rate = float(_cfg("FACTOR_SCORER_FUNDING_RATE", 0.0001))
    bars_per_year = int(round(365.0 * 24.0 / {"4h": 4.0, "1d": 24.0}.get(timeframe, 4.0)))

    calc = FactorCalculator()
    evaluator = get_factor_evaluator(forward_period=fwd)

    ic_list: List[float] = []
    icir_list: List[float] = []
    decay_list: List[int] = []
    mono_list: List[float] = []
    net_list: List[float] = []
    sharpe_list: List[float] = []
    wr_list: List[float] = []
    trades_total = 0
    net_total = 0.0
    per_symbol: Dict[str, Any] = {}
    data_points = 0

    for sym in symbols:
        klines = factor_backtest_scorer._load_klines(sym, timeframe, lookback)
        if not klines or len(klines) < 120:
            continue
        try:
            import pandas as _pd
            df = _pd.DataFrame(klines)
        except Exception:
            continue
        try:
            series_map = calc.calculate([registry_factor_id], df, symbol=sym, timeframe=timeframe)
        except Exception as e:
            logger.debug("[MidlongRegistry] %s/%s 计算失败: %s", fid, timeframe, e)
            continue
        series = series_map.get(registry_factor_id)
        if series is not None and len(series):
            _v = np.asarray(series, dtype=float)
        else:
            _v = np.zeros(0)
        # [2026-08-15] 快照型因子回退：全量计算只得到末值单点时（有效值过少），
        # 改走滑动窗口逐点重算，构造可回测的历史序列。
        if int(np.isfinite(_v).sum()) < max(60, int(len(df) * 0.05)):
            vals = _rolling_recompute(calc, registry_factor_id, df, sym, timeframe, fwd)
        else:
            vals = _v
        closes = df["close"].astype(float).to_numpy()
        n = min(len(vals), len(closes))
        vals, closes = vals[-n:], closes[-n:]
        if not np.isfinite(vals).sum() >= 60:
            continue
        # IC/ICIR/衰减/单调性（与 score_formula 同款）
        try:
            rep = evaluator.evaluate_factor(
                fid, pd.Series(vals), pd.Series(closes), forward_period=fwd,
            )
            if rep.data_points >= 30:
                ic_list.append(rep.ic_mean)
                icir_list.append(rep.icir)
                decay_list.append(rep.ic_decay_halflife)
                mono_list.append(rep.monotonicity)
                data_points += rep.data_points
        except Exception as e:
            logger.debug("[MidlongRegistry] %s evaluate 失败: %s", fid, e)
        # 样本外 walk-forward（与公式因子同一引擎）
        bt = factor_backtest_scorer._walk_forward_backtest(
            vals, closes, fwd, cost,
            funding_per_hold=funding_rate * (fwd * {"4h": 4.0, "1d": 24.0}.get(timeframe, 4.0) / 8.0)
            if funding_rate > 0 else 0.0,
            bars_per_year=bars_per_year,
        )
        if bt.get("trades", 0) > 0:
            net_list.append(bt["net_return"])
            sharpe_list.append(bt["sharpe"])
            wr_list.append(bt["win_rate"])
            trades_total += bt["trades"]
            net_total += bt["net_return"]
            per_symbol[sym] = bt

    if not net_list or not ic_list:
        return {"factor_id": fid, "timeframe": timeframe, "reason": "有效样本不足",
                "grade": "F", "admitted": False}

    ic_mean = float(np.mean(ic_list))
    icir = float(np.mean(icir_list))
    oos_net = float(np.mean(net_list))
    oos_sharpe = float(np.mean(sharpe_list))
    avg_net_per_trade = net_total / max(trades_total, 1)
    grade = _grade_from_metrics(
        ic_mean, icir, oos_sharpe, oos_net, avg_net_per_trade,
        cost, min_sharpe, min_net,
    )
    return {
        "factor_id": fid,
        "timeframe": timeframe,
        "grade": grade,
        "admitted": grade in ("A", "B"),
        "ic_mean": round(ic_mean, 5),
        "icir": round(icir, 4),
        "ic_decay_halflife": int(np.mean(decay_list)) if decay_list else 0,
        "monotonicity": round(float(np.mean(mono_list)), 4) if mono_list else 0.0,
        "oos_net_return": round(oos_net, 6),
        "oos_sharpe": round(oos_sharpe, 4),
        "oos_win_rate": round(float(np.mean(wr_list)), 4),
        "oos_trades": trades_total,
        "per_symbol": per_symbol,
        "data_points": data_points,
    }


def scan_registry_midlong(limit: int = 200) -> Dict[str, Any]:
    """对 registry 引用候选逐个打分并回写 store（candidate → active/rejected）。"""
    from backend.services.factor_engine.custom_factor_store import custom_factor_store

    _tid = _admin_tenant()
    cands = [
        r for r in custom_factor_store.list_candidates(tenant_id=_tid)
        if str((r.get("extra") or {}).get("kind") or "") == "registry"
    ][:limit]
    results: List[Dict[str, Any]] = []
    for rec in cands:
        fid = rec.get("factor_id")
        _extra = rec.get("extra") or {}
        registry_fid = str(_extra.get("registry_factor_id") or fid)
        tf = str(_extra.get("timeframe") or "4h").lower()
        try:
            r = _score_one_registry_factor(fid, registry_fid, tf)
        except Exception as e:
            logger.warning("[MidlongRegistry] %s/%s 打分异常: %s", fid, tf, e)
            continue
        if not r:
            continue
        status = "active" if r["admitted"] else "rejected"
        custom_factor_store.update_scores(
            fid,
            grade=r.get("grade", "F"),
            scores={
                "ic_mean": r.get("ic_mean", 0.0),
                "icir": r.get("icir", 0.0),
                "ic_decay_halflife": r.get("ic_decay_halflife", 0),
                "monotonicity": r.get("monotonicity", 0.0),
                "oos_net_return": r.get("oos_net_return", 0.0),
                "oos_sharpe": r.get("oos_sharpe", 0.0),
                "oos_win_rate": r.get("oos_win_rate", 0.0),
                "oos_trades": r.get("oos_trades", 0),
                "per_symbol": r.get("per_symbol") or {},
                "reason": r.get("reason", ""),
            },
            status=status,
            tenant_id=_tid,
        )
        results.append({
            "factor_id": fid, "timeframe": tf, "grade": r.get("grade"),
            "admitted": r.get("admitted", False), "ic": r.get("ic_mean"),
            "oos_sharpe": r.get("oos_sharpe"),
        })
    promoted = [r for r in results if r["admitted"]]
    logger.info("[MidlongRegistry] 扫描完成: 打分%d 晋升%d", len(results), len(promoted))
    return {"scored": len(results), "promoted": len(promoted), "results": results}


def seed_and_scan() -> Dict[str, Any]:
    """登记 + 扫描一步完成（CLI/接口用）。"""
    seed = seed_registry_candidates()
    scan = scan_registry_midlong()
    return {"seed": seed, "scan": scan}
