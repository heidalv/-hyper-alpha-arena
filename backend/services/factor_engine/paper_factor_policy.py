"""影子（role=paper）因子的在线权重策略 —— 实盘/模拟分口径的唯一入口。

[2026-09-03 审查修正 A] 背景
====================================
held-out 判决**没过**但训练段 A/B 级的因子，08-22（M0-F1）起以 ``extra.role=paper``
晋升进 active（"先让因子进得来、用实盘数据学"）；进化仓 ``factor_active_set``
``state=PAPER`` 的 AST 因子同理。它们在线权重被封顶 ``PAPER_FACTOR_WEIGHT_CAP``
（默认 0.5），但**active 集合实盘与模拟共用**，即"没过判决的因子以半权重参与
实盘决策"。这与 09-02 路线图 3.1（因子池收敛到有统计证据的因子）方向相反。

而且封顶只在 ``factor_evaluation_pipeline._compute_weights`` 一处实现；短线循环
**优先**消费的 V3 路径（``v3_factor_pipeline`` → ``FactorSignalGenerator``）只传
IC 运行时权重，从未封顶——审查前这是个漏洞。

本模块把"谁是影子因子"与"在某个交易模式下该给多少权重"收成一个函数，
三条消费路径（pipeline / V3 / 中线因子路由）统一调用：

- ``trading_mode == live`` 且 ``PAPER_FACTOR_LIVE_EXCLUDE=true``（默认）→ 影子因子
  权重 **0**（不参与实盘融合；聚合器对 w<=0 跳过）。
- 其余情况（paper 会话、或显式关闭排除）→ 保持原口径：``min(w, PAPER_FACTOR_WEIGHT_CAP)``
  （cap<=0 表示不限制，与 settings 注释一致）。

回滚：``PAPER_FACTOR_LIVE_EXCLUDE=false`` 恢复 08-22 行为（实盘也给半权重）。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, Iterable, Optional, Set

logger = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "yes", "on")

# 影子因子 id 集合缓存（两个来源合并：factor_active_set.state=PAPER ∪
# custom_factor_store.extra.role=paper）。custom_factor_store 扫一遍要读目录文件，
# 短线每轮每币都会问一次，5 分钟 TTL 足够（晋升/退役本身是小时级事件）。
_CACHE: dict = {"ts": 0.0, "ids": None}
_CACHE_TTL_SEC = 300.0
_last_log: dict = {"ts": 0.0}


def _setting(name: str, default):
    try:
        from backend.config import settings as _s
        return getattr(_s, name, default)
    except Exception:  # pragma: no cover
        return default


def is_live_mode(trading_mode: Optional[str]) -> bool:
    return str(trading_mode or "").strip().lower() == "live"


def live_exclude_enabled() -> bool:
    """实盘是否排除影子因子（默认开）。settings 优先，其次环境变量。"""
    v = _setting("PAPER_FACTOR_LIVE_EXCLUDE", None)
    if v is None:
        raw = (os.getenv("PAPER_FACTOR_LIVE_EXCLUDE", "true") or "true").strip().lower()
        return raw in _TRUTHY
    return bool(v)


def paper_weight_cap() -> float:
    """paper 会话下影子因子权重上限；<=0 表示不限制（沿用 settings 语义）。"""
    try:
        return float(_setting("PAPER_FACTOR_WEIGHT_CAP", 0.5) or 0.0)
    except Exception:  # pragma: no cover
        return 0.5


def paper_factor_ids(refresh: bool = False) -> Set[str]:
    """两个来源合并后的影子因子 id 集合（引擎键名规范化后）。"""
    now = time.time()
    if (not refresh and _CACHE["ids"] is not None
            and now - float(_CACHE["ts"]) < _CACHE_TTL_SEC):
        return set(_CACHE["ids"])
    ids: Set[str] = set()
    try:
        from backend.services.factor_engine.key_utils import normalize_engine_key as _nk
    except Exception:  # pragma: no cover
        def _nk(x):  # type: ignore
            return str(x)
    # 来源①：进化仓 factor_active_set.state=PAPER
    try:
        from backend.services.scalp.scalp_factor_exclude import get_paper_factor_ids
        for fid in get_paper_factor_ids() or ():
            ids.add(_nk(str(fid)))
            # 桥接进短线/中线集合时前缀为 evo_（见 *_active_factor_set._tradable_ast_bridge）
            ids.add(_nk(f"evo_{fid}"))
    except Exception as e:  # pragma: no cover
        logger.debug("[PaperPolicy] factor_active_set PAPER 集合读取跳过: %s", e)
    # 来源②：custom_factor_store extra.role=paper（held-out 未过 / 条件口径晋升）
    try:
        from backend.services.factor_engine.custom_factor_store import custom_factor_store
        from backend.services.coin_select_platform_service import resolve_admin_tenant_id
        for rec in custom_factor_store.list_active(tenant_id=resolve_admin_tenant_id()) or []:
            if str((rec.get("extra") or {}).get("role") or "") == "paper":
                fid = str(rec.get("factor_id") or "")
                if fid:
                    ids.add(_nk(fid))
    except Exception as e:  # pragma: no cover
        logger.debug("[PaperPolicy] custom_factor_store role=paper 集合读取跳过: %s", e)
    _CACHE["ts"] = now
    _CACHE["ids"] = set(ids)
    return set(ids)


def is_paper_record(rec: dict) -> bool:
    """active-set 记录是否影子因子（按 extra.role 判定，不查库）。"""
    try:
        return str((rec.get("extra") or {}).get("role") or "") == "paper"
    except Exception:
        return False


def paper_factor_excluded(trading_mode: Optional[str]) -> bool:
    """当前模式下影子因子是否应完全排除（权重 0）。"""
    return is_live_mode(trading_mode) and live_exclude_enabled()


def effective_paper_weight(weight: Optional[float], trading_mode: Optional[str]) -> float:
    """给一个影子因子的原始权重，返回该交易模式下的生效权重。"""
    w = 1.0 if weight is None else float(weight)
    if paper_factor_excluded(trading_mode):
        return 0.0
    cap = paper_weight_cap()
    return min(w, cap) if cap > 0 else w


def apply_paper_policy(
    weights: Dict[str, float],
    trading_mode: Optional[str],
    *,
    paper_ids: Optional[Iterable[str]] = None,
    where: str = "",
) -> Dict[str, float]:
    """对一张 {factor_name: weight} 权重表就地施加影子因子策略并返回。

    ``paper_ids`` 不传则用缓存集合；传入时按引擎键名规范化后比对。
    """
    if not weights:
        return weights
    try:
        from backend.services.factor_engine.key_utils import normalize_engine_key as _nk
    except Exception:  # pragma: no cover
        def _nk(x):  # type: ignore
            return str(x)
    # 两个来源都归一化到裸 id 再比对（evo_/ai_/t{tid}: 前缀统一剥掉），
    # 避免"集合里存的是 evo_x、权重表键是 x"这类前缀错位漏封。
    src = paper_ids if paper_ids is not None else paper_factor_ids()
    ids = set(_nk(str(x)) for x in src)
    if not ids:
        return weights
    excluded = paper_factor_excluded(trading_mode)
    cap = paper_weight_cap()
    touched = 0
    for name in list(weights.keys()):
        if _nk(name) not in ids:
            continue
        w = weights.get(name)
        if excluded:
            weights[name] = 0.0
        elif cap > 0:
            weights[name] = min(1.0 if w is None else float(w), cap)
        touched += 1
    if touched and excluded:
        # 每 10 分钟最多记一条 INFO，避免每轮每币刷屏；但要让运维看得见"实盘少了哪些因子"
        now = time.time()
        if now - float(_last_log["ts"]) > 600:
            _last_log["ts"] = now
            logger.info(
                "[PaperPolicy] 实盘融合排除 %d 个影子因子(role=paper/PAPER) @%s；"
                "PAPER_FACTOR_LIVE_EXCLUDE=false 可恢复半权重",
                touched, where or "-",
            )
    return weights
