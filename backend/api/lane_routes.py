# -*- coding: utf-8 -*-
"""[F56] 车道 API —— `/api/trading/lanes*`

设计依据：《套利中心重构设计_含前端_V2》§6（三命名空间收敛为 `/api/trading/*`）。
本文件是收敛的第一块：车道注册表读写 + 晋升判定查询。
旧命名空间（/api/arbitrage、/api/rebate、/api/arbitrage-paper）保持不变，
前端可先行切到本路径。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading", tags=["trading-lanes"])


class ModeBody(BaseModel):
    mode: str = Field(..., description="paper | live | disabled")


class StatusBody(BaseModel):
    status: str = Field(..., description="active | paused | stopped")


class EdgeBody(BaseModel):
    """扣费后的边际指标（缺字段视为未验证，fail-closed）。"""

    gross_bp: Optional[float] = None
    cost_bp: Optional[float] = None
    net_bp: Optional[float] = None
    t: Optional[float] = None
    n: Optional[int] = None
    folds: Optional[list] = None          # [{net_bp, t, n}]
    max_dd_pct: Optional[float] = None
    fill_rate_ratio: Optional[float] = None
    as_of: Optional[str] = None


def _enrich_lanes(items: list) -> list:
    """给车道补上「今日/近 7 天盈亏 + 当前库存敞口」，避免前端拿不到就要显示 0。

    数据源全部是六维账本（`lane_ledger`），因此**新车道只要写账本就会自动出现**。
    任何一项取不到时给 None（前端显示「无数据」），不返回 0 冒充「没有盈亏」。
    """
    from backend.services import lane_ledger

    # [2026-09-14 统计时代隔离] 每个车道各自的「统计起点」：meta.stats_since。
    # 配置/账户重构前的旧账本行不再计入该车道的今日/7 天口径（历史仍在库中）。
    _since_of: Dict[str, Optional[str]] = {}
    for _ln in items:
        _s = ((_ln.get("meta") or {}) or {}).get("stats_since")
        if _s:
            _since_of[str(_ln.get("lane_id"))] = str(_s)

    def _attr_map(days: float) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        try:
            for r in (lane_ledger.attribution(days=days).get("by_lane") or []):
                lid = str(r.get("lane_id"))
                _s = _since_of.get(lid)
                if _s:
                    # 有统计起点的车道单独查询（按起点裁剪）
                    sub = lane_ledger.attribution(days=days, lane_id=lid, since=_s)
                    rows = sub.get("by_lane") or []
                    if rows:
                        out[lid] = rows[0]
                    continue
                out[lid] = r
        except Exception as e:  # pragma: no cover
            logger.warning("[LaneRoutes] 归因读取失败: %s", e)
        return out

    try:
        attr_1d = _attr_map(1.0)
        attr_7d = _attr_map(7.0)
    except Exception as logger_err:  # pragma: no cover - 账本不可用时降级
        logger.warning("[LaneRoutes] 归因读取失败: %s", logger_err)
        attr_1d, attr_7d = {}, {}
    try:
        pos = lane_ledger.open_positions(days=30.0)
    except Exception as e:  # pragma: no cover
        logger.warning("[LaneRoutes] 持仓重建失败: %s", e)
        pos = []

    inv: Dict[str, Dict[str, float]] = {}
    for p in pos:
        if abs(p["qty"]) < 1e-12:
            continue
        d = inv.setdefault(p["lane_id"], {"inventory_usd": 0.0, "net_usd": 0.0,
                                          "unrealized_usd": 0.0, "open_symbols": 0})
        d["inventory_usd"] += abs(p["notional_usd"])
        d["net_usd"] += p["notional_usd"] * (1 if p["qty"] > 0 else -1)
        d["unrealized_usd"] += p["unrealized_usd"]
        d["open_symbols"] += 1

    for ln in items:
        lid = ln.get("lane_id")
        a1, a7 = attr_1d.get(lid), attr_7d.get(lid)
        ln["pnl_today_usd"] = None if a1 is None else round(float(a1.get("net_usd") or 0.0), 4)
        ln["fills_today"] = None if a1 is None else int(a1.get("n") or 0)
        ln["pnl_7d_usd"] = None if a7 is None else round(float(a7.get("net_usd") or 0.0), 4)
        ln["fills_7d"] = None if a7 is None else int(a7.get("n") or 0)
        ln["notional_7d"] = None if a7 is None else round(float(a7.get("notional") or 0.0), 2)
        invd = inv.get(lid) or {}
        ln["inventory_usd"] = round(invd.get("inventory_usd", 0.0), 2)
        ln["net_exposure_usd"] = round(invd.get("net_usd", 0.0), 2)
        ln["unrealized_usd"] = round(invd.get("unrealized_usd", 0.0), 4)
        ln["open_symbols"] = int(invd.get("open_symbols", 0))
        # 车道级数据年龄：健康检查时间优先，其次账本最后成交时间
        health = ln.get("health") or {}
        ln["data_age_sec"] = health.get("data_age_sec")
        ln["last_activity_at"] = health.get("updated_at") or ln.get("updated_at")
    return items


@router.get("/lanes")
def list_lanes() -> Dict[str, Any]:
    """全部车道（含 edge/risk/health/promotion/今日与近 7 天盈亏/库存敞口）。"""
    from backend.services import lane_registry as reg

    items = _enrich_lanes(reg.list_lanes())
    return {
        "items": items,
        "count": len(items),
        "criteria": reg.PROMOTION_CRITERIA,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/lanes/{lane_id}")
def get_lane(lane_id: str) -> Dict[str, Any]:
    from backend.services import lane_registry as reg

    lane = reg.get_lane(lane_id)
    if not lane:
        raise HTTPException(status_code=404, detail=f"车道不存在: {lane_id}")
    return _enrich_lanes([lane])[0]


@router.post("/lanes/seed")
def seed_lanes() -> Dict[str, Any]:
    """把设计文档中的初始车道写入（幂等）。"""
    from backend.services import lane_registry as reg

    n = reg.seed_defaults()
    return {"seeded": n, "items": reg.list_lanes()}


@router.post("/lanes/{lane_id}/mode")
def set_mode(lane_id: str, body: ModeBody) -> Dict[str, Any]:
    """切换车道模式（paper ⇄ live / disabled）。"""
    from backend.services import lane_registry as reg

    try:
        ok = reg.set_mode(lane_id, body.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"车道不存在: {lane_id}")
    return reg.get_lane(lane_id) or {}


@router.post("/lanes/{lane_id}/status")
def set_status(lane_id: str, body: StatusBody) -> Dict[str, Any]:
    """暂停/恢复/停止车道。"""
    from backend.services import lane_registry as reg

    try:
        ok = reg.set_status(lane_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"车道不存在: {lane_id}")
    return reg.get_lane(lane_id) or {}


@router.post("/lanes/{lane_id}/edge")
def update_edge(lane_id: str, body: EdgeBody) -> Dict[str, Any]:
    """车道实现上报扣费后边际指标（同时重算晋升进度）。"""
    from backend.services import lane_registry as reg

    ok = reg.update_edge(lane_id, body.model_dump(exclude_none=True))
    if not ok:
        raise HTTPException(status_code=404, detail=f"车道不存在: {lane_id}")
    return reg.get_lane(lane_id) or {}


@router.get("/lanes/{lane_id}/promotion")
def get_promotion(lane_id: str) -> Dict[str, Any]:
    """晋升判定矩阵（前端「晋升进度」数据源）。"""
    from backend.services import lane_registry as reg

    lane = reg.get_lane(lane_id)
    if not lane:
        raise HTTPException(status_code=404, detail=f"车道不存在: {lane_id}")
    return {
        "lane_id": lane_id,
        "mode": lane["mode"],
        "promotion": lane.get("promotion") or reg.evaluate_promotion(lane.get("edge")),
    }


# ═══════════════════════════════════════════════════════════════════
# [F57] 六维归因账本
# ═══════════════════════════════════════════════════════════════════

class FillBody(BaseModel):
    """车道实现上报一笔成交，账本自动算六维。"""

    lane_id: str
    symbol: str
    side: str = Field(..., description="buy/long 或 sell/short")
    qty: float
    fill_px: float
    mid_px: float
    fee_rate: float = 0.0
    order_px: Optional[float] = None
    funding_bp: float = 0.0
    price_bp: float = 0.0
    points_usd: float = 0.0
    position_id: Optional[str] = None


@router.get("/portfolio/attribution")
def portfolio_attribution(days: float = 7.0, lane_id: Optional[str] = None) -> Dict[str, Any]:
    """近 N 天六维归因（前端「归因堆叠条」数据源）。

    [2026-09-14 统计时代隔离] 按车道 `stats_since` 裁剪——重构前的旧账本行
    不再出现在归因条里（否则总览会显示历史污染数字）。
    """
    from backend.services import lane_ledger as ledger
    from backend.services import lane_registry as reg

    res = ledger.attribution(days=days, lane_id=lane_id,
                             since=reg.stats_since(lane_id))
    res["as_of"] = datetime.now(timezone.utc).isoformat()
    return res


@router.get("/portfolio/series")
def portfolio_series(days: float = 7.0, lane_id: Optional[str] = None) -> Dict[str, Any]:
    """近 N 天逐日净收益（前端迷你曲线数据源；同样按 stats_since 裁剪）。"""
    from backend.services import lane_ledger as ledger
    from backend.services import lane_registry as reg

    _since = reg.stats_since(lane_id)
    return {"days": days, "lane_id": lane_id,
            "series": ledger.daily_series(lane_id, days, since=_since),
            "as_of": datetime.now(timezone.utc).isoformat()}


@router.post("/ledger/fill")
def record_fill(body: FillBody) -> Dict[str, Any]:
    """记录一笔成交到六维账本（车道实现调用）。"""
    from backend.services import lane_ledger as ledger

    dims = ledger.compute_fill_dimensions(
        side=body.side, fill_px=body.fill_px, mid_px=body.mid_px,
        fee_rate=body.fee_rate, order_px=body.order_px,
        funding_bp=body.funding_bp, price_bp=body.price_bp,
    )
    ok = ledger.record_fill(
        lane_id=body.lane_id, symbol=body.symbol, side=body.side, qty=body.qty,
        fill_px=body.fill_px, mid_px=body.mid_px, fee_rate=body.fee_rate,
        order_px=body.order_px, funding_bp=body.funding_bp, price_bp=body.price_bp,
        points_usd=body.points_usd, position_id=body.position_id,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="账本写入失败")
    return {"ok": True, "dimensions": dims, "net_bp": ledger.net_bp(**dims)}


# ═══════════════════════════════════════════════════════════════════
# [F60] L1 做市车道影子期（模拟账户直跑）
# ═══════════════════════════════════════════════════════════════════

_RUNNERS: Dict[str, Any] = {}
_RUNNER_LOCK = None


def _runner_for(lane_id: str):
    """按车道取（或建）影子期驱动器；与调度器共用同一实例。"""
    from backend.services import lane_registry as reg
    from backend.services.market_maker.runner import get_runner

    lane = reg.get_lane(lane_id)
    if not lane:
        raise HTTPException(status_code=404, detail=f"车道不存在: {lane_id}")
    if (lane.get("mode") or "paper") == "disabled":
        raise HTTPException(status_code=409, detail=f"车道已停用: {lane_id}")
    r = get_runner(lane_id)
    if r is None:
        raise HTTPException(status_code=500, detail=f"影子期驱动器初始化失败: {lane_id}")
    return r


@router.get("/lanes/{lane_id}/shadow")
def shadow_status(lane_id: str) -> Dict[str, Any]:
    """影子期实时状态：库存、挂单、本进程 tick/fill 计数。"""
    return _runner_for(lane_id).status()


@router.post("/lanes/{lane_id}/shadow/tick")
def shadow_tick(lane_id: str) -> Dict[str, Any]:
    """手动推进一个影子期 tick（调试/补跑用；线上由调度器驱动）。"""
    return _runner_for(lane_id).tick()


@router.get("/lanes/{lane_id}/shadow/report")
def shadow_report(lane_id: str, days: float = 30.0) -> Dict[str, Any]:
    """影子期达标报告：六维归因 + 逐日序列 + 晋级判定。"""
    rep = _runner_for(lane_id).report(days=int(max(1.0, days)))
    rep["as_of"] = datetime.now(timezone.utc).isoformat()
    return rep

