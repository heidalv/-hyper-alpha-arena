"""key_utils — 因子键名归一化（P0-2 修复，2026-08-14）。

统一三处命名空间（SSOT）：
- `factor_engine.FACTORS` 运行时键：`evo_{factor_id}`（进化闭环）、`ai_*`（公式因子）、
  裸名（Registry 因子）。
- `get_scalp_factor_allowlist()` 精选白名单：一律存**裸 factor_id**（DB/商店原始 id）。
- `custom_factor_store` 目录：`t{tenant_id}:factor_id` 键，factor_id 裸。

本模块只提供「比较前归一化」，不改变任何存储格式，避免大规模迁移。
"""
from __future__ import annotations

# 引擎运行时键前缀：evo_（进化闭环 AST）、ai_（AI 公式因子）
_ENGINE_PREFIXES = ("evo_", "ai_")


def normalize_engine_key(name: str) -> str:
    """把 FACTORS 运行时键归一化为裸 factor_id。

    [2026-08-15 扩展] 除 evo_ 前缀外，再处理两类键形：
    - `ai_*`：AI 公式因子运行时键 → 剥前缀为裸 id；
    - `t{tenant_id}:factor_id`：custom_factor_store 键 → 取冒号后裸 id。
    裸名（Registry 因子）原样返回；幂等。
    """
    s = str(name or "").strip()
    for prefix in _ENGINE_PREFIXES:
        if s.startswith(prefix):
            return s[len(prefix):]
    # t{tenant_id}:factor_id → factor_id
    if s.startswith("t") and ":" in s:
        head, _, tail = s.partition(":")
        if head[1:].isdigit() and tail:
            return tail
    return s


def is_engine_key_normalized(name: str) -> bool:
    """键是否已是裸 id（无需再归一）。"""
    return normalize_engine_key(name) == str(name or "")


def allowlist_hits(engine_keys, allowlist) -> set:
    """计算 engine 键与精选白名单（裸 id 集合）的命中集合。

    返回命中白名单的裸 id 集合（用于启动自检/运维台）。
    """
    _allow = {str(x) for x in (allowlist or set())}
    if not _allow:
        return set()
    return {normalize_engine_key(k) for k in (engine_keys or [])} & _allow
