"""OpenCode Prompt Registry — 从 docs/opencode/prompts 加载并渲染 task prompt。"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def _registry_root() -> str:
    return os.path.join(_repo_root(), "docs", "opencode", "prompts")


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3 :].lstrip("\n")
    return text


@lru_cache(maxsize=1)
def _load_manifest() -> Dict[str, Any]:
    import yaml

    manifest_path = os.path.join(_registry_root(), "manifest.yaml")
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    layer_map = {item["id"]: item for item in data.get("layers", [])}
    task_map = {item["id"]: item for item in data.get("tasks", [])}
    return {"raw": data, "layers": layer_map, "tasks": task_map}


class PromptRegistry:
    """内存单例：按 manifest 拼接 layer + task 并替换 {{var}}。"""

    def __init__(self) -> None:
        self._manifest = _load_manifest()

    def list_tasks(self) -> List[str]:
        return sorted(self._manifest["tasks"].keys())

    def list_layers(self) -> List[str]:
        return sorted(self._manifest["layers"].keys())

    def render_layer(self, layer_id: str) -> str:
        item = self._manifest["layers"].get(layer_id)
        if not item:
            raise KeyError(f"unknown layer: {layer_id}")
        path = os.path.join(_registry_root(), item["path"])
        return _strip_frontmatter(_read_text(path))

    def render_task(self, task_id: str, variables: Optional[Dict[str, Any]] = None, *, consumer: str = "") -> str:
        # L2 自进化回灌 + A/B 真分流（见 prompt_l2_resolver）
        try:
            from backend.services.prompt_l2_resolver import resolve_l2_prompt, set_last_resolution
            res = resolve_l2_prompt(task_id, consumer=consumer)
            if res and res.full_text:
                set_last_resolution(res)
                return self._inject_variables(res.full_text, variables or {})
        except Exception as e:
            logger.debug("[PromptRegistry] L2 resolve 失败 task=%s: %s", task_id, e)

        override = _try_load_l2_active_prompt(task_id)
        if override:
            return self._inject_variables(override, variables or {})

        task = self._manifest["tasks"].get(task_id)
        if not task:
            raise KeyError(f"unknown task: {task_id}")

        parts: List[str] = []
        for layer_id in task.get("extends") or []:
            parts.append(self.render_layer(layer_id))
        task_path = os.path.join(_registry_root(), task["path"])
        parts.append(_strip_frontmatter(_read_text(task_path)))

        merged = "\n\n".join(p.strip() for p in parts if p.strip())
        return self._inject_variables(merged, variables or {})

    def _inject_variables(self, text: str, variables: Dict[str, Any]) -> str:
        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in variables:
                return match.group(0)
            val = variables[key]
            if isinstance(val, (dict, list)):
                import json
                return json.dumps(val, ensure_ascii=False, indent=2)
            return str(val)

        return _PLACEHOLDER_RE.sub(_replace, text)


_registry: Optional[PromptRegistry] = None


def get_prompt_registry() -> PromptRegistry:
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry


def _try_load_l2_active_prompt(task_id: str) -> str:
    """从 Hermes L2 的 prompt_versions 读取该 task 的 active 优化版本（只读）。

    失败/无记录时返回空串（调用方回退到磁盘 manifest）。刻意用 try/except 包裹，
    避免 hermes_db 未初始化、表缺失、循环导入等任何异常波及到 registry 这一公共入口。

    S1-13b 修复：版本校验 —— 若 DB version != manifest version，返回空串回退到文件。
    避免 L2 缓存的旧版 prompt 在文件升级（v2→v3）后仍被使用。
    """
    try:
        from backend.services.hermes_db import hermes_fetchone
        row = hermes_fetchone(
            "SELECT full_text, version FROM prompt_versions WHERE task_id=? AND status='active' "
            "ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        if row and row.get("full_text"):
            # S1-13b 版本校验
            try:
                manifest = _load_manifest()
                task_cfg = manifest["tasks"].get(task_id, {})
                manifest_ver = str(task_cfg.get("version") or "0")
                db_ver = str(row.get("version") or "0")
                if manifest_ver != "0" and db_ver != manifest_ver:
                    logger.info(
                        "[PromptRegistry] %s DB version=%s != manifest version=%s, 回退到文件",
                        task_id, db_ver, manifest_ver,
                    )
                    return ""
            except Exception:
                pass
            return row["full_text"]
    except Exception as e:  # noqa: BLE001 — registry 不可因 L2 故障而崩
        logger.debug("[PromptRegistry] L2 override 读取失败 task=%s: %s", task_id, e)
    return ""
