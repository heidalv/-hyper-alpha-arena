"""Hermes Layer 2: Prompt 自优化引擎

管理 prompt 版本、评估质量、生成优化建议、运行 A/B 测试。
通过 PromptRegistry 渲染 + 版本快照实现 prompt 的进化追踪。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
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

# 需要优化的任务 ID
OPTIMIZABLE_TASKS = [
    "task_trading_runtime_analysis",
    "task_proposal_review",
    "task_swing_agent",
    "task_trend_agent_direction",
    "task_trend_agent_review",
]

# Agent task → agent_decision_wisdom.agent_type
AGENT_TASK_TYPES = {
    "task_swing_agent": "swing",
    "task_trend_agent_direction": "trend",
    "task_trend_agent_review": "trend",
}

# 最少需要积累的 wisdom 数量才触发优化
MIN_WISDOM_FOR_OPTIMIZATION = 10
MIN_AGENT_WISDOM_FOR_OPTIMIZATION = 5
MIN_PROPOSALS_FOR_OPTIMIZATION = 10
MIN_AGENT_SAMPLES_FOR_OPTIMIZATION = 5


class PromptOptimizerEngine:
    """Layer 2: Prompt 自优化引擎。

    核心职责：
    1. 快照当前活跃 prompt 为版本记录
    2. 评估各版本 prompt 的提案质量
    3. 调用 LLM 生成优化建议
    4. Paper 默认直接激活新版本；Live 可选 A/B
    """

    @staticmethod
    def _ab_enabled() -> bool:
        try:
            from backend.config.settings import HERMES_L2_AB_ENABLED
            return bool(HERMES_L2_AB_ENABLED)
        except Exception:
            return False

    def activate_version_direct(self, task_id: str, version: str, *, reason: str = "") -> None:
        """直接将指定版本设为 active，结束该 task 上所有 running A/B。"""
        hermes_execute(
            "UPDATE prompt_versions SET status='deprecated' "
            "WHERE task_id=? AND version!=? AND status IN ('active','ab_testing')",
            (task_id, version),
        )
        hermes_execute(
            "UPDATE prompt_versions SET status='active', activated_at=datetime('now') "
            "WHERE task_id=? AND version=?",
            (task_id, version),
        )
        hermes_execute(
            """UPDATE prompt_ab_tests SET status='concluded', winner=?, concluded_at=datetime('now')
               WHERE task_id=? AND status='running'""",
            (version, task_id),
        )
        logger.info(
            "[Hermes:L2] 直接激活 prompt %s@%s %s",
            task_id, version, reason or "",
        )

    def recover_stuck_versions(self) -> Dict[str, Any]:
        """修复卡在 ab_testing 但从未晋升的 prompt（A/B 关闭或测试僵死）。"""
        if self._ab_enabled():
            return {"skipped": "ab_enabled"}

        recovered: List[str] = []
        stuck = hermes_fetchall(
            """SELECT pv.task_id, pv.version FROM prompt_versions pv
               WHERE pv.status='ab_testing'
               AND NOT EXISTS (
                 SELECT 1 FROM prompt_ab_tests ab
                 WHERE ab.task_id=pv.task_id AND ab.status='running'
               )
               ORDER BY pv.id DESC"""
        )
        for row in stuck:
            tid, ver = row["task_id"], row["version"]
            self.activate_version_direct(tid, ver, reason="recover_stuck")
            recovered.append(f"{tid}@{ver}")

        # running A/B 但 AB 已全局关闭 → 强制 B 晋升
        running = hermes_fetchall(
            "SELECT id, task_id, version_b FROM prompt_ab_tests WHERE status='running'"
        )
        for ab in running:
            self.activate_version_direct(
                ab["task_id"], ab["version_b"], reason="ab_disabled_force_b",
            )
            hermes_execute(
                "UPDATE prompt_ab_tests SET status='concluded', winner=?, concluded_at=datetime('now') WHERE id=?",
                (ab["version_b"], ab["id"]),
            )
            recovered.append(f"{ab['task_id']}@{ab['version_b']}(force)")

        if recovered:
            logger.info("[Hermes:L2] recover_stuck: %s", recovered)
        return {"recovered": recovered, "count": len(recovered)}

    # ──── 公共 API ────

    def ensure_baseline_versions(self) -> None:
        """首次启动：从 PromptRegistry 快照基线 prompt 为 v1.0.0（各可优化 task 一条 active）。"""
        for task_id in OPTIMIZABLE_TASKS:
            existing = hermes_fetchone(
                "SELECT id FROM prompt_versions WHERE task_id=? LIMIT 1",
                (task_id,),
            )
            if existing:
                continue
            full_text = self._render_from_registry(task_id)
            if not full_text:
                logger.warning("[Hermes:L2] 无法渲染基线 prompt: %s", task_id)
                continue
            hermes_execute(
                """INSERT INTO prompt_versions
                   (task_id, version, full_text, change_type, change_summary, status, created_at, activated_at)
                   VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'))""",
                (
                    task_id,
                    "1.0.0",
                    full_text,
                    "manual",
                    "Hermes 基线快照（PromptRegistry）",
                    "active",
                ),
            )
            logger.info("[Hermes:L2] 基线 prompt 快照: task=%s ver=1.0.0", task_id)

    # ──── 磁盘→数据库热同步（关键：提示词升级后自动同步，不在旧版上做优化）────

    @staticmethod
    def _parse_disk_version(task_id: str) -> Optional[str]:
        """从磁盘 .md frontmatter 解析 version 字段。"""
        from backend.services.hermes_db import resolve_hermes_prompt_path

        path = resolve_hermes_prompt_path("tasks", f"{task_id}.md")
        try:
            if not os.path.isfile(path):
                return None
            with open(path, encoding="utf-8") as f:
                head = f.read(4096)
            m = re.search(r'^version:\s*["\']?([^"\'\n]+)', head, re.MULTILINE)
            return m.group(1).strip() if m else None
        except Exception:
            return None

    @staticmethod
    def _read_disk_prompt(task_id: str) -> str:
        """直接从磁盘读取 .md 文件正文（跳过 YAML frontmatter），绕过 PromptRegistry。

        解决 Registry 缓存被污染导致渲染出垃圾内容的问题。
        如果文件有 extends 继承关系，尝试合并 layers（简单拼接）。
        """
        from backend.services.hermes_db import resolve_hermes_prompt_path

        path = resolve_hermes_prompt_path("tasks", f"{task_id}.md")
        try:
            if not os.path.isfile(path):
                return ""
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            # 去掉 YAML frontmatter (--- ... ---)
            if raw.startswith("---"):
                end = raw.find("\n---", 3)
                if end != -1:
                    body = raw[end + 4:].lstrip("\n")
                else:
                    body = raw
            else:
                body = raw

            # 尝试合并 extends layers（从 frontmatter 解析）
            extends = []
            if raw.startswith("---"):
                fm = raw[3:raw.find("\n---", 3)] if "\n---" in raw[3:] else ""
                for line in fm.split("\n"):
                    if line.strip().startswith("- "):
                        extends.append(line.strip()[2:].strip())
                # 只在 extends: 区块内才取
                extends = []
                in_extends = False
                for line in fm.split("\n"):
                    if line.strip().startswith("extends:"):
                        in_extends = True
                        continue
                    if in_extends:
                        if line.strip().startswith("- "):
                            extends.append(line.strip()[2:].strip())
                        elif line and not line[0].isspace():
                            in_extends = False

            # 拼接 layers（persona + protocol → body）
            parts = []
            for layer_id in extends:
                layer_path = resolve_hermes_prompt_path("layers", f"{layer_id}.md")
                if os.path.isfile(layer_path):
                    with open(layer_path, encoding="utf-8") as lf:
                        lraw = lf.read()
                    if lraw.startswith("---"):
                        lend = lraw.find("\n---", 3)
                        lbody = lraw[lend + 4:].lstrip("\n") if lend != -1 else lraw
                    else:
                        lbody = lraw
                    if lbody.strip():
                        parts.append(lbody.strip())

            parts.append(body.strip())
            return "\n\n".join(parts)
        except Exception as e:
            logger.warning("[Hermes:L2] _read_disk_prompt failed: %s — %s", task_id, e)
            return ""

    @staticmethod
    def _version_tuple(v: str) -> tuple:
        """版本号转可比较元组 '2.0.0' → (2, 0, 0)。"""
        try:
            return tuple(int(x) for x in re.split(r"[.\-]", v)[:4])
        except Exception:
            return (0,)

    @staticmethod
    def _content_hash(text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def sync_baseline_from_disk(self) -> Dict[str, Any]:
        """磁盘→数据库同步：当磁盘 .md 版本 > 数据库 active 版本时，重新快照基线。

        解决「提示词 .md 已手动升级但 L2 仍用旧数据库快照做优化」的致命问题。
        """
        results: Dict[str, Any] = {"synced": [], "up_to_date": [], "errors": []}
        for task_id in OPTIMIZABLE_TASKS:
            try:
                # 直接读取磁盘 .md 文件（绕过可能被污染的 PromptRegistry）
                disk_text = self._read_disk_prompt(task_id)
                disk_ver = self._parse_disk_version(task_id) or "unknown"
                if not disk_text:
                    # fallback: 尝试 Registry 渲染
                    disk_text = self._render_from_registry(task_id)
                if not disk_text:
                    results["errors"].append({"task": task_id, "error": "磁盘读取失败"})
                    continue

                disk_hash = self._content_hash(disk_text)

                # 查数据库当前 active 版本
                row = hermes_fetchone(
                    "SELECT version, full_text FROM prompt_versions WHERE task_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                    (task_id,),
                )

                if row:
                    db_ver = row["version"]
                    db_hash = self._content_hash(row["full_text"] or "")
                    # 磁盘版本 > 数据库版本，或内容 hash 变了
                    if self._version_tuple(disk_ver) > self._version_tuple(db_ver) or db_hash != disk_hash:
                        # 检查同版本号是否已存在
                        existing_same_ver = hermes_fetchone(
                            "SELECT id FROM prompt_versions WHERE task_id=? AND version=?",
                            (task_id, disk_ver),
                        )
                        if existing_same_ver:
                            # 版本号相同但内容变了：直接 UPDATE 现有记录
                            hermes_execute(
                                """UPDATE prompt_versions
                                   SET full_text=?, status='active', change_summary=?,
                                       activated_at=datetime('now')
                                   WHERE task_id=? AND version=?""",
                                (disk_text, f"磁盘内容热同步→{disk_ver}", task_id, disk_ver),
                            )
                            # 其他同 task 记录标记 deprecated
                            hermes_execute(
                                "UPDATE prompt_versions SET status='deprecated' WHERE task_id=? AND version!=? AND status='active'",
                                (task_id, disk_ver),
                            )
                        else:
                            # 旧 active 标记 deprecated
                            hermes_execute(
                                "UPDATE prompt_versions SET status='deprecated' WHERE task_id=? AND status='active'",
                                (task_id,),
                            )
                            # 写入磁盘新版
                            hermes_execute(
                                """INSERT INTO prompt_versions
                                   (task_id, version, full_text, change_type, change_summary, status, created_at, activated_at)
                                   VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'))""",
                                (
                                    task_id, disk_ver, disk_text, "manual",
                                    f"磁盘文件热同步 {db_ver}→{disk_ver}", "active",
                                ),
                            )
                        results["synced"].append({
                            "task": task_id,
                            "db_version": db_ver,
                            "disk_version": disk_ver,
                        })
                        logger.info("[Hermes:L2] 磁盘同步: task=%s %s→%s", task_id, db_ver, disk_ver)
                    else:
                        results["up_to_date"].append({"task": task_id, "version": db_ver})
                else:
                    # 无 active 记录：直接写入磁盘内容为 active
                    # 先检查同版本号是否已存在（可能被标记为 deprecated）
                    existing_same_ver = hermes_fetchone(
                        "SELECT id FROM prompt_versions WHERE task_id=? AND version=?",
                        (task_id, disk_ver),
                    )
                    if existing_same_ver:
                        # 直接 UPDATE 现有记录：恢复为 active 并更新 full_text
                        hermes_execute(
                            """UPDATE prompt_versions
                               SET full_text=?, status='active', change_summary=?,
                                   activated_at=datetime('now')
                               WHERE task_id=? AND version=?""",
                            (disk_text, f"磁盘文件热同步→{disk_ver}", task_id, disk_ver),
                        )
                    else:
                        hermes_execute(
                            """INSERT INTO prompt_versions
                               (task_id, version, full_text, change_type, change_summary, status, created_at, activated_at)
                               VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'))""",
                            (
                                task_id, disk_ver, disk_text, "manual",
                                f"磁盘文件热同步 (无active)→{disk_ver}", "active",
                            ),
                        )
                    results["synced"].append({
                        "task": task_id,
                        "db_version": "none",
                        "disk_version": disk_ver,
                    })
                    logger.info("[Hermes:L2] 磁盘同步(新建active): task=%s ver=%s", task_id, disk_ver)
            except Exception as e:
                results["errors"].append({"task": task_id, "error": str(e)})
                logger.exception("[Hermes:L2] sync_baseline_from_disk failed: %s", task_id)

        if results["synced"]:
            logger.info("[Hermes:L2] 磁盘同步完成: %d 升级 %d 最新 %d 错误",
                        len(results["synced"]), len(results["up_to_date"]), len(results["errors"]))
        return results

    def resolve_active_version(self, task_id: str) -> str:
        """返回当前 active 版本号；无记录时先 ensure 基线再查。"""
        row = hermes_fetchone(
            "SELECT version FROM prompt_versions WHERE task_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        if row:
            return row["version"]
        self.ensure_baseline_versions()
        row = hermes_fetchone(
            "SELECT version FROM prompt_versions WHERE task_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        return row["version"] if row else "1.0.0"

    def get_current_prompt(self, task_id: str) -> str:
        """获取当前活跃的 prompt 全文。优先从数据库读取，否则从 PromptRegistry 渲染。"""
        # 1. 查数据库是否有 active 版本
        active = hermes_fetchone(
            "SELECT full_text FROM prompt_versions WHERE task_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        if active:
            return active["full_text"]

        # 2. 从 PromptRegistry 渲染（首次）
        return self._render_from_registry(task_id)

    def snapshot_current_prompt(self, task_id: str, *, change_type: str = "auto_optimized") -> str:
        """快照当前 prompt 为 prompt_versions 的一条记录。返回版本号。"""
        full_text = self.get_current_prompt(task_id)
        if not full_text:
            return "0.0.1"

        # 确定版本号
        latest = hermes_fetchone(
            "SELECT version FROM prompt_versions WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        if latest:
            parts = latest["version"].split(".")
            major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
            if change_type in ("auto_optimized", "ab_test_winner"):
                minor += 1
                patch = 0
            else:
                patch += 1
            new_ver = f"{major}.{minor}.{patch}"
        else:
            new_ver = "1.0.0"

        # 写入版本
        hermes_execute(
            """INSERT INTO prompt_versions (task_id, version, full_text, change_type, status, created_at, activated_at)
               VALUES (?,?,?,?,?,datetime('now'),datetime('now'))""",
            (task_id, new_ver, full_text, change_type, "active"),
        )
        # 旧版本标记为 deprecated
        hermes_execute(
            "UPDATE prompt_versions SET status='deprecated' WHERE task_id=? AND version!=? AND status='active'",
            (task_id, new_ver),
        )
        logger.info("[Hermes:L2] Prompt 快照: task=%s ver=%s type=%s", task_id, new_ver, change_type)
        return new_ver

    def evaluate_prompt_quality(
        self,
        task_id: str,
        version: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """计算某个 prompt 版本的质量指标。

        OpenCode task：统计 proposal 的 improved/degraded 率。
        Agent task：统计 agent_decision_wisdom 的 win/loss 率（样本门槛更低）。
        """
        if task_id in AGENT_TASK_TYPES:
            return self._evaluate_agent_prompt_quality(
                task_id, version, AGENT_TASK_TYPES[task_id], since=since, until=until,
            )

        db = get_main_session()
        try:
            from backend.database.models import OpenCodeEvolutionProposalDB

            q = db.query(OpenCodeEvolutionProposalDB).filter(
                OpenCodeEvolutionProposalDB.status.in_(
                    ["paper_validated", "rolled_back"]
                ),
            )
            # 时间窗归属：仅计入该版本 active 期间创建的提案
            if since:
                q = q.filter(OpenCodeEvolutionProposalDB.created_at >= since)
            if until:
                q = q.filter(OpenCodeEvolutionProposalDB.created_at < until)
            proposals = q.order_by(OpenCodeEvolutionProposalDB.id.desc()).limit(200).all()

            improved = 0
            degraded = 0
            neutral = 0
            total = 0
            total_pnl_boost = 0.0

            for p in proposals:
                try:
                    after = json.loads(p.after_json or "{}")
                    v = after.get("verdict", "?")
                    em = after.get("eval_metrics") or {}
                    total += 1
                    if v == "improved":
                        improved += 1
                        total_pnl_boost += float(em.get("after_avg_pnl", 0) or 0) - float(
                            em.get("baseline_avg_pnl", 0) or 0
                        )
                    elif v == "degraded":
                        degraded += 1
                    else:
                        neutral += 1
                except Exception:
                    continue

            # 更新统计到 prompt_versions 表
            if total > 0:
                hermes_execute(
                    """UPDATE prompt_versions
                       SET proposals_generated=?, avg_improved_rate=?,
                           avg_degraded_rate=?, avg_quality_score=?
                       WHERE task_id=? AND version=?""",
                    (
                        total,
                        round(improved / total, 3),
                        round(degraded / total, 3),
                        round((improved - degraded) / max(total, 1), 3),
                        task_id,
                        version,
                    ),
                )

            return {
                "total": total,
                "improved_rate": round(improved / max(total, 1), 3),
                "degraded_rate": round(degraded / max(total, 1), 3),
                "avg_quality": round((improved - degraded) / max(total, 1), 3),
            }
        finally:
            db.close()

    def generate_optimization_suggestions(self, task_id: str) -> Dict[str, Any]:
        """调用 LLM 分析当前 prompt 并产出优化建议。"""
        # 确保使用最新磁盘版本的 prompt，而非旧数据库快照
        try:
            self.sync_baseline_from_disk()
        except Exception:
            logger.exception("[Hermes:L2] sync 失败（generate_optimization_suggestions）")

        latest_ver = hermes_fetchone(
            "SELECT version, full_text FROM prompt_versions WHERE task_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        if latest_ver and latest_ver.get("full_text"):
            current_prompt = latest_ver["full_text"]
            ver = latest_ver["version"]
        else:
            current_prompt = self.get_current_prompt(task_id)
            ver = "registry"
        if not current_prompt:
            return {"error": "无法获取当前 prompt"}

        quality = self.evaluate_prompt_quality(
            task_id,
            ver if ver != "registry" else self.resolve_active_version(task_id),
            since=(self._version_window(task_id, ver)[0] if ver != "registry" else None),
            until=(self._version_window(task_id, ver)[1] if ver != "registry" else None),
        )

        # 获取智慧上下文
        if task_id in AGENT_TASK_TYPES:
            from backend.services.hermes_agent_wisdom_engine import build_agent_wisdom_context
            wisdom_ctx = build_agent_wisdom_context(AGENT_TASK_TYPES[task_id], limit=10)
        else:
            wisdom_ctx = proposal_wisdom.build_wisdom_context(limit=10)

        # 构建优化分析 prompt
        system_prompt = self._load_l2_system_prompt()
        user_text = (
            f"## Prompt 优化分析: {task_id}\n\n"
            f"### 当前 Prompt 质量指标\n"
            f"- 提案总数: {quality.get('total', 0)}\n"
            f"- improved 率: {quality.get('improved_rate', 0):.1%}\n"
            f"- degraded 率: {quality.get('degraded_rate', 0):.1%}\n"
            f"- 综合质量: {quality.get('avg_quality', 0):.3f}\n\n"
            f"### 历史提案智慧\n{wisdom_ctx}\n\n"
            f"### 当前 Prompt 全文\n```markdown\n{current_prompt[:8000]}\n```\n\n"
            f"请评估该 prompt 的质量，给出最多 3 条优化建议和优化后的完整 prompt 文本。"
        )

        result = self._call_llm(system_prompt, user_text, task_id)
        return result

    def start_ab_test(self, task_id: str, version_a: str, version_b: str) -> int:
        """启动 A/B 测试。"""
        ab_id = hermes_execute(
            """INSERT INTO prompt_ab_tests (task_id, version_a, version_b, started_at, status)
               VALUES (?,?,?,datetime('now'),'running')""",
            (task_id, version_a, version_b),
        )
        # version_a 保持 active（对照组继续生效）；version_b 为 ab_testing
        hermes_execute(
            "UPDATE prompt_versions SET status='ab_testing' WHERE task_id=? AND version=?",
            (task_id, version_b),
        )
        hermes_execute(
            "UPDATE prompt_versions SET status='active' WHERE task_id=? AND version=?",
            (task_id, version_a),
        )
        logger.info("[Hermes:L2] A/B 测试启动: task=%s A=%s B=%s id=%d", task_id, version_a, version_b, ab_id)
        return ab_id

    def _version_active_windows(
        self, task_id: str, version_a: str, version_b: str
    ) -> tuple:
        """计算两个版本的 active 时间窗 (since, until)，用于 A/B 归因。

        每个版本的「开始」=其 activated_at；「结束」=同一 task 内按时间更晚的下一条
        prompt_versions 记录的 created_at（即该版本被替换/降级的时刻），无则到当前时刻。
        无法确定时间戳时返回 (None, None)，回退为全量统计（兼容旧数据）。
        """
        rows = hermes_fetchall(
            "SELECT version, activated_at, created_at FROM prompt_versions "
            "WHERE task_id=? ORDER BY id ASC",
            (task_id,),
        )
        def _parse(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(str(s).replace("Z", ""))
            except Exception:
                return None

        ordered = [(_parse(r["activated_at"]) or _parse(r["created_at"]), r["version"]) for r in rows]

        def _window(ver: str):
            idx = None
            for i, (_ts, v) in enumerate(ordered):
                if v == ver:
                    idx = i
                    break
            if idx is None:
                return (None, None)
            # 首版基线（v1.0.0）：active 自系统上线起，不按快照 activated_at 截断历史提案
            if idx == 0:
                start = None
            else:
                start = ordered[idx][0]
            end = None
            for ts, _v in ordered[idx + 1 :]:
                if ts:
                    end = ts
                    break
            return (start, end)

        return (_window(version_a), _window(version_b))

    def _version_window(self, task_id: str, version: str) -> tuple:
        """单个 prompt 版本的 active 时间窗 (since, until)。"""
        win_a, _ = self._version_active_windows(task_id, version, version)
        return win_a

    def evaluate_ab_test(self, ab_test_id: int) -> Dict[str, Any]:
        """评估 A/B 测试结果。"""
        test = hermes_fetchone(
            "SELECT * FROM prompt_ab_tests WHERE id=? AND status='running'",
            (ab_test_id,),
        )
        if not test:
            return {"error": "A/B 测试不存在或已结束"}

        # 评估 A/B 测试结果 — B 版在测试期已通过 PromptRegistry 获得真实流量
        win_a, win_b = self._version_active_windows(test["task_id"], test["version_a"], test["version_b"])
        qa = self.evaluate_prompt_quality(test["task_id"], test["version_a"], *win_a)
        qb = self.evaluate_prompt_quality(test["task_id"], test["version_b"], *win_b)

        # 简单判定：improved_rate 更高的胜出
        winner = "tie"
        if qa["improved_rate"] > qb["improved_rate"] + 0.05:
            winner = "A"
        elif qb["improved_rate"] > qa["improved_rate"] + 0.05:
            winner = "B"

        hermes_execute(
            """UPDATE prompt_ab_tests
               SET proposals_a=?, proposals_b=?,
                   improved_rate_a=?, improved_rate_b=?,
                   degraded_rate_a=?, degraded_rate_b=?,
                   avg_quality_a=?, avg_quality_b=?,
                   winner=?, status='concluded', concluded_at=datetime('now')
               WHERE id=?""",
            (
                qa["total"], qb["total"],
                qa["improved_rate"], qb["improved_rate"],
                qa["degraded_rate"], qb["degraded_rate"],
                qa["avg_quality"], qb["avg_quality"],
                winner, ab_test_id,
            ),
        )

        # 胜者晋升为 active；tie 时若 B 有样本则优先新版本（减门）
        if winner == "tie" and qb.get("total", 0) >= 3:
            winner = "B"
        win_ver = test["version_b"] if winner == "B" else test["version_a"]
        self.activate_version_direct(test["task_id"], win_ver, reason=f"ab_winner={winner}")

        logger.info(
            "[Hermes:L2] A/B 测试 %d 结束: winner=%s A_ir=%.1f%% B_ir=%.1f%%",
            ab_test_id, winner, qa["improved_rate"] * 100, qb["improved_rate"] * 100,
        )
        return {"winner": winner, "quality_a": qa, "quality_b": qb}

    def auto_optimize_cycle(self) -> Dict[str, Any]:
        """完整的自动优化周期：评估 → 生成建议 → 快照 → 启动 A/B 测试。"""
        # 关键：先检查磁盘 .md 是否有新版本，确保不在旧 prompt 基础上做优化
        try:
            self.sync_baseline_from_disk()
        except Exception:
            logger.exception("[Hermes:L2] sync_baseline_from_disk 失败（不影响后续）")

        results = {}
        for task_id in OPTIMIZABLE_TASKS:
            try:
                version = self.resolve_active_version(task_id)
                since, until = self._version_window(task_id, version)
                quality = self.evaluate_prompt_quality(
                    task_id, version, since=since, until=until,
                )
                total = quality.get("total", 0)

                is_agent = task_id in AGENT_TASK_TYPES
                if is_agent:
                    agent_type = AGENT_TASK_TYPES[task_id]
                    wisdom_count = len(hermes_fetchall(
                        "SELECT id FROM agent_decision_wisdom WHERE agent_type=?",
                        (agent_type,),
                    ))
                    min_wisdom = MIN_AGENT_WISDOM_FOR_OPTIMIZATION
                    min_samples = MIN_AGENT_SAMPLES_FOR_OPTIMIZATION
                else:
                    wisdom_count = len(hermes_fetchall(
                        "SELECT id FROM proposal_wisdom_records", ()
                    ))
                    min_wisdom = MIN_WISDOM_FOR_OPTIMIZATION
                    min_samples = MIN_PROPOSALS_FOR_OPTIMIZATION

                degraded = quality.get("degraded_rate", 0)

                if wisdom_count < min_wisdom:
                    results[task_id] = {
                        "skipped": f"wisdom={wisdom_count} < {min_wisdom}"
                    }
                    continue

                if total < min_samples:
                    label = "agent samples" if is_agent else "proposals"
                    results[task_id] = {"skipped": f"insufficient {label} ({total})"}
                    continue

                # 生成优化建议
                suggestions = self.generate_optimization_suggestions(task_id)
                if suggestions.get("error") or not suggestions.get("target_prompt_text"):
                    err = suggestions.get("error") or "LLM 未产出有效优化"
                    results[task_id] = {"skipped": err}
                    continue

                # 快照旧版本
                old_ver = self.snapshot_current_prompt(task_id, change_type="auto_optimized")

                # 写入新版本
                new_ver = self._bump_version(old_ver)
                if self._ab_enabled():
                    hermes_execute(
                        """INSERT INTO prompt_versions
                           (task_id, version, full_text, change_type, parent_version, status, created_at)
                           VALUES (?,?,?,?,?,?,datetime('now'))""",
                        (task_id, new_ver, suggestions["target_prompt_text"],
                         "auto_optimized", old_ver, "ab_testing"),
                    )
                    ab_id = self.start_ab_test(task_id, old_ver, new_ver)
                    results[task_id] = {
                        "mode": "ab_test",
                        "old_version": old_ver,
                        "new_version": new_ver,
                        "ab_test_id": ab_id,
                        "suggestions_count": len(suggestions.get("suggestions", [])),
                    }
                else:
                    hermes_execute(
                        """INSERT INTO prompt_versions
                           (task_id, version, full_text, change_type, parent_version, status, created_at, activated_at)
                           VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'))""",
                        (task_id, new_ver, suggestions["target_prompt_text"],
                         "auto_optimized", old_ver, "active"),
                    )
                    self.activate_version_direct(task_id, new_ver, reason="paper_direct")
                    results[task_id] = {
                        "mode": "direct_active",
                        "old_version": old_ver,
                        "new_version": new_ver,
                        "suggestions_count": len(suggestions.get("suggestions", [])),
                    }
            except Exception as e:
                logger.error("[Hermes:L2] auto_optimize %s: %s", task_id, e, exc_info=True)
                results[task_id] = {"error": str(e)}
        self._finalize_l2_run_log(results)
        return results

    def _finalize_l2_run_log(self, results: Dict[str, Any]) -> None:
        """周期结束：有成功则清错误；全失败则写汇总。"""
        successes = [
            tid for tid, r in results.items()
            if isinstance(r, dict) and (r.get("ab_test_id") or r.get("mode") == "direct_active")
        ]
        hard_errors = [
            f"{tid}: {r.get('error') or r.get('skipped')}"
            for tid, r in results.items()
            if isinstance(r, dict)
            and (r.get("error") or str(r.get("skipped", "")).startswith("timed out"))
        ]
        try:
            from backend.services.hermes_db import upsert_task_run
            if successes:
                upsert_task_run(
                    "hermes_prompt_optimize",
                    last_error=None,
                    last_status="ok",
                )
            elif hard_errors:
                upsert_task_run(
                    "hermes_prompt_optimize",
                    last_error=hard_errors[-1][:500],
                    last_status="error",
                )
        except Exception:
            pass

    def evaluate_all_ab_tests(self) -> int:
        """评估所有运行中的 A/B 测试。返回结案数量。"""
        tests = hermes_fetchall(
            "SELECT id FROM prompt_ab_tests WHERE status='running'"
        )
        concluded = 0
        for t in tests:
            try:
                self.evaluate_ab_test(t["id"])
                concluded += 1
            except Exception as e:
                logger.error("[Hermes:L2] AB test eval %d: %s", t["id"], e)
        return concluded

    # ──── 私有辅助 ────

    def _evaluate_agent_prompt_quality(
        self,
        task_id: str,
        version: str,
        agent_type: str,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """Agent task 质量：基于 agent_decision_wisdom 的 win/loss 统计。"""
        sql = "SELECT outcome, pnl FROM agent_decision_wisdom WHERE agent_type=?"
        params: List[Any] = [agent_type]
        if since:
            ts = since.isoformat() if hasattr(since, "isoformat") else str(since)
            sql += " AND created_at >= ?"
            params.append(ts)
        if until:
            ts = until.isoformat() if hasattr(until, "isoformat") else str(until)
            sql += " AND created_at < ?"
            params.append(ts)
        sql += " ORDER BY id DESC LIMIT 200"
        rows = hermes_fetchall(sql, tuple(params))

        wins = losses = neutral = 0
        total_pnl = 0.0
        for row in rows:
            outcome = (row.get("outcome") or "").lower()
            pnl = float(row.get("pnl") or 0)
            total_pnl += pnl
            if outcome == "win":
                wins += 1
            elif outcome == "loss":
                losses += 1
            else:
                neutral += 1

        total = len(rows)
        if total > 0:
            hermes_execute(
                """UPDATE prompt_versions
                   SET proposals_generated=?, avg_improved_rate=?,
                       avg_degraded_rate=?, avg_quality_score=?
                   WHERE task_id=? AND version=?""",
                (
                    total,
                    round(wins / total, 3),
                    round(losses / total, 3),
                    round((wins - losses) / max(total, 1), 3),
                    task_id,
                    version,
                ),
            )

        return {
            "total": total,
            "improved_rate": round(wins / max(total, 1), 3),
            "degraded_rate": round(losses / max(total, 1), 3),
            "avg_quality": round((wins - losses) / max(total, 1), 3),
            "avg_pnl": round(total_pnl / max(total, 1), 2),
        }

    def _render_from_registry(self, task_id: str) -> str:
        """从 PromptRegistry 渲染当前 prompt。"""
        try:
            from backend.services.prompt_registry import get_prompt_registry
            registry = get_prompt_registry()
            return registry.render_task(task_id, {})
        except Exception as e:
            logger.warning("[Hermes:L2] PromptRegistry render failed: %s", e)
            try:
                from backend.services.prompt_registry import get_prompt_registry
                get_prompt_registry.cache_clear()
                registry = get_prompt_registry()
                return registry.render_task(task_id, {})
            except Exception as e2:
                logger.warning("[Hermes:L2] PromptRegistry retry failed: %s", e2)
            return ""

    def _load_l2_system_prompt(self) -> str:
        """加载元分析系统 prompt。"""
        from backend.services.hermes_db import resolve_hermes_prompt_path

        prompt_path = resolve_hermes_prompt_path("tasks", "task_prompt_optimization.md")
        try:
            if os.path.isfile(prompt_path):
                with open(prompt_path, encoding="utf-8") as f:
                    return f.read()
        except Exception:
            pass
        # 内联 fallback
        return (
            "You are Hermes, a prompt optimization specialist. "
            "Analyze the given prompt and suggest evidence-based improvements. "
            "Output JSON with: overall_assessment, suggestions (array of "
            "{type, target_section, issue, proposed_change, rationale, confidence}), "
            "and target_prompt_text (full optimized prompt)."
        )

    def _call_llm(
        self, system_prompt: str, user_text: str, task_id: str
    ) -> Dict[str, Any]:
        """调用 LLM 进行分析。"""
        try:
            from backend.services.opencode_bridge import (
                collect_http_agent_stream_text,
                _agent_plan,
                _model,
                _extract_json,
            )
            raw, err = collect_http_agent_stream_text(
                system_prompt=system_prompt,
                user_text=user_text,
                agent=_agent_plan(),
                model_slug=_model(),
                session_title=f"Hermes L2: {task_id}",
                log_prefix=f"Hermes:L2:{task_id}",
                idle_timeout_s=900.0,
                max_duration_s=7200.0,
            )
            if err:
                self._log_l2_error(task_id, err)
                return {"error": err}

            parsed = _extract_json(raw or "")
            if not parsed.get("target_prompt_text"):
                parsed = self._enrich_llm_result(parsed, raw_hint=raw or "")
            return parsed
        except Exception as e:
            self._log_l2_error(task_id, str(e))
            return {"error": str(e)}

    def _enrich_llm_result(self, result: Dict[str, Any], *, raw_hint: str) -> Dict[str, Any]:
        """JSON 缺 target_prompt_text 时从 markdown 代码块兜底提取。"""
        if result.get("target_prompt_text"):
            return result
        text = raw_hint or json.dumps(result, ensure_ascii=False)
        for pattern in (
            r"```(?:markdown|md|text)?\s*\n([\s\S]+?)```",
            r'"target_prompt_text"\s*:\s*"((?:\\.|[^"\\])*)"',
        ):
            m = re.search(pattern, text)
            if m:
                candidate = m.group(1).strip()
                if len(candidate) > 80:
                    result["target_prompt_text"] = candidate.replace("\\n", "\n")
                    return result
        return result

    def _log_l2_error(self, task_id: str, err: str) -> None:
        logger.error("[Hermes:L2] auto_optimize %s: %s", task_id, err)
        try:
            from backend.services.hermes_db import upsert_task_run
            upsert_task_run(
                "hermes_prompt_optimize",
                last_error=f"{task_id}: {err[:500]}",
                last_status="error",
            )
        except Exception:
            pass

    def _bump_version(self, old_ver: str) -> str:
        parts = old_ver.split(".")
        parts[1] = str(int(parts[1]) + 1)
        parts[2] = "0"
        return ".".join(parts)


# 全局单例
prompt_optimizer = PromptOptimizerEngine()
