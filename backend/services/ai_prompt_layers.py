"""分层提示词与证据评分规则。

将 MasterController 超长 prompt 拆为可组合层，用证据评分制替代冲突规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EvidenceScoreConfig:
    """开仓证据评分阈值（可经反馈闭环动态调整）。"""

    entry_threshold: int = 50
    scalp_extra: int = 8
    prefilter_bonus: int = 10
    debate_win_bonus: int = 10
    orchestrator_align_bonus: int = 5
    template_strong_bonus: int = 15
    template_medium_bonus: int = 8
    rsi_penalty: int = 5
    extreme_vol_penalty: int = 10
    short_tier_penalty: int = 8  # 防短线亏损


@dataclass
class LayeredPromptContext:
    report_text: str
    debate_text: str
    symbols_text: str
    tier_context_text: str = ""
    hold_timeout_text: str = ""
    recent_lessons_text: str = ""
    feedback_constraints_text: str = ""
    action_constraint: str = ""
    mode: str = "running"
    entry_gate: int = 50
    scalp_gate: int = 50  # 2026-06-18: 默认放宽(paper)，调用方 trading_analysts 会覆盖
    evidence_config: EvidenceScoreConfig = field(default_factory=EvidenceScoreConfig)
    v5_context_text: str = ""  # V5 费用感知 + 市场状态 + 盈亏结构纪律


def build_direction_layer(ctx: LayeredPromptContext) -> str:
    """方向判断层：只负责方向、性质、置信度，不决定金额。"""
    ec = ctx.evidence_config
    effective_gate = max(ctx.entry_gate, ec.entry_threshold)
    return f"""## 方向判断层（DirectionAgent 职责）
你是首席交易官的方向研判模块。你只判断：**买/卖/持有/加仓/补仓** 以及 **trade_nature**。
**禁止**在本层决定 leverage、position_pct、具体下单金额。

### 证据评分制（取代互相矛盾的「积极开仓」与「严防守」）
对每个「本 tier 尚无持仓」的标的，按证据累加得分（满分约 100）：
| 证据项 | 得分 |
|--------|------|
| 本 tier 置信度 ≥ {effective_gate}% | +25 |
| 多空辩论支持方 ≥ 反对方+1 | +{ec.debate_win_bonus} |
| 编排器方向与开仓一致 | +{ec.orchestrator_align_bonus} |
| 预筛选通过（RSI/MACD/BOLL/ADX） | +{ec.prefilter_bonus} |
| 模板信号 ≥ 60% | +{ec.template_strong_bonus} |
| 模板信号 45-60% | +{ec.template_medium_bonus} |
| 模板信号 < 30% | **否决**（不得开仓） |
| RSI 极端超买/超卖逆势 | -{ec.rsi_penalty} |
| 波动率 extreme | -{ec.extreme_vol_penalty} |
| short tier 开 scalp/intraday | -{ec.short_tier_penalty} |

**开仓条件**：总分 ≥ {effective_gate} 且未被否决。
**scalp 额外**：confidence ≥ {ctx.scalp_gate}%（比普通高 {ec.scalp_extra}%）。
**hold 合法**：分数不足时 hold，但 confidence 仍反映方向倾向（25-45%），不要压到 0。

### 分层置信度
| trade_nature | 参考置信度维度 |
|--------------|---------------|
| trend_follow / position | 长线置信度 |
| swing | 中线置信度 |
| intraday / scalp | 短线置信度 |

### 交易性质选择
| 性质 | 适用场景 | 预计持仓 |
|------|---------|---------|
| scalp | 高波动无趋势 | 1-4h |
| intraday | 中波动弱趋势 | 4-12h |
| swing | 明确中期趋势 | 1-3d |
| trend_follow | 极强趋势共振 | 1周+ |

{ctx.tier_context_text}
{ctx.feedback_constraints_text}
"""


def build_position_mgmt_layer() -> str:
    """持仓管理层：管理已有仓，不决定新开仓方向。"""
    return """## 持仓管理层（已有仓位专用）
对「本 tier 持仓详情」中的**每一个**持仓必须给出决策：hold / pyramid / dca / reduce / close / adjust_tp / adjust_sl。

### 管理原则
- 有 SL 的仓位：**禁止 close**（系统硬拦截）。浮亏 → hold + adjust_sl 上移。
- 盈利 ≥1.5% 且趋势延续 → 评估 pyramid（最多 2 次）。
- 亏损 2-8% 且方向仍成立 → 极谨慎 dca（最多 1 次，30% 原仓）。
- reduce 需 **4h+1h 双重反转确认**，且距上次减仓 >30min，累计 <2 次。
- partial_close_pct 只能配合 reduce，不能与 hold 组合。

### 时间保护
- 预计持仓 <4h：开仓 <15min 不做 reduce/close（除非亏>3%）
- 预计持仓 4-24h：开仓 <1h 不做 reduce/close（除非亏>5%）
- 预计持仓 >24h：开仓 <4h 不做 reduce/close（除非亏>8%）
"""


def build_risk_review_layer() -> str:
    """风控审核层：只允许拒绝、降杠杆、缩仓。"""
    return """## 风控审核层（TradeRiskAgent 职责）
- 风险评分 ≥ 80 且方向反向 → **拒绝该 symbol 新开仓**（不是全局冻结）。
- 风控层**只能**缩小仓位或降低杠杆，**禁止放大**。
- **分级回撤控制（不是全局冻结）**：
  - 全局回撤 > 50%（极端系统性风险）→ 禁止所有新开仓。
  - 全局回撤 12%-50% → **只禁止回撤贡献最大的 1-2 个 symbol 开仓**，其他 symbol 正常交易。
  - 单 symbol 浮亏 > 15% → 只冻结该 symbol，不影响其他。
  - **禁止因一个币的回撤冻结全局**——其他盈利机会不应被连带牺牲。
- 同 symbol 反向开仓禁令：上轮刚 close 的币种，本轮禁止反方向开仓。
- close 唯一合法场景：① 完全无 SL 且浮亏 ≥15%；② 周线级别趋势反转且 SL 深度穿透。
"""


def build_sizing_hint_layer() -> str:
    """仓位提示层：LLM 可给风险偏好提示，最终由 PositionSizingAgent 计算。"""
    return """## 仓位提示层（Sizing 参考 — 非最终值）
你可输出 leverage 和 position_pct 作为**建议**，但执行层会经 PositionSizingAgent 按风险预算重算：
- leverage 建议范围 2-20，**必须随波动率与置信度变化**：高波动/低确信 → 2-5x，常态 → 5-10x，极高确信+低波动 → 最多 15x。禁止每次都输出同一个值。
- position_pct 建议范围 0.04-0.35（占可用余额名义比例）
- 开仓必须给出 stop_loss_pct 和 take_profit_pct，且 take_profit_pct ÷ stop_loss_pct ≥ 1.8（否则系统硬拦截）
- hold/close/reduce 时 leverage 和 position_pct 可省略或填 0
**最终成交数量以 SizingAgent 审计字段为准，单笔最大亏损被硬性限制在权益 1.5% 以内。**
"""


def build_output_schema_layer(entry_gate: int) -> str:
    return f"""## 输出格式（JSON only，无 markdown）
- 每个 symbol 必须 1 条 decision
- reasoning 最多 80 中文字符
- confidence 反映方向确信度；开仓需 ≥ {entry_gate}%（scalp 更高）

{{
  "overall_assessment": "一句话总结",
  "risk_level": "low/medium/high/critical",
  "decisions": [{{
    "symbol": "BTC",
    "action": "hold/buy/sell/pyramid/dca/reduce/close",
    "confidence": 0-100,
    "reasoning": "关键证据",
    "trade_nature": "scalp/intraday/swing/position/trend_follow",
    "expected_hold_hours": 24,
    "stop_loss_pct": 0.03,
    "take_profit_pct": 0.08,
    "risk_reward_ratio": 2.5,
    "leverage": 6,
    "position_pct": 0.08,
    "adjust_tp": null,
    "adjust_sl": null,
    "partial_close_pct": null,
    "extend_hold_hours": 0
  }}]
}}"""


def build_layered_master_prompt(ctx: LayeredPromptContext) -> str:
    """组装完整 MasterController 分层提示词。"""
    parts = [
        "你是一个顶级加密货币量化基金的首席交易官(CTO)。",
        "策略库已匹配模板信号，你的任务是**审核信号**并用证据评分制决策。",
        "",
        "## 分析师报告",
        ctx.report_text,
        "",
        ctx.debate_text,
        "",
        f"## 交易对: {ctx.symbols_text}",
        "",
        ctx.v5_context_text,
        "",
        build_direction_layer(ctx),
        "",
        build_position_mgmt_layer(),
        "",
        build_risk_review_layer(),
        "",
        build_sizing_hint_layer(),
        "",
        ctx.hold_timeout_text,
        ctx.recent_lessons_text,
        ctx.action_constraint,
        "",
        build_output_schema_layer(ctx.entry_gate),
    ]
    return "\n".join(p for p in parts if p)


def compute_evidence_score(
    *,
    tier_confidence: float,
    debate_delta: int,
    orchestrator_aligned: bool,
    prefilter_passed: bool,
    template_confidence: float,
    is_short_tier_scalp: bool,
    config: Optional[EvidenceScoreConfig] = None,
) -> Dict[str, Any]:
    """确定性证据评分（供规则回退与审计）。"""
    cfg = config or EvidenceScoreConfig()
    score = 0
    breakdown: List[str] = []

    if tier_confidence >= cfg.entry_threshold:
        score += 25
        breakdown.append("tier_conf+25")
    if debate_delta >= 1:
        score += cfg.debate_win_bonus
        breakdown.append(f"debate+{cfg.debate_win_bonus}")
    if orchestrator_aligned:
        score += cfg.orchestrator_align_bonus
        breakdown.append(f"orch+{cfg.orchestrator_align_bonus}")
    if prefilter_passed:
        score += cfg.prefilter_bonus
        breakdown.append(f"prefilter+{cfg.prefilter_bonus}")
    if template_confidence >= 60:
        score += cfg.template_strong_bonus
        breakdown.append(f"template_strong+{cfg.template_strong_bonus}")
    elif template_confidence >= 45:
        score += cfg.template_medium_bonus
        breakdown.append(f"template_med+{cfg.template_medium_bonus}")
    if template_confidence < 30 and template_confidence > 0:
        return {"score": 0, "vetoed": True, "breakdown": ["template_veto"], "can_open": False}

    if is_short_tier_scalp:
        score -= cfg.short_tier_penalty
        breakdown.append(f"short_penalty-{cfg.short_tier_penalty}")

    can_open = score >= cfg.entry_threshold and not (template_confidence < 30 and template_confidence > 0)
    return {
        "score": score,
        "vetoed": False,
        "breakdown": breakdown,
        "can_open": can_open,
        "threshold": cfg.entry_threshold,
    }
