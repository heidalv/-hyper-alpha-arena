"""GovernedCodegenService — 受控代码生成服务

见 __init__.py 的安全说明。核心：生成的 .py 只落在隔离沙箱，approve 不自动合并。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import flags
from ..envelope import EvolutionEnvelope, STAGE_LEARN, STATUS_PENDING, STATUS_PASSED, STATUS_REJECTED
from ..ledger import ledger

logger = logging.getLogger(__name__)


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


SHADOW_DIR = os.path.join(_repo_root(), "data", "codegen_shadow")
PROPOSALS_FILE = os.path.join(SHADOW_DIR, "proposals.json")

_lock = threading.Lock()

_FACTOR_SYSTEM_PROMPT = """你是量化交易系统的资深因子/策略工程师。
请根据用户需求生成**单个 Python 文件**，实现一个可插拔的因子或策略类。
要求：
1. 只输出一个 ```python 代码块，不要多余解释。
2. 代码需自包含、可静态审查，禁止任何文件/网络/系统副作用。
3. 因子类应有清晰的 calculate 方法与文档字符串。
4. 变量命名规范，含中文注释说明关键意图。
"""


class GovernedCodegenService:
    """受控 codegen（单例 governed_codegen）。"""

    # ── 开发期助手（直接问答，不落盘）──

    def assist(self, prompt: str, *, model_slug: str = "deepseek/deepseek-chat") -> Dict[str, Any]:
        """开发期 LLM 助手：直接返回生成文本（不写入任何文件）。"""
        text, err = self._call_llm(_FACTOR_SYSTEM_PROMPT, prompt, model_slug=model_slug, title="Codegen Assist")
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "text": text}

    # ── 产品内 codegen（写入隔离沙箱 + 提案）──

    def propose(
        self,
        *,
        name: str,
        spec: str,
        kind: str = "factor",
        model_slug: str = "deepseek/deepseek-chat",
    ) -> Dict[str, Any]:
        """生成一份因子/策略 .py 提案，写入隔离沙箱并登记，等待 review + Governor 审批。"""
        if not flags.get_flag("OPENCODE_CODEGEN_ENABLED"):
            return {"ok": False, "error": "OPENCODE_CODEGEN_ENABLED=False（受控管道未开启）"}

        text, err = self._call_llm(
            _FACTOR_SYSTEM_PROMPT,
            f"[类型={kind}] [名称={name}]\n需求：\n{spec}",
            model_slug=model_slug,
            title=f"Codegen {kind}:{name}",
        )
        if err:
            return {"ok": False, "error": err}

        code = self._extract_python(text or "")
        if not code:
            return {"ok": False, "error": "未能从模型输出中提取 Python 代码块", "raw": (text or "")[:500]}

        proposal_id = f"cg_{uuid.uuid4().hex[:12]}"
        safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "generated"
        sandbox_path = os.path.join(SHADOW_DIR, proposal_id, f"{safe_name}.py")
        try:
            os.makedirs(os.path.dirname(sandbox_path), exist_ok=True)
            with open(sandbox_path, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as exc:
            return {"ok": False, "error": f"写入沙箱失败: {exc}"}

        # 静态安全体检
        safety = self._static_safety_check(code)

        record = {
            "proposal_id": proposal_id,
            "name": name,
            "kind": kind,
            "status": "pending_review",   # pending_review -> approved / rejected
            "sandbox_path": sandbox_path,
            "spec": spec,
            "safety": safety,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._append_proposal(record)

        # 记血缘
        try:
            env = EvolutionEnvelope.root(
                stage=STAGE_LEARN,
                source="opencode_codegen",
                payload={"proposal_id": proposal_id, "kind": kind, "name": name, "safety": safety},
                status=STATUS_PENDING,
            )
            ledger.record(env)
            record["lineage_id"] = env.lineage_id
        except Exception:
            pass

        return {"ok": True, **record}

    def list_proposals(self) -> List[Dict[str, Any]]:
        return self._load_proposals()

    def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        for p in self._load_proposals():
            if p.get("proposal_id") == proposal_id:
                code = ""
                try:
                    with open(p["sandbox_path"], "r", encoding="utf-8") as f:
                        code = f.read()
                except Exception:
                    pass
                return {**p, "code": code}
        return None

    def approve(self, proposal_id: str, *, approver: str = "manual") -> Dict[str, Any]:
        """Governor 审批通过（仅标记可合入，**不自动合并**到主干）。"""
        return self._set_status(
            proposal_id, "approved", STATUS_PASSED,
            extra={"approver": approver, "note": "已批准，需人工在隔离 worktree + paper 验证后合入"},
        )

    def reject(self, proposal_id: str, *, reason: str = "") -> Dict[str, Any]:
        return self._set_status(proposal_id, "rejected", STATUS_REJECTED, extra={"reason": reason})

    # ── 内部 ──

    def _set_status(self, proposal_id: str, status: str, env_status: str, extra: Dict[str, Any]) -> Dict[str, Any]:
        with _lock:
            proposals = self._load_proposals()
            found = None
            for p in proposals:
                if p.get("proposal_id") == proposal_id:
                    p["status"] = status
                    p.update(extra)
                    p["decided_at"] = datetime.now(timezone.utc).isoformat()
                    found = p
                    break
            if not found:
                return {"ok": False, "error": "提案不存在"}
            self._save_proposals(proposals)
        try:
            env = EvolutionEnvelope.root(
                stage=STAGE_LEARN, source="opencode_codegen_governor",
                payload={"proposal_id": proposal_id, "decision": status, **extra},
                status=env_status,
            )
            ledger.record(env)
        except Exception:
            pass
        return {"ok": True, **found}

    def _call_llm(self, system_prompt: str, user_text: str, *, model_slug: str, title: str):
        try:
            from backend.services.opencode_bridge import collect_http_agent_stream_text
            return collect_http_agent_stream_text(
                system_prompt=system_prompt,
                user_text=user_text,
                agent="codegen",
                model_slug=model_slug,
                session_title=title,
                log_prefix="Codegen",
            )
        except Exception as exc:
            return None, f"opencode 通道不可用: {exc}"

    @staticmethod
    def _extract_python(text: str) -> str:
        m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # 无围栏时，若整体像 python 则原样返回
        if "def " in text or "class " in text:
            return text.strip()
        return ""

    @staticmethod
    def _static_safety_check(code: str) -> Dict[str, Any]:
        """轻量静态安全体检：命中危险调用则标记（供 review 参考，不自动放行）。"""
        banned = ["os.system", "subprocess", "eval(", "exec(", "__import__", "open(",
                  "socket", "requests", "shutil", "pickle", "marshal"]
        hits = [b for b in banned if b in code]
        # 语法检查
        syntax_ok = True
        syntax_err = None
        try:
            compile(code, "<codegen>", "exec")
        except SyntaxError as e:
            syntax_ok = False
            syntax_err = str(e)
        return {"banned_hits": hits, "syntax_ok": syntax_ok, "syntax_error": syntax_err,
                "clean": (not hits) and syntax_ok}

    def _load_proposals(self) -> List[Dict[str, Any]]:
        if not os.path.isfile(PROPOSALS_FILE):
            return []
        try:
            with open(PROPOSALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_proposals(self, proposals: List[Dict[str, Any]]) -> None:
        os.makedirs(SHADOW_DIR, exist_ok=True)
        with open(PROPOSALS_FILE, "w", encoding="utf-8") as f:
            json.dump(proposals, f, ensure_ascii=False, indent=2)

    def _append_proposal(self, record: Dict[str, Any]) -> None:
        with _lock:
            proposals = self._load_proposals()
            proposals.append(record)
            self._save_proposals(proposals)


# 单例
governed_codegen = GovernedCodegenService()
