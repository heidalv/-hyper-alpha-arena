"""ReplayHarness — 回测与 AI 主链共用 evaluate 管道（MVP）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReplayTrade:
    symbol: str
    tier: str
    action: str
    bar_index: int
    allowed: bool
    reason: str
    confidence: float = 0.0


@dataclass
class ReplayReport:
    symbol: str
    tier: str
    bars: int = 0
    proposals: int = 0
    allowed: int = 0
    blocked: int = 0
    block_reasons: Dict[str, int] = field(default_factory=dict)
    trades: List[ReplayTrade] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "tier": self.tier,
            "bars": self.bars,
            "proposals": self.proposals,
            "allowed": self.allowed,
            "blocked": self.blocked,
            "block_reasons": self.block_reasons,
            "allow_rate": round(self.allowed / max(1, self.proposals), 3),
            "trade_count": len([t for t in self.trades if t.allowed]),
        }


@dataclass
class BatchReplayReport:
    """多标的 × 多 tier 的聚合回放报告（三周期全覆盖）。"""
    symbols: List[str] = field(default_factory=list)
    tiers: List[str] = field(default_factory=list)
    reports: Dict[str, ReplayReport] = field(default_factory=dict)  # key="SYM:tier"
    total_bars: int = 0
    total_proposals: int = 0
    total_allowed: int = 0
    total_blocked: int = 0
    block_reasons: Dict[str, int] = field(default_factory=dict)
    per_tier: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbols": self.symbols,
            "tiers": self.tiers,
            "total_bars": self.total_bars,
            "total_proposals": self.total_proposals,
            "total_allowed": self.total_allowed,
            "total_blocked": self.total_blocked,
            "allow_rate": round(self.total_allowed / max(1, self.total_proposals), 3),
            "block_reasons": self.block_reasons,
            "per_tier": self.per_tier,
            "reports": {k: r.to_dict() for k, r in self.reports.items()},
        }


def _replay_rsi(all_bars: List[dict], idx: int, period: int = 14):
    """真实 RSI（Wilder 平滑）计算，数据不足返回 None。

    [2026-08-15] 供 replay 指标注入使用——用真实序列替代 RSI=50 占位。
    """
    try:
        if idx + 1 < period + 1:
            return None
        closes = [float(b.get("close") or 0) for b in all_bars[idx - period: idx + 1]]
        closes = [c for c in closes if c > 0]
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)
    except Exception:
        return None


def _replay_volatility(all_bars: List[dict], idx: int, window: int = 24):
    """真实收益波动率（对数收益 std，年化 ×√24 近似），数据不足返回 None。"""
    import math
    try:
        if idx + 1 < window + 1:
            return None
        closes = [float(b.get("close") or 0) for b in all_bars[idx - window: idx + 1]]
        closes = [c for c in closes if c > 0]
        if len(closes) < window:
            return None
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
        std = math.sqrt(var)
        return round(min(2.0, std * math.sqrt(24)), 4)
    except Exception:
        return None


class ReplayHarness:
    """历史 K 线回放 → Proposal → evaluate_proposal（无 LLM / 无下单）。"""

    def __init__(self, *, mode: str = "backtest"):
        self.mode = mode

    def run(
        self,
        symbol: str,
        tier: str = "mid",
        *,
        db=None,
        account_id: int = 0,
        bars: Optional[List[dict]] = None,
        min_confidence: float = 48.0,
        proposer: str = "rule",
    ) -> ReplayReport:
        sym = str(symbol).upper()
        tier = (tier or "mid").lower()
        report = ReplayReport(symbol=sym, tier=tier)

        if bars is None:
            bars = self._load_bars(sym, tier)
        report.bars = len(bars or [])
        if not bars:
            logger.warning("[ReplayHarness] 无 K 线 %s tier=%s", sym, tier)
            return report

        from backend.services.decision_core.proposal import TradeProposal
        from backend.services.decision_core.execute_proposal import evaluate_proposal

        tf = {"short": "15m", "mid": "4h", "long": "1d"}.get(tier, "4h")

        for i, bar in enumerate(bars):
            close = float(bar.get("close") or 0)
            if close <= 0:
                continue
            market_data = self._bar_to_market(sym, bar, tier, bars, i)
            if proposer == "atas":
                from backend.services.replay.atas_proposer import (
                    atas_factor_to_proposal,
                    load_atas_market_overlay,
                )
                market_data.update(load_atas_market_overlay(sym, db))
                proposal, p_reason = atas_factor_to_proposal(sym, market_data, tier=tier)
                if not proposal:
                    continue
                report.proposals += 1
                conf = float(proposal.confidence)
                action = proposal.action
            else:
                action, conf = self._rule_propose(market_data, tier)
                if not action:
                    continue
                report.proposals += 1
                proposal = TradeProposal.from_agent(
                    sym=sym,
                    tier=tier,
                    action=action,
                    confidence=conf,
                    trade_nature={"short": "scalp", "mid": "swing", "long": "trend_follow"}.get(tier, "swing"),
                    source_lane="replay_rule",
                    reasoning=f"replay bar={i}",
                )
            if conf < min_confidence:
                verdict_allowed = False
                reason = f"conf={conf:.0f}<{min_confidence:.0f}"
            else:
                verdict = evaluate_proposal(
                    db=db,
                    account_id=account_id,
                    proposal=proposal,
                    market_data=market_data,
                    mode=self.mode,
                    persistence_allow=True,
                )
                verdict_allowed = verdict.allowed
                reason = verdict.reason

            if verdict_allowed:
                report.allowed += 1
            else:
                report.blocked += 1
                key = reason.split("]")[0] + "]" if "]" in reason else reason[:40]
                report.block_reasons[key] = report.block_reasons.get(key, 0) + 1

            report.trades.append(
                ReplayTrade(
                    symbol=sym,
                    tier=tier,
                    action=action,
                    bar_index=i,
                    allowed=verdict_allowed,
                    reason=reason,
                    confidence=conf,
                )
            )
        return report

    def run_batch(
        self,
        symbols: List[str],
        tiers: Optional[List[str]] = None,
        *,
        db=None,
        account_id: int = 0,
        min_confidence: float = 48.0,
        proposer: str = "rule",
    ) -> BatchReplayReport:
        """三周期全覆盖回放：对每个 symbol × 每个 tier 各跑一遍并聚合。

        2026-07-06 整改（P2 ReplayHarness 全覆盖）：此前只有单 symbol + 单 tier 的
        `run()`（审查报告指出"覆盖面窄：单 symbol + mid tier"）。本方法按审查报告
        §7.3/#18 要求覆盖 short/mid/long 三个 tier，并给出组合级聚合（总放行率 +
        合并的 block 原因分布 + 分 tier 汇总），可作为上线前的全链路安全网。
        """
        tiers = [t.lower() for t in (tiers or ["short", "mid", "long"])]
        syms = [str(s).upper() for s in (symbols or [])]
        batch = BatchReplayReport(symbols=syms, tiers=tiers)

        for sym in syms:
            for tier in tiers:
                rep = self.run(
                    sym, tier,
                    db=db, account_id=account_id,
                    min_confidence=min_confidence, proposer=proposer,
                )
                batch.reports[f"{sym}:{tier}"] = rep
                batch.total_bars += rep.bars
                batch.total_proposals += rep.proposals
                batch.total_allowed += rep.allowed
                batch.total_blocked += rep.blocked
                for k, v in rep.block_reasons.items():
                    batch.block_reasons[k] = batch.block_reasons.get(k, 0) + v
                pt = batch.per_tier.setdefault(
                    tier, {"proposals": 0, "allowed": 0, "blocked": 0}
                )
                pt["proposals"] += rep.proposals
                pt["allowed"] += rep.allowed
                pt["blocked"] += rep.blocked

        logger.info(
            "[ReplayHarness] batch 完成: %d symbols × %d tiers, proposals=%d allow=%d block=%d",
            len(syms), len(tiers), batch.total_proposals,
            batch.total_allowed, batch.total_blocked,
        )
        return batch

    def _load_bars(self, symbol: str, tier: str) -> List[dict]:
        tf = {"short": "15m", "mid": "4h", "long": "1d"}.get(tier, "4h")
        try:
            from backend.services.kline_data_service import kline_service
            raw = kline_service.get_klines_from_db(symbol, tf, count=120) or []
            return list(raw)[-60:]
        except Exception as err:
            logger.debug("[ReplayHarness] load bars: %s", err)
            return []

    def _bar_to_market(
        self, symbol: str, bar: dict, tier: str, all_bars: List[dict], idx: int
    ) -> dict:
        close = float(bar.get("close") or 0)
        # [2026-08-15 消费端验收] volatility 不再硬编码 0.015：有足够历史时
        # 用真实收益波动率；历史不足时标注估计值（volatility_is_estimate）。
        _real_vol = _replay_volatility(all_bars, idx)
        mkt: Dict[str, Any] = {
            "symbol": symbol,
            "price": close,
            "current_price": close,
            "volatility_value": _real_vol if _real_vol is not None else 0.015,
            "volatility_is_estimate": _real_vol is None,
        }
        if idx >= 14:
            closes = [float(b.get("close") or 0) for b in all_bars[idx - 14 : idx + 1]]
            if closes[0] > 0:
                mkt["change_1h_pct"] = (closes[-1] - closes[0]) / closes[0] * 100
        # [2026-08-15 消费端验收] mid/long 指标块不再伪造 RSI=50：
        # 对回放可用的 bar 序列计算真实 RSI；回放只有单一粒度，4h/1d/1w
        # 数据不存在 → 如实标注 unavailable，不冒充中性 50（原占位会扭曲
        # 回放 allow_rate 等 API 可见结论）。
        if tier in ("mid", "long"):
            _rsi = _replay_rsi(all_bars, idx, 14)
            if _rsi is not None:
                mkt["indicators_1h"] = {"rsi": round(_rsi, 2), "close": close}
            else:
                mkt["indicators_1h"] = {"available": False, "note": "replay 数据不足"}
            mkt["indicators_4h"] = {"available": False, "note": "replay 单粒度无 4h 数据"}
            mkt["indicators_1d"] = {"available": False, "note": "replay 单粒度无 1d 数据"}
        if tier == "long":
            mkt["indicators_1w"] = {"available": False, "note": "replay 单粒度无 1w 数据"}
        # MVP 回放无 orchestrator 实算：如实标注，不冒充真实择时视图
        mkt.setdefault(
            "orchestrator",
            {"mid_bias": "neutral", "long_bias": "neutral",
             "note": "replay_mvp_no_orchestrator"},
        )
        return mkt

    def _rule_propose(self, market_data: dict, tier: str) -> tuple:
        """MVP 规则 proposer。"""
        change = float(market_data.get("change_1h_pct") or 0)
        if change > 1.5:
            return "buy", 55.0
        if change < -1.5:
            return "sell", 55.0
        return "", 0.0


replay_harness = ReplayHarness()
