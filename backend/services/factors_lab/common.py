# -*- coding: utf-8 -*-
"""factors_lab 共用：LLM 调用（租户链复用 channel_b 先例）+ jsonl 读写 + 词汇检索。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def load_llm_config():
    """租户级 LLM 配置（失败返回 None，不碰共享默认配置）。"""
    try:
        from backend.services.coin_select_platform_service import resolve_admin_tenant_id
        from backend.services.llm_config_service import get_llm_config
        tid = resolve_admin_tenant_id()
        for tier in ("quick", "deep"):
            cfg = get_llm_config(tier=tier, tenant_id=tid)
            if cfg is not None and getattr(cfg, "api_key", None):
                return cfg
    except Exception as e:  # noqa: BLE001
        logger.debug("[FactorsLab] LLM 配置解析失败: %s", str(e)[:120])
    return None


def call_llm(system: str, user: str, *, caller: str, max_tokens: int = 2500,
             temperature: float = 0.4) -> Optional[str]:
    """同步调用，任何失败返回 None（调用方自行降级）。"""
    cfg = load_llm_config()
    if cfg is None:
        logger.info("[FactorsLab] 无租户 LLM 配置（caller=%s 缺席）", caller)
        return None
    try:
        from backend.services.llm_config_service import call_llm_api_sync
        resp = call_llm_api_sync(
            cfg,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens, temperature=temperature, caller=caller, timeout=90.0,
        )
        if not resp:
            return None
        choices = resp.get("choices") or []
        return ((choices[0].get("message") or {}).get("content") or "") if choices else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[FactorsLab] LLM 调用失败（caller=%s）: %s", caller, str(e)[:160])
        return None


def parse_json_block(raw: str) -> Optional[Any]:
    """剥 code fence → 解析 JSON（对象或数组）；失败返回 None。"""
    if not raw:
        return None
    text = str(raw).strip()
    if text.startswith("```"):
        lines = [l for l in text.splitlines() if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    for candidate in (text,
                      text[text.find("["): text.rfind("]") + 1] if "[" in text and "]" in text else "",
                      text[text.find("{"): text.rfind("}") + 1] if "{" in text and "}" in text else ""):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def append_jsonl(path, entry: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_jsonl(path, limit: int = 0) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    if limit and len(lines) > limit:
        lines = lines[-limit:]
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except Exception:
            continue
    return out


_TOKEN_RE = re.compile(r"[a-zA-Z_]{2,}|[\u4e00-\u9fff]")


def tokens(text: str) -> List[str]:
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")]


def jaccard(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def lexical_score(query: str, text: str) -> float:
    tq, tt = set(tokens(query)), set(tokens(text))
    if not tq or not tt:
        return 0.0
    return len(tq & tt) / len(tq)
