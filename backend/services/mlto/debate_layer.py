"""Gray-zone Bull/Bear debate (rule-based moderator).

整改#11 追加：AdversarialDebateLayer —— TradingAgents 式牛/熊对抗辩论 + 独立风险角色层，
作为 MLTO quant/qual 辩论的补充防幻觉手段。可选注入 llm_client；无 LLM 时纯规则降级（可测）。
既有 run_debate / persist_debate_log 等函数保持不变。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from backend.services.mlto.types import MemoryEventDTO, PerceptionPacket

logger = logging.getLogger(__name__)


def should_debate(hub_adjusted: float, tier: str) -> bool:
    low = 0.40 if tier == "mid" else 0.45
    high = 0.70 if tier == "mid" else 0.75
    return low <= hub_adjusted < high


def _score_debate(
    packet: PerceptionPacket,
    memory_events: List[MemoryEventDTO],
) -> Tuple[int, int]:
    bull_pts = 0
    bear_pts = 0
    for e in memory_events:
        s = (e.summary or "").lower()
        if e.layer == "deep":
            if any(w in s for w in ("bullish", "long", "buy", "support")):
                bull_pts += 2
            if any(w in s for w in ("bearish", "short", "sell", "resist")):
                bear_pts += 2
        elif "alignment=" in s:
            try:
                align = int(s.split("alignment=")[1].split("/")[0])
                if align >= 6:
                    bull_pts += 1
                elif align <= 2:
                    bear_pts += 1
            except Exception:
                pass
    orch = packet.orchestrator or {}
    if (orch.get("mid_bias") or "").lower() == "bullish" or (orch.get("long_bias") or "").lower() == "bullish":
        bull_pts += 1
    if (orch.get("mid_bias") or "").lower() == "bearish" or (orch.get("long_bias") or "").lower() == "bearish":
        bear_pts += 1
    return bull_pts, bear_pts


def run_debate(
    packet: PerceptionPacket,
    memory_events: List[MemoryEventDTO],
    hub_adjusted: float,
) -> float:
    """Return debate_signal 0-1 without extra LLM (moderator rules).

    整改#11：ADVERSARIAL_DEBATE_ENABLED=true 时叠加 AdversarialDebateLayer 裁决，
    reject→强空、reduce→缩仓倾向；失败自动回退纯规则辩论。
    """
    import os as _os
    if _os.getenv("ADVERSARIAL_DEBATE_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
        try:
            layer = AdversarialDebateLayer()
            proposal = {
                "symbol": packet.symbol,
                "tier": packet.tier,
                "direction": "long" if hub_adjusted >= 0.55 else ("short" if hub_adjusted <= 0.45 else "neutral"),
                "confidence": hub_adjusted,
            }
            context = {
                "orchestrator": packet.orchestrator or {},
                "tier": packet.tier,
                "regime": getattr(packet, "regime_hash", "") or "",
            }
            evidence = [e.summary for e in memory_events[:8] if e.summary]
            adv = layer.debate(proposal, context, evidence)
            if adv.final_verdict == "reject":
                logger.info("[MLTO][Debate#11] 对抗辩论 reject → debate_signal=0.30")
                return 0.30
            sig = 0.5 + adv.net_sentiment * 0.25
            if adv.final_verdict == "reduce":
                sig = min(sig, 0.45)
            sig = max(0.20, min(0.80, sig))
            logger.debug("[MLTO][Debate#11] adversarial sig=%.3f verdict=%s", sig, adv.final_verdict)
            return sig
        except Exception as _adv_err:
            logger.debug("[MLTO][Debate#11] 对抗辩论失败，回退规则: %s", _adv_err)

    bull_pts, bear_pts = _score_debate(packet, memory_events)
    if bull_pts > bear_pts + 1:
        return min(0.75, 0.5 + 0.05 * (bull_pts - bear_pts))
    if bear_pts > bull_pts + 1:
        return max(0.25, 0.5 - 0.05 * (bear_pts - bull_pts))
    return 0.5


def persist_debate_log(
    thesis_id: str,
    packet: PerceptionPacket,
    memory_events: List[MemoryEventDTO],
    hub_adjusted: float,
    debate_signal: float,
    db=None,
) -> int:
    """写入 Bull/Bear 两行 debate_log（验收 #6）。"""
    if db is None:
        return 0
    bull_pts, bear_pts = _score_debate(packet, memory_events)
    cited = [e.event_id for e in memory_events[:6]]
    rows_written = 0
    try:
        from backend.services.mlto.db_models import MltoDebateLog
        for side, pts, stance in (
            ("bull", bull_pts, "long bias evidence"),
            ("bear", bear_pts, "short bias evidence"),
        ):
            db.add(
                MltoDebateLog(
                    debate_id=str(uuid.uuid4()),
                    thesis_id=thesis_id,
                    round_num=1,
                    side=side,
                    content_json=json.dumps(
                        {
                            "points": pts,
                            "hub_adjusted": hub_adjusted,
                            "debate_signal": debate_signal,
                            "stance": stance,
                        },
                        ensure_ascii=False,
                    )[:4000],
                    cited_event_ids_json=json.dumps(cited),
                )
            )
            rows_written += 1
        db.commit()
    except Exception as exc:
        logger.debug("[MLTO] debate log skip: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
    return rows_written


# ============================================================================
# 整改#11：对抗辩论层（TradingAgents 式 bull/bear + 风险角色）
# ============================================================================
LLMClient = Callable[[str], str]   # 约定：接收 prompt 文本，返回模型文本（内含 JSON）


@dataclass
class DebateTurn:
    role: str                       # 'bull'|'bear'|'risk_aggressive'|'risk_conservative'|'risk_neutral'
    argument: str
    evidence: list = field(default_factory=list)   # 证据引用（锚定市场数据）
    confidence: float = 0.5


@dataclass
class AdversarialDebateResult:
    bull_turns: List[DebateTurn] = field(default_factory=list)
    bear_turns: List[DebateTurn] = field(default_factory=list)
    risk_turns: List[DebateTurn] = field(default_factory=list)
    final_verdict: str = "proceed"          # 'proceed'|'reduce'|'reject'
    net_sentiment: float = 0.0              # 综合净倾向 [-1, 1]
    consensus_confidence: float = 0.5

    def as_dict(self) -> dict:
        return {
            "final_verdict": self.final_verdict,
            "net_sentiment": round(self.net_sentiment, 4),
            "consensus_confidence": round(self.consensus_confidence, 4),
            "bull": [t.argument for t in self.bull_turns],
            "bear": [t.argument for t in self.bear_turns],
            "risk": [{"role": t.role, "confidence": t.confidence} for t in self.risk_turns],
        }


def _parse_llm_turn(text: str) -> Tuple[str, float, list]:
    """从 LLM 文本解析 {argument, confidence, evidence}；解析失败给中性兜底。"""
    if not text:
        return ("", 0.5, [])
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            obj = json.loads(m.group(0))
            arg = str(obj.get("argument", "")).strip()
            conf = float(obj.get("confidence", 0.5))
            ev = obj.get("evidence", [])
            if not isinstance(ev, list):
                ev = [str(ev)]
            return (arg or text.strip()[:500], max(0.0, min(1.0, conf)), ev)
    except Exception:  # noqa: BLE001
        pass
    return (text.strip()[:500], 0.5, [])


class AdversarialDebateLayer:
    """两层对抗辩论。

    Layer 1: 牛 vs 熊（交易论点）—— 最多 max_rounds 轮，直到 net_sentiment 收敛。
    Layer 2: 风险角色（激进/保守/中立）—— 独立于交易辩论评估幸存提案风险面。

    llm_client 可选：给定则用 LLM 生成论点；缺省则依据 market_context 纯规则降级，
    保证在无 LLM/离线环境下依然可跑与可测（零依赖硬阻塞）。
    """

    RISK_ROLES = ("risk_aggressive", "risk_conservative", "risk_neutral")

    def __init__(self, llm_client: Optional[LLMClient] = None, max_rounds: int = 3):
        self.llm = llm_client
        self.max_rounds = max(1, int(max_rounds))

    # ---------- 主入口 ----------
    def debate(self, trade_proposal: dict, market_context: dict,
               evidence_chain: Optional[list] = None) -> AdversarialDebateResult:
        evidence_chain = evidence_chain or []
        bull_turns, bear_turns = self._run_trade_debate(trade_proposal, market_context, evidence_chain)
        risk_turns = self._run_risk_debate(trade_proposal, market_context)
        return self._synthesize(bull_turns, bear_turns, risk_turns)

    # ---------- Layer 1 ----------
    def _run_trade_debate(self, proposal, context, evidence) -> tuple:
        bull_turns: List[DebateTurn] = []
        bear_turns: List[DebateTurn] = []
        prev_net = None
        for rnd in range(self.max_rounds):
            bull = self._one_turn("bull", proposal, context, evidence, bear_turns)
            bear = self._one_turn("bear", proposal, context, evidence, bull_turns)
            bull_turns.append(bull)
            bear_turns.append(bear)
            net = bull.confidence - bear.confidence
            if prev_net is not None and abs(net - prev_net) < 0.05:
                break  # 收敛
            prev_net = net
        return bull_turns, bear_turns

    def _one_turn(self, role, proposal, context, evidence, opponent_turns) -> DebateTurn:
        if self.llm is not None:
            try:
                prompt = self._build_prompt(role, proposal, context, evidence, opponent_turns)
                arg, conf, ev = _parse_llm_turn(self.llm(prompt))
                return DebateTurn(role=role, argument=arg, evidence=ev or evidence[:3], confidence=conf)
            except Exception as e:  # noqa: BLE001
                logger.debug("[AdvDebate] LLM %s 失败，规则降级: %s", role, e)
        return self._rule_turn(role, proposal, context, evidence)

    def _rule_turn(self, role, proposal, context, evidence) -> DebateTurn:
        """无 LLM 规则降级：从 context 的方向/动量/趋势一致性推导多空论点强度。"""
        direction = str(proposal.get("direction") or context.get("composite_direction") or "").lower()
        score = float(context.get("composite_score", context.get("factor_score", 0.0)) or 0.0)
        momentum = float(context.get("momentum", 0.0) or 0.0)
        trend_align = float(context.get("trend_alignment", 0.0) or 0.0)   # [-1,1]
        # 归一到 [0,1] 的看多强度
        bull_strength = 0.5 + 0.25 * _clip(score) + 0.15 * _clip(momentum) + 0.1 * _clip(trend_align)
        bull_strength = max(0.0, min(1.0, bull_strength))
        if role == "bull":
            conf = bull_strength if direction != "short" else max(0.2, bull_strength - 0.2)
            arg = f"看多：综合分{score:.2f}/动量{momentum:.2f}/趋势一致{trend_align:.2f} 支持做多"
        else:
            conf = (1.0 - bull_strength) if direction != "long" else max(0.2, (1.0 - bull_strength) - 0.2)
            arg = f"看空：反向证据（分{score:.2f}/动量{momentum:.2f}）质疑该方向的持续性"
        return DebateTurn(role=role, argument=arg, evidence=list(evidence[:3]), confidence=round(conf, 4))

    # ---------- Layer 2 ----------
    def _run_risk_debate(self, proposal, context) -> list:
        turns: List[DebateTurn] = []
        vol = float(context.get("volatility_value", context.get("atr_ratio", 0.0)) or 0.0)
        leverage = float(proposal.get("leverage", context.get("leverage", 1.0)) or 1.0)
        for role in self.RISK_ROLES:
            if self.llm is not None:
                try:
                    prompt = self._build_risk_prompt(role, proposal, context)
                    arg, conf, ev = _parse_llm_turn(self.llm(prompt))
                    turns.append(DebateTurn(role=role, argument=arg, evidence=ev, confidence=conf))
                    continue
                except Exception as e:  # noqa: BLE001
                    logger.debug("[AdvDebate] LLM risk %s 失败，规则降级: %s", role, e)
            turns.append(self._rule_risk_turn(role, vol, leverage))
        return turns

    def _rule_risk_turn(self, role, vol, leverage) -> DebateTurn:
        # 风险信心 = 对"应继续开仓"的支持度；高波动+高杠杆 → 保守方降信心
        risk_load = min(1.0, vol * 20.0) * 0.6 + min(1.0, max(0.0, (leverage - 1) / 10.0)) * 0.4
        if role == "risk_aggressive":
            conf = max(0.3, 1.0 - 0.3 * risk_load)
            arg = f"激进：波动/杠杆负荷{risk_load:.2f} 可接受，倾向按计划执行"
        elif role == "risk_conservative":
            conf = max(0.1, 1.0 - risk_load)
            arg = f"保守：负荷{risk_load:.2f} 偏高，建议减仓或收紧止损"
        else:
            conf = max(0.2, 1.0 - 0.6 * risk_load)
            arg = f"中立：负荷{risk_load:.2f}，可执行但需风控约束"
        return DebateTurn(role=role, argument=arg, confidence=round(conf, 4))

    # ---------- 综合 ----------
    def _synthesize(self, bull_turns, bear_turns, risk_turns) -> AdversarialDebateResult:
        bull_c = _avg([t.confidence for t in bull_turns]) if bull_turns else 0.5
        bear_c = _avg([t.confidence for t in bear_turns]) if bear_turns else 0.5
        net = bull_c - bear_c                                  # [-1,1]
        risk_c = _avg([t.confidence for t in risk_turns]) if risk_turns else 0.6

        # 裁决：净倾向 + 风险共识共同决定
        if net <= -0.15 or risk_c < 0.35:
            verdict = "reject"
        elif net < 0.15 or risk_c < 0.55:
            verdict = "reduce"
        else:
            verdict = "proceed"
        consensus = round(min(1.0, (abs(net) * 0.5 + risk_c * 0.5)), 4)
        return AdversarialDebateResult(
            bull_turns=bull_turns, bear_turns=bear_turns, risk_turns=risk_turns,
            final_verdict=verdict, net_sentiment=round(net, 4), consensus_confidence=consensus,
        )

    # ---------- prompts ----------
    def _build_prompt(self, role, proposal, context, evidence, opponent_turns) -> str:
        opp = "\n".join(f"- {t.argument}" for t in opponent_turns[-2:])
        if role == "bull":
            side = (
                "你是看多(bull)分析师。你的任务：给出支持该交易的最强论点，"
                "引用具体证据（资金流/趋势/链上数据）。然后反驳对方的论点。"
                "最后承认你自己论点的最大弱点。目标不是赢，而是通过对抗逼近真相。"
            )
        else:
            side = (
                "你是看空(bear)分析师。你的任务：给出反对该交易的最强论点，"
                "引用具体证据（超买/资金流背离/清算风险/阻力位）。然后反驳对方的论点。"
                "最后承认你自己论点的最大弱点。目标不是赢，而是通过对抗逼近真相。"
            )
        return (
            f"{side}\n\n"
            f"交易提案: {json.dumps(proposal, ensure_ascii=False)}\n"
            f"市场上下文: {json.dumps(context, ensure_ascii=False, default=str)[:1500]}\n"
            f"证据链: {json.dumps(evidence, ensure_ascii=False, default=str)[:1000]}\n"
            f"对方最新论点:\n{opp or '（暂无）'}\n"
            f"只输出 JSON: {{\"argument\": \"你的核心论点\", \"confidence\": 0-1, "
            f"\"evidence\": [\"支持你论点的证据\"], \"counterargument\": \"反驳对方\", "
            f"\"weakness\": \"你论点的弱点\"}}"
        )

    def _build_risk_prompt(self, role, proposal, context) -> str:
        role_desc = {
            "risk_aggressive": "你偏好承担风险，但仍需评估下行是否在可控范围",
            "risk_conservative": "你极度厌恶风险，倾向于保护本金",
            "risk_neutral": "你中立评估风险收益比",
        }.get(role, "你评估该提案的风险")
        return (
            f"{role_desc}。评估该交易提案是否应继续。\n"
            f"考虑：杠杆风险/清算距离/波动率/相关性/最大回撤承受力。\n"
            f"提案: {json.dumps(proposal, ensure_ascii=False)}\n"
            f"上下文: {json.dumps(context, ensure_ascii=False, default=str)[:1200]}\n"
            f"只输出 JSON: {{\"argument\": \"风险评估结论\", \"confidence\": 0-1(支持开仓的程度)}}"
        )


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _avg(xs: list) -> float:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.5
