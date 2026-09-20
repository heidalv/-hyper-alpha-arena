# -*- coding: utf-8 -*-
"""ContextPack：双模型深度分析的统一输入（五层 + 数据截止时间 + hash）。

  market       多周期 K 线摘要（1d/4h：收益、实现波动、ATR%、RSI、EMA 结构、BTC>EMA200）、regime、相关性（30d 日收益）
  flows        资金面：funding（8h 归一）、OI 与多空比（position_structure）、24h 清算（liquidation_ticks）、
               市场事件（market_events，24h，severity ≥ 2）、新闻密度/正负比（代理社交情绪）
  positions    各账户权益、TradingState、RiskEngine 状态、每个持仓的名义/杠杆/浮盈 R/距止损/持仓时长
  performance  edge_ledger 最新快照（分 tier 的 n/net/PF/胜率/费用）、signal_ledger 信号源统计、Agent 可信度矩阵
  config       config_hash（runtime_tuning 全量 + 关键开关）与生效开关子集

每一层取数失败只记 `_errors`（不抛、不造数）；build() 输出的 pack 可直接 JSON 化落 analysis_runs.context_pack，
to_prompt_text() 会按 token 预算逐级裁剪（先 events 明细 → 相关性对 → 持仓明细 → 币种数）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.services.analysis.quota_guard import estimate_tokens

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX"]
CONFIG_FLAGS = (
    "LIVE_KILL_SWITCH", "RISK_ENGINE_V3_ENABLED", "TREND_ENGINE_ENABLED", "SCALP_SHADOW_MODE", "SCALP_SHADOW_ENABLED",
    "LLM_EXIT_ADVISORY_MODE", "BUCKET_WEIGHTS", "ANALYSIS_PRIMARY_TRANSPORTS", "ANALYSIS_ARBITER_TRANSPORT",
    "EVENT_COLLECTORS_HOST", "UNIFIED_DATA_POOL_MARKET_EVENTS", "MULTI_VENUE_FUNDING_SYMBOLS",
    "LIQUIDATION_STREAM_SOURCES", "FEE_BUDGET_DAILY_PCT", "MAX_LEVERAGE", "VOL_TARGET_ANNUAL",
)


def _r(v: Any, n: int = 4) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, n)
    except Exception:
        return None


def universe() -> List[str]:
    raw = (os.getenv("ANALYSIS_UNIVERSE") or "").strip()
    if raw:
        syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
        if syms:
            return syms[:24]
    return list(DEFAULT_UNIVERSE)


def _base(symbol: str) -> str:
    from backend.services.analysis.ledgers import _kline_base

    return _kline_base(symbol)


@dataclass
class ContextPack:
    task: str
    data_cutoff_ms: int
    layers: Dict[str, Any]
    errors: List[str] = field(default_factory=list)
    built_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    @property
    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.layers, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task, "data_cutoff_ms": self.data_cutoff_ms, "built_ms": self.built_ms,
            "hash": self.hash, "layers": self.layers, "errors": self.errors,
        }

    def to_prompt_text(self, max_tokens: int = 40000) -> str:
        """渲染为模型输入；超预算按重要性逐级裁剪。

        [2026-09-07 前缀缓存] 易变字段（data_cutoff_ms/hash/errors）移到**末尾**。
        此前它们在头部，每次调用前缀都从第一个 token 开始发散，OpenAI/DeepSeek
        自动前缀缓存命中率为 0（对标 TradingAgents issue #750：稳定前缀后命中
        率提升 4.3 倍）。layers 序列化保持稳定 key 顺序，让相同数据前缀可命中。
        """
        layers = json.loads(json.dumps(self.layers, ensure_ascii=False, default=str))

        def _trim_factor_route(L: Dict[str, Any]) -> None:
            """[轮107] 因子层先砍路由明细（保留 action/score 概览）。"""
            for v in (L.get("factors", {}).get("symbols") or {}).values():
                if isinstance(v, dict):
                    v.pop("route", None)

        def _trim_factor_symbols(L: Dict[str, Any]) -> None:
            """再只留第一个币的因子证据。"""
            fs = L.get("factors")
            if isinstance(fs, dict):
                fs["symbols"] = dict(list((fs.get("symbols") or {}).items())[:1])

        def _trim_analyst_symbols(L: Dict[str, Any]) -> None:
            """[轮129] 分析师信号层超预算时先砍币数（保留全局宏观 + 前 3 个币）。"""
            an = L.get("analysts")
            if isinstance(an, dict):
                an["symbols"] = dict(list((an.get("symbols") or {}).items())[:3])

        trims = [
            lambda L: L.get("flows", {}).update({"events": (L.get("flows", {}).get("events") or [])[:12]}),
            lambda L: L.get("market", {}).pop("correlation_pairs", None),
            lambda L: L.get("positions", {}).update({"open": (L.get("positions", {}).get("open") or [])[:10]}),
            lambda L: L.get("flows", {}).update({"events": (L.get("flows", {}).get("events") or [])[:4]}),
            lambda L: L.get("performance", {}).pop("signal_sources", None),
            _trim_factor_route,
            _trim_factor_symbols,
            _trim_analyst_symbols,
            lambda L: L.get("market", {}).update({"symbols": dict(list((L.get("market", {}).get("symbols") or {}).items())[:6])}),
            lambda L: L.get("positions", {}).update({"open": []}),
        ]

        def _render(layers_obj: Dict[str, Any]) -> str:
            body = json.dumps(layers_obj, ensure_ascii=False, separators=(",", ":"), default=str)
            tail = (
                f"\n__meta__：数据截止（UTC ms）={self.data_cutoff_ms}；"
                f"pack hash={self.hash[:16]}；"
                f"缺失层/错误={'; '.join(self.errors) if self.errors else '无'}"
            )
            return body + tail

        text = _render(layers)
        i = 0
        while estimate_tokens(text) > max_tokens and i < len(trims):
            try:
                trims[i](layers)
            except Exception:
                pass
            i += 1
            text = _render(layers)
        return text


# --------------------------------------------------------------------------- market layer
def _klines(symbol: str, tf: str, count: int) -> list:
    try:
        from backend.services.agent_deep_context import _fetch_klines_for_prompt

        return _fetch_klines_for_prompt(symbol, tf, count) or []
    except Exception as exc:
        logger.debug("[context_pack] klines %s/%s 失败: %s", symbol, tf, exc)
        return []


def _series(kl: list, key: str = "close") -> List[float]:
    out = []
    for r in kl:
        try:
            out.append(float(r[key]))
        except Exception:
            continue
    return out


def _ema(vals: List[float], span: int) -> Optional[float]:
    if len(vals) < span:
        return None
    k = 2.0 / (span + 1)
    e = sum(vals[:span]) / span
    for v in vals[span:]:
        e = v * k + e * (1 - k)
    return e


def _rsi(vals: List[float], n: int = 14) -> Optional[float]:
    if len(vals) < n + 1:
        return None
    gains, losses = [], []
    for a, b in zip(vals[:-1], vals[1:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + l) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def _atr_pct(kl: list, n: int = 14) -> Optional[float]:
    if len(kl) < n + 1:
        return None
    trs = []
    prev_close = None
    for r in kl:
        try:
            h, l, c = float(r["high"]), float(r["low"]), float(r["close"])
        except Exception:
            continue
        tr = (h - l) if prev_close is None else max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    if len(trs) < n or not prev_close:
        return None
    return sum(trs[-n:]) / n / prev_close * 100.0


# ── [轮138 2026-09-20] 主脑预检长期报缺的五项指标（都能直接从**已在取的 K 线**算出）──
# 实测（300 次 refresh）：missing 恒为 rsi_4h(102) / macd_hist_1h(101) / vol_ratio_1h(104)
# / adx_1d(107) / trend_1w(102) / fear_greed(102)；主脑每轮都在"缺一大片证据"下判断
# ⇒ 85% 的 refresh 是 accepted=False dir=neutral（不成交的最后一环）。
# 这五项不需要新数据源：4h/1h/1d K 线本来就在取，1w 也在库里（crypto_klines period='1w'）。
def _macd_hist(vals: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[float]:
    if len(vals) < slow + signal:
        return None
    ef, es = _ema(vals, fast), _ema(vals, slow)
    if ef is None or es is None:
        return None
    # 用滚动序列算 signal 线（避免只看最后一点的假值）
    macd_series = []
    for i in range(slow, len(vals) + 1):
        a, b = _ema(vals[:i], fast), _ema(vals[:i], slow)
        if a is not None and b is not None:
            macd_series.append(a - b)
    if len(macd_series) < signal:
        return None
    sig = _ema(macd_series, signal)
    if sig is None:
        return None
    return macd_series[-1] - sig


def _vol_ratio_1h(kl: list, recent: int = 3, base: int = 20) -> Optional[float]:
    vols = []
    for r in kl:
        try:
            v = float(r.get("volume") or 0)
        except Exception:
            continue
        if v > 0:
            vols.append(v)
    if len(vols) < base:
        return None
    avg = sum(vols[-base:]) / base
    rec = sum(vols[-recent:]) / max(1, min(recent, len(vols)))
    return (rec / avg) if avg > 0 else None


def _adx(kl: list, n: int = 14) -> Optional[float]:
    """简化 ADX(14)：用 DI 差与和推导 DX，再做 Wilder 平滑。"""
    if len(kl) < n * 2:
        return None
    plus_dm, minus_dm, trs = [], [], []
    prev_h = prev_l = prev_c = None
    for r in kl:
        try:
            h, l, c = float(r["high"]), float(r["low"]), float(r["close"])
        except Exception:
            continue
        if prev_h is not None:
            up, dn = h - prev_h, prev_l - l
            plus_dm.append(up if (up > dn and up > 0) else 0.0)
            minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
            trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
        prev_h, prev_l, prev_c = h, l, c
    if len(trs) < n:
        return None
    dxs = []
    for i in range(n, len(trs) + 1):
        tr_s = sum(trs[i - n:i])
        if tr_s <= 0:
            continue
        pdi = 100.0 * sum(plus_dm[i - n:i]) / tr_s
        mdi = 100.0 * sum(minus_dm[i - n:i]) / tr_s
        denom = pdi + mdi
        dxs.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)
    if not dxs:
        return None
    return sum(dxs[-n:]) / min(n, len(dxs))


def _log_returns(vals: List[float]) -> List[float]:
    out = []
    for a, b in zip(vals[:-1], vals[1:]):
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def _std(xs: List[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _corr(x: List[float], y: List[float]) -> Optional[float]:
    n = min(len(x), len(y))
    if n < 10:
        return None
    x, y = x[-n:], y[-n:]
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def _regime(symbol: str, kl_4h: list) -> Dict[str, Any]:
    try:
        import pandas as pd
        from backend.services.market_regime import MarketRegimeClassifier

        if len(kl_4h) >= 50:
            cls = MarketRegimeClassifier().classify(pd.DataFrame(kl_4h))
            regime = cls.regime.value if hasattr(cls.regime, "value") else str(cls.regime)
            return {"regime": regime, "confidence": _r(cls.confidence, 2)}
    except Exception as exc:
        logger.debug("[context_pack] regime %s 失败: %s", symbol, exc)
    return {}


# [2026-09-08] market 层 60s 缓存：K线指标计算是 CPU 大头（每币 1d/4h 加载+指标），
# 主脑三车道连续刷新导致后端单核 100%、API 被饿死（实测论题端点 16s）。
# 1h/4h 结构分析不需要秒级新鲜，60s 缓存大幅降 CPU 且不影响决策正确性。
_MARKET_LAYER_CACHE: Dict[tuple, tuple] = {}  # (tuple(symbols)) -> (ts, result)
_MARKET_LAYER_CACHE_TTL = 60.0


def build_market_layer(symbols: Sequence[str], errors: List[str]) -> Dict[str, Any]:
    _key = tuple(str(s).upper() for s in (symbols or []))
    _now = time.time()
    _hit = _MARKET_LAYER_CACHE.get(_key)
    if _hit and (_now - _hit[0]) < _MARKET_LAYER_CACHE_TTL:
        # 命中缓存：errors 由首次构建写入，命中时不再重复
        return _hit[1]
    out: Dict[str, Any] = {"symbols": {}, "as_of_ms": None}
    daily_returns: Dict[str, List[float]] = {}
    latest_ts = 0
    # ── [轮147 2026-09-21] 接上 **fear_greed 等链上/宏观辅助序列** ──────────────────
    # 为什么必须接：主脑预检里 `fear_greed` 此前**恒缺**（24h 102 次），是最后一个长期缺项
    # （其余五项已在轮138 由 K 线派生补齐）。实测源：`market.symbol_aux_timeseries`
    # （188,810 行、35 币近 1h 有数据、fear_greed=71），另有 btc_dominance/tvl/active_addresses。
    # 开关：CTX_FEAR_GREED_ENABLED（默认 true）；取不到就**留空**（预检继续如实记缺，不编数）。
    _aux: Dict[str, Dict[str, Any]] = {}
    if (os.getenv("CTX_FEAR_GREED_ENABLED", "true") or "true").strip().lower() in (
            "1", "true", "yes", "on"):
        try:
            _adb = _market_db()
            try:
                _aux_rows = _rows(
                    _adb,
                    "SELECT DISTINCT ON (symbol) symbol, fear_greed, btc_dominance, active_addresses, "
                    "news_sentiment, timestamp_ms FROM symbol_aux_timeseries "
                    "WHERE timestamp_ms >= :since ORDER BY symbol, timestamp_ms DESC",
                    {"since": int(time.time() * 1000) - 6 * 3600 * 1000},
                    errors, "market.aux_series",
                )
            finally:
                _adb.close()
            for _r_aux in _aux_rows or []:
                _aux[_base(_r_aux["symbol"])] = _r_aux
        except Exception as _aux_err:  # noqa: BLE001
            errors.append(f"market.aux_series: {str(_aux_err)[:90]}")
    for sym in symbols:
        kl_1d = _klines(sym, "1d", 230)
        kl_4h = _klines(sym, "4h", 80)
        # [2026-09-09 第十八轮] 主脑 market 层补 1h：此前只有 1d/4h，
        # 而近三轮全部有效修复（learned 准入 / 位置闸 / regime 门）用的正是
        # 1h 派生特征（24h 涨跌 chg24、24h 区间位置 pos24）——LLM 主脑却看不到。
        # 数据依据：近 30 天 184 笔实际 P&L，`up chg∈[3,6) ∪ chop pos≥60&chg≥2`
        # 放行集 +0.406%/笔（多头 +1.722%、胜率 0.857），不拦 -0.406%/笔。
        kl_1h = _klines(sym, "1h", 60)
        if len(kl_1d) < 10 and len(kl_4h) < 10:
            errors.append(f"market:{sym}: 无 K 线")
            continue
        closes_1d = _series(kl_1d)
        closes_4h = _series(kl_4h)
        last = closes_1d[-1] if closes_1d else (closes_4h[-1] if closes_4h else None)
        try:
            ts = int(kl_1d[-1].get("timestamp") or 0) if kl_1d else int(kl_4h[-1].get("timestamp") or 0)
            ts = ts * 1000 if ts and ts < 10_000_000_000 else ts
            latest_ts = max(latest_ts, ts)
        except Exception:
            pass
        lr = _log_returns(closes_1d[-31:]) if len(closes_1d) >= 31 else _log_returns(closes_1d)
        rv30 = _std(lr)
        ema200 = _ema(closes_1d, 200)
        ema9_4h, ema21_4h, ema50_4h = _ema(closes_4h, 9), _ema(closes_4h, 21), _ema(closes_4h, 50)
        ema_trend = None
        if ema9_4h and ema21_4h and ema50_4h:
            ema_trend = "bullish" if ema9_4h > ema21_4h > ema50_4h else ("bearish" if ema9_4h < ema21_4h < ema50_4h else "mixed")
        d: Dict[str, Any] = {
            "last": _r(last, 6),
            "ret_1d_pct": _r((closes_1d[-1] / closes_1d[-2] - 1) * 100, 2) if len(closes_1d) >= 2 else None,
            "ret_7d_pct": _r((closes_1d[-1] / closes_1d[-8] - 1) * 100, 2) if len(closes_1d) >= 8 else None,
            "ret_30d_pct": _r((closes_1d[-1] / closes_1d[-31] - 1) * 100, 2) if len(closes_1d) >= 31 else None,
            "rv30_annual_pct": _r(rv30 * math.sqrt(365) * 100, 1) if rv30 else None,
            "atr14_1d_pct": _r(_atr_pct(kl_1d), 2),
            "rsi14_1d": _r(_rsi(closes_1d), 1),
            "ema_trend_4h": ema_trend,
            "above_ema200_1d": (last > ema200) if (ema200 and last) else None,
            "dist_ema200_pct": _r((last / ema200 - 1) * 100, 2) if (ema200 and last) else None,
        }
        # [2026-09-09 第十八轮] 1h 派生择时读数（与闸门同源口径）
        if len(kl_1h) >= 25:
            closes_1h = _series(kl_1h)
            highs_1h = _series(kl_1h, "high")
            lows_1h = _series(kl_1h, "low")
            px1h = closes_1h[-1]
            hi24 = max(highs_1h[-24:]) if len(highs_1h) >= 24 else None
            lo24 = min(lows_1h[-24:]) if len(lows_1h) >= 24 else None
            pos24 = (
                _r((px1h - lo24) / (hi24 - lo24) * 100, 1)
                if (hi24 is not None and lo24 is not None and hi24 > lo24) else None
            )
            ema9_1h, ema21_1h = _ema(closes_1h, 9), _ema(closes_1h, 21)
            d.update({
                "ret_1h_pct": _r((closes_1h[-1] / closes_1h[-2] - 1) * 100, 2) if len(closes_1h) >= 2 else None,
                "ret_24h_pct": _r((px1h / closes_1h[-25] - 1) * 100, 2) if closes_1h[-25] > 0 else None,
                "pos24_pct": pos24,
                "range_24h_high": _r(hi24, 6),
                "range_24h_low": _r(lo24, 6),
                "rsi14_1h": _r(_rsi(closes_1h), 1),
                "atr14_1h_pct": _r(_atr_pct(kl_1h), 2),
                "ema_trend_1h": (
                    "bullish" if (ema9_1h and ema21_1h and ema9_1h > ema21_1h)
                    else ("bearish" if (ema9_1h and ema21_1h and ema9_1h < ema21_1h) else None)
                ),
            })
        d.update(_regime(sym, kl_4h))
        # ── [轮138] 补齐主脑预检长期报缺的五项（全部由已取 K 线派生，不新增数据源）──
        if len(kl_4h) >= 20:
            _c4h = _series(kl_4h)
            d["rsi14_4h"] = _r(_rsi(_c4h), 1)
            # [轮148] 模型点名要「4h 精确指标数值」：4h 的 MACD/ATR 一并给出
            d["macd_hist_4h"] = _r(_macd_hist(_c4h), 6)
            d["atr14_4h_pct"] = _r(_atr_pct(kl_4h), 2)
        if len(kl_1h) >= 30:
            _c1h = _series(kl_1h)
            d["macd_hist_1h"] = _r(_macd_hist(_c1h), 6)
            d["vol_ratio_1h"] = _r(_vol_ratio_1h(kl_1h), 2)
        if len(kl_1d) >= 30:
            d["adx14_1d"] = _r(_adx(kl_1d), 1)
        try:
            kl_1w = _klines(sym, "1w", 60)
            if len(kl_1w) >= 12:
                _cw = _series(kl_1w)
                _e9w, _e21w = _ema(_cw, 9), _ema(_cw, 21)
                if _e9w and _e21w:
                    d["trend_1w"] = ("bullish" if _e9w > _e21w
                                     else ("bearish" if _e9w < _e21w else "mixed"))
                    d["ema_trend_1w"] = d["trend_1w"]
                # [轮148 2026-09-21] 模型在 missing_evidence 里点名「1w 精确指标数值缺失」
                # （只给了方向不够）⇒ 补周线**数值**：RSI14 / ATR% / 距均线 / 4 周收益。
                d["rsi14_1w"] = _r(_rsi(_cw), 1)
                d["atr14_1w_pct"] = _r(_atr_pct(kl_1w), 2)
                _e50w = _ema(_cw, 50)
                if _e50w and _cw[-1]:
                    d["dist_ema50_1w_pct"] = _r((_cw[-1] / _e50w - 1) * 100, 2)
                    d["above_ema50_1w"] = bool(_cw[-1] > _e50w)
                if len(_cw) >= 5 and _cw[-5] > 0:
                    d["ret_4w_pct"] = _r((_cw[-1] / _cw[-5] - 1) * 100, 2)
        except Exception as _w_err:  # noqa: BLE001
            errors.append(f"market:{sym}: 1w 派生失败 {str(_w_err)[:60]}")
        # ── [轮147] 链上/宏观辅助读数（fear_greed 等）──
        _aux_row = _aux.get(_base(sym))
        if _aux_row:
            d["fear_greed"] = _r(_aux_row.get("fear_greed"), 1)
            d["btc_dominance"] = _r(_aux_row.get("btc_dominance"), 2)
            d["active_addresses"] = _r(_aux_row.get("active_addresses"), 0)
            # 量化简报的另一条读取路径（`md.onchain_macro.fear_greed`）
            d["onchain_macro"] = {"fear_greed": d["fear_greed"],
                                  "btc_dominance": d["btc_dominance"],
                                  "ts_ms": _aux_row.get("timestamp_ms")}
        out["symbols"][sym] = d
        if len(lr) >= 10:
            daily_returns[sym] = lr
    # 相关性（30d 日对数收益）：只报 |rho| ≥ 0.7 的对 + 各币对 BTC 的相关
    pairs = []
    syms = list(daily_returns.keys())
    btc_corr = {}
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            c = _corr(daily_returns[syms[i]], daily_returns[syms[j]])
            if c is None:
                continue
            if "BTC" in (syms[i], syms[j]):
                other = syms[j] if syms[i] == "BTC" else syms[i]
                btc_corr[other] = _r(c, 2)
            if abs(c) >= 0.7:
                pairs.append({"a": syms[i], "b": syms[j], "rho": _r(c, 2)})
    out["correlation_pairs"] = sorted(pairs, key=lambda p: -abs(p["rho"]))[:20]
    out["corr_to_btc"] = btc_corr
    if btc_corr:
        vals = [v for v in btc_corr.values() if v is not None]
        out["avg_corr_to_btc"] = _r(sum(vals) / len(vals), 2) if vals else None
    out["as_of_ms"] = latest_ts or None
    # [2026-09-08] 写入 60s 缓存（见函数头注释）
    _MARKET_LAYER_CACHE[_key] = (_now, out)
    # 粗清：缓存超过 64 个键时丢最旧一半，防长跑泄漏
    if len(_MARKET_LAYER_CACHE) > 64:
        for k in sorted(_MARKET_LAYER_CACHE, key=lambda x: _MARKET_LAYER_CACHE[x][0])[:32]:
            _MARKET_LAYER_CACHE.pop(k, None)
    return out


# --------------------------------------------------------------------------- flows layer
def _market_db():
    from backend.database.connection import MarketSessionLocal

    return MarketSessionLocal()


def _rows(db, sql: str, params: Dict[str, Any], errors: List[str], tag: str) -> List[Any]:
    try:
        from sqlalchemy import text

        return db.execute(text(sql), params).mappings().all()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        errors.append(f"{tag}: {str(exc)[:120]}")
        return []


def build_flows_layer(symbols: Sequence[str], errors: List[str], *, events_hours: float = 24.0) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    now_ms = int(time.time() * 1000)
    bases = {_base(s) for s in symbols}
    db = _market_db()
    try:
        # funding：每 (exchange, symbol) 最新一条，近 24h
        rows = _rows(
            db,
            "SELECT DISTINCT ON (exchange, symbol) exchange, symbol, funding_rate, timestamp FROM perp_funding "
            "WHERE timestamp >= :since ORDER BY exchange, symbol, timestamp DESC",
            {"since": now_ms - 24 * 3600 * 1000}, errors, "flows.funding",
        )
        funding: Dict[str, Dict[str, float]] = {}
        try:
            from backend.services.events.funding_universe import rate_8h
        except Exception:
            def rate_8h(ex: str, rate: float) -> float:  # type: ignore
                return float(rate)
        extremes: List[Dict[str, Any]] = []
        for r in rows:
            b = _base(r["symbol"])
            r8 = rate_8h(str(r["exchange"]), float(r["funding_rate"] or 0.0))
            if b in bases:
                funding.setdefault(b, {})[str(r["exchange"])] = _r(r8 * 100, 4)  # 8h 费率 %
            if abs(r8) >= 0.0005:  # ≥ 0.05%/8h（年化 ≈ 55%）视为尾部
                extremes.append({"symbol": r["symbol"], "exchange": r["exchange"], "rate_8h_pct": _r(r8 * 100, 4)})
        out["funding_8h_pct"] = funding
        out["funding_universe_scanned"] = len(rows)
        out["funding_extremes"] = sorted(extremes, key=lambda x: -abs(x["rate_8h_pct"] or 0))[:12]

        # OI / 多空比：每币最新一条 + 24h 前一条 → 变化
        rows = _rows(
            db,
            "SELECT DISTINCT ON (symbol) symbol, ts_ms, open_interest_value, global_ls_ratio, top_position_ls_ratio, taker_buy_sell_ratio "
            "FROM position_structure WHERE ts_ms >= :since ORDER BY symbol, ts_ms DESC",
            {"since": now_ms - 6 * 3600 * 1000}, errors, "flows.position_structure",
        )
        ps: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            b = _base(r["symbol"])
            if b in bases:
                ps[b] = {
                    "oi_usd": _r(r["open_interest_value"], 0),
                    "global_ls": _r(r["global_ls_ratio"], 3),
                    "top_pos_ls": _r(r["top_position_ls_ratio"], 3),
                    "taker_bs": _r(r["taker_buy_sell_ratio"], 3),
                }
        if ps:
            prev = _rows(
                db,
                "SELECT DISTINCT ON (symbol) symbol, open_interest_value FROM position_structure "
                "WHERE ts_ms BETWEEN :lo AND :hi ORDER BY symbol, ts_ms DESC",
                {"lo": now_ms - 26 * 3600 * 1000, "hi": now_ms - 22 * 3600 * 1000}, errors, "flows.position_structure_prev",
            )
            for r in prev:
                b = _base(r["symbol"])
                cur = ps.get(b)
                if cur and cur.get("oi_usd") and r["open_interest_value"]:
                    cur["oi_chg_24h_pct"] = _r((cur["oi_usd"] / float(r["open_interest_value"]) - 1) * 100, 2)
        out["position_structure"] = ps

        # 24h 清算：按币 / 按方向合计 + 全市场
        rows = _rows(
            db,
            "SELECT symbol, side, SUM(notional_usd) AS usd, COUNT(*) AS n FROM liquidation_ticks "
            "WHERE ts_ms >= :since GROUP BY symbol, side",
            {"since": now_ms - 24 * 3600 * 1000}, errors, "flows.liquidations",
        )
        liq: Dict[str, Dict[str, Any]] = {}
        total_long = total_short = 0.0
        for r in rows:
            b = _base(r["symbol"])
            side = str(r["side"]).upper()
            usd = float(r["usd"] or 0)
            # side 为被清算的持仓方向：SELL 单 = 多头被清算
            key = "long_liq_usd" if side in ("SELL", "LONG") else "short_liq_usd"
            if key == "long_liq_usd":
                total_long += usd
            else:
                total_short += usd
            if b in bases:
                liq.setdefault(b, {"long_liq_usd": 0.0, "short_liq_usd": 0.0})
                liq[b][key] = _r(liq[b][key] + usd, 0)
        out["liquidations_24h"] = liq
        out["liquidations_24h_market"] = {"long_liq_usd": _r(total_long, 0), "short_liq_usd": _r(total_short, 0)}
    finally:
        db.close()

    # 市场事件（events 总线，24h，severity ≥ 2）
    try:
        from backend.services.events import market_events_store as mes

        evs = mes.recent(hours=events_hours, min_severity=2, limit=200)
        keep = []
        news_pos = news_neg = news_n = 0
        for e in evs:
            et = str(e.get("event_type") or "")
            if et.startswith("news"):
                news_n += 1
                d = e.get("direction")
                if d is not None:
                    if d > 0:
                        news_pos += 1
                    elif d < 0:
                        news_neg += 1
            sym = _base(e.get("symbol") or "") if e.get("symbol") else None
            if sym is None or sym in bases or int(e.get("severity") or 0) >= 4:
                keep.append({
                    "t": et, "sym": e.get("symbol"), "ts": e.get("ts_ms"), "sev": e.get("severity"),
                    "dir": e.get("direction"), "title": (e.get("title") or "")[:120], "src": e.get("source"),
                })
        out["events"] = keep[:40]
        out["events_total_24h"] = len(evs)
        out["high_impact_news_24h"] = {"n": news_n, "pos": news_pos, "neg": news_neg}
    except Exception as exc:
        errors.append(f"flows.events: {str(exc)[:120]}")

    # 新闻密度 / 正负比（全部已标注新闻，社交情绪代理；news_events 在 market 库，created_at 为本地 naive 时间）
    mdb = _market_db()
    try:
        from datetime import datetime, timedelta

        since_naive = datetime.now() - timedelta(hours=24)
        prev_naive = datetime.now() - timedelta(hours=48)
        rows = _rows(
            mdb,
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN impact_direction > 0 THEN 1 ELSE 0 END) AS pos, "
            "SUM(CASE WHEN impact_direction < 0 THEN 1 ELSE 0 END) AS neg, "
            "AVG(impact_strength) AS avg_strength "
            "FROM news_events WHERE created_at >= :since",
            {"since": since_naive}, errors, "flows.news_density",
        )
        prev = _rows(mdb, "SELECT COUNT(*) AS n FROM news_events WHERE created_at >= :lo AND created_at < :hi",
                     {"lo": prev_naive, "hi": since_naive}, errors, "flows.news_density_prev")
        if rows:
            r = rows[0]
            n, pos, neg = int(r["n"] or 0), int(r["pos"] or 0), int(r["neg"] or 0)
            prev_n = int(prev[0]["n"] or 0) if prev else 0
            out["news_density_24h"] = {
                "n": n, "pos": pos, "neg": neg,
                "pos_ratio": _r(pos / (pos + neg), 2) if (pos + neg) else None,
                "avg_strength": _r(r["avg_strength"], 2),
                "density_vs_prev_24h": _r(n / prev_n, 2) if prev_n else None,
            }
    finally:
        mdb.close()
    return out


# --------------------------------------------------------------------------- positions layer
def _main_db():
    from backend.database.connection import SessionLocal
    from backend.core.tenant import set_system_identity

    set_system_identity()
    return SessionLocal()


def build_positions_layer(errors: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        from backend.services.risk.trading_state import get_state_store

        out["trading_state"] = get_state_store().snapshot().to_dict()
    except Exception as exc:
        errors.append(f"positions.trading_state: {str(exc)[:120]}")
    try:
        from backend.services.risk.risk_engine import get_risk_engine_v3

        st = get_risk_engine_v3().status()
        out["risk_engine"] = {k: v for k, v in (st or {}).items() if k in ("enabled", "state", "kill_switch", "drawdown", "accounts", "peak_equity", "breakers")} or st
    except Exception as exc:
        errors.append(f"positions.risk_engine: {str(exc)[:120]}")
    db = _main_db()
    try:
        accts = _rows(
            db,
            "SELECT account_id, total_equity, available_balance, unrealized_pnl, realized_pnl, total_fee_paid, initial_balance "
            "FROM paper_balances ORDER BY account_id",
            {}, errors, "positions.balances",
        )
        out["accounts"] = [
            {"account_id": r["account_id"], "equity": _r(r["total_equity"], 2), "available": _r(r["available_balance"], 2),
             "upnl": _r(r["unrealized_pnl"], 2), "realized": _r(r["realized_pnl"], 2), "fees": _r(r["total_fee_paid"], 2),
             "initial": _r(r["initial_balance"], 2)}
            for r in accts
        ]
        eq = {int(r["account_id"]): float(r["total_equity"] or 0) for r in accts}
        pos = _rows(
            db,
            "SELECT account_id, strategy_id, symbol, side, size, entry_price, mark_price, leverage, margin, unrealized_pnl, "
            "sl_price, tp_price, opened_at, timeframe_tier, trade_nature, expected_hold_hours, peak_pnl_pct "
            "FROM paper_positions WHERE status = 'open' ORDER BY account_id, symbol",
            {}, errors, "positions.open",
        )
        now = time.time()
        rows_out = []
        gross_by_acct: Dict[int, float] = {}
        for r in pos:
            entry, mark = float(r["entry_price"] or 0), float(r["mark_price"] or 0)
            size = float(r["size"] or 0)
            notional = abs(size) * (mark or entry)
            acct = int(r["account_id"])
            gross_by_acct[acct] = gross_by_acct.get(acct, 0.0) + notional
            sign = 1 if str(r["side"]).lower() == "long" else -1
            upnl_pct = ((mark / entry - 1) * sign * 100) if (entry and mark) else None
            sl = float(r["sl_price"]) if r["sl_price"] else None
            risk_per_unit = (entry - sl) * sign if (sl and entry) else None
            r_mult = ((mark - entry) * sign / risk_per_unit) if (risk_per_unit and risk_per_unit > 0 and mark) else None
            dist_sl_pct = ((mark - sl) * sign / mark * 100) if (sl and mark) else None
            age_h = None
            try:
                ca = r["opened_at"]
                if ca is not None:
                    age_h = _r((now - ca.timestamp()) / 3600, 1)
            except Exception:
                pass
            time_limit_left_h = None
            try:
                if age_h is not None and r["expected_hold_hours"]:
                    time_limit_left_h = _r(float(r["expected_hold_hours"]) - age_h, 1)
            except Exception:
                pass
            rows_out.append({
                "acct": acct, "strategy": r["strategy_id"], "sym": r["symbol"], "side": r["side"],
                "tier": r["timeframe_tier"], "nature": r["trade_nature"],
                "notional": _r(notional, 0), "lev": _r(r["leverage"], 1), "upnl_pct": _r(upnl_pct, 2),
                "peak_pnl_pct": _r(r["peak_pnl_pct"], 2),
                "R": _r(r_mult, 2), "dist_sl_pct": _r(dist_sl_pct, 2), "age_h": age_h,
                "time_limit_left_h": time_limit_left_h,
                "pct_equity": _r(notional / eq[acct] * 100, 1) if eq.get(acct) else None,
            })
        out["open"] = rows_out[:60]
        out["open_count"] = len(rows_out)
        out["gross_exposure_pct"] = {str(a): _r(g / eq[a] * 100, 1) for a, g in gross_by_acct.items() if eq.get(a)}
    finally:
        db.close()
    return out


# --------------------------------------------------------------------------- performance layer
def build_performance_layer(errors: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    db = _main_db()
    try:
        snaps = _rows(
            db,
            "SELECT DISTINCT ON (account_id) account_id, computed_at, window_days, payload FROM edge_ledger_snapshots "
            "ORDER BY account_id, computed_at DESC",
            {}, errors, "performance.edge_ledger",
        )
        ledger = {}
        for r in snaps:
            payload = r["payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            payload = payload or {}

            def _slim(stats: Dict[str, Any]) -> Dict[str, Any]:
                keys = ("n", "net", "gross", "fees", "pf", "win_rate", "avg_net", "net_bp", "ci_low", "ci_high", "expectancy")
                return {k: _r(stats.get(k), 4) if isinstance(stats.get(k), (int, float)) else stats.get(k) for k in keys if k in stats}

            ledger[str(r["account_id"])] = {
                "as_of": r["computed_at"].isoformat() if r["computed_at"] else None,
                "window_days": r["window_days"],
                "total": _slim(payload.get("total") or {}),
                "by_tier": {k: _slim(v) for k, v in (payload.get("by_tier") or {}).items()},
                "by_close_cat": {k: _slim(v) for k, v in (payload.get("by_close_cat") or {}).items()},
                "worst_symbols": [{"symbol": x.get("symbol"), "net": _r(x.get("net"), 2), "n": x.get("n")} for x in (payload.get("worst_symbols") or [])[:5]],
                "shadow_scalp": payload.get("shadow_scalp"),
            }
        out["edge_ledger"] = ledger
    finally:
        db.close()
    try:
        from backend.services.analysis import ledgers

        out["signal_sources"] = [
            {k: (_r(v, 4) if isinstance(v, float) else v) for k, v in row.items() if k != "last_ms"}
            for row in ledgers.signal_source_stats(30)
        ][:20]
        out["agent_credibility"] = [
            {k: (_r(v, 4) if isinstance(v, float) else v) for k, v in row.items() if k != "last_ms"}
            for row in ledgers.agent_credibility(30)
        ][:20]
    except Exception as exc:
        errors.append(f"performance.ledgers: {str(exc)[:120]}")
    try:
        from backend.services.analysis import ledgers

        summ = ledgers.runs_summary(7)
        out["analysis_runs_7d"] = {"daily_brief_days": summ.get("daily_brief_days"),
                                   "rows": [{k: r.get(k) for k in ("task", "transport", "role", "n", "n_ok", "avg_consensus")} for r in summ.get("rows", [])][:20]}
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------- config layer
def config_hash_and_flags() -> Tuple[str, Dict[str, Any]]:
    flags: Dict[str, Any] = {k: os.getenv(k) for k in CONFIG_FLAGS if os.getenv(k) is not None}
    tuning: Dict[str, Any] = {}
    try:
        from backend.services.runtime_tuning_store import get_all_tuning

        tuning = get_all_tuning() or {}
    except Exception as exc:
        logger.debug("[context_pack] runtime_tuning 读取失败: %s", exc)
    blob = json.dumps({"flags": flags, "tuning": tuning}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest(), flags


def build_config_layer(errors: List[str]) -> Dict[str, Any]:
    try:
        h, flags = config_hash_and_flags()
        tuning_keys: List[str] = []
        try:
            from backend.services.runtime_tuning_store import get_all_tuning

            tuning_keys = sorted((get_all_tuning() or {}).keys())[:60]
        except Exception:
            pass
        return {"config_hash": h, "flags": flags, "runtime_tuning_keys": tuning_keys}
    except Exception as exc:
        errors.append(f"config: {str(exc)[:120]}")
        return {}


# --------------------------------------------------------------------------- factor layer
def build_factor_layer(
    symbols: Sequence[str],
    errors: List[str],
    *,
    market_layer: Optional[Dict[str, Any]] = None,
    flows_layer: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """[轮107 2026-09-19] 中长线**因子证据层** —— 主脑 prompt 此前完全没有因子。

    ## 为什么必须补这一层

    现役主脑（`mlto/brain.py`）用的上下文是 `context_pack.build("midlong_thesis")`，
    而它的 5 个层（market / flows / positions / performance / config）里
    **没有任何因子字段**；写好的因子注入只存在于 `mlto/qual_layer`
    ——那条路属于**旧 orchestrator（9/5 已下线）**，主脑根本不经过。
    实测后果：主脑做中/长线方向判断时，看不到"量化层在用什么因子、方向如何、结论是什么"。

    本层给每个币放三样东西（都是**只读证据**，不是指令）：

      · ``active``  —— 活跃中长线因子的当前读数 + **方向语义**
        （`sign=-1` 表示该因子 IC<0、量化层**反着用**：读数越高越看空）；
      · ``route``   —— 中线因子路由的结论（action / score / 前几票），
        即"量化层真正会做的方向"；
      · ``brief``   —— `MidLongQuantBrief`（对齐分 / 证据可用率 / 缺失项，
        零 LLM）。该模块此前**全库没有生产调用方**，这里首次接入。

    成本：每币 1 次 `build_snapshot`（因子已在别处缓存 K 线）+ 1 次路由判定；
    只对传入的 `symbols` 计算（主脑是 1~2 个币）。
    关掉本层：`CONTEXT_PACK_FACTORS_ENABLED=false`。
    """
    try:
        from backend.config import settings as _st
        if not bool(getattr(_st, "CONTEXT_PACK_FACTORS_ENABLED", True)):
            return {}
    except Exception:
        pass

    out: Dict[str, Any] = {"role": "证据，非指令", "symbols": {}}
    try:
        from backend.services.factor_engine.midlong_active_factor_set import (
            midlong_active_factor_set as _mlset,
        )
    except Exception as _imp_err:
        errors.append(f"factors: 活跃因子集不可用 {_imp_err}")
        return {}
    try:
        from backend.services.factor_engine.midlong_factor_route import factor_route_decide as _route
    except Exception:
        _route = None
    try:
        from backend.services.mid_long_quant_brief import mid_long_quant_brief_builder as _qbld
    except Exception:
        _qbld = None

    _mrows = (market_layer or {}).get("symbols") or {}
    _fund = (flows_layer or {}).get("funding_8h_pct") or {}

    for sym in symbols:
        sym_u = str(sym).upper()
        row = _mrows.get(sym_u) or {}
        _px = 0.0
        try:
            _px = float(row.get("last") or 0.0)
        except (TypeError, ValueError):
            _px = 0.0
        entry: Dict[str, Any] = {}

        # ① 因子读数 + 方向语义
        try:
            snap = _mlset.build_snapshot(sym_u)
            meta = snap.get("meta") if isinstance(snap.get("meta"), dict) else {}
            ranked: List[Dict[str, Any]] = []
            for tf in ("4h", "1d"):
                for fid, val in (snap.get(tf) or {}).items():
                    m = meta.get(fid) or {}
                    try:
                        _v = float(val)
                    except (TypeError, ValueError):
                        continue
                    try:
                        _ic = float(m.get("ic") or 0.0)
                    except (TypeError, ValueError):
                        _ic = 0.0
                    try:
                        _sg = int(m.get("sign") or 1)
                    except (TypeError, ValueError):
                        _sg = 1
                    ranked.append({
                        "id": str(fid)[:32], "tf": tf, "value": round(_v, 5),
                        "ic": round(_ic, 4), "sign": _sg,
                        # inv=true → 该因子 IC<0，量化层反着用（读数越高越看空）
                        "inv": _sg < 0,
                    })
            # 按 |IC| 排（预测力），不要按原始量纲 —— 否则 macd(312) 永远霸榜
            ranked.sort(key=lambda x: -abs(x.get("ic") or 0))
            if ranked:
                entry["active"] = {"count": int(snap.get("count") or len(ranked)),
                                   "top": ranked[:8],
                                   "note": "inv=true 的因子 IC<0，量化层反着用（读数越高越看空）"}
        except Exception as _snap_err:
            errors.append(f"factors:{sym_u}: 快照失败 {str(_snap_err)[:80]}")

        # ② 因子路由结论（量化层真正会做的方向）
        if _route is not None and _px > 0:
            try:
                r = _route(sym_u, {sym_u: {"current_price": _px, "data_reliable": True}}, "paper")
                _votes = r.get("votes") or {}
                _tv = sorted(
                    [(k, v) for k, v in _votes.items() if isinstance(v, dict)
                     and isinstance(v.get("vote"), (int, float))],
                    key=lambda kv: -abs(kv[1]["vote"]),
                )[:6]
                entry["route"] = {
                    "action": r.get("action"), "score": r.get("score"),
                    "n": len(_votes),
                    "top": [{"id": str(k)[:32], "vote": v.get("vote")} for k, v in _tv],
                    **({"regime_inverted": r.get("trend_invert_regime")}
                       if r.get("trend_invert_regime") else {}),
                }
            except Exception as _rt_err:
                errors.append(f"factors:{sym_u}: 路由失败 {str(_rt_err)[:80]}")

        # ③ 零 LLM 量化简报（对齐分/证据可用率/缺失项）
        if _qbld is not None:
            try:
                # [轮138 2026-09-20 根因修复] 这里原来只塞 rsi/ema_trend，而
                # `mid_long_quant_brief` 的预检要的是 `ind_1h.macd_hist` / `ind_1h.vol_ratio`
                # / `ind_1d.adx` / `md.adx_1d` —— 于是 300 次 refresh 里
                # `macd_hist_1h(101) / vol_ratio_1h(104) / adx_1d(107)` **恒缺**，
                # 主脑每轮都在"缺一大片证据"下判断 ⇒ 85% 是 accepted=False/neutral（不成交的最后一环）。
                # 现按 market 层已有的派生字段补齐（`adx_1d` 兼容两种键名）。
                _ind_1h = {"rsi": row.get("rsi14_1h"), "ema_trend": row.get("ema_trend_1h"),
                           "macd_hist": row.get("macd_hist_1h"), "vol_ratio": row.get("vol_ratio_1h")}
                _ind_4h = {"rsi": row.get("rsi14_4h"), "ema_trend": row.get("ema_trend_4h")}
                _ind_1d = {"rsi": row.get("rsi14_1d"), "atr_pct": row.get("atr14_1d_pct"),
                           "adx": row.get("adx14_1d") or row.get("adx_1d")}
                # [轮148] 周线数值块（模型点名要"1w 精确指标数值"）
                _ind_1w = {"rsi": row.get("rsi14_1w"), "atr_pct": row.get("atr14_1w_pct"),
                           "trend": row.get("trend_1w") or row.get("ema_trend_1w"),
                           "ema_trend": row.get("ema_trend_1w") or row.get("trend_1w")}
                _fr = (_fund.get(sym_u) or {})
                _md = {
                    "indicators_1h": {k: v for k, v in _ind_1h.items() if v is not None},
                    "indicators_4h": {k: v for k, v in _ind_4h.items() if v is not None},
                    "indicators_1d": {k: v for k, v in _ind_1d.items() if v is not None},
                    "indicators_1w": {k: v for k, v in _ind_1w.items() if v is not None},
                    "adx_1d": row.get("adx14_1d") or row.get("adx_1d"),
                    "trend_1w": row.get("trend_1w"),
                    "funding_rate": (list(_fr.values())[0] if _fr else None),
                    "market_cycle": row.get("regime") or row.get("market_cycle"),
                    "swing_low": row.get("range_24h_low"),
                    "swing_high": row.get("range_24h_high"),
                    # 若 market 层提供则透传（当前无源，预检会如实记缺）
                    "fear_greed": row.get("fear_greed"),
                }
                _b = _qbld.build(sym_u, _md, orchestrator=None, side_hint="long").to_dict()
                entry["brief"] = {
                    "direction": _b.get("direction"),
                    "alignment_score": _b.get("alignment_score"),
                    "evidence_available_ratio": _b.get("evidence_available_ratio"),
                    "missing_data": (_b.get("missing_data") or [])[:8],
                    "structure_levels": _b.get("structure_levels") or {},
                }
            except Exception as _qb_err:
                errors.append(f"factors:{sym_u}: 简报失败 {str(_qb_err)[:80]}")

        # ③b [轮108] 文本版量化简报（`decision_core.quant_brief.build_quant_brief`）。
        # 该模块此前只被**已退场**的 `trend_agent` 引用 —— 同样是"写好了没进 prompt"。
        # 它给的是"先看证据质量再下结论"的口径（多周期一致性 / 结构位 / 数据完整度 +
        # 决策指引），与 ③ 的结构化简报互补。
        try:
            from backend.services.decision_core.quant_brief import build_quant_brief as _bqb
            _menv = {
                "orchestrator": {},
                "price": _px,
                "indicators_1h": {k: v for k, v in
                                  {"rsi": row.get("rsi14_1h")}.items() if v is not None},
                "indicators_4h": {k: v for k, v in
                                  {"rsi": row.get("rsi14_4h"), "trend": row.get("ema_trend_4h")}.items()
                                  if v is not None},
                "indicators_1d": {k: v for k, v in
                                  {"rsi": row.get("rsi14_1d"), "adx": row.get("adx_1d")}.items()
                                  if v is not None},
                "structure_levels": {"support": row.get("range_24h_low"),
                                     "resistance": row.get("range_24h_high")},
                "midlong_factors": {"count": (entry.get("active") or {}).get("count") or 0},
                "mtf_resonance": row.get("mtf_resonance") or {},
            }
            _txt = str(_bqb(sym_u, {sym_u: _menv}, nature="swing") or "").strip()
            if _txt:
                entry["brief_text"] = _txt[:1200]
        except Exception as _bt_err:
            errors.append(f"factors:{sym_u}: 文本简报失败 {str(_bt_err)[:80]}")

        if entry:
            out["symbols"][sym_u] = entry

    # ④ [轮108] 因子系统状态（学习产物，全局一份）：权重覆盖/归零/负权重 + 衰减退役。
    # `learning_readback.factor_system_snapshot()` 的 docstring 就写着"供决策 prompt 参考"，
    # 但此前只有它自己的 CLI 在读 —— 主脑看不到"因子池现在是什么状态"。
    try:
        from backend.services.learning_readback import factor_system_snapshot as _fss
        _sys = _fss(top_n=4) or {}
        _rw = _sys.get("runtime_weights") or {}
        if isinstance(_rw, dict) and "error" not in _rw:
            out["system"] = {
                "role": "证据，非指令",
                "runtime_weights": {k: _rw.get(k) for k in (
                    "n", "n_zero", "n_negative", "n_file", "n_file_zero", "zero_sample")},
                "decay": _sys.get("decay") or _sys.get("decay_status") or {},
                "backtest_attr": _sys.get("backtest_attr") or {},
            }
    except Exception as _sys_err:
        errors.append(f"factors:system 失败 {str(_sys_err)[:80]}")

    if not out["symbols"]:
        return {}
    return out


# --------------------------------------------------------------------------- build
def build_analyst_layer(symbols: Sequence[str], errors: List[str]) -> Dict[str, Any]:
    """[轮129 2026-09-20] 六分析师数值化信号层（**读库**，不在 prompt 路径里现算）。

    为什么读库而不是现场计算：计算要走 6 张表 + 多周期 K 线，放在主脑每轮 prompt 路径上
    会把 tick 拖慢；而"日频落地"本来就允许半小时级的延迟（见 main.py 的 analyst_signals_daily）。
    没有数据时返回 `{}`（层缺省），并**在 errors 里写明原因**，绝不塞中性 0 冒充有数据。
    """
    try:
        from backend.services.analysts import prompt_block as _pb
        from backend.services.analysts.scorers import _enabled as _an_enabled

        if not _an_enabled():
            errors.append("analysts: 层已停用（ANALYST_SIGNALS_ENABLED=false）")
            return {}
        blk = _pb(list(symbols)) or {}
        if not blk.get("available"):
            errors.append(f"analysts: {(blk.get('note') or '无信号')[:100]}")
            return {}
        return blk
    except Exception as exc:  # noqa: BLE001
        errors.append(f"analysts: {type(exc).__name__}: {str(exc)[:100]}")
        return {}


def build(task: str, *, symbols: Optional[Sequence[str]] = None, layers: Optional[Sequence[str]] = None,
          events_hours: float = 24.0) -> ContextPack:
    """构建 context pack。layers 缺省 = 全部五层；事件评估任务可只取 market+flows。"""
    syms = list(symbols) if symbols else universe()
    # [轮107] 主脑的中长线论文任务默认带上因子层（其余任务按需显式传 layers）
    # [轮129] 再带上 `analysts`（六分析师数值化信号）——这是"分析师判断进主脑"的落点。
    _default_layers = ("market", "flows", "positions", "performance", "config")
    if str(task or "") == "midlong_thesis":
        _default_layers = _default_layers + ("factors", "analysts")
    want = set(layers or _default_layers)
    errors: List[str] = []
    out: Dict[str, Any] = {"universe": syms}
    t0 = time.time()
    if "market" in want:
        out["market"] = build_market_layer(syms, errors)
    if "flows" in want:
        out["flows"] = build_flows_layer(syms, errors, events_hours=events_hours)
    if "positions" in want:
        out["positions"] = build_positions_layer(errors)
    if "performance" in want:
        out["performance"] = build_performance_layer(errors)
    if "config" in want:
        out["config"] = build_config_layer(errors)
    if "factors" in want:
        _fl = build_factor_layer(syms, errors, market_layer=out.get("market"),
                                 flows_layer=out.get("flows"))
        if _fl:
            out["factors"] = _fl
    if "analysts" in want:
        _al = build_analyst_layer(syms, errors)
        if _al:
            out["analysts"] = _al
    cutoff = int(time.time() * 1000)
    m_as_of = (out.get("market") or {}).get("as_of_ms")
    if m_as_of:
        cutoff = min(cutoff, int(m_as_of) + 1)
    pack = ContextPack(task=task, data_cutoff_ms=cutoff, layers=out, errors=errors)
    logger.info("[context_pack] %s 构建完成 layers=%s symbols=%d errors=%d elapsed=%.1fs tokens≈%d",
                task, sorted(want), len(syms), len(errors), time.time() - t0, estimate_tokens(pack.to_prompt_text(10**9)))
    return pack
