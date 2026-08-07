"""
RuntimeTuningStore — 统一运行时阈值热改（data/runtime_tuning.json）

合并 v5_runtime_gates、总控阈值、持仓超时、套利 multipliers；60s 缓存，不触发 uvicorn reload。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

TUNING_FILE = os.path.join("data", "runtime_tuning.json")
SNAPSHOT_DIR = os.path.join("data", "runtime_tuning_snapshots")
OVERLAY_DIR = os.path.join("data", "runtime_tuning_overlays")
LEGACY_GATES_FILE = os.path.join("data", "v5_runtime_gates.json")
_cache: dict = {"ts": 0.0, "data": {}}

# schema: key -> {value, min, max} or nested dict
_DEFAULT_SCHEMA: Dict[str, Any] = {
    "master_reduce_min_loss_pct": {"value": 0.10, "min": 0.05, "max": 0.20},
    "master_close_min_loss_pct_by_tier": {
        "short": 0.02, "mid": 0.04, "long": 0.07,
    },
    "tier_max_hold_sec": {"short": 7200, "mid": 172800, "long": 604800},
    "max_daily_trades": {"value": 12, "min": 3, "max": 20},
    # 日开仓总基数（旧版共享基数，已被 scalp/trend 独立配额取代，保留键供回退/兼容）
    "daily_cap_base": {"value": 120, "min": 10, "max": 300},
    # 各 tier 独立日开仓配额（2026-07-23 改造：替代共享 base × 比例分配）。
    # 各策略页面独立配置、独立保存、独立生效；前端 PUT /daily-cap/{tier} 热改直达 unified_gate。
    # 默认：短线 60/天、长线 10/天（用户明确指定，仅作初始默认值，可在前端修改）。
    "scalp_daily_cap": {"value": 150, "min": 10, "max": 300},
    "trend_daily_cap": {"value": 15, "min": 1, "max": 60},
    "scalp_min_confidence": {"value": 70, "min": 60, "max": 90},
    "min_risk_reward": {"value": 1.8, "min": 1.2, "max": 2.5},
    # MaturityController 高层旋钮（OpenCode 慢循环只调这些，不直接改各处硬阈值）：
    #   maturity_max_warmup_relief —— warmup 期置信门最大放宽分数
    #   maturity_global_n1/n2      —— 全局维度 warmup/growth/mature 分界样本数
    "maturity_max_warmup_relief": {"value": 15, "min": 5, "max": 20},
    "maturity_global_n1": {"value": 20, "min": 5, "max": 60},
    "maturity_global_n2": {"value": 60, "min": 20, "max": 120},
    # 被禁用的交易 nature 列表（由 RuntimeGovernor 仲裁后写入，默认空=不禁用）。
    # 此前由 decision_feedback 直写 v5_runtime_gates.json，现收敛为唯一写入口。
    "disabled_natures": [],
    "by_nature": {
        "scalp": {"min_confidence": 65, "min_alignment": 4, "min_risk_reward": 1.3},
        "swing": {"min_confidence": 55, "min_alignment": 6, "min_risk_reward": 1.5},
        "trend_follow": {"min_score": 68, "min_alignment": 6, "min_risk_reward": 1.8},
    },
    "rebate_paper_multipliers": {},
}


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


# by_nature.<nature>.* 细粒度参数夹紧范围（与顶层 max_daily_trades/
# scalp_min_confidence/min_risk_reward 的 [min,max] 保护同款风格）。
# 此前顶层参数都有边界保护，但 by_nature 系列完全没有，可被离线进化
# (NSGA-II) 或人工 (OpenCode) 写入任意极端值（例如 min_score=95 导致该
# nature 事实上永久停摆，或 min_risk_reward=0.1 导致门禁形同虚设）。
_BY_NATURE_CLAMP: Dict[str, tuple] = {
    "min_risk_reward": (1.2, 3.0),
    "min_confidence": (50.0, 90.0),
    "min_score": (30.0, 90.0),
}


def _clamp_by_nature(value: Dict[str, Any]) -> Dict[str, Any]:
    """对 by_nature.<nature>.* 逐字段夹紧；越界值直接截断到边界，不 raise。

    保持"写入永不因越界而整体失败"的产品取向——宁可把离谱值截断到安全区间，
    也不能因为一次异常写入就让整份 patch 全部作废。
    """
    clamped: Dict[str, Any] = {}
    for nature, fields in (value or {}).items():
        if not isinstance(fields, dict):
            clamped[nature] = fields
            continue
        clamped_fields: Dict[str, Any] = {}
        for field_name, field_val in fields.items():
            bounds = _BY_NATURE_CLAMP.get(field_name)
            if bounds is not None and isinstance(field_val, (int, float)) and not isinstance(field_val, bool):
                clamped_fields[field_name] = _clamp(float(field_val), bounds[0], bounds[1])
            else:
                clamped_fields[field_name] = field_val
        clamped[nature] = clamped_fields
    return clamped


def _load_raw() -> Dict[str, Any]:
    data: Dict[str, Any] = copy.deepcopy(_DEFAULT_SCHEMA)
    if os.path.isfile(LEGACY_GATES_FILE):
        try:
            with open(LEGACY_GATES_FILE, "r", encoding="utf-8") as f:
                legacy = json.load(f) or {}
            for k in ("max_daily_trades", "scalp_min_confidence", "min_risk_reward", "disabled_natures"):
                if k in legacy:
                    if k in data and isinstance(data[k], dict) and "value" in data[k]:
                        data[k]["value"] = legacy[k]
                    else:
                        data[k] = legacy[k]
            if "by_nature" in legacy and isinstance(legacy["by_nature"], dict):
                data["by_nature"] = legacy["by_nature"]
        except Exception as err:
            logger.debug("[RuntimeTuning] legacy gates: %s", err)
    if os.path.isfile(TUNING_FILE):
        try:
            with open(TUNING_FILE, "r", encoding="utf-8") as f:
                override = json.load(f) or {}
            for k, v in override.items():
                if k in data and isinstance(data[k], dict) and isinstance(v, dict):
                    if "value" in data[k] and "value" in v:
                        merged = dict(data[k])
                        merged.update(v)
                        data[k] = merged
                    elif k in ("master_close_min_loss_pct_by_tier", "tier_max_hold_sec"):
                        merged = dict(data[k])
                        merged.update(v)
                        data[k] = merged
                    else:
                        data[k] = v
                else:
                    data[k] = v
        except Exception as err:
            logger.warning("[RuntimeTuning] 读取失败: %s", err)
    return data


def get_all_tuning(*, max_age: float = 60.0) -> Dict[str, Any]:
    now = time.time()
    if now - _cache["ts"] < max_age:
        return _cache["data"]
    data = _load_raw()
    _cache["ts"] = now
    _cache["data"] = data
    return data


def invalidate_cache() -> None:
    _cache["ts"] = 0.0


def get_tuning(key: str, default: Any = None) -> Any:
    data = get_all_tuning()
    entry = data.get(key)
    if entry is None:
        return default
    if isinstance(entry, dict) and "value" in entry:
        return entry["value"]
    return entry


def get_tuning_float(key: str, default: float) -> float:
    try:
        return float(get_tuning(key, default))
    except (TypeError, ValueError):
        return default


def get_tuning_int(key: str, default: int) -> int:
    try:
        return int(get_tuning(key, default))
    except (TypeError, ValueError):
        return default


def get_tier_value(section: str, tier: str, default: float) -> float:
    data = get_all_tuning()
    block = data.get(section) or {}
    if isinstance(block, dict):
        try:
            return float(block.get(tier, default))
        except (TypeError, ValueError):
            pass
    return default


def apply_patches(patches: Dict[str, Any], *, proposal_id: Optional[int] = None) -> Dict[str, Any]:
    """应用 patch 并写文件；返回实际写入的值。"""
    current = _load_raw()
    if proposal_id is not None:
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        snap_path = os.path.join(SNAPSHOT_DIR, f"{proposal_id}_before.json")
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)

    applied: Dict[str, Any] = {}
    for key, new_val in patches.items():
        if key == "tier_max_hold_sec":
            try:
                from backend.config.settings import HOLD_TIME_TUNING_LOCKED
                if HOLD_TIME_TUNING_LOCKED:
                    logger.warning(
                        "[RuntimeTuning] 拒绝 tier_max_hold_sec patch：HOLD_TIME_TUNING_LOCKED=true"
                    )
                    continue
            except Exception:
                pass
        if key not in _DEFAULT_SCHEMA and key not in current:
            logger.warning("[RuntimeTuning] 拒绝未知键: %s", key)
            continue
        schema = _DEFAULT_SCHEMA.get(key)
        if isinstance(schema, dict) and "value" in schema and not isinstance(new_val, dict):
            lo, hi = float(schema.get("min", 0)), float(schema.get("max", 999999))
            clamped = _clamp(float(new_val), lo, hi)
            current[key] = {**schema, "value": clamped}
            applied[key] = clamped
        elif key == "by_nature" and isinstance(new_val, dict):
            # 逐 nature 合并（而非整体覆盖，避免一次 patch 只带一个 nature 时
            # 把其它 nature 的既有配置连带清空），合并后统一套用夹紧边界。
            base = dict(current.get(key) or _DEFAULT_SCHEMA.get(key) or {})
            for nature, fields in new_val.items():
                if isinstance(fields, dict):
                    merged_nature = dict(base.get(nature) or {})
                    merged_nature.update(fields)
                    base[nature] = merged_nature
                else:
                    base[nature] = fields
            current[key] = _clamp_by_nature(base)
            applied[key] = current[key]
        elif key == "master_close_min_loss_pct_by_tier" and isinstance(new_val, dict):
            base = dict(current.get(key) or _DEFAULT_SCHEMA[key])
            base.update(new_val)
            current[key] = base
            applied[key] = base
        elif key == "tier_max_hold_sec" and isinstance(new_val, dict):
            base = dict(current.get(key) or _DEFAULT_SCHEMA[key])
            for tier, sec in new_val.items():
                try:
                    base[tier] = int(sec)
                except (TypeError, ValueError):
                    pass
            current[key] = base
            applied[key] = base
        else:
            current[key] = new_val
            applied[key] = new_val

    os.makedirs(os.path.dirname(TUNING_FILE) or "data", exist_ok=True)
    with open(TUNING_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    invalidate_cache()
    return applied


def save_overlay(proposal_id: int, patches: Dict[str, Any]) -> str:
    """训练期 proposal 级 overlay（Paper 验证隔离元数据）。"""
    os.makedirs(OVERLAY_DIR, exist_ok=True)
    path = os.path.join(OVERLAY_DIR, f"{proposal_id}.json")
    payload = {"proposal_id": proposal_id, "patches": patches, "merged": False}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def merge_overlay_to_global(proposal_id: int) -> Dict[str, Any]:
    """paper_validated 后确认 overlay 已生效（写 audit 标记 merged）。"""
    path = os.path.join(OVERLAY_DIR, f"{proposal_id}.json")
    if not os.path.isfile(path):
        return {"proposal_id": proposal_id, "merged": False, "reason": "no_overlay"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        data["merged"] = True
        data["merged_at"] = time.time()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"proposal_id": proposal_id, "merged": True, "patches": data.get("patches") or {}}
    except Exception as err:
        logger.warning("[RuntimeTuning] merge overlay %s: %s", proposal_id, err)
        return {"proposal_id": proposal_id, "merged": False, "error": str(err)}


def remove_overlay(proposal_id: int) -> bool:
    path = os.path.join(OVERLAY_DIR, f"{proposal_id}.json")
    if os.path.isfile(path):
        try:
            os.remove(path)
            return True
        except Exception:
            return False
    return False


def list_overlays() -> Dict[str, Any]:
    if not os.path.isdir(OVERLAY_DIR):
        return {}
    out: Dict[str, Any] = {}
    for fname in os.listdir(OVERLAY_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(OVERLAY_DIR, fname), "r", encoding="utf-8") as f:
                out[fname.replace(".json", "")] = json.load(f)
        except Exception:
            pass
    return out


def rollback_snapshot(proposal_id: int) -> bool:
    snap_path = os.path.join(SNAPSHOT_DIR, f"{proposal_id}_before.json")
    if not os.path.isfile(snap_path):
        return False
    try:
        with open(snap_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        with open(TUNING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        invalidate_cache()
        remove_overlay(proposal_id)
        return True
    except Exception as err:
        logger.error("[RuntimeTuning] rollback 失败: %s", err)
        return False


def runtime_gates_compat() -> Dict[str, Any]:
    """供 unified_gate 向后兼容。"""
    data = get_all_tuning()
    out: Dict[str, Any] = {}
    for k in ("max_daily_trades", "scalp_min_confidence", "min_risk_reward",
              "daily_cap_base"):
        v = get_tuning(k)
        if v is not None:
            out[k] = v
    if "disabled_natures" in data:
        out["disabled_natures"] = data["disabled_natures"]
    if "by_nature" in data and isinstance(data["by_nature"], dict):
        out["by_nature"] = data["by_nature"]
    return out
