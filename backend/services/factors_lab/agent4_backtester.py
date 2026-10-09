# -*- coding: utf-8 -*-
"""④ 回测工程师 agent —— gpfactor / gpbacktest 工具层。

gpfactor：AST 在白名单宇宙逐币求值 → 面板（因子值 × 前瞻收益）；
gpbacktest：IC/RankIC 序列与均值、ICIR、五分位价差、换手、IC 半衰期。
数据装载复用 hybrid_scoring.kpanel（alpha_market 直连 + crypto 白名单 + 股票黑名单）。
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backend.services.factors_lab import agent3_engineer, common, config

logger = logging.getLogger(__name__)

_EPS = 1e-12


def _universe(n: int) -> List[str]:
    try:
        from backend.services.hybrid_scoring import kpanel
        return kpanel.top_liquid_symbols(n, days=60, period=config.PANEL_PERIOD)
    except Exception as e:  # noqa: BLE001
        logger.warning("[FactorsLab④] 宇宙获取失败: %s", str(e)[:120])
        return []


def _load_klines(sym: str, bars: int) -> Optional[pd.DataFrame]:
    try:
        from backend.services.hybrid_scoring import kpanel
        df = kpanel.load_klines(sym, period=config.PANEL_PERIOD, bars=bars)
        if df is None or len(df) < 80:
            return None
        d = df.copy()
        d["vwap"] = (d["high"] + d["low"] + d["close"]) / 3.0
        d["returns"] = d["close"].pct_change()
        return d
    except Exception:
        return None


def gpfactor(ast: dict, universe: List[str], bars: int = 300) -> pd.DataFrame:
    """AST → 面板 DataFrame(index=date,symbol: [factor, fwd_ret])。求值失败币跳过。"""
    from backend.services.factor_engine.expr.parser import ExprError, parse

    expr = parse(ast)  # 调用方已验过；再 parse 一次拿可执行对象
    frames = []
    for sym in universe:
        df = _load_klines(sym, bars)
        if df is None:
            continue
        try:
            fields = {c: df[c].to_numpy(dtype=float)
                      for c in ("open", "high", "low", "close", "volume", "vwap", "returns")}
            vals = np.asarray(expr.evaluate(fields), dtype=float)
        except Exception:
            continue
        if vals.size != len(df):
            continue
        f = pd.DataFrame({
            "factor": vals,
            "fwd_ret": df["close"].shift(-1).to_numpy() / (df["close"].to_numpy() + _EPS) - 1.0,
            "symbol": sym,
        }, index=df.index)
        frames.append(f.dropna())
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames)
    panel.index.name = "date"
    panel["n_cs"] = panel.groupby(level="date")["symbol"].transform("count")
    panel = panel[panel["n_cs"] >= 5].drop(columns=["n_cs"])
    return panel.sort_index()


def _rank_ic_series(panel: pd.DataFrame) -> pd.Series:
    def _ric(g):
        if len(g) < 5:
            return np.nan
        rx = g["factor"].rank()
        ry = g["fwd_ret"].rank()
        sd = rx.std() * ry.std()
        return float(rx.cov(ry) / sd) if sd and sd > 0 else np.nan
    return panel.groupby(level="date").apply(_ric)


def gpbacktest(ast: dict, universe: Optional[List[str]] = None,
               bars: int = 300, *, timeout_sec: float = 60.0) -> Dict[str, object]:
    """完整评估报告。超时/数据不足返回失败结构（不抛出）。"""
    t0 = time.time()
    universe = universe or _universe(config.universe_n())
    if not universe:
        return {"ok": False, "reason": "universe_empty"}
    try:
        panel = gpfactor(ast, universe, bars=bars)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"eval_error: {str(e)[:80]}"}
    if panel.empty or panel.index.get_level_values(0).nunique() < 30:
        return {"ok": False, "reason": f"panel_too_small rows={len(panel)}"}
    if time.time() - t0 > timeout_sec:
        return {"ok": False, "reason": "timeout"}

    ics = _rank_ic_series(panel).dropna()
    ic = float(ics.mean()) if len(ics) else 0.0
    icir = float(ics.mean() / (ics.std() + _EPS)) if len(ics) > 2 else 0.0

    # 五分位价差（逐日 top20% - bottom20% 前瞻收益，取时序均值）
    def _q(g):
        try:
            q = pd.qcut(g["factor"].rank(method="first"), 5, labels=False, duplicates="drop")
            top = g.loc[q == q.max(), "fwd_ret"].mean()
            bot = g.loc[q == q.min(), "fwd_ret"].mean()
            return float(top - bot)
        except Exception:
            return np.nan
    spread = float(np.nanmean(panel.groupby(level="date").apply(_q)))

    # 换手：因子排名逐日变化占比（秩相关 1-|Δrank| 近似用 1-rank autocorr）
    def _turn(g):
        if len(g) < 5:
            return np.nan
        return float(g["factor"].rank().corr(g["factor"].shift(0).rank()))
    rank_persistence = float(np.nanmean(
        panel.groupby("symbol")["factor"].apply(lambda s: s.rank().autocorr(lag=1))))
    turnover = 1.0 - (rank_persistence if np.isfinite(rank_persistence) else 0.0)

    # IC 半衰期：IC(滞后k) 衰减到 |IC0|/2 的最小 k
    def _lag_ic(k: int) -> float:
        df2 = panel.copy()
        df2["fwd_k"] = df2.groupby("symbol")["fwd_ret"].shift(-(k - 1))
        d = df2.dropna(subset=["fwd_k"])
        if d.empty:
            return np.nan
        return float(np.nanmean(d.groupby(level="date").apply(
            lambda g: g["factor"].rank().corr(g["fwd_k"].rank()) if len(g) >= 5 else np.nan)))

    half_life = None
    ic0 = abs(ic)
    if ic0 > 1e-6:
        for k in range(1, 11):
            lik = abs(_lag_ic(k))
            if np.isfinite(lik) and lik <= ic0 / 2:
                half_life = k
                break

    return {
        "ok": True, "n_rows": int(len(panel)), "n_symbols": int(panel["symbol"].nunique()),
        "n_days": int(panel.index.get_level_values(0).nunique()),
        "mean_rank_ic": round(ic, 5), "icir": round(icir, 4),
        "quantile_spread": round(spread, 6), "turnover": round(float(turnover), 4),
        "ic_half_life_bars": half_life, "elapsed_sec": round(time.time() - t0, 2),
    }


def evaluate_candidate(cand: Dict[str, object]) -> Dict[str, object]:
    """对单候选执行 gpbacktest，附判定。"""
    ast = cand.get("ast")
    if cand.get("status") != "unit_pass" or not isinstance(ast, dict):
        return {"ok": False, "reason": f"candidate_not_testable({cand.get('status')})"}
    rep = gpbacktest(ast)
    rep["cand_id"] = cand.get("cand_id")
    rep["hyp_id"] = cand.get("hyp_id")
    return rep
