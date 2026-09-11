# -*- coding: utf-8 -*-
"""Hub 决策归因落库（mid/long 方向来源审计）。

## 为什么需要

2026-09-09 根因调查发现：`alpha_analytics.ai_decision_logs` 里 mid/long 的
`decision_snapshot` **只有** `trade_nature / tier / confidence / reasoning /
agent_source / hub_mode / ai_governed_weight`——**没有 `direction`、没有
`dir_src`、没有 `llm_qual`**。后果：

- 无法回答"这次开仓的方向到底是谁定的"（LLM 还是框架兜底）；
- 无法做 LLM 方向命中率的滚动校准（`brain_agent_calibration` 表 0 行）；
- 本次调查只能退而用 `brain_theses` 的 `updated_at` 近似决策时刻，
  样本从 8.6 万条缩到 192 条。

本模块把 hub 的**方向归因三元组**写进决策快照：
`direction` / `dir_src` / `llm_qual` / `fw_mean` / `hub_action` / `hub_adjusted`。

## 设计

- 只写 analytics 库（`AIDecisionLog` 在 `AnalyticsBase`），失败静默不影响主链；
- `decision_source` 固定 `"hub"`，`_scan_log` 标记便于与其它来源区分；
- 调用点在 `mlto/orchestrator.py::tick` 的 `fuse_signals` 之后（唯一权威点）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _llm_qual_value(signals) -> Optional[float]:
    try:
        for s in signals or []:
            if getattr(s, "name", "") == "llm_qual":
                return float(getattr(s, "value", 0.5))
    except Exception:
        pass
    return None


def _fw_mean(signals) -> Optional[float]:
    try:
        from backend.services.mlto.decision_hub import _fw_mean as _fwm
        return float(_fwm(list(signals or [])))
    except Exception:
        return None


def persist_brain_decision(
    *,
    account_id: Optional[int],
    symbol: str,
    tier: str,
    direction: str,
    conviction: int,
    recommend_open: bool,
    accepted: bool,
    consensus_score: float = 0.0,
    alignment_score: int = 0,
    regime: str = "",
    session_id: str = "",
    thesis_id: str = "",
    market_summary: Optional[dict] = None,
    reasoning: str = "",
) -> None:
    """[2026-09-09] 主脑（LLM）方向决策归因落库。

    背景：旧 MLTO orchestrator 已于 2026-09-05 下线（`run_mlto_tick` 不再调用），
    mid/long 的方向由 `brain.refresh_thesis` 直接决定。因此 `persist_hub_decision`
    在 orchestrator 里不会触发——归因必须挂在主脑上。

    落库字段：`direction` / `llm_conviction` / `fw_mean`(=alignment/15) /
    `regime` / `recommend_open` / `accepted` / `consensus_score` / `thesis_id`。
    这是 LLM 方向命中率滚动校准的唯一数据来源（`brain_agent_calibration` 此前 0 行）。
    """
    try:
        from decimal import Decimal

        from backend.database.connection import AnalyticsSessionLocal
        from backend.database.models import AIDecisionLog

        _mkt_orch: Dict[str, Any] = {}
        if isinstance(market_summary, dict):
            _blk = market_summary.get(str(symbol).upper()) or market_summary.get(symbol) or {}
            if isinstance(_blk, dict):
                _o = _blk.get("orchestrator") or {}
                if isinstance(_o, dict):
                    _mkt_orch = _o

        snap: Dict[str, Any] = {
            "tier": tier,
            "direction": str(direction or ""),
            "dir_src": "llm_brain",
            "llm_qual": round(float(conviction or 0) / 100.0, 4),
            "llm_conviction": int(conviction or 0),
            "fw_mean": round(float(alignment_score or 0) / 15.0, 4) if alignment_score else None,
            "alignment_score": int(alignment_score or 0),
            "consensus_score": round(float(consensus_score or 0), 4),
            "recommend_open": bool(recommend_open),
            "accepted": bool(accepted),
            "regime": str(regime or ""),
            "session_id": str(session_id or ""),
            "thesis_id": str(thesis_id or ""),
            "reasoning": str(reasoning or "")[:2000],
            "agent_source": "midlong_brain",
            "_scan_log": True,
            "_brain_decision_log": True,
        }
        try:
            from backend.services.mlto import decision_hub as _dh
            snap["llm_direction_require_fw_agree"] = bool(
                _dh._llm_direction_requires_framework_agree()
            )
        except Exception:
            pass

        db = AnalyticsSessionLocal()
        try:
            db.add(AIDecisionLog(
                account_id=int(account_id or 0),
                reason=str(reasoning or "")[:1000] or "brain",
                operation="buy" if str(direction) == "long" else (
                    "sell" if str(direction) == "short" else "hold"),
                symbol=str(symbol).upper(),
                prev_portion=Decimal("0"),
                target_portion=Decimal("0"),
                total_balance=Decimal("0"),
                executed="false",
                reasoning_snapshot=str(reasoning or "")[:4000] or None,
                decision_source="brain",
                decision_snapshot=json.dumps(snap, ensure_ascii=False, default=str),
                short_bias=str(_mkt_orch.get("short_bias") or "") or None,
                short_confidence=float(_mkt_orch.get("short_confidence") or 0) or None,
                mid_bias=str(_mkt_orch.get("mid_bias") or "") or None,
                mid_confidence=float(_mkt_orch.get("mid_confidence") or 0) or None,
                long_bias=str(_mkt_orch.get("long_bias") or "") or None,
                long_confidence=float(_mkt_orch.get("long_confidence") or 0) or None,
            ))
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[BrainLog] %s %s 落库跳过: %s", symbol, tier, exc)


def persist_hub_decision(
    *,
    account_id: Optional[int],
    symbol: str,
    tier: str,
    hub,
    signals,
    trade_nature: str = "",
    regime: str = "",
    session_id: str = "",
    thesis_id: str = "",
    market_summary: Optional[dict] = None,
) -> None:
    """把 hub 决策的方向归因写入 `ai_decision_logs.decision_snapshot`（异常静默）。"""
    try:
        from decimal import Decimal

        from backend.database.connection import AnalyticsSessionLocal
        from backend.database.models import AIDecisionLog

        _mkt_orch: Dict[str, Any] = {}
        if isinstance(market_summary, dict):
            _blk = market_summary.get(str(symbol).upper()) or market_summary.get(symbol) or {}
            if isinstance(_blk, dict):
                _o = _blk.get("orchestrator") or {}
                if isinstance(_o, dict):
                    _mkt_orch = _o

        snap: Dict[str, Any] = {
            "trade_nature": trade_nature,
            "tier": tier,
            "confidence": int(getattr(hub, "open_readiness", 0) or 0),
            "direction": str(getattr(hub, "direction", "") or ""),
            "dir_src": str(getattr(hub, "dir_src", "") or ""),
            "hub_action": str(getattr(hub, "action", "") or ""),
            "hub_adjusted": round(float(getattr(hub, "adjusted", 0.0) or 0.0), 4),
            "llm_qual": _llm_qual_value(signals),
            "fw_mean": _fw_mean(signals),
            "regime": str(regime or ""),
            "reasoning": str(getattr(hub, "reason_text", "") or "")[:2000],
            "agent_source": "mlto_hub",
            "session_id": str(session_id or ""),
            "thesis_id": str(thesis_id or ""),
            "_scan_log": True,
            "_hub_decision_log": True,
        }
        try:
            from backend.services.mlto import decision_hub as _dh
            snap["hub_mode"] = "ai_governed" if _dh.ai_governed_enabled() else "standard"
            snap["ai_governed_weight"] = (
                _dh.ai_governed_weight() if _dh.ai_governed_enabled() else None
            )
            snap["llm_direction_require_fw_agree"] = bool(
                _dh._llm_direction_requires_framework_agree()
            )
        except Exception:
            pass

        db = AnalyticsSessionLocal()
        try:
            db.add(AIDecisionLog(
                account_id=int(account_id or 0),
                reason=str(getattr(hub, "reason_text", "") or "")[:1000] or "hub",
                operation=str(getattr(hub, "action", "hold") or "hold").lower(),
                symbol=str(symbol).upper(),
                prev_portion=Decimal("0"),
                target_portion=Decimal("0"),
                total_balance=Decimal("0"),
                executed="false",
                reasoning_snapshot=str(getattr(hub, "reason_text", "") or "")[:4000] or None,
                decision_source="hub",
                decision_snapshot=json.dumps(snap, ensure_ascii=False, default=str),
                short_bias=str(_mkt_orch.get("short_bias") or "") or None,
                short_confidence=float(_mkt_orch.get("short_confidence") or 0) or None,
                mid_bias=str(_mkt_orch.get("mid_bias") or "") or None,
                mid_confidence=float(_mkt_orch.get("mid_confidence") or 0) or None,
                long_bias=str(_mkt_orch.get("long_bias") or "") or None,
                long_confidence=float(_mkt_orch.get("long_confidence") or 0) or None,
            ))
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[HubLog] %s %s 落库跳过: %s", symbol, tier, exc)
