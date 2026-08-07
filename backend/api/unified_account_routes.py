"""统一账户 API 路由 —— 阶段 5.1

暴露 unified_account_service 的能力，供前端 AI 交易员配置 + 套利中心 共用。

端点:
- GET  /api/unified-account/list          列出所有 paper 账户（可按 scope/owner 过滤）
- GET  /api/unified-account/exposure/combined  跨系统合并敞口（AI + 套利）
- GET  /api/unified-account/fee-schedule   费率表（fee_schedule_service 摘要）
- POST /api/unified-account/transfer       跨账户资金划转（记账层）
- GET  /api/unified-account/{scope}/{id}   获取单个归一化账户视图（最后定义，避免吞静态路由）

设计: 双表共存，不合并表。前端通过此 API 看到统一视图，绑定入口可统一。
注意: 静态路由（/exposure/combined, /fee-schedule, /transfer）必须定义在
      参数化路由 /{scope}/{account_id} 之前，否则会被后者吞掉。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.connection import get_db
from backend.services.unified_account_service import unified_account_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/unified-account", tags=["Unified Account"])


class TransferRequest(BaseModel):
    from_scope: str  # "ai" / "arbitrage"
    from_id: int
    to_scope: str
    to_id: int
    amount: float


# ════════════════════════════════════════════════════════
#  静态路由（必须先定义，避免被 /{scope}/{account_id} 吞掉）
# ════════════════════════════════════════════════════════

@router.get("/list")
def list_paper_accounts(
    scope: Optional[str] = Query(None, description="过滤: ai/arbitrage/None=全部"),
    owner_account_id: Optional[int] = Query(None, description="按关联交易员过滤"),
    db: Session = Depends(get_db),
):
    """列出所有 paper 账户（归一化视图）。

    返回 AI 树（PaperBalance）+ 套利树（ArbitragePaperAccountDB）的所有账户，
    统一字段: id/scope/source_table/total_equity/available_balance/...
    """
    try:
        views = unified_account_service.list_all_paper_accounts(
            db, scope=scope, owner_account_id=owner_account_id,
        )
        return {
            "accounts": [v.to_dict() for v in views],
            "count": len(views),
        }
    except Exception as e:
        logger.error(f"[UnifiedAccount] list 异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/exposure/combined")
def get_combined_exposure(
    ai_account_id: Optional[int] = Query(None, description="AI paper 账户 ID"),
    arbitrage_account_id: Optional[int] = Query(None, description="套利 paper 账户 ID"),
    db: Session = Depends(get_db),
):
    """跨系统（AI + 套利）合并敞口。

    用于前端展示总权益/总冻结/总 uPnL，以及 cross_system_coordinator 资金冲突检测。
    """
    try:
        exposure = unified_account_service.get_combined_exposure(
            db,
            ai_account_id=ai_account_id,
            arbitrage_account_id=arbitrage_account_id,
        )
        return exposure.to_dict()
    except Exception as e:
        logger.error(f"[UnifiedAccount] exposure 异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fee-schedule")
def get_fee_schedule():
    """返回所有交易所费率表（fee_schedule_service 摘要）。

    前端展示用: maker/taker 费率、维持保证金率、最小名义价值。
    """
    try:
        from backend.services.fee_schedule_service import get_all_exchange_summary
        from backend.config.settings import DEFAULT_EXCHANGE
        return {
            "default_exchange": DEFAULT_EXCHANGE,
            "exchanges": get_all_exchange_summary(),
        }
    except Exception as e:
        logger.error(f"[UnifiedAccount] fee-schedule 异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/transfer")
def transfer_capital(req: TransferRequest, db: Session = Depends(get_db)):
    """跨账户资金划转（记账层，不动真实资金）。

    用于 AI ↔ 套利 之间的资金调配。
    """
    try:
        result = unified_account_service.transfer_capital(
            db,
            from_scope=req.from_scope,
            from_id=req.from_id,
            to_scope=req.to_scope,
            to_id=req.to_id,
            amount=req.amount,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "划转失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[UnifiedAccount] transfer 异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ════════════════════════════════════════════════════════
#  参数化路由（最后定义，静态路由优先匹配）
# ════════════════════════════════════════════════════════

@router.get("/{scope}/{account_id}")
def get_unified_account(
    scope: str,
    account_id: int,
    db: Session = Depends(get_db),
):
    """获取单个归一化账户视图。

    - scope=ai: 从 PaperBalance 读取
    - scope=arbitrage: 从 ArbitragePaperAccountDB 读取
    """
    if scope not in ("ai", "arbitrage"):
        raise HTTPException(status_code=400, detail=f"无效 scope: {scope}（应为 ai/arbitrage）")
    try:
        view = unified_account_service.get_unified_paper_account(db, account_id, scope=scope)
        if not view:
            raise HTTPException(status_code=404, detail=f"账户不存在: {scope}/{account_id}")
        return view.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[UnifiedAccount] get 异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
