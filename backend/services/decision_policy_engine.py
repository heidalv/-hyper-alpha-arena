"""
决策规则引擎 — 读取 data/decision_policies/*.yaml，热更新无需 uvicorn reload。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

POLICY_DIR = os.path.join("data", "decision_policies")
_cache: Dict[str, Any] = {"ts": 0.0, "policies": {}}


@dataclass
class PolicyResult:
    effect: str  # allow | block | pass
    rule_id: Optional[str] = None
    reason: Optional[str] = None


def _load_yaml(path: str) -> Dict[str, Any]:
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # 无 PyYAML 时用极简 JSON 兼容
        if path.endswith(".json"):
            import json
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        logger.warning("[PolicyEngine] PyYAML 未安装，跳过 %s", path)
        return {}
    except Exception as err:
        logger.warning("[PolicyEngine] 加载 %s 失败: %s", path, err)
        return {}


def reload_policies(*, max_age: float = 60.0) -> Dict[str, Any]:
    now = time.time()
    if now - _cache["ts"] < max_age:
        return _cache["policies"]
    policies: Dict[str, Any] = {}
    if os.path.isdir(POLICY_DIR):
        for fname in os.listdir(POLICY_DIR):
            if fname.endswith((".yaml", ".yml", ".json")):
                name = fname.rsplit(".", 1)[0]
                policies[name] = _load_yaml(os.path.join(POLICY_DIR, fname))
    _cache["ts"] = now
    _cache["policies"] = policies
    return policies


def _match_when(when: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    for key, expected in when.items():
        if key == "action":
            if str(ctx.get("action", "")).lower() != str(expected).lower():
                return False
        elif key == "floating_loss_pct_lt":
            if float(ctx.get("floating_loss_pct") or 0) >= float(expected):
                return False
        elif key == "floating_loss_pct_gte":
            if float(ctx.get("floating_loss_pct") or 0) < float(expected):
                return False
        elif key == "risk_score_lt":
            if float(ctx.get("risk_score") or 0) >= float(expected):
                return False
        elif key == "sl_breach_ratio_gte":
            if float(ctx.get("sl_breach_ratio") or 0) < float(expected):
                return False
        elif key == "tier":
            if str(ctx.get("tier", "")).lower() != str(expected).lower():
                return False
    return True


def evaluate(policy_name: str, ctx: Dict[str, Any]) -> PolicyResult:
    policies = reload_policies()
    doc = policies.get(policy_name) or {}
    rules: List[Dict[str, Any]] = doc.get("rules") or []
    for rule in rules:
        when = rule.get("when") or {}
        if _match_when(when, ctx):
            effect = str(rule.get("effect", "pass")).lower()
            return PolicyResult(
                effect=effect,
                rule_id=rule.get("id"),
                reason=rule.get("reason"),
            )
    return PolicyResult(effect="pass")


def apply_policy_patch(policy_name: str, content: str, *, ext: str = "yaml", proposal_id: Optional[int] = None) -> str:
    os.makedirs(POLICY_DIR, exist_ok=True)
    path = os.path.join(POLICY_DIR, f"{policy_name}.{ext}")
    # 写前快照，供提案退化时回滚（与 apply_policy_field_patch 同一快照目录）
    if proposal_id is not None and os.path.isfile(path):
        snap_dir = os.path.join("data", "policy_snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        snap = os.path.join(snap_dir, f"{proposal_id}_{policy_name}_before.{ext}")
        try:
            with open(path, "r", encoding="utf-8") as src, open(snap, "w", encoding="utf-8") as dst:
                dst.write(src.read())
        except Exception as err:
            logger.warning("[PolicyEngine] 快照失败 %s: %s", snap, err)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    _cache["ts"] = 0.0
    return path


def rollback_policy_snapshot(proposal_id: int) -> int:
    """回滚某提案写入的所有 policy YAML（从 data/policy_snapshots 还原）。

    返回成功还原的文件数。供 evaluate_applied_proposals 判定退化时调用，
    补齐此前「只回滚 tuning、policy 永久残留」的断点。
    """
    snap_dir = os.path.join("data", "policy_snapshots")
    if not os.path.isdir(snap_dir):
        return 0
    prefix = f"{proposal_id}_"
    restored = 0
    for fname in os.listdir(snap_dir):
        if not fname.startswith(prefix) or "_before." not in fname:
            continue
        # 文件名格式: {pid}_{policy_name}_before.{ext}
        try:
            rest = fname[len(prefix):]
            policy_part, ext = rest.rsplit("_before.", 1)
            target = os.path.join(POLICY_DIR, f"{policy_part}.{ext}")
            snap_path = os.path.join(snap_dir, fname)
            with open(snap_path, "r", encoding="utf-8") as src, open(target, "w", encoding="utf-8") as dst:
                dst.write(src.read())
            restored += 1
            logger.info("[PolicyEngine] 回滚 policy %s (proposal=%s)", target, proposal_id)
        except Exception as err:
            logger.warning("[PolicyEngine] 回滚 %s 失败: %s", fname, err)
    if restored:
        _cache["ts"] = 0.0
    return restored


def parse_policy_patch_key(
    key: str, policy_hint: Optional[str] = None
) -> Tuple[str, str, Optional[str]]:
    """统一解析 policy_yaml patch 的 key，杜绝「校验放行但应用解析不同」的根因。

    支持三种来自 LLM 的格式：
      1. ``"<policy>.yaml#<rule_id>.<field>"`` —— 带文件前缀（如 #26 的写法）
      2. ``"<rule_id>.<field>"``              —— 无前缀，policy 取 policy_hint
      3. ``"<rule_id>"``                       —— 整 rule（value 应为 dict 多字段）

    返回 ``(policy_name, rule_id, field_or_None)``，policy_name 已去 .yaml/.yml 后缀。
    field 为 None 时表示「整 rule 多字段」，应配合 dict value 逐字段写入。
    """
    raw = (key or "").strip()
    policy_name = (policy_hint or "").strip()
    if "#" in raw:
        prefix, raw = raw.split("#", 1)
        prefix = prefix.strip()
        # 仅当调用方未显式给出 policy 时，才采信 key 前缀里的文件名
        if prefix and not policy_name:
            policy_name = prefix
        raw = raw.strip()
    policy_name = (
        policy_name.replace(".yaml", "").replace(".yml", "").strip() or "master_close"
    )
    if "." in raw:
        rule_id, field = raw.split(".", 1)
        return policy_name, rule_id.strip(), field.strip()
    return policy_name, raw.strip(), None


def apply_policy_field_patch(
    policy_name: str,
    rule_id: str,
    field: str,
    value: Any,
    *,
    ext: str = "yaml",
    proposal_id: Optional[int] = None,
) -> str:
    """按 rule id 修改 YAML 规则 when 字段，如 block_master_close_tiny_loss.risk_score_lt。"""
    import yaml

    os.makedirs(POLICY_DIR, exist_ok=True)
    path = os.path.join(POLICY_DIR, f"{policy_name}.{ext}")
    if proposal_id is not None and os.path.isfile(path):
        snap_dir = os.path.join("data", "policy_snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        snap = os.path.join(snap_dir, f"{proposal_id}_{policy_name}_before.{ext}")
        with open(path, "r", encoding="utf-8") as src, open(snap, "w", encoding="utf-8") as dst:
            dst.write(src.read())

    doc = _load_yaml(path) if os.path.isfile(path) else {"rules": []}
    rules: List[Dict[str, Any]] = doc.get("rules") or []
    updated = False
    for rule in rules:
        if str(rule.get("id")) == rule_id:
            when = dict(rule.get("when") or {})
            when[field] = value
            rule["when"] = when
            updated = True
            break
    if not updated:
        rules.append({
            "id": rule_id,
            "when": {field: value},
            "effect": "block",
            "reason": f"OpenCode patch {field}={value}",
        })
        doc["rules"] = rules

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
    _cache["ts"] = 0.0
    logger.info("[PolicyEngine] patched %s rule=%s %s=%s", policy_name, rule_id, field, value)
    return path
