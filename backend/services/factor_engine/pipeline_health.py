"""pipeline_health — 因子管线健康自检（阶段 0 基线仪表，2026-08-14）。

提供两类检查：
1. 精选白名单与 FACTORS 键的命中率（P0-2 修复验证器）：
   allowlist 非空但命中 0 个键 = 精选路径恒空集（修复前状态），启动期告警。
2. DSR 闸门配置快照（P0-1）：min_symbols/required/max_pbo，供运维台对照。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)


def collect_factor_pipeline_health() -> Dict[str, Any]:
    """只读采集：engine 因子数、白名单命中数、DSR 配置。绝不触发重活。"""
    out: Dict[str, Any] = {
        "engine_factor_count": 0,
        "allowlist_enabled": False,
        "allowlist_size": 0,
        "allowlist_hits": 0,
        "allowlist_hit_keys": [],
        "dsr": {},
    }
    try:
        from backend.services.factor_engine.base_factors import factor_engine
        out["engine_factor_count"] = len(factor_engine.FACTORS)
    except Exception as e:
        logger.debug("[PipelineHealth] factor_engine 不可用: %s", e)
        return out

    try:
        from backend.services.factor_engine.key_utils import allowlist_hits
        from backend.services.scalp.scalp_factor_exclude import get_scalp_factor_allowlist
        allowlist: Optional[Set[str]] = get_scalp_factor_allowlist()
        if allowlist is not None:
            out["allowlist_enabled"] = True
            out["allowlist_size"] = len(allowlist)
            hits = allowlist_hits(factor_engine.FACTORS.keys(), allowlist)
            out["allowlist_hits"] = len(hits)
            out["allowlist_hit_keys"] = sorted(hits)[:50]
    except Exception as e:
        logger.debug("[PipelineHealth] allowlist 采集失败: %s", e)

    try:
        from backend.config import settings as _s
        out["dsr"] = {
            "required": bool(getattr(_s, "FACTOR_SCORER_DSR_REQUIRED", True)),
            "min_symbols": int(getattr(_s, "FACTOR_SCORER_DSR_MIN_SYMBOLS", 4)),
            "max_pbo": float(getattr(_s, "FACTOR_SCORER_MAX_PBO", 0.5)),
        }
    except Exception:
        pass
    return out


def check_startup(raise_on_fatal: bool = False) -> Dict[str, Any]:
    """启动期自检：返回健康快照；白名单 0 命中时 ERROR 告警（可升级为异常）。"""
    health = collect_factor_pipeline_health()
    if health.get("allowlist_enabled"):
        if health.get("allowlist_size", 0) == 0:
            # 空白名单 = 精选路径全拦（宁缺毋滥）。若 DB 里实际有 PAPER/ACTIVE 因子，
            # 说明白名单构造链路断了（例如 DB 会话错库），必须显式告警。
            _db_tradable = _count_db_tradable_factors()
            msg = (
                "[FactorPipeline] ⚠️ 精选白名单为空：实盘精选路径将拦掉全部因子。"
                "factor_active_set 可交易行=%d（>0 说明白名单构造链路异常，见 P0-2 修复）。"
            ) % _db_tradable
            if _db_tradable > 0:
                logger.error(msg)
            else:
                logger.warning(msg)
            if raise_on_fatal and _db_tradable > 0:
                raise RuntimeError(msg)
        elif health.get("allowlist_hits", 0) == 0:
            msg = (
                "[FactorPipeline] ⚠️ 精选白名单已启用（%d 个 id）但命中 FACTORS 键数为 0："
                "实盘精选路径恒为空集（可能为 evo_ 前缀/类别映射错位，见 P0-2）。"
                "请检查 key 归一化与白名单构造。"
            ) % health["allowlist_size"]
            logger.error(msg)
            if raise_on_fatal:
                raise RuntimeError(msg)
        else:
            _hits = int(health.get("allowlist_hits", 0))
            if _hits < 5:
                # [2026-08-15 停摆防护] 因子池过小会导致短线全线低分 hold
                #（8-13 实况：ACTIVE 被清洗后仅剩 3 个 PAPER → 分数 0-25 → 零开仓）。
                # 升级 WARNING + 节流通知，让「精选在后台跑不出来」的停摆状态可见。
                logger.warning(
                    "[FactorPipeline] ⚠️ 精选白名单命中仅 %d/%d（engine 因子 %d）："
                    "因子池过小，短线策略可能长期低分 hold。请关注进化晋升链路。",
                    _hits, health["allowlist_size"], health.get("engine_factor_count", 0),
                )
                _notify_degraded_pool(health)
            else:
                logger.info(
                    "[FactorPipeline] 精选白名单命中 %d/%d（engine 因子 %d）",
                    health["allowlist_hits"], health["allowlist_size"],
                    health.get("engine_factor_count", 0),
                )
    return health


# ── 停摆通知节流（进程内，最小间隔 6 小时，重启后重新计） ──
_NOTIFY_STATE = {"last_ts": 0.0}


def _notify_degraded_pool(health: Dict[str, Any]) -> None:
    """因子池退化（<5 命中）时节流通知飞书/钉钉（每 6h 最多一次）。"""
    import time as _time
    _now = _time.time()
    if _now - _NOTIFY_STATE["last_ts"] < 6 * 3600:
        return
    _NOTIFY_STATE["last_ts"] = _now
    try:
        from backend.services.openclaw_notify import get_notifier, NotifyLevel
        notifier = get_notifier()
        notifier.send_sync(
            text=(
                f"因子精选池退化：命中仅 {health.get('allowlist_hits')}/"
                f"{health.get('allowlist_size')}（engine 因子 "
                f"{health.get('engine_factor_count')}）。短线策略可能长期低分 hold，"
                f"请检查因子进化晋升链路（factor_active_set 状态分布）。"
            ),
            title="⚠️ 因子池退化告警",
            level=NotifyLevel.WARNING,
            event_type="system",
        )
    except Exception as e:
        logger.debug("[PipelineHealth] 退化通知发送失败（忽略）: %s", e)


def _count_db_tradable_factors() -> int:
    """factor_active_set 中 state∈{ACTIVE,PAPER,SMALL_LIVE} 的行数（只读）。"""
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.database.models import FactorActiveSet
        db = AnalyticsSessionLocal()
        try:
            return int(
                db.query(FactorActiveSet.factor_id)
                .filter(FactorActiveSet.state.in_(["ACTIVE", "PAPER", "SMALL_LIVE"]))
                .count()
            )
        finally:
            db.close()
    except Exception as e:
        logger.debug("[PipelineHealth] DB tradable 计数失败: %s", e)
        return -1
