"""Hermes Layer 3: ϵͳ�ܹ���������

�ӿ��᰸ģʽ�з���ϵͳ�ܹ�ȱ�ڣ�������ģ��/������/�ع����顣
ͨ�� LLM ���������䶯����ͼ + �᰸�ǻ�ժҪ������ϵͳ������������
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.services.hermes_db import (
    hermes_execute,
    hermes_fetchall,
    hermes_fetchone,
    get_main_session,
)
from backend.services.hermes_proposal_wisdom_engine import proposal_wisdom

logger = logging.getLogger(__name__)


class ArchitectureEvolutionEngine:
    """Layer 3: ϵͳ�ܹ��������档

    ���֣�
    - ��Щ���������������������ʵ� �� ��Ҫ��ģ��
    - ��Щ������ϴ��ڽ���ЧӦ �� ��Ҫ�������
    - ��Щ�����޶�Ӧ�����ɵ� �� ��Ҫ��������
    """

    def analyze_parameter_churn(self) -> Dict[str, Any]:
        """���������䶯��Ƶ�κ�Ч����"""
        records = hermes_fetchall(
            """SELECT param_key, COUNT(*) as total,
                      SUM(CASE WHEN outcome='improved' THEN 1 ELSE 0 END) as improved,
                      SUM(CASE WHEN outcome='degraded' THEN 1 ELSE 0 END) as degraded,
                      AVG(pnl_impact) as avg_pnl,
                      AVG(ABS(pnl_impact)) as avg_abs_pnl
               FROM proposal_wisdom_records
               WHERE param_key IS NOT NULL
               GROUP BY param_key
               ORDER BY total DESC"""
        )

        heatmap = []
        for r in records:
            total = int(r["total"])
            imp = int(r["improved"])
            imp_rate = imp / max(total, 1)
            heatmap.append({
                "param_key": r["param_key"],
                "total_adjustments": total,
                "improved_rate": round(imp_rate, 3),
                "avg_pnl_impact": round(float(r["avg_pnl"] or 0), 2),
                "churn_score": round(total * (1 - imp_rate), 1),  # �߱䶯�͸��� = �� churn
                "needs_rethink": imp_rate < 0.35 and total >= 5,
            })

        return {"heatmap": heatmap, "total_params": len(heatmap)}

    def discover_architecture_gaps(self) -> List[Dict[str, Any]]:
        """����ϵͳ�ܹ�ȱ�ڣ����� LLM ������"""
        churn = self.analyze_parameter_churn()
        wisdom_ctx = proposal_wisdom.build_wisdom_context(limit=20)
        top_patterns = proposal_wisdom.get_top_patterns(min_samples=2)

        # ������������
        churn_summary = "\n".join(
            f"- `{h['param_key']}`: ���� {h['total_adjustments']} �Σ������� {h['improved_rate']:.0%}"
            + (" ?? �߱䶯�͸���" if h.get("needs_rethink") else "")
            for h in churn["heatmap"][:15]
        )

        pattern_summary = "\n".join(
            f"- [{p['outcome']}] {p.get('direction','')} `{p['param_key']}` "
            f"({p.get('market_condition','')}): ${p.get('avg_pnl_impact',0):+.2f}/��, "
            f"����={p.get('sample_count',0)}, ���Ŷ�={p.get('confidence_avg',0):.0%}"
            for p in top_patterns[:10]
        )

        system_prompt = self._load_l3_system_prompt()
        user_text = (
            f"## ϵͳ�ܹ���������\n\n"
            f"### �����䶯����ͼ\n{churn_summary}\n\n"
            f"### �����Ŷ�ģʽ\n{pattern_summary}\n\n"
            f"### ��ʷ�᰸�ǻ�\n{wisdom_ctx[:3000]}\n\n"
            f"### ��ǰ����������\n"
            f"master_reduce_min_loss_pct, tier_max_hold_sec, "
            f"master_close_min_loss_pct_by_tier, max_daily_trades, "
            f"maturity_max_warmup_relief, maturity_global_n1, maturity_global_n2\n\n"
            f"�뷢��ϵͳ�ܹ�ȱ�ڲ������������顣"
        )

        result = self._call_llm(system_prompt, user_text)
        proposals = result.get("proposals") or []
        priority = result.get("priority_ranking") or []
        llm_error = result.get("error")

        # �־û���7 ����ͬ����ȥ�أ�����ÿ�ζ�ʱ�����ظ����룩
        inserted = 0
        for p in proposals:
            title = p.get("title", "δ����")
            dup = hermes_fetchone(
                """SELECT id FROM architecture_evolution_proposals
                   WHERE title=? AND created_at >= datetime('now', '-7 days') LIMIT 1""",
                (title,),
            )
            if dup:
                continue
            hermes_execute(
                """INSERT INTO architecture_evolution_proposals
                   (title, category, description, evidence_patterns, related_proposal_ids,
                    feasibility, expected_impact, implementation_notes, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))""",
                (
                    p.get("title", "δ����"),
                    p.get("category", "new_module"),
                    p.get("rationale", ""),
                    json.dumps(p.get("evidence", []), ensure_ascii=False),
                    json.dumps(p.get("related_proposal_ids", []), ensure_ascii=False),
                    p.get("feasibility", "medium"),
                    p.get("expected_impact", "medium"),
                    p.get("implementation_hint", ""),
                    "pending",
                ),
            )
            inserted += 1

        logger.info(
            "[Hermes:L3] �ܹ���������: %d ���������飨��д�� %d��",
            len(proposals),
            inserted,
        )
        return {
            "proposals": proposals,
            "priority": priority,
            "inserted": inserted,
            "llm_error": llm_error,
            "parsed_ok": bool(proposals) or not llm_error,
        }

    def get_pending_proposals(self) -> List[Dict[str, Any]]:
        """��ȡ������ļܹ��᰸��"""
        return hermes_fetchall(
            "SELECT * FROM architecture_evolution_proposals WHERE status='pending' ORDER BY id DESC"
        )

    def get_stats(self) -> Dict[str, int]:
        """��ȡ�ܹ�����ͳ�ơ�"""
        total = hermes_fetchall("SELECT status, COUNT(*) as cnt FROM architecture_evolution_proposals GROUP BY status")
        stats = {"total": 0, "pending": 0, "accepted": 0, "rejected": 0, "implemented": 0}
        for r in total:
            stats[r["status"]] = r["cnt"]
            stats["total"] += r["cnt"]
        return stats

    def accept_proposal(self, proposal_id: int) -> Dict[str, Any]:
        """���� L3 �᰸ �� RuntimeGovernor patch ��������"""
        from backend.services.hermes_db import hermes_fetchone, hermes_execute
        row = hermes_fetchone(
            "SELECT * FROM architecture_evolution_proposals WHERE id=?",
            (proposal_id,),
        )
        if not row:
            return {"ok": False, "error": "�᰸������"}
        if row.get("status") not in ("pending", "accepted"):
            return {"ok": False, "error": f"״̬���ɽ���: {row.get('status')}"}

        patch_keys: Dict[str, Any] = {
            "_patch_type": "runtime_tuning",
            "_source": "hermes_l3",
            "_proposal_id": proposal_id,
        }
        executable_keys: Dict[str, Any] = {}
        notes = (row.get("implementation_notes") or "").strip()
        if notes.startswith("{"):
            try:
                parsed = json.loads(notes)
                if isinstance(parsed, dict):
                    executable_keys = {
                        k: v for k, v in parsed.items() if not str(k).startswith("_")
                    }
                    patch_keys.update(executable_keys)
            except Exception:
                pass
        if not executable_keys:
            patch_keys["hermes_l3_note"] = (row.get("description") or row.get("title") or "")[:500]

        from backend.services.runtime_governor import runtime_governor
        patch = runtime_governor.propose_patch(
            patch_keys,
            reason=f"[Hermes L3] {row.get('title') or proposal_id}",
        )
        if patch.status == "approved":
            self.mark_implemented(
                proposal_id,
                governor_patch_id=patch.patch_id,
                executable=bool(executable_keys),
            )
            final_status = "implemented"
        else:
            hermes_execute(
                "UPDATE architecture_evolution_proposals SET status='accepted', reviewed_at=datetime('now') WHERE id=?",
                (proposal_id,),
            )
            final_status = "accepted"
        return {
            "ok": True,
            "proposal_id": proposal_id,
            "governor_patch_id": patch.patch_id,
            "status": final_status,
            "executable": bool(executable_keys),
        }

    def mark_implemented(
        self,
        proposal_id: int,
        *,
        governor_patch_id: Optional[str] = None,
        executable: bool = False,
    ) -> Dict[str, Any]:
        """Governor patch ����� �� ��� L3 �᰸Ϊ implemented��"""
        row = hermes_fetchone(
            "SELECT id, status FROM architecture_evolution_proposals WHERE id=?",
            (proposal_id,),
        )
        if not row:
            return {"ok": False, "error": "�᰸������"}
        if row.get("status") == "implemented":
            return {"ok": True, "proposal_id": proposal_id, "already": True}
        hermes_execute(
            """UPDATE architecture_evolution_proposals
               SET status='implemented', reviewed_at=datetime('now') WHERE id=?""",
            (proposal_id,),
        )
        logger.info(
            "[Hermes:L3] implemented id=%s patch=%s executable=%s",
            proposal_id, governor_patch_id, executable,
        )
        return {
            "ok": True,
            "proposal_id": proposal_id,
            "governor_patch_id": governor_patch_id,
            "executable": executable,
        }

    def reconcile_implemented_paper(self, *, limit: Optional[int] = 100) -> Dict[str, Any]:
        """Paper������ accept �� Governor ����׼�� L3 �᰸������� implemented��"""
        if not self._paper_auto_accept_enabled():
            return {"skipped": "not paper", "implemented": 0}

        if limit is None:
            limit = 100
        rows = hermes_fetchall(
            """SELECT id FROM architecture_evolution_proposals
               WHERE status='accepted' ORDER BY id ASC LIMIT ?""",
            (int(limit),),
        )
        done: List[int] = []
        for row in rows:
            pid = int(row["id"])
            res = self.mark_implemented(pid, executable=False)
            if res.get("ok"):
                done.append(pid)
        stats = self.get_stats()
        logger.info(
            "[Hermes:L3] reconcile_implemented: %d �� implemented, stats=%s",
            len(done), stats,
        )
        return {
            "implemented": len(done),
            "ids": done[:20],
            "stats": stats,
        }

    @staticmethod
    def _paper_auto_accept_enabled() -> bool:
        try:
            from backend.config.settings import HERMES_L3_AUTO_ACCEPT_PAPER
            if not HERMES_L3_AUTO_ACCEPT_PAPER:
                return False
            from backend.services.lock_strength_service import get_lock_strength_service
            return bool(get_lock_strength_service().get_profile("paper").disable_loss_locks)
        except Exception:
            return False

    def auto_accept_pending_paper(self, *, limit: Optional[int] = None) -> Dict[str, Any]:
        """Paper ģʽ���� accept pending L3 �᰸ �� Governor��Paper �Զ� approve����"""
        if not self._paper_auto_accept_enabled():
            return {"skipped": "HERMES_L3_AUTO_ACCEPT_PAPER=false or not paper", "accepted": 0}

        if limit is None:
            try:
                from backend.config.settings import HERMES_L3_AUTO_ACCEPT_BATCH
                limit = int(HERMES_L3_AUTO_ACCEPT_BATCH or 20)
            except Exception:
                limit = 20

        rows = hermes_fetchall(
            """SELECT id FROM architecture_evolution_proposals
               WHERE status='pending' ORDER BY id ASC LIMIT ?""",
            (int(limit),),
        )
        accepted: List[int] = []
        errors: List[Dict[str, Any]] = []
        for row in rows:
            pid = int(row["id"])
            try:
                res = self.accept_proposal(pid)
                if res.get("ok"):
                    accepted.append(pid)
                else:
                    errors.append({"proposal_id": pid, "error": res.get("error")})
            except Exception as err:
                errors.append({"proposal_id": pid, "error": str(err)})

        stats = self.get_stats()
        logger.info(
            "[Hermes:L3] auto_accept_pending: accepted=%d remaining_pending=%d",
            len(accepted), stats.get("pending", 0),
        )
        return {
            "accepted": len(accepted),
            "ids": accepted,
            "errors": errors[:5],
            "remaining_pending": stats.get("pending", 0),
            "stats": stats,
        }

    def reject_proposal(self, proposal_id: int, reason: str = "") -> Dict[str, Any]:
        from backend.services.hermes_db import hermes_execute
        hermes_execute(
            "UPDATE architecture_evolution_proposals SET status='rejected', reviewed_at=datetime('now') WHERE id=?",
            (proposal_id,),
        )
        return {"ok": True, "proposal_id": proposal_id, "reason": reason}

    # �������� ˽�и��� ��������

    def _load_l3_system_prompt(self) -> str:
        from backend.services.hermes_db import resolve_hermes_prompt_path

        prompt_path = resolve_hermes_prompt_path("tasks", "task_architecture_evolution.md")
        try:
            if os.path.isfile(prompt_path):
                with open(prompt_path, encoding="utf-8") as f:
                    return f.read()
        except Exception:
            pass
        return (
            "You are Hermes, a system architecture evolution specialist. "
            "Analyze cross-proposal patterns to identify missing system capabilities "
            "and suggest architectural upgrades beyond parameter tuning. "
            "Output JSON with: proposals (array of {category, title, rationale, evidence, "
            "expected_impact, feasibility, implementation_hint}) and priority_ranking.\n"
            "implementation_hint MUST be a JSON object string when tunable, e.g. "
            '{"max_daily_trades": 12, "maturity_global_n1": 0.55} '
            "using keys from: max_daily_trades, min_risk_reward, scalp_min_confidence, "
            "maturity_max_warmup_relief, maturity_global_n1, maturity_global_n2, disabled_natures."
        )

    @staticmethod
    def _json_output_protocol(expected_fields: str) -> str:
        """ǿ�� LLM ֱ�� JSON�����ù���/����̽�������Э�顣

        ʵ�ⷢ�֣�deepseek-v4-flash �� `plan` agent �£���Ժ�����/���������ĵ�
        ����ʽ���ʻ���롸̽������⡹�Ĺ���ѭ����"Let me explore the codebase..."����
        �ľ���������Ҳ������ JSON������ L3/L4 ��Զ 0 �᰸����Э��ǰ��Լ����
        ȷ��ģ��ֱ�Ӳ����ɽ��� JSON��
        """
        return (
            "CRITICAL OUTPUT PROTOCOL (override any tool-use impulse):\n"
            "- Do NOT explore the codebase, do NOT call any tools, do NOT read files.\n"
            "- Do NOT narrate, explain, or output prose. Do NOT output markdown.\n"
            "- Your ENTIRE response must be a single valid JSON object, nothing else.\n"
            "- Base the answer solely on the data provided in the user message.\n"
            f"- Output schema: {expected_fields}\n"
            "- If insufficient data, still return the JSON object with an empty array "
            "and a short \"note\" field. Begin your response with '{'.\n\n"
        )

    def _call_llm(self, system_prompt: str, user_text: str) -> Dict[str, Any]:
        try:
            from backend.services.opencode_bridge import (
                collect_http_agent_stream_text,
                _agent_plan,
                _model,
                _extract_json,
            )
            # ǰ�� JSON ���Э�飬ѹ�� plan agent �Ĵ���̽���嶯
            protocol = self._json_output_protocol(
                '{"proposals": [{category, title, rationale, evidence, '
                'expected_impact, feasibility, implementation_hint}], '
                '"priority_ranking": [{title, score}]}'
            )
            raw, err = collect_http_agent_stream_text(
                system_prompt=protocol + system_prompt,
                user_text=user_text,
                agent=_agent_plan(),
                model_slug=_model(),
                session_title="Hermes L3: Architecture Evolution",
                log_prefix="Hermes:L3",
                idle_timeout_s=900.0,
                max_duration_s=7200.0,
            )
            if err:
                return {"proposals": [], "error": err}
            return _extract_json(raw or "")
        except Exception as e:
            return {"proposals": [], "error": str(e)}


# ȫ�ֵ���
architecture_evolution = ArchitectureEvolutionEngine()
