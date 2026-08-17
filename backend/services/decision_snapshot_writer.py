"""DecisionSnapshotWriter — 统一决策快照 v2 写入 + HMAC 链。"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 重复快照抑制（2026-08-15 修复：决策流滚屏刷屏根因） ──
# 短线 10s/tick、中线 45s/tick 下，同一 (账户,币种,周期,动作,理由) 的决策
# 每个 tick 都会生成一条内容几乎完全相同的快照，导致决策流/滚屏日志被垃圾刷满。
# 这里在写入端做窗口去重：相同签名在窗口内只落库第一条，后续重复静默跳过。
# - 理由变化（score/门控细节变化）会改变签名 → 正常记录，不丢信息。
# - executed 回写（快照已有 id）不受去重影响。
_DEDUP_LOCK = threading.Lock()
_DEDUP_STATE: Dict[str, float] = {}
_DEDUP_WINDOW_SEC = 120.0
_DEDUP_MAX_ENTRIES = 8000


def _dedup_signature(snap) -> str:
    # [P0-4] 原签名只看 account|symbol|tier|action|reason[:80]：
    # 同币种同动作同模板措辞的【不同决策】（不同策略/置信度/regime）在 120s 内
    # 会被静默丢弃，导致平仓盈亏回写匹配不到正确快照。加入 strategy_id 与置信度。
    reason = (getattr(snap, "ai_reasoning", "") or "")[:80]
    return "|".join(
        str(x)
        for x in (
            getattr(snap, "account_id", 0),
            getattr(snap, "strategy_id", "") or "",
            getattr(snap, "symbol", ""),
            getattr(snap, "tier", ""),
            getattr(snap, "action", ""),
            round(float(getattr(snap, "confidence", 0) or 0), 2),
            reason,
        )
    )


def _dedup_check(snap) -> bool:
    """返回 True=放行写入；False=窗口内重复，跳过。"""
    if getattr(snap, "id", None) is not None:
        return True  # 已入库对象的回写（executed 标记等）不去重
    # [P0-4] 带唯一键（proposal_id/trace_id）的决策不去重——唯一键天然区分决策；
    # 去重只作用于无唯一键的滚动 tick 噪音快照。
    _pid = getattr(snap, "proposal_id", None) or None
    _tid = getattr(snap, "trace_id", None) or None
    if _pid or _tid:
        return True
    sig = _dedup_signature(snap)
    now = time.time()
    with _DEDUP_LOCK:
        last = _DEDUP_STATE.get(sig)
        if last is not None and now - last < _DEDUP_WINDOW_SEC:
            return False
        if len(_DEDUP_STATE) > _DEDUP_MAX_ENTRIES:
            cutoff = now - _DEDUP_WINDOW_SEC * 2
            for k in [k for k, v in _DEDUP_STATE.items() if v < cutoff]:
                _DEDUP_STATE.pop(k, None)
        _DEDUP_STATE[sig] = now
        return True

# JSONB 列在写库时是严格 JSON 序列化（不像 canonical_json 那样带 default=str 兜底），
# 如果快照里混入了 DataFrame/Series/numpy 等对象会直接抛错并让整次 persist 失败。
# 这里在落库前统一"降级"成纯 JSON 安全的结构，兜底 default=str，避免因为上游偶尔
# 塞进一个 DataFrame 就导致该轮决策快照整体丢失。
_JSON_FIELDS = (
    "market_snapshot_json",
    "proposal_json",
    "evaluate_verdict_json",
    "gate_blocks_json",
    "orchestrator_json",
)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


class DecisionSnapshotWriter:
    """构建并持久化 DecisionSnapshot v2。"""

    @classmethod
    def build(
        cls,
        *,
        session_id: Optional[int],
        strategy_id: str = "",
        symbol: str,
        tier: str,
        action: str,
        confidence: float,
        reasoning: str = "",
        market_snapshot: Optional[dict] = None,
        proposal: Optional[dict] = None,
        evaluate_verdict: Optional[dict] = None,
        source_lane: str = "",
        trace_id: str = "",
        proposal_id: str = "",
        executed: bool = False,
        execution_channel: str = "",
        orchestrator: Optional[dict] = None,
        use_audit_chain: bool = True,
        account_id: int = 0,
        mode: str = "paper",
        content_hash: Optional[str] = None,
        prev_hash: Optional[str] = None,
    ):
        from backend.database.models import DecisionSnapshot
        from backend.services.audit_chain_service import append_to_chain, sha256_content

        mkt = dict(market_snapshot or {})
        if orchestrator:
            mkt.setdefault("orchestrator", orchestrator)

        proposal_json = proposal or {}
        verdict_json = evaluate_verdict or {}
        canonical = {
            "symbol": symbol,
            "tier": tier,
            "action": action,
            "proposal": proposal_json,
            "verdict": verdict_json,
        }

        if use_audit_chain and not content_hash:
            hashes = append_to_chain(canonical, account_id=account_id, mode=mode)
            content_hash = hashes.get("content_hash")
            prev_hash = hashes.get("prev_hash")
        elif not content_hash:
            content_hash = sha256_content(canonical)

        snap = DecisionSnapshot(
            session_id=session_id,
            strategy_id=strategy_id or None,
            symbol=str(symbol).upper(),
            tier=(tier or "mid").lower(),
            market_snapshot_json=mkt,
            ai_reasoning=(reasoning or "")[:2000] or None,
            action=action,
            direction="buy" if action == "buy" else ("sell" if action == "sell" else action),
            confidence=float(confidence or 0),
            regime_at_decision=mkt.get("market_cycle") or mkt.get("regime"),
            volatility_at_decision=float(mkt.get("volatility_value", 0) or 0) or None,
        )
        for attr, val in (
            ("proposal_id", proposal_id or proposal_json.get("proposal_id")),
            ("trace_id", trace_id or proposal_json.get("trace_id")),
            ("source_lane", source_lane or proposal_json.get("source_lane")),
            ("proposal_json", proposal_json or None),
            ("evaluate_verdict_json", verdict_json or None),
            ("gate_blocks_json", verdict_json.get("gate_blocks")),
            ("orchestrator_json", orchestrator or mkt.get("orchestrator")),
            ("executed", executed),
            ("execution_channel", execution_channel or None),
            ("content_hash", content_hash),
            ("prev_hash", prev_hash),
        ):
            if hasattr(DecisionSnapshot, attr):
                setattr(snap, attr, val)
        return snap

    @classmethod
    def persist(cls, snap, *, mark_executed: Optional[bool] = None) -> bool:
        from backend.database.connection import AnalyticsSessionLocal

        if not _dedup_check(snap):
            return False  # 窗口内重复快照，静默跳过（决策流去重）
        if mark_executed is not None and hasattr(snap, "executed"):
            snap.executed = mark_executed
        for _field in _JSON_FIELDS:
            if hasattr(snap, _field):
                try:
                    setattr(snap, _field, _json_safe(getattr(snap, _field)))
                except Exception:
                    pass
        adb = AnalyticsSessionLocal()
        try:
            adb.add(snap)
            adb.commit()
            return True
        except Exception as err:
            logger.warning("[DecisionSnapshotWriter] persist 失败: %s", err)
            try:
                adb.rollback()
            except Exception:
                pass
            return False
        finally:
            adb.close()

    @classmethod
    def commit_batch(cls, db, snapshots: List) -> int:
        if not snapshots:
            return 0
        kept = [s for s in snapshots if _dedup_check(s)]
        if not kept:
            return 0
        try:
            for s in kept:
                for _field in _JSON_FIELDS:
                    if hasattr(s, _field):
                        try:
                            setattr(s, _field, _json_safe(getattr(s, _field)))
                        except Exception:
                            pass
                db.add(s)
            db.commit()
            return len(kept)
        except Exception as err:
            logger.warning("[DecisionSnapshotWriter] batch commit 失败: %s", err)
            try:
                db.rollback()
            except Exception:
                pass
            return 0


decision_snapshot_writer = DecisionSnapshotWriter()
