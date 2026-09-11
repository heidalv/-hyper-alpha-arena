# -*- coding: utf-8 -*-
"""Timing / Regime Agent — 策略择时（v3 方向 3，p1-agents-a）。

确定性核心（只读 1d K 线，纯计算）：

  L1 计数        8 主流币各自 `trend_layer.classify()` 的 up / down 个数（与 E1 同一套结构信号）
  BTC 制度       收盘 > EMA200？
  实现波动       BTC 60d 日收益标准差 × √365
  动能           BTC ADX（trend_layer 的 ADX 数值，非投票）
  流动性         核心币近 7d 平均成交量 / 90d 平均成交量（枯竭代理）

  → regime ∈ {trend_up, trend_down, range, high_vol, low_liquidity, unknown}（与 schemas.REGIMES 同名同义）
  → 三桶资本建议 bucket_weights{trend, cashflow, research} + 各引擎（E1/E2/E5/E3）权重建议

**观察模式铁律**：只落 `agent_predictions`（kind=`regime`）与
`backend/data/agents/latest_timing_weights.json`，**绝不写 runtime_tuning**。
E4 分配器（Phase 2/3）读该文件时须自行判断 Agent 可信度是否达标。

预测评分（`score_regime`）：到期用**同一个确定性分类器**在到期时点重算 regime
（1d 数据截断到 expires 当天），完全一致 1.0、同族不同名 0.5、判反 0.0。
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.services.agents.base import (
    Advice,
    ObservationAgent,
    Prediction,
    env_float,
    env_int,
    env_true,
    now_ms,
    write_latest,
)

logger = logging.getLogger(__name__)

AGENT_ID = "timing"
KIND_REGIME = "regime"

# regime → 三桶权重（方案第八节默认 60/30/10 为 trend_up 时的基准）
REGIME_BUCKETS: Dict[str, Dict[str, float]] = {
    "trend_up":       {"trend": 0.60, "cashflow": 0.30, "research": 0.10},
    "range":          {"trend": 0.35, "cashflow": 0.50, "research": 0.15},
    "high_vol":       {"trend": 0.25, "cashflow": 0.55, "research": 0.20},
    "trend_down":     {"trend": 0.15, "cashflow": 0.65, "research": 0.20},
    "low_liquidity":  {"trend": 0.20, "cashflow": 0.60, "research": 0.20},
    "unknown":        {"trend": 0.30, "cashflow": 0.55, "research": 0.15},
}

# 评分用的 regime 家族（同族判错只扣一半）
REGIME_FAMILY: Dict[str, str] = {
    "trend_up": "risk_on",
    "range": "neutral",
    "low_liquidity": "neutral",
    "trend_down": "risk_off",
    "high_vol": "risk_off",
    "unknown": "neutral",
}


def _engine_capital(regime: str, buckets: Dict[str, float]) -> List[Dict[str, Any]]:
    """三桶 → 引擎级建议。E1 吃趋势桶；E2a/E2b 吃现金流桶；E5 事件与 E3 挑战者吃研究桶。"""
    trend = float(buckets.get("trend", 0.0))
    cash = float(buckets.get("cashflow", 0.0))
    research = float(buckets.get("research", 0.0))
    return [
        {"engine": "E1", "weight": round(trend, 3), "rationale": f"趋势桶全额给 E1（regime={regime}）"},
        {"engine": "E2", "weight": round(cash, 3), "rationale": "现金流桶：Aster T&E + carry + 理财"},
        {"engine": "E5", "weight": round(research * 0.6, 3), "rationale": "研究桶 60% 给事件影子车道"},
        {"engine": "E3", "weight": round(research * 0.4, 3), "rationale": "研究桶 40% 给挑战者/参数寻优"},
    ]


def load_daily(symbols: Sequence[str], *, until_ms: Optional[int] = None):
    """核心币 1d OHLC；until_ms 给定时截断到该时刻（评分器回放用，避免前视）。"""
    import pandas as pd  # noqa: F401  （load_daily_ohlc 返回 DataFrame）

    from backend.research.trend_sleeve_backtest import load_daily_ohlc

    data = load_daily_ohlc(list(symbols))
    if until_ms is None:
        return data
    cutoff = int(until_ms // 1000)
    out = {}
    for sym, df in data.items():
        if df is None or df.empty:
            continue
        try:
            ts = (df.index.view("int64") // 10**9) if hasattr(df.index, "view") else None
        except Exception:
            ts = None
        if ts is None:
            out[sym] = df
            continue
        out[sym] = df[ts <= cutoff]
    return out


def load_daily_volume(symbols: Sequence[str], *, until_ms: Optional[int] = None, days: int = 120,
                      exchange: str = "binance") -> Dict[str, List[float]]:
    """核心币 1d 成交量序列（升序）。`load_daily_ohlc` 只取 OHLC，流动性通道单独查这一列。"""
    from sqlalchemy import bindparam, text

    from backend.database.connection import MarketSessionLocal

    syms = [str(s).upper() for s in symbols]
    out: Dict[str, List[float]] = {s: [] for s in syms}
    if not syms:
        return out
    hi = int((until_ms or now_ms()) // 1000)
    lo = hi - int(days) * 86400
    db = MarketSessionLocal()
    try:
        stmt = text(
            "SELECT symbol, timestamp, volume FROM crypto_klines WHERE exchange = :ex AND period = '1d' "
            "AND symbol IN :syms AND timestamp >= :lo AND timestamp <= :hi ORDER BY symbol, timestamp"
        ).bindparams(bindparam("syms", expanding=True))
        for r in db.execute(stmt, {"ex": exchange, "syms": syms, "lo": lo, "hi": hi}).mappings().all():
            if r["volume"] is not None:
                out.setdefault(str(r["symbol"]).upper(), []).append(float(r["volume"]))
    except Exception as exc:
        logger.debug("[Timing] load_daily_volume 失败: %s", exc)
    finally:
        db.close()
    return out


def classify_regime(data: Dict[str, Any], *, high_vol_pct: float = 100.0, min_up: int = 5,
                    liquidity_ratio_floor: float = 0.35,
                    volumes: Optional[Dict[str, List[float]]] = None) -> Dict[str, Any]:
    """确定性 regime 分类器（预测与评分共用同一函数，保证可复算）。"""
    from backend.services.trend_layer import _signals, classify

    up = down = side = 0
    per_symbol: Dict[str, Any] = {}
    for sym, df in (data or {}).items():
        if df is None or len(df) < 60:
            per_symbol[sym] = {"state": "insufficient", "n": 0 if df is None else len(df)}
            continue
        c = classify(df)
        per_symbol[sym] = {"state": c["state"], "score": c["score"], "strength": c["strength"]}
        if c["state"] == "up":
            up += 1
        elif c["state"] == "down":
            down += 1
        else:
            side += 1

    btc = (data or {}).get("BTC")
    btc_above_ema200 = None
    btc_vol_pct = None
    btc_adx = None
    if btc is not None and len(btc) >= 200:
        close = btc["close"].astype(float)
        ema200 = close.ewm(span=200, adjust=False).mean()
        btc_above_ema200 = bool(close.iloc[-1] > ema200.iloc[-1])
        ret = close.pct_change(fill_method=None).dropna()
        if len(ret) >= 60:
            btc_vol_pct = float(ret.tail(60).std() * math.sqrt(365.0) * 100.0)
        try:
            btc_adx = float(_signals(btc)["adx_v"].iloc[-1])
        except Exception:
            btc_adx = None

    # 流动性枯竭代理：核心币 7d 均量 / 90d 均量
    liq_ratio = None
    ratios: List[float] = []
    for sym, vols in (volumes or {}).items():
        vals = [v for v in vols if v and v > 0]
        if len(vals) < 90:
            continue
        long_avg = sum(vals[-90:]) / 90.0
        short_avg = sum(vals[-7:]) / 7.0
        if long_avg > 0:
            ratios.append(short_avg / long_avg)
    if ratios:
        liq_ratio = sum(ratios) / len(ratios)

    # 优先级：无数据 > 高波动 > 明确趋势 > 流动性枯竭 > 震荡。
    # 趋势排在流动性前面，是因为加密市场周末/淡季缩量很常见，缩量本身不应盖过一致的方向信号。
    rated = up + down + side
    # 一致方向门槛：8 币时 = 4（方案 E4 的 "L1=up ≥ 4"）；可评级币变少时按半数取，但不低于 3。
    need = max(3, min(int(min_up), -(-rated // 2))) if rated else int(min_up)
    if rated == 0:
        regime, reason, conf = "unknown", "无足够 1d 数据判定任何核心币", 0.2
    elif btc_vol_pct is not None and btc_vol_pct >= high_vol_pct:
        regime = "high_vol"
        reason = f"BTC 60d 实现波动 {btc_vol_pct:.0f}% ≥ {high_vol_pct:.0f}%"
        conf = min(0.9, 0.5 + (btc_vol_pct - high_vol_pct) / 200.0)
    elif up >= need and up > down and btc_above_ema200:
        regime = "trend_up"
        reason = f"{up}/{rated} 币 L1=up（门槛 {need}）且 BTC>EMA200"
        conf = min(0.9, 0.45 + 0.08 * up)
    elif down >= need and down > up and btc_above_ema200 is False:
        regime = "trend_down"
        reason = f"{down}/{rated} 币 L1=down（门槛 {need}）且 BTC<EMA200"
        conf = min(0.9, 0.45 + 0.08 * down)
    elif liq_ratio is not None and liq_ratio < liquidity_ratio_floor:
        regime = "low_liquidity"
        reason = f"无一致方向且核心币 7d/90d 均量比 {liq_ratio:.2f} < {liquidity_ratio_floor}"
        conf = 0.6
    else:
        regime = "range"
        reason = f"up={up} down={down} sideways={side}，无一致方向"
        conf = 0.5

    return {
        "regime": regime,
        "confidence": round(conf, 3),
        "reason": reason,
        "up_count": up,
        "down_count": down,
        "sideways_count": side,
        "rated_symbols": rated,
        "consensus_needed": need,
        "btc_above_ema200": btc_above_ema200,
        "btc_realized_vol_pct": round(btc_vol_pct, 2) if btc_vol_pct is not None else None,
        "btc_adx": round(btc_adx, 2) if btc_adx is not None else None,
        "liquidity_ratio_7d_90d": round(liq_ratio, 3) if liq_ratio is not None else None,
        "per_symbol": per_symbol,
    }


class TimingAgent(ObservationAgent):
    agent_id = AGENT_ID
    description = "regime 分类（L1 计数 / EMA200 / 实现波动 / ADX / 流动性）→ 三桶与引擎资本建议"
    kinds = (KIND_REGIME,)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.window_h = env_float("AGENT_TIMING_WINDOW_H", 168.0)   # 预测窗口默认 7d
        self.high_vol_pct = env_float("AGENT_TIMING_HIGH_VOL_PCT", 100.0)
        self.min_up = env_int("AGENT_TIMING_MIN_UP", 5)
        self.liq_floor = env_float("AGENT_TIMING_LIQ_FLOOR", 0.35)

    def analyze(self, errors: List[str]) -> Dict[str, Any]:
        from backend.services.trend_core import core_symbols

        symbols = core_symbols()
        try:
            data = load_daily(symbols)
        except Exception as exc:
            errors.append(f"load_daily: {str(exc)[:160]}")
            data = {}
        if not data:
            errors.append("1d K 线为空，regime 判定退化为 unknown")
        try:
            volumes = load_daily_volume(symbols)
        except Exception as exc:
            errors.append(f"load_daily_volume: {str(exc)[:120]}")
            volumes = {}
        cls = classify_regime(data, high_vol_pct=self.high_vol_pct, min_up=self.min_up,
                              liquidity_ratio_floor=self.liq_floor, volumes=volumes)
        regime = cls["regime"]
        buckets = dict(REGIME_BUCKETS.get(regime, REGIME_BUCKETS["unknown"]))
        engines = _engine_capital(regime, buckets)
        findings = {
            "symbols": symbols,
            **cls,
            "bucket_weights": buckets,
            "engine_capital": engines,
            "params": {"high_vol_pct": self.high_vol_pct, "min_up": self.min_up, "liq_floor": self.liq_floor},
            "counterfactual": self._counterfactual(errors),
        }
        findings["llm"] = self._llm_layer(findings, errors)
        # 建议落独立文件（E4 分配器读；观察模式下**不写** runtime_tuning）
        if not self.dry_run:
            write_latest("timing_weights", {
                "ts_ms": now_ms(),
                "agent": AGENT_ID,
                "mode_note": "观察模式产物，未生效；E4 消费前需核对 /api/agents/status 的可信度",
                "regime": regime,
                "regime_confidence": cls["confidence"],
                "bucket_weights": buckets,
                "engine_capital": engines,
                "reason": cls["reason"],
            })
        return findings

    def _counterfactual(self, errors: List[str]) -> Optional[Dict[str, Any]]:
        """上一条已评分的 regime 预测对不对（方案要求择时带反事实栏）。"""
        try:
            from backend.services.analysis import ledgers

            rows = ledgers.list_predictions(agent=AGENT_ID, kind=KIND_REGIME, status="scored", limit=5)
        except Exception as exc:
            errors.append(f"counterfactual: {str(exc)[:120]}")
            return None
        if not rows:
            return {"note": "尚无已评分的 regime 预测"}
        last = rows[0]
        return {
            "last_predicted": (last.get("prediction") or {}).get("regime"),
            "last_actual": (last.get("outcome") or {}).get("actual_regime"),
            "last_score": last.get("score"),
            "scored_ms": last.get("scored_ms"),
        }

    def predict(self, findings: Dict[str, Any]) -> List[Prediction]:
        if int(findings.get("rated_symbols") or 0) == 0:
            return []
        return [Prediction(
            kind=KIND_REGIME,
            subject="portfolio",
            prediction={
                "regime": findings["regime"],
                "up_count": findings.get("up_count"),
                "down_count": findings.get("down_count"),
                "btc_above_ema200": findings.get("btc_above_ema200"),
                "bucket_weights": findings.get("bucket_weights"),
                "params": findings.get("params"),
                "window_h": self.window_h,
            },
            horizon_ms=int(self.window_h * 3600 * 1000),
            confidence=float(findings.get("confidence") or 0.5),
        )]

    def advise(self, findings: Dict[str, Any]) -> List[Advice]:
        return [Advice(
            action="set_bucket_weights",
            target="capital_allocator",
            severity=2,
            params={"bucket_weights": findings.get("bucket_weights"),
                    "engine_capital": findings.get("engine_capital"),
                    "regime": findings.get("regime")},
            reason=str(findings.get("reason") or ""),
        )]

    def apply_advice(self, adv: Advice, findings: Dict[str, Any]) -> str:
        return "Timing Agent 不直接写 runtime_tuning：三桶权重由 E4 capital_allocator 统一落地"

    def _llm_layer(self, findings: Dict[str, Any], errors: List[str]) -> Optional[Dict[str, Any]]:
        if not env_true("AGENT_LLM_ENABLED", False):
            return None
        try:
            import json as _json

            from backend.services.analysis.model_gateway import get_model_gateway

            system = (
                "你是策略择时分析员。只依据给出的 regime 指标作答，禁止编造未给出的数字；"
                "写一段周度叙述并指出该 regime 判定最可能出错的情形。输出单个 JSON 对象，不要 Markdown。"
            )
            payload = {k: findings.get(k) for k in
                       ("regime", "confidence", "reason", "up_count", "down_count", "btc_above_ema200",
                        "btc_realized_vol_pct", "btc_adx", "liquidity_ratio_7d_90d", "bucket_weights")}
            user = "【regime 指标】\n" + _json.dumps(payload, ensure_ascii=False, default=str)
            cres = get_model_gateway().dual_call("timing", system, user, max_output_tokens=1400, timeout_s=180.0)
            return {"accepted": cres.accepted, "consensus_score": cres.consensus_score,
                    "run_id": cres.run_id, "final": cres.final if cres.accepted else None}
        except Exception as exc:
            errors.append(f"llm_layer: {exc}")
            return None


def build() -> TimingAgent:
    return TimingAgent()


# ─────────────────────────── 到期评分器 ───────────────────────────
def score_regime(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """kind=regime 的 outcome 评估器：到期时点用同一分类器重算 regime 并比对。"""
    pred = row.get("prediction") or {}
    predicted = str(pred.get("regime") or "").strip().lower()
    if not predicted:
        return {"score": 0.0, "outcome": {"error": "预测缺少 regime"}}
    params = pred.get("params") or {}
    try:
        from backend.services.trend_core import core_symbols

        syms = core_symbols()
        expires = int(row["expires_ms"])
        data = load_daily(syms, until_ms=expires)
        volumes = load_daily_volume(syms, until_ms=expires)
    except Exception as exc:
        logger.debug("[Timing] score_regime 取数失败: %s", exc)
        return None
    if not data or all((df is None or len(df) < 60) for df in data.values()):
        return None
    actual = classify_regime(
        data,
        high_vol_pct=float(params.get("high_vol_pct") or 100.0),
        min_up=int(params.get("min_up") or 5),
        liquidity_ratio_floor=float(params.get("liq_floor") or 0.35),
        volumes=volumes,
    )
    actual_regime = actual["regime"]
    if actual_regime == predicted:
        score = 1.0
    elif REGIME_FAMILY.get(actual_regime) == REGIME_FAMILY.get(predicted):
        score = 0.5
    else:
        score = 0.0
    return {
        "score": score,
        "outcome": {
            "predicted_regime": predicted,
            "actual_regime": actual_regime,
            "actual_reason": actual["reason"],
            "actual_up_count": actual.get("up_count"),
            "actual_down_count": actual.get("down_count"),
            "family_match": REGIME_FAMILY.get(actual_regime) == REGIME_FAMILY.get(predicted),
        },
    }
