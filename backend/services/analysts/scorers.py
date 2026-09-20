# -*- coding: utf-8 -*-
"""六域 scorer：把"今天真的在更新的表"换算成 [-1,1] 的一等 alpha 信号。

每个 scorer 的纪律：
  · 只读**已确认存在**的表（列名经轮129 实测核对，不猜）；
  · 拿不到数据 → 返回 `data_quality=missing` 的信号并写清 reason，**绝不返回中性 0 冒充有数据**；
  · 每条信号带 `evidence`（分项数值、样本量、来源表、最新时间），可回溯、可复核。
"""
from __future__ import annotations

import json
import logging
import math
import os
import statistics
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from .signals import (
    QUALITY_MISSING,
    QUALITY_OK,
    QUALITY_WEAK,
    AnalystSignal,
    DOMAIN_SPECS,
)

logger = logging.getLogger(__name__)

PRODUCER = "backend/services/analysts/scorers.py"


# ───────────────────────────── 工具 ─────────────────────────────

def _lookback_h() -> float:
    try:
        return max(1.0, float(os.getenv("ANALYST_SIGNAL_LOOKBACK_H", "24") or 24))
    except Exception:  # noqa: BLE001
        return 24.0


def _enabled() -> bool:
    return (os.getenv("ANALYST_SIGNALS_ENABLED", "true") or "true").strip().lower() in ("1", "true", "yes", "on")


def _base(symbol: str) -> str:
    """`BTCUSDT`/`BTC-PERP` → `BTC`（市场表与新闻表的标的写法不统一）。"""
    s = str(symbol or "").upper().strip()
    for suf in ("-PERP", "PERP", "USDT", "USDC", "BUSD", "USD"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    return s.strip("-_/") or str(symbol or "").upper()


def _ms_now() -> int:
    return int(datetime.now().timestamp() * 1000)


def _epoch_dt(ts: Any) -> Optional[datetime]:
    """epoch → datetime，**自动判别秒/毫秒**。

    [轮129 修正] 首版对 `crypto_klines.timestamp` 直接除以 1000，而该列是**秒**
    （实测 1.79e9），于是 as_of 被算成 1970-01-22。这里按量级判别，避免再踩。
    """
    try:
        v = float(ts)
    except Exception:  # noqa: BLE001
        return None
    if v > 1e11:          # 毫秒
        v /= 1000.0
    try:
        return datetime.fromtimestamp(v)
    except Exception:  # noqa: BLE001
        return None


def _q(engine, sql: str, params: Optional[Dict[str, Any]] = None):
    from sqlalchemy import text

    with engine.connect() as c:
        return c.execute(text(sql), params or {}).fetchall()


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    try:
        f = float(v)
    except Exception:  # noqa: BLE001
        return 0.0
    if f != f or math.isinf(f):
        return 0.0
    return max(lo, min(hi, f))


def _ema(vals: Sequence[float], n: int) -> Optional[float]:
    if len(vals) < n or n <= 0:
        return None
    k = 2.0 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def _rsi(vals: Sequence[float], n: int = 14) -> Optional[float]:
    if len(vals) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def _missing(domain: str, reason: str, *, symbol: str = "*",
             missing_sources: Optional[List[str]] = None,
             evidence: Optional[Dict[str, Any]] = None) -> AnalystSignal:
    return AnalystSignal(
        domain=domain, symbol=symbol, score=0.0, confidence=0.0,
        data_quality=QUALITY_MISSING, n_samples=0, producer=PRODUCER,
        evidence=evidence or {}, missing_sources=missing_sources or [], reason=reason,
    )


# ───────────────────────────── ① 宏观 ─────────────────────────────

_BIAS = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}


def score_macro(symbols: Sequence[str], *, stale_hours: float = 26.0) -> List[AnalystSignal]:
    """宏观：`analytics.strategic_reports` 最新一份（周期阶段/偏向/置信度/风险预算）。"""
    from backend.database.connection import analytics_engine, market_engine

    try:
        rows = _q(analytics_engine,
                  "select timestamp, market_cycle_phase, macro_bias, macro_confidence, "
                  "risk_budget_adjustment, recommended_direction, data_quality_score, key_insights "
                  "from strategic_reports order by timestamp desc limit 1")
    except Exception as exc:  # noqa: BLE001
        return [_missing("macro", f"strategic_reports 读取失败：{type(exc).__name__}: {str(exc)[:80]}",
                         missing_sources=["analytics.strategic_reports"])]
    if not rows:
        return [_missing("macro", "analytics.strategic_reports 为空（宏观分析师无产物）",
                         missing_sources=["analytics.strategic_reports"])]
    ts, phase, bias, conf, risk_budget, rec_dir, quality, insights = rows[0]
    age_h = None
    if isinstance(ts, datetime):
        age_h = (datetime.now() - ts).total_seconds() / 3600.0
    if age_h is not None and age_h > stale_hours:
        return [_missing("macro", f"战略报告 {age_h:.1f}h 未更新（阈值 {stale_hours}h）",
                         missing_sources=["analytics.strategic_reports"],
                         evidence={"report_ts": str(ts)[:19], "age_h": round(age_h, 1)})]

    b = _BIAS.get(str(bias or "").strip().lower(), 0.0)
    cf = _clamp(float(conf or 0.0), 0.0, 1.0)
    rb = float(risk_budget or 1.0)
    score = b * cf
    if rb < 1.0:                      # 风险预算下调 = 同一偏向下的加码收缩（不翻转方向）
        score *= max(0.3, rb)

    # 利率环境（DFF）：上行 = 收紧 = 风险资产逆风（只作为温和惩罚项）
    rate_ev: Dict[str, Any] = {}
    try:
        rr = _q(market_engine,
                "select ts, value from macro_series where series_id='DFF' order by ts desc limit 120")
        if rr:
            latest = float(rr[0][1])
            old = float(rr[min(len(rr) - 1, 90)][1])
            delta = latest - old
            rate_ev = {"DFF_latest": latest, "DFF_90d_delta": round(delta, 3)}
            score += -0.15 * _clamp(delta / 0.5)
    except Exception as exc:  # noqa: BLE001
        rate_ev = {"DFF_error": f"{type(exc).__name__}: {str(exc)[:60]}"}

    try:
        ins = json.loads(insights) if isinstance(insights, str) and insights.strip().startswith("[") else insights
    except Exception:  # noqa: BLE001
        ins = insights

    return [AnalystSignal(
        domain="macro", symbol="*", score=_clamp(score), confidence=cf,
        data_quality=QUALITY_OK, n_samples=1, producer=PRODUCER,
        as_of=str(ts)[:19],
        evidence={"cycle_phase": phase, "macro_bias": bias, "confidence": cf,
                  "risk_budget_adjustment": rb, "recommended_direction": rec_dir,
                  "data_quality_score": quality,
                  "key_insights": (ins[:2] if isinstance(ins, list) else str(ins)[:160]),
                  **rate_ev},
    )]


# ───────────────────────────── ② 舆情 ─────────────────────────────

def _news_rows(hours: float, categories: Optional[Sequence[str]] = None):
    from backend.database.connection import market_engine

    since = datetime.now() - timedelta(hours=hours)
    sql = ("select created_at, title, ai_summary, impact_direction, impact_strength, "
           "affected_symbols, event_category from news_events where created_at >= :since")
    params: Dict[str, Any] = {"since": since}
    if categories:
        sql += " and event_category = any(:cats)"
        params["cats"] = list(categories)
    sql += " order by created_at desc limit 2000"
    return _q(market_engine, sql, params)


def _sym_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [_base(x) for x in raw if str(x).strip()]
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(v, list):
            return [_base(x) for x in v if str(x).strip()]
    except Exception:  # noqa: BLE001
        pass
    return [_base(str(raw))] if str(raw).strip() else []


def _news_score(rows, symbols: Sequence[str], *, domain: str, weak: bool = False,
                missing_sources: Optional[List[str]] = None) -> List[AnalystSignal]:
    """把新闻行按标的聚合成情绪分：Σ(方向×强度)/Σ(强度)。"""
    per: Dict[str, Dict[str, Any]] = {}
    for created_at, title, summary, direction, strength, affected, cat in rows:
        syms = _sym_list(affected) or ["*"]
        w = float(max(1, int(strength or 1)))
        d = float(direction or 0.0)
        for s in syms:
            b = per.setdefault(s, {"num": 0.0, "den": 0.0, "n": 0, "pos": 0, "neg": 0,
                                   "top": None, "newest": str(created_at or "")[:19]})
            b["num"] += d * w
            b["den"] += w
            b["n"] += 1
            b["pos"] += 1 if d > 0.15 else 0
            b["neg"] += 1 if d < -0.15 else 0
            # as_of 必须是**用到的最新数据时间**，不是"权重最大那条"的时间（首版语义混淆，已修）
            ts_txt = str(created_at or "")[:19]
            if ts_txt and (not b["newest"] or ts_txt > b["newest"]):
                b["newest"] = ts_txt
            if b["top"] is None or abs(d) * w > abs(b["top"][2] * b["top"][3]):
                b["top"] = (str(title or "")[:110], str(summary or "")[:150], d, w, ts_txt, cat)

    wanted = {_base(s) for s in symbols} | {"*"}
    out: List[AnalystSignal] = []
    for sym, b in per.items():
        if sym not in wanted and sym != "*":
            continue
        score = b["num"] / b["den"] if b["den"] else 0.0
        top = b["top"] or ("", "", 0.0, 1.0, "", "")
        out.append(AnalystSignal(
            domain=domain, symbol=sym, score=_clamp(score),
            confidence=_clamp(min(1.0, b["n"] / 3.0), 0.0, 1.0),
            data_quality=QUALITY_WEAK if weak else QUALITY_OK,
            n_samples=b["n"], producer=PRODUCER, as_of=b["newest"] or top[4],
            evidence={"n": b["n"], "pos": b["pos"], "neg": b["neg"],
                      "weighted_dir": round(score, 3),
                      "top_title": top[0], "top_summary": top[1], "top_category": top[5]},
            missing_sources=list(missing_sources or []),
            reason=("数据源可用但有已知缺口：" + "；".join(missing_sources)) if (weak and missing_sources) else "",
        ))
    if not out:
        return [_missing(domain, f"近 {_lookback_h():.0f}h 无{f'该类' if domain == 'fundamental' else ''}已标注新闻",
                         missing_sources=["market.news_events"])]
    return out


def score_sentiment(symbols: Sequence[str]) -> List[AnalystSignal]:
    """舆情：新闻情绪（ai_summary + 方向 + 强度 + affected_symbols）。"""
    try:
        rows = _news_rows(_lookback_h())
    except Exception as exc:  # noqa: BLE001
        return [_missing("sentiment", f"news_events 读取失败：{type(exc).__name__}: {str(exc)[:80]}",
                         missing_sources=["market.news_events"])]
    return _news_score(rows, symbols, domain="sentiment")


# ───────────────────────────── ③ 基本面 ─────────────────────────────

_FUND_CATS = ("exchange", "regulatory", "listing", "macro")


def score_fundamental(symbols: Sequence[str]) -> List[AnalystSignal]:
    """基本面：ETF/监管/上所类事件 + 利率环境。**已知缺口显式标注**（无 ETF 净流入结构表/链上表）。"""
    gaps = list(DOMAIN_SPECS["fundamental"]["known_gaps"])
    try:
        rows = _news_rows(72.0, categories=_FUND_CATS)
    except Exception as exc:  # noqa: BLE001
        return [_missing("fundamental", f"news_events 读取失败：{type(exc).__name__}: {str(exc)[:80]}",
                         missing_sources=["market.news_events"])]
    sigs = _news_score(rows, symbols, domain="fundamental", weak=True, missing_sources=gaps)
    for s in sigs:
        s.evidence["lookback_h"] = 72
        s.evidence["categories"] = list(_FUND_CATS)
    return sigs


# ───────────────────────────── ④ 量价 ─────────────────────────────

def _klines(symbol: str, period: str, limit: int = 220):
    from backend.database.connection import market_engine

    base = _base(symbol)
    rows = _q(market_engine,
              "select timestamp, high_price, low_price, close_price from crypto_klines "
              "where (symbol = :s or symbol = :raw) and period = :p "
              "order by timestamp desc limit :n",
              {"s": base, "raw": f"{base}USDT", "p": period, "n": limit})
    return list(reversed(rows))


def score_technical(symbols: Sequence[str]) -> List[AnalystSignal]:
    """量价：EMA 结构 + RSI14 + 24h 区间位置 + 距 EMA200（多周期）。"""
    out: List[AnalystSignal] = []
    for sym in symbols:
        base = _base(sym)
        try:
            k1h, k4h, k1d = _klines(base, "1h", 168), _klines(base, "4h", 220), _klines(base, "1d", 220)
        except Exception as exc:  # noqa: BLE001
            out.append(_missing("technical", f"crypto_klines 读取失败：{type(exc).__name__}: {str(exc)[:70]}",
                                symbol=base, missing_sources=["market.crypto_klines"]))
            continue
        c1h = [float(r[3]) for r in k1h if r[3] is not None]
        c1d = [float(r[3]) for r in k1d if r[3] is not None]
        if len(c1h) < 30 and len(c1d) < 30:
            out.append(_missing("technical", "1h/1d K 线不足 30 根", symbol=base,
                                missing_sources=["market.crypto_klines"]))
            continue

        ev: Dict[str, Any] = {"bars_1h": len(c1h), "bars_4h": len(k4h), "bars_1d": len(c1d)}
        score = 0.0
        # ① 1h 趋势（EMA9/21）
        if len(c1h) >= 21:
            e9, e21 = _ema(c1h, 9), _ema(c1h, 21)
            if e9 and e21:
                tr = 1.0 if e9 > e21 else -1.0
                ev["ema9_1h"], ev["ema21_1h"], ev["trend_1h"] = round(e9, 4), round(e21, 4), tr
                score += 0.35 * tr
        # ② 24h 区间位置（动量）
        if len(k1h) >= 24:
            hi = max(float(r[1]) for r in k1h[-24:] if r[1] is not None)
            lo = min(float(r[2]) for r in k1h[-24:] if r[2] is not None)
            if hi > lo:
                pos24 = (c1h[-1] - lo) / (hi - lo) * 100.0
                ev["pos24_pct"] = round(pos24, 1)
                score += 0.25 * _clamp((pos24 - 50.0) / 50.0)
        # ③ 日线趋势位置（距 EMA200）
        if len(c1d) >= 200:
            e200 = _ema(c1d, 200)
            if e200:
                dist = (c1d[-1] / e200 - 1.0) * 100.0
                ev["dist_ema200_1d_pct"] = round(dist, 2)
                score += 0.20 * _clamp(dist / 25.0)
        # ④ 超买超卖（反向微调，避免在极端处继续追）
        r14 = _rsi(c1h) if len(c1h) >= 15 else None
        if r14 is not None:
            ev["rsi14_1h"] = round(r14, 1)
            score += -0.20 * _clamp((r14 - 50.0) / 30.0)
        if len(c1d) >= 2:
            ev["ret_1d_pct"] = round((c1d[-1] / c1d[-2] - 1) * 100.0, 2)

        conf = _clamp(min(1.0, len(c1h) / 48.0) * (1.0 if "rsi14_1h" in ev else 0.6), 0.0, 1.0)
        out.append(AnalystSignal(
            domain="technical", symbol=base, score=_clamp(score), confidence=conf,
            data_quality=QUALITY_OK, n_samples=len(c1h), producer=PRODUCER,
            as_of=(_epoch_dt(k1h[-1][0]).strftime("%Y-%m-%d %H:%M:%S") if (k1h and k1h[-1][0] and _epoch_dt(k1h[-1][0])) else ""),
            evidence=ev,
        ))
    return out


# ───────────────────────────── ⑤ 资金流 ─────────────────────────────

def score_flow(symbols: Sequence[str]) -> List[AnalystSignal]:
    """资金流：拥挤度（费率/多空比取反向）+ 清算（取反向）+ 鲸鱼（顺向）+ OI 变化（证据）。"""
    from backend.database.connection import market_engine

    hours = _lookback_h()
    since_ms = _ms_now() - int(hours * 3600 * 1000)
    out: List[AnalystSignal] = []
    for sym in symbols:
        base = _base(sym)
        ev: Dict[str, Any] = {}
        score = 0.0
        parts = 0

        # ① 资金费率中位数（8h 口径近似：直接取原始费率中位数）
        try:
            rows = _q(market_engine,
                      "select exchange, funding_rate from perp_funding where symbol = :s and timestamp >= :t",
                      {"s": base, "t": since_ms})
            rates = [float(r[1]) for r in rows if r[1] is not None]
            if rates:
                med = statistics.median(rates)
                ev["funding_median_pct"] = round(med * 100, 4)
                ev["funding_rows"] = len(rates)        # 行数（跨交易所多行），不是交易所个数
                score += -0.40 * _clamp(med / 0.001)          # 费率越正 → 多头越拥挤 → 反向偏空
                parts += 1
        except Exception as exc:  # noqa: BLE001
            ev["funding_error"] = f"{type(exc).__name__}"

        # ② 多空比（拥挤 → 反向）
        try:
            rows = _q(market_engine,
                      "select global_ls_ratio, open_interest_value, ts_ms from position_structure "
                      "where symbol = :s and ts_ms >= :t order by ts_ms desc limit 1",
                      {"s": base, "t": since_ms})
            if rows and rows[0][0] is not None:
                ls = float(rows[0][0])
                ev["global_ls_ratio"] = round(ls, 3)
                score += -0.30 * _clamp((ls - 1.0) / 0.5)
                parts += 1
            if rows and rows[0][1] is not None:
                ev["oi_usd"] = float(rows[0][1])
        except Exception as exc:  # noqa: BLE001
            ev["position_error"] = f"{type(exc).__name__}"

        # ③ 24h 清算（多头被清算多 → 反向偏多）
        try:
            rows = _q(market_engine,
                      "select side, sum(notional_usd) from liquidation_ticks "
                      "where symbol = :s and ts_ms >= :t group by side",
                      {"s": base, "t": _ms_now() - 24 * 3600 * 1000})
            liq = {str(r[0]).upper(): float(r[1] or 0) for r in rows}
            # side=BUY 表示空头被清算（买回），SELL 表示多头被清算
            short_rekt, long_rekt = liq.get("BUY", 0.0), liq.get("SELL", 0.0)
            if short_rekt or long_rekt:
                ev["liq_short_usd"], ev["liq_long_usd"] = short_rekt, long_rekt
                score += 0.30 * _clamp((long_rekt - short_rekt) / max(1.0, long_rekt + short_rekt))
                parts += 1
        except Exception as exc:  # noqa: BLE001
            ev["liq_error"] = f"{type(exc).__name__}"

        # ④ 鲸鱼（信号方向 × 金额权重，顺向）
        try:
            rows = _q(market_engine,
                      "select signal_direction, amount_usd from whale_activities "
                      "where symbol = :s and created_at >= :t and signal_direction is not null",
                      {"s": base, "t": datetime.now() - timedelta(hours=48)})
            num = den = 0.0
            for sd, amt in rows:
                w = min(1.0, float(amt or 0) / 1e6)
                num += float(sd) * w
                den += w
            if den > 0:
                ev["whale_weighted_dir"] = round(num / den, 3)
                ev["whale_n"] = len(rows)
                score += 0.25 * _clamp(num / den)
                parts += 1
        except Exception as exc:  # noqa: BLE001
            ev["whale_error"] = f"{type(exc).__name__}"

        if parts == 0:
            out.append(_missing("flow", f"近 {hours:.0f}h 四个资金流来源均无 {base} 数据",
                                symbol=base,
                                missing_sources=DOMAIN_SPECS["flow"]["tables"], evidence=ev))
            continue
        out.append(AnalystSignal(
            domain="flow", symbol=base, score=_clamp(score),
            confidence=_clamp(parts / 4.0, 0.0, 1.0), data_quality=QUALITY_OK,
            n_samples=parts, producer=PRODUCER,
            as_of=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), evidence=ev,
        ))
    return out


# ───────────────────────────── ⑥ K线深度 ─────────────────────────────

_BULL_KW = ("看多", "偏多", "做多", "上涨", "突破", "bullish", "long")
_BEAR_KW = ("看空", "偏空", "做空", "下跌", "跌破", "bearish", "short")


def score_kline_deep(symbols: Sequence[str]) -> List[AnalystSignal]:
    """K线深度：消费 `analytics.kline_ai_analysis_logs`。

    **现状（轮129 实测）**：该表 0 行 —— KlineAnalyst 每 24h 仍在烧 ~222 次 LLM 调用，
    但结果从未落库。本 scorer 如实返回 `missing`，把这个"烧钱无产物"暴露在契约里，
    而不是假装有个信号。
    """
    from backend.database.connection import analytics_engine

    hours = _lookback_h() * 3      # 深度分析是低频产物，放宽到 72h
    try:
        rows = _q(analytics_engine,
                  "select symbol, analysis_result, created_at from kline_ai_analysis_logs "
                  "where created_at >= :t order by created_at desc limit 200",
                  {"t": datetime.now() - timedelta(hours=hours)})
    except Exception as exc:  # noqa: BLE001
        return [_missing("kline_deep", f"kline_ai_analysis_logs 读取失败：{type(exc).__name__}: {str(exc)[:80]}",
                         missing_sources=["analytics.kline_ai_analysis_logs"])]
    if not rows:
        return [_missing(
            "kline_deep",
            f"近 {hours:.0f}h 无 K 线深度分析产物（kline_ai_analysis_logs 0 行）"
            "：KlineAnalyst 仍在被调用但不落库 ⇒ 该域信号当前不可用",
            missing_sources=["analytics.kline_ai_analysis_logs"],
            evidence={"llm_calls_observed_24h": 222,
                      "where_wasted": "full_auto_trading_service._warmup_analyst_reports 只做内存预热"},
        )]

    out: List[AnalystSignal] = []
    for sym, result, created in rows:
        txt = str(result or "")
        low = txt.lower()
        pos = sum(low.count(k.lower()) for k in _BULL_KW)
        neg = sum(low.count(k.lower()) for k in _BEAR_KW)
        if not txt.strip():
            continue
        score = _clamp((pos - neg) / max(1, pos + neg))
        out.append(AnalystSignal(
            domain="kline_deep", symbol=_base(sym), score=score,
            confidence=_clamp(min(1.0, len(txt) / 800.0), 0.0, 1.0),
            data_quality=QUALITY_WEAK, n_samples=1, producer=PRODUCER,
            as_of=str(created)[:19],
            evidence={"chars": len(txt), "bull_kw": pos, "bear_kw": neg,
                      "excerpt": txt[:200],
                      "note": "关键词口径为临时实现（产物为自由文本），落库结构化后应替换"},
            missing_sources=["结构化字段（direction/score）未落库，当前用关键词近似"],
            reason="产物为自由文本，方向判定为关键词近似",
        ))
    if not out:
        return [_missing("kline_deep", "有产物行但内容为空", missing_sources=["analytics.kline_ai_analysis_logs"])]
    return out


# ───────────────────────────── 汇总 ─────────────────────────────

SCORERS = {
    "macro": score_macro,
    "fundamental": score_fundamental,
    "technical": score_technical,
    "flow": score_flow,
    "sentiment": score_sentiment,
    "kline_deep": score_kline_deep,
}


def score_all(symbols: Sequence[str]) -> List[AnalystSignal]:
    """按契约顺序跑满六域。**任一域抛异常都降级为 missing 信号**（不静默、不中断其它域）。"""
    syms = [_base(s) for s in symbols if str(s).strip()]
    out: List[AnalystSignal] = []
    for domain, fn in SCORERS.items():
        try:
            out.extend(fn(syms))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Analysts] %s 域计算异常，降级为 missing: %s", domain, exc)
            out.append(_missing(domain, f"{type(exc).__name__}: {str(exc)[:120]}",
                                missing_sources=DOMAIN_SPECS[domain]["tables"]))
    return out
