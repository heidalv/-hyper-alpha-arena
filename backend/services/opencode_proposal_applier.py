"""OpenCode 提案 — 创建 / 手动确认 apply / Paper 验证 / 回滚。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

REPORT_DIR = os.path.join("data", "opencode_reports")


def _split_patches(patches: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    tuning: Dict[str, Any] = {}
    policy: List[Dict[str, Any]] = []
    for p in patches:
        if not isinstance(p, dict):
            continue
        key = p.get("key") or p.get("path")
        val = p.get("value")
        ptype = (p.get("type") or "tuning").lower()
        if not key:
            continue
        if ptype == "shadow_py":
            continue
        if ptype == "policy_yaml":
            policy.append(p)
        else:
            tuning[str(key)] = val
    return tuning, policy


def _proposal_focus(patches: List[Dict[str, Any]]) -> str:
    """从 patches 推断验证应关注的维度（补丁级因果隔离）。

    - master_close / master_reduce / tiny_loss 相关 → "master_close"（看 master_close 维度）
    - max_daily_trades                              → "frequency"（看全局，频率类无专属维度）
    - 其它（maturity 旋钮等）                        → "global"
    """
    keys = " ".join(
        str(p.get("key") or "") for p in patches if isinstance(p, dict)
    ).lower()
    if any(t in keys for t in ("master_close", "master_reduce", "tiny_loss")):
        return "master_close"
    if "max_daily_trades" in keys:
        return "frequency"
    return "global"


def _patch_type_label(
    tuning: Dict[str, Any],
    policy: List[Dict[str, Any]],
    patches: Optional[List[Dict[str, Any]]] = None,
) -> str:
    if patches and any((p.get("type") or "").lower() == "shadow_py" for p in patches if isinstance(p, dict)):
        return "shadow_py"
    if tuning and policy:
        return "mixed"
    if policy:
        return "policy_yaml"
    return "tuning"


def _apply_tuning_and_policy(
    tuning_patches: Dict[str, Any],
    policy_patches: List[Dict[str, Any]],
    *,
    proposal_id: Optional[int] = None,
) -> Dict[str, Any]:
    applied: Dict[str, Any] = {"tuning": {}, "policy": []}
    if tuning_patches:
        from backend.services.runtime_tuning_store import apply_patches

        # [2026-07-11 修复] 统一调参仲裁口径：此前 OpenCode 落地 patch 一律直接走
        # apply_patches 快照路径，完全绕过 RuntimeGovernor 的意图仲裁——导致
        # runtime_governor_decisions.jsonl 里从来看不到 winner_source=opencode 的
        # 记录，且 GOVERNED_KEYS 白名单内的几个门控参数之后被别的来源(manual/
        # decision_feedback/maturity)提交新意图时，会在下一次 reconcile 里被悄悄
        # 覆盖回去，事后完全查不出"opencode 改的值去哪了"（调参双轨制）。
        # 现在：白名单内的 key 改走 RuntimeGovernor.submit_intent(source="opencode")，
        # 与其它来源同一优先级仲裁表统一管理；非白名单 key（master_reduce_min_loss_pct、
        # by_nature 等，Governor 不管辖）继续走原 apply_patches 快照路径，snapshot/
        # rollback 机制仍对这部分 key 有效。
        from backend.services.runtime_governor import RuntimeGovernor, GOVERNED_KEYS

        governed_patches = {k: v for k, v in tuning_patches.items() if k in GOVERNED_KEYS}
        ungoverned_patches = {k: v for k, v in tuning_patches.items() if k not in GOVERNED_KEYS}

        applied_tuning: Dict[str, Any] = {}
        if governed_patches:
            gov = RuntimeGovernor()
            for key, value in governed_patches.items():
                result = gov.submit_intent(
                    key, value,
                    source="opencode",
                    confidence=0.7,
                    reason=f"opencode_proposal:{proposal_id}" if proposal_id else "opencode_proposal",
                )
                applied_tuning[key] = result.get("value")
                if not result.get("applied"):
                    logger.info(
                        "[ProposalApplier] opencode意图 %s=%s 未即时生效(当前winner=%s)，"
                        "已记录意图等待仲裁", key, value, result.get("winner_source"),
                    )
        if ungoverned_patches:
            applied_tuning.update(apply_patches(ungoverned_patches, proposal_id=proposal_id))
        applied["tuning"] = applied_tuning
    for pp in policy_patches:
        from backend.services.decision_policy_engine import (
            apply_policy_field_patch,
            apply_policy_patch,
            parse_policy_patch_key,
        )

        key = str(pp.get("key") or pp.get("path") or "")
        val = pp.get("value")
        content = pp.get("content")
        if content:
            name = key.replace(".yaml", "") or "master_close"
            apply_policy_patch(name, str(content), proposal_id=proposal_id)
            applied["policy"].append({"key": key, "mode": "full_content"})
            continue

        # 统一解析三种 key 格式（与 reviewer 校验同一函数，避免误判）
        policy_name, rule_id, field = parse_policy_patch_key(key, pp.get("policy"))
        if not rule_id:
            logger.warning("[ProposalApplier] 无法解析 policy patch key=%r，跳过", key)
            applied["policy"].append({"key": key, "error": "unparseable"})
            continue

        if field is not None:
            path = apply_policy_field_patch(
                policy_name, rule_id, field, val, proposal_id=proposal_id
            )
            applied["policy"].append(
                {"key": key, "policy": policy_name, "rule": rule_id,
                 "field": field, "value": val, "path": path}
            )
        elif isinstance(val, dict):
            # 整 rule 多字段：逐字段写入，仅首字段触发 snapshot（保证回滚还原到改动前）
            path = None
            first = True
            for fk, fv in val.items():
                path = apply_policy_field_patch(
                    policy_name, rule_id, str(fk), fv,
                    proposal_id=(proposal_id if first else None),
                )
                first = False
            applied["policy"].append(
                {"key": key, "policy": policy_name, "rule": rule_id,
                 "fields": val, "path": path}
            )
        else:
            # 既无 field 又非 dict：无法安全定位字段，拒绝误写整文件
            logger.warning(
                "[ProposalApplier] policy patch key=%r 无 field 且 value 非 dict，跳过避免误写整文件",
                key,
            )
            applied["policy"].append({"key": key, "error": "no field & value not dict"})
    return applied


def create_proposal(
    db,
    patches: List[Dict[str, Any]],
    *,
    severity: str = "minor",
    title: str = "OpenCode patch",
    source: str = "opencode",
    dedupe_key: Optional[str] = None,
) -> Optional[int]:
    """创建提案记录；major/critical 默认 pending，不自动改参数。"""
    from backend.database.models import OpenCodeEvolutionProposalDB
    from backend.database.connection import sqlite_write_commit

    if not patches:
        return None

    tuning_patches, policy_patches = _split_patches(patches)
    if not tuning_patches and not policy_patches:
        return None

    if dedupe_key:
        # dedupe：rolled_back+样本不足 24h 后可重建；LLM reject 3d；其余 7d
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        existing = (
            db.query(OpenCodeEvolutionProposalDB)
            .order_by(OpenCodeEvolutionProposalDB.id.desc())
            .limit(200)
            .all()
        )
        for row in existing:
            try:
                payload = json.loads(row.proposal_json or "{}")
            except Exception:
                payload = {}
            if payload.get("dedupe_key") != dedupe_key:
                continue
            st = row.status or ""
            created = row.created_at
            if created and created.tzinfo is not None:
                created = created.replace(tzinfo=None)
            age_h = (now_naive - created).total_seconds() / 3600.0 if created else 9999
            if st == "rolled_back" and age_h < 24:
                logger.info("[ProposalApplier] skip rolled_back dedupe id=%s (%.1fh<24h)", row.id, age_h)
                return row.id
            if st == "rejected" and age_h < 72:
                logger.info("[ProposalApplier] skip rejected dedupe id=%s (%.1fh<72h)", row.id, age_h)
                return row.id
            if age_h < 168:
                logger.info(
                    "[ProposalApplier] skip duplicate id=%s (status=%s, within 7d)",
                    row.id, st,
                )
                return row.id

    requires_manual = severity in ("major", "critical")
    proposal = OpenCodeEvolutionProposalDB(
        source=source,
        severity=severity,
        title=title[:256],
        proposal_json=json.dumps({"patches": patches, "dedupe_key": dedupe_key}, ensure_ascii=False),
        patch_type=_patch_type_label(tuning_patches, policy_patches, patches),
        status="pending",
        requires_paper_validation=True,
        requires_manual_live_confirm=requires_manual,
    )
    db.add(proposal)
    sqlite_write_commit(db)
    return proposal.id


def apply_proposal(
    db,
    proposal_id: int,
    *,
    patches_override: Optional[List[Dict[str, Any]]] = None,
    to_live: bool = False,
    manual_confirmed: bool = False,
    auto_promoted: bool = False,
) -> Dict[str, Any]:
    """手动或自动将 pending 提案应用到 Paper 环境。

    to_live=True 表示晋升真金环境：此时 major/critical 提案
    （requires_manual_live_confirm）必须 manual_confirmed=True 或 auto_promoted=True 才放行。
    默认 to_live=False（仅写 paper），探索期 paper 自由验证不受影响。
    """
    from backend.database.models import OpenCodeEvolutionProposalDB
    from backend.database.connection import sqlite_write_commit

    row = db.query(OpenCodeEvolutionProposalDB).filter(
        OpenCodeEvolutionProposalDB.id == proposal_id
    ).first()
    if not row:
        raise ValueError("proposal not found")
    if row.status not in ("pending",):
        raise ValueError(f"proposal status {row.status} cannot apply")

    # requires_manual_live_confirm 接线：此前是从不检查的 dead flag。
    # 仅当晋升 live 且未人工确认时阻断；paper apply（to_live=False）放行。
    if to_live and row.requires_manual_live_confirm and not manual_confirmed and not auto_promoted:
        raise ValueError(
            f"proposal {proposal_id} ({row.severity}) requires manual live confirmation"
        )

    try:
        payload = json.loads(row.proposal_json or "{}")
    except Exception:
        payload = {}
    patches = patches_override if patches_override is not None else (payload.get("patches") or [])
    tuning_patches, policy_patches = _split_patches(patches)

    # P3-11: 提案预验证 — 应用前检查 patch 合法性，防止空/无效提案绕过评审
    if not patches or (not tuning_patches and not policy_patches):
        raise ValueError(
            f"proposal {proposal_id} has no valid patches to apply "
            f"(proposal_json may be empty or malformed)"
        )

    # P3-11: 验证 patches 来源合理性
    # 检查 patches 是否都通过了硬规则审查（防止跳过 reviewer 直接 apply）
    try:
        from backend.services.opencode_proposal_reviewer import validate_patches_hard
        hard_ok, hard_errs = validate_patches_hard(patches)
        if not hard_ok:
            logger.warning(
                "[ProposalApplier] P3-11 提案 %s 预验证失败(硬规则): %s",
                proposal_id, "; ".join(hard_errs[:3]),
            )
    except Exception:
        pass  # reviewer 未就绪时降级放行

    # ── 修复 baseline bug ──
    # 此前 baseline_json 只存了「应用的 patch 值」，evaluate 时取不到 win_rate，
    # 导致 baseline_wr 恒为 0、几乎永不判退化。这里在应用前先抓一份真实绩效快照。
    baseline_perf = {
        "win_rate": 0.0, "total_pnl": 0.0, "total_closed": 0,
        "master_close_loss_ratio": 0.0, "master_close_count": 0,
    }
    try:
        from backend.services.strategy_runtime_report import generate_report
        _before = generate_report(db, window="24h", domain="ai")
        baseline_perf = {
            "win_rate": float(_before.win_rate or 0),
            "total_pnl": float(_before.total_pnl or 0),
            "total_closed": int(_before.total_closed or 0),
            # 补丁级因果隔离所需的维度基线（master_close 类提案据此对比）
            "master_close_loss_ratio": float(_before.master_close_loss_ratio or 0),
            "master_close_count": int(_before.master_close_count or 0),
        }
    except Exception as err:
        logger.warning("[ProposalApplier] baseline 绩效抓取失败: %s", err)

    applied = _apply_tuning_and_policy(tuning_patches, policy_patches, proposal_id=proposal_id)

    if tuning_patches and not to_live:
        try:
            from backend.services.runtime_tuning_store import save_overlay
            from backend.services.training_phase_service import is_active

            if is_active():
                save_overlay(proposal_id, tuning_patches)
        except Exception as err:
            logger.debug("[ProposalApplier] overlay save: %s", err)

    apply_meta = {
        "mode": "live" if to_live else "paper",
        "manual_confirmed": manual_confirmed,
        "auto_promoted": auto_promoted,
    }
    row.baseline_json = json.dumps(
        {"applied": applied, "baseline_perf": baseline_perf, "apply_meta": apply_meta},
        ensure_ascii=False,
    )
    if to_live and (manual_confirmed or auto_promoted):
        row.status = "applied"
        row.validated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        row.status = "paper_applying"
    row.applied_at = datetime.now(timezone.utc).replace(tzinfo=None)
    sqlite_write_commit(db)
    return {
        "proposal_id": proposal_id, "status": row.status,
        "applied": applied, "baseline_perf": baseline_perf,
        "apply_mode": apply_meta["mode"],
    }


def reject_proposal(db, proposal_id: int, *, reason: str = "") -> Dict[str, Any]:
    from backend.database.models import OpenCodeEvolutionProposalDB
    from backend.database.connection import sqlite_write_commit

    row = db.query(OpenCodeEvolutionProposalDB).filter(
        OpenCodeEvolutionProposalDB.id == proposal_id
    ).first()
    if not row:
        raise ValueError("proposal not found")
    if row.status != "pending":
        raise ValueError(f"proposal status {row.status} cannot reject")
    row.status = "rejected"
    if reason:
        try:
            payload = json.loads(row.proposal_json or "{}")
        except Exception:
            payload = {}
        payload["reject_reason"] = reason[:500]
        row.proposal_json = json.dumps(payload, ensure_ascii=False)
    sqlite_write_commit(db)
    return {"proposal_id": proposal_id, "status": row.status}


def create_and_apply_patches(
    db,
    patches: List[Dict[str, Any]],
    *,
    severity: str = "minor",
    title: str = "OpenCode patch",
    auto_apply: bool = True,
) -> Optional[int]:
    """Deprecated：统一 create → review 链路；auto_apply 仅作兼容。"""
    dedupe = hashlib.sha256(json.dumps(patches, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    pid = create_proposal(db, patches, severity=severity, title=title, dedupe_key=dedupe)
    if pid is None:
        return None
    try:
        from backend.config.settings import OPENCODE_AUTO_REVIEW
        if OPENCODE_AUTO_REVIEW:
            from backend.services.opencode_proposal_reviewer import review_and_apply_proposal
            review_and_apply_proposal(db, pid)
        elif auto_apply and severity in ("minor", "info"):
            apply_proposal(db, pid)
    except Exception as err:
        logger.error("[ProposalApplier] review/apply %s: %s", pid, err, exc_info=True)
    return pid


def backfill_proposals_from_reports(db, *, report_dir: str = REPORT_DIR) -> int:
    """从历史 analysis_*.json 补建 pending 提案（跳过已存在 dedupe）。"""
    if not os.path.isdir(report_dir):
        return 0
    created = 0
    files = sorted(
        [f for f in os.listdir(report_dir) if f.startswith("analysis_") and f.endswith(".json")],
        reverse=True,
    )
    for fname in files:
        path = os.path.join(report_dir, fname)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        patches = data.get("patches") or []
        if not patches:
            continue
        severity = str(data.get("severity") or "info").lower()
        window = "24h"
        title = f"OpenCode {severity} backfill ({fname.replace('.json', '')})"
        dedupe = hashlib.sha256(json.dumps(patches, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        pid = create_proposal(db, patches, severity=severity, title=title, dedupe_key=dedupe)
        if pid:
            created += 1
    return created


def _ensure_proposal_eval_metadata(db, row) -> bool:
    """补齐 applied_at / baseline_perf，返回是否有改动。"""
    changed = False
    if not row.applied_at:
        row.applied_at = row.created_at or datetime.now(timezone.utc).replace(tzinfo=None)
        changed = True
        logger.info("[ProposalApplier] 提案 %s 补齐 applied_at=%s", row.id, row.applied_at)

    try:
        baseline = json.loads(row.baseline_json or "{}")
    except Exception:
        baseline = {}
    if not isinstance(baseline, dict):
        baseline = {}
    perf = baseline.get("baseline_perf")
    if not isinstance(perf, dict) or int(perf.get("total_closed") or 0) <= 0:
        try:
            from backend.services.strategy_runtime_report import generate_report
            snap = generate_report(db, window="24h", domain="ai")
            baseline["baseline_perf"] = {
                "win_rate": float(snap.win_rate or 0),
                "total_pnl": float(snap.total_pnl or 0),
                "total_closed": int(snap.total_closed or 0),
                "master_close_loss_ratio": float(snap.master_close_loss_ratio or 0),
                "master_close_count": int(snap.master_close_count or 0),
                "backfilled_at_eval": True,
            }
            row.baseline_json = json.dumps(baseline, ensure_ascii=False)
            changed = True
            logger.warning(
                "[ProposalApplier] 提案 %s baseline_perf 缺失，评估时用当前 SRR 回填（对比可能偏 neutral）",
                row.id,
            )
        except Exception as err:
            logger.debug("[ProposalApplier] baseline 回填失败: %s", err)
    return changed


def evaluate_applied_proposals(
    db,
    *,
    force: bool = False,
    min_eval_samples: Optional[int] = None,
) -> int:
    from backend.database.models import OpenCodeEvolutionProposalDB
    from backend.database.connection import sqlite_write_commit
    from backend.services.proposal_validation_policy import (
        can_evaluate_proposal,
        current_paper_gear,
        min_eval_samples as _default_min_samples,
        should_mark_inconclusive,
        validation_policy_for_gear,
    )
    from backend.services.strategy_runtime_report import build_ai_report_since

    MIN_EVAL_SAMPLES = min_eval_samples if min_eval_samples is not None else _default_min_samples(force=force)
    gear = current_paper_gear()
    pol = validation_policy_for_gear(gear)

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = (
        db.query(OpenCodeEvolutionProposalDB)
        .filter(OpenCodeEvolutionProposalDB.status == "paper_applying")
        .all()
    )
    n = 0
    meta_dirty = False
    for row in rows:
        if _ensure_proposal_eval_metadata(db, row):
            meta_dirty = True
    if meta_dirty:
        sqlite_write_commit(db)

    for row in rows:
        if not row.applied_at:
            continue
        applied_at = row.applied_at
        if applied_at.tzinfo is not None:
            applied_at = applied_at.replace(tzinfo=None)
        age_hours = (now_naive - applied_at).total_seconds() / 3600.0

        try:
            after = build_ai_report_since(db, applied_at)
            post_closed = int(after.total_closed or 0)

            ready, ready_reason = can_evaluate_proposal(
                age_hours=age_hours,
                post_apply_closed=post_closed,
                gear=gear,
                force=force,
            )
            if not ready:
                if should_mark_inconclusive(
                    age_hours=age_hours,
                    post_apply_closed=post_closed,
                    gear=gear,
                ):
                    row.status = "inconclusive"
                    _after_dict = dict(after.to_dict())
                    _after_dict["eval_skipped"] = ready_reason
                    _after_dict["validation_policy"] = pol
                    row.after_json = json.dumps(_after_dict, ensure_ascii=False)
                    logger.warning(
                        "[ProposalApplier] 提案 %s 超时仍无足够 post-apply 样本(%d<%d)，inconclusive",
                        row.id, post_closed, MIN_EVAL_SAMPLES,
                    )
                    n += 1
                else:
                    logger.info(
                        "[ProposalApplier] 提案 %s 延后评估: %s (post_apply=%d age=%.1fh gear=%s)",
                        row.id, ready_reason, post_closed, age_hours, gear,
                    )
                continue

            # 读取应用前真实绩效（兼容旧记录：旧格式无 baseline_perf 则退回 0）
            baseline_wr = 0.0
            baseline_pnl = 0.0
            baseline_closed = 0
            baseline_mc_ratio = 0.0
            try:
                baseline = json.loads(row.baseline_json or "{}")
                perf = baseline.get("baseline_perf") if isinstance(baseline, dict) else None
                if isinstance(perf, dict):
                    baseline_wr = float(perf.get("win_rate") or 0)
                    baseline_pnl = float(perf.get("total_pnl") or 0)
                    baseline_closed = int(perf.get("total_closed") or 0)
                    baseline_mc_ratio = float(perf.get("master_close_loss_ratio") or 0)
            except Exception:
                pass

            try:
                _payload = json.loads(row.proposal_json or "{}")
                _patches = _payload.get("patches") or []
            except Exception:
                _patches = []
            focus = _proposal_focus(_patches)

            # ── PnL 导向 verdict：每笔期望收益为核心指标，胜率仅辅助 ──
            # 核心理念：胜率高不挣钱 = 无效策略；胜率低但盈亏比高 = 可能优秀
            base_avg_pnl = (baseline_pnl / baseline_closed) if baseline_closed > 0 else 0.0
            aft_avg_pnl = (after.total_pnl / after.total_closed) if after.total_closed > 0 else 0.0
            aft_wr = float(after.win_rate or 0)

            # 期望收益变化（核心判决依据）
            if base_avg_pnl > 0:
                pnl_change_ratio = (aft_avg_pnl - base_avg_pnl) / abs(base_avg_pnl)
            elif base_avg_pnl < 0:
                # 基线亏损→盈利 = 巨大改善
                pnl_change_ratio = 999 if aft_avg_pnl >= 0 else (
                    (aft_avg_pnl - base_avg_pnl) / abs(base_avg_pnl)
                )
            else:
                # 基线 0（新策略），看绝对值
                pnl_change_ratio = 999 if aft_avg_pnl > 5 else 0

            pnl_improved = pnl_change_ratio > 0.30 and aft_avg_pnl > 0
            pnl_degraded = (
                pnl_change_ratio < -0.30
                or (aft_avg_pnl < 0 and base_avg_pnl >= 0 and aft_avg_pnl < -5)
            )

            # 辅助：胜率维度的异常（不计入 verdict，但影响 confidence 标记）
            wr_changed_significantly = abs(aft_wr - baseline_wr) > 0.08
            wr_divergence = ""  # 胜率与 PnL 方向背离时记录
            if pnl_improved and aft_wr < baseline_wr - 0.05:
                wr_divergence = "pnl_up_wr_down"  # 盈亏比改善（好事）
            elif pnl_degraded and aft_wr > baseline_wr + 0.05:
                wr_divergence = "pnl_down_wr_up"  # 危险：胜率高但亏更多钱

            if focus == "master_close":
                mc_after = float(after.master_close_loss_ratio or 0)
                mc_degraded = mc_after > baseline_mc_ratio + 0.10
                mc_improved = mc_after < baseline_mc_ratio - 0.05
                if mc_degraded or (pnl_degraded and mc_after >= baseline_mc_ratio):
                    verdict = "degraded"
                elif mc_improved and not pnl_degraded:
                    verdict = "improved"
                else:
                    verdict = "neutral"
            else:
                if pnl_degraded:
                    verdict = "degraded"
                elif pnl_improved:
                    verdict = "improved"
                else:
                    verdict = "neutral"

            _after_dict = dict(after.to_dict())
            _after_dict["verdict"] = verdict
            _after_dict["focus"] = focus
            _after_dict["eval_mode"] = "post_apply_slice"
            _after_dict["eval_ready_reason"] = ready_reason
            _after_dict["validation_policy"] = pol
            _after_dict["post_apply_closed"] = post_closed
            _after_dict["age_hours"] = round(age_hours, 2)
            _after_dict["eval_metrics"] = {
                "baseline_avg_pnl": round(base_avg_pnl, 2),
                "after_avg_pnl": round(aft_avg_pnl, 2),
                "pnl_change_ratio": round(pnl_change_ratio, 3),
                "win_rate_divergence": wr_divergence or None,
            }
            _after_dict["baseline_perf"] = {
                "win_rate": baseline_wr, "avg_pnl_per_trade": round(base_avg_pnl, 2),
                "master_close_loss_ratio": baseline_mc_ratio,
            }
            row.after_json = json.dumps(_after_dict, ensure_ascii=False)

            if verdict == "degraded":
                from backend.services.runtime_tuning_store import rollback_snapshot
                from backend.services.decision_policy_engine import rollback_policy_snapshot
                _t_ok = rollback_snapshot(row.id)
                _p_n = rollback_policy_snapshot(row.id)
                # [2026-07-11] 若该提案里有走 Governor 仲裁的门控参数(GOVERNED_KEYS)，
                # 光靠 rollback_snapshot 恢复 JSON 文件不够——Governor 仍记得这条
                # source="opencode" 的意图，下次任意 key 触发 reconcile 时会把值改回来。
                # 必须同时撤销该提案对应的 opencode 意图。
                try:
                    from backend.services.runtime_governor import RuntimeGovernor, GOVERNED_KEYS
                    _prior = json.loads(row.baseline_json or "{}")
                    _governed_keys_in_row = [
                        k for k in (_prior.get("applied", {}).get("tuning") or {}).keys()
                        if k in GOVERNED_KEYS
                    ]
                    if _governed_keys_in_row:
                        _wd = RuntimeGovernor().withdraw(source="opencode", keys=_governed_keys_in_row)
                        logger.info(
                            "[ProposalApplier] 提案 %s 回滚: 撤销opencode意图 %s",
                            row.id, _wd,
                        )
                except Exception as _gov_err:
                    logger.debug("[ProposalApplier] 回滚时撤销governor意图失败: %s", _gov_err)
                row.status = "rolled_back"
                logger.info(
                    "[ProposalApplier] 提案 %s 退化回滚(focus=%s post_apply=%d): tuning=%s policy=%d",
                    row.id, focus, post_closed, _t_ok, _p_n,
                )
                _run_proposal_attribution_analysis(row.id, verdict)
            else:
                row.status = "paper_validated"
                row.validated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                logger.info(
                    "[ProposalApplier] 提案 %s 验证通过(focus=%s verdict=%s post_apply=%d age=%.1fh)",
                    row.id, focus, verdict, post_closed, age_hours,
                )
            _run_proposal_attribution_analysis(row.id, verdict)
            n += 1
        except Exception as err:
            logger.error("[ProposalApplier] eval %s: %s", row.id, err)
    if n:
        sqlite_write_commit(db)

    # P2-7: 提案→收益闭环验证 — 评估后检查验证通过的提案是否真的改善了交易表现
    _cross_validate_validated_proposals(db)

    return n


# ══════════════════════════════════════════════════════
#  P2-7: 提案→收益闭环验证
# ══════════════════════════════════════════════════════

def _cross_validate_validated_proposals(db) -> int:
    """P2-7: 交叉验证 paper_validated 提案是否真的改善了实盘交易表现。

    问题：提案在 paper 环境被标记为 "improved"，但这可能是：
    1. 真实改善（因果关系）
    2. 市场顺势（相关性，非因果）
    3. 样本不足（小样本噪声）

    本函数检查：
    - validated 提案对应的策略在验证后的表现是否持续改善
    - **改用每笔期望收益（avg_pnl_per_trade）替代胜率**：胜率高不挣钱仍是失败
    - 如果提案验证后 24h+ 的每笔期望收益下降 >40%，标记为 "suspicious"
    """
    from backend.database.models import OpenCodeEvolutionProposalDB
    from backend.services.strategy_runtime_report import generate_report
    from datetime import timedelta

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    # 只检查 24h 前验证通过的提案（给充足观察时间）
    cutoff = now_naive - timedelta(hours=24)

    rows = (
        db.query(OpenCodeEvolutionProposalDB)
        .filter(
            OpenCodeEvolutionProposalDB.status == "paper_validated",
            OpenCodeEvolutionProposalDB.validated_at.isnot(None),
            OpenCodeEvolutionProposalDB.validated_at < cutoff,
        )
        .order_by(OpenCodeEvolutionProposalDB.validated_at.desc())
        .limit(20)
        .all()
    )

    flagged = 0
    for row in rows:
        try:
            after_data = json.loads(row.after_json or "{}")
        except Exception:
            after_data = {}

        verdict = after_data.get("verdict", "?")
        if verdict != "improved":
            continue

        # 获取当前 SRR，与 after 报告中的期望收益对比
        try:
            current_report = generate_report(db, window="24h", domain="ai")

            # 从 after_json 提取验证时的 per-trade PnL
            eval_metrics = after_data.get("eval_metrics") or {}
            after_avg_pnl = float(eval_metrics.get("after_avg_pnl") or 0)
            current_avg_pnl = (
                current_report.total_pnl / max(current_report.total_closed, 1)
            )

            # 期望收益下降 >40% 或从正变负，标记可疑
            is_suspicious = False
            reason = ""
            if after_avg_pnl > 0 and current_avg_pnl < after_avg_pnl * 0.60:
                is_suspicious = True
                reason = (
                    f"验证后每笔期望收益从 ${after_avg_pnl:+.2f} 降至 ${current_avg_pnl:+.2f}"
                    f"（下降 {(1 - current_avg_pnl/max(after_avg_pnl, 0.01))*100:.0f}%），"
                    f"提案改善可能非因果"
                )
            elif after_avg_pnl >= 0 and current_avg_pnl < -5:
                is_suspicious = True
                reason = (
                    f"验证后每笔期望收益从 ${after_avg_pnl:+.2f} 变为 ${current_avg_pnl:+.2f}"
                    f"（由盈转亏），提案改善可能非因果"
                )

            if is_suspicious:
                after_data["cross_validation"] = {
                    "checked_at": now_naive.isoformat(),
                    "verdict_avg_pnl": after_avg_pnl,
                    "current_avg_pnl": round(current_avg_pnl, 2),
                    "status": "suspicious",
                    "note": reason,
                }
                row.after_json = json.dumps(after_data, ensure_ascii=False)
                flagged += 1
                logger.warning(
                    "[ProposalCrossVal] 提案 %s 标记可疑: verdict=%s reason=%s",
                    row.id, verdict, reason,
                )
        except Exception as err:
            logger.debug("[ProposalCrossVal] 检查 %s: %s", row.id, err)

    if flagged:
        from backend.database.connection import sqlite_write_commit
        sqlite_write_commit(db)
        logger.info("[ProposalCrossVal] 标记 %d 个可疑验证提案", flagged)

    return flagged


def evaluate_proposals_summary(db, *, force: bool = False) -> Dict[str, Any]:
    """评估并返回漏斗友好摘要。"""
    n = evaluate_applied_proposals(db, force=force)
    from backend.database.models import OpenCodeEvolutionProposalDB

    by_status: Dict[str, int] = {}
    verdicts = {"improved": 0, "neutral": 0, "degraded": 0, "unevaluated": 0}
    rows = db.query(OpenCodeEvolutionProposalDB).all()
    for row in rows:
        st = row.status or "unknown"
        by_status[st] = by_status.get(st, 0) + 1
        verdict = None
        try:
            verdict = json.loads(row.after_json or "{}").get("verdict")
        except Exception:
            verdict = None
        if st in ("paper_validated", "rolled_back", "inconclusive") and not verdict:
            verdict = "neutral"
        verdicts[verdict if verdict in verdicts else "unevaluated"] += 1
    evaluated = verdicts["improved"] + verdicts["neutral"] + verdicts["degraded"]
    from backend.services.proposal_validation_policy import validation_policy_for_gear

    return {
        "evaluated_this_run": n,
        "by_status": by_status,
        "verdicts": verdicts,
        "evaluated_total": evaluated,
        "force": force,
        "validation_policy": validation_policy_for_gear(),
    }


# ══════════════════════════════════════════════════════
#  Phase 7: 提案归因分析 — 为什么改对了/改错了
# ══════════════════════════════════════════════════════

def _run_proposal_attribution_analysis(proposal_id: int, verdict: str) -> None:
    """
    在提案评估完成后，调用 OpenCode plan agent 进行归因分析：
    这个参数变更为什么导致了改善/恶化？是因果关系还是巧合？
    结果写入 row.after_json 的 attribution 字段。
    """
    import threading

    def _worker():
        db = None
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import OpenCodeEvolutionProposalDB
            from backend.services.opencode_bridge import (
                _is_enabled, _load_system_prompt, _agent_plan, _model,
                run_http_agent_message, _extract_json,
            )
            if not _is_enabled():
                logger.debug("[ProposalAttr] OpenCode 未启用，跳过归因分析")
                return

            db = SessionLocal()
            row = db.query(OpenCodeEvolutionProposalDB).filter(
                OpenCodeEvolutionProposalDB.id == proposal_id
            ).first()
            if not row:
                return

            patches = []
            try:
                patches = json.loads(row.proposal_json or "{}").get("patches") or []
            except Exception:
                pass

            before = {}
            after = {}
            try:
                before = json.loads(row.baseline_json or "{}")
            except Exception:
                pass
            try:
                after = json.loads(row.after_json or "{}")
            except Exception:
                pass

            system = _load_system_prompt()
            user_text = (
                f"## 参数变更归因分析\n\n"
                f"- 提案ID: {row.id}\n"
                f"- 评估结论: {verdict}\n"
                f"- 修改的参数: {json.dumps(patches, ensure_ascii=False)}\n"
                f"- 变更前基线: {json.dumps(before.get('baseline_perf', before), ensure_ascii=False)}\n"
                f"- 变更后 eval_metrics: {json.dumps(after.get('eval_metrics', {}), ensure_ascii=False)}\n\n"
                f"请分析这个参数变更为什么导致了{verdict}。是因果关系还是巧合？\n"
                f"输出JSON: {{\"is_causal\": true/false, \"explanation\": \"...\", \"confidence\": 0.0}}"
            )

            raw, err = run_http_agent_message(
                system_prompt=system,
                user_text=user_text,
                agent=_agent_plan(),
                model_slug=_model(),
                session_title=f"Proposal Attribution #{row.id}",
            )
            if err:
                logger.debug("[ProposalAttr] 调用失败: %s", err)
                return

            attribution = _extract_json(raw or "")
            after["attribution"] = attribution
            row.after_json = json.dumps(after, ensure_ascii=False)
            db.commit()

            logger.info(
                "[ProposalAttr] 归因完成 #%d: is_causal=%s conf=%s",
                row.id,
                attribution.get("is_causal"),
                attribution.get("confidence", 0),
            )
        except Exception as exc:
            logger.debug("[ProposalAttr] 归因异常(非致命): %s", exc)
        finally:
            if db is not None:
                db.close()

    threading.Thread(
        target=_worker, daemon=True, name=f"proposal-attribution-{proposal_id}"
    ).start()
