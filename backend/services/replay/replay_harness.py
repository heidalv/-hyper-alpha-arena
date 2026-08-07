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
        mkt: Dict[str, Any] = {
            "symbol": symbol,
            "price": close,
            "current_price": close,
            "volatility_value": 0.015,
        }
        if idx >= 14:
            closes = [float(b.get("close") or 0) for b in all_bars[idx - 14 : idx + 1]]
            if closes[0] > 0:
                mkt["change_1h_pct"] = (closes[-1] - closes[0]) / closes[0] * 100
        # mid/long 注入占位指标块（replay 简化）
        if tier in ("mid", "long"):
            mkt["indicators_1h"] = {"rsi": 50, "close": close}
            mkt["indicators_4h"] = {"rsi": 50, "close": close}
            mkt["indicators_1d"] = {"rsi": 50, "close": close}
        if tier == "long":
            mkt["indicators_1w"] = {"rsi": 50, "close": close}
        mkt.setdefault("orchestrator", {"mid_bias": "neutral", "long_bias": "neutral"})
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
