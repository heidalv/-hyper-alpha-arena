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


def _coerce_regime_label(raw: Any) -> Optional[str]:
    """regime_at_decision 列是 VARCHAR；market_data.regime 常为 dict。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        name = raw.get("name") or raw.get("regime") or raw.get("label")
        return str(name)[:64] if name else None
    text = str(raw).strip()
    return text[:64] if text else None


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
        factor_votes: Optional[list] = None,
    ):
        from backend.database.models import DecisionSnapshot
        from backend.services.audit_chain_service import append_to_chain, sha256_content

        mkt = dict(market_snapshot or {})
        if orchestrator:
            mkt.setdefault("orchestrator", orchestrator)
        # [流B 2026-09-17] 因子票入快照（JSON列免迁移）——学习闭环补因子维度（断点5）
        if factor_votes:
            mkt.setdefault("factor_votes", list(factor_votes)[:8])

        proposal_json = proposal or {}
        verdict_json = dict(evaluate_verdict or {})

        # [2026-10-03 修复 · 决策拦截原因从未落库]
        # 实测（近 30 天 12,219 条快照）：`gate_blocks_json` **全表为空**、
        # `evaluate_verdict_json.reason`/`code_reason` 是空串 ⇒ 11,990 条"未执行"决策
        # **无法归因是哪一层挡的**（block-pattern 学习因此无结构化输入，只能靠 ai_reasoning 自由文本分类）。
        # 这里在**不伪造数据**的前提下补齐：
        #   1) reason/code_reason 空 → 依次尝试调用方已有的
        #      evaluate_verdict.{code_reason,reason,gate_reason} → proposal.{block_reason,reason,code_reason}
        #      → 最后用 ai 自由文本前 120 字，并标 `reason_source="derived_from_reasoning"`；
        #   2) gate_blocks 缺 → 仅在**未执行**时合成一条 `[{layer, rule, reason, auto_fill: true}]`，
        #      显式标注 auto_fill，避免被误读成真实闸门回执。
        if not str(verdict_json.get("reason") or "").strip() and not str(verdict_json.get("code_reason") or "").strip():
            _derived = (
                verdict_json.get("gate_reason")
                or proposal_json.get("block_reason")
                or proposal_json.get("code_reason")
                or proposal_json.get("reason")
                or ""
            )
            _src = "caller" if _derived else ""
            # [2026-10-03 ②] 代码里强制 hold 的地方**本来就会给 reasoning 加前缀标签**
            # （实测约定：`arb_conflict:` / `cycle_conflict:` / `data_gate:` / `midlong_choke:` …）。
            # 这里把该标签提取成**结构化 key**（code_reason=<tag>），
            # 使 `master 车道为什么只会 hold` 这类问题可按标签精确计数，而不是靠自由文本猜。
            _tag = ""
            if reasoning:
                import re as _re

                _m = _re.match(r"^\s*\[?([a-z][a-z0-9_]{2,30})\]?\s*[:：]", str(reasoning))
                if _m:
                    _tag = _m.group(1)
            if not _derived and reasoning:
                _derived = str(reasoning).strip()[:120]
                _src = "derived_from_reasoning"
            if _derived or _tag:
                _final = _tag or str(_derived)[:200]
                if not str(verdict_json.get("code_reason") or "").strip():
                    verdict_json["code_reason"] = str(_final)[:200]
                if not str(verdict_json.get("reason") or "").strip():
                    verdict_json["reason"] = str(_derived or _tag)[:200]
                verdict_json["reason_source"] = ("tag_from_reasoning" if _tag and _src == "derived_from_reasoning"
                                                 else (_src or "derived_from_reasoning"))
        # [2026-10-04 工作流⑤-a] hold 决策附带**方向倾向**（lean / lean_strength）——
        # **只写快照、不参与任何判定**（零行为风险）。
        # 依据：实测 master 车道 9,555 条决策 direction 全为 'hold' ⇒ 该车道**从不表达方向**
        # ⇒ 前向标注无样本（12 天 0 条）⇒ 无法校准、无法评估、无法改进，形成死循环。
        # 这里把**已在载荷里存在**的方向证据（如辩论净倾向 net_sentiment / bull-bear 差）
        # 结构化记录下来；**没有证据就什么都不写**（绝不造数）。
        try:
            if str(action or "").strip().lower() in ("hold", ""):
                _src_val = None
                _src_name = ""
                for _k in ("net_sentiment", "consensus_net_sentiment", "sentiment"):
                    if isinstance(proposal_json.get(_k), (int, float)):
                        _src_val, _src_name = float(proposal_json[_k]), _k
                        break
                if _src_val is None:
                    for _k in ("net_sentiment", "sentiment"):
                        if isinstance(verdict_json.get(_k), (int, float)):
                            _src_val, _src_name = float(verdict_json[_k]), "verdict." + _k
                            break
                if _src_val is not None and abs(_src_val) > 1e-9:
                    verdict_json["lean"] = "long" if _src_val > 0 else "short"
                    verdict_json["lean_strength"] = round(min(1.0, abs(_src_val)), 4)
                    verdict_json["lean_source"] = _src_name
                    verdict_json["lean_effect"] = "observability_only"  # 明确标注不参与判定
        except Exception as _lean_err:  # noqa: BLE001
            logger.debug("[DecisionSnapshot] lean 标注跳过: %s", _lean_err)

        if not verdict_json.get("gate_blocks") and executed is not True:
            verdict_json["gate_blocks"] = [{
                "layer": verdict_json.get("layer") or "unknown",
                "rule": verdict_json.get("rule") or "",
                "reason": verdict_json.get("code_reason") or verdict_json.get("reason") or "not_recorded",
                "auto_fill": True,
            }]
        # [2026-10-03 用户指令「补齐」· 面板①"决策→血缘账本 停滞：真实交易决策未入账"]
        # 只对**真正执行**的决策入账（`source=live_decision`），避免把每轮上千条 hold 灌进账本；
        # fail-open：账本异常绝不影响决策写入。
        if executed is True:
            try:
                from backend.services.trade_learning_ledger import record_decision

                record_decision(
                    symbol=symbol, action=str(action or ""),
                    lane=str(verdict_json.get("lane") or proposal_json.get("lane") or ""),
                    tier=str(tier or ""),
                    confidence=proposal_json.get("confidence"),
                    executed=True,
                    trace_id=str(trace_id or content_hash or proposal_id or ""),
                    code_reason=str(verdict_json.get("code_reason") or ""),
                    account_id=account_id,
                )
            except Exception as _ledger_err:  # noqa: BLE001
                logger.debug("[DecisionSnapshot] 决策入账跳过: %s", _ledger_err)

        # [2026-10-04 工作流①-b] 持久化标注行（**独立于快照保留期**）。
        # 实测阻断：快照只保留 ~8 天 < 7 天标注窗口 ⇒ 可标注样本从 335 掉到 30，永远无法累积。
        # 这里在决策时固化一行（entry_price 可后补），只增不删 ⇒ 标注不再依赖快照存活。
        # 开关 FWD_LABEL_TABLE=off|shadow|on（默认 off = 行为与今日一致）；fail-open。
        try:
            from backend.services.learning_core.decision_labels import record_decision_label

            _lid_src = str(trace_id or content_hash or proposal_id or "")
            if _lid_src:
                import hashlib as _hl

                _did = int(_hl.sha1(_lid_src.encode("utf-8")).hexdigest()[:15], 16)
                record_decision_label(
                    decision_id=_did,
                    symbol=symbol,
                    action=str(action or ""),
                    direction=str(direction or ""),
                    confidence=float(proposal_json.get("confidence") or 0.0),
                    lane=str(verdict_json.get("lane") or proposal_json.get("lane") or ""),
                    tier=str(tier or ""),
                    trace_id=_lid_src,
                    source="live_decision",
                )
        except Exception as _lbl_err:  # noqa: BLE001
            logger.debug("[DecisionSnapshot] 标注行写入跳过: %s", _lbl_err)

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
            regime_at_decision=_coerce_regime_label(
                mkt.get("market_cycle") or mkt.get("regime")
            ),
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
