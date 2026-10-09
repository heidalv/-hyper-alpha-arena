# -*- coding: utf-8 -*-
"""通道B —— LLM 综合器（Lopez-Lira & Tang 范式：证据 → 数值评分 + 理由）。

纪律（继承 08-26 选币重设计 §三.1）：
    - LLM 失败/超时/解析失败/无配置 → 返回 None（整通道本轮缺席），**绝不出 0.5 假中性**；
    - 租户级配置链复用 alpha_miner 先例（resolve_admin_tenant_id + get_llm_config），
      不碰共享默认配置（llm_config_service 治理规则）；
    - 单次批量调用（一个 prompt 覆盖全部证据包），token 纳入 llm_quota_usage 计量（caller 标识）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, Optional

from backend.services.hybrid_scoring import evidence

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是加密货币选币评审员。对每个币，基于给定的证据包（因子暴露摘要、分析师thesis信号、"
    "历史命中统计）输出 0-10 的综合评分（10=最强看多）、方向与置信度。"
    "只使用证据中当日可得的信息，不臆造数据。"
    '输出 JSON：{"scores": [{"symbol": "...", "score": 0.0, "direction": "long|neutral|short", '
    '"confidence": 0.0, "rationale": "≤60字"}]}。只输出 JSON。'
)


def _load_llm_config():
    """租户级配置（alpha_miner 先例路径；失败返回 None 而非抛出）。"""
    try:
        from backend.services.coin_select_platform_service import resolve_admin_tenant_id
        from backend.services.llm_config_service import get_llm_config
        tid = resolve_admin_tenant_id()
        for tier in ("quick", "deep"):
            cfg = get_llm_config(tier=tier, tenant_id=tid)
            if cfg is not None and getattr(cfg, "api_key", None):
                return cfg
    except Exception as e:  # noqa: BLE001
        logger.debug("[HybridScore.channel_b] LLM 配置解析失败: %s", str(e)[:120])
    return None


def parse_scores(raw: str, expected_symbols=None) -> Dict[str, Dict[str, object]]:
    """解析 LLM 输出 → {sym: {score∈[0,1], confidence, rationale, direction}}。解析失败抛 ValueError。"""
    if not raw:
        raise ValueError("空响应")
    text = str(raw).strip()
    if text.startswith("```"):
        lines = [l for l in text.splitlines() if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        if s < 0 or e <= s:
            raise ValueError(f"非 JSON 响应: {text[:80]}")
        data = json.loads(text[s:e + 1])
    items = data.get("scores") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("scores 非列表")
    out: Dict[str, Dict[str, object]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "").upper().strip()
        if not sym:
            continue
        try:
            sc = float(it.get("score"))
        except (TypeError, ValueError):
            continue
        if not (0.0 <= sc <= 10.0):
            sc = min(10.0, max(0.0, sc))
        conf = it.get("confidence")
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = None
        out[sym] = {
            "score": round(sc / 10.0, 4),  # 归一到 [0,1]
            "confidence": conf,
            "direction": str(it.get("direction") or "neutral")[:12],
            "rationale": str(it.get("rationale") or "")[:120],
        }
    if not out:
        raise ValueError("无有效评分条目")
    return out


def run(packs: Dict[str, Dict[str, object]], *, timeout_s: float = 60.0) -> Optional[Dict[str, Dict[str, object]]]:
    """批量证据包 → LLM 评分。任何失败返回 None（通道缺席，不抛出）。"""
    if not packs:
        return None
    cfg = _load_llm_config()
    if cfg is None:
        logger.info("[HybridScore.channel_b] 无租户级 LLM 配置，本轮缺席")
        return None
    try:
        from backend.services.llm_config_service import call_llm_api_sync
        prompt = evidence.to_prompt(packs)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"评审以下 {len(packs)} 个币的证据包：\n{prompt}\n"
                f"必须覆盖全部 {len(packs)} 个 symbol。")},
        ]
        resp = call_llm_api_sync(
            cfg, messages=messages, max_tokens=2500, temperature=0.2,
            caller="hybrid_scoring", timeout=timeout_s,
        )
        content = ""
        if resp:
            choices = resp.get("choices") or []
            if choices:
                content = (choices[0].get("message") or {}).get("content") or ""
        scores = parse_scores(content, expected_symbols=set(packs.keys()))
        logger.info("[HybridScore.channel_b] LLM 评分 n=%d top=%s", len(scores), 
                    sorted(scores.items(), key=lambda kv: -float(kv[1]["score"]))[:3])
        return scores
    except Exception as e:  # noqa: BLE001
        logger.warning("[HybridScore.channel_b] 失败（本轮缺席）: %s", str(e)[:160])
        return None
