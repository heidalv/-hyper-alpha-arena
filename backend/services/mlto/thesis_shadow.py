"""thesis_shadow — LLM 2.0 中长线 thesis 影子恢复（U1-2，2026-08-25）。

定位（统一计划 U1-2 / 混合决策 L3 影子段）：
  - 恢复 MLTO thesis 的「方向论题」能力，但**只写论点、绝不落单**（shadow-first 铁律）；
  - 产出：JSON 结构化 thesis（direction / llm_conviction / invalidation / missing_evidence /
    thesis_summary / recommend_open / should_close），经 thesis_store 落库（mlto_thesis 表），
    供 L4 融合仲裁（decide_mid/decide_long 的 thesis 门）与后续 U2/U3 上线消费；
  - 与 FactorRoute 方向冲突时只记日志（双轨对拍数据），不改任何交易行为。

成本纪律（LLM 2.0 §3.2）：
  - 频率：每 (symbol,tier) 每 THESIS_SHADOW_MIN_INTERVAL_S（默认 14400=4h）至多一次；
  - 预算：每日 THESIS_SHADOW_MAX_PER_DAY（默认 200）次硬上限；
  - 模型：本地 14B 优先（get_llm_config_local_first, tier=quick），云端兜底；
  - 每轮周期最多处理 THESIS_SHADOW_MAX_PER_CYCLE（默认 3）个 symbol，避免阻塞主循环。

回滚：THESIS_SHADOW_ENABLED=false 即完全停用（无任何交易副作用）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ENABLED = lambda: os.getenv("THESIS_SHADOW_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
_MIN_INTERVAL = lambda: float(os.getenv("THESIS_SHADOW_MIN_INTERVAL_S", "14400") or 14400)
_MAX_PER_DAY = lambda: int(os.getenv("THESIS_SHADOW_MAX_PER_DAY", "200") or 200)
_MAX_PER_CYCLE = lambda: int(os.getenv("THESIS_SHADOW_MAX_PER_CYCLE", "3") or 3)

_lock = threading.Lock()
_last_run: Dict[str, float] = {}      # "SYMBOL:tier" -> last ts
_day_counter: Dict[str, int] = {}     # "YYYY-MM-DD" -> count


def _budget_ok() -> bool:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _lock:
        _day_counter[today] = _day_counter.get(today, 0)
        if _day_counter[today] >= _MAX_PER_DAY():
            return False
        _day_counter[today] += 1
        return True


def _rate_ok(key: str) -> bool:
    now = time.time()
    with _lock:
        last = _last_run.get(key, 0.0)
        if now - last < _MIN_INTERVAL():
            return False
        _last_run[key] = now
        return True


def _kline_regime_context(analyst_reports: Optional[Dict], symbol: str) -> str:
    """从 KlineAnalyst 报告提取 regime 结构化字段（U1-1 产物），喂给 thesis prompt。"""
    try:
        kline_rep = (analyst_reports or {}).get("kline")
        rep = kline_rep.to_dict() if hasattr(kline_rep, "to_dict") else (kline_rep or {})
        if not isinstance(rep, dict):
            return "（无 K 线分析师报告）"
        sym_u = symbol.upper()
        for sig in rep.get("signals", []) or []:
            if not isinstance(sig, dict) or (sig.get("symbol") or "").upper() != sym_u:
                continue
            data = sig.get("data") if isinstance(sig.get("data"), dict) else {}
            if data.get("source") == "llm_deep":
                regime = data.get("regime") or "?"
                rc = data.get("regime_confidence", "?")
                inv = data.get("invalidation") or "无"
                return (
                    f"K线分析师 regime={regime}(置信{rc}%) 方向={data.get('direction', '?')}"
                    f" 置信={data.get('confidence', '?')}% 失效条件={inv}"
                )
    except Exception:
        pass
    return "（K 线 regime 结构化字段缺失）"


def _factor_context(market_summary: Optional[Dict], symbol: str) -> str:
    """中线因子路由方向（若有），用于冲突对拍。"""
    try:
        ms = (market_summary or {}).get(symbol.upper()) or (market_summary or {}).get(symbol)
        if not isinstance(ms, dict):
            return "（无因子路由上下文）"
        fr = ms.get("factor_route") if isinstance(ms.get("factor_route"), dict) else None
        if fr:
            return f"FactorRoute 方向={fr.get('direction', '?')} score={fr.get('score', '?')}"
        fv3 = ms.get("factor_v3") if isinstance(ms.get("factor_v3"), dict) else None
        if fv3:
            return (
                f"因子v3 方向={fv3.get('direction_label', '?')} "
                f"分数={fv3.get('signal_score', '?')} 置信={fv3.get('confidence', '?')}"
            )
    except Exception:
        pass
    return "（无因子上下文）"


def _build_prompt(symbol: str, tier: str, market_summary: Dict, analyst_reports: Optional[Dict]) -> str:
    regime_ctx = _kline_regime_context(analyst_reports, symbol)
    factor_ctx = _factor_context(market_summary, symbol)
    tier_label = "中线(4h/1d 波段)" if tier == "mid" else "长线(1d/1w 结构)"
    _ms = (market_summary or {}).get(symbol.upper())
    _price = (_ms or {}).get("price", "?") if isinstance(_ms, dict) else "?"
    return f"""你是资深加密货币{tier_label}交易员。基于以下证据，输出你的方向论题（thesis）。

币种: {symbol} | tier: {tier}
- {regime_ctx}
- {factor_ctx}
- 当前价: {_price}

只输出 JSON（不要其他文本）：
{{
  "direction": "long" 或 "short" 或 "neutral",
  "llm_conviction": 0到100的整数（<40 表示证据不足）,
  "thesis_summary": "一句话论题（必须引用具体字段/数值作依据）",
  "invalidation": {{"condition": "什么价格/条件出现时论题失效", "price_level": 价格或null}},
  "missing_evidence": ["还想看但当前没有的证据1", "..."],
  "recommend_open": true 或 false（证据是否支持开仓）,
  "should_close": false（影子期恒 false，仅记录）
}}"""


def run_thesis_shadow(
    *,
    session_id: str,
    symbol: str,
    tier: str,
    market_summary: Dict,
    analyst_reports: Optional[Dict],
    mode: str = "paper",
    session: Any = None,
) -> Optional[Dict]:
    """执行一次 thesis 影子研判（纯写论点，零交易）。返回结果 dict 或 None。"""
    if not _ENABLED():
        return None
    if str(mode or "paper").strip().lower() != "paper":
        if not os.getenv("THESIS_SHADOW_ON_LIVE", "false").strip().lower() in ("1", "true", "yes", "on"):
            return None
    sym_u = str(symbol or "").upper()
    if not sym_u or not session_id:
        return None
    key = f"{sym_u}:{tier}"
    if not _rate_ok(key) or not _budget_ok():
        return None

    try:
        try:
            from backend.services.llm_budget_governor import llm2_allow
            if not llm2_allow("thesis"):
                return None
        except Exception:
            pass

        from backend.services.llm_config_service import get_llm_config_local_first, call_llm_api_sync
        llm_config, fallback_cfg = get_llm_config_local_first(
            "thesis", account_id=None, tier="quick",
        )
        llm_config = llm_config or fallback_cfg
        if not llm_config:
            logger.warning("[ThesisShadow] %s 无可用 LLM 配置，跳过", sym_u)
            return None

        prompt = _build_prompt(sym_u, tier, market_summary or {}, analyst_reports)
        messages = [
            {"role": "system", "content": "你是资深加密货币交易员。只输出JSON。"},
            {"role": "user", "content": prompt},
        ]
        resp = call_llm_api_sync(
            llm_config, messages, temperature=0.2, max_tokens=1200,
            response_format={"type": "json_object"},
            caller="ThesisShadow",
            fallback_config=fallback_cfg,
        )
        if not resp:
            return None
        content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return None
        m = re.search(r'\{.*\}', content, re.DOTALL)
        if not m:
            logger.warning("[ThesisShadow] %s 输出非 JSON，跳过", sym_u)
            return None
        parsed = json.loads(m.group())

        direction = str(parsed.get("direction") or "neutral").lower()
        if direction not in ("long", "short", "neutral"):
            direction = "neutral"
        try:
            conviction = max(0, min(100, int(parsed.get("llm_conviction", 0) or 0)))
        except (TypeError, ValueError):
            conviction = 0
        invalidation = parsed.get("invalidation") if isinstance(parsed.get("invalidation"), dict) else {}
        missing = parsed.get("missing_evidence") if isinstance(parsed.get("missing_evidence"), list) else []
        summary = str(parsed.get("thesis_summary") or "")[:500]
        recommend_open = bool(parsed.get("recommend_open", False))
        should_close = bool(parsed.get("should_close", False))

        result = {
            "symbol": sym_u, "tier": tier, "direction": direction,
            "llm_conviction": conviction, "thesis_summary": summary,
            "invalidation": invalidation, "missing_evidence": missing,
            "recommend_open": recommend_open, "should_close": should_close,
        }

        # ── 落库（thesis_store；注意无 save()，持久化 = get_or_create(db=..) + _persist）──
        _db = None
        try:
            from backend.core.tenant import set_system_identity
            set_system_identity()
            from backend.database.connection import SessionLocal
            from backend.services.mlto.thesis_store import get_or_create, _persist
            _db = SessionLocal()
            thesis = get_or_create(session_id, sym_u, tier, db=_db)
            if thesis is not None:
                thesis.direction = direction
                thesis.llm_conviction = conviction
                thesis.thesis_summary = summary
                thesis.invalidation = invalidation or {}
                thesis.missing_evidence = list(missing or [])[:8]
                thesis.recommend_open = recommend_open
                thesis.should_close = should_close
                thesis.direction_history = (thesis.direction_history or [])[-7:] + [direction]
                thesis.review_count = int(getattr(thesis, "review_count", 0) or 0) + 1
                _persist(_db, thesis)
                _db.commit()
        except Exception as _sv_err:
            logger.warning("[ThesisShadow] %s 落库失败: %s", sym_u, _sv_err)
        finally:
            if _db is not None:
                try:
                    _db.close()
                except Exception:
                    pass

        # ── 与因子方向冲突对拍（仅日志，双轨记账原料）──
        try:
            ms = (market_summary or {}).get(sym_u)
            fr = (ms or {}).get("factor_route") if isinstance(ms, dict) else None
            fr_dir = (fr or {}).get("direction") if isinstance(fr, dict) else None
            if fr_dir and direction not in ("neutral",) and str(fr_dir).lower() != direction:
                logger.info(
                    "[ThesisShadow] %s 冲突对拍: thesis=%s(conv=%d) vs factor_route=%s — 仅记录",
                    sym_u, direction, conviction, fr_dir,
                )
        except Exception:
            pass

        logger.info(
            "[ThesisShadow] %s/%s: %s conv=%d recommend=%s (shadow, 不落单)",
            sym_u, tier, direction, conviction, recommend_open,
        )
        # [U3-2b 2026-08-25] 委员会影子研判会（bull/bear 红队 + 决策卡只写日志）
        try:
            from backend.services.mlto.committee_shadow import run_committee
            run_committee(
                session_id=session_id, symbol=sym_u, tier=tier,
                market_summary=market_summary, analyst_reports=analyst_reports,
                thesis_direction=direction, thesis_conviction=conviction,
                thesis_id=(getattr(thesis, "thesis_id", "") if thesis is not None else ""),
            )
        except Exception as _cm_err:
            logger.debug("[CommitteeShadow] 钩子异常(已吞): %s", _cm_err)
        return result
    except Exception as e:  # noqa: BLE001 — 影子层任何异常都不许外溢
        logger.warning("[ThesisShadow] %s 异常(已吞): %s", symbol, e)
        return None


def run_shadow_batch(
    *,
    session_id: str,
    jobs: List[tuple],
    market_summary: Dict,
    analyst_reports: Optional[Dict],
    mode: str = "paper",
    session: Any = None,
) -> int:
    """批量影子研判：每轮最多 _MAX_PER_CYCLE 个；失败不阻塞主循环。返回处理数。"""
    if not _ENABLED() or not jobs:
        return 0
    done = 0
    for (sym_u, _slot, tier) in jobs:
        if done >= _MAX_PER_CYCLE():
            break
        try:
            if run_thesis_shadow(
                session_id=session_id, symbol=sym_u, tier=tier,
                market_summary=market_summary, analyst_reports=analyst_reports,
                mode=mode, session=session,
            ):
                done += 1
        except Exception as e:
            logger.debug("[ThesisShadow] batch item 异常: %s", e)
    return done
