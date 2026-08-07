"""
K 线数据增强 — 将订单流(CVD/Taker)与链上/社交时间序列按 K 线时间戳对齐，禁止整列广播假历史。
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

FLOW_EXCHANGE = "hyperliquid"

TF_TO_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


def normalize_flow_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    for suf in ("USDT", "USDC", "USD", "PERP"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    return s or symbol


def floor_ts_ms(ts_ms: int, interval_ms: int) -> int:
    return (ts_ms // interval_ms) * interval_ms


def attach_flow_timeseries_to_df(
    db,
    symbol: str,
    df: pd.DataFrame,
    timeframe: str,
) -> pd.DataFrame:
    """把 MarketTradesAggregated 按 K 线周期桶对齐，写入 cvd_delta / taker_* 列。"""
    if df is None or df.empty or "timestamp" not in df.columns:
        return df

    interval_ms = TF_TO_MS.get(timeframe)
    if not interval_ms:
        return df

    from backend.database.models import MarketTradesAggregated

    flow_sym = normalize_flow_symbol(symbol)
    ts_sec = df["timestamp"].astype(np.int64)
    start_ms = int(ts_sec.min()) * 1000
    end_ms = int(ts_sec.max()) * 1000 + interval_ms

    try:
        rows = (
            db.query(
                MarketTradesAggregated.timestamp,
                MarketTradesAggregated.taker_buy_notional,
                MarketTradesAggregated.taker_sell_notional,
            )
            .filter(
                MarketTradesAggregated.exchange == FLOW_EXCHANGE,
                MarketTradesAggregated.symbol == flow_sym,
                MarketTradesAggregated.timestamp >= start_ms,
                MarketTradesAggregated.timestamp <= end_ms,
            )
            .order_by(MarketTradesAggregated.timestamp)
            .all()
        )
    except Exception as e:
        logger.debug(f"[KlineEnrich] flow query {flow_sym}/{timeframe}: {e}")
        return df

    if not rows:
        df["cvd_delta"] = np.nan
        df["taker_buy_notional"] = np.nan
        df["taker_sell_notional"] = np.nan
        df["taker_ratio"] = np.nan
        df["flow_data_ok"] = False
        return df

    buckets: Dict[int, Dict[str, float]] = {}
    for ts, buy_n, sell_n in rows:
        bkt = floor_ts_ms(int(ts), interval_ms)
        if bkt not in buckets:
            buckets[bkt] = {"buy": 0.0, "sell": 0.0}
        buckets[bkt]["buy"] += float(buy_n or 0)
        buckets[bkt]["sell"] += float(sell_n or 0)

    bar_ms = (ts_sec * 1000).values
    cvd_deltas = []
    taker_buys = []
    taker_sells = []
    taker_ratios = []
    for tms in bar_ms:
        b = buckets.get(int(tms), {"buy": 0.0, "sell": 0.0})
        buy, sell = b["buy"], b["sell"]
        taker_buys.append(buy)
        taker_sells.append(sell)
        cvd_deltas.append(buy - sell)
        taker_ratios.append(buy / sell if sell > 0 else (1.0 if buy > 0 else np.nan))

    out = df.copy()
    out["cvd_delta"] = cvd_deltas
    out["taker_buy_notional"] = taker_buys
    out["taker_sell_notional"] = taker_sells
    out["taker_ratio"] = taker_ratios
    out["flow_data_ok"] = True
    return out


def attach_aux_timeseries_to_df(
    db,
    symbol: str,
    df: pd.DataFrame,
    fear_greed_daily: Optional[Dict[int, float]] = None,
) -> pd.DataFrame:
    """链上/社交：DB 快照 merge_asof + 恐惧贪婪日序列按日对齐。"""
    if df is None or df.empty or "timestamp" not in df.columns:
        return df

    from backend.database.models import SymbolAuxTimeseries

    sym = (symbol or "").upper()
    ts_sec = df["timestamp"].astype(np.int64).values
    bar_ms = pd.Series(ts_sec * 1000, index=df.index)

    aux_cols = [
        "fear_greed", "btc_dominance", "tvl", "exchange_net_flow",
        "whale_tx_count", "whale_tx_volume", "active_addresses",
        "social_score", "news_sentiment", "discussion_volume",
    ]
    out = df.copy()
    for c in aux_cols:
        out[c] = np.nan

    rows = []
    try:
        start_ms = int(bar_ms.min())
        rows = (
            db.query(SymbolAuxTimeseries)
            .filter(
                SymbolAuxTimeseries.symbol == sym,
                SymbolAuxTimeseries.timestamp_ms >= start_ms - 7 * 24 * 3600 * 1000,
                SymbolAuxTimeseries.timestamp_ms <= int(bar_ms.max()) + 3600_000,
            )
            .order_by(SymbolAuxTimeseries.timestamp_ms)
            .all()
        )
        if rows:
            aux_df = pd.DataFrame([
                {
                    "timestamp_ms": r.timestamp_ms,
                    **{c: getattr(r, c) for c in aux_cols},
                }
                for r in rows
            ])
            aux_df = aux_df.sort_values("timestamp_ms")
            left = pd.DataFrame({"timestamp_ms": bar_ms.values}).sort_values("timestamp_ms")
            merged = pd.merge_asof(
                left, aux_df, on="timestamp_ms", direction="backward",
            )
            for c in aux_cols:
                out[c] = merged[c].values
    except Exception as e:
        logger.debug(f"[KlineEnrich] aux merge {sym}: {e}")

    if fear_greed_daily:
        fgi = []
        for ts in ts_sec:
            day_ms = (int(ts) // 86400) * 86400 * 1000
            fgi.append(fear_greed_daily.get(day_ms, np.nan))
        if any(v == v for v in fgi):
            out["fear_greed"] = [
                fgi[i] if fgi[i] == fgi[i] else out["fear_greed"].iloc[i]
                for i in range(len(fgi))
            ]

    out["aux_data_ok"] = len(rows) > 0 if rows else False
    return out


def enrich_kline_dataframe(
    db,
    symbol: str,
    df: pd.DataFrame,
    timeframe: str,
    *,
    scalar_onchain: Optional[Dict[str, Any]] = None,
    scalar_social: Optional[Dict[str, Any]] = None,
    fear_greed_daily: Optional[Dict[int, float]] = None,
) -> pd.DataFrame:
    """
    完整增强：订单流时间序列 + 链上/社交历史对齐。
    仅在无任何历史时使用 scalar 作为最后一根 K 线的值（不再整列广播）。
    """
    out = attach_flow_timeseries_to_df(db, symbol, df, timeframe)
    out = attach_aux_timeseries_to_df(db, symbol, out, fear_greed_daily=fear_greed_daily)

    has_flow = (
        out["flow_data_ok"].any()
        if "flow_data_ok" in out.columns and len(out) > 0
        else False
    )
    has_aux = (
        out["aux_data_ok"].any()
        if "aux_data_ok" in out.columns and len(out) > 0
        else False
    )

    if not has_aux and (scalar_onchain or scalar_social):
        last_idx = out.index[-1]
        oc = scalar_onchain or {}
        sc = scalar_social or {}
        if oc:
            for k in ("exchange_net_flow", "whale_tx_count", "whale_tx_volume", "tvl",
                      "active_addresses", "fear_greed", "btc_dominance"):
                if k in oc and k in out.columns:
                    out.at[last_idx, k] = oc.get(k)
        if sc:
            for k in ("social_score", "news_sentiment", "discussion_volume"):
                if k in out.columns:
                    out.at[last_idx, k] = sc.get(k, np.nan)
        logger.debug(
            f"[KlineEnrich] {symbol}/{timeframe} 无历史序列，仅最后一根K线写入当前链上/社交快照"
        )

    return out


def fetch_fear_greed_daily_history(limit: int = 60) -> Dict[int, float]:
    """alternative.me 恐惧贪婪日序列 → {day_start_ms: value}"""
    result: Dict[int, float] = {}
    try:
        import requests
        resp = requests.get(
            f"https://api.alternative.me/fng/?limit={limit}",
            timeout=12,
        )
        if resp.status_code != 200:
            return result
        for item in resp.json().get("data", []):
            ts = int(item.get("timestamp", 0))
            if ts > 0:
                day_ms = (ts // 86400) * 86400 * 1000
                result[day_ms] = float(item.get("value", 50))
    except Exception as e:
        logger.debug(f"[KlineEnrich] fear_greed history: {e}")
    return result


def record_aux_snapshots(
    db,
    symbols: List[str],
    onchain_data: Dict[str, Dict[str, Any]],
    social_data: Dict[str, Dict[str, Any]],
) -> None:
    """每次采集写入 symbol_aux_timeseries（供后续 merge_asof）。"""
    import time
    from backend.database.models import SymbolAuxTimeseries

    now_ms = int(time.time() * 1000)
    try:
        for sym in symbols:
            sym_u = (sym or "").upper()
            oc = onchain_data.get(sym) or onchain_data.get(sym_u) or {}
            sc = social_data.get(sym) or social_data.get(sym_u) or {}
            existing = (
                db.query(SymbolAuxTimeseries)
                .filter(
                    SymbolAuxTimeseries.symbol == sym_u,
                    SymbolAuxTimeseries.timestamp_ms == now_ms,
                )
                .first()
            )
            payload = dict(
                symbol=sym_u,
                timestamp_ms=now_ms,
                fear_greed=oc.get("fear_greed"),
                btc_dominance=oc.get("btc_dominance"),
                tvl=oc.get("tvl"),
                exchange_net_flow=oc.get("exchange_net_flow"),
                whale_tx_count=oc.get("whale_tx_count"),
                whale_tx_volume=oc.get("whale_tx_volume"),
                active_addresses=oc.get("active_addresses"),
                social_score=sc.get("social_score"),
                news_sentiment=sc.get("news_sentiment"),
                discussion_volume=sc.get("discussion_volume"),
            )
            if existing:
                for k, v in payload.items():
                    if k not in ("symbol", "timestamp_ms") and hasattr(existing, k):
                        setattr(existing, k, v)
            else:
                db.add(SymbolAuxTimeseries(**payload))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.debug(f"[KlineEnrich] record_aux_snapshots: {e}")


def capture_flow_indicators_for_symbol(db, symbol: str) -> Dict[str, Any]:
    """当前窗口 CVD/Taker 汇总，写入 unified indicators。"""
    from services.market_flow_indicators import get_flow_indicators_for_prompt

    flow_sym = normalize_flow_symbol(symbol)
    out: Dict[str, Any] = {}
    for period in ("15m", "1h"):
        try:
            block = get_flow_indicators_for_prompt(
                db, flow_sym, period, ["CVD", "TAKER"],
            )
            cvd = block.get("CVD") or {}
            taker = block.get("TAKER") or {}
            if cvd:
                cum = float(cvd.get("cumulative", 0) or 0)
                cur = float(cvd.get("current", 0) or 0)
                out[f"cvd_cumulative_{period}"] = cum
                out[f"cvd_delta_{period}"] = cur
                total = abs(cum) + 1e-9
                out[f"cvd_ratio_{period}"] = cur / total if total else 0.0
            if taker:
                buy = float(taker.get("buy", 0) or 0)
                sell = float(taker.get("sell", 0) or 0)
                ratio = float(taker.get("ratio", 1) or 1)
                out[f"taker_buy_{period}"] = buy
                out[f"taker_sell_{period}"] = sell
                out[f"taker_ratio_{period}"] = ratio
                if period == "1h":
                    out["taker_ratio"] = ratio
                    out["cvd_ratio"] = out.get(f"cvd_ratio_{period}", 0.0)
        except Exception as e:
            logger.debug(f"[KlineEnrich] flow indicators {flow_sym}/{period}: {e}")
    out["flow_data_ok"] = bool(out)
    return out
