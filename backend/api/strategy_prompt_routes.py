# -*- coding: utf-8 -*-
"""
策略提示词管理 API — /api/strategy-prompt/{tier}

功能：
- GET    /           读取提示词（system + task）
- PUT    /           保存提示词（含 JSON 结构校验）
- POST   /test       测试提示词（调 LLM 验证输出）
- GET    /schema     获取 JSON 输出契约（只读）
- POST   /reset      恢复默认（从磁盘文件重新读取）

结构保护：保存时校验所有 locked 字段仍在提示词中，防止破坏 JSON 输出契约。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/strategy-prompt", tags=["strategy-prompt"])

# ════════════════════════════════════════
# 提示词配置（tier → task_id → 文件路径 + JSON 契约）
# ════════════════════════════════════════

_PROMPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "docs", "opencode", "prompts", "tasks"
)

_TIER_PROMPTS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "mid": {
        "task_swing_agent": {
            "file": "task_swing_agent.md",
            "label": "中线波段决策",
            "system_fallback": "你是华尔街顶级加密永续合约波段交易专家，专注 1h/4h 时间尺度。只返回 JSON。",
            "locked_fields": ["action", "confidence", "direction", "sl_pct", "tp_pct", "risk_reward", "cited_fact_ids", "reasoning"],
            "schema": {
                "action": {"type": "string", "required": True, "enum": ["buy", "sell", "hold"]},
                "confidence": {"type": "number", "required": True, "range": [0, 95]},
                "direction": {"type": "string", "required": True, "enum": ["long", "short", "neutral"]},
                "sl_pct": {"type": "number", "required": True, "range": [0.02, 0.05]},
                "tp_pct": {"type": "number", "required": True, "range": [0.04, 0.09]},
                "risk_reward": {"type": "number", "required": True},
                "regime_fit": {"type": "string", "required": False, "enum": ["good", "marginal", "poor"]},
                "cited_fact_ids": {"type": "array", "required": True},
                "reasoning": {"type": "string", "required": True},
            },
        },
    },
    "long": {
        "task_trend_agent_direction": {
            "file": "task_trend_agent_direction.md",
            "label": "趋势方向分析",
            "system_fallback": "你是趋势交易专家 Agent，只返回 JSON。趋势单核心哲学：顺势+让利润奔跑+止损果断。",
            "locked_fields": ["trend_score", "trend_direction", "should_open_trend", "cited_fact_ids", "reasoning"],
            "schema": {
                "trend_score": {"type": "number", "required": True, "range": [0, 100]},
                "trend_direction": {"type": "string", "required": True, "enum": ["long", "short", "neutral"]},
                "should_open_trend": {"type": "boolean", "required": True},
                "multi_tf_aligned": {"type": "boolean", "required": False},
                "suggested_sl_pct": {"type": "number", "required": False, "range": [0.04, 0.20]},
                "lifecycle": {"type": "string", "required": False, "enum": ["启动", "加速", "衰竭", "反转", "震荡"]},
                "cited_fact_ids": {"type": "array", "required": True},
                "reasoning": {"type": "string", "required": True},
            },
        },
        "task_trend_agent_review": {
            "file": "task_trend_agent_review.md",
            "label": "持仓战略复查",
            "system_fallback": "你是趋势交易专家 Agent，负责持仓生命周期管理。让利润奔跑，但不把利润还回去。只返回 JSON。",
            "locked_fields": ["action", "reduce_ratio", "reasoning"],
            "schema": {
                "action": {"type": "string", "required": True, "enum": ["hold", "reduce", "close", "tighten_trailing"]},
                "reduce_ratio": {"type": "number", "required": True, "range": [0, 1]},
                "tighten_to_pct": {"type": "number", "required": False},
                "cited_fact_ids": {"type": "array", "required": False},
                "reasoning": {"type": "string", "required": True},
            },
        },
        "task_trend_thesis_update": {
            "file": "task_trend_thesis_update.md",
            "label": "长线论点更新(MLTO)",
            "system_fallback": "你是长线趋势战略分析师，维护持续更新的趋势论点。只返回 JSON。",
            "locked_fields": ["direction", "conviction_delta", "thesis_summary", "cited_event_ids", "recommend_open"],
            "schema": {
                "direction": {"type": "string", "required": True, "enum": ["long", "short", "neutral"]},
                "conviction_delta": {"type": "number", "required": True, "range": [-8, 8]},
                "thesis_summary": {"type": "string", "required": True},
                "cited_event_ids": {"type": "array", "required": True},
                "missing_evidence": {"type": "array", "required": False},
                "invalidation": {"type": "object", "required": False},
                "recommend_open": {"type": "boolean", "required": True},
            },
        },
    },
}

# ════════════════════════════════════════
# DB 覆盖存储
# ════════════════════════════════════════

_OVERRIDE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "strategy_prompt_overrides.json"
)


def _load_overrides() -> Dict[str, Any]:
    try:
        if os.path.exists(_OVERRIDE_FILE):
            with open(_OVERRIDE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_overrides(data: Dict[str, Any]):
    try:
        os.makedirs(os.path.dirname(_OVERRIDE_FILE), exist_ok=True)
        with open(_OVERRIDE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[StrategyPrompt] 覆盖存储失败: {e}")


def _read_file_prompt(filename: str) -> str:
    """从磁盘读取原始提示词文件。"""
    path = os.path.join(_PROMPTS_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        # 剥离 frontmatter（---...---）
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return content
    except Exception as e:
        logger.warning(f"[StrategyPrompt] 读取 {filename} 失败: {e}")
        return ""


def _get_override_key(tier: str, task_id: str) -> str:
    return f"{tier}:{task_id}"


# ════════════════════════════════════════
# 结构校验
# ════════════════════════════════════════

def _validate_prompt(prompt_text: str, locked_fields: List[str]) -> tuple[bool, str]:
    """校验提示词的 JSON 输出契约未被破坏。"""
    if not prompt_text or len(prompt_text.strip()) < 50:
        return False, "提示词内容过短（<50字符）"

    missing = []
    for field in locked_fields:
        # 检查字段名是否仍在提示词中（在 JSON 模板部分）
        if f'"{field}"' not in prompt_text:
            missing.append(field)

    if missing:
        return False, f"以下必填字段被删除: {', '.join(missing)}"

    return True, "OK"


# ════════════════════════════════════════
# API 端点
# ════════════════════════════════════════

@router.get("/{tier}")
async def get_prompts(tier: str):
    """读取该 tier 的全部提示词（合并 DB覆盖 + 磁盘默认）。"""
    if tier not in _TIER_PROMPTS:
        return {"error": f"tier must be one of {list(_TIER_PROMPTS.keys())}"}

    overrides = _load_overrides()
    result = {}

    for task_id, config in _TIER_PROMPTS[tier].items():
        override_key = _get_override_key(tier, task_id)
        override = overrides.get(override_key, {})

        # 优先用 DB 覆盖，否则读磁盘文件
        task_prompt = override.get("task_prompt") or _read_file_prompt(config["file"])
        system_prompt = override.get("system_prompt") or config["system_fallback"]

        result[task_id] = {
            "label": config["label"],
            "system_prompt": system_prompt,
            "task_prompt": task_prompt,
            "is_overridden": bool(override),
            "locked_fields": config["locked_fields"],
            "schema": config["schema"],
        }

    return {"tier": tier, "prompts": result}


class PromptUpdateRequest(BaseModel):
    task_id: str
    system_prompt: str
    task_prompt: str


@router.put("/{tier}")
async def update_prompt(tier: str, req: PromptUpdateRequest):
    """保存提示词（含结构校验）。"""
    if tier not in _TIER_PROMPTS:
        return {"success": False, "error": f"未知 tier: {tier}"}

    config = _TIER_PROMPTS[tier].get(req.task_id)
    if not config:
        return {"success": False, "error": f"未知 task_id: {req.task_id}"}

    # 结构校验：检查所有 locked 字段仍在 task_prompt 中
    ok, msg = _validate_prompt(req.task_prompt, config["locked_fields"])
    if not ok:
        return {"success": False, "error": f"结构校验失败: {msg}", "locked_fields": config["locked_fields"]}

    # 保存到覆盖文件
    overrides = _load_overrides()
    override_key = _get_override_key(tier, req.task_id)
    overrides[override_key] = {
        "system_prompt": req.system_prompt,
        "task_prompt": req.task_prompt,
        "updated_at": time.time(),
    }
    _save_overrides(overrides)

    return {"success": True, "message": "提示词已保存（结构校验通过）"}


@router.get("/{tier}/{task_id}/schema")
async def get_schema(tier: str, task_id: str):
    """获取 JSON 输出契约（只读）。"""
    if tier not in _TIER_PROMPTS:
        return {"error": f"未知 tier"}
    config = _TIER_PROMPTS[tier].get(task_id)
    if not config:
        return {"error": f"未知 task_id"}
    return {"schema": config["schema"], "locked_fields": config["locked_fields"]}


class ResetRequest(BaseModel):
    task_id: str


@router.post("/{tier}/reset")
async def reset_prompt(tier: str, req: ResetRequest):
    """恢复默认（删除 DB 覆盖，回到磁盘文件版本）。"""
    if tier not in _TIER_PROMPTS:
        return {"success": False, "error": "未知 tier"}
    overrides = _load_overrides()
    override_key = _get_override_key(tier, req.task_id)
    if override_key in overrides:
        del overrides[override_key]
        _save_overrides(overrides)
        return {"success": True, "message": "已恢复默认"}
    return {"success": True, "message": "本来就是默认（无覆盖）"}


class TestRequest(BaseModel):
    task_id: str
    system_prompt: str
    task_prompt: str
    symbol: str = "BTC"


@router.post("/{tier}/test")
async def test_prompt(tier: str, req: TestRequest):
    """测试提示词：调一次 LLM 验证输出格式。"""
    if tier not in _TIER_PROMPTS:
        return {"error": "未知 tier"}
    config = _TIER_PROMPTS[tier].get(req.task_id)
    if not config:
        return {"error": "未知 task_id"}

    # 先做结构校验
    ok, msg = _validate_prompt(req.task_prompt, config["locked_fields"])
    if not ok:
        return {"success": False, "error": f"结构校验失败: {msg}"}

    # 调 LLM（用精简测试数据）
    try:
        from backend.services.llm_config_service import call_llm_api_sync
        from backend.services.llm_config_service import get_llm_config_for_analysis

        # 注入测试变量
        test_prompt = req.task_prompt
        test_prompt = test_prompt.replace("{{symbol}}", req.symbol)
        test_prompt = test_prompt.replace("{{regime}}", "trending")
        test_prompt = test_prompt.replace("{{mid_opens_today}}", "0")
        test_prompt = test_prompt.replace("{{min_score}}", "40")
        test_prompt = test_prompt.replace("{{side}}", "long")
        test_prompt = test_prompt.replace("{{long_opens_week}}", "0")
        test_prompt = test_prompt.replace("{{side_hint}}", "long")
        # 其他 {{xxx}} 变量保留（LLM 会理解）

        llm_config = get_llm_config_for_analysis()
        if not llm_config:
            return {"success": False, "error": "未配置 LLM（无 analysis config）"}

        result = call_llm_api_sync(
            config=llm_config,
            system_prompt=req.system_prompt,
            user_prompt=test_prompt,
            timeout=30,
        )

        if not result or not result.get("content"):
            return {"success": False, "error": "LLM 返回空"}

        raw = result["content"].strip()
        # 尝试解析 JSON
        # 去除 markdown 围栏
        if raw.startswith("```"):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

        # 截取 JSON
        first_brace = raw.find("{")
        last_brace = raw.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            json_str = raw[first_brace:last_brace + 1]
            parsed = json.loads(json_str)

            # 验证必填字段
            present_fields = list(parsed.keys())
            missing = [f for f in config["locked_fields"] if f not in parsed]

            return {
                "success": True,
                "json_valid": True,
                "parsed": parsed,
                "present_fields": present_fields,
                "missing_fields": missing,
                "all_fields_present": len(missing) == 0,
                "raw_response": raw[:500],
            }
        else:
            return {
                "success": True,
                "json_valid": False,
                "error": "LLM 返回中未找到 JSON 对象",
                "raw_response": raw[:500],
            }

    except json.JSONDecodeError as e:
        return {"success": False, "json_valid": False, "error": f"JSON解析失败: {e}", "raw_response": raw[:300] if 'raw' in dir() else ""}
    except Exception as e:
        return {"success": False, "error": f"测试失败: {type(e).__name__}: {str(e)[:200]}"}
