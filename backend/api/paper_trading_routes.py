"""Paper Trading API Routes — 模拟交易接口

提供虚拟资金管理、模拟下单/平仓、持仓与订单查询、统计摘要等功能。
"""

import logging
import threading
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.connection import get_db
from backend.services.paper_trading_engine import paper_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/paper", tags=["Paper Trading"])


def _ensure_paper_schema():
    """确保 paper trading 相关表和列已创建"""
    try:
        from backend.database.connection import engine
        from backend.database.models import PaperBalance, PaperPosition, PaperOrder
        from sqlalchemy import text, inspect

        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()

        if "paper_balances" not in existing_tables or "paper_positions" not in existing_tables or "paper_orders" not in existing_tables:
            from backend.database.connection import Base
            Base.metadata.create_all(bind=engine)
            logger.info("[Paper] 创建了 paper 相关表")

        if "accounts" in existing_tables:
            cols = {c["name"] for c in inspector.get_columns("accounts")}
            if "trading_mode" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE accounts ADD COLUMN trading_mode VARCHAR(10) DEFAULT 'live'"))
                logger.info("[Paper] 添加了 accounts.trading_mode 列")
        if "paper_orders" in existing_tables:
            cols = {c["name"] for c in inspector.get_columns("paper_orders")}
            if "exchange" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE paper_orders ADD COLUMN exchange VARCHAR(32)"))
                    conn.execute(text(
                        "UPDATE paper_orders "
                        "SET exchange = COALESCE((SELECT selected_exchange FROM accounts WHERE accounts.id = paper_orders.account_id), 'asterdex') "
                        "WHERE exchange IS NULL"
                    ))
                logger.info("[Paper] 添加了 paper_orders.exchange 列")
            if "entry_price" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE paper_orders ADD COLUMN entry_price FLOAT"))
                logger.info("[Paper] 添加了 paper_orders.entry_price 列")
    except Exception as e:
        logger.warning(f"[Paper] schema 检查: {e}")


_paper_schema_lock = threading.Lock()
_paper_schema_done = False


def ensure_paper_schema_once() -> None:
    """
    在应用 startup 中调用一次。禁止在模块 import 时执行 DB 操作，否则 SQLite 锁竞争时会阻塞整个
    后端导入，桌面端表现为长时间「卡死」、/api/health 迟迟不可用。
    """
    global _paper_schema_done
    with _paper_schema_lock:
        if _paper_schema_done:
            return
        _ensure_paper_schema()
        _paper_schema_done = True


# ───── Request Models ─────

class InitializeRequest(BaseModel):
    account_id: int
    initial_balance: float = 100000.0


class PlaceOrderRequest(BaseModel):
    account_id: int
    symbol: str
    side: str           # buy / sell
    quantity: float
    order_type: str = "market"
    price: Optional[float] = None
    leverage: float = 1.0
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    strategy_id: Optional[str] = None


class ClosePositionRequest(BaseModel):
    account_id: int
    symbol: str
    side: str           # long / short
    quantity: Optional[float] = None  # None = 全部平仓, >0 = 部分平仓


# ───── 初始化 / 重置 ─────

@router.post("/initialize")
def initialize_paper_account(req: InitializeRequest, db: Session = Depends(get_db)):
    """初始化模拟账户（设置虚拟资金）"""
    try:
        result = paper_engine.initialize_account(db, req.account_id, req.initial_balance)
        return {"ok": True, "balance": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"初始化模拟账户失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset-balance/{account_id}")
def reset_paper_balance(account_id: int, db: Session = Depends(get_db)):
    """软重置：仅归零盈亏和手续费，保留持仓/订单/交易对配置"""
    try:
        result = paper_engine.reset_balance_only(db, account_id)
        return {"ok": True, "balance": result, "mode": "balance_only"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"软重置模拟账户失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class SetBalanceRequest(BaseModel):
    account_id: int
    initial_balance: float


@router.post("/set-balance")
def set_paper_balance(req: SetBalanceRequest, db: Session = Depends(get_db)):
    """修改模拟账户初始金额（仅无持仓时可用)"""
    try:
        result = paper_engine.set_initial_balance(db, req.account_id, req.initial_balance)
        return {"ok": True, "balance": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"设置模拟账户金额失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset/{account_id}")
def reset_paper_account(account_id: int, db: Session = Depends(get_db)):
    """硬重置：清除所有持仓/订单，恢复初始资金（不影响交易对配置）"""
    try:
        result = paper_engine.reset_account(db, account_id)
        return {"ok": True, "balance": result, "mode": "full"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"重置模拟账户失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ───── 下单 / 平仓 ─────

@router.post("/order")
def place_paper_order(req: PlaceOrderRequest, db: Session = Depends(get_db)):
    """模拟下单"""
    # 白名单路径无 JWT → 注入 system identity(RLS tenant_id)
    from backend.core.tenant import set_system_identity
    set_system_identity()
    try:
        # ── 2026-07-06 整改（审查报告 4.2 路径覆盖矩阵 / 4.5 #8）──
        # 此前 REST 直接下单完全裸奔，不经过任何门禁，是全链路里唯一"100% 无风控"
        # 的下单入口。现接入与 FullAuto 内部路径同一套 unified_gate.evaluate_entry：
        # 日额度、单笔风险硬顶、盈亏比、regime 极端行情等硬约束统一生效，不再有
        # 能完全绕过风控的旁路。手动/外部系统调用视为高置信度请求（跳过置信度软
        # 阈值），但仍必须通过上述硬约束，被拦截时返回 403 而不是静默放行。
        _side_l = (req.side or "").strip().lower()
        if _side_l in ("buy", "sell"):
            from backend.services.decision_core.unified_gate import evaluate_entry
            from backend.database.models import Account

            _acct = db.query(Account).filter(Account.id == req.account_id).first()
            _trading_mode = (getattr(_acct, "trading_mode", None) or "paper").strip().lower()

            _ref_price = req.price
            if not _ref_price:
                try:
                    from backend.services.market_price_service import get_price
                    _ref_price = get_price(req.symbol)
                except Exception:
                    _ref_price = None

            _tp_pct = None
            _sl_pct = None
            if _ref_price and _ref_price > 0:
                if req.tp_price:
                    _tp_pct = abs(float(req.tp_price) - float(_ref_price)) / float(_ref_price)
                if req.sl_price:
                    _sl_pct = abs(float(req.sl_price) - float(_ref_price)) / float(_ref_price)

            _gate_result = evaluate_entry(
                db=db,
                account_id=req.account_id,
                symbol=req.symbol,
                action=_side_l,
                confidence=100.0,
                tier="short",
                trade_nature="scalp",
                tp_pct=_tp_pct,
                sl_pct=_sl_pct,
                mode=_trading_mode,
            )
            if not _gate_result.allowed:
                logger.info(
                    f"[Paper API] /order 被统一门禁拦截: {req.symbol} {_side_l} "
                    f"reason={_gate_result.reason}"
                )
                raise HTTPException(
                    status_code=403,
                    detail=f"下单被统一门禁拦截: {_gate_result.reason}",
                )

        result = paper_engine.place_order(
            db=db,
            account_id=req.account_id,
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            order_type=req.order_type,
            price=req.price,
            leverage=req.leverage,
            tp_price=req.tp_price,
            sl_price=req.sl_price,
            strategy_id=req.strategy_id,
        )
        if result is None:
            raise HTTPException(status_code=400, detail="下单失败（余额不足或无法获取价格）")
        return {"ok": True, "order": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"模拟下单失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/close")
def close_paper_position(req: ClosePositionRequest, db: Session = Depends(get_db)):
    """模拟平仓（支持部分平仓）"""
    # 白名单路径无 JWT → 注入 system identity(RLS tenant_id)
    from backend.core.tenant import set_system_identity
    set_system_identity()
    try:
        result = paper_engine.close_position(
            db, req.account_id, req.symbol, req.side,
            reason="manual", quantity=req.quantity,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="无可平持仓")
        return {"ok": True, "result": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"模拟平仓失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ───── 查询 ─────

@router.get("/balance/{account_id}")
def get_paper_balance(account_id: int, db: Session = Depends(get_db)):
    """获取虚拟余额"""
    result = paper_engine.get_balance(db, account_id)
    if result is None:
        raise HTTPException(status_code=404, detail="模拟账户未初始化")
    return result


@router.get("/positions/{account_id}")
def get_paper_positions(account_id: int, status: str = "open", db: Session = Depends(get_db)):
    """获取虚拟持仓"""
    # [perf 2026-08-18] 前端 3s 轮询 + 每持仓多查询（净额/事件溯源对拍），
    # GIL 竞争下实测 3.9s。2.5s TTL 缓存：命中≈0ms，轮询间隔 3s 无感知差异。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached(
        f"paper_positions:{account_id}:{status}",
        5.0,
        lambda: paper_engine.get_positions(db, account_id, status),
    )


@router.get("/positions/{position_id}/health")
def get_paper_position_health(position_id: int, db: Session = Depends(get_db)):
    """获取单个持仓的趋势健康分、峰值利润和退出状态。"""
    try:
        import json
        from backend.database.models import PaperPosition

        pos = db.query(PaperPosition).filter(PaperPosition.id == position_id).first()
        if not pos:
            raise HTTPException(status_code=404, detail="持仓不存在")
        try:
            exit_state = json.loads(getattr(pos, "exit_state_json", None) or "null")
        except Exception:
            exit_state = None
        return {
            "position_id": pos.id,
            "account_id": pos.account_id,
            "symbol": pos.symbol,
            "side": pos.side,
            "trade_nature": getattr(pos, "trade_nature", None),
            "health_score": getattr(pos, "health_score", None),
            "health_regime": getattr(pos, "health_regime", None),
            "peak_unrealized_pnl": round(float(getattr(pos, "peak_unrealized_pnl", 0.0) or 0.0), 2),
            "peak_pnl_pct": round(float(getattr(pos, "peak_pnl_pct", 0.0) or 0.0) * 100, 2),
            "exit_state": exit_state,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取持仓健康分失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders/{account_id}")
def get_paper_orders(
    account_id: int,
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """获取订单历史"""
    return paper_engine.get_orders(db, account_id, status, limit)


@router.get("/summary/{account_id}")
def get_paper_summary(account_id: int, db: Session = Depends(get_db)):
    """获取交易统计摘要"""
    return paper_engine.get_summary(db, account_id)


@router.get("/equity-curve/{account_id}")
def get_paper_equity_curve(
    account_id: int,
    period: str = "7d",
    db: Session = Depends(get_db),
):
    """Paper 权益曲线（与仪表盘「当前」同源：paper_balances + 订单重建）。

    period: 7d | 30d | all
    """
    try:
        from backend.services.paper_equity_curve import build_paper_equity_curve

        return build_paper_equity_curve(db, account_id, period=period)
    except Exception as e:
        logger.error("paper equity curve failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
