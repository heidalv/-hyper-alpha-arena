"""MLTO learning bridge — OWM + post-close."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OWM_SOURCES = (
    "orch", "quant", "analyst", "llm", "learning", "prescreen", "regime",
)

# ─────────────────────────────────────────────────────────────────────
# [阶段3f] OWM delta 相对化（决策4 + 计划 §5.3）
# 旧:绝对 ±0.02/±0.03。阶段3a 把 llm_qual 基础权重 0.04→0.30 后,
#   绝对 ±0.02 对 llm 是 ~6.7% 相对摆动,对 orch(0.12)却是 ~16.7%,
#   对 feedback_loop(0.03)更是 ~67%——相对影响差 13 倍,小权重源被
#   过度调整,OWM 失调。
# 新:delta = 基础权重 × 5%(赢)/−5%(输),invalidation 额外 −10%。
#   反馈与源的基础权重成正比,无论 0.03 还是 0.30 都是 5% 调整。
# OWM 乘子在 hub 仍 clamp 到 [0.5, 1.5](见 decision_hub.fuse_signals)。
# ─────────────────────────────────────────────────────────────────────
_OWM_DELTA_PCT = 0.05              # ±5% of the source's base weight
_OWM_INVALIDATION_PENALTY_PCT = 0.10  # invalidation 退出额外 -10% of base
_OWM_DEFAULT_BASE_WEIGHT = 0.1     # 兜底(prescreen/regime 无对应信号)


# ─────────────────────────────────────────────────────────────────────
# [R21] cited_ids 为空时的 OWM 源兜底策略（可切换，便于回滚）
# 背景：`mlto_memory_events` 长期 0 行 ⇒ `_bump_owm` 里的 cited_ids 恒为空，
#   于是每一笔平仓都被记到 'llm' 源。这等于给 llm 源凭空计入本不属于它的
#   盈亏证据（68 笔 mlto 平仓全部如此），是"来源归因失明"。
#   llm    : 保持历史行为（默认，向后兼容，改动可零风险回滚）
#   unknown: 记为 'unknown'（不映射任何信号 ⇒ 不加虚假信用，诚实但停在原地）
#   lane   : 用 meta 的 entry_source/timeframe_tier 作源（可按车道分别审计）
# ─────────────────────────────────────────────────────────────────────
_OWM_UNKNOWN_ATTRIB_MODES = ("llm", "unknown", "lane")


def _owm_unknown_attrib() -> str:
    v = (os.getenv("MLTO_OWM_UNKNOWN_ATTRIB") or "llm").strip().lower()
    return v if v in _OWM_UNKNOWN_ATTRIB_MODES else "llm"


def _owm_fallback_sources(meta: Dict[str, Any]) -> List[str]:
    """cited_ids 为空时决定把这次盈亏记到哪个源（纯函数，便于单测）。"""
    mode = _owm_unknown_attrib()
    if mode == "unknown":
        return ["unknown"]
    if mode == "lane":
        lane = str(
            meta.get("entry_source") or meta.get("timeframe_tier") or ""
        ).strip().lower()
        return [lane or "unknown"]
    return ["llm"]

# OWM 源(=MltoMemoryEvent.source)→ decision_hub 中的信号名映射。
# 注意:OWM 源命名空间(orch/quant/...)与 Signal.source(framework/llm/...)
# 不同;这里把 OWM 源映射到它代表的信号的基础权重。
_OWM_SOURCE_TO_SIGNAL_LONG = {
    "orch": "orch_long_bias",
    "quant": "quant_alignment",
    "analyst": "analyst_consensus",
    "llm": "llm_qual",
    "learning": "feedback_loop",
}
_OWM_SOURCE_TO_SIGNAL_MID = {
    "orch": "orch_mid_bias",
    "quant": "quant_alignment",
    "analyst": "analyst_consensus",
    "llm": "llm_qual",
    "learning": "feedback_loop",
}


def _base_weight_for_source(source: str, tier: str) -> float:
    """查 decision_hub.WEIGHTS_LONG/MID 得到该 OWM 源对应信号的基础权重。"""
    try:
        from backend.services.mlto.decision_hub import WEIGHTS_LONG, WEIGHTS_MID
        if tier == "long":
            table = WEIGHTS_LONG
            name_map = _OWM_SOURCE_TO_SIGNAL_LONG
        else:
            table = WEIGHTS_MID
            name_map = _OWM_SOURCE_TO_SIGNAL_MID
        sig_name = name_map.get(source)
        if sig_name and sig_name in table:
            return float(table[sig_name])
    except Exception:
        pass
    return _OWM_DEFAULT_BASE_WEIGHT


def _normalize_owm_tier(raw: Any) -> str:
    """OWM 键用 mid/long；平仓 outcome.tier 常被映射成 swing/trend_follow。"""
    t = str(raw or "mid").strip().lower()
    if t in ("long", "trend", "trend_follow", "position"):
        return "long"
    if t in ("mid", "swing"):
        return "mid"
    return "mid"


def _owm_trace_enabled() -> bool:
    """[2026-09-24 新目标 R12] 学习写入路径的 INFO 级追踪开关（默认 true）。

    为什么需要：`mlto_signal_weights` 自 09-18 起停更，而**四个可能原因的外部表现完全一样**
    （表不动）：H1 上游没调 `record_outcome` · H2 被 `_has_postmortem` 短路 ·
    H3 `_bump_owm` 抛异常 · B 上游去重短路。前三者原先分别是 debug/无日志，生产看不到。
    打开后每一步都留 INFO 痕迹，一次平仓即可分辨。回滚：MLTO_LEARNING_TRACE=false。
    """
    return os.getenv("MLTO_LEARNING_TRACE", "true").strip().lower() in ("1", "true", "yes", "on")


def record_outcome(db, outcome, analytics_db=None) -> None:
    meta = outcome.metadata if isinstance(outcome.metadata, dict) else {}
    thesis_id = meta.get("thesis_id")
    if not thesis_id:
        if _owm_trace_enabled():
            logger.info(
                "[MLTO learning][trace] skip=no_thesis strategy=%s symbol=%s "
                "has_pid=%s meta_keys=%s",
                getattr(outcome, "strategy_id", None), getattr(outcome, "symbol", None),
                bool(meta.get("paper_position_id")), ",".join(sorted(meta.keys()))[:120],
            )
        return
    # 同一 thesis 只记一次：防平仓路径双触发刷 postmortem / 双 bump OWM
    # ─────────────────────────────────────────────────────────────────
    # [R22] **去重拆开**。原实现"只要查到 postmortem 就整体 return"，但 postmortem
    # 有**两个写入方**：`learning_bus.enqueue_thesis_postmortem`（异步线程，节流
    # 1h/symbol+tier）与本函数。bus 常常先落库 ⇒ 本函数每次都命中 `has_postmortem`
    # 而在 `_bump_owm` 之前返回。
    # 实测证据（analytics 库 mlto_thesis_events，按 payload 的 `"async": true` 区分）：
    #   bridge 侧 postmortem：09-11 n=6、09-13/14/16 各 1、**09-17~09-23 全 0**、
    #   09-24 仅 1 条(12:02:33)；同期 bus 侧每天 4~12 条。
    #   ⇒ 与 `mlto_signal_weights.updated_at` 停在 09-18 12:40 完全吻合。
    # 且已实测 `_bump_owm` 本身健康：在生产库用 SAVEPOINT 试调一次 →
    #   `sources=[llm] delta=+0.0050`，weight 0.985→0.99、win_count 0→1、updated_at 刷新
    #   （随后回滚，零副作用）。所以病在**门**，不在写入器。
    # 新行为：postmortem 与 OWM bump **各自去重**；OWM 用独立标记事件 `owm_bump`。
    # 回滚：MLTO_OWM_SPLIT_DEDUPE=false → 退回旧行为（有 postmortem 即视为已记）。
    # ─────────────────────────────────────────────────────────────────
    has_pm = _has_postmortem(thesis_id, analytics_db)
    owm_done = _has_owm_bump(thesis_id, analytics_db)
    # [新目标 R4 · 2026-09-28] 逐笔去重：同一 thesis 的不同平仓各自学习一次。
    _pid = meta.get("paper_position_id")
    _pnl = float(getattr(outcome, "pnl", 0) or 0)
    if _per_trade_dedupe():
        has_pm = _has_event_for_trade(thesis_id, "postmortem", _pnl, _pid, analytics_db)
        owm_done = _has_event_for_trade(thesis_id, "owm_bump", _pnl, _pid, analytics_db)
    if not _split_dedupe():
        owm_done = has_pm or owm_done  # 旧行为：postmortem 存在即不再 bump
    if has_pm and owm_done:
        if _owm_trace_enabled():
            logger.info(
                "[MLTO learning][trace] skip=has_postmortem+owm_done thesis=%s pnl=%.4f",
                thesis_id, _pnl,
            )
        logger.debug("[MLTO learning] outcome already recorded thesis=%s", thesis_id)
        return
    cited = meta.get("memory_event_ids") or []
    pnl = _pnl
    session_id = meta.get("session_id") or ""
    # 优先 timeframe_tier（mid/long），再退到 nature 化的 outcome.tier
    tier = _normalize_owm_tier(
        meta.get("timeframe_tier") or meta.get("tier") or getattr(outcome, "tier", None) or "mid"
    )

    if owm_done:
        _owm_res = "skip=owm_already_bumped"
    else:
        _owm_res = _bump_owm(db, session_id, tier, cited, pnl, meta, analytics_db)
        # 只在真正尝试过之后写标记，避免把失败也标记成"已 bump"
        if not str(_owm_res).startswith("err:"):
            _mark_owm_bump(thesis_id, _owm_res, analytics_db, pnl=pnl, pid=_pid)
    if _owm_trace_enabled():
        logger.info(
            "[MLTO learning][trace] done thesis=%s tier=%s session=%s pnl=%.4f cited=%d owm=%s",
            thesis_id, tier, session_id or "(empty)", pnl, len(cited or []), _owm_res,
        )
    if has_pm:
        # [R22] postmortem 已由 learning_bus（或本函数的早前一次）写过 ⇒ 只跳过这次写，
        # 但上面的 OWM bump 已经执行过（这正是本次修复的目的）。
        logger.debug("[MLTO learning] postmortem already present thesis=%s", thesis_id)
        return
    try:
        from backend.services.mlto import thesis_store
        thesis_store.append_event(
            thesis_id,
            "postmortem",
            {
                "pnl": pnl,
                "close_reason": meta.get("close_reason") or outcome.exit_channel,
                "hub_at_entry": meta.get("hub_adjusted_at_entry"),
                # [新目标 R4] 补交易身份，供逐笔去重使用（历史事件没有该键，按 pnl 兜底）
                "paper_position_id": meta.get("paper_position_id"),
            },
            db=analytics_db,
        )
    except Exception as exc:
        logger.debug("[MLTO learning] postmortem skip: %s", exc)


def _has_postmortem(thesis_id: str, analytics_db=None) -> bool:
    """查询 analytics 是否已有该 thesis 的 postmortem（**按 thesis** 的旧口径）。"""
    if not thesis_id:
        return False
    try:
        from backend.services.mlto.db_models import MltoThesisEvent
        if analytics_db is not None:
            n = (
                analytics_db.query(MltoThesisEvent.id)
                .filter(
                    MltoThesisEvent.thesis_id == thesis_id,
                    MltoThesisEvent.event_type == "postmortem",
                )
                .limit(1)
                .first()
            )
            return n is not None
        from backend.database.connection import AnalyticsSessionLocal
        with AnalyticsSessionLocal() as adb:
            n = (
                adb.query(MltoThesisEvent.id)
                .filter(
                    MltoThesisEvent.thesis_id == thesis_id,
                    MltoThesisEvent.event_type == "postmortem",
                )
                .limit(1)
                .first()
            )
            return n is not None
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────
# [新目标 R4 · 2026-09-28] **逐笔去重**：把去重键从 `thesis_id` 改成
# `(thesis_id, pnl, paper_position_id)`。
#
# 为什么：R22 的注释写的是"同一 thesis 只记一次（防**同一笔平仓**双触发）"，
# 但实现按 thesis 去重 —— 而一个 thesis 跨数周、覆盖多笔平仓（实测 thesis
# `25e9c215` 关联 18 笔平仓，却只有 1 次 owm_bump ⇒ **17/18 笔对学习不可见**）。
# 这同时完整解释了"全窗口 158 笔平仓只有 2 条 owm_bump"。
# 新口径：postmortem 与 owm_bump 各按**同一笔交易**去重 —— payload 里带数值 pnl
# （历史 payload 也有），新写入时再补 `paper_position_id`；pnl 是主判据。
# 回滚：`MLTO_OWM_PER_TRADE_DEDUPE=false` ⇒ 退回按 thesis 去重。
# ─────────────────────────────────────────────────────────────────────
def _per_trade_dedupe() -> bool:
    # 注意：必须用两参形式 os.getenv(KEY, default) —— env 治理扫描器只识别两参形态，
    # 单参 + `or` 的形式会漏扫，导致新键无法被 register 脚本自动补登（实测踩过）。
    return (os.getenv("MLTO_OWM_PER_TRADE_DEDUPE", "true") or "true").strip().lower() in (
        "1", "true", "yes", "on")


def _payload_of(ev) -> dict:
    try:
        import json as _json
        raw = getattr(ev, "payload_json", None)
        pl = _json.loads(raw) if isinstance(raw, str) else (raw or {})
        return pl if isinstance(pl, dict) else {}
    except Exception:
        return {}


def _trade_matches(payload: dict, pnl: float, pid) -> bool:
    """同一笔交易的判据：payload 带数值 pnl 且与本次一致；pid 双方都有时才要求相等。"""
    try:
        pl_pnl = payload.get("pnl")
        if pl_pnl is None:
            return False
        if abs(float(pl_pnl) - float(pnl or 0)) > 1e-6:
            return False
    except (TypeError, ValueError):
        return False
    if pid is not None and payload.get("paper_position_id") is not None:
        try:
            if int(payload.get("paper_position_id")) != int(pid):
                return False
        except (TypeError, ValueError):
            pass
    return True


def _has_event_for_trade(
    thesis_id: str, event_type: str, pnl: float, pid=None, analytics_db=None,
) -> bool:
    """该 thesis 的事件里是否已有**同一笔交易**（pnl 匹配）的 event_type。"""
    if not thesis_id:
        return False
    try:
        from backend.services.mlto.db_models import MltoThesisEvent

        def _check(s):
            rows = (
                s.query(MltoThesisEvent)
                .filter(
                    MltoThesisEvent.thesis_id == thesis_id,
                    MltoThesisEvent.event_type == event_type,
                )
                .all()
            )
            return any(_trade_matches(_payload_of(ev), pnl, pid) for ev in rows)

        if analytics_db is not None:
            return _check(analytics_db)
        from backend.database.connection import AnalyticsSessionLocal
        with AnalyticsSessionLocal() as adb:
            return _check(adb)
    except Exception as exc:
        # 读不到时按"未记录"处理：宁可多记一次，也不要像旧行为那样永远不记。
        logger.debug("[MLTO OWM] 逐笔去重查询失败，按未记录处理: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# [R22] OWM bump 的独立去重标记
#   `mlto_thesis_events` 里用 event_type='owm_bump' 作为"这笔平仓的 OWM 已更新过"的凭据。
#   它与 'postmortem' 解耦，因此 learning_bus 抢先写 postmortem 不再会吞掉 OWM 更新。
# ─────────────────────────────────────────────────────────────────────
def _split_dedupe() -> bool:
    """是否启用去重拆分（默认 true = 修复后的行为）。"""
    return (os.getenv("MLTO_OWM_SPLIT_DEDUPE") or "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _has_owm_bump(thesis_id: str, analytics_db=None) -> bool:
    if not thesis_id:
        return False
    try:
        from backend.services.mlto.db_models import MltoThesisEvent
        q = lambda s: (  # noqa: E731
            s.query(MltoThesisEvent.id)
            .filter(
                MltoThesisEvent.thesis_id == thesis_id,
                MltoThesisEvent.event_type == "owm_bump",
            )
            .limit(1)
            .first()
        )
        if analytics_db is not None:
            return q(analytics_db) is not None
        from backend.database.connection import AnalyticsSessionLocal
        with AnalyticsSessionLocal() as adb:
            return q(adb) is not None
    except Exception:
        # 读不到标记时按"未 bump"处理：宁可多 bump 一次，也不要像旧行为那样永远不 bump。
        return False


def _mark_owm_bump(thesis_id: str, res: str, analytics_db=None, pnl=None, pid=None) -> None:
    """写下 OWM 已更新标记；失败只留日志，不影响主流程。

    [新目标 R4] payload 带交易身份（pnl / paper_position_id）供逐笔去重；
    历史事件没有这两个键 ⇒ 按"未记录"处理（正是行为变更的目的）。
    """
    payload: Dict[str, Any] = {"res": str(res)[:160]}
    if pnl is not None:
        payload["pnl"] = float(pnl)
    if pid is not None:
        payload["paper_position_id"] = pid
    try:
        from backend.services.mlto import thesis_store
        thesis_store.append_event(
            str(thesis_id), "owm_bump", payload, db=analytics_db,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[MLTO OWM] 标记写入失败 thesis=%s: %s", thesis_id, exc)


def _bump_owm(db, session_id, tier, cited_ids, pnl, meta, analytics_db):
    """返回一行摘要字符串（供 R12 的 trace 使用）；失败时返回 `err:...`。"""
    adb = analytics_db or db
    if adb is None:
        return "err:no_db"
    try:
        from backend.services.mlto.db_models import MltoMemoryEvent, MltoSignalWeight
        sources: List[str] = []
        if cited_ids:
            rows = adb.query(MltoMemoryEvent).filter(MltoMemoryEvent.event_id.in_(list(cited_ids)[:20])).all()
            sources = list({r.source for r in rows if r.source})
        if not sources:
            sources = _owm_fallback_sources(meta)
            logger.info(
                "[OWM] cited_ids 为空(记忆事件缺失) -> 源兜底 mode=%s sources=%s",
                _owm_unknown_attrib(), sources,
            )
        close_reason = (meta.get("close_reason") or "")
        is_invalidation = "invalidation" in close_reason
        for src in sources:
            base_w = _base_weight_for_source(src, tier)
            delta = base_w * _OWM_DELTA_PCT if pnl > 0 else -(base_w * _OWM_DELTA_PCT)
            if is_invalidation:
                delta -= base_w * _OWM_INVALIDATION_PENALTY_PCT
            row = (
                adb.query(MltoSignalWeight)
                .filter(
                    MltoSignalWeight.session_id == session_id,
                    MltoSignalWeight.tier == tier,
                    MltoSignalWeight.source == src,
                )
                .first()
            )
            if not row:
                row = MltoSignalWeight(session_id=session_id, tier=tier, source=src, weight=1.0)
                adb.add(row)
            row.weight = max(0.5, min(1.5, float(row.weight or 1) + delta))
            if pnl > 0:
                row.win_count = int(row.win_count or 0) + 1
            else:
                row.loss_count = int(row.loss_count or 0) + 1
        adb.commit()
        return "sources=[%s] delta=%+.4f" % (",".join(sources), float(delta))
    except Exception as exc:
        # [R12] 原为 logger.debug ⇒ 生产不可见。`_bump_owm` 抛异常是"表不动"的可能原因之一，
        # 必须至少 WARNING 级，否则永远查不到。
        logger.warning("[MLTO OWM] bump 失败（学习权重未更新）: %s", exc)
        try:
            adb.rollback()
        except Exception:
            pass
        return "err:%s" % type(exc).__name__


def load_owm_weights(session_id: str, tier: str, db) -> Dict[str, float]:
    if db is None:
        return {}
    try:
        from backend.services.mlto.db_models import MltoSignalWeight
        rows = (
            db.query(MltoSignalWeight)
            .filter(MltoSignalWeight.session_id == session_id, MltoSignalWeight.tier == tier)
            .all()
        )
        return {r.source: float(r.weight or 1.0) for r in rows}
    except Exception:
        return {}


def get_learning_metrics(session_id: str, db) -> Dict[str, Any]:
    """Thesis hit rate / premature open / source contribution."""
    out = {
        "thesis_hit_rate": None,
        "premature_open_rate": None,
        "evidence_source_contribution": {},
        "thesis_drift_resets": 0,
        "sample_count": 0,
    }
    if db is None:
        return out
    try:
        from backend.services.mlto.db_models import MltoSignalWeight, MltoThesisEvent
        resets = (
            db.query(MltoThesisEvent)
            .filter(MltoThesisEvent.event_type.in_(("regime_reset", "macro_phase_shift")))
            .count()
        )
        out["thesis_drift_resets"] = resets

        import json as _json
        postmortems = (
            db.query(MltoThesisEvent)
            .filter(MltoThesisEvent.event_type == "postmortem")
            .all()
        )
        premature = 0
        closed = 0
        for ev in postmortems:
            try:
                payload = _json.loads(ev.payload_json or "{}")
            except Exception:
                payload = {}
            closed += 1
            reason = str(payload.get("close_reason") or "").lower()
            if "master" in reason and float(payload.get("pnl") or 0) < 0:
                premature += 1
        if closed >= 3:
            out["premature_open_rate"] = round(premature / closed, 3)

        rows = db.query(MltoSignalWeight).filter(MltoSignalWeight.session_id == session_id).all()
        if not rows and session_id == "":
            rows = db.query(MltoSignalWeight).all()
        total_w = sum(r.win_count or 0 for r in rows)
        total_l = sum(loss_count if (loss_count := r.loss_count) else 0 for r in rows)
        out["sample_count"] = total_w + total_l
        if total_w + total_l >= 5:
            out["thesis_hit_rate"] = round(total_w / max(total_w + total_l, 1), 3)
        out["evidence_source_contribution"] = {
            r.source: {"weight": r.weight, "wins": r.win_count, "losses": r.loss_count}
            for r in rows
        }
    except Exception:
        pass
    return out
