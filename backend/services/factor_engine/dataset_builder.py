"""
多源训练数据装配器 — K 线 + 资金费/OI/CVD/链上社交/清算/事件 对齐到同一时间轴。

[2026-08-15 阶段3 T1]
    此前因子/训练离线数据只有 OHLCV（factor_evolution_loop._load_data 只取
    data_center.get_klines），资金费/OI/CVD/链上/事件数据虽已落库却从不进训练
    管道。本模块以 crypto_klines 为时间骨架，merge_asof 对齐各源，产出可直接
    供 ContinualTrainingPipeline / factor_evolution_loop / v3_factor_pipeline
    使用的样本 DataFrame。

关键设计：
    1. 覆盖率门槛（诚实原则）：某列在窗口内非空占比 < min_coverage 时整列丢弃
       并记录在 coverage_report，绝不填充假值（ffill 仅用于「低频真实值」列，
       且同样受覆盖率门槛约束）。
    2. 防未来函数：所有对齐源用「事件时刻 ≤ bar 收盘时刻」口径；事件特征只
       使用截至该 bar 收盘已知的信息（含 asof 资金费/OI），标签另由调用方
       提供（前视收益等）。
    3. 时间单位：crypto_klines.timestamp 为秒；市场流/资金费表为毫秒；统一换算。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 各数据源 → 覆盖率门槛默认值
DEFAULT_MIN_COVERAGE = 0.8


def _range_of(df: pd.DataFrame) -> Tuple[int, int]:
    start = int(df.index[0].timestamp()) if len(df) else 0
    end = int(df.index[-1].timestamp()) if len(df) else 0
    return start, end


def load_base_klines(
    symbol: str,
    timeframe: str,
    count: int = 500,
    exchange: Optional[str] = None,
) -> pd.DataFrame:
    """K 线骨架（DatetimeIndex，UTC tz-aware，OHLCV）。

    [2026-08-15] data_center.to_dataframe 的索引是 naive datetime；不统一时区会
    让 .timestamp() 按本地时区解释（+08 环境整体偏移 8 小时），导致事件/资金费
    窗口错位。此处统一 localize 到 UTC。
    """
    from backend.services.data_center import data_center

    df = data_center.get_klines_df(
        symbol, timeframe, count=count, exchange=exchange or None, purpose="research"
    )
    if df.empty:
        logger.warning("[DatasetBuilder] %s/%s 无 K 线", symbol, timeframe)
        return df
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def load_funding_and_oi(symbol: str, start_s: int, end_s: int, exchange: Optional[str] = None) -> pd.DataFrame:
    """资金费率（perp_funding）+ OI（market_asset_metrics）对齐序列（毫秒→秒）。"""
    from sqlalchemy import text as _sa_text

    from backend.database.connection import MarketSessionLocal
    out = pd.DataFrame()
    try:
        with MarketSessionLocal() as db:
            ex_clause = "AND exchange = :ex" if exchange else ""
            funding = db.execute(
                _sa_text(
                    f"SELECT timestamp, funding_rate, exchange, symbol FROM perp_funding "
                    f"WHERE symbol = :sym AND timestamp >= :s AND timestamp <= :e {ex_clause} "
                    f"ORDER BY timestamp"
                ),
                {"sym": symbol, "s": start_s * 1000, "e": (end_s + 3600) * 1000, "ex": (exchange or "").lower()},
            ).mappings().all()
            oi = db.execute(
                _sa_text(
                    f"SELECT timestamp, open_interest, funding_rate, mark_price, exchange, symbol "
                    f"FROM market_asset_metrics "
                    f"WHERE symbol = :sym AND timestamp >= :s AND timestamp <= :e {ex_clause} "
                    f"ORDER BY timestamp"
                ),
                {"sym": symbol, "s": start_s * 1000, "e": (end_s + 3600) * 1000, "ex": (exchange or "").lower()},
            ).mappings().all()
        if funding:
            fdf = pd.DataFrame(funding)
            fdf["ts"] = pd.to_datetime(fdf["timestamp"], unit="ms")
            fdf = fdf.set_index("ts").sort_index()
            fdf = fdf[~fdf.index.duplicated(keep="last")]
            out["funding_rate"] = fdf["funding_rate"].astype(float)
        if oi:
            odf = pd.DataFrame(oi)
            odf["ts"] = pd.to_datetime(odf["timestamp"], unit="ms")
            odf = odf.set_index("ts").sort_index()
            odf = odf[~odf.index.duplicated(keep="last")]
            out["open_interest"] = odf["open_interest"].astype(float)
            out["mark_price"] = odf["mark_price"].astype(float)
    except Exception as exc:
        logger.debug("[DatasetBuilder] funding/oi 加载失败: %s", exc)
    return out


def load_cvd(symbol: str, start_s: int, end_s: int, exchange: Optional[str] = None) -> pd.DataFrame:
    """吃单成交聚合（market_trades_aggregated 15s 窗口 → bar 内净吃单额/笔数）。"""
    from sqlalchemy import text as _sa_text

    from backend.database.connection import MarketSessionLocal
    out = pd.DataFrame()
    try:
        with MarketSessionLocal() as db:
            ex_clause = "AND exchange = :ex" if exchange else ""
            rows = db.execute(
                _sa_text(
                    f"SELECT timestamp, taker_buy_notional, taker_sell_notional, "
                    f"taker_buy_count, taker_sell_count, largest_trade_usd, largest_trade_side "
                    f"FROM market_trades_aggregated "
                    f"WHERE symbol = :sym AND timestamp >= :s AND timestamp <= :e {ex_clause} "
                    f"ORDER BY timestamp"
                ),
                {"sym": symbol, "s": start_s * 1000, "e": (end_s + 3600) * 1000, "ex": (exchange or "").lower()},
            ).mappings().all()
        if not rows:
            return out
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.set_index("ts").sort_index()
        for c in ("taker_buy_notional", "taker_sell_notional", "taker_buy_count", "taker_sell_count"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        # 以窗口行原始粒度返回，由 merge 端按 bar 聚合
        out["taker_buy_notional"] = df["taker_buy_notional"]
        out["taker_sell_notional"] = df["taker_sell_notional"]
        out["taker_buy_count"] = df["taker_buy_count"]
        out["taker_sell_count"] = df["taker_sell_count"]
        out["largest_trade_usd"] = pd.to_numeric(df["largest_trade_usd"], errors="coerce")
    except Exception as exc:
        logger.debug("[DatasetBuilder] cvd 加载失败: %s", exc)
    return out


def load_aux(symbol: str, start_s: int, end_s: int) -> pd.DataFrame:
    """链上/社交辅助时序（symbol_aux_timeseries，毫秒）。"""
    from sqlalchemy import text as _sa_text

    from backend.database.connection import MarketSessionLocal
    out = pd.DataFrame()
    try:
        with MarketSessionLocal() as db:
            rows = db.execute(
                _sa_text(
                    "SELECT timestamp_ms, fear_greed, btc_dominance, tvl, exchange_net_flow, "
                    "whale_tx_count, whale_tx_volume, active_addresses, social_score, "
                    "news_sentiment, discussion_volume FROM symbol_aux_timeseries "
                    "WHERE symbol = :sym AND timestamp_ms >= :s AND timestamp_ms <= :e ORDER BY timestamp_ms"
                ),
                {"sym": symbol, "s": start_s * 1000, "e": (end_s + 3600) * 1000},
            ).mappings().all()
        if not rows:
            return out
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["timestamp_ms"], unit="ms")
        df = df.set_index("ts").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        for c in df.columns:
            if c != "timestamp_ms":
                df[c] = pd.to_numeric(df[c], errors="coerce")
        out = df.drop(columns=["timestamp_ms"], errors="ignore")
    except Exception as exc:
        logger.debug("[DatasetBuilder] aux 加载失败: %s", exc)
    return out


def load_liquidation(symbol: str, start_s: int, end_s: int) -> pd.DataFrame:
    """清算小时聚合（long/short USD）。"""
    from sqlalchemy import text as _sa_text

    from backend.database.connection import MarketSessionLocal
    out = pd.DataFrame()
    try:
        with MarketSessionLocal() as db:
            rows = db.execute(
                _sa_text(
                    "SELECT ts_ms, long_usd, short_usd FROM liquidation_events "
                    "WHERE symbol = :sym AND ts_ms >= :s AND ts_ms <= :e ORDER BY ts_ms"
                ),
                {"sym": symbol, "s": start_s * 1000, "e": (end_s + 3600) * 1000},
            ).mappings().all()
        if not rows:
            return out
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms")
        df = df.set_index("ts").sort_index()
        out["liquidation_long_usd"] = pd.to_numeric(df["long_usd"], errors="coerce")
        out["liquidation_short_usd"] = pd.to_numeric(df["short_usd"], errors="coerce")
    except Exception as exc:
        logger.debug("[DatasetBuilder] liquidation 加载失败: %s", exc)
    return out


def load_events(symbol: str, start_s: int, end_s: int) -> pd.DataFrame:
    """新闻/宏观/鲸鱼事件（事件时间戳序列，供 bar 窗口聚合与 hours-since 特征）。

    防泄漏口径：只返回 ts ≤ end_s 的事件；聚合端进一步限制 ts ≤ bar 收盘。
    """
    from sqlalchemy import text as _sa_text

    from backend.database.connection import MarketSessionLocal
    out = pd.DataFrame()
    try:
        with MarketSessionLocal() as db:
            news = db.execute(
                _sa_text(
                    "SELECT COALESCE(published_at, created_at) AS ts, impact_direction, "
                    "impact_strength, affected_symbols, event_category, title "
                    "FROM news_events WHERE created_at >= :s AND created_at <= :e ORDER BY created_at"
                ),
                {"s": pd.Timestamp(start_s, unit="s", tz="UTC"), "e": pd.Timestamp(end_s, unit="s", tz="UTC")},
            ).mappings().all()
            macro = db.execute(
                _sa_text(
                    "SELECT scheduled_at AS ts, impact_direction, impact_strength, importance, event "
                    "FROM macro_events WHERE scheduled_at >= :s AND scheduled_at <= :e ORDER BY scheduled_at"
                ),
                {"s": pd.Timestamp(start_s, unit="s", tz="UTC"), "e": pd.Timestamp(end_s, unit="s", tz="UTC")},
            ).mappings().all()
            whale = db.execute(
                _sa_text(
                    "SELECT timestamp AS ts, amount_usd, signal_direction, direction "
                    "FROM whale_activities WHERE symbol = :sym AND timestamp >= :s AND timestamp <= :e ORDER BY timestamp"
                ),
                {"sym": symbol, "s": pd.Timestamp(start_s, unit="s", tz="UTC"), "e": pd.Timestamp(end_s, unit="s", tz="UTC")},
            ).mappings().all()
        frames = []
        if news:
            nd = pd.DataFrame(news)
            nd["kind"] = "news"
            nd["score"] = pd.to_numeric(nd["impact_direction"], errors="coerce") * pd.to_numeric(
                nd["impact_strength"], errors="coerce"
            ).fillna(1)
            frames.append(nd[["ts", "kind", "score"]])
        if macro:
            md = pd.DataFrame(macro)
            md["kind"] = "macro"
            md["score"] = pd.to_numeric(md["impact_direction"], errors="coerce") * pd.to_numeric(
                md["importance"], errors="coerce"
            ).fillna(3)
            frames.append(md[["ts", "kind", "score"]])
        if whale:
            wd = pd.DataFrame(whale)
            wd["kind"] = "whale"
            wd["score"] = pd.to_numeric(wd["signal_direction"], errors="coerce").fillna(0) * pd.to_numeric(
                wd["amount_usd"], errors="coerce"
            ).fillna(0).clip(lower=0) / 1e6  # 金额加权，百万 USD 量纲
            frames.append(wd[["ts", "kind", "score"]])
        if frames:
            out = pd.concat(frames, ignore_index=True)
            # [2026-08-15] news/macro/whale 表的 timestamp 列是 naive TIMESTAMP，
            # 存储的是服务器本地时间（+08）。统一按本地时区解释再转 UTC，
            # 否则与 K 线（epoch UTC）对齐时整体偏移 8 小时。
            out["ts"] = pd.to_datetime(out["ts"])
            if getattr(out["ts"].dt, "tz", None) is None:
                out["ts"] = out["ts"].dt.tz_localize(
                    datetime.now().astimezone().tzinfo
                ).dt.tz_convert("UTC")
            out = out.dropna(subset=["ts"]).sort_values("ts")
            out = out[~out["ts"].duplicated(keep="last")]
    except Exception as exc:
        logger.debug("[DatasetBuilder] events 加载失败: %s", exc)
    return out


def aggregate_event_features(
    events: pd.DataFrame,
    bar_ends: pd.DatetimeIndex,
    event_window_hours: float = 24.0,
) -> Tuple[List[int], List[float]]:
    """把事件流聚合为每根 bar 的 (count, score)。

    防泄漏口径：事件 ts ∈ (bar_close - window, bar_close] 才算入该 bar；
    bar_close 之后的事件必须留给后续 bar（严格点-in-time）。
    独立成函数便于单元测试（permutation 泄漏测试）。
    """
    ev = events.sort_values("ts")
    counts: List[int] = []
    scores: List[float] = []
    window = pd.Timedelta(hours=event_window_hours)
    for be in bar_ends:
        w = ev[(ev["ts"] > be - window) & (ev["ts"] <= be)]
        counts.append(len(w))
        scores.append(float(w["score"].sum()) if len(w) else 0.0)
    return counts, scores


def build_enriched_dataset(
    symbol: str,
    timeframe: str,
    count: int = 500,
    exchange: Optional[str] = None,
    include: Tuple[str, ...] = ("funding", "oi", "cvd", "aux", "liquidation", "events"),
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    event_window_hours: float = 24.0,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """装配训练样本：K 线骨架 + 多源对齐列 + 覆盖率报告。

    Returns (df, report)。report 含每源覆盖率、被丢弃列、装配耗时。
    """
    t0 = time.time()
    report: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, "sources": {}, "dropped": []}

    base = load_base_klines(symbol, timeframe, count=count, exchange=exchange)
    if base.empty:
        report["error"] = "no klines"
        return base, report
    start_s, end_s = _range_of(base)
    bar_n = len(base)
    # 最后一根是未收盘成形 bar：其开盘时间 < 当前时刻。给所有加载窗口加一个
    # 周期缓冲，覆盖成形 bar 内的资金费/成交/事件（bar 内数据归属该 bar）。
    from backend.services.data_center import PERIOD_SECONDS
    period_s = PERIOD_SECONDS.get(timeframe, 3600)
    end_s = end_s + period_s
    merged = base.copy()
    merged["_ts_s"] = merged.index.view("int64") // 10**9

    def _asof_merge(source_df: pd.DataFrame, cols: List[str], label: str) -> None:
        if source_df.empty:
            report["sources"][label] = {"coverage": 0.0, "rows": 0}
            return
        tmp = source_df.copy()
        tmp["_ts_s"] = tmp.index.view("int64") // 10**9
        tmp = tmp.sort_values("_ts_s")
        # 防泄漏：只用 ≤ bar 收盘（merge_asof backward）
        m = pd.merge_asof(
            merged[["_ts_s"]].sort_values("_ts_s"),
            tmp[["_ts_s"] + cols],
            on="_ts_s",
            direction="backward",
            allow_exact_matches=True,
        )
        for c in cols:
            merged[c] = m[c].values
        report["sources"][label] = {
            "rows": len(tmp),
            "coverage": round(float(merged[cols[0]].notna().mean()), 3) if cols else 0.0,
        }

    if "funding" in include or "oi" in include:
        fio = load_funding_and_oi(symbol, start_s, end_s, exchange)
        if "funding" in include and "funding_rate" in fio.columns:
            _asof_merge(fio[["funding_rate"]], ["funding_rate"], "funding")
        if "oi" in include and "open_interest" in fio.columns:
            _asof_merge(fio[["open_interest", "mark_price"]], ["open_interest", "mark_price"], "oi")

    if "cvd" in include:
        cvd = load_cvd(symbol, start_s, end_s, exchange)
        if not cvd.empty:
            # bar 内聚合：吃单净额/笔数、最大单笔（searchsorted 分桶，稳健）
            tmp = cvd.copy()
            tmp["_ts_s"] = tmp.index.view("int64") // 10**9
            tmp = tmp.sort_values("_ts_s")
            bar_starts = merged["_ts_s"].sort_values().unique()
            idx = np.searchsorted(bar_starts, tmp["_ts_s"].values, side="right") - 1
            idx = np.clip(idx, 0, len(bar_starts) - 1)
            tmp["_bar"] = bar_starts[idx]
            agg = tmp.groupby("_bar").agg(
                taker_buy_usd=("taker_buy_notional", "sum"),
                taker_sell_usd=("taker_sell_notional", "sum"),
                taker_buy_count=("taker_buy_count", "sum"),
                taker_sell_count=("taker_sell_count", "sum"),
                largest_trade_usd=("largest_trade_usd", "max"),
            )
            for col in ("taker_buy_usd", "taker_sell_usd", "taker_buy_count",
                        "taker_sell_count", "largest_trade_usd"):
                merged[col] = merged["_ts_s"].map(agg[col])
            merged["taker_net_usd"] = merged["taker_buy_usd"] - merged["taker_sell_usd"]
            report["sources"]["cvd"] = {
                "rows": len(tmp),
                "coverage": round(float(merged["taker_buy_usd"].notna().mean()), 3),
            }

    if "aux" in include:
        aux = load_aux(symbol, start_s, end_s)
        if not aux.empty:
            cols = [c for c in ("fear_greed", "btc_dominance", "whale_tx_count",
                                "active_addresses", "social_score", "news_sentiment")
                    if c in aux.columns]
            _asof_merge(aux[cols], cols, "aux")

    if "liquidation" in include:
        liq = load_liquidation(symbol, start_s, end_s)
        if not liq.empty:
            tmp = liq.copy()
            tmp["_ts_s"] = tmp.index.view("int64") // 10**9
            tmp = tmp.sort_values("_ts_s")
            bar_starts = merged["_ts_s"].sort_values().unique()
            idx = np.searchsorted(bar_starts, tmp["_ts_s"].values, side="right") - 1
            idx = np.clip(idx, 0, len(bar_starts) - 1)
            tmp["_bar"] = bar_starts[idx]
            agg = tmp.groupby("_bar").agg(
                liquidation_long_usd=("liquidation_long_usd", "sum"),
                liquidation_short_usd=("liquidation_short_usd", "sum"),
            )
            merged["liquidation_long_usd"] = merged["_ts_s"].map(agg["liquidation_long_usd"])
            merged["liquidation_short_usd"] = merged["_ts_s"].map(agg["liquidation_short_usd"])
            report["sources"]["liquidation"] = {
                "rows": len(tmp),
                "coverage": round(float(merged["liquidation_long_usd"].notna().mean()), 3),
            }

    if "events" in include:
        ev = load_events(symbol, start_s, end_s)
        if not ev.empty:
            # bar 收盘时刻 = bar 开盘 + 周期（最后一根成形 bar 用当前时刻）
            bar_closes = merged.index + pd.Timedelta(seconds=period_s)
            bar_closes = pd.to_datetime(
                [min(be, pd.Timestamp.utcnow()) for be in bar_closes], utc=True
            )
            # 每根 bar：窗口内事件数/加权分（事件 ts ≤ bar 收盘，防泄漏）
            counts, scores = aggregate_event_features(ev, bar_closes, event_window_hours)
            merged["event_count"] = counts
            merged["event_score"] = scores
            report["sources"]["events"] = {
                "rows": len(ev),
                "coverage": round(float((merged["event_count"] > 0).mean()), 3),
            }

    merged = merged.drop(columns=["_ts_s"])
    # 覆盖率门槛：低覆盖列整列丢弃（诚实，不填假值）
    _DROP_COLS = {
        "funding": ["funding_rate"],
        "oi": ["open_interest", "mark_price"],
        "cvd": ["taker_buy_usd", "taker_sell_usd", "taker_net_usd",
                "taker_buy_count", "taker_sell_count", "largest_trade_usd"],
        "aux": ["fear_greed", "btc_dominance", "whale_tx_count",
                "active_addresses", "social_score", "news_sentiment"],
        "liquidation": ["liquidation_long_usd", "liquidation_short_usd"],
        "events": ["event_count", "event_score"],
    }
    for src_label, meta in list(report["sources"].items()):
        if meta.get("coverage", 0.0) < min_coverage:
            report["sources"][src_label]["dropped"] = True
            report["dropped"].append(src_label)
            for _col in _DROP_COLS.get(src_label, []):
                if _col in merged.columns:
                    merged = merged.drop(columns=[_col])
    # DSL 字段「liquidation」总列：仅当清算源未被丢弃时生成（否则不造假 0）
    if "liquidation_long_usd" in merged.columns or "liquidation_short_usd" in merged.columns:
        _l = merged.get("liquidation_long_usd", pd.Series(index=merged.index)).fillna(0.0)
        _s = merged.get("liquidation_short_usd", pd.Series(index=merged.index)).fillna(0.0)
        merged["liquidation"] = _l + _s
    report["elapsed_ms"] = round((time.time() - t0) * 1000, 1)
    report["bars"] = bar_n
    return merged, report


def data_availability_report(symbol: str, timeframe: str, count: int = 500,
                             exchange: Optional[str] = None) -> Dict[str, Any]:
    """T2 因子数据可用性门：返回各源覆盖率（不装配，轻量）。"""
    from backend.services.data_center import PERIOD_SECONDS

    base = load_base_klines(symbol, timeframe, count=count, exchange=exchange)
    if base.empty:
        return {"symbol": symbol, "timeframe": timeframe, "available": False}
    start_s, end_s = _range_of(base)
    end_s = end_s + PERIOD_SECONDS.get(timeframe, 3600)  # 覆盖当前成形 bar
    rep: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, "bars": len(base), "available": True}
    rep["funding"] = bool(len(load_funding_and_oi(symbol, start_s, end_s, exchange).get("funding_rate", pd.Series()).dropna()))
    rep["oi"] = bool(len(load_funding_and_oi(symbol, start_s, end_s, exchange).get("open_interest", pd.Series()).dropna()))
    rep["cvd"] = not load_cvd(symbol, start_s, end_s, exchange).empty
    rep["aux"] = not load_aux(symbol, start_s, end_s).empty
    rep["liquidation"] = not load_liquidation(symbol, start_s, end_s).empty
    rep["events"] = not load_events(symbol, start_s, end_s).empty
    return rep


def build_weekly_dataset(symbol: str, weeks: int = 104,
                         exchange: Optional[str] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """T5 长线趋势数据：1w K 线 + 周聚合（宏观序列 asof / 清算周额 / 鲸鱼周净额）。"""
    from sqlalchemy import text as _sa_text

    from backend.database.connection import MarketSessionLocal
    base = load_base_klines(symbol, "1w", count=weeks, exchange=exchange)
    report: Dict[str, Any] = {"symbol": symbol, "weeks": len(base)}
    if base.empty:
        report["error"] = "no weekly klines"
        return base, report
    start_s, end_s = _range_of(base)
    start_d = pd.Timestamp(start_s, unit="s", tz="UTC").normalize()
    end_d = pd.Timestamp(end_s, unit="s", tz="UTC").normalize()

    out = base.copy()
    try:
        with MarketSessionLocal() as db:
            liq = db.execute(
                _sa_text(
                    "SELECT ts_ms, SUM(long_usd) AS l, SUM(short_usd) AS s FROM liquidation_events "
                    "WHERE symbol=:sym AND ts_ms>=:s AND ts_ms<=:e GROUP BY ts_ms ORDER BY ts_ms"
                ),
                {"sym": symbol, "s": start_s * 1000, "e": (end_s + 7 * 86400) * 1000},
            ).mappings().all()
            whale = db.execute(
                _sa_text(
                    "SELECT timestamp, direction, amount_usd FROM whale_activities "
                    "WHERE symbol=:sym AND timestamp>=:s AND timestamp<=:e ORDER BY timestamp"
                ),
                {"sym": symbol, "s": start_d, "e": end_d + pd.Timedelta(days=7)},
            ).mappings().all()
            macro = db.execute(
                _sa_text(
                    "SELECT ts, series_id, value FROM macro_series "
                    "WHERE ts>=:s AND ts<=:e ORDER BY ts"
                ),
                {"s": start_d.date(), "e": end_d.date()},
            ).mappings().all()
        wl = pd.DataFrame(liq) if liq else pd.DataFrame(columns=["ts_ms", "l", "s"])
        if not wl.empty:
            # ts_ms 是 epoch 毫秒 → UTC（与周 K tz-aware 索引可比）
            wl["ts"] = pd.to_datetime(wl["ts_ms"], unit="ms", utc=True)
            wl = wl.set_index("ts")
            out["liq_long_week"] = out.index.map(
                lambda i: wl[(wl.index > i - pd.Timedelta(weeks=1)) & (wl.index <= i)]["l"].sum()
            )
            out["liq_short_week"] = out.index.map(
                lambda i: wl[(wl.index > i - pd.Timedelta(weeks=1)) & (wl.index <= i)]["s"].sum()
            )
        ww = pd.DataFrame(whale) if whale else pd.DataFrame(columns=["timestamp", "direction", "amount_usd"])
        if not ww.empty:
            # naive 本地时间 → UTC（与 K 线对齐）
            ww["ts"] = pd.to_datetime(ww["timestamp"])
            if getattr(ww["ts"].dt, "tz", None) is None:
                ww["ts"] = ww["ts"].dt.tz_localize(
                    datetime.now().astimezone().tzinfo
                ).dt.tz_convert("UTC")
            ww["signed"] = ww.apply(
                lambda r: float(r["amount_usd"] or 0) * (1 if r["direction"] == "buy" else -1), axis=1,
            )
            ww = ww.set_index("ts")
            out["whale_net_week"] = out.index.map(
                lambda i: ww[(ww.index > i - pd.Timedelta(weeks=1)) & (ww.index <= i)]["signed"].sum()
            )
        mm = pd.DataFrame(macro) if macro else pd.DataFrame(columns=["ts", "series_id", "value"])
        if not mm.empty:
            mm["ts"] = pd.to_datetime(mm["ts"], utc=True)
            pivot = mm.pivot_table(index="ts", columns="series_id", values="value", aggfunc="last")
            pivot = pivot.sort_index()
            if pivot.index.tz is None:
                pivot.index = pivot.index.tz_localize("UTC")  # 与周 K 索引可比
            for col in pivot.columns:
                out[f"macro_{col.lower()}"] = out.index.map(
                    lambda i: pivot[pivot.index <= i][col].iloc[-1] if (pivot.index <= i).any() else None
                )
        report["liq_weeks"] = len(wl)
        report["whale_rows"] = len(ww)
        report["macro_series"] = list(pivot.columns) if not mm.empty else []
    except Exception as exc:
        logger.warning("[DatasetBuilder] weekly 装配失败: %s", exc)
    return out, report
