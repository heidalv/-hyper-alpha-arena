# -*- coding: utf-8 -*-
"""Anomaly Agent — 市场与运行时异常检测（v3 方向 3，p1-agents-a）。

确定性核心：对六条独立通道做**滚动 z-score + CUSUM 漂移检测**，合成 0–1 的 stress_score，
再映射成 TradingState 建议（ACTIVE / REDUCING / HALTED）。

  通道              数据源                        检测量
  ────────────────────────────────────────────────────────────────────────────
  price            crypto_klines 1h (BTC)        1h/4h 收益、24h 最大回撤（方案硬触发：1h ≤ −8% 或 4h ≤ −12%）
  volume           crypto_klines 1h (核心币)      成交量 z-score（放量恐慌 / 缩量枯竭）
  funding          perp_funding                  8h 归一费率的 z-score + CUSUM（费率制度切换）
  open_interest    position_structure            OI 变化率 z-score
  liquidation      liquidation_ticks             小时清算额 z-score + CUSUM（级联）
  freshness        market_events / job_registry   数据源静默时长、失败任务数（运行时异常）

未纳入本 Agent（归 Phase 2 ExecutionQA，避免两个 Agent 争同一职责）：点差、下单延迟、成交偏差/滑点。

**观察模式铁律**：本 Agent 永远只把 TradingState 建议写进 `agent_predictions`（kind=`trading_state`）与
建议列表，**绝不调用 `TradingStateStore.set_state`**。方案要求"REDUCING 以上需双模型或规则确认"，
在可信度门通过并显式给 act 模式之前，RiskEngine 的自动触发仍是唯一权威。

预测评分（`score_trading_state`）：窗口内用 BTC 1h 真实价格算最大回撤，
回撤 ≥ 阈值 = 实际处于压力；建议减仓且确有压力 → 1.0，建议 ACTIVE 且平静 → 1.0，其余 0.0。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.services.agents.base import (
    Advice,
    ObservationAgent,
    Prediction,
    cusum,
    env_float,
    env_int,
    env_true,
    now_ms,
    zscore,
)

logger = logging.getLogger(__name__)

AGENT_ID = "anomaly"
KIND_TRADING_STATE = "trading_state"

# 各通道对 stress_score 的权重（和为 1）
CHANNEL_WEIGHTS: Dict[str, float] = {
    "price": 0.30,
    "liquidation": 0.20,
    "funding": 0.15,
    "open_interest": 0.13,
    "volume": 0.12,
    "freshness": 0.10,
}


def _market_db():
    from backend.database.connection import MarketSessionLocal

    return MarketSessionLocal()


def _rows(db, sql: str, params: Dict[str, Any], errors: List[str], label: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text

    try:
        return [dict(r) for r in db.execute(text(sql), params).mappings().all()]
    except Exception as exc:
        errors.append(f"{label}: {str(exc)[:160]}")
        return []


def _z_to_unit(z: Optional[float], *, soft: float = 2.0, hard: float = 4.0) -> float:
    """|z| 映射到 0–1 的分项压力分：|z| ≤ soft → 0；≥ hard → 1；中间线性。"""
    if z is None:
        return 0.0
    a = abs(float(z))
    if a <= soft:
        return 0.0
    if a >= hard:
        return 1.0
    return (a - soft) / (hard - soft)


def load_hourly_klines(symbols: Sequence[str], hours: int, errors: List[str],
                       exchange: str = "binance") -> Dict[str, List[Dict[str, Any]]]:
    """核心币近 N 小时 1h K 线（升序）。crypto_klines.timestamp 为秒。"""
    from sqlalchemy import bindparam, text

    syms = [str(s).upper() for s in symbols]
    out: Dict[str, List[Dict[str, Any]]] = {s: [] for s in syms}
    if not syms:
        return out
    since = int(time.time()) - int(hours) * 3600
    db = _market_db()
    try:
        stmt = text(
            "SELECT symbol, timestamp, open_price, high_price, low_price, close_price, volume "
            "FROM crypto_klines WHERE exchange = :ex AND period = '1h' AND symbol IN :syms AND timestamp >= :since "
            "ORDER BY symbol, timestamp"
        ).bindparams(bindparam("syms", expanding=True))
        for r in db.execute(stmt, {"ex": exchange, "syms": syms, "since": since}).mappings().all():
            out.setdefault(str(r["symbol"]).upper(), []).append({
                "ts": int(r["timestamp"]),
                "open": float(r["open_price"] or 0),
                "high": float(r["high_price"] or 0),
                "low": float(r["low_price"] or 0),
                "close": float(r["close_price"] or 0),
                "volume": float(r["volume"] or 0),
            })
    except Exception as exc:
        errors.append(f"klines_1h: {str(exc)[:160]}")
    finally:
        db.close()
    return out


def max_drawdown_pct(closes: Sequence[float]) -> float:
    """收盘序列的最大回撤（正数百分比）。"""
    peak = None
    mdd = 0.0
    for c in closes:
        if c <= 0:
            continue
        peak = c if peak is None else max(peak, c)
        if peak > 0:
            mdd = max(mdd, (peak - c) / peak * 100.0)
    return mdd


def btc_stress_window(start_ms: int, end_ms: int, *, exchange: str = "binance") -> Optional[Dict[str, Any]]:
    """窗口内 BTC 1h 的最大回撤与区间收益（评分器用；取不到价返回 None，不造数）。"""
    from sqlalchemy import text

    db = _market_db()
    try:
        rows = db.execute(
            text(
                "SELECT timestamp, close_price FROM crypto_klines WHERE exchange = :ex AND period = '1h' "
                "AND symbol = 'BTC' AND timestamp >= :lo AND timestamp <= :hi ORDER BY timestamp"
            ),
            {"ex": exchange, "lo": int(start_ms // 1000), "hi": int(end_ms // 1000)},
        ).mappings().all()
    except Exception as exc:
        logger.debug("[Anomaly] btc_stress_window 失败: %s", exc)
        return None
    finally:
        db.close()
    closes = [float(r["close_price"]) for r in rows if r["close_price"]]
    if len(closes) < 4:
        return None
    return {
        "n_bars": len(closes),
        "max_drawdown_pct": round(max_drawdown_pct(closes), 3),
        "ret_pct": round((closes[-1] / closes[0] - 1.0) * 100.0, 3),
    }


class AnomalyAgent(ObservationAgent):
    agent_id = AGENT_ID
    description = "六通道 z-score + CUSUM 异常检测，输出 TradingState 建议（观察模式不生效）"
    kinds = (KIND_TRADING_STATE,)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.lookback_h = env_int("AGENT_ANOMALY_LOOKBACK_H", 168)
        self.reduce_threshold = env_float("AGENT_ANOMALY_REDUCE_SCORE", 0.55)
        self.halt_threshold = env_float("AGENT_ANOMALY_HALT_SCORE", 0.80)
        self.window_h = env_float("AGENT_ANOMALY_WINDOW_H", 24.0)          # 预测窗口
        self.stress_dd_pct = env_float("AGENT_ANOMALY_STRESS_DD_PCT", 4.0)  # 评分：窗口内 BTC 回撤阈值
        self.crash_1h_pct = env_float("RISK_FLASH_CRASH_1H_PCT", 8.0)
        self.crash_4h_pct = env_float("RISK_FLASH_CRASH_4H_PCT", 12.0)
        self.stale_hours = env_float("AGENT_ANOMALY_STALE_H", 6.0)

    # ---------------- 确定性核心 ----------------
    def analyze(self, errors: List[str]) -> Dict[str, Any]:
        from backend.services.trend_core import core_symbols

        symbols = core_symbols()
        klines = load_hourly_klines(["BTC"] + [s for s in symbols if s != "BTC"], self.lookback_h, errors)
        channels: Dict[str, Dict[str, Any]] = {
            "price": self._price_channel(klines, errors),
            "volume": self._volume_channel(klines, symbols, errors),
            "funding": self._funding_channel(symbols, errors),
            "open_interest": self._oi_channel(symbols, errors),
            "liquidation": self._liquidation_channel(errors),
            "freshness": self._freshness_channel(errors),
        }

        weighted = 0.0
        wsum = 0.0
        for name, ch in channels.items():
            score = ch.get("score")
            if score is None:
                continue
            w = CHANNEL_WEIGHTS.get(name, 0.0)
            weighted += w * float(score)
            wsum += w
        stress = round(weighted / wsum, 4) if wsum > 0 else 0.0
        coverage = round(wsum, 3)

        hard = self._hard_triggers(channels["price"])
        suggested, reason = self._suggest_state(stress, hard, coverage)

        try:
            from backend.services.risk.trading_state import get_state_store

            current = get_state_store().snapshot().state
        except Exception as exc:
            errors.append(f"trading_state: {str(exc)[:120]}")
            current = None

        return {
            "symbols": symbols,
            "lookback_h": self.lookback_h,
            "channels": channels,
            "stress_score": stress,
            "channel_coverage": coverage,
            "hard_triggers": hard,
            "suggested_state": suggested,
            "suggest_reason": reason,
            "current_state": current,
            "thresholds": {"reduce": self.reduce_threshold, "halt": self.halt_threshold,
                           "crash_1h_pct": self.crash_1h_pct, "crash_4h_pct": self.crash_4h_pct},
            "llm": self._llm_layer(channels, stress, suggested, errors, hard),
        }

    # ---- 通道 ----
    def _price_channel(self, klines: Dict[str, List[Dict[str, Any]]], errors: List[str]) -> Dict[str, Any]:
        btc = klines.get("BTC") or []
        if len(btc) < 12:
            return {"score": None, "note": "BTC 1h 数据不足"}
        closes = [b["close"] for b in btc if b["close"] > 0]
        rets = [(closes[i] / closes[i - 1] - 1.0) * 100.0 for i in range(1, len(closes))]
        ret_1h = rets[-1] if rets else 0.0
        ret_4h = ((closes[-1] / closes[-5] - 1.0) * 100.0) if len(closes) >= 5 else 0.0
        ret_24h = ((closes[-1] / closes[-25] - 1.0) * 100.0) if len(closes) >= 25 else None
        dd_24h = max_drawdown_pct(closes[-25:]) if len(closes) >= 6 else 0.0
        z = zscore(rets)
        # 只有下跌方向计压力（上涨的极端波动由 volume/funding 通道体现）
        down_z = abs(z) if (z is not None and ret_1h < 0) else 0.0
        score = max(_z_to_unit(down_z if down_z else None), min(1.0, dd_24h / max(1e-6, self.crash_4h_pct)))
        return {
            "score": round(min(1.0, score), 4),
            "ret_1h_pct": round(ret_1h, 3),
            "ret_4h_pct": round(ret_4h, 3),
            "ret_24h_pct": round(ret_24h, 3) if ret_24h is not None else None,
            "drawdown_24h_pct": round(dd_24h, 3),
            "ret_zscore": round(z, 3) if z is not None else None,
            "n_bars": len(closes),
        }

    def _volume_channel(self, klines: Dict[str, List[Dict[str, Any]]], symbols: Sequence[str],
                        errors: List[str]) -> Dict[str, Any]:
        per_symbol: Dict[str, Optional[float]] = {}
        for s in symbols:
            bars = klines.get(s) or []
            vols = [b["volume"] for b in bars if b["volume"] > 0]
            per_symbol[s] = zscore(vols)
        zs = [v for v in per_symbol.values() if v is not None]
        if not zs:
            return {"score": None, "note": "成交量样本不足"}
        peak = max(abs(v) for v in zs)
        return {
            "score": round(_z_to_unit(peak, soft=2.5, hard=5.0), 4),
            "peak_abs_z": round(peak, 3),
            "n_symbols": len(zs),
            "by_symbol": {k: (round(v, 3) if v is not None else None) for k, v in per_symbol.items()},
        }

    def _funding_channel(self, symbols: Sequence[str], errors: List[str]) -> Dict[str, Any]:
        from sqlalchemy import bindparam, text

        bases = {str(s).upper() for s in symbols}
        # 同时匹配 BTC / BTCUSDT 两种落库形态
        sym_params = sorted(bases | {f"{b}USDT" for b in bases})
        db = _market_db()
        try:
            if not sym_params:
                rows = []
            else:
                # [2026-09-07] 按核心币过滤：旧 SQL 拉全表 168h funding，单查 100s+
                # → LeakGuard 强杀 market 连接。
                stmt = text(
                    "SELECT symbol, exchange, funding_rate, timestamp FROM perp_funding "
                    "WHERE timestamp >= :since AND symbol IN :syms "
                    "ORDER BY symbol, timestamp"
                ).bindparams(bindparam("syms", expanding=True))
                try:
                    rows = [dict(r) for r in db.execute(
                        stmt,
                        {"since": now_ms() - self.lookback_h * 3600 * 1000, "syms": sym_params},
                    ).mappings().all()]
                except Exception as exc:
                    errors.append(f"funding: {str(exc)[:160]}")
                    rows = []
        finally:
            db.close()
        try:
            from backend.services.events.funding_universe import rate_8h
        except Exception:
            def rate_8h(ex: str, rate: float) -> float:  # type: ignore
                # [R29] 兜底也必须归一到 8h：否则 hyperliquid 的 1h 费率被当 8h（高估 8×）。
                _hrs = {"hyperliquid": 1.0}.get(str(ex or "").lower(), 8.0)
                return float(rate) * (8.0 / _hrs) if _hrs > 0 else float(rate)

        series: Dict[str, List[float]] = {}
        for r in rows:
            sym = str(r["symbol"] or "").upper()
            base = sym[:-4] if sym.endswith("USDT") else sym
            if base not in bases:
                continue
            try:
                series.setdefault(base, []).append(float(rate_8h(str(r["exchange"]), float(r["funding_rate"]))))
            except Exception:
                continue
        if not series:
            return {"score": None, "note": "perp_funding 无核心币样本"}
        detail: Dict[str, Any] = {}
        peak = 0.0
        for base, vals in series.items():
            z = zscore(vals)
            cs = cusum(vals)
            detail[base] = {"z": round(z, 3) if z is not None else None,
                            "latest_8h_pct": round(vals[-1] * 100, 4),
                            "cusum": {"detected": cs["detected"], "direction": cs["direction"]}}
            if z is not None:
                peak = max(peak, abs(z))
        drifting = [b for b, d in detail.items() if (d.get("cusum") or {}).get("detected")]
        score = max(_z_to_unit(peak, soft=2.5, hard=5.0), min(1.0, len(drifting) / max(1, len(detail))) * 0.6)
        return {"score": round(score, 4), "peak_abs_z": round(peak, 3),
                "cusum_drifting": drifting, "by_symbol": detail}

    def _oi_channel(self, symbols: Sequence[str], errors: List[str]) -> Dict[str, Any]:
        from sqlalchemy import bindparam, text

        bases = {str(s).upper() for s in symbols}
        sym_params = sorted(bases | {f"{b}USDT" for b in bases})
        db = _market_db()
        try:
            if not sym_params:
                rows = []
            else:
                stmt = text(
                    "SELECT symbol, ts_ms, open_interest_value FROM position_structure "
                    "WHERE ts_ms >= :since AND symbol IN :syms "
                    "ORDER BY symbol, ts_ms"
                ).bindparams(bindparam("syms", expanding=True))
                try:
                    rows = [dict(r) for r in db.execute(
                        stmt,
                        {"since": now_ms() - self.lookback_h * 3600 * 1000, "syms": sym_params},
                    ).mappings().all()]
                except Exception as exc:
                    errors.append(f"open_interest: {str(exc)[:160]}")
                    rows = []
        finally:
            db.close()
        series: Dict[str, List[float]] = {}
        for r in rows:
            sym = str(r["symbol"] or "").upper()
            base = sym[:-4] if sym.endswith("USDT") else sym
            if base not in bases or not r.get("open_interest_value"):
                continue
            series.setdefault(base, []).append(float(r["open_interest_value"]))
        if not series:
            return {"score": None, "note": "position_structure 无核心币样本"}
        detail: Dict[str, Any] = {}
        peak = 0.0
        for base, vals in series.items():
            if len(vals) < 10:
                continue
            chg = [(vals[i] / vals[i - 1] - 1.0) for i in range(1, len(vals)) if vals[i - 1] > 0]
            z = zscore(chg)
            detail[base] = {"z": round(z, 3) if z is not None else None, "n": len(chg)}
            if z is not None:
                peak = max(peak, abs(z))
        if not detail:
            return {"score": None, "note": "OI 序列过短"}
        return {"score": round(_z_to_unit(peak, soft=2.5, hard=5.0), 4), "peak_abs_z": round(peak, 3), "by_symbol": detail}

    def _liquidation_channel(self, errors: List[str]) -> Dict[str, Any]:
        since = now_ms() - self.lookback_h * 3600 * 1000
        db = _market_db()
        try:
            rows = _rows(
                db,
                "SELECT (ts_ms / 3600000) AS hour_bucket, SUM(notional_usd) AS usd, COUNT(*) AS n "
                "FROM liquidation_ticks WHERE ts_ms >= :since GROUP BY 1 ORDER BY 1",
                {"since": since}, errors, "liquidation",
            )
            earliest = _rows(db, "SELECT MIN(ts_ms) AS mn FROM liquidation_ticks", {}, errors, "liquidation_min")
        finally:
            db.close()
        # 采集器已覆盖的小时里"没有清算"是真实信息 → 补 0；采集器上线前的小时不补（不造数）。
        by_hour = {int(r["hour_bucket"]): float(r["usd"] or 0) for r in rows}
        mn = (earliest[0].get("mn") if earliest else None)
        vals: List[float] = []
        if by_hour and mn:
            lo = max(int(since) // 3600000, int(mn) // 3600000)
            hi = now_ms() // 3600000 - 1     # 排除当前未走完的小时
            if hi >= lo:
                vals = [by_hour.get(h, 0.0) for h in range(lo, hi + 1)]
        if len(vals) < 12:
            return {"score": None, "note": f"清算样本不足（覆盖 {len(vals)} 小时 < 12）"}
        z = zscore(vals)
        cs = cusum(vals)
        score = max(_z_to_unit(z, soft=2.5, hard=5.0), 0.6 if (cs["detected"] and cs["direction"] > 0) else 0.0)
        return {
            "score": round(score, 4),
            "latest_hour_usd": round(vals[-1], 2),
            "median_hour_usd": round(sorted(vals)[len(vals) // 2], 2),
            "zscore": round(z, 3) if z is not None else None,
            "cusum": cs,
            "n_hours": len(vals),
        }

    def _freshness_channel(self, errors: List[str]) -> Dict[str, Any]:
        stale: List[Dict[str, Any]] = []
        sources = 0
        try:
            from backend.services.events.market_events_store import latest_by_source

            for src, info in (latest_by_source() or {}).items():
                sources += 1
                age_sec = info.get("age_sec")
                if age_sec is None:
                    continue
                age_h = float(age_sec) / 3600.0
                if age_h > self.stale_hours:
                    stale.append({"source": src, "age_h": round(age_h, 2)})
        except Exception as exc:
            errors.append(f"freshness.events: {str(exc)[:120]}")
        failing = 0
        n_jobs = 0
        try:
            from backend.services.ops.job_registry import list_jobs

            for j in list_jobs() or []:
                if j.get("scheduler_only") or not j.get("enabled", True):
                    continue
                n_jobs += 1
                if str(j.get("last_status") or "").lower() in ("error", "failed") or j.get("stale"):
                    failing += 1
        except Exception as exc:
            errors.append(f"freshness.jobs: {str(exc)[:120]}")
        if sources == 0 and n_jobs == 0:
            return {"score": None, "note": "无数据新鲜度样本"}
        stale_ratio = (len(stale) / sources) if sources else 0.0
        score = min(1.0, stale_ratio + min(0.5, failing * 0.1))
        return {"score": round(score, 4), "stale_sources": stale, "n_sources": sources,
                "failing_jobs": failing, "n_jobs": n_jobs, "stale_threshold_h": self.stale_hours}

    # ---- 合成 ----
    def _hard_triggers(self, price: Dict[str, Any]) -> List[Dict[str, Any]]:
        """方案第七节的闪崩硬触发；与 RiskEngine 同阈值，Agent 只负责"提出"。"""
        out: List[Dict[str, Any]] = []
        r1 = price.get("ret_1h_pct")
        r4 = price.get("ret_4h_pct")
        if r1 is not None and float(r1) <= -abs(self.crash_1h_pct):
            out.append({"rule": "btc_1h_crash", "value_pct": r1, "threshold_pct": -abs(self.crash_1h_pct)})
        if r4 is not None and float(r4) <= -abs(self.crash_4h_pct):
            out.append({"rule": "btc_4h_crash", "value_pct": r4, "threshold_pct": -abs(self.crash_4h_pct)})
        return out

    def _suggest_state(self, stress: float, hard: List[Dict[str, Any]], coverage: float) -> Tuple[str, str]:
        if hard:
            return "reducing", f"闪崩硬触发：{', '.join(h['rule'] for h in hard)}"
        if coverage < 0.5:
            return "active", f"通道覆盖率 {coverage:.2f} < 0.5，证据不足不建议降档"
        if stress >= self.halt_threshold:
            return "halted", f"stress={stress:.2f} ≥ {self.halt_threshold}"
        if stress >= self.reduce_threshold:
            return "reducing", f"stress={stress:.2f} ≥ {self.reduce_threshold}"
        return "active", f"stress={stress:.2f} 低于减仓线 {self.reduce_threshold}"

    # ---------------- 预测 ----------------
    def predict(self, findings: Dict[str, Any]) -> List[Prediction]:
        if findings.get("channel_coverage", 0) <= 0:
            return []
        stress = float(findings.get("stress_score") or 0.0)
        suggested = str(findings.get("suggested_state") or "active")
        # 置信度：ACTIVE 建议的把握 = 1 − stress；减仓建议的把握 = stress
        conf = (1.0 - stress) if suggested == "active" else stress
        return [Prediction(
            kind=KIND_TRADING_STATE,
            subject="portfolio",
            prediction={
                "state": suggested,
                "stress_score": stress,
                "window_h": self.window_h,
                "stress_dd_pct": self.stress_dd_pct,
                "hard_triggers": findings.get("hard_triggers"),
                "channels": {k: (v or {}).get("score") for k, v in (findings.get("channels") or {}).items()},
            },
            horizon_ms=int(self.window_h * 3600 * 1000),
            confidence=round(max(0.05, min(0.95, conf)), 3),
        )]

    # ---------------- 建议 ----------------
    def advise(self, findings: Dict[str, Any]) -> List[Advice]:
        suggested = str(findings.get("suggested_state") or "active")
        current = str(findings.get("current_state") or "active")
        if suggested == current or suggested == "active":
            return []
        return [Advice(
            action="set_trading_state",
            target=suggested,
            severity=5 if suggested == "halted" else 3,
            params={
                "state": suggested,
                "stress_score": findings.get("stress_score"),
                "hard_triggers": findings.get("hard_triggers"),
                "current_state": current,
                "requires_confirmation": "双模型或规则确认（方案第七节）",
            },
            reason=str(findings.get("suggest_reason") or ""),
        )]

    def apply_advice(self, adv: Advice, findings: Dict[str, Any]) -> str:
        """act 模式才会被调用；Phase 1 max_mode=advise，因此此分支不可达（留给 Phase 3 晋升）。"""
        return "Anomaly Agent 不直接写 TradingState：需 RiskEngine 规则或双模型确认后由 ops 接口执行"

    # ---------------- LLM 归因层（默认关闭） ----------------
    def _llm_layer(self, channels: Dict[str, Any], stress: float, suggested: str,
                   errors: List[str], hard: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
        if not env_true("AGENT_LLM_ENABLED", False):
            return None
        # [2026-09-04] 本 Agent 每 30 分钟跑一次，而 stress 长期在 0.2 上下（实测 0.19，
        # 减仓线 0.55）。无差别调用 = 每天 48 轮 × 2 个模型 = 96 次推理，绝大多数是让
        # 模型复述「一切正常」，白占 GPU 还把单轮耗时从 45s 推到 60s+。归因只在异常有
        # 苗头时才有信息量，故设门槛；硬触发无视门槛，必须解释。
        min_stress = env_float("AGENT_ANOMALY_LLM_MIN_STRESS", 0.35)
        if not hard and float(stress or 0.0) < min_stress:
            return {"skipped": True,
                    "reason": f"stress={float(stress or 0.0):.2f} < {min_stress}（无异常苗头，不调用模型）"}
        try:
            import json as _json

            from backend.services.analysis.model_gateway import get_model_gateway

            system = (
                "你是风控异常归因分析员。只依据给出的通道指标作答，禁止编造未给出的数字；"
                "解释异常最可能的成因，并指出该建议可能出错的情形。输出单个 JSON 对象，不要 Markdown。"
            )
            user = "【通道检测结果】\n%s\n【合成】stress=%.3f 建议=%s" % (
                _json.dumps(channels, ensure_ascii=False, default=str), stress, suggested)
            cres = get_model_gateway().dual_call("anomaly", system, user, max_output_tokens=1200, timeout_s=150.0)
            return {"accepted": cres.accepted, "consensus_score": cres.consensus_score,
                    "run_id": cres.run_id, "final": cres.final if cres.accepted else None}
        except Exception as exc:
            errors.append(f"llm_layer: {exc}")
            return None


def build() -> AnomalyAgent:
    return AnomalyAgent()


# ─────────────────────────── 到期评分器 ───────────────────────────
def score_trading_state(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """kind=trading_state 的 outcome 评估器。

    实际压力 = 预测窗口内 BTC 1h 最大回撤 ≥ stress_dd_pct。
    建议减仓（reducing/halted）且确有压力 → 1.0；建议 active 且平静 → 1.0；否则 0.0。
    取不到 BTC K 线 → None（保持 open，48h 后 void）。
    """
    pred = row.get("prediction") or {}
    win = btc_stress_window(int(row["created_ms"]), int(row["expires_ms"]))
    if not win:
        return None
    threshold = float(pred.get("stress_dd_pct") or 4.0)
    stressed = float(win["max_drawdown_pct"]) >= threshold
    suggested = str(pred.get("state") or "active").lower()
    defensive = suggested in ("reducing", "halted")
    score = 1.0 if (defensive == stressed) else 0.0
    return {
        "score": score,
        "outcome": {
            "suggested_state": suggested,
            "actual_stressed": stressed,
            "threshold_dd_pct": threshold,
            "btc_max_drawdown_pct": win["max_drawdown_pct"],
            "btc_ret_pct": win["ret_pct"],
            "n_bars": win["n_bars"],
        },
    }
