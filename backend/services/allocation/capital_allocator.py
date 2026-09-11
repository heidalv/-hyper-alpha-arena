# -*- coding: utf-8 -*-
"""E4 资本分配器（v3 方向 6/8，p3-promotion）。

读 Timing Agent 的三桶建议 → 用 Agent 可信度缩放 → 写出可消费快照。
**默认不写 runtime_tuning**（与 Timing 观察模式铁律一致）；显式
`ALLOCATOR_APPLY=true` 才把 bucket_weights 写入 runtime_tuning 的只读旁路键。

研究桶阶梯（上线协议）：
  shadow → small(5%) → gray(翻倍至 100%) → 退回 shadow
每档 4 周评估；本模块只做**资格与目标占比计算**，不下单。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "allocation"
LADDER_PATH = DATA_DIR / "research_ladder.json"
LATEST_PATH = DATA_DIR / "latest.json"

DEFAULT_BUCKETS = {"trend": 0.60, "cashflow": 0.30, "research": 0.10}


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def apply_enabled() -> bool:
    return _env_true("ALLOCATOR_APPLY", False)


def promotion_frozen() -> Dict[str, Any]:
    """黑天鹅后 24h 禁止新策略晋升。"""
    try:
        from backend.services.risk.trading_state import get_state_store
        snap = get_state_store().snapshot()
        until = getattr(snap, "promotion_freeze_until", None)
        if until is None:
            return {"frozen": False}
        left = float(until) - time.time()
        if left <= 0:
            return {"frozen": False}
        return {"frozen": True, "until": until, "seconds_left": round(left, 1),
                "reason": getattr(snap, "reason", "")}
    except Exception as exc:
        return {"frozen": False, "note": f"trading_state 不可用: {exc}"}


def _load_timing_weights() -> Dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "data" / "agents" / "latest_timing_weights.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _timing_credibility() -> Dict[str, Any]:
    try:
        from backend.services.agents.base import credibility_of
        return credibility_of("timing")
    except Exception as exc:
        return {"n_scored": 0, "avg_score": None, "error": str(exc)[:120]}


def _credibility_scale(cred: Dict[str, Any]) -> float:
    """可信度缩放：样本不足 → 0.5（半信任默认桶）；达标 → clamp(avg_score, 0.3, 1.0)。"""
    min_n = int(float(os.getenv("ALLOCATOR_CRED_MIN_N", "30") or 30))
    min_score = _env_float("ALLOCATOR_CRED_MIN_SCORE", 0.55)
    n = int(cred.get("n_scored") or cred.get("n") or 0)
    avg = cred.get("avg_score")
    if n < min_n or avg is None:
        return 0.5
    try:
        s = float(avg)
    except (TypeError, ValueError):
        return 0.5
    if s < min_score:
        return max(0.3, s)
    return max(0.3, min(1.0, s))


def _blend_buckets(suggested: Dict[str, float], scale: float) -> Dict[str, float]:
    """scale=1 全信建议；scale=0 全用默认；中间线性混合。"""
    out = {}
    for k in ("trend", "cashflow", "research"):
        d = float(DEFAULT_BUCKETS[k])
        s = float(suggested.get(k, d))
        out[k] = round(d * (1.0 - scale) + s * scale, 4)
    total = sum(out.values()) or 1.0
    return {k: round(v / total, 4) for k, v in out.items()}


def _load_ladder() -> Dict[str, Any]:
    if not LADDER_PATH.exists():
        return {"strategies": {}}
    try:
        return json.loads(LADDER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"strategies": {}}


def _save_ladder(data: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LADDER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")


def research_notional_for(strategy_id: str, *, equity_usd: Optional[float] = None) -> Dict[str, Any]:
    """按阶梯档返回目标名义（占研究桶权益）。frozen → 0。"""
    freeze = promotion_frozen()
    equity = float(equity_usd if equity_usd is not None
                   else _env_float("ALLOCATOR_RESEARCH_EQUITY_USD", 5000.0))
    buckets = allocate()["bucket_weights"]
    research_equity = equity * float(buckets.get("research", 0.10))
    ladder = _load_ladder()
    row = (ladder.get("strategies") or {}).get(strategy_id) or {
        "stage": "shadow", "bucket_pct": 0.0, "weeks_in_stage": 0,
    }
    stage = str(row.get("stage") or "shadow")
    pct = float(row.get("bucket_pct") or 0.0)
    if freeze.get("frozen"):
        return {
            "strategy_id": strategy_id, "stage": stage, "bucket_pct": 0.0,
            "notional_usd": 0.0, "blocked": True, "reason": "promotion_freeze",
            "freeze": freeze,
        }
    if stage == "shadow":
        pct = 0.0
    notional = round(research_equity * pct, 2)
    return {
        "strategy_id": strategy_id, "stage": stage, "bucket_pct": pct,
        "research_equity_usd": round(research_equity, 2),
        "notional_usd": notional, "blocked": False, "freeze": freeze,
    }


def promote_strategy(strategy_id: str, *, force: bool = False) -> Dict[str, Any]:
    """shadow→small(5%)。需 force 或调用方已确认 promotion_ready；freeze 时拒。"""
    freeze = promotion_frozen()
    if freeze.get("frozen") and not force:
        return {"ok": False, "reason": "promotion_freeze", "freeze": freeze}
    ladder = _load_ladder()
    strategies = ladder.setdefault("strategies", {})
    row = strategies.get(strategy_id) or {}
    stage = str(row.get("stage") or "shadow")
    if stage != "shadow" and not force:
        return {"ok": False, "reason": f"已在 {stage}，非 shadow 请用 advance_ladder"}
    strategies[strategy_id] = {
        "stage": "small",
        "bucket_pct": 0.05,
        "weeks_in_stage": 0,
        "entered_ms": int(time.time() * 1000),
        "history": (row.get("history") or []) + [
            {"ts_ms": int(time.time() * 1000), "to": "small", "pct": 0.05}
        ],
    }
    _save_ladder(ladder)
    return {"ok": True, "strategy": strategies[strategy_id]}


def advance_ladder(strategy_id: str, *, positive_4w: bool) -> Dict[str, Any]:
    """灰度：4 周为正翻倍（上限 100%）；为负减半；连续两次负 → 退回 shadow。"""
    freeze = promotion_frozen()
    if freeze.get("frozen"):
        return {"ok": False, "reason": "promotion_freeze", "freeze": freeze}
    ladder = _load_ladder()
    strategies = ladder.setdefault("strategies", {})
    row = strategies.get(strategy_id)
    if not row:
        return {"ok": False, "reason": "策略不在阶梯上"}
    pct = float(row.get("bucket_pct") or 0.05)
    stage = str(row.get("stage") or "small")
    neg_streak = int(row.get("neg_streak") or 0)
    if positive_4w:
        pct = min(1.0, pct * 2.0)
        stage = "gray" if pct < 1.0 else "full"
        neg_streak = 0
    else:
        pct = max(0.0, pct * 0.5)
        neg_streak += 1
        if neg_streak >= 2 or pct < 0.025:
            stage, pct, neg_streak = "shadow", 0.0, 0
        else:
            stage = "small" if pct <= 0.05 else "gray"
    row.update({
        "stage": stage, "bucket_pct": round(pct, 4), "neg_streak": neg_streak,
        "weeks_in_stage": 0,
    })
    row.setdefault("history", []).append({
        "ts_ms": int(time.time() * 1000), "to": stage, "pct": pct,
        "positive_4w": positive_4w,
    })
    strategies[strategy_id] = row
    _save_ladder(ladder)
    return {"ok": True, "strategy": row}


def allocate(*, persist: bool = True) -> Dict[str, Any]:
    timing = _load_timing_weights()
    suggested = timing.get("bucket_weights") or DEFAULT_BUCKETS
    if isinstance(suggested, dict) and "payload" in timing and not timing.get("bucket_weights"):
        suggested = (timing.get("payload") or {}).get("bucket_weights") or DEFAULT_BUCKETS
    # timing 文件结构可能是 findings 包在里层
    if not isinstance(suggested, dict) or "trend" not in suggested:
        findings = timing.get("findings") or timing.get("advice") or {}
        if isinstance(findings, dict):
            suggested = findings.get("bucket_weights") or suggested
        params = timing.get("params") or {}
        if isinstance(params, dict) and "bucket_weights" in params:
            suggested = params["bucket_weights"]

    cred = _timing_credibility()
    scale = _credibility_scale(cred)
    buckets = _blend_buckets(
        {k: float(suggested.get(k, DEFAULT_BUCKETS[k])) for k in DEFAULT_BUCKETS},
        scale,
    )
    freeze = promotion_frozen()
    out = {
        "ts_ms": int(time.time() * 1000),
        "bucket_weights": buckets,
        "suggested_raw": suggested,
        "credibility": {k: cred.get(k) for k in ("n", "n_scored", "avg_score", "avg_brier") if k in cred},
        "credibility_scale": scale,
        "promotion_freeze": freeze,
        "apply_enabled": apply_enabled(),
        "ladder": _load_ladder().get("strategies") or {},
        "note": "默认只落盘；ALLOCATOR_APPLY=true 才写 runtime_tuning.bucket_weights",
    }
    if persist:
        _persist(out)
        if apply_enabled() and not freeze.get("frozen"):
            _apply_runtime(buckets)
    return out


def _persist(payload: Dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LATEST_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("[allocator] 落盘失败: %s", exc)


def _apply_runtime(buckets: Dict[str, float]) -> None:
    """旁路写入：不覆盖交易阈值，只放 bucket_weights 供消费者读取。"""
    try:
        from backend.services import runtime_tuning as RT
        if hasattr(RT, "update"):
            RT.update({"bucket_weights": buckets, "bucket_weights_source": "capital_allocator"})
        elif hasattr(RT, "set_many"):
            RT.set_many({"bucket_weights": buckets})
        else:
            path = Path(__file__).resolve().parents[2] / "data" / "runtime_tuning.json"
            data = {}
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
            data["bucket_weights"] = buckets
            data["bucket_weights_ts_ms"] = int(time.time() * 1000)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[allocator] 已写入 bucket_weights=%s", buckets)
    except Exception as exc:
        logger.warning("[allocator] 写入 runtime_tuning 失败: %s", exc)


def latest_allocation() -> Optional[Dict[str, Any]]:
    if not LATEST_PATH.exists():
        return None
    try:
        return json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def scheduled_allocate() -> Dict[str, Any]:
    from backend.core.tenant import set_system_identity
    set_system_identity()
    if not apply_enabled():
        logger.info("[allocator] ALLOCATOR_APPLY=false，跳过每日空转")
        return {"ok": True, "skipped": True, "reason": "ALLOCATOR_APPLY=false"}
    out = allocate(persist=True)
    logger.info("[allocator] buckets=%s scale=%s freeze=%s",
                out.get("bucket_weights"), out.get("credibility_scale"),
                (out.get("promotion_freeze") or {}).get("frozen"))
    return {"ok": True, "bucket_weights": out.get("bucket_weights"),
            "credibility_scale": out.get("credibility_scale")}
