"""
QAA Prompt Schema — 统一 TradingDecision Schema (Prompt P0)

设计文档: docs/V4_MULTI_AGENT_ARCHITECTURE.md §6.5.2-6.5.3

核心目标:
1. 统一概念模型 — 消除跨系统不一致 (置信度 0-100 vs 0.0-1.0, 操作集不同)
2. Schema-as-Prompt — 用 Pydantic Field(description=...) 替代文本格式描述
3. with_structured_output — LLM 直接返回 Schema 实例, 无需 JSON 解析

影响文件:
- ai_decision_service.py (4,414 行)
- trading_analysts.py (2,723 行)

兼容策略:
- 新 Schema 不修改现有代码, 仅作为 QAA 模式的输出格式
- QAA_MODE=legacy 时完全不影响现有行为
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════
#  统一枚举
# ══════════════════════════════════════════════════


class TradingAction(str, Enum):
    """统一操作类型 — 取代两套混用

    旧系统: ai_decision_service 用 buy/sell/hold/close/reduce
             trading_analysts 用 buy/sell/hold/close_position/reduce_position
    统一后: 全部使用 TradingAction
    """
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"
    REDUCE = "reduce"          # 减仓
    PYRAMID = "pyramid"        # 加仓 (盈利加仓)


class ConfidenceScale(int, Enum):
    """统一置信度 — 0-100 整数, 取代 0.0-1.0 浮点

    旧系统: ai_decision_service 用 0.0-1.0 float
             trading_analysts 用 0-100 int
    统一后: 全部使用 0-100 int (与 LLM 自然输出对齐)
    """
    NONE = 0
    LOW = 25
    MEDIUM = 50
    HIGH = 75
    VERY_HIGH = 100


# ══════════════════════════════════════════════════
#  统一 Schema
# ══════════════════════════════════════════════════


class StopTakeLevel(BaseModel):
    """统一止盈止损 — 同时记录价格和百分比"""
    price: Optional[float] = Field(
        default=None,
        description="目标价格 (绝对值), 如 68000.0",
    )
    pct: float = Field(
        description="距离当前价的百分比, 如 2.5 表示 2.5%",
    )


class TradingDecision(BaseModel):
    """统一决策输出 — 所有 LLM Prompt 最终输出此格式 (Prompt P0)

    Schema-as-Prompt 核心理念:
    - 每个字段的 Field(description=...) 直接作为 LLM 的输出指令
    - 搭配 with_structured_output() 实现 100% 结构化输出
    - 无需在 prompt 中描述 JSON 格式, Schema 本身就是格式说明
    """
    symbol: str = Field(
        description="交易对, 如 BTC, ETH, SOL",
    )
    action: TradingAction = Field(
        description=(
            "交易动作。HOLD 为默认安全选择。"
            "仅在高置信度 (>65) 且有明确信号时才 BUY/SELL。"
            "REDUCE 用于部分减仓。PYRAMID 用于盈利加仓。"
        ),
    )
    confidence: int = Field(
        ge=0,
        le=100,
        description=(
            "决策置信度 0-100。"
            "< 40 强制 HOLD。< 60 自动降级为 HOLD。"
            ">= 65 允许开仓。>= 80 允许较大仓位。"
        ),
    )
    reasoning: str = Field(
        description=(
            "决策理由, 必须包含: "
            "(1) 正反论点权衡 (bull vs bear) "
            "(2) 关键数据引用 (价格、指标值) "
            "(3) 与上一次决策的一致性说明。"
            "3-5 句话, 简洁精准。"
        ),
    )

    # 以下字段仅 action=BUY/SELL/PYRAMID 时有意义
    trade_nature: Optional[str] = Field(
        default=None,
        description=(
            "交易性质: scalping (分钟级) / day (日内) / swing (数日) / position (周级)。"
            "仅 action=BUY/SELL/PYRAMID 时需要填写。"
        ),
    )
    stop_loss: Optional[StopTakeLevel] = Field(
        default=None,
        description="止损设置。仅 action=BUY/SELL/PYRAMID 时需要填写。",
    )
    take_profit: Optional[StopTakeLevel] = Field(
        default=None,
        description="止盈设置。仅 action=BUY/SELL/PYRAMID 时需要填写。",
    )
    risk_reward_ratio: Optional[float] = Field(
        default=None,
        ge=1.0,
        description=(
            "盈亏比 (R:R), 必须 >= 1.5 才允许开仓。"
            "计算方式: take_profit_pct / stop_loss_pct。"
        ),
    )
    suggested_leverage: Optional[int] = Field(
        default=None,
        ge=1,
        le=20,
        description="建议杠杆倍数 1-20x。系统会叠加风控上限。",
    )

    # 以下字段仅 action=HOLD/REDUCE 时有意义
    adjust_sl: Optional[float] = Field(
        default=None,
        description="调整止损到新价格 (仅 HOLD/REDUCE)。传 0 或不传表示不调整。",
    )
    adjust_tp: Optional[float] = Field(
        default=None,
        description="调整止盈到新价格 (仅 HOLD/REDUCE)。传 0 或不传表示不调整。",
    )

    # 元信息 (不由 LLM 填充, 由系统填充)
    tier: Optional[str] = Field(
        default=None,
        description="交易周期 tier: short/mid/long (系统自动填充)",
    )
    source_agent: Optional[str] = Field(
        default=None,
        description="来源 Agent ID (系统自动填充)",
    )


# ══════════════════════════════════════════════════
#  信号压缩 (Prompt P1)
# ══════════════════════════════════════════════════


class CompressedSignal(BaseModel):
    """压缩信号 — 将分析师报告压缩为结构化信号

    效果: MasterController prompt 从 ~5000 token 缩短至 ~2500 token,
    同时保留关键决策信息。
    """
    symbol: str = Field(description="交易对")
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: int = Field(ge=0, le=100, description="0-100 置信度")
    key_drivers: List[str] = Field(
        default_factory=list,
        max_length=3,
        description="最多 3 个关键驱动因素",
    )
    risk_flags: List[str] = Field(
        default_factory=list,
        max_length=2,
        description="最多 2 个风险标记",
    )
    source: str = Field(default="", description="信号来源 (如 position_analyst)")


class SignalCompressor:
    """信号压缩器 — 将多路分析师报告压缩为结构化信号

    借鉴 ai-hedge-fund 的层级漏斗聚合:
    - 输入: 多个 AnalystReport (trading_analysts.py 的产物)
    - 输出: 每个 symbol 一个 CompressedSignal
    - 效果: MC prompt token 减少 30-50%
    """

    def compress(self, reports: List[Any]) -> Dict[str, CompressedSignal]:
        """压缩分析师报告列表

        Args:
            reports: AnalystReport 对象列表, 需有 symbol, direction, confidence, key_factors, risk_flags 属性

        Returns:
            {symbol: CompressedSignal} 字典
        """
        result: Dict[str, CompressedSignal] = {}

        for report in reports:
            symbol = getattr(report, "symbol", None) or getattr(report, "pair", "UNKNOWN")
            direction = self._normalize_direction(
                getattr(report, "direction", "neutral")
            )
            confidence = self._normalize_confidence(
                getattr(report, "confidence", 50)
            )
            key_factors = getattr(report, "key_factors", []) or []
            risk_flags = getattr(report, "risk_flags", []) or []

            result[symbol] = CompressedSignal(
                symbol=symbol,
                signal=direction,
                confidence=confidence,
                key_drivers=[str(f)[:80] for f in key_factors[:3]],
                risk_flags=[str(f)[:60] for f in risk_flags[:2]],
                source=getattr(report, "analyst_type", ""),
            )

        return result

    def compress_from_dict(self, market_summary: Dict[str, Any]) -> Dict[str, CompressedSignal]:
        """从 market_summary 字典压缩 (兼容旧数据格式)

        Args:
            market_summary: {symbol: {...analyst_data...}} 字典

        Returns:
            {symbol: CompressedSignal} 字典
        """
        result: Dict[str, CompressedSignal] = {}

        for symbol, data in market_summary.items():
            if not isinstance(data, dict):
                continue

            # 1) 从各 analyst 字段收集「明确方向」的票。
            #    [2026-06-13 修复] 只统计真正带 direction 的 analyst —— 历史 bug：
            #    缺失/空的 analyst 字段被 data.get(key, {}) 当成 neutral 灌票，使每个
            #    symbol 永远凑足 5 张 neutral 票 → 永远 neutral，把编排器算出的真实方向
            #    彻底稀释。现在缺失字段不计票。
            signals = []
            for key in ("position_analyst", "market_analyst", "risk_analyst",
                        "strategy_analyst", "intel_analyst"):
                analyst = data.get(key)
                if isinstance(analyst, dict) and analyst.get("direction"):
                    direction = self._normalize_direction(analyst.get("direction"))
                    conf = self._normalize_confidence(analyst.get("confidence", 50))
                    signals.append((direction, conf))

            bull_count = sum(1 for d, _ in signals if d == "bullish")
            bear_count = sum(1 for d, _ in signals if d == "bearish")

            signal = "neutral"
            confidence = 50
            source = ""

            if signals and bull_count != bear_count:
                # analyst 给出明确多数方向 → 采用
                signal = "bullish" if bull_count > bear_count else "bearish"
                confidence = int(sum(c for _, c in signals) / len(signals))
                source = "analyst"
            else:
                # 2) analyst 无明确方向（缺失/全中性/平票）→ 回落读编排器多周期方向。
                #    [2026-06-13 修复] 把编排器写在 orchestrator 块里的 side / 三周期 bias
                #    接回主控信号，否则编排器的 enter long/short 在压缩层被全部拍平成
                #    neutral，主控永远看不到机会、从不开新仓（自选币因此永远不成交）。
                orch = data.get("orchestrator")
                o_signal, o_conf = self._signal_from_orchestrator(orch)
                if o_signal != "neutral":
                    signal = o_signal
                    confidence = o_conf
                    source = "orchestrator"
                elif signals:
                    # analyst 有票但平局：维持中性，保留均值置信度
                    confidence = int(sum(c for _, c in signals) / len(signals))
                    source = "analyst"

            result[symbol] = CompressedSignal(
                symbol=symbol,
                signal=signal,
                confidence=confidence,
                key_drivers=[],
                risk_flags=[],
                source=source,
            )

        return result

    @staticmethod
    def _signal_from_orchestrator(orch: Any) -> "tuple[str, int]":
        """从编排器块推断方向与置信度，返回 (signal, confidence_0_100)。

        字段口径见 full_auto_trading_service 写入处（market_summary[sym]["orchestrator"]）：
        权威方向用 side(=final_side, long/short)；置信度与回落方向用三周期 bias 投票
        (long/mid/short_bias + *_conf)。无任何方向信息时返回 ("neutral", 0)。
        """
        if not isinstance(orch, dict) or not orch:
            return ("neutral", 0)

        # 三周期 bias 置信度加权投票
        bull_w = 0.0
        bear_w = 0.0
        n_dir = 0
        for bk, ck in (("long_bias", "long_conf"),
                       ("mid_bias", "mid_conf"),
                       ("short_bias", "short_conf")):
            b = SignalCompressor._normalize_direction(orch.get(bk, "neutral"))
            c = SignalCompressor._normalize_confidence(orch.get(ck, 0))
            if b == "bullish":
                bull_w += c
                n_dir += 1
            elif b == "bearish":
                bear_w += c
                n_dir += 1
        vote = "neutral"
        if bull_w > bear_w:
            vote = "bullish"
        elif bear_w > bull_w:
            vote = "bearish"
        vote_conf = int(max(bull_w, bear_w) / n_dir) if n_dir else 0

        # side(=final_side) 作为权威方向；兼容历史 final_side 键名
        side = SignalCompressor._normalize_direction(
            orch.get("side") or orch.get("final_side") or "neutral"
        )
        if side in ("bullish", "bearish"):
            return (side, vote_conf or 50)
        if vote != "neutral":
            return (vote, vote_conf or 50)
        return ("neutral", 0)

    @staticmethod
    def _normalize_direction(direction: str) -> str:
        """将各种方向表示统一为 bullish/bearish/neutral"""
        d = str(direction).lower().strip()
        if d in ("bullish", "buy", "long", "strong_buy"):
            return "bullish"
        if d in ("bearish", "sell", "short", "strong_sell"):
            return "bearish"
        return "neutral"

    @staticmethod
    def _normalize_confidence(conf: Any) -> int:
        """将各种置信度表示统一为 0-100 int"""
        if isinstance(conf, (int, float)):
            if 0 < conf <= 1.0:
                return int(conf * 100)
            return max(0, min(100, int(conf)))
        try:
            return int(float(conf))
        except (ValueError, TypeError):
            return 50


# ══════════════════════════════════════════════════
#  safe_invoke — Schema-as-Prompt 降级策略
# ══════════════════════════════════════════════════


def safe_invoke_structured(
    llm: Any,
    prompt: str,
    schema_class: type,
    extract_json_fn: Any = None,
) -> Any:
    """安全调用 LLM with_structured_output, 失败时降级为 JSON 提取

    Args:
        llm: LangChain ChatModel 实例 (需支持 with_structured_output)
        prompt: 提示词字符串
        schema_class: Pydantic BaseModel 类 (如 TradingDecision)
        extract_json_fn: JSON 提取函数 (从 free-text 中提取 JSON)

    Returns:
        schema_class 实例
    """
    # 尝试 with_structured_output
    try:
        structured_llm = llm.with_structured_output(schema_class)
        result = structured_llm.invoke(prompt)
        if result is not None:
            return result
    except Exception:
        pass

    # 降级: free-text + JSON 提取
    try:
        response = llm.invoke(prompt)
        content = getattr(response, "content", str(response))

        if extract_json_fn:
            json_str = extract_json_fn(content)
        else:
            json_str = _extract_json_simple(content)

        if json_str:
            return schema_class.model_validate_json(json_str)
    except Exception:
        pass

    # 最终降级: 返回默认值
    return _default_decision(schema_class)


def _extract_json_simple(text: str) -> Optional[str]:
    """从文本中简单提取 JSON 字符串"""
    import re
    # 尝试提取 ```json ... ``` 块
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # 尝试提取 { ... } 块
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if match:
        return match.group(0)

    return None


def _default_decision(schema_class: type) -> Any:
    """生成安全的默认决策 (HOLD)"""
    if schema_class == TradingDecision:
        return TradingDecision(
            symbol="UNKNOWN",
            action=TradingAction.HOLD,
            confidence=0,
            reasoning="LLM 调用失败, 安全降级为 HOLD",
        )
    # 通用默认: 全部字段用默认值
    try:
        return schema_class()
    except Exception:
        return None
