"""
AiConfigGenerator — AI 一键配置生成服务

通过 LLM 根据用户风险偏好、资金量、目标交易所动态生成最优配置。
当 LLM 不可用时，回退到硬编码模板。

生成范围: 引擎全局参数 + 风控门禁参数 + S1-S8 策略参数
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════
#  参数范围约束（用于 clamp LLM 输出）
# ════════════════════════════════════════════════════════

ENGINE_PARAM_RANGES = {
    "min_monthly_value": (1, 10000),
    "max_position_usd": (10, 100000),
    "max_total_volume_7d": (1000, 10000000),
    "max_holding_days": (1, 180),
}

RISK_GATE_PARAM_RANGES = {
    "max_daily_volume_per_exchange": (100, 10000000),
    "max_weekly_volume_per_exchange": (1000, 50000000),
    "max_daily_loss_pct": (0.005, 0.20),
}


# ════════════════════════════════════════════════════════
#  Fallback 模板
# ════════════════════════════════════════════════════════

def _fallback_config(risk_profile: str, total_equity: float, target_exchanges: List[str]) -> Dict[str, Any]:
    """三套硬编码 fallback 模板"""
    equity = max(total_equity, 50)

    if risk_profile == "conservative":
        return {
            "engine": {
                "min_monthly_value": 5,
                "max_position_usd": min(equity * 0.5, 300),
                "max_total_volume_7d": equity * 200,
                "max_holding_days": 14,
            },
            "risk_gate": {
                "max_daily_volume_per_exchange": equity * 30,
                "max_weekly_volume_per_exchange": equity * 150,
                "max_daily_loss_pct": 0.02,
            },
            "strategies": {
                "S2": {"enabled": False, "params": {}, "risk_overrides": {}},
                "S3": {"enabled": True, "params": {}, "risk_overrides": {"max_position_usd": min(equity * 0.3, 200)}},
                "S4": {"enabled": False, "params": {}, "risk_overrides": {}},
                "S6": {"enabled": False, "params": {}, "risk_overrides": {}},
                "S7": {"enabled": False, "params": {}, "risk_overrides": {}},
                "S8": {"enabled": True, "params": {"DEFAULT_LEVERAGE": 5, "ROUNDS_PER_DAY": 2}, "risk_overrides": {"max_position_usd": min(equity * 0.4, 250)}},
            },
            "reasoning": f"保守模板: 资金${equity:.0f}，启用 S3/S8（S1/S5 已下线、S6 负EV关闭），杠杆限制5x，S8日轮2次，持仓上限14天。",
        }
    elif risk_profile == "aggressive":
        return {
            "engine": {
                "min_monthly_value": 20,
                "max_position_usd": min(equity * 1.5, 3000),
                "max_total_volume_7d": equity * 1000,
                "max_holding_days": 60,
            },
            "risk_gate": {
                "max_daily_volume_per_exchange": equity * 200,
                "max_weekly_volume_per_exchange": equity * 800,
                "max_daily_loss_pct": 0.08,
            },
            "strategies": {
                "S2": {"enabled": True, "params": {}, "risk_overrides": {"max_position_usd": min(equity * 0.5, 800)}},
                "S3": {"enabled": True, "params": {}, "risk_overrides": {"max_position_usd": min(equity * 0.8, 1500)}},
                "S4": {"enabled": True, "params": {}, "risk_overrides": {"max_position_usd": min(equity * 0.3, 500)}},
                "S6": {"enabled": False, "params": {}, "risk_overrides": {}},
                "S7": {"enabled": False, "params": {}, "risk_overrides": {}},
                "S8": {"enabled": True, "params": {"DEFAULT_LEVERAGE": 12, "ROUNDS_PER_DAY": 4}, "risk_overrides": {"max_position_usd": min(equity * 1.0, 2000)}},
            },
            "reasoning": f"激进模板: 资金${equity:.0f}，启用 S2/S3/S4/S8（S1/S5 已下线、S6 负EV关闭、S7 仅监控），S8杠杆12x/日4轮，持仓上限60天，日亏损容忍8%。",
        }
    else:  # balanced
        return {
            "engine": {
                "min_monthly_value": 10,
                "max_position_usd": min(equity * 0.8, 1000),
                "max_total_volume_7d": equity * 500,
                "max_holding_days": 30,
            },
            "risk_gate": {
                "max_daily_volume_per_exchange": equity * 80,
                "max_weekly_volume_per_exchange": equity * 400,
                "max_daily_loss_pct": 0.05,
            },
            "strategies": {
                "S2": {"enabled": False, "params": {}, "risk_overrides": {}},
                "S3": {"enabled": True, "params": {}, "risk_overrides": {"max_position_usd": min(equity * 0.5, 600)}},
                "S4": {"enabled": False, "params": {}, "risk_overrides": {}},
                "S6": {"enabled": False, "params": {}, "risk_overrides": {}},
                "S7": {"enabled": False, "params": {}, "risk_overrides": {}},
                "S8": {"enabled": True, "params": {"DEFAULT_LEVERAGE": 10, "ROUNDS_PER_DAY": 3}, "risk_overrides": {"max_position_usd": min(equity * 0.6, 800)}},
            },
            "reasoning": f"平衡模板: 资金${equity:.0f}，启用 S3/S8（S1/S5 已下线、S6 负EV关闭、S7 仅监控），S8杠杆10x/日3轮，持仓上限30天。",
        }


# ════════════════════════════════════════════════════════
#  策略参数发现
# ════════════════════════════════════════════════════════

# 仅展示这些可调参数
PARAM_WHITELIST = {
    "ASTERDEX_MAKER", "ASTERDEX_REBATE", "BINANCE_TAKER",
    "TAKER_FEE", "MAKER_FEE", "REBATE_RATE",
    "DEFAULT_LEVERAGE", "MAX_LEVERAGE",
    "MIN_EQUITY", "ROUNDS_PER_DAY", "MIN_HOLD_SECONDS",
    "USDF_AU_MULTIPLIER", "ASTER_PRICE",
    "HL_MAKER", "HL_TAKER", "BINANCE_MAKER",
    "POINTS_BONUS_RATE", "TARGET_TAKER",
    "TOKENS_PER_POINT", "GRADUATION_RATE",
    "FIRST_DAY_PREMIUM",
}


def _discover_strategy_params() -> Dict[str, Dict[str, Any]]:
    """从 ALL_STRATEGIES 单例读取每个策略的可调参数及其当前值"""
    try:
        from backend.services.rebate_arb.strategies import ALL_STRATEGIES
    except Exception:
        return {}

    result = {}
    for sid, strategy in ALL_STRATEGIES.items():
        params = {}
        for attr in dir(strategy):
            if attr.startswith("_"):
                continue
            if attr.upper() != attr:
                continue
            if attr in PARAM_WHITELIST:
                val = getattr(strategy, attr, None)
                if isinstance(val, (int, float)):
                    params[attr] = val
        result[sid] = params
    return result


# ════════════════════════════════════════════════════════
#  Prompt 构建
# ════════════════════════════════════════════════════════

RISK_PROFILE_GUIDES = {
    "conservative": (
        "- 杠杆: 3-5x\n"
        "- 单仓: ≤50% 权益\n"
        "- 仅启用低风险策略(S1,S3,S6,S8)\n"
        "- 风控门禁收紧(日亏损≤2%)\n"
        "- 持仓天数≤14天\n"
        "- S8日轮次≤2, 杠杆≤5x"
    ),
    "balanced": (
        "- 杠杆: 5-10x\n"
        "- 单仓: ≤75% 权益\n"
        "- 启用中等风险策略(S1,S3,S5,S6,S7,S8)\n"
        "- 风控门禁适中(日亏损≤5%)\n"
        "- 持仓天数≤30天\n"
        "- S8日轮次3, 杠杆10x"
    ),
    "aggressive": (
        "- 杠杆: 10-20x\n"
        "- 单仓: ≤150% 权益\n"
        "- 全部策略启用(S1-S8)\n"
        "- 风控门禁宽松(日亏损≤8%)\n"
        "- 持仓天数≤60天\n"
        "- S8日轮次4, 杠杆15x"
    ),
}


def _build_system_prompt(
    risk_profile: str,
    total_equity: float,
    target_exchanges: List[str],
    goal: str,
    strategy_params: Dict[str, Dict[str, Any]],
) -> str:
    """构建 system prompt"""
    # 策略参数描述
    strategy_desc_lines = []
    for sid, params in sorted(strategy_params.items()):
        if params:
            param_items = ", ".join(f"{k}={v}" for k, v in sorted(params.items()))
            strategy_desc_lines.append(f"  {sid}: {param_items}")
        else:
            strategy_desc_lines.append(f"  {sid}: (无可调参数)")
    strategy_desc = "\n".join(strategy_desc_lines)

    exchanges_str = ", ".join(target_exchanges) if target_exchanges else "all"
    profile_guide = RISK_PROFILE_GUIDES.get(risk_profile, RISK_PROFILE_GUIDES["balanced"])

    goal_section = f"\n用户附加目标: {goal}" if goal else ""

    return f"""你是一个专业的加密货币返利套利引擎配置专家。请根据用户的风险偏好、资金量和目标交易所，生成最优配置。

## 可调参数说明

### 引擎全局参数
- min_monthly_value: 最低月收益(USD), 范围1-10000, 默认50
- max_position_usd: 最大单仓(USD), 范围10-100000, 默认5000
- max_total_volume_7d: 7日最大总量(USD), 范围1000-10000000, 默认50000
- max_holding_days: 最大持仓天数, 范围1-180, 默认30

### 风控门禁参数
- max_daily_volume_per_exchange: 每所日均量(USD), 范围100-10000000, 默认10000
- max_weekly_volume_per_exchange: 每所周均量(USD), 范围1000-50000000, 默认50000
- max_daily_loss_pct: 日最大亏损比例, 范围0.005-0.20, 默认0.03

### 策略参数 (当前值)
{strategy_desc}

### 策略说明
- S1: Maker返佣对冲 (Asterdex+Binance) P0
- S2: VIP等级冲刺 (OKX) P1
- S3: 积分挖矿 (Hyperliquid) P1
- S4: 交易竞赛套利 (Multi) P2
- S5: 资金费率+积分叠加 (Hyperliquid) P1
- S6: 跨所费率差 (Asterdex+Binance) P1
- S7: 币安Alpha积分 (Binance) P0
- S8: Asterdex Rh+ASTER (Asterdex) P0, 最高ROI

## {risk_profile} 风险偏好指导
{profile_guide}

## 用户信息
- 总权益: ${total_equity:.2f}
- 目标交易所: {exchanges_str}{goal_section}

## 输出格式要求
返回严格 JSON（不要markdown代码块，不要多余文字），结构如下:
{{
  "engine": {{ "min_monthly_value": 数值, "max_position_usd": 数值, "max_total_volume_7d": 数值, "max_holding_days": 数值 }},
  "risk_gate": {{ "max_daily_volume_per_exchange": 数值, "max_weekly_volume_per_exchange": 数值, "max_daily_loss_pct": 数值 }},
  "strategies": {{
    "S1": {{ "enabled": true/false, "params": {{}}, "risk_overrides": {{ "max_position_usd": 数值 }} }},
    "S2": {{ "enabled": true/false, "params": {{}}, "risk_overrides": {{}} }},
    ...
    "S8": {{ "enabled": true/false, "params": {{ "DEFAULT_LEVERAGE": 数值, "ROUNDS_PER_DAY": 数值 }}, "risk_overrides": {{ "max_position_usd": 数值 }} }}
  }},
  "reasoning": "配置理由简述(100字以内)"
}}

注意:
1. params中只包含上面列出的策略参数，不要编造参数名
2. risk_overrides中只使用 max_position_usd 和 max_daily_volume
3. 所有数值必须在上面的范围内
4. max_position_usd 不应超过权益的2倍
5. 每个策略的risk_overrides.max_position_usd不应超过引擎全局max_position_usd"""


# ════════════════════════════════════════════════════════
#  JSON 提取 & 校验
# ════════════════════════════════════════════════════════

def _extract_json(content: str) -> Optional[Dict]:
    """从 LLM 输出中提取 JSON（三级 fallback）"""
    if not content:
        return None

    # 1. 直接解析
    try:
        return json.loads(content.strip())
    except Exception:
        pass

    # 2. 去除 markdown 代码块
    cleaned = re.sub(r"```json\s*", "", content)
    cleaned = re.sub(r"```\s*", "", cleaned)
    try:
        return json.loads(cleaned.strip())
    except Exception:
        pass

    # 3. 正则提取第一个 JSON 对象
    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass

    return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _validate_and_clamp(config: Dict, total_equity: float) -> Dict:
    """校验并钳制 LLM 返回的配置值"""
    # Engine params
    engine = config.get("engine", {})
    for key, (lo, hi) in ENGINE_PARAM_RANGES.items():
        if key in engine:
            engine[key] = _clamp(float(engine[key]), lo, hi)
    # 特殊: max_position_usd 不超过权益 2x
    if "max_position_usd" in engine:
        engine["max_position_usd"] = min(engine["max_position_usd"], total_equity * 2)
    config["engine"] = engine

    # Risk gate params
    risk_gate = config.get("risk_gate", {})
    for key, (lo, hi) in RISK_GATE_PARAM_RANGES.items():
        if key in risk_gate:
            risk_gate[key] = _clamp(float(risk_gate[key]), lo, hi)
    config["risk_gate"] = risk_gate

    # Strategies
    strategies = config.get("strategies", {})
    for sid, scfg in strategies.items():
        if not isinstance(scfg, dict):
            continue
        # 确保 enabled 是 bool
        if "enabled" in scfg:
            scfg["enabled"] = bool(scfg["enabled"])
        if "params" not in scfg:
            scfg["params"] = {}
        if "risk_overrides" not in scfg:
            scfg["risk_overrides"] = {}
        # 钳制 per-strategy max_position_usd
        if "max_position_usd" in scfg.get("risk_overrides", {}):
            engine_max = engine.get("max_position_usd", total_equity * 2)
            scfg["risk_overrides"]["max_position_usd"] = min(
                _clamp(float(scfg["risk_overrides"]["max_position_usd"]), 10, engine_max),
                engine_max,
            )
    config["strategies"] = strategies

    return config


# ════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════

async def generate_ai_config(
    risk_profile: str,
    total_equity: float,
    target_exchanges: List[str],
    goal: str = "",
) -> Dict[str, Any]:
    """
    AI 一键配置生成主入口。

    Returns:
        {
            "success": bool,
            "source": "llm" | "fallback",
            "config": { engine, risk_gate, strategies, reasoning },
            "reasoning": str,
            "error": str | None,
        }
    """
    fallback = _fallback_config(risk_profile, total_equity, target_exchanges)

    # 1. 尝试获取 LLM 配置
    try:
        from backend.services.llm_config_service import get_llm_config, call_llm_api
        llm_config = get_llm_config()
        if not llm_config:
            logger.info("[AiConfig] No LLM config available, using fallback")
            return {
                "success": True,
                "source": "fallback",
                "config": fallback,
                "reasoning": fallback["reasoning"],
                "error": None,
            }
    except Exception as e:
        logger.warning(f"[AiConfig] LLM service unavailable: {e}")
        return {
            "success": True,
            "source": "fallback",
            "config": fallback,
            "reasoning": fallback["reasoning"],
            "error": None,
        }

    # 2. 构建 Prompt
    strategy_params = _discover_strategy_params()
    system_prompt = _build_system_prompt(risk_profile, total_equity, target_exchanges, goal, strategy_params)
    user_prompt = (
        f"请为总权益 ${total_equity:.2f}、风险偏好「{risk_profile}」、"
        f"目标交易所 {', '.join(target_exchanges) if target_exchanges else '全部'} "
        f"生成最优配置。"
    )
    if goal:
        user_prompt += f" 附加目标: {goal}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # 3. 调用 LLM
    try:
        response = await call_llm_api(
            config=llm_config,
            messages=messages,
            temperature=0.3,
            max_tokens=4000,
        )
        if not response:
            logger.warning("[AiConfig] LLM returned None")
            return {
                "success": True,
                "source": "fallback",
                "config": fallback,
                "reasoning": fallback["reasoning"],
                "error": "LLM returned empty response",
            }

        # 提取 content
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return {
                "success": True,
                "source": "fallback",
                "config": fallback,
                "reasoning": fallback["reasoning"],
                "error": "LLM returned empty content",
            }

        # 4. 提取 JSON
        parsed = _extract_json(content)
        if not parsed:
            logger.warning(f"[AiConfig] Failed to parse LLM JSON: {content[:200]}")
            return {
                "success": True,
                "source": "fallback",
                "config": fallback,
                "reasoning": fallback["reasoning"],
                "error": "Failed to parse LLM response as JSON",
            }

        # 5. 校验 + clamp
        validated = _validate_and_clamp(parsed, total_equity)

        # 确保有 reasoning
        if "reasoning" not in validated:
            validated["reasoning"] = f"AI 生成的{risk_profile}配置，总权益${total_equity:.0f}"

        return {
            "success": True,
            "source": "llm",
            "config": validated,
            "reasoning": validated.get("reasoning", ""),
            "error": None,
        }

    except Exception as e:
        logger.error(f"[AiConfig] LLM call failed: {e}")
        return {
            "success": True,
            "source": "fallback",
            "config": fallback,
            "reasoning": fallback["reasoning"],
            "error": str(e),
        }
