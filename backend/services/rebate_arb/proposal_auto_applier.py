"""
套利进化提案 Paper 自动应用 + 7 天对照（M9）

此前 rebate_strategy_evolver 生成的提案只是进入人工确认队列，
max_position_multiplier 没有任何消费方 — 「只记不学」。

本模块闭环：
  1. auto_apply_pending_paper_proposals()：pending 的 evolution 提案在
     Paper 模式自动应用（写 data/rebate_paper_multipliers.json，
     引擎开仓时按 strategy_type 缩放保证金），状态 → paper_applying，
     并记录应用时点的基线绩效（前 7 天胜率/净值）。
  2. evaluate_applied_proposals()：应用满 7 天后对照前后绩效：
     - 改善或持平 → status=paper_validated（live 仍需人工确认）
     - 恶化 → status=dismissed，回滚 multiplier
  3. get_paper_multiplier(strategy_type)：引擎消费入口。

Live 模式永不自动应用（requires_manual_live_confirm 保持 True）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MULTIPLIERS_FILE = os.path.join("data", "rebate_paper_multipliers.json")
_mult_cache: dict = {"ts": 0.0, "data": {}}

OBSERVATION_DAYS = 7
MULTIPLIER_BOUNDS = (0.5, 1.5)

# [2026-07-06 病灶E 修复] 最小样本门槛：baseline 成交数 < N_MIN 时不应用任何参数变更。
# 此前引擎 import 崩溃 + S8 刷已结束活动 → 从没成交过 → 拿 n=0 的空 baseline 反复给
# 死策略调 multiplier，是自我参照的垃圾进/垃圾出反馈环。空样本一律不调参、留待样本累积。
MIN_SAMPLE_N = int(os.getenv("REBATE_PROPOSAL_MIN_SAMPLE_N", "10"))
# 观察期最长等待上限：超过后仍无足够样本，判定该策略"无活性"，回滚并作废（避免永久挂起）。
MAX_OBSERVATION_DAYS = OBSERVATION_DAYS * 4


def _load_file() -> Dict[str, Any]:
    try:
        if os.path.exists(MULTIPLIERS_FILE):
            with open(MULTIPLIERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception as err:
        logger.warning(f"[ArbProposal] multipliers 读取失败: {err}")
    return {}


def _save_file(data: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(MULTIPLIERS_FILE), exist_ok=True)
        with open(MULTIPLIERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _mult_cache["ts"] = 0.0
    except Exception as err:
        logger.warning(f"[ArbProposal] multipliers 写入失败: {err}")


def get_paper_multiplier(strategy_type: str) -> float:
    """引擎消费入口：Paper 模式下该策略的保证金缩放系数（默认 1.0）。"""
    now = time.time()
    if now - _mult_cache["ts"] >= 60:
        _mult_cache["data"] = _load_file()
        _mult_cache["ts"] = now
    entry = (_mult_cache["data"] or {}).get(str(strategy_type or "").upper())
    if not isinstance(entry, dict):
        return 1.0
    try:
        m = float(entry.get("multiplier") or 1.0)
        return max(MULTIPLIER_BOUNDS[0], min(MULTIPLIER_BOUNDS[1], m))
    except (TypeError, ValueError):
        return 1.0


def _strategy_stats(db, strategy_type: str, start: datetime, end: datetime) -> Dict[str, float]:
    """统计某策略在 [start, end) 区间的 Paper 轮次绩效。"""
    from backend.database.models import RebateTradeOutcomeDB

    rows = (
        db.query(RebateTradeOutcomeDB)
        .filter(
            RebateTradeOutcomeDB.strategy_type == strategy_type,
            RebateTradeOutcomeDB.mode == "paper",
            RebateTradeOutcomeDB.created_at >= start.replace(tzinfo=None),
            RebateTradeOutcomeDB.created_at < end.replace(tzinfo=None),
        )
        .all()
    )
    n = len(rows)
    wins = sum(1 for r in rows if float(r.net_value or 0) > 0)
    return {
        "n": n,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "net_value": round(sum(float(r.net_value or 0) for r in rows), 4),
        "points": round(sum(float(r.points or 0) for r in rows), 2),
    }


def auto_apply_pending_paper_proposals() -> int:
    """把 pending 的 evolution 提案在 Paper 模式自动应用。返回应用数量。"""
    from backend.database.connection import SessionLocal, sqlite_write_commit
    from backend.database.models import RebateEvolutionProposalDB

    applied = 0
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        rows = (
            db.query(RebateEvolutionProposalDB)
            .filter(
                RebateEvolutionProposalDB.source == "evolution",
                RebateEvolutionProposalDB.status == "pending",
            )
            .order_by(RebateEvolutionProposalDB.id.asc())
            .all()
        )
        if not rows:
            return 0

        multipliers = _load_file()
        # 同策略只保留最新提案，旧的直接作废
        latest_by_sid: Dict[str, Any] = {}
        for row in rows:
            sid = (row.strategy_type or "").upper()
            if not sid:
                continue
            if sid in latest_by_sid:
                latest_by_sid[sid].status = "dismissed"
            latest_by_sid[sid] = row

        for sid, row in latest_by_sid.items():
            try:
                proposal = json.loads(row.proposal_json or "{}")
            except Exception:
                proposal = {}
            params = proposal.get("params") or {}
            mult = params.get("max_position_multiplier")
            if mult is None:
                continue
            mult = max(MULTIPLIER_BOUNDS[0], min(MULTIPLIER_BOUNDS[1], float(mult)))

            baseline = _strategy_stats(
                db, sid, now - timedelta(days=OBSERVATION_DAYS), now
            )
            # [病灶E] 空/极少样本时不应用（否则是拿 n=0 空 baseline 给死策略乱调参）。
            # 保持 pending，待有足够真实成交后再评估应用。
            if int(baseline.get("n", 0)) < MIN_SAMPLE_N:
                logger.info(
                    "[ArbProposal] 跳过提案 #%s(%s)：样本不足 n=%s < %s，空样本不调参"
                    "（保持 pending，待成交累积）",
                    row.id, sid, baseline.get("n", 0), MIN_SAMPLE_N,
                )
                continue
            proposal["paper_auto_apply"] = {
                "applied_at": now.isoformat(),
                "multiplier": mult,
                "baseline": baseline,
                "observe_until": (now + timedelta(days=OBSERVATION_DAYS)).isoformat(),
            }
            row.proposal_json = json.dumps(proposal, ensure_ascii=False, default=str)
            row.status = "paper_applying"
            multipliers[sid] = {
                "multiplier": mult,
                "proposal_id": row.id,
                "applied_at": now.isoformat(),
            }
            applied += 1
            logger.info(
                f"[ArbProposal] Paper 自动应用提案 #{row.id}: {sid} "
                f"multiplier={mult} baseline={baseline}"
            )

        if applied:
            sqlite_write_commit(db, label="rebate_proposal_auto_apply")
            _save_file(multipliers)
        return applied
    except Exception as exc:
        logger.warning(f"[ArbProposal] 自动应用失败: {exc}")
        try:
            db.rollback()
        except Exception:
            pass
        return applied
    finally:
        db.close()


def evaluate_applied_proposals() -> int:
    """对照应用满 7 天的提案：改善留用 / 恶化回滚。返回处理数量。"""
    from backend.database.connection import SessionLocal, sqlite_write_commit
    from backend.database.models import RebateEvolutionProposalDB

    processed = 0
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        rows = (
            db.query(RebateEvolutionProposalDB)
            .filter(RebateEvolutionProposalDB.status == "paper_applying")
            .all()
        )
        if not rows:
            return 0

        multipliers = _load_file()
        for row in rows:
            try:
                proposal = json.loads(row.proposal_json or "{}")
            except Exception:
                continue
            meta = proposal.get("paper_auto_apply") or {}
            applied_at_raw = meta.get("applied_at")
            if not applied_at_raw:
                continue
            try:
                applied_at = datetime.fromisoformat(str(applied_at_raw))
                if applied_at.tzinfo is None:
                    applied_at = applied_at.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if now - applied_at < timedelta(days=OBSERVATION_DAYS):
                continue  # 观察期未满

            sid = (row.strategy_type or "").upper()
            after = _strategy_stats(db, sid, applied_at, now)
            baseline = meta.get("baseline") or {}

            # [病灶E] after 样本不足时不要在空数据上下"改善/恶化"结论：
            #   - 未超最长观察上限 → 继续等待（保持 paper_applying）；
            #   - 超上限仍无足够样本 → 判为"无活性"，回滚并作废（避免永久挂起）。
            if int(after.get("n", 0)) < MIN_SAMPLE_N:
                if now - applied_at < timedelta(days=MAX_OBSERVATION_DAYS):
                    logger.info(
                        "[ArbProposal] 提案 #%s(%s) 观察期已满但样本不足 n=%s<%s，延长观察",
                        row.id, sid, after.get("n", 0), MIN_SAMPLE_N,
                    )
                    continue
                meta["after"] = after
                meta["evaluated_at"] = now.isoformat()
                meta["verdict"] = "rolled_back_no_activity"
                proposal["paper_auto_apply"] = meta
                row.proposal_json = json.dumps(proposal, ensure_ascii=False, default=str)
                row.status = "dismissed"
                multipliers.pop(sid, None)
                logger.info(
                    "[ArbProposal] 提案 #%s(%s) 超最长观察期仍无足够成交(n=%s)，"
                    "判为无活性并回滚作废", row.id, sid, after.get("n", 0),
                )
                processed += 1
                continue

            # 对照判定：净值不低于基线且胜率没有明显恶化(>5pp) → 通过
            improved = (
                after.get("net_value", 0) >= float(baseline.get("net_value") or 0)
                and after.get("win_rate", 0) >= float(baseline.get("win_rate") or 0) - 0.05
            )
            meta["after"] = after
            meta["evaluated_at"] = now.isoformat()
            meta["verdict"] = "validated" if improved else "rolled_back"
            proposal["paper_auto_apply"] = meta
            row.proposal_json = json.dumps(proposal, ensure_ascii=False, default=str)

            if improved:
                row.status = "paper_validated"
                logger.info(
                    f"[ArbProposal] 提案 #{row.id} 7天对照通过: {sid} "
                    f"baseline={baseline} after={after}（live 仍需人工确认）"
                )
            else:
                row.status = "dismissed"
                multipliers.pop(sid, None)
                logger.info(
                    f"[ArbProposal] 提案 #{row.id} 7天对照恶化，已回滚: {sid} "
                    f"baseline={baseline} after={after}"
                )
            processed += 1

        if processed:
            sqlite_write_commit(db, label="rebate_proposal_evaluate")
            _save_file(multipliers)
        return processed
    except Exception as exc:
        logger.warning(f"[ArbProposal] 对照评估失败: {exc}")
        try:
            db.rollback()
        except Exception:
            pass
        return processed
    finally:
        db.close()


def run_auto_apply_cycle() -> Dict[str, int]:
    """调度入口：先评估到期提案，再应用新提案。"""
    evaluated = evaluate_applied_proposals()
    applied = auto_apply_pending_paper_proposals()
    return {"evaluated": evaluated, "applied": applied}
