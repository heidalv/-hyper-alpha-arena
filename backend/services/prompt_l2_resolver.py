"""Hermes L2 Prompt 解析 — active 优先；可选按请求随机 A/B。"""
from __future__ import annotations

import logging
import random
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_thread_ctx = threading.local()


@dataclass
class PromptResolution:
    task_id: str
    full_text: str
    version: str = ""
    version_id: Optional[int] = None
    ab_test_id: Optional[int] = None
    ab_arm: str = "active"  # A | B | active
    source: str = "manifest"  # hermes_l2 | manifest

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "version": self.version,
            "version_id": self.version_id,
            "ab_test_id": self.ab_test_id,
            "ab_arm": self.ab_arm,
            "source": self.source,
        }


def _ab_enabled() -> bool:
    try:
        from backend.config.settings import HERMES_L2_AB_ENABLED
        return bool(HERMES_L2_AB_ENABLED)
    except Exception:
        return False


def _ab_split_ratio() -> float:
    try:
        from backend.config.settings import HERMES_AB_TRAFFIC_RATIO
        return float(HERMES_AB_TRAFFIC_RATIO)
    except Exception:
        return 0.5


def resolve_l2_prompt(task_id: str, *, consumer: str = "") -> Optional[PromptResolution]:
    """解析 Hermes L2 prompt。

    - 默认（HERMES_L2_AB_ENABLED=false）：只读 status=active，新版本优化后立即生效。
    - A/B 开启时：按**每次请求随机**分流到 B（ab_testing），避免 consumer 固定永远走旧版。
    """
    del consumer  # 保留参数兼容；不再做 per-consumer 固定分桶
    try:
        from backend.services.hermes_db import hermes_fetchone
    except Exception as exc:
        logger.debug("[PromptL2] hermes_db 不可用: %s", exc)
        return None

    try:
        running = None
        if _ab_enabled():
            running = hermes_fetchone(
                """SELECT id, task_id, version_a, version_b FROM prompt_ab_tests
                   WHERE task_id=? AND status='running' ORDER BY id DESC LIMIT 1""",
                (task_id,),
            )

        if running and _ab_enabled():
            ab_id = int(running["id"])
            use_b = random.random() < _ab_split_ratio()
            if use_b:
                row = hermes_fetchone(
                    """SELECT id, version, full_text FROM prompt_versions
                       WHERE task_id=? AND version=? AND status='ab_testing' LIMIT 1""",
                    (task_id, running["version_b"]),
                )
                if row and row.get("full_text"):
                    return PromptResolution(
                        task_id=task_id,
                        full_text=row["full_text"],
                        version=row.get("version") or running["version_b"],
                        version_id=row.get("id"),
                        ab_test_id=ab_id,
                        ab_arm="B",
                        source="hermes_l2",
                    )

        row = hermes_fetchone(
            """SELECT id, version, full_text FROM prompt_versions
               WHERE task_id=? AND status='active' ORDER BY id DESC LIMIT 1""",
            (task_id,),
        )
        if row and row.get("full_text"):
            # S1-13 修复：版本校验 —— 若 DB 里的 version 低于 manifest 里的 version，
            # 说明文件已升级（如 v2→v3），DB 缓存过期，回退到 manifest 让 render_task 读文件。
            # 这样新版 prompt 文件部署后自动生效，无需手动清理 DB。
            try:
                from backend.services.prompt_registry import _load_manifest
                manifest = _load_manifest()
                task_cfg = manifest["tasks"].get(task_id, {})
                manifest_ver = str(task_cfg.get("version") or "0")
                db_ver = str(row.get("version") or "0")
                if manifest_ver != "0" and db_ver != manifest_ver:
                    logger.info(
                        "[PromptL2] %s DB version=%s < manifest version=%s, 回退到 manifest（prompt 已升级）",
                        task_id, db_ver, manifest_ver,
                    )
                    return None
            except Exception as _ver_err:
                logger.debug("[PromptL2] %s 版本校验跳过: %s", task_id, _ver_err)

            return PromptResolution(
                task_id=task_id,
                full_text=row["full_text"],
                version=row.get("version") or "",
                version_id=row.get("id"),
                ab_test_id=int(running["id"]) if running else None,
                ab_arm="A" if running else "active",
                source="hermes_l2",
            )
    except Exception as exc:
        logger.debug("[PromptL2] resolve 失败 task=%s: %s", task_id, exc)
    return None


def set_last_resolution(res: PromptResolution) -> None:
    if not hasattr(_thread_ctx, "resolutions"):
        _thread_ctx.resolutions = {}
    _thread_ctx.resolutions[res.task_id] = res


def get_last_resolution(task_id: str) -> Optional[PromptResolution]:
    store = getattr(_thread_ctx, "resolutions", None) or {}
    return store.get(task_id)


def pick_ab_arm_for_test(task_id: str, consumer: str = "") -> Tuple[bool, float]:
    """供单测：返回是否走 B 及 ratio（Monte Carlo 近似）。"""
    del task_id, consumer
    ratio = _ab_split_ratio()
    return random.random() < ratio, ratio
