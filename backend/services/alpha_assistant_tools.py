"""Alpha 助手 L2 工具执行（只读 + paper 档操作）。"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_LIVE_CONFIRM_PHRASES = frozenset({"确认晋升", "confirm live", "确认 live", "确认上线"})


def _audit_tool(
    session_id: str,
    user_action: str,
    args: Dict[str, Any],
    ok: bool,
    *,
    level: str = "L2",
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    rollback_hint: Optional[str] = None,
) -> None:
    from backend.services.assistant_audit_service import append_assistant_audit

    append_assistant_audit(
        user_action=user_action,
        args=args,
        ok=ok,
        session_id=session_id,
        level=level,
        result=result,
        error=error,
        rollback_hint=rollback_hint,
    )


def _is_live_confirm(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in _LIVE_CONFIRM_PHRASES or t.replace(" ", "") in {"确认晋升", "确认live"}


def _proposal_rollback_hint(proposal_id: int) -> str:
    return (
        f"回滚指引：OpenCode 智能中心 → 进化提案 → #{proposal_id} → 回滚；"
        "或检查 data/runtime_tuning_snapshots/ 与 decision_policies 备份。"
    )

_GEAR_ALIASES = {
    "turbo": "turbo",
    "极速": "turbo",
    "warm": "warm",
    "热身": "warm",
    "balanced": "balanced",
    "平衡": "balanced",
    "conservative": "conservative",
    "保守": "conservative",
    "慢": "conservative",
    "降档": "conservative",
}


def _parse_gear(text: str) -> Optional[str]:
    lower = text.lower()
    for token, gear in _GEAR_ALIASES.items():
        if token in lower or token in text:
            return gear
    m = re.search(r"\b(turbo|warm|balanced|conservative)\b", lower)
    return m.group(1) if m else None


def _gear_index(gear: str) -> int:
    order = ("turbo", "warm", "balanced", "conservative")
    return order.index(gear) if gear in order else 2


def trigger_opencode_analyze(*, window: str = "24h", domain: str = "ai") -> Dict[str, Any]:
    from backend.database.connection import SessionLocal
    from backend.services.opencode_bridge import run_scheduled_analysis

    db = SessionLocal()
    try:
        return run_scheduled_analysis(db, window=window, domain=domain)
    finally:
        db.close()


def apply_paper_pace(gear: str, *, manual: bool = True) -> Dict[str, Any]:
    from backend.services.paper_pace_controller import paper_pace_controller

    paper_pace_controller.set_gear(gear, manual=manual, reason="alpha_assistant")
    return paper_pace_controller.to_dict()


def list_pending_proposals(limit: int = 5) -> List[Dict[str, Any]]:
    from backend.database.connection import SessionLocal
    from backend.database.models import OpenCodeEvolutionProposalDB

    db = SessionLocal()
    try:
        rows = (
            db.query(OpenCodeEvolutionProposalDB)
            .filter(OpenCodeEvolutionProposalDB.status == "pending")
            .order_by(OpenCodeEvolutionProposalDB.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "title": r.title,
                "severity": r.severity,
                "patch_type": r.patch_type,
            }
            for r in rows
        ]
    finally:
        db.close()


def apply_proposal_paper(proposal_id: int) -> Dict[str, Any]:
    from backend.database.connection import SessionLocal
    from backend.services.opencode_proposal_applier import apply_proposal

    db = SessionLocal()
    try:
        return apply_proposal(db, proposal_id)
    finally:
        db.close()


def apply_proposal_live(proposal_id: int) -> Dict[str, Any]:
    from backend.database.connection import SessionLocal
    from backend.services.opencode_proposal_applier import apply_proposal

    db = SessionLocal()
    try:
        return apply_proposal(
            db,
            proposal_id,
            to_live=True,
            manual_confirmed=True,
        )
    finally:
        db.close()


def get_proposal_summary(proposal_id: int) -> Optional[Dict[str, Any]]:
    from backend.database.connection import SessionLocal
    from backend.database.models import OpenCodeEvolutionProposalDB

    db = SessionLocal()
    try:
        row = db.query(OpenCodeEvolutionProposalDB).filter(
            OpenCodeEvolutionProposalDB.id == proposal_id
        ).first()
        if not row:
            return None
        return {
            "id": row.id,
            "title": row.title,
            "severity": row.severity,
            "status": row.status,
            "requires_manual_live_confirm": bool(row.requires_manual_live_confirm),
        }
    finally:
        db.close()


def evaluate_proposals(force: bool = False) -> Dict[str, Any]:
    from backend.database.connection import SessionLocal
    from backend.services.opencode_proposal_applier import evaluate_proposals_summary

    db = SessionLocal()
    try:
        return evaluate_proposals_summary(db, force=force)
    finally:
        db.close()


def try_l2_action(
    user_message: str,
    *,
    session_id: str,
    pending: Optional[Dict[str, Any]],
    confirm: bool = False,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """返回 (reply_text, new_pending, tool_result)。"""
    text = (user_message or "").strip()
    lower = text.lower()
    live_confirm = _is_live_confirm(text)
    if not confirm:
        confirm = text.strip().lower() in ("确认", "confirm", "是的", "好", "ok", "yes", "y")

    if pending and (confirm or live_confirm):
        action = pending.get("action")
        if action == "apply_proposal_live" and not live_confirm:
            return (
                "⚠️ Live 晋升必须使用 **确认晋升**（普通「确认」不够）。",
                pending,
                None,
            )
        if action != "apply_proposal_live" and live_confirm and not confirm:
            return (
                "当前待确认操作不是 Live 晋升。请回复 **确认**，或重新发起指令。",
                pending,
                None,
            )
        if action == "set_paper_pace":
            gear = pending.get("gear")
            if gear:
                pace = apply_paper_pace(gear, manual=True)
                _audit_tool(
                    session_id,
                    "set_paper_pace",
                    {"gear": gear},
                    True,
                    result={"pace": pace},
                    rollback_hint="Paper Pace 可在 OpenCode 总览快捷控制台或助手再次调整档位。",
                )
                return (
                    f"已确认：Paper Pace 档位设为 **{pace.get('gear')}**。\n\n"
                    f"- tick 间隔：{pace.get('tick_seconds')}s\n"
                    f"- 手动锁定：{'是' if pace.get('manual_lock') else '否'}",
                    None,
                    {"tool": "set_paper_pace", "ok": True, "pace": pace},
                )
        if action == "trigger_analyze":
            result = trigger_opencode_analyze(
                window=pending.get("window", "24h"),
                domain=pending.get("domain", "ai"),
            )
            _audit_tool(
                session_id,
                "trigger_analyze",
                {"window": pending.get("window", "24h"), "domain": pending.get("domain", "ai")},
                not bool(result.get("error")),
                result={"severity": result.get("severity"), "skipped": result.get("skipped")},
            )
            sev = result.get("severity") or result.get("status") or "done"
            return (
                f"分析已触发（{pending.get('window', '24h')} / {pending.get('domain', 'ai')}）。"
                f"\n\n结果摘要：severity={sev}。"
                "\n\n请到 **OpenCode 智能中心 → 智能分析** 查看完整报告。",
                None,
                {"tool": "trigger_analyze", "ok": True, "result": result},
            )
        if action == "apply_proposal":
            pid = int(pending.get("proposal_id") or 0)
            if pid <= 0:
                return "提案 ID 无效。", None, None
            try:
                result = apply_proposal_paper(pid)
            except Exception as exc:
                _audit_tool(session_id, "apply_proposal_paper", {"proposal_id": pid}, False, error=str(exc))
                return f"应用失败：{exc}", None, {"tool": "apply_proposal", "ok": False}
            _audit_tool(
                session_id,
                "apply_proposal_paper",
                {"proposal_id": pid},
                True,
                result={"status": result.get("status")},
                rollback_hint=_proposal_rollback_hint(pid),
            )
            return (
                f"提案 **#{pid}** 已应用到 Paper，状态：{result.get('status')}。\n"
                "24h 后将自动验证成效，可在 **进化提案** Tab 查看。",
                None,
                {"tool": "apply_proposal", "ok": True, "result": result},
            )
        if action == "apply_proposal_live":
            pid = int(pending.get("proposal_id") or 0)
            if pid <= 0:
                return "提案 ID 无效。", None, None
            try:
                result = apply_proposal_live(pid)
            except Exception as exc:
                _audit_tool(
                    session_id,
                    "apply_proposal_live",
                    {"proposal_id": pid},
                    False,
                    level="L3",
                    error=str(exc),
                )
                return f"Live 晋升失败：{exc}", None, {"tool": "apply_proposal_live", "ok": False}
            hint = _proposal_rollback_hint(pid)
            _audit_tool(
                session_id,
                "apply_proposal_live",
                {"proposal_id": pid},
                True,
                level="L3",
                result={"status": result.get("status"), "apply_mode": result.get("apply_mode")},
                rollback_hint=hint,
            )
            return (
                f"⚠️ 提案 **#{pid}** 已 **Live 晋升**（跳过 paper 24h 验证），状态：{result.get('status')}。\n\n"
                f"**回滚**：{hint}",
                None,
                {"tool": "apply_proposal_live", "ok": True, "result": result},
            )
        if action == "evaluate_proposals":
            summary = evaluate_proposals(force=bool(pending.get("force")))
            _audit_tool(
                session_id,
                "evaluate_proposals",
                {"force": bool(pending.get("force"))},
                True,
                result={"evaluated_this_run": summary.get("evaluated_this_run")},
            )
            v = summary.get("verdicts") or {}
            return (
                f"评估完成：本轮处理 {summary.get('evaluated_this_run', 0)} 条。\n"
                f"累计已评估 {summary.get('evaluated_total', 0)} · "
                f"improved={v.get('improved', 0)} neutral={v.get('neutral', 0)} degraded={v.get('degraded', 0)}",
                None,
                {"tool": "evaluate_proposals", "ok": True, "summary": summary},
            )

    if any(k in lower for k in ("待审提案", "pending proposal", "有哪些提案")):
        pending_list = list_pending_proposals(5)
        if not pending_list:
            return "当前没有 pending 状态的进化提案。", None, None
        lines = ["待处理提案（最近 5 条）："]
        for p in pending_list:
            lines.append(f"- #{p['id']} [{p['severity']}] {p['title'][:60]}")
        lines.append("\n说「应用提案 123」可发起 Paper 应用（需确认）。")
        return "\n".join(lines), None, {"tool": "list_pending_proposals", "items": pending_list}

    m_apply = re.search(r"(?:应用|apply)\s*提案\s*#?\s*(\d+)", text, re.I)
    if not m_apply:
        m_apply = re.search(r"提案\s*#?\s*(\d+)\s*(?:应用|apply)", text, re.I)
    if m_apply and not any(k in lower for k in ("live", "晋升", "上线", "真金")):
        pid = int(m_apply.group(1))
        return (
            f"将把提案 **#{pid}** 应用到 Paper 环境（修改 tuning/policy）。\n\n回复 **确认** 执行。",
            {"action": "apply_proposal", "proposal_id": pid},
            None,
        )

    m_live = re.search(
        r"(?:live|晋升|上线|真金)\s*(?:应用|apply)?\s*提案\s*#?\s*(\d+)",
        text,
        re.I,
    )
    if not m_live:
        m_live = re.search(r"提案\s*#?\s*(\d+)\s*(?:live|晋升|上线)", text, re.I)
    if m_live:
        pid = int(m_live.group(1))
        info = get_proposal_summary(pid)
        if not info:
            return f"未找到提案 #{pid}。", None, None
        if info.get("status") != "pending":
            return f"提案 #{pid} 当前状态为 `{info.get('status')}`，无法 Live 晋升。", None, None
        sev = info.get("severity") or "minor"
        title = (info.get("title") or "")[:80]
        return (
            f"⚠️ **Live 晋升** 提案 #{pid} [{sev}]\n"
            f"「{title}」\n\n"
            "将 **直接写入运行时参数**（跳过 Paper 24h 验证）。\n"
            "此操作已记入审计日志。\n\n"
            "回复 **确认晋升** 执行（不可用普通「确认」）。",
            {"action": "apply_proposal_live", "proposal_id": pid},
            None,
        )

    if any(k in lower or k in text for k in ("评估提案", "验证提案", "evaluate proposal")):
        force = "立即" in text or "force" in lower
        if force:
            return (
                "将 **立即** 评估所有 paper_applying 提案（post-apply 样本够即评，忽略浸泡等待）。\n\n回复 **确认** 继续。",
                {"action": "evaluate_proposals", "force": True},
                None,
            )
        summary = evaluate_proposals(force=False)
        _audit_tool(
            session_id,
            "evaluate_proposals",
            {"force": False},
            True,
            result={"evaluated_this_run": summary.get("evaluated_this_run")},
        )
        return (
            f"已触发评估（post-apply 模式：Pace 档位决定最短浸泡时间，凑够 5 笔应用后平仓即可）。"
            f"\n本轮 {summary.get('evaluated_this_run', 0)} 条。"
            f"\n累计 evaluated={summary.get('evaluated_total', 0)}。"
            "\n\n若要立即评估全部，说「立即评估提案」后确认。",
            None,
            {"tool": "evaluate_proposals", "summary": summary},
        )

    if any(k in lower or k in text for k in ("分析", "analyze", "触发分析", "帮我分析")):
        if "确认" not in text and "confirm" not in lower:
            pending_action = {"action": "trigger_analyze", "window": "24h", "domain": "ai"}
            return (
                "将触发 OpenCode **24h / ai** 智能分析（可能需 1–3 分钟）。\n\n"
                "回复 **确认** 继续，或说「分析 arb」指定域。",
                pending_action,
                None,
            )
        result = trigger_opencode_analyze()
        _audit_tool(
            session_id,
            "trigger_analyze",
            {"window": "24h", "domain": "ai"},
            not bool(result.get("error")),
            result={"severity": result.get("severity")},
        )
        sev = result.get("severity") or "done"
        return (
            f"分析已完成，severity={sev}。请打开 OpenCode 智能中心查看。",
            None,
            {"tool": "trigger_analyze", "ok": True, "result": result},
        )

    if any(k in lower or k in text for k in ("pace", "档位", "降档", "turbo", "conservative", "paper pace")):
        gear = _parse_gear(text)
        if not gear:
            return (
                "可选档位：`turbo` · `warm` · `balanced` · `conservative`。\n"
                "例如：「设为 conservative」",
                None,
                None,
            )
        from backend.services.paper_pace_controller import paper_pace_controller

        current = paper_pace_controller.gear
        if _gear_index(gear) > _gear_index(current):
            return (
                f"将把 Pace 从 **{current}** 降到 **{gear}**（减慢 paper 节奏）。\n\n"
                "回复 **确认** 执行。",
                {"action": "set_paper_pace", "gear": gear},
                None,
            )
        pace = apply_paper_pace(gear, manual=True)
        _audit_tool(
            session_id,
            "set_paper_pace",
            {"gear": gear, "auto": True},
            True,
            result={"pace": pace},
        )
        return (
            f"Pace 已设为 **{pace.get('gear')}**（无需确认，未降档）。",
            None,
            {"tool": "set_paper_pace", "ok": True, "pace": pace},
        )

    if any(k in lower for k in ("打开", "跳转", "deep link", "opencode")):
        if "健康" in text or "health" in lower:
            return (
                "请在左侧菜单打开 **OpenCode 智能中心**，切换到 **系统健康** Tab。",
                None,
                {"deep_links": [{"page": "opencode-center", "tab": "health"}]},
            )
        if "治理" in text or "governor" in lower:
            return (
                "请打开 **OpenCode 智能中心 → 治理仲裁** Tab。",
                None,
                {"deep_links": [{"page": "opencode-center", "tab": "governor"}]},
            )
        if "提案" in text:
            return (
                "请打开 **OpenCode 智能中心 → 进化提案** Tab。",
                None,
                {"deep_links": [{"page": "opencode-center", "tab": "proposals"}]},
            )

    return None, pending, None
