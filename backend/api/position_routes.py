"""仓位视图 API — /api/positions/*

阶段 3 本地仓位协调器对外接口:
- GET  /api/positions/net/{symbol}     某币种净仓位视图（所有 tier 合并，来自 LivePositionManager）
- GET  /api/positions/sub/{symbol}     某币种子仓位分解（scalp / trend_follow 各自，来自 LiveSubPosition）
- POST /api/positions/reconcile        手动触发对账（live 子仓账本 vs 交易所实际仓位）

账户解析: 当前系统以单用户/默认账户为主，account_id 通过 query/body 显式传入；
未传时回退到第一个 active 账户（与 order_routes / dashboard_routes 一致）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal
from backend.database.models import Account, LiveSubPosition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/positions", tags=["positions"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _resolve_account_id(db: Session, account_id: Optional[int]) -> int:
    """解析 account_id：显式传入优先，否则回退到第一个 active 账户。"""
    if account_id:
        return int(account_id)
    acct = (
        db.query(Account)
        .filter(Account.is_active == "true")
        .order_by(Account.id.asc())
        .first()
    )
    if not acct:
        raise HTTPException(status_code=404, detail="No active trading account found")
    return int(acct.id)


@router.get("/net/{symbol}")
async def get_net_position(
    symbol: str,
    account_id: Optional[int] = Query(None, description="账户 ID（不传则用默认账户）"),
    db: Session = Depends(get_db),
):
    """获取某币种的净仓位视图（所有 tier 合并）。

    聚合 LiveSubPosition 中所有 open 子仓位，返回统一净仓位视图：
    net_side / net_size(signed) / unified_leverage(max) / sub_positions 摘要。
    """
    try:
        from backend.services.live_position_manager import live_position_manager

        acct_id = _resolve_account_id(db, account_id)
        view = live_position_manager.get_net_position(db, acct_id, symbol.upper())
        return {
            "symbol": view.symbol,
            "net_side": view.net_side,
            "net_size": view.net_size,
            "unified_leverage": view.unified_leverage,
            "sub_positions": view.sub_positions,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[positions] get_net_position {symbol} 异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get net position: {e}")


@router.get("/sub/{symbol}")
async def get_sub_positions(
    symbol: str,
    account_id: Optional[int] = Query(None, description="账户 ID（不传则用默认账户）"),
    db: Session = Depends(get_db),
):
    """获取某币种的子仓位分解（scalp / trend_follow 各自）。

    直接查询 LiveSubPosition 表的 open 记录。
    """
    try:
        acct_id = _resolve_account_id(db, account_id)
        subs = (
            db.query(LiveSubPosition)
            .filter(
                LiveSubPosition.account_id == acct_id,
                LiveSubPosition.symbol == symbol.upper(),
                LiveSubPosition.status == "open",
            )
            .order_by(LiveSubPosition.id.asc())
            .all()
        )
        return [
            {
                "id": s.id,
                "trade_nature": s.trade_nature,
                "timeframe_tier": s.timeframe_tier,
                "side": s.side,
                "size": s.size,
                "leverage": s.leverage,
                "margin": s.margin,
                "entry_price": s.entry_price,
                "exchange_order_id": s.exchange_order_id,
                "status": s.status,
            }
            for s in subs
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[positions] get_sub_positions {symbol} 异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get sub positions: {e}")


class ReconcileRequest(BaseModel):
    """手动对账请求。"""
    account_id: Optional[int] = None
    symbols: Optional[List[str]] = None  # 不传则对账该账户所有 open 子仓涉及的 symbol


@router.post("/reconcile")
async def trigger_reconcile(
    req: ReconcileRequest,
    db: Session = Depends(get_db),
):
    """手动触发对账：live 子仓账本 vs 交易所实际净仓位。

    对每个 symbol：
    1. 从 LiveExecutor.get_positions 拉取交易所实际净仓位（signed）。
    2. 调用 live_position_manager.reconcile 比对本地账本。
    返回每个 symbol 的对账结果。
    """
    try:
        from backend.services.live_position_manager import live_position_manager
        from backend.services.exchange.live_executor import LiveExecutor

        acct_id = _resolve_account_id(db, req.account_id)

        # 确定要对账的 symbol 集合
        if req.symbols:
            symbols = [s.upper() for s in req.symbols]
        else:
            rows = (
                db.query(LiveSubPosition.symbol)
                .filter(
                    LiveSubPosition.account_id == acct_id,
                    LiveSubPosition.status == "open",
                )
                .distinct()
                .all()
            )
            symbols = [r[0] for r in rows]

        if not symbols:
            return {"account_id": acct_id, "checked": 0, "results": []}

        # 拉取交易所实际仓位（一次性），按 symbol 索引
        exchange_qty_map: Dict[str, float] = {}
        exchange_lev_map: Dict[str, float] = {}
        try:
            live_positions = LiveExecutor().get_positions(db, acct_id, status="open")
            for p in (live_positions or []):
                psym = (p.get("symbol") or "").upper()
                if not psym:
                    continue
                sz = float(p.get("size", 0) or 0)
                pside = str(p.get("side", "") or "").lower()
                exchange_qty_map[psym] = sz if pside == "long" else -sz
                exchange_lev_map[psym] = float(p.get("leverage", 1) or 1)
        except Exception as ex_err:
            logger.warning(f"[positions] reconcile: 交易所仓位拉取失败: {ex_err}")

        results: List[Dict[str, Any]] = []
        for sym in symbols:
            ex_qty = float(exchange_qty_map.get(sym, 0) or 0)
            ex_lev = float(exchange_lev_map.get(sym, 1) or 1)
            recon = live_position_manager.reconcile(
                db, acct_id, sym,
                exchange_qty=ex_qty,
                exchange_leverage=ex_lev,
            )
            recon["symbol"] = sym
            results.append(recon)

        matched_count = sum(1 for r in results if r.get("matched"))
        return {
            "account_id": acct_id,
            "checked": len(results),
            "matched": matched_count,
            "mismatched": len(results) - matched_count,
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[positions] trigger_reconcile 异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to reconcile: {e}")


@router.get("/health")
async def positions_health():
    """仓位服务健康检查。"""
    return {
        "status": "healthy",
        "modules": {
            "LivePositionManager": True,
            "LiveSubPosition": True,
        },
    }
