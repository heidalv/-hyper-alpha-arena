"""
QAA Prompt Utils — XML 数据分隔 + 确定性预填充 (Prompt P1)

设计文档: docs/V4_MULTI_AGENT_ARCHITECTURE.md §6.5.5 + §6.5.6

核心目标:
1. XML 数据分隔 — 用 XML 标签包裹动态注入数据, 防止 Prompt Injection
2. 确定性预填充 — 将已知数据 (价格、持仓) 预先填入 Schema 默认值, 减少 LLM token

改造策略:
- 新建 prompt 构建函数, 不修改现有 prompt 模板
- QAA 模式使用新 prompt 构建器, legacy 模式不受影响
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from backend.services.qaa.prompt_schema import (
    CompressedSignal,
    TradingAction,
    TradingDecision,
)


# ══════════════════════════════════════════════════
#  XML 数据分隔
# ══════════════════════════════════════════════════

def wrap_xml(tag: str, content: str) -> str:
    """用 XML 标签包裹动态数据

    Args:
        tag: XML 标签名 (如 analyst_reports, tier_context)
        content: 要包裹的内容

    Returns:
        <tag>content</tag>

    安全性: XML 标签使 LLM 能区分"指令"和"数据",
    防止注入在数据中的 prompt 指令被 LLM 执行。
    """
    # 清理内容中的 XML 注入
    safe_content = content.replace(f"</{tag}>", f"\\</{tag}>")
    return f"<{tag}>\n{safe_content}\n</{tag}>"


def build_master_prompt_xml(
    tier: str,
    symbol: str,
    compressed_signals: Optional[Dict[str, CompressedSignal]] = None,
    analyst_reports_text: str = "",
    debate_summary: str = "",
    recent_lessons: str = "",
    position_data: Optional[Dict[str, Any]] = None,
    market_data: Optional[Dict[str, Any]] = None,
    orchestrator_data: Optional[Dict[str, Any]] = None,
    rebate_arb_context: Optional[Dict[str, Any]] = None,
) -> str:
    """构建 MasterController 的 XML 格式 Prompt (Prompt P1)

    所有动态数据用 XML 标签包裹:
    - <tier_context> — 交易周期上下文
    - <position_data> — 持仓信息 (Layer A 确定性数据)
    - <market_data> — 市场数据快照
    - <signals> — 压缩信号
    - <analyst_reports> — 分析师报告全文
    - <debate_summary> — 辩论摘要
    - <orchestrator_view> — 编排器视图
    - <rebate_arb_context> — 积分/返利套利上下文（与方向交易共用 AI 交易员）
    - <recent_lessons> — 近期教训

    Args:
        tier: 交易周期 (short/mid/long)
        symbol: 交易对
        其余: 动态注入数据

    Returns:
        XML 格式的 Prompt 字符串
    """
    sections = []

    # 系统 prompt (指令, 不包裹 XML)
    sections.append(
        "你是一个专业的加密货币交易决策系统。\n"
        "基于以下结构化数据做出交易决策。\n"
        "你必须输出 TradingDecision 格式。\n"
    )

    # 交易周期上下文
    # 2026-07-06 整改（审查 3 #20）：short(scalp) 文案原写"持仓<2h"，但
    # sub_position_manager.NATURE_RULES["scalp"]["expected_hold_hours"] 早已在
    # 2026-07-01 改为 8（"假 scalp"用紧止损却只给2h,两头亏，实盘16笔-372U/胜率6%
    # 才改成日内单）。Prompt 文案不同步会让 LLM 按"2小时就要跑"的错误预期去
    # 管理仓位，与实际的止损/止盈/超时平仓规则脱节。以代码里的真实数值为准改写。
    tier_hints = {
        "short": "SHORT (scalp/日内): 日内单, SL≈1.5×ATR, 杠杆10-20x, 持仓约8h(非<2h)",
        "mid": "MID (intraday): 日内中线, SL 3-5%, 杠杆8-12x, 持仓2-8h",
        "long": "LONG (trend): 周级趋势, SL 6-10%, 杠杆≤8x, 持仓24-72h",
    }
    sections.append(wrap_xml("tier_context", tier_hints.get(tier, tier_hints["mid"])))

    # 当前交易对
    sections.append(wrap_xml("target_symbol", symbol))

    # Layer A: 确定性持仓数据
    if position_data:
        sections.append(wrap_xml("position_data", json.dumps(position_data, ensure_ascii=False, indent=2)))

    # 市场数据快照
    if market_data:
        sections.append(wrap_xml("market_data", json.dumps(market_data, ensure_ascii=False, indent=2)))

    # 压缩信号
    if compressed_signals:
        signals_text = "\n".join(
            f"- {sym}: {sig.signal} (conf={sig.confidence}) "
            f"drivers={sig.key_drivers} risks={sig.risk_flags}"
            for sym, sig in compressed_signals.items()
        )
        sections.append(wrap_xml("signals", signals_text))

    # 分析师报告 (全文, 可能较长)
    if analyst_reports_text:
        sections.append(wrap_xml("analyst_reports", analyst_reports_text))

    # 辩论摘要
    if debate_summary:
        sections.append(wrap_xml("debate_summary", debate_summary))

    # 编排器视图
    if orchestrator_data:
        sections.append(wrap_xml("orchestrator_view", json.dumps(orchestrator_data, ensure_ascii=False, indent=2)))

    # 积分套利上下文（可选）
    if rebate_arb_context:
        summary = rebate_arb_context.get("summary_text")
        if not summary and isinstance(rebate_arb_context, dict):
            summary = json.dumps(rebate_arb_context, ensure_ascii=False, indent=2)
        if summary:
            sections.append(wrap_xml("rebate_arb_context", str(summary)))

    # 近期教训
    if recent_lessons:
        sections.append(wrap_xml("recent_lessons", recent_lessons))

    return "\n\n".join(sections)


# ══════════════════════════════════════════════════
#  确定性预填充
# ══════════════════════════════════════════════════

def prefill_decision(
    symbol: str,
    tier: str,
    current_price: float = 0,
    has_position: bool = False,
    position_side: str = "",
    position_entry_price: float = 0,
) -> TradingDecision:
    """用确定性数据预填充 TradingDecision (Prompt P1)

    借鉴 ai-hedge-fund 的确定性预填充:
    - symbol, tier 由系统确定, 不让 LLM 猜
    - action 默认 HOLD (安全)
    - adjust_sl/adjust_tp 根据持仓状态预填
    - trade_nature 根据 tier 推断

    效果: 每次 LLM 调用节省 ~500-1000 token (系统确定的字段不需要 LLM 推理)

    Args:
        symbol: 交易对
        tier: 交易周期
        current_price: 当前价格
        has_position: 是否有持仓
        position_side: 持仓方向 (long/short)
        position_entry_price: 持仓入场价格

    Returns:
        预填充的 TradingDecision (LLM 需修改的部分为 None)
    """
    nature_map = {"short": "scalping", "mid": "day", "long": "swing"}

    decision = TradingDecision(
        symbol=symbol,
        action=TradingAction.HOLD,  # 安全默认
        confidence=0,
        reasoning="",
        trade_nature=nature_map.get(tier),
        tier=tier,
    )

    # 如果有持仓, 预填 adjust 字段
    if has_position and position_entry_price > 0 and current_price > 0:
        pnl_pct = (current_price - position_entry_price) / position_entry_price
        if position_side == "short":
            pnl_pct = -pnl_pct

        # 亏损持仓 → 预设调整倾向
        if pnl_pct < -0.02:
            decision.reasoning = f"当前持仓浮亏 {pnl_pct*100:.1f}%, "

    return decision


# ══════════════════════════════════════════════════
#  Prompt 注入防护 (Prompt P3)
# ══════════════════════════════════════════════════

def sanitize_user_input(text: str) -> str:
    """清理用户输入/动态数据中的潜在 Prompt 注入

    防护策略:
    1. 移除常见的注入前缀 ("ignore previous", "system:", etc.)
    2. 截断过长输入 (防止 token 溢出攻击)
    3. 转义 XML 标签 (配合 XML 数据分隔使用)
    """
    if not text:
        return ""

    # 截断 (最大 10000 字符)
    text = text[:10000]

    # 移除/替换注入关键词
    injection_patterns = [
        "ignore previous instructions",
        "ignore all previous",
        "disregard all",
        "system:",
        "SYSTEM:",
        "### system",
        "### SYSTEM",
        "<|im_start|>",
        "<|im_end|>",
        "[INST]",
        "[/INST]",
    ]
    for pattern in injection_patterns:
        text = text.replace(pattern, "[FILTERED]")

    # 转义 XML 标签 (防止注入伪造数据标签)
    import re
    text = re.sub(r'<(?!/?tier_context|/?target_symbol|/?position_data|/?market_data|/?signals|/?analyst_reports|/?debate_summary|/?orchestrator_view|/?rebate_arb_context|/?recent_lessons)',
                  '&lt;', text)

    return text


def data_hash(data: Any) -> str:
    """计算数据的哈希值 (审计用)

    每次决策记录输入数据的哈希, 确保可审计可回溯。
    """
    try:
        json_str = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()[:16]
    except Exception:
        return "hash_error"


# ══════════════════════════════════════════════════
#  KlineAnalyst Few-shot 示例 (Prompt P2)
# ══════════════════════════════════════════════════

KLINE_FEW_SHOT_EXAMPLES = """
## 分析示例 (Few-shot)

### 正面示例 1: 锤子线反转
K线数据: BTC 1h, 连续4根阴线后出现长下影线(下影线长度=实体3倍), 成交量较前4根均值放大1.5倍
分析: 锤子线形态出现在下降趋势末端, 下影线长度为实体3倍表明买方在低位强力介入。
成交量放大确认了反转信号的有效性。RSI=32 接近超卖区, 支撑反弹预期。
结论: bullish, confidence=70

### 正面示例 2: 三只乌鸦
K线数据: ETH 4h, 连续3根阴线, 每根开盘价在前一根实体范围内, 收盘价逐步降低, 成交量递增
分析: 三只乌鸦是典型的看跌持续形态。每根K线的开盘价在前一根实体范围内说明
多头试图反击但失败, 收盘逐步走低+量能递增确认空头主导。MACD即将死叉。
结论: bearish, confidence=75

### 正面示例 3: 无明确信号
K线数据: SOL 15m, 近5根K线在一个窄幅区间内震荡(振幅<0.5%), 成交量萎缩
分析: 价格在窄幅区间震荡, 成交量萎缩, 说明多空双方力量均衡, 方向不明。
没有出现明确的形态或突破信号。
结论: neutral, confidence=30

## 错误示例 (不要这样做)

### 错误 1: 过度解读单根K线
错误: "这根K线是红色的, 所以看跌"
纠正: 单根K线颜色不构成信号。必须结合趋势背景、成交量、前后K线关系综合判断。
一根红色K线在上升趋势中可能只是回调, 不是反转。

### 错误 2: 忽略成交量
错误: "出现了十字星, 所以要反转"
纠正: 没有成交量确认的形态不可靠。十字星+缩量=犹豫(中性),
十字星+放量=真正的多空分歧(可能有方向变化)。始终报告成交量变化。

### 错误 3: 过高置信度
错误: "看到了头肩顶形态, confidence=95"
纠正: K线形态分析的固有准确率约60-70%。在缺乏多重确认时, 置信度不应超过75。
需要多个信号共振(形态+量能+指标)才能给到80+。
"""


def get_kline_prompt_with_examples(base_prompt: str, tier: str = "mid") -> str:
    """将 Few-shot 示例注入 K 线分析师 prompt (Prompt P2)

    Args:
        base_prompt: 原始 K 线分析师 prompt
        tier: 交易周期 (影响时间框架)

    Returns:
        注入示例后的 prompt
    """
    tier_note = {
        "short": "\n注意: 你在分析 SHORT 周期 (5m/15m), 重点关注短期形态和快速反转信号。",
        "mid": "\n注意: 你在分析 MID 周期 (1h/4h), 重点关注日内趋势和中期形态。",
        "long": "\n注意: 你在分析 LONG 周期 (4h/1d), 重点关注周级趋势和长期形态。",
    }

    return f"{base_prompt}\n\n{KLINE_FEW_SHOT_EXAMPLES}\n{tier_note.get(tier, '')}"

