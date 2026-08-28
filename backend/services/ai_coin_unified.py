# -*- coding: utf-8 -*-
"""AI 选币统一状态层（2026-08-28 归一）。

背景：历史上有两套并行 AI 选币——短线 auto_coin（平台看板跟投，状态存
full_auto_sessions.auto_coin_symbols + 选择器内存池）与中线 AI 候选
（平台看板 midlong approve，状态存 ai_mid_sticky/*.json）。两套各自存储、
消费点不一致，导致选出的币进不了 master 决策层（有选无析无交易）。

本模块 = AI 选币的 factor_active_set：
  - 单一状态存储：backend/services/data/ai_coin_unified/<session_id>.json
    （short/mid 两档同住一份文件）
  - 单一读取入口：get_ai_coin_symbols(session_id, db, tier) —— 所有循环
    （scalp/midlong/mlto/trading_cycle/master）只认这一个入口；
  - tier 只是字段：short 给短线车道，mid 给中线车道。

选择逻辑各自保留（短线=短线看板信号，中线=midlong approve + sticky 重采样），
但写入统一走 _write_state；旧存储（DB 列 / ai_mid_sticky 文件）只作迁移源与
向后兼容镜像。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "ai_coin_unified",
)
_lock = threading.Lock()


def _state_path(session_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))
    return os.path.join(_BASE_DIR, f"{safe}.json")


def _read_state(session_id: str) -> Dict[str, Any]:
    try:
        path = _state_path(session_id)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.debug("[AiCoinUnified] read state fail %s: %s", session_id, e)
    return {}


def _write_state(session_id: str, state: Dict[str, Any]) -> None:
    with _lock:
        try:
            os.makedirs(_BASE_DIR, exist_ok=True)
            tmp = _state_path(session_id) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _state_path(session_id))
        except Exception as e:
            logger.warning("[AiCoinUnified] write state fail %s: %s", session_id, e)


def set_tier_symbols(
    session_id: str,
    tier: str,
    symbols: List[str],
    *,
    reason: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """写某一档的当前选中币（选择器/中线重采样完成后调用）。"""
    tier = (tier or "").strip().lower()
    if tier not in ("short", "mid"):
        return
    state = _read_state(session_id)
    state.setdefault("version", 1)
    state.setdefault("session_id", str(session_id))
    state[tier] = {
        "symbols": [str(s).upper() for s in symbols if s],
        "updated_at": time.time(),
        "reason": reason or "",
    }
    if extra:
        state[tier].update(extra)
    state["updated_at"] = time.time()
    _write_state(session_id, state)


def get_tier_state(session_id: str, tier: str) -> Dict[str, Any]:
    tier = (tier or "").strip().lower()
    state = _read_state(session_id)
    return state.get(tier) or {} if tier in ("short", "mid") else {}


def _migrate_legacy(session_id: str, db=None) -> Dict[str, Any]:
    """一次性迁移：旧 ai_mid_sticky 文件 + DB auto_coin_symbols 列 → 统一状态。"""
    state = _read_state(session_id)
    changed = False
    # mid：旧 sticky 文件
    if not state.get("mid"):
        try:
            from backend.services.auto_coin_selector import _ai_mid_sticky_path
            legacy = _ai_mid_sticky_path(session_id)
            if os.path.exists(legacy):
                with open(legacy, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                if isinstance(data, dict) and data.get("symbols"):
                    state["mid"] = {
                        "symbols": [str(s).upper() for s in data.get("symbols") or [] if s],
                        "updated_at": float(data.get("updated_at") or time.time()),
                        "reason": str(data.get("reason") or "migrated_from_sticky"),
                    }
                    changed = True
                    logger.info(
                        "[AiCoinUnified] migrate mid sticky → unified session=%s n=%d",
                        session_id, len(state["mid"]["symbols"]),
                    )
        except Exception as e:
            logger.debug("[AiCoinUnified] migrate mid skip %s: %s", session_id, e)
    # short：DB auto_coin_symbols 列
    if not state.get("short") and db is not None:
        try:
            from sqlalchemy import text as _sa_text
            row = db.execute(
                _sa_text(
                    "SELECT auto_coin_symbols FROM full_auto_sessions "
                    "WHERE session_id = :sid"
                ),
                {"sid": session_id},
            ).first()
            syms = _parse_list(row[0]) if row and row[0] else []
            if syms:
                state["short"] = {
                    "symbols": [str(s).upper() for s in syms if s],
                    "updated_at": time.time(),
                    "reason": "migrated_from_db_column",
                }
                changed = True
                logger.info(
                    "[AiCoinUnified] migrate short db column → unified session=%s n=%d",
                    session_id, len(syms),
                )
        except Exception as e:
            logger.debug("[AiCoinUnified] migrate short skip %s: %s", session_id, e)
    if changed:
        state.setdefault("version", 1)
        state.setdefault("session_id", str(session_id))
        state["updated_at"] = time.time()
        _write_state(session_id, state)
    return state


def _parse_list(raw) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(s) for s in raw if s]
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return _parse_list(data)
        except Exception:
            return [s.strip() for s in raw.split(",") if s.strip()]
    return []


def get_ai_coin_symbols(
    session_id: str,
    db=None,
    tier: Optional[str] = None,
) -> List[str]:
    """AI 选币统一读取入口（全部循环只认这里）。

    tier='short' → 短线 AI 选币；tier='mid' → 中线 AI 候选；
    tier=None → 两档合并去重。
    """
    _migrate_legacy(session_id, db=db)
    state = _read_state(session_id)
    out: List[str] = []
    seen = set()
    for _t in ("short", "mid") if tier is None else ((tier or "").lower(),):
        for s in (state.get(_t) or {}).get("symbols") or []:
            u = str(s).strip().upper()
            if u and u not in seen:
                seen.add(u)
                out.append(u)
    return out
