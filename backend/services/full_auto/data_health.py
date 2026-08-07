"""数据健康检查 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Set

logger = logging.getLogger(__name__)


@dataclass
class DataHealthHost:
    symbol_frozen_set: Dict[str, set] = field(default_factory=dict)
    health_status: Dict[str, Any] = field(default_factory=dict)

    freeze_symbol_strategies: Callable = field(repr=False, default=lambda *a, **k: None)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)


def build_data_health_host(svc) -> DataHealthHost:
    return DataHealthHost(
        symbol_frozen_set=getattr(svc, "_symbol_frozen_set", None) or {},
        health_status=getattr(svc, "_health_status", None) or {},
        freeze_symbol_strategies=svc._freeze_symbol_strategies,
        append_event=svc._append_event,
    )


def check_data_health(
    session, market_summary: Dict[str, Any],
    symbols: List[str], host: DataHealthHost, db=None,
):
    issues = []
    critical_count = 0
    warning_count = 0
    stale_symbols: Dict[str, float] = {}
    recovered_symbols: Set[str] = set()
    sid = session.session_id
    already_frozen = host.symbol_frozen_set.get(sid, set())

    hub_prices: Dict[str, float] = {}
    try:
        from backend.services.unified_data_pool import unified_data_pool
        _hub_result = unified_data_pool.get_market(symbols)
        for sym, data in (_hub_result or {}).items():
            p = data.get("price", 0) if isinstance(data, dict) else 0
            if p and p > 0:
                hub_prices[sym.upper()] = float(p)
    except Exception:
        pass

    for sym in symbols:
        info = market_summary.get(sym, {})
        price = info.get("current_price", 0)
        if (not price or price <= 0) and sym.upper() in hub_prices:
            info["current_price"] = hub_prices[sym.upper()]
            info["price_source"] = "data_hub"
            price = hub_prices[sym.upper()]

        if not price or price <= 0:
            try:
                # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连
                # HL allMids 兜底，统一从数据中心 DB 读价。
                from backend.services.market_data import _dc_only_enabled
                if _dc_only_enabled():
                    from backend.services.data_center import data_center
                    _p = data_center.get_price(sym)
                    if _p and float(_p) > 0:
                        info["current_price"] = float(_p)
                        price = float(_p)
                        host.health_status.pop(f"price_miss_{sym.upper()}", None)
                else:
                    import requests
                    _resp = requests.post("https://api.hyperliquid.xyz/info",
                        json={"type":"allMids"}, timeout=10)
                    if _resp.status_code == 200:
                        _p = _resp.json().get(sym.upper(), 0)
                        if _p and float(_p) > 0:
                            info["current_price"] = float(_p)
                            price = float(_p)
                            host.health_status.pop(f"price_miss_{sym.upper()}", None)
            except Exception:
                pass

        if not price or price <= 0:
            key = f"price_miss_{sym.upper()}"
            miss = host.health_status.get(key, 0) + 1
            host.health_status[key] = miss
            if miss >= 30:
                issues.append(f"🚨 {sym}: 连续{miss}次价格缺失")
                critical_count += 1
            continue
        else:
            # 价格恢复 → 重置计数器
            host.health_status.pop(f"price_miss_{sym.upper()}", None)

        # 数据源不可靠
        if not info.get("data_reliable", True):
            ds = info.get("data_source", "unknown")
            issues.append(f"⚠️ {sym}: 数据源不可靠({ds})，分析结果可能失真")
            warning_count += 1

        # 价格可能过期
        if info.get("price_stale_warning", False):
            issues.append(f"⚠️ {sym}: 价格数据可能过期(stale)")
            warning_count += 1

        # K线数据不足
        kline_count = info.get("kline_count", 0)
        if 0 < kline_count < 20:
            issues.append(f"⚠️ {sym}: K线数据不足({kline_count}根)，技术分析可靠度低")
            warning_count += 1

        # K线过于老旧 → D7修复: 自动冻结品种策略
        kline_age = info.get("kline_age_hours", 0)
        if kline_age > 2:
            issues.append(f"⚠️ {sym}: K线数据老旧({kline_age:.1f}小时前)")
            warning_count += 1
            # 自动冻结该品种（如果尚未冻结且有 db session）
            if sym.upper() not in already_frozen and db is not None:
                stale_symbols[sym] = kline_age
        elif sym.upper() in already_frozen:
            # 数据已恢复：标记为需要解冻
            recovered_symbols.add(sym)

    # ── D7修复: 批量冻结/解冻品种 ──
    if db is not None:
        for sym, age in stale_symbols.items():
            host.freeze_symbol_strategies(db, session, sym,
                f"K线数据老旧({age:.1f}小时)，自动冻结")
        if recovered_symbols:
            for sym in recovered_symbols:
                # 从冻结集合中移除已恢复的品种
                frozen = host.symbol_frozen_set.get(sid, set())
                frozen.discard(sym.upper())
            logger.info(f"[DataHealth] 解冻{len(recovered_symbols)}个已恢复品种: {recovered_symbols}")

    # 全部 symbol 都失败 → 最高级别告警
    total_symbols = len(symbols)
    failed_symbols = sum(1 for s in symbols if "error" in market_summary.get(s, {}))
    if failed_symbols > 0 and failed_symbols == total_symbols:
        host.append_event(session, "system_alert",
            f"🚨🚨 严重：所有交易对({total_symbols}个)数据获取失败！"
            f"系统无法正常分析，请检查网络和 API 连接",
            severity="critical")
        logger.critical(
            f"[FullAuto] 全部{total_symbols}个交易对数据断流！")
    elif critical_count > 0:
        host.append_event(session, "system_alert",
            f"🚨 数据告警：{critical_count}个交易对存在严重数据问题 | "
            + " | ".join(issues[:3]),
            severity="critical")
        logger.error(f"[FullAuto] 数据健康检查: {critical_count} critical issues")
    elif warning_count > 0:
        host.append_event(session, "data_warning",
            f"⚠️ 数据质量提醒：{warning_count}个问题 | "
            + " | ".join(issues[:3]),
            severity="warning")
        logger.warning(f"[FullAuto] 数据健康检查: {warning_count} warnings")

    # 更新内部健康状态
    host.health_status["data_flow_ok"] = (critical_count == 0)
    host.health_status["data_issues"] = issues[-10:]

    # ══════════════════════════════════════════════════════════════
    #  AI 连接告警 + 自动重试追踪
    # ══════════════════════════════════════════════════════════════
