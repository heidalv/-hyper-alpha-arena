"""committee_shadow — 委员会影子研判会（U3-2b 最小实现，2026-08-25）。

统一计划 U3-2b / 大脑 M2 影子段：多智能体研判的**影子版**——同一模型先出 bull 论点、
再出 bear 论点（红队），最后裁决决策卡；**只写日志与 brain_theses，绝不写控制面**。

节奏：挂在 thesis shadow 成功后（同 4h 限频）；预算 scope committee=20/天。
验收依据（V2）：各 agent 校准度显著优于随机才转正——本影子期先积累决策卡与
事后方向命中对拍（thesis 滞后验证的基础数据）。

回滚：COMMITTEE_SHADOW_ENABLED=false。
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("COMMITTEE_CONTROL_ENABLED",
                     os.getenv("COMMITTEE_SHADOW_ENABLED", "false")).strip().lower() in ("1", "true", "yes", "on")


def _build_prompt(symbol: str, tier: str, regime_ctx: str, factor_ctx: str,
                  thesis_dir: str, thesis_conv: int) -> str:
    tier_label = "中线" if tier == "mid" else "长线"
    return (
        f"你是{tier_label}投资委员会主席。先分别站在多方和空方立场各给一条最强论点（红队模式，"
        f"必须引用下面证据里的具体数字），然后裁决。\n"
        f"币种 {symbol} | tier {tier}\n"
        f"- K线regime证据: {regime_ctx}\n"
        f"- 因子证据: {factor_ctx}\n"
        f"- 当前 thesis: 方向={thesis_dir} 信念度={thesis_conv}\n"
        "只输出 JSON：\n"
        '{"bull_case": "多方最强论点", "bear_case": "空方最强论点", '
        '"consensus_direction": "long|short|neutral", "consensus_confidence": 0到100的整数, '
        '"red_flag": "最需要警惕的风险（或none）", '
        '"decision_card": {"budget_hint": "increase|reduce|pause（必须三选一：无观点给reduce、方向不明给pause，禁止keep默认）", "tilt": "long|short|none", '
        '"known_unknown": "最大未知"}}'
    )


def run_committee(
    *,
    session_id: str,
    symbol: str,
    tier: str,
    market_summary: Optional[Dict],
    analyst_reports: Optional[Dict],
    thesis_direction: str = "neutral",
    thesis_conviction: int = 0,
    thesis_id: str = "",
) -> Optional[Dict]:
    if not _enabled():
        return None
    sym = str(symbol or "").upper()
    if not sym or not session_id:
        return None
    try:
        from backend.services.llm_budget_governor import llm2_allow
        if not llm2_allow("committee"):
            return None
    except Exception:
        pass

    try:
        from backend.services.mlto.thesis_shadow import (
            _kline_regime_context, _factor_context,
        )
        regime_ctx = _kline_regime_context(analyst_reports, sym)
        factor_ctx = _factor_context(market_summary, sym)
    except Exception:
        regime_ctx, factor_ctx = "（缺失）", "（缺失）"

    try:
        from backend.services.llm_config_service import get_llm_config_local_first, call_llm_api_sync
        llm_config, fallback = get_llm_config_local_first("thesis", account_id=None, tier="quick")
        llm_config = llm_config or fallback
        if not llm_config:
            return None
        resp = call_llm_api_sync(
            llm_config,
            [{"role": "system", "content": "你是投资委员会主席。只输出JSON。"},
             {"role": "user", "content": _build_prompt(
                 sym, tier, regime_ctx, factor_ctx, thesis_direction, thesis_conviction)}],
            temperature=0.2, max_tokens=800,
            response_format={"type": "json_object"},
            caller="CommitteeShadow",
            fallback_config=fallback,
        )
        if not resp:
            return None
        content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return None
        m = re.search(r'\{.*\}', content, re.DOTALL)
        if not m:
            return None
        parsed = json.loads(m.group())

        direction = str(parsed.get("consensus_direction") or "neutral").lower()
        if direction not in ("long", "short", "neutral"):
            direction = "neutral"
        try:
            conf = max(0, min(100, int(parsed.get("consensus_confidence", 0) or 0)))
        except (TypeError, ValueError):
            conf = 0
        card = parsed.get("decision_card") if isinstance(parsed.get("decision_card"), dict) else {}

        # ── 落库 brain_theses（source=committee_shadow；只写不控制）──
        try:
            from sqlalchemy import text
            from backend.database.connection import SessionLocal
            db = SessionLocal()
            try:
                db.execute(text(
                    "INSERT INTO brain_theses (thesis_id, session_id, symbol, tier, direction, "
                    "llm_conviction, thesis_summary, invalidation_json, missing_evidence_json, "
                    "recommend_open, should_close, source, created_at, updated_at) "
                    "VALUES (:tid, :sid, :sym, :tier, :dir, :conv, :summ, :inv, :me, :ro, :sc, :src, :ts, :ts)"
                ), {
                    "tid": thesis_id or None, "sid": session_id, "sym": sym, "tier": tier,
                    "dir": direction, "conv": conf,
                    "summ": f"bull={parsed.get('bull_case', '')} | bear={parsed.get('bear_case', '')}"[:500],
                    "inv": json.dumps({"red_flag": parsed.get("red_flag", ""),
                                       "known_unknown": card.get("known_unknown", ""),
                                       "budget_hint": card.get("budget_hint", "keep"),
                                       "tilt": card.get("tilt", "none")}, ensure_ascii=False),
                    "me": "[]", "ro": False, "sc": False, "src": "committee_shadow",
                    "ts": datetime.now(timezone.utc),
                })
                db.commit()
            finally:
                db.close()
        except Exception as _db_err:
            logger.debug("[CommitteeShadow] 落库跳过: %s", _db_err)

        logger.info(
            "[CommitteeShadow] %s/%s 决策卡: %s(conf=%d) budget=%s tilt=%s (shadow, 不写控制面)",
            sym, tier, direction, conf, card.get("budget_hint", "?"), card.get("tilt", "?"),
        )
        return {"symbol": sym, "tier": tier, "direction": direction, "confidence": conf,
                "decision_card": card}
    except Exception as e:  # noqa: BLE001
        logger.debug("[CommitteeShadow] 异常(已吞): %s", e)
        return None
