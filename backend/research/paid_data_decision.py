# -*- coding: utf-8 -*-
"""付费数据源决策（v3 方向 4，p3-promotion）。

原则：免费源 event_study 已显著 → HOLD/不急着订；免费源样本足但不显著 → SKIP；
免费源覆盖不足且假说依赖该数据 → BUY（建议评估，不自动下单订阅）。

读 `backend/data/event_study/latest.json`（或传入 reports），输出可审计决策卡。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "research"
EVENT_STUDY_PATH = Path(__file__).resolve().parents[1] / "data" / "event_study" / "latest.json"

# 假说：哪类事件可能需要付费源补覆盖（方案点名 Coinglass / Tokenomist 等）
PAID_CANDIDATES: List[Dict[str, Any]] = [
    {
        "vendor": "coinglass",
        "product": "liquidation_heatmap_oi",
        "helps_event_types": ["liquidation.cascade", "position.oi_jump", "funding.extreme"],
        "annual_usd_est": 3000,
        "note": "清算热力/OI 细粒度；免费 forceOrder 已有逐笔时优先用免费",
    },
    {
        "vendor": "tokenomist",
        "product": "token_unlocks",
        "helps_event_types": ["token.unlock", "unlock.cliff"],
        "annual_usd_est": 2400,
        "note": "解锁日历；CoinMarketCal 免费层覆盖不足时再订",
    },
]


def _load_event_study() -> Dict[str, Any]:
    if not EVENT_STUDY_PATH.exists():
        return {}
    try:
        return json.loads(EVENT_STUDY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[paid_data] 读 event_study 失败: %s", exc)
        return {}


def _iter_type_reports(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """兼容 latest.json 多种包法：{reports:{type:..}} / {by_type:..} / list。"""
    if not payload:
        return []
    if isinstance(payload.get("reports"), dict):
        return [{"event_type": k, **(v if isinstance(v, dict) else {"raw": v})}
                for k, v in payload["reports"].items()]
    if isinstance(payload.get("by_type"), dict):
        return [{"event_type": k, **(v if isinstance(v, dict) else {"raw": v})}
                for k, v in payload["by_type"].items()]
    if isinstance(payload.get("types"), list):
        return [t for t in payload["types"] if isinstance(t, dict)]
    out = []
    for k, v in payload.items():
        if k in ("ts_ms", "generated_at", "notes", "ok", "summary") or not isinstance(v, dict):
            continue
        if any(x in v for x in ("n", "n_events", "N", "promotion_ready", "significant", "mean_excess", "excess_ci_bp")):
            out.append({"event_type": k, **v})
    return out


def decide_for_vendor(vendor_row: Dict[str, Any], reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    helps = set(vendor_row.get("helps_event_types") or [])
    related = [r for r in reports if str(r.get("event_type") or "") in helps
               or any(h in str(r.get("event_type") or "") for h in helps)]
    # 宽松匹配：类型名包含关键词
    if not related:
        keys = []
        for h in helps:
            keys.extend(h.split("."))
        related = [r for r in reports
                   if any(k and k in str(r.get("event_type") or "").lower() for k in keys)]

    n_sig = 0
    n_insuf = 0
    n_insig = 0
    details = []
    for r in related:
        n = int(r.get("n") or r.get("n_events") or r.get("N") or 0)
        ready = bool(r.get("promotion_ready") or r.get("significant"))
        # 净期望下界
        lo = r.get("net_lower_bp")
        if lo is None and isinstance(r.get("excess_ci_bp"), (list, tuple)) and len(r["excess_ci_bp"]) >= 1:
            try:
                lo = float(r["excess_ci_bp"][0]) - 14.0
            except (TypeError, ValueError):
                lo = None
        if n < 30:
            n_insuf += 1
            verdict_t = "insufficient"
        elif ready or (lo is not None and float(lo) > 0):
            n_sig += 1
            verdict_t = "significant"
        else:
            n_insig += 1
            verdict_t = "insignificant"
        details.append({"event_type": r.get("event_type"), "n": n, "verdict": verdict_t, "net_lower_bp": lo})

    if not related:
        decision = "BUY"
        reason = "event_study 无相关类型报告：覆盖不足，建议评估付费源能否补齐"
    elif n_sig > 0 and n_insuf == 0:
        decision = "HOLD"
        reason = "免费源已显著，无需为同一假说再付钱"
    elif n_insuf > 0 and n_sig == 0:
        decision = "BUY"
        reason = f"{n_insuf} 类样本不足（N<30），付费源可能提高覆盖"
    elif n_insig > 0 and n_sig == 0 and n_insuf == 0:
        decision = "SKIP"
        reason = "免费源样本够但不显著：付钱也救不了假说"
    else:
        decision = "HOLD"
        reason = "混合结果：先把免费源覆盖做满再议"

    return {
        "vendor": vendor_row["vendor"],
        "product": vendor_row["product"],
        "decision": decision,
        "reason": reason,
        "annual_usd_est": vendor_row.get("annual_usd_est"),
        "related": details,
        "note": vendor_row.get("note"),
    }


def run_paid_data_decision(*, persist: bool = True) -> Dict[str, Any]:
    payload = _load_event_study()
    reports = _iter_type_reports(payload)
    decisions = [decide_for_vendor(v, reports) for v in PAID_CANDIDATES]
    out = {
        "ts_ms": int(time.time() * 1000),
        "n_event_types_in_report": len(reports),
        "decisions": decisions,
        "summary": {
            "BUY": sum(1 for d in decisions if d["decision"] == "BUY"),
            "HOLD": sum(1 for d in decisions if d["decision"] == "HOLD"),
            "SKIP": sum(1 for d in decisions if d["decision"] == "SKIP"),
        },
        "note": "决策卡仅建议，不自动订阅；采纳需人工确认预算",
    }
    if persist:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            (DATA_DIR / "paid_data_decision.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception as exc:
            logger.warning("[paid_data] 落盘失败: %s", exc)
    return out


def latest_decision() -> Optional[Dict[str, Any]]:
    p = DATA_DIR / "paid_data_decision.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
