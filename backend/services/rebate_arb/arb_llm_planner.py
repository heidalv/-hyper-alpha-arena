"""
专用套利双模型调用器

- 策略分析模型 (strategy_llm_config_id / llm_config_id_deep): 选币 + 方向 + 风险评级
- 执行规划模型 (execution_llm_config_id / llm_config_id): 仓位缩放 + 杠杆 + 执行节奏

深度推理模型走 call_llm_api_sync 的 SSE 流式，以 [DONE] 为完成信号，不用固定秒数截断。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_STRATEGY_MAX_TOKENS = int(__import__("os").getenv("REBATE_STRATEGY_LLM_MAX_TOKENS", "4096"))
_EXECUTION_MAX_TOKENS = int(__import__("os").getenv("REBATE_EXECUTION_LLM_MAX_TOKENS", "1200"))


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _extract_message_content(resp: Dict[str, Any]) -> str:
    """兼容非流式 content 与 deepseek-reasoner 流式 reasoning_content。"""
    choice = (resp.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "\n".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        ).strip()
    if not content and message.get("reasoning_content"):
        content = str(message.get("reasoning_content") or "").strip()
    return content


def _call_config(
    config_id: int,
    messages: List[Dict[str, str]],
    *,
    max_tokens: int = 1200,
    role: str = "strategy",
    account_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    from backend.services.llm_config_service import (
        call_llm_api_sync,
        get_llm_config,
        should_use_llm_streaming,
    )

    config = get_llm_config(config_id)
    if not config:
        logger.warning("[ArbLLM] config #%s not found", config_id)
        return None

    streaming = should_use_llm_streaming(config)
    if streaming:
        logger.info(
            "[ArbLLM] %s 模型 #%s (%s) 流式调用，等待 [DONE]",
            role, config_id, config.model,
        )

    resp = call_llm_api_sync(
        config,
        messages,
        temperature=0.2,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        account_id=account_id,
        caller=f"rebate_arb.arb_llm_planner:{role}",
    )
    if not resp:
        logger.warning("[ArbLLM] %s model #%s 无响应", role, config_id)
        return None

    content = _extract_message_content(resp)
    parsed = _extract_json(content)
    if not parsed:
        logger.warning(
            "[ArbLLM] %s model #%s JSON 解析失败, content_len=%s",
            role, config_id, len(content),
        )
        return None
    return parsed


def _recent_s8_rounds_summary(limit: int = 10) -> List[Dict[str, Any]]:
    """
    M10: 取最近 N 轮 S8 的真实结果（方向对错、积分实收、时长），
    注入策略模型 prompt 让选币/方向决策带上自己的历史。失败返回空列表。
    """
    try:
        from backend.services.rebate_arb.s8_param_learner import get_recent_s8_rounds_for_ai

        return get_recent_s8_rounds_for_ai(limit=limit)
    except Exception as exc:
        logger.debug("[ArbLLM] S8 历史轮次摘要失败(跳过): %s", exc)
        return []


def call_strategy_model(
    config_id: int,
    candidates: List[str],
    intel_signals: List[Dict[str, Any]],
    size_usd: float,
    *,
    account_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    策略分析模型：从候选池选币并给出方向/置信度/风险。
    失败时返回 available=False。
    """
    system = (
        "你是 Asterdex Stage 6 积分套利策略（S8）的分析模型。"
        "本策略的收益主体是 Rh 积分（交易积分 + 持仓时长积分 + 资产积分），"
        "不是方向性价差盈亏；费用成本与积分价值的净 EV 已由系统量化模型把关，"
        "你不需要评估费率高低或套利空间是否存在。你的任务只有三件："
        "1) 选币：从候选池选出本轮最适合开仓的币种（优先 symbol_boost 高、流动性好、波动可控）；"
        "2) 方向：bullish/bearish/neutral，仅作为持仓期间的方向风险控制，不要求强趋势信号；"
        "方向不明朗时输出 neutral 即可（系统会用宏观趋势兜底方向，neutral 不等于跳过本轮）；"
        "3) 风险等级：safe/normal/warning/danger。"
        "danger 只用于极端行情（暴涨暴跌、剧烈波动、黑天鹅、流动性枯竭），表示本轮必须跳过；"
        "「方向不明朗」「没有套利优势」「费率担忧」都不构成 danger，请用 neutral + normal 表达。"
        "user 数据中的 recent_rounds 是你自己最近几轮的真实结果（方向对错/积分实收）："
        "同一币种方向连续做错时，本轮换币或给 neutral；方向持续做对的币种可以延续。"
        "输出 JSON："
        '{"symbol":"ETH/USDT","direction":"bullish|bearish|neutral","confidence":72,'
        '"risk_level":"safe|normal|warning|danger","reasoning":"..."}'
    )
    user = json.dumps(
        {
            "strategy": "S8",
            "size_usd": size_usd,
            "candidates": candidates,
            "intel_signals": intel_signals,
            # M10: 历史轮次反思 — 最近 10 轮真实结果
            "recent_rounds": _recent_s8_rounds_summary(limit=10),
        },
        ensure_ascii=False,
    )
    parsed = _call_config(
        config_id,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=_STRATEGY_MAX_TOKENS,
        role="strategy",
        account_id=account_id,
    )
    if not parsed:
        return {"available": False, "reasoning": "strategy_model_unavailable"}

    symbol = str(parsed.get("symbol") or "").strip()
    direction = str(parsed.get("direction") or "neutral").lower()
    if direction not in ("bullish", "bearish", "neutral"):
        direction = "neutral"
    risk = str(parsed.get("risk_level") or "normal").lower()
    if risk not in ("safe", "normal", "warning", "danger"):
        risk = "normal"
    try:
        confidence = float(parsed.get("confidence", 50))
    except (TypeError, ValueError):
        confidence = 50.0
    confidence = max(0.0, min(100.0, confidence))

    if not symbol:
        return {"available": False, "reasoning": "strategy_model_no_symbol"}

    return {
        "available": True,
        "symbol": symbol,
        "direction": direction,
        "confidence": confidence,
        "risk_level": risk,
        "reasoning": parsed.get("reasoning") or "",
        "model_role": "strategy",
        "llm_config_id": config_id,
    }


def call_execution_model(
    config_id: int,
    strategy_signal: Dict[str, Any],
    size_usd: float,
    constraints: Optional[Dict[str, Any]] = None,
    *,
    account_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    执行规划模型：在策略信号已确定后，规划仓位缩放与杠杆。
    失败时返回 available=False。
    """
    constraints = constraints or {}
    system = (
        "你是积分套利策略（S8）的执行规划模型。策略分析与风控闸门已在上游完成，"
        "本策略收益主体是 Rh 积分，净 EV 已由系统量化模型把关。你只负责执行层参数："
        "position_scale(0.2-1.0)、leverage(1-20)、execute_now(true/false)、reasoning。"
        "execute_now=false 只用于极端行情或执行条件明显异常（如流动性枯竭、价差异常），"
        "不要因「方向不明朗」「套利优势不足」拒绝执行。输出 JSON："
        '{"execute_now":true,"position_scale":0.8,"leverage":10,"reasoning":"..."}'
    )
    user = json.dumps(
        {
            "strategy_signal": strategy_signal,
            "size_usd": size_usd,
            "constraints": constraints,
        },
        ensure_ascii=False,
    )
    parsed = _call_config(
        config_id,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=_EXECUTION_MAX_TOKENS,
        role="execution",
        account_id=account_id,
    )
    if not parsed:
        return {"available": False, "reasoning": "execution_model_unavailable"}

    execute_now = bool(parsed.get("execute_now", True))
    try:
        position_scale = float(parsed.get("position_scale", 1.0))
    except (TypeError, ValueError):
        position_scale = 1.0
    position_scale = max(0.2, min(1.0, position_scale))
    try:
        leverage = int(parsed.get("leverage", constraints.get("default_leverage", 10)))
    except (TypeError, ValueError):
        leverage = int(constraints.get("default_leverage", 10))
    leverage = max(1, min(int(constraints.get("max_leverage", 20)), leverage))

    return {
        "available": True,
        "execute_now": execute_now,
        "position_scale": position_scale,
        "leverage": leverage,
        "reasoning": parsed.get("reasoning") or "",
        "model_role": "execution",
        "llm_config_id": config_id,
    }
