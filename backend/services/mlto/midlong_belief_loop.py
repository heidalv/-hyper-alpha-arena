"""MidLong v2 Phase4 — 概念信念学习闭环（FINCON verbal reinforcement 轻量版）。

目标：
1. 记录失败 Intent（fuse hold）供日度复盘
2. 规则产出概念信念（如「震荡勿追 trend」）→ 持久化 + 有界调 OWM / by_nature
3. 信念文本注入 Trend / MLTO prompt

不引入 prompt 自进化；不改 Single Writer 契约。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_DATA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "midlong_beliefs.json"
)
_MAX_FAILED = 200
_MAX_BELIEFS = 30
_REVIEW_COOLDOWN_SEC = 6 * 3600  # 每会话最少 6h 复盘一次


def _enabled() -> bool:
    try:
        from backend.config.settings import MIDLONG_BELIEF_LOOP_ENABLED
        return bool(MIDLONG_BELIEF_LOOP_ENABLED)
    except Exception:
        return True


def _load() -> Dict[str, Any]:
    path = os.path.normpath(_DATA_PATH)
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.debug("[MidLongBelief] load skip: %s", exc)
    return {"failed_intents": [], "beliefs": [], "last_review_ts": 0.0}


def _save(data: Dict[str, Any]) -> None:
    path = os.path.normpath(_DATA_PATH)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("[MidLongBelief] save failed: %s", exc)


def record_failed_intent(
    *,
    symbol: str,
    reason: str,
    regime: str = "",
    score: int = 0,
    authority: str = "",
    source: str = "",
    session_id: str = "",
    noise: bool = False,
) -> None:
    """Writer/fuse 拒绝新开时记录一条失败 Intent（轻量、无 LLM）。

    [P2-4 修复] `noise` 标记用于区分「真失败」与「正常结论」：
    - 真失败：应该开仓但被 authority / score / regime 拦截（值得复盘）
    - 噪音：LLM 自身判定 should_open=False（trend_hold）——这是正常的市场结论，
      不是失败。每 2 分钟循环一轮，若全记会把 200 条上限灌满噪音，稀释真正
      需要复盘的高价值失败样本，导致 score_low 等统计失真。
    """
    if not _enabled():
        return
    sym = str(symbol or "").upper()
    if not sym:
        return
    entry = {
        "symbol": sym,
        "reason": str(reason or "")[:160],
        "regime": str(regime or "")[:32],
        "score": int(score or 0),
        "authority": str(authority or "")[:16],
        "source": str(source or "")[:16],
        "session_id": str(session_id or "")[:64],
        "noise": bool(noise),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK:
        data = _load()
        failed = list(data.get("failed_intents") or [])
        # [P2-4] 噪音条目不占真失败配额：同 symbol 去重，避免每 tick 灌一条。
        # 真失败永远保留；噪音只在没有真失败样本时兜底。
        if noise:
            failed = [f for f in failed if not (isinstance(f, dict) and f.get("noise"))]
            failed = [f for f in failed
                      if not (isinstance(f, dict)
                              and not f.get("noise")
                              and str(f.get("symbol") or "").upper() == sym)]
        failed.append(entry)
        data["failed_intents"] = failed[-_MAX_FAILED:]
        _save(data)
    logger.debug(
        "[MidLongBelief] failed_intent %s reason=%s regime=%s noise=%s",
        sym, entry["reason"], entry["regime"], entry["noise"],
    )


def format_beliefs_for_prompt(
    *,
    symbol: Optional[str] = None,
    regime: Optional[str] = None,
    limit: int = 5,
) -> str:
    """供 Trend/MLTO prompt 注入的概念信念块。"""
    if not _enabled():
        return ""
    with _LOCK:
        data = _load()
        beliefs = list(data.get("beliefs") or [])
        failed = list(data.get("failed_intents") or [])
        # [P2-4] prompt 注入只展示真失败（noise=False），噪音不进入 AI 上下文
        failed = [f for f in failed if isinstance(f, dict) and not f.get("noise")]

    sym_u = str(symbol or "").upper()
    reg = str(regime or "").strip().lower()
    lines: List[str] = []

    # 信念优先：匹配 regime / 全局
    scored: List[tuple] = []
    for b in beliefs:
        if not isinstance(b, dict):
            continue
        b_reg = str(b.get("regime") or "").lower()
        if reg and b_reg not in ("", "any", reg):
            continue
        weight = 2 if (reg and b_reg == reg) else 1
        scored.append((weight, str(b.get("ts") or ""), b))
    scored.sort(key=lambda x: (-x[0], x[1]))
    for _, __, b in scored[: max(1, limit)]:
        lesson = str(b.get("lesson") or "").strip()
        if lesson:
            lines.append(f"- [{b.get('id', 'belief')}] {lesson}")

    # 最近失败 Intent（同币优先）
    recent = []
    for fi in reversed(failed):
        if not isinstance(fi, dict):
            continue
        if sym_u and str(fi.get("symbol") or "").upper() == sym_u:
            recent.append(fi)
        elif not sym_u:
            recent.append(fi)
        if len(recent) >= 3:
            break
    if not recent:
        for fi in reversed(failed[-5:]):
            if isinstance(fi, dict):
                recent.append(fi)

    for fi in recent[:3]:
        lines.append(
            f"- [failed_intent {fi.get('symbol')}] "
            f"{fi.get('reason') or '?'} regime={fi.get('regime') or '-'}"
        )

    if not lines:
        return ""
    return (
        "## 中长线概念信念 / 近期失败 Intent（参考，不可替代本次证据）\n"
        + "\n".join(lines[: limit + 3])
    )


def _upsert_belief(data: Dict[str, Any], belief_id: str, lesson: str, regime: str) -> bool:
    beliefs = list(data.get("beliefs") or [])
    now = datetime.now(timezone.utc).isoformat()
    for b in beliefs:
        if isinstance(b, dict) and b.get("id") == belief_id:
            b["lesson"] = lesson
            b["regime"] = regime
            b["ts"] = now
            b["hits"] = int(b.get("hits") or 0) + 1
            data["beliefs"] = beliefs[-_MAX_BELIEFS:]
            return True
    beliefs.append({
        "id": belief_id,
        "lesson": lesson,
        "regime": regime,
        "ts": now,
        "hits": 1,
        "type": "midlong_belief",
    })
    data["beliefs"] = beliefs[-_MAX_BELIEFS:]
    return True


def _apply_owm_nudge(session_id: str, *, llm_delta: float = -0.03) -> None:
    """震荡追涨信念触发时，略降 llm 源权重、略升 framework。"""
    if not session_id:
        return
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.services.mlto.db_models import MltoSignalWeight
    except Exception:
        return
    adb = None
    try:
        adb = AnalyticsSessionLocal()
        for src, delta in (("llm", llm_delta), ("framework", abs(llm_delta) * 0.5)):
            row = (
                adb.query(MltoSignalWeight)
                .filter(
                    MltoSignalWeight.session_id == session_id,
                    MltoSignalWeight.tier == "long",
                    MltoSignalWeight.source == src,
                )
                .first()
            )
            if not row:
                row = MltoSignalWeight(
                    session_id=session_id, tier="long", source=src, weight=1.0,
                )
                adb.add(row)
            row.weight = max(0.5, min(1.5, float(row.weight or 1.0) + delta))
        adb.commit()
        logger.info(
            "[MidLongBelief] OWM nudge session=%s llm%+.3f",
            session_id[:12], llm_delta,
        )
    except Exception as exc:
        logger.debug("[MidLongBelief] OWM nudge skip: %s", exc)
        if adb is not None:
            try:
                adb.rollback()
            except Exception:
                pass
    finally:
        if adb is not None:
            try:
                adb.close()
            except Exception:
                pass


def _apply_runtime_nudge(*, raise_min_score: bool = False) -> None:
    """有界抬升 trend_follow.min_score（单次 +1，上限 70，夹紧由 store 负责）。"""
    if not raise_min_score:
        return
    try:
        from backend.services.runtime_tuning_store import apply_patches, get_all_tuning
        cur = get_all_tuning() or {}
        by_n = dict(cur.get("by_nature") or {})
        tf = dict(by_n.get("trend_follow") or {})
        old = int(tf.get("min_score") or 56)
        new = min(old + 1, 70)
        if new <= old:
            return
        apply_patches({"by_nature": {"trend_follow": {"min_score": new}}})
        logger.info(
            "[MidLongBelief] runtime_tuning trend_follow.min_score %s→%s",
            old, new,
        )
    except Exception as exc:
        logger.debug("[MidLongBelief] runtime nudge skip: %s", exc)


def _count_closed_ranging_losses(db, lookback_hours: int = 168) -> Dict[str, int]:
    """统计近 N 小时 trend_follow 亏损笔数（若表结构可用）。

    [P2-4 修复] 原实现查 `Position`（positions 表），该表根本没有
    trade_nature / closed_at 列 → 查询必然报错被吞 → 恒 0 → 规则 3
    （亏损占比高 → 收紧门槛）永不触发。中长线模拟盘持仓实际在
    `PaperPosition`（paper_positions，含 trade_nature / closed_at /
    close_reason / exit_state_json / realized_pnl），改查该表。
    """
    out = {"closed": 0, "losses": 0, "ranging_hint": 0}
    if db is None:
        return out
    try:
        from datetime import timedelta
        from backend.database.models import PaperPosition
        since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        q = (
            db.query(PaperPosition)
            .filter(PaperPosition.status == "closed")
            .filter(PaperPosition.trade_nature.in_(["trend_follow", "swing", "position"]))
            .filter(PaperPosition.closed_at.isnot(None))
            .filter(PaperPosition.closed_at >= since)
        )
        rows = q.limit(200).all()
        for p in rows:
            out["closed"] += 1
            # 分批止盈的已实现盈亏在 partial_realized_pnl，尾仓在 realized_pnl
            pnl = float(
                getattr(p, "realized_pnl", None) or 0
            ) + float(getattr(p, "partial_realized_pnl", None) or 0)
            if pnl < 0:
                out["losses"] += 1
                meta = getattr(p, "exit_state_json", None) or getattr(p, "close_reason", "") or ""
                blob = str(meta or "") + str(getattr(p, "close_reason", "") or "")
                if "ranging" in blob.lower() or "震荡" in blob:
                    out["ranging_hint"] += 1
    except Exception as exc:
        logger.debug("[MidLongBelief] closed trade scan skip: %s", exc)
    return out


def maybe_run_belief_review(
    *,
    session_id: str = "",
    db=None,
    force: bool = False,
) -> Dict[str, Any]:
    """日度/节流复盘：失败 Intent + 平仓样本 → 概念信念 + 有界回灌。"""
    result = {"ok": False, "beliefs_added": [], "skipped": ""}
    if not _enabled():
        result["skipped"] = "disabled"
        return result

    now = time.time()
    with _LOCK:
        data = _load()
        last = float(data.get("last_review_ts") or 0)
        if not force and now - last < _REVIEW_COOLDOWN_SEC:
            result["skipped"] = "cooldown"
            return result

        failed = [f for f in (data.get("failed_intents") or []) if isinstance(f, dict)]
        # [P2-4] 复盘只看真失败（noise=False）：trend_hold 等正常结论不计入
        # ranging_blocks / score_low / authority_blocks，避免噪音灌满统计失真。
        real_failed = [f for f in failed if not f.get("noise")]
        # 只看最近 100 条真失败
        recent = real_failed[-100:]
        ranging_blocks = sum(
            1 for f in recent
            if "ranging" in str(f.get("reason") or "").lower()
            or str(f.get("regime") or "").lower() == "ranging"
        )
        score_low = sum(
            1 for f in recent if "score_low" in str(f.get("reason") or "").lower()
        )
        authority_blocks = sum(
            1 for f in recent if "authority" in str(f.get("reason") or "").lower()
        )

        closed_stats = _count_closed_ranging_losses(db)
        # v6 M6：方向一致率回填到 last_review，供看板验收
        try:
            from backend.services.mlto.midlong_direction_audit import summarize_consistency
            dir_audit = summarize_consistency(48.0)
        except Exception:
            dir_audit = {}
        added: List[str] = []

        # 规则 1：震荡相关失败多 → 「震荡勿追 trend」
        if ranging_blocks >= 3 or closed_stats.get("ranging_hint", 0) >= 2:
            lesson = (
                "震荡市（ranging）勿追 trend_follow 满仓：优先 hold 或极小仓探针；"
                "等待趋势态再 BUILD。"
            )
            _upsert_belief(data, "ranging_no_chase_trend", lesson, "ranging")
            added.append("ranging_no_chase_trend")

        # 规则 2：大量 score_low → 提醒勿在弱对齐时硬开
        if score_low >= 5:
            lesson = (
                "趋势分长期偏低时勿用主观强行开仓；等 MTF 共振与 should_open 一致。"
            )
            _upsert_belief(data, "wait_for_score_alignment", lesson, "any")
            added.append("wait_for_score_alignment")

        # 规则 3：亏损占比高 → 略收紧门槛（有界）
        raise_score = False
        losses = int(closed_stats.get("losses") or 0)
        closed = int(closed_stats.get("closed") or 0)
        if closed >= 4 and losses / max(closed, 1) >= 0.6:
            lesson = (
                f"近周中长线亏损占比偏高（{losses}/{closed}）：提高开仓质量，"
                "拒绝叙事驱动、证据不足的 Intent。"
            )
            _upsert_belief(data, "raise_open_quality", lesson, "any")
            added.append("raise_open_quality")
            raise_score = True

        data["last_review_ts"] = now
        data["last_review"] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ranging_blocks": ranging_blocks,
            "score_low": score_low,
            "authority_blocks": authority_blocks,
            "closed_stats": closed_stats,
            "direction_audit_48h": dir_audit,
            "added": added,
        }
        _save(data)

    # 锁外做副作用
    if "ranging_no_chase_trend" in added:
        _apply_owm_nudge(session_id, llm_delta=-0.03)
    if raise_score:
        _apply_runtime_nudge(raise_min_score=True)

    result["ok"] = True
    result["beliefs_added"] = added
    logger.info(
        "[MidLongBelief] review session=%s added=%s ranging_blocks=%s score_low=%s",
        (session_id or "-")[:12], added, ranging_blocks, score_low,
    )
    return result
