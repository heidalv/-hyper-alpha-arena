"""trade_review_officer — 复盘官（U4 最小实现，2026-08-25）。

统一计划 U4 / 大脑 M1：在**事实归因**（source_attribution：钱从哪来到哪去）之上做
**认知归因**（运气vs技能、原因标签、反事实、教训蒸馏）。

设计约束（LLM 2.0 纪律）：
  - 只消费真实成交（paper 源），绝不回测幻觉；
  - 本地 14B 优先（usage=journal，已绑定 id84）+ 云端兜底；
  - 预算：llm2_allow("review") scope 硬上限（默认 50/天）+ 每 symbol 300s 限频；
  - 落库：brain_episodes（归因 JSON）+ brain_lessons（教训文本）；
  - 全程 try/except 静默，任何失败不影响交易与既有学习链路。

回滚：REVIEW_OFFICER_ENABLED=false。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_run: Dict[str, float] = {}
_MIN_INTERVAL = lambda: float(os.getenv("REVIEW_MIN_INTERVAL_S", "300") or 300)


def _enabled() -> bool:
    return os.getenv("REVIEW_OFFICER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def _rate_ok(symbol: str) -> bool:
    now = time.time()
    with _lock:
        last = _last_run.get(symbol, 0.0)
        if now - last < _MIN_INTERVAL():
            return False
        _last_run[symbol] = now
        return True


def _build_prompt(outcome) -> str:
    meta = outcome.metadata if isinstance(outcome.metadata, dict) else {}
    nature = outcome.trade_nature or "swing"
    return (
        f"你是交易复盘官。基于一笔已平仓交易的事实，做认知归因（运气vs技能），输出一句可复用教训。\n"
        f"事实: {outcome.symbol} {outcome.side} {nature} | pnl={outcome.pnl:.4f} pnl_pct={outcome.pnl_pct:.4f} | "
        f"持仓{int(outcome.duration_seconds or 0)}s | 出场={meta.get('close_reason', '?')} | "
        f"来源={meta.get('source', outcome.source)} | 开仓regime={outcome.regime_at_entry} | "
        f"thesis_id={meta.get('thesis_id', '无')}\n"
        "只输出 JSON：\n"
        '{"luck_or_skill": "skill|luck|mixed", "cause": "regime_misjudge|execution_slippage|factor_decay|liquidity|discipline|noise", '
        '"lesson": "一句话可复用教训（引用具体数字）", "counterfactual": "若当时怎么改会更好（或不适用）", "severity": "low|medium|high"}'
    )


def review_close(db, outcome) -> Optional[Dict]:
    """平仓复盘：认知归因 + 教训落库。返回 review dict 或 None。"""
    if not _enabled():
        return None
    if getattr(outcome, "source", "") != "paper":
        return None
    sym = str(getattr(outcome, "symbol", "") or "").upper()
    if not sym or not _rate_ok(sym):
        return None
    try:
        from backend.services.llm_budget_governor import llm2_allow
        if not llm2_allow("review"):
            return None
    except Exception:
        pass

    try:
        from backend.services.llm_config_service import get_llm_config_local_first, call_llm_api_sync
        llm_config, fallback = get_llm_config_local_first("journal", account_id=None, tier="quick")
        llm_config = llm_config or fallback
        if not llm_config:
            return None
        resp = call_llm_api_sync(
            llm_config,
            [{"role": "system", "content": "你是交易复盘官。只输出JSON。"},
             {"role": "user", "content": _build_prompt(outcome)}],
            temperature=0.2, max_tokens=600,
            response_format={"type": "json_object"},
            caller="ReviewOfficer",
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

        luck = str(parsed.get("luck_or_skill") or "mixed")[:16]
        cause = str(parsed.get("cause") or "noise")[:32]
        lesson = str(parsed.get("lesson") or "")[:500]
        counterfactual = str(parsed.get("counterfactual") or "")[:300]
        severity = str(parsed.get("severity") or "medium")[:16]

        meta = outcome.metadata if isinstance(outcome.metadata, dict) else {}
        review = {
            "luck_or_skill": luck, "cause": cause, "lesson": lesson,
            "counterfactual": counterfactual, "severity": severity,
        }

        # ── 落库：brain_episodes（归因 JSON）+ brain_lessons（教训）──
        try:
            from sqlalchemy import text
            _pid = meta.get("paper_position_id")
            _tid = meta.get("thesis_id") or ""
            _ts = datetime.now(timezone.utc)
            db.execute(text(
                "INSERT INTO brain_episodes (trade_id, thesis_id, symbol, decision_source, "
                "close_reason, pnl, fee, net, win, attribution_json, created_at) "
                "VALUES (:tid, :thesis, :sym, :src, :reason, :pnl, :fee, :net, :win, :aj, :ts)"
            ), {
                "tid": int(_pid) if str(_pid or "").isdigit() else None,
                "thesis": _tid, "sym": sym, "src": str(meta.get("source") or outcome.source or "")[:32],
                "reason": str(meta.get("close_reason") or "")[:120],
                "pnl": float(getattr(outcome, "pnl", 0) or 0),
                "fee": 0.0, "net": float(getattr(outcome, "pnl", 0) or 0),
                "win": bool((getattr(outcome, "pnl", 0) or 0) > 0),
                "aj": json.dumps(review, ensure_ascii=False), "ts": _ts,
            })
            if lesson:
                db.execute(text(
                    "INSERT INTO brain_lessons (lesson_text, embedding_ref, source_episode_id, "
                    "confirm_count, contradict_count, status, created_at, updated_at) "
                    "VALUES (:lt, :er, :seid, 0, 0, 'active', :ts, :ts)"
                ), {"lt": lesson, "er": None,
                    "seid": int(_pid) if str(_pid or "").isdigit() else None, "ts": _ts})
        except Exception as _db_err:
            logger.debug("[ReviewOfficer] 落库跳过: %s", _db_err)

        logger.info(
            "[ReviewOfficer] %s %s: %s/%s -> %s", sym, outcome.trade_nature or "?",
            luck, cause, (lesson or "")[:60],
        )
        return review
    except Exception as e:  # noqa: BLE001
        logger.debug("[ReviewOfficer] 异常(已吞): %s", e)
        return None
