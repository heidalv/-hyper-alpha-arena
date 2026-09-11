"""
AI Trading Routes - AI建议执行和管理API
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import List, Optional
from datetime import datetime, timedelta, timezone
import logging

from backend.database.connection import SessionLocal, AnalyticsSessionLocal
from backend.database.models import Account, AIDecisionLog, Position
from backend.services.trading_commands import place_ai_driven_hyperliquid_order
from backend.services.hyperliquid_environment import get_hyperliquid_client
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-trading", tags=["ai-trading"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Pydantic models
class AISuggestion(BaseModel):
    id: int
    decision_time: str
    operation: str
    symbol: Optional[str]
    target_portion: float
    reason: str
    take_profit_price: Optional[float]
    stop_loss_price: Optional[float]
    executed: bool
    can_execute: bool


class ExecuteSuggestionRequest(BaseModel):
    suggestion_id: int
    modified_params: Optional[dict] = None


class ExecuteSuggestionResponse(BaseModel):
    status: str
    message: str
    order_id: Optional[str] = None
    tp_order_id: Optional[str] = None
    sl_order_id: Optional[str] = None
    executed_at: Optional[str] = None


class PositionWithPlan(BaseModel):
    symbol: str
    side: str
    amount: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    close_plan: Optional[dict]
    position_id: Optional[str] = None


class ClosePositionRequest(BaseModel):
    symbol: str
    position_id: Optional[str] = None  # Specific position to close, if multiple positions exist for same symbol
    amount: Optional[float] = None
    reason: str = "手动平仓"


@router.get("/accounts/{account_id}/suggestions", response_model=List[AISuggestion])
def get_ai_suggestions(
    account_id: int,
    limit: int = 10,
    executed_only: bool = False,
    auto_only: bool = False,
    db: Session = Depends(get_db)
):
    """
    获取AI开单建议列表

    - executed_only=true: 只显示已执行的
    - executed_only=false: 显示未执行的（默认）
    - auto_only=true: 只显示自动执行的（通过信号池触发）
    - auto_only=false: 只显示手动执行的（没有signal_trigger_id）
    """
    try:
        analytics_db = AnalyticsSessionLocal()
        try:
            query = analytics_db.query(AIDecisionLog).filter(
                AIDecisionLog.account_id == account_id,
                AIDecisionLog.operation.in_(["buy", "sell", "close"])
            )

            if not executed_only:
                query = query.filter(AIDecisionLog.executed == "false")

            # auto_only过滤：true=只显示自动触发，false=只显示手动执行
            if auto_only:
                # 只显示有signal_trigger_id的（自动触发）
                query = query.filter(AIDecisionLog.signal_trigger_id.isnot(None))
            elif executed_only:
                # 当executed_only=true且auto_only=false时，只显示手动执行的
                query = query.filter(AIDecisionLog.signal_trigger_id.is_(None))

            query = query.order_by(desc(AIDecisionLog.decision_time)).limit(limit)

            decisions = query.all()
        finally:
            analytics_db.close()

        result = []
        for decision in decisions:
            # 解析decision_snapshot获取详细信息
            take_profit_price = None
            stop_loss_price = None

            if decision.decision_snapshot:
                try:
                    import json
                    snapshot = json.loads(decision.decision_snapshot)
                    take_profit_price = snapshot.get("take_profit_price")
                    stop_loss_price = snapshot.get("stop_loss_price")
                except:
                    pass

            result.append(AISuggestion(
                id=decision.id,
                decision_time=decision.decision_time.isoformat() if decision.decision_time else "",
                operation=decision.operation,
                symbol=decision.symbol,
                target_portion=float(decision.target_portion) if decision.target_portion else 0.0,
                reason=decision.reason or "",
                take_profit_price=take_profit_price,
                stop_loss_price=stop_loss_price,
                executed=(decision.executed == "true"),
                can_execute=(decision.executed == "false")
            ))

        return result

    except Exception as e:
        logger.error(f"Error getting AI suggestions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts/{account_id}/execute-suggestion", response_model=ExecuteSuggestionResponse)
def execute_suggestion(
    account_id: int,
    request: ExecuteSuggestionRequest,
    db: Session = Depends(get_db)
):
    """
    执行AI建议（开仓）

    可以选择修改建议的参数
    """
    try:
        # 获取决策记录
        analytics_db = AnalyticsSessionLocal()
        try:
            decision = analytics_db.query(AIDecisionLog).filter(
                AIDecisionLog.id == request.suggestion_id,
                AIDecisionLog.account_id == account_id
            ).first()
        finally:
            analytics_db.close()

        if not decision:
            raise HTTPException(status_code=404, detail="建议不存在")

        if decision.executed == "true":
            return ExecuteSuggestionResponse(
                status="error",
                message="该建议已经执行过了"
            )

        # 获取账户信息
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="账户不存在")

        # 解析决策
        import json
        decision_data = {}
        if decision.decision_snapshot:
            try:
                decision_data = json.loads(decision.decision_snapshot)
            except:
                pass

        # 应用修改的参数
        if request.modified_params:
            decision_data.update(request.modified_params)

        # 确保基本字段存在
        if "operation" not in decision_data:
            decision_data["operation"] = decision.operation
        if "symbol" not in decision_data:
            decision_data["symbol"] = decision.symbol
        if "target_portion_of_balance" not in decision_data:
            decision_data["target_portion_of_balance"] = float(decision.target_portion)

        # 获取当前组合状态
        portfolio = {
            "total_assets": float(account.current_cash or 0),
            "positions": {}
        }

        # 获取持仓信息
        positions = db.query(Position).filter(
            Position.account_id == account_id,
            Position.szi != 0
        ).all()

        for pos in positions:
            if pos.symbol not in portfolio["positions"]:
                portfolio["positions"][pos.symbol] = {
                    "szi": float(pos.szi),
                    "current_value": float(pos.unrealized_pnl or 0) + float(pos.position_value or 0)
                }

        # 执行交易
        logger.info(f"Executing AI suggestion {request.suggestion_id}: {decision_data}")

        try:
            # Phase 1: Binance removed - only HyperLiquid
            if getattr(account, "hyperliquid_enabled", "false") != "true":
                return ExecuteSuggestionResponse(status="error", message="仅支持 HyperLiquid 账户执行（Binance 已移除）")
            place_ai_driven_hyperliquid_order(account_id=account.id)
            result = {"status": "success"}

            if result.get("status") == "success":
                return ExecuteSuggestionResponse(
                    status="success",
                    message="订单执行成功",
                    order_id=result.get("order_id"),
                    tp_order_id=result.get("tp_order_id"),
                    sl_order_id=result.get("sl_order_id"),
                    executed_at=datetime.now(timezone.utc).isoformat()
                )
            else:
                return ExecuteSuggestionResponse(
                    status="error",
                    message=result.get("message", "订单执行失败")
                )

        except Exception as e:
            logger.error(f"Error executing trade: {e}", exc_info=True)
            return ExecuteSuggestionResponse(
                status="error",
                message=f"执行失败: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in execute_suggestion: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/accounts/{account_id}/positions-with-plans", response_model=List[PositionWithPlan])
def get_positions_with_plans(
    account_id: int,
    db: Session = Depends(get_db)
):
    """
    获取持仓列表及其平仓计划（止盈止损）
    支持Hyperliquid和Binance
    """
    try:
        # 获取账户
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="账户不存在")

        positions = []

        # ============ 处理 Hyperliquid 持仓 ============
        if account.hyperliquid_enabled == "true":
            try:
                client = get_hyperliquid_client(db, account_id)
                hl_positions = client.get_positions(db, include_timing=False)

                for pos in hl_positions:
                    coin = pos.get('coin')
                    position_size = float(pos.get('szi', 0))

                    # 跳过无持仓的币种
                    if not coin or abs(position_size) < 1e-8:
                        continue

                    # 判断多空方向：正数为多头，负数为空头
                    side = 'long' if position_size > 0 else 'short'

                    # 查找对应的开仓决策
                    analytics_db = AnalyticsSessionLocal()
                    try:
                        open_decision = analytics_db.query(AIDecisionLog).filter(
                            AIDecisionLog.account_id == account_id,
                            AIDecisionLog.symbol == coin,
                            AIDecisionLog.operation.in_(["buy", "sell"]),
                            AIDecisionLog.executed == "true"
                        ).order_by(desc(AIDecisionLog.decision_time)).first()
                    finally:
                        analytics_db.close()

                    close_plan = None
                    if open_decision:
                        # 解析止盈止损
                        take_profit_price = None
                        stop_loss_price = None
                        if open_decision.decision_snapshot:
                            try:
                                import json
                                snapshot = json.loads(open_decision.decision_snapshot)
                                take_profit_price = snapshot.get("take_profit_price")
                                stop_loss_price = snapshot.get("stop_loss_price")
                            except:
                                pass

                        close_plan = {
                            "decision_id": open_decision.id,
                            "take_profit_price": take_profit_price,
                            "stop_loss_price": stop_loss_price,
                            "tp_order_id": open_decision.tp_order_id,
                            "sl_order_id": open_decision.sl_order_id,
                            "tp_triggered": False,
                            "sl_triggered": False
                        }

                    # 计算当前价格（使用 entry_px + unrealized_pnl / position_size 更准确）
                    entry_price = float(pos.get('entry_px', 0))
                    unrealized_pnl = float(pos.get('unrealized_pnl', 0))
                    position_value = float(pos.get('position_value', 0))

                    # 如果没有 entry_price，尝试从 position_value 和 size 计算
                    if entry_price == 0 and position_value != 0 and abs(position_size) > 0:
                        current_price = position_value / abs(position_size)
                    else:
                        current_price = entry_price + (unrealized_pnl / abs(position_size) if abs(position_size) > 0 else 0)

                    positions.append(PositionWithPlan(
                        symbol=coin,
                        side=side,
                        amount=abs(position_size),
                        entry_price=entry_price,
                        current_price=current_price,
                        unrealized_pnl=unrealized_pnl,
                        close_plan=close_plan
                    ))

                logger.info(f"Retrieved {len(hl_positions)} Hyperliquid positions for account {account_id}")

            except Exception as e:
                logger.error(f"Error getting Hyperliquid positions: {e}", exc_info=True)

        # ============ 处理 Binance 持仓 ============ # 🔥 为每个独立的AI决策创建单独的持仓记录，不合并 # 每笔AI交易决策都有独立的持仓记录，包含完整的交易信息
        if account.binance_enabled == "true":
            try:
                from backend.database.models import Order
                from services.market_data import get_last_price

                logger.info(f"[DEBUG] Getting individual AI decision positions for account {account_id}")

                # 🔥🔥🔥 新增：先获取币安实际持仓，用于交叉验证
                actual_positions_set = set()  # 存储(symbol_simplified, side)元组
                try:
                    api_key, api_secret = decrypt_api_credentials(account.binance_api_credentials)
                    market_type = getattr(account, 'binance_market_type', 'futures')
                    testnet = getattr(account, 'binance_testnet', 'false') == 'true'
                    client = create_binance_client(account.id, api_key, api_secret, market_type, testnet)
                    actual_binance_positions = client.get_positions(db)
                    
                    for pos in actual_binance_positions:
                        # 统一symbol格式：ETH/USDT:USDT -> ETH
                        raw_symbol = pos.get('symbol', '')
                        simple_symbol = raw_symbol.replace('/USDT:USDT', '').replace('/USDT', '').replace('USDT', '')
                        side = pos.get('side', 'long')  # long or short
                        size = abs(float(pos.get('size', 0)))
                        if size > 0:
                            actual_positions_set.add((simple_symbol, side))
                    
                    logger.info(f"[DEBUG] Actual Binance positions: {actual_positions_set}")
                except Exception as e:
                    logger.error(f"[DEBUG] Failed to get actual Binance positions: {e}")
                    # 如果获取失败，返回空列表而不是过时的数据
                    return positions

                # 🔥 查询所有已执行的开仓决策（buy/sell），排除已被平仓的
                # 通过检查是否存在后续的平仓(close)操作来判断持仓是否仍然有效
                from sqlalchemy import and_, exists

                # 子查询：查找每个开仓决策后是否有对应的平仓决策
                open_decisions = []
                analytics_db = AnalyticsSessionLocal()
                try:
                    all_open_decisions = analytics_db.query(AIDecisionLog).filter(
                        AIDecisionLog.account_id == account_id,
                        AIDecisionLog.operation.in_(["buy", "sell"]),
                        AIDecisionLog.executed == "true"
                    ).order_by(desc(AIDecisionLog.decision_time)).all()
                finally:
                    analytics_db.close()

                for decision in all_open_decisions:
                    # 🔥🔥🔥 新增：检查该symbol在币安是否还有实际持仓
                    decision_symbol = decision.symbol or ''
                    decision_side = 'long' if decision.operation == 'buy' else 'short'
                    
                    # 检查币安实际持仓是否存在
                    if (decision_symbol, decision_side) not in actual_positions_set:
                        logger.info(f"[DEBUG] Skipping decision {decision.id}: {decision_symbol} {decision_side} not in actual positions")
                        continue

                    # 检查这个开仓决策后是否有对应的平仓决策 # 🔥 修复：平仓记录的reason中包含side信息，需要匹配 # 例如 "同步平仓: BNB short 在交易所已平仓" 或 "Orphaned Fix ID xxx" # 为了简化，我们检查close记录的reason是否包含当前持仓的side
                    analytics_db2 = AnalyticsSessionLocal()
                    try:
                        close_records = analytics_db2.query(AIDecisionLog).filter(
                            AIDecisionLog.account_id == account_id,
                            AIDecisionLog.symbol == decision.symbol,
                            AIDecisionLog.operation == "close",
                            AIDecisionLog.executed == "true",
                            AIDecisionLog.decision_time > decision.decision_time
                        ).all()
                    finally:
                        analytics_db2.close()

                    # 检查是否有匹配当前side的close记录
                    close_exists = False
                    for close_rec in close_records:
                        # 检查reason中是否包含当前持仓的side # 如果reason不包含side信息（老的记录），则认为是匹配的
                        if close_rec.reason:
                            # 新格式: "同步平仓: BNB short 在交易所已平仓" # 检查是否包含不同side的关键词
                            opposite_side = 'short' if decision_side == 'long' else 'long'
                            if opposite_side in close_rec.reason.lower():
                                # 这个close是针对另一个side的，跳过
                                continue
                        close_exists = True
                        break

                    # 只有当没有后续平仓记录时，才认为这个持仓仍然有效
                    if not close_exists:
                        open_decisions.append(decision)

                logger.info(f"[DEBUG] Found {len(open_decisions)} executed open decisions (after cross-validation with Binance)")

                # 为每个决策创建独立的持仓记录
                for decision in open_decisions:
                    if not decision.symbol:
                        continue

                    # 从Order表获取实际交易数量和价格
                    if decision.order_id:
                        order = db.query(Order).filter(Order.id == decision.order_id).first()
                        if order:
                            qty = float(order.quantity)
                            price = float(order.price)
                            symbol = decision.symbol
                            operation = decision.operation

                            # 判断多空方向
                            side = 'long' if operation == "buy" else 'short'

                            # 获取当前市场价格计算盈亏
                            current_price = get_last_price(symbol, "binance")
                            if not current_price:
                                current_price = price

                            # 计算未实现盈亏
                            if operation == "buy":
                                unrealized_pnl = (current_price - price) * qty
                            else:  # sell
                                unrealized_pnl = (price - current_price) * qty

                            # 解析止盈止损
                            close_plan = None
                            take_profit_price = None
                            stop_loss_price = None

                            if decision.decision_snapshot:
                                try:
                                    import json
                                    snapshot = json.loads(decision.decision_snapshot)
                                    take_profit_price = snapshot.get("take_profit_price")
                                    stop_loss_price = snapshot.get("stop_loss_price")
                                except:
                                    pass

                            close_plan = {
                                "decision_id": decision.id,
                                "take_profit_price": take_profit_price,
                                "stop_loss_price": stop_loss_price,
                                "tp_order_id": decision.tp_order_id,
                                "sl_order_id": decision.sl_order_id,
                                "tp_triggered": False,
                                "sl_triggered": False
                            }

                            # 创建独立的持仓记录
                            position = PositionWithPlan(
                                symbol=symbol,
                                side=side,
                                amount=qty,
                                entry_price=price,
                                current_price=current_price,
                                unrealized_pnl=unrealized_pnl,
                                close_plan=close_plan,
                                position_id=f"decision_{decision.id}"  # 使用决策ID作为唯一标识
                            )
                            positions.append(position)
                            logger.info(
                                f"[DEBUG] Added independent position: {symbol} {side} {qty} @ ${price:.2f} "
                                f"(decision_id={decision.id}, PnL=${unrealized_pnl:.2f})"
                            )

                logger.info(f"Retrieved {len(positions)} independent AI positions for account {account_id}")

            except Exception as e:
                logger.error(f"Error getting AI positions from database: {e}", exc_info=True)
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")

        return positions

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting positions with plans: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts/{account_id}/close-position")
def close_position(
    account_id: int,
    request: ClosePositionRequest,
    db: Session = Depends(get_db)
):
    """
    手动平仓 - 支持Hyperliquid和Binance
    """
    try:
        # 获取账户
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="账户不存在")

        result = None
        current_position = None
        exchange = None

        # ============ Hyperliquid 平仓 ============
        if account.hyperliquid_enabled == "true":
            try:
                client = get_hyperliquid_client(db, account_id)

                # 获取当前持仓以确定平仓参数
                positions = client.get_positions(db, include_timing=False)
                for pos in positions:
                    if pos.get('coin') == request.symbol:
                        current_position = pos
                        break

                if not current_position:
                    raise HTTPException(status_code=404, detail=f"未找到 {request.symbol} 的持仓")

                position_size = float(current_position.get('szi', 0))
                if abs(position_size) < 1e-8:
                    raise HTTPException(status_code=400, detail=f"{request.symbol} 持仓数量为0，无法平仓")

                # 确定平仓方向：多头平仓卖出，空头平仓买入
                is_buy = position_size < 0  # 空头需要买入平仓
                size = abs(position_size)

                # 获取当前市场价格
                from services.market_data import get_last_price
                current_price = get_last_price(request.symbol, "hyperliquid")

                # 读取全局保证金模式
                from backend.database.models import SystemConfig
                _m_cfg = db.query(SystemConfig).filter(SystemConfig.key == "global_margin_mode").first()
                _is_cross = _m_cfg.value == "cross" if _m_cfg else False

                result = client.place_order(
                    db=db,
                    symbol=request.symbol,
                    is_buy=is_buy,
                    size=size,
                    order_type="market",
                    price=current_price,
                    reduce_only=True,
                    is_cross=_is_cross,
                )

                exchange = "hyperliquid"

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error closing Hyperliquid position: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Hyperliquid平仓失败: {str(e)}")

        # ============ Binance 平仓 ============
        elif account.binance_enabled == "true":
            try:
                # 解密API密钥
                api_key, api_secret = decrypt_api_credentials(account.binance_api_credentials)
                market_type = getattr(account, 'binance_market_type', 'futures')
                testnet = getattr(account, 'binance_testnet', 'false') == 'true'

                # 创建客户端
                client = create_binance_client(account.id, api_key, api_secret, market_type, testnet)

                # 🔥 支持两种position_id格式： # 1. "decision_{id}" - 从前端传来的决策ID格式 # 2. 原始的Binance position_id格式
                if request.position_id:
                    # 检查是否是decision格式
                    if request.position_id.startswith("decision_"):
                        # 提取decision_id
                        decision_id = int(request.position_id.replace("decision_", ""))

                        # 查找对应的决策记录
                        analytics_db = AnalyticsSessionLocal()
                        try:
                            decision = analytics_db.query(AIDecisionLog).filter(
                                AIDecisionLog.id == decision_id,
                                AIDecisionLog.account_id == account_id,
                                AIDecisionLog.symbol == request.symbol
                            ).first()
                        finally:
                            analytics_db.close()

                        if not decision:
                            raise HTTPException(status_code=404, detail=f"未找到ID为 {decision_id} 的决策记录")

                        # 从决策记录的order_id获取订单信息
                        if decision.order_id:
                            from backend.database.models import Order
                            order = db.query(Order).filter(Order.id == decision.order_id).first()
                            if order:
                                close_amount = float(order.quantity)
                                logger.info(f"[BINANCE] Closing decision {decision_id} with order amount {close_amount}")
                            else:
                                raise HTTPException(status_code=404, detail=f"未找到决策 {decision_id} 对应的订单")
                        else:
                            raise HTTPException(status_code=400, detail=f"决策 {decision_id} 没有关联订单")
                    else:
                        # 原始格式：查找指定的Binance持仓
                        target_position = db.query(BinancePosition).filter(
                            BinancePosition.account_id == account_id,
                            BinancePosition.position_id == request.position_id,
                            BinancePosition.symbol == request.symbol,
                            BinancePosition.status == 'open'
                        ).first()

                        if not target_position:
                            raise HTTPException(status_code=404, detail=f"未找到ID为 {request.position_id} 的持仓或该持仓不在开仓状态")

                        # 使用目标持仓的数量进行平仓
                        close_amount = float(target_position.size)
                        logger.info(f"[BINANCE] Closing specific position {request.position_id} with amount {close_amount}")
                else:
                    # 没有指定position_id时，关闭该symbol的所有持仓
                    positions = client.get_positions(db)
                    position_to_close = None
                    for pos in positions:
                        if pos.get('symbol') == request.symbol:
                            position_to_close = pos
                            break

                    if not position_to_close:
                        raise HTTPException(status_code=404, detail=f"未找到 {request.symbol} 的持仓")

                    close_amount = abs(position_to_close.get('size', 0))
                    logger.info(f"[BINANCE] Closing all positions for {request.symbol} with amount {close_amount}")

                # 执行平仓 # 🔥 如果是decision格式，直接使用之前获取的close_amount
                if request.position_id and request.position_id.startswith("decision_"):
                    # 已经在前面获取了close_amount
                    result = client.close_position(db, request.symbol, close_amount)
                elif request.position_id:
                    # 如果指定了特定的position_id，我们先获取该持仓的数量
                    target_position = db.query(BinancePosition).filter(
                        BinancePosition.account_id == account_id,
                        BinancePosition.position_id == request.position_id,
                        BinancePosition.symbol == request.symbol,
                        BinancePosition.status == 'open'
                    ).first()

                    if target_position:
                        close_amount = float(target_position.size)
                        result = client.close_position(db, request.symbol, close_amount)
                    else:
                        # 如果找不到特定持仓，使用默认逻辑
                        result = client.close_position(db, request.symbol)
                else:
                    # 没有指定position_id，关闭整个symbol的持仓
                    result = client.close_position(db, request.symbol)

                # 获取当前持仓信息
                positions = client.get_positions(db)
                for pos in positions:
                    if pos.get('symbol') == request.symbol:
                        current_position = pos
                        break

                exchange = "binance"

            except Exception as e:
                logger.error(f"Error closing Binance position: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Binance平仓失败: {str(e)}")
        else:
            raise HTTPException(status_code=400, detail="该账户未启用任何交易所")

        # ============ 处理平仓结果 ============
        if result and result.get("status") == "success":
            # 🔥 更新BinancePosition状态（对于币安平仓）
            if exchange == "binance":
                try:
                    from backend.database.models import BinancePosition

                    # 如果提供了position_id，更新特定的持仓
                    if request.position_id:
                        # 🔥 检查是否是decision格式
                        if request.position_id.startswith("decision_"):
                            # 提取decision_id并查找对应的BinancePosition
                            decision_id = int(request.position_id.replace("decision_", ""))
                            binance_position = db.query(BinancePosition).filter(
                                BinancePosition.account_id == account_id,
                                BinancePosition.symbol == request.symbol,
                                BinancePosition.status == 'open'
                            ).first()

                            if binance_position:
                                binance_position.status = 'closed'
                                binance_position.closed_at = func.now()
                                if current_position:
                                    binance_position.realized_pnl = current_position.get('unrealized_pnl', 0)
                                db.commit()
                                logger.info(f"[BINANCE] Updated decision {decision_id} position status to closed: {request.symbol}")
                            else:
                                logger.warning(f"[BINANCE] No open position found for decision {decision_id} to update")
                        else:
                            # 原始格式：直接使用position_id查找
                            binance_position = db.query(BinancePosition).filter(
                                BinancePosition.account_id == account_id,
                                BinancePosition.position_id == request.position_id,
                                BinancePosition.status == 'open'
                            ).first()

                            if binance_position:
                                binance_position.status = 'closed'
                                binance_position.closed_at = func.now()
                                if current_position:
                                    binance_position.realized_pnl = current_position.get('unrealized_pnl', 0)
                                db.commit()
                                logger.info(f"[BINANCE] Updated specific position status to closed: {request.position_id}")
                            else:
                                logger.warning(f"[BINANCE] No open position found for {request.position_id} to update")
                    else:
                        # 否则更新该symbol的第一个开放持仓
                        binance_position = db.query(BinancePosition).filter(
                            BinancePosition.account_id == account_id,
                            BinancePosition.symbol == request.symbol,
                            BinancePosition.status == 'open'
                        ).first()

                        if binance_position:
                            binance_position.status = 'closed'
                            binance_position.closed_at = func.now()
                            if current_position:
                                binance_position.realized_pnl = current_position.get('unrealized_pnl', 0)
                            db.commit()
                            logger.info(f"[BINANCE] Updated position status to closed: {request.symbol}")
                        else:
                            logger.warning(f"[BINANCE] No open position found for {request.symbol} to update")
                except Exception as update_err:
                    logger.error(f"[BINANCE] Failed to update position status: {update_err}")
                    # 不影响主流程
            
            # 记录平仓决策
            from services.ai_decision_service import save_ai_decision

            portfolio = {
                "total_assets": float(account.current_cash or 0),
                "positions": {}
            }

            close_decision = {
                "operation": "close",
                "symbol": request.symbol,
                "target_portion_of_balance": 0,
                "reason": request.reason,
                "close_type": "manual",
                "exchange": exchange
            }

            analytics_db = AnalyticsSessionLocal()
            try:
                save_ai_decision(
                    db=analytics_db,
                    account=account,
                    decision=close_decision,
                    portfolio=portfolio,
                    executed=True,
                    order_id=result.get("order_id")
                )
                analytics_db.commit()
            finally:
                analytics_db.close()

            return {
                "status": "success",
                "message": "平仓成功",
                "exchange": exchange,
                "order_id": result.get("order_id"),
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "realized_pnl": current_position.get('unrealized_pnl', 0) if current_position else 0
            }
        else:
            error_msg = result.get("message", "平仓失败") if result else "平仓失败"
            raise HTTPException(status_code=400, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing position: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts/{account_id}/sync-positions")
def sync_positions(
    account_id: int,
    db: Session = Depends(get_db)
):
    """
    同步持仓状态 - 将项目中的持仓与交易所实际持仓同步

    对于Binance：
    - 检查交易所实际持仓
    - 将项目中状态为open但交易所已不存在的持仓标记为closed
    - 返回同步结果
    """
    try:
        # 获取账户
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="账户不存在")

        synced_count = 0
        errors = []

        # ============ 处理 Binance 持仓同步 ============
        if account.binance_enabled == "true":
            try:
                # 🔥 提前导入 or_，避免在条件分支内导入导致后续代码无法访问
                from sqlalchemy import or_
                
                # 解密API密钥
                api_key, api_secret = decrypt_api_credentials(account.binance_api_credentials)
                market_type = getattr(account, 'binance_market_type', 'futures')
                testnet = getattr(account, 'binance_testnet', 'false') == 'true'

                # 创建客户端
                client = create_binance_client(account.id, api_key, api_secret, market_type, testnet)

                # 获取交易所实际持仓
                exchange_positions = client.get_positions(db)
                exchange_pos_set = set()

                for pos in exchange_positions:
                    # 🔥 全面兼容格式：移除所有可能的分隔符和后缀，只保留基础币种 # 例如: "BTC/USDT" -> "BTC", "BTCUSDT" -> "BTC", "BTC-USDT" -> "BTC"
                    raw_symbol = pos.get('symbol', '')
                    symbol = raw_symbol.replace('/', '').replace('-', '').replace(':USDT', '').replace('USDT', '')
                    
                    # Normalize side: 'long' or 'short'
                    # If side is missing but size is positive/negative, infer it
                    side = pos.get('side', '').lower() if pos.get('side') else ('long' if float(pos.get('size', 0) or pos.get('position_amt', 0) or 0) >= 0 else 'short')
                    size = float(pos.get('size', 0) or pos.get('position_amt', 0))
                    
                    # 只记录有实际持仓的
                    if size != 0:
                        exchange_pos_set.add((symbol, side))

                logger.info(f"[BINANCE] 交易所实际持仓 (normalized_symbol, side): {exchange_pos_set}")

                # 查询项目中所有open状态的持仓
                from backend.database.models import BinancePosition
                open_positions = db.query(BinancePosition).filter(
                    BinancePosition.account_id == account_id,
                    BinancePosition.status == 'open'
                ).all()

                logger.info(f"[BINANCE] 项目中open持仓: {len(open_positions)} 个")

                # 🔥 改进：添加缺失的持仓（用户手动开的） # 构建现有数据库持仓的符号集合
                existing_symbols = set()
                for binance_pos in open_positions:
                    symbol = binance_pos.symbol.replace('/', '').replace('-', '').replace(':USDT', '').replace('USDT', '')
                    side = binance_pos.side.lower() if binance_pos.side else 'long'
                    existing_symbols.add((symbol, side))

                # 检查交易所持仓中是否有数据库没有的
                for pos in exchange_positions:
                    raw_symbol = pos.get('symbol', '')
                    symbol_norm = raw_symbol.replace('/', '').replace('-', '').replace(':USDT', '').replace('USDT', '')
                    side = pos.get('side', '').lower() if pos.get('side') else ('long' if float(pos.get('size', 0) or pos.get('position_amt', 0) or 0) >= 0 else 'short')
                    size = float(pos.get('size', 0) or pos.get('position_amt', 0))

                    if size != 0 and (symbol_norm, side) not in existing_symbols:
                        # 交易所有但数据库没有，创建新记录
                        logger.info(f"[BINANCE] 添加缺失的持仓到数据库: {raw_symbol} ({side}) size={size}")
                        new_pos = BinancePosition(
                            account_id=account_id,
                            symbol=raw_symbol,
                            position_id=f"autosync_{int(datetime.now().timestamp())}_{symbol_norm}",
                            side=side,
                            size=abs(size),
                            entry_price=float(pos.get('entryPrice', 0) or 0),
                            leverage=int(pos.get('leverage', 1) or 1),
                            unrealized_pnl=float(pos.get('unrealizedProfit', 0) or 0),
                            status='open',
                            source='auto_sync',
                            created_at=datetime.now()
                        )
                        db.add(new_pos)
                        existing_symbols.add((symbol_norm, side))
                        synced_count += 1

                analytics_db = AnalyticsSessionLocal()
                for binance_pos in open_positions:
                    # 🔥 同样对数据库中的symbol进行归一化处理
                    raw_db_symbol = binance_pos.symbol
                    symbol = raw_db_symbol.replace('/', '').replace('-', '').replace(':USDT', '').replace('USDT', '')
                    side = binance_pos.side.lower() if binance_pos.side else 'long'

                    # 如果交易所没有这个持仓(匹配symbol和side)，标记为closed
                    if (symbol, side) not in exchange_pos_set:
                        logger.info(f"[BINANCE] 同步关闭持仓: {raw_db_symbol} (归一化: {symbol}) {side} (交易所已无此持仓)")
                        binance_pos.status = 'closed'
                        binance_pos.closed_at = func.now()

                        # 🔥 创建平仓决策记录，这样前端就会过滤掉这个持仓 # 查找该symbol最近的开仓决策 # 兼容处理：支持 "ETH" 和 "ETH/USDT" 两种格式
                        simple_symbol = binance_pos.symbol.replace('/USDT', '').replace('USDT', '')
                        
                        latest_open = analytics_db.query(AIDecisionLog).filter(
                            AIDecisionLog.account_id == account_id,
                            or_(
                                AIDecisionLog.symbol == binance_pos.symbol,
                                AIDecisionLog.symbol == simple_symbol
                            ),
                            AIDecisionLog.operation.in_(["buy", "sell"]),
                            AIDecisionLog.executed == "true"
                        ).order_by(desc(AIDecisionLog.decision_time)).first()

                        if latest_open:
                            # 检查是否已经有对应的平仓记录
                            existing_close = analytics_db.query(AIDecisionLog).filter(
                                AIDecisionLog.account_id == account_id,
                                or_(
                                    AIDecisionLog.symbol == binance_pos.symbol,
                                    AIDecisionLog.symbol == simple_symbol
                                ),
                                AIDecisionLog.operation == "close",
                                AIDecisionLog.executed == "true",
                                AIDecisionLog.decision_time > latest_open.decision_time
                            ).first()

                            if not existing_close:
                                # 创建平仓记录 # 使用原始决策中的symbol格式，保持一致性
                                target_symbol = latest_open.symbol
                                
                                close_decision = AIDecisionLog(
                                    account_id=account_id,
                                    symbol=target_symbol,
                                    operation="close",
                                    target_portion=1.0,
                                    reason=f"同步平仓: {symbol} {side} 在交易所已平仓",
                                    executed="true",
                                    # FIX: Use latest_open.decision_snapshot instead of binance_pos.decision_snapshot
                                    decision_snapshot=latest_open.decision_snapshot,
                                    prev_portion=0,
                                    total_balance=0
                                )
                                analytics_db.add(close_decision)
                                logger.info(f"[BINANCE] 创建平仓记录: symbol={target_symbol}")

                        synced_count += 1

                # ============ Step 3: Sync AIDecisionLog directly (Fix Orphaned Logs) ============
                # Logic: Find AI logs that think they are open but have no corresponding position on Exchange
                
                # 1. Get recent executed decisions (buy/sell) to identify potential open AI positions
                # Look back 30 days to capture active trading sessions
                recent_cutoff = datetime.now() - timedelta(days=30)
                
                potential_open_logs = analytics_db.query(AIDecisionLog).filter(
                    AIDecisionLog.account_id == account_id,
                    AIDecisionLog.executed == 'true',
                    AIDecisionLog.operation.in_(['buy', 'sell']),
                    AIDecisionLog.decision_time > recent_cutoff
                ).order_by(desc(AIDecisionLog.decision_time)).all()
                
                # 2. Filter for truly open logs (latest action is open, and no subsequent close)
                # ai_open_symbols = {} # REMOVED: Don't limit to one per symbol
                # processed_symbols = set() # REMOVED: Process all logs

                logger.info(f"[BINANCE] Checking {len(potential_open_logs)} potential open AI logs for zombies...")

                for log in potential_open_logs:
                    # REMOVED: if log.symbol in processed_symbols: continue
                    # processed_symbols.add(log.symbol)
                    
                    # Check if this "open" has been closed by a subsequent "close"
                    has_close = analytics_db.query(AIDecisionLog).filter(
                        AIDecisionLog.account_id == account_id,
                        or_(
                            AIDecisionLog.symbol == log.symbol,
                            AIDecisionLog.symbol == log.symbol.replace('/USDT', '').replace('USDT', '')
                        ),
                        AIDecisionLog.operation == 'close',
                        AIDecisionLog.executed == 'true',
                        AIDecisionLog.decision_time > log.decision_time
                    ).first()
                    
                    if has_close:
                        continue

                    # 3. Check against Exchange Positions
                    # Normalize symbol
                    raw_symbol = log.symbol
                    symbol_norm = raw_symbol.replace('/', '').replace('-', '').replace(':USDT', '').replace('USDT', '')
                    
                    # Determine expected side
                    expected_side = 'long' if log.operation == 'buy' else 'short'
                    
                    # Check if (symbol, side) exists in exchange_pos_set
                    if (symbol_norm, expected_side) not in exchange_pos_set:
                        logger.info(f"[BINANCE] Found orphaned AI position: ID={log.id} {raw_symbol} ({log.operation}) - Not on exchange")
                        
                        # Create Close Record
                        close_decision = AIDecisionLog(
                            account_id=account_id,
                            symbol=raw_symbol,
                            operation="close",
                            target_portion=0.0,
                            reason=f"同步平仓: {raw_symbol} {expected_side} 交易所无此持仓 (Orphaned Fix ID {log.id})",
                            executed="true",
                            decision_snapshot=log.decision_snapshot,
                            prev_portion=0,
                            total_balance=0
                        )
                        analytics_db.add(close_decision)
                        # 🔥 重要：立即Flush，确保下一个循环能查到这条close记录，防止重复创建
                        analytics_db.flush()
                        synced_count += 1
                        logger.info(f"[BINANCE] Created close record for orphaned position: {raw_symbol} (Ref ID {log.id})")

                analytics_db.commit()
                db.commit()
                logger.info(f"[BINANCE] 持仓同步完成: {synced_count} 个持仓被标记为closed")

            except Exception as e:
                error_msg = f"Binance持仓同步出错: {str(e)}"
                errors.append(error_msg)
                logger.error(error_msg, exc_info=True)
            finally:
                if 'analytics_db' in locals():
                    analytics_db.close()

        # ============ 处理 Hyperliquid 持仓同步 ============
        if account.hyperliquid_enabled == "true":
            try:
                client = get_hyperliquid_client(db, account_id)
                exchange_positions = client.get_positions(db, include_timing=False)
                exchange_symbol_set = set()

                for pos in exchange_positions:
                    coin = pos.get('coin')
                    position_size = float(pos.get('szi', 0))
                    if coin and abs(position_size) >= 1e-8:
                        exchange_symbol_set.add(coin)

                logger.info(f"[HYPERLIQUID] 交易所实际持仓: {exchange_symbol_set}")

                # Hyperliquid 不使用 BinancePosition 表，跳过

            except Exception as e:
                error_msg = f"Hyperliquid持仓同步出错: {str(e)}"
                errors.append(error_msg)
                logger.error(error_msg, exc_info=True)

        return {
            "status": "success",
            "synced_count": synced_count,
            "errors": errors,
            "message": f"同步完成，已更新 {synced_count} 个持仓状态"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error syncing positions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts/{account_id}/close-all-positions")
def close_all_positions(
    account_id: int,
    request: ClosePositionRequest,
    db: Session = Depends(get_db)
):
    """
    一键平仓所有持仓 - 支持Hyperliquid和Binance
    """
    try:
        # 获取账户
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="账户不存在")

        closed_positions = []
        total_pnl = 0.0
        errors = []

        # ============ 处理 Binance 所有持仓 ============
        if account.binance_enabled == "true":
            try:
                # 解密API密钥
                api_key, api_secret = decrypt_api_credentials(account.binance_api_credentials)
                market_type = getattr(account, 'binance_market_type', 'futures')
                testnet = getattr(account, 'binance_testnet', 'false') == 'true'

                # 创建客户端
                client = create_binance_client(account.id, api_key, api_secret, market_type, testnet)

                # 🔥 核心修复：先执行一次强制同步，确保数据库是最新的 # 这样可以避免数据库漏掉用户手动开的仓位，导致无法平仓
                try:
                    logger.info("[BINANCE] Pre-close sync started...")
                    exchange_positions = client.get_positions(db)
                    
                    # 快速更新数据库状态
                    from backend.database.models import BinancePosition
                    
                    # 1. 获取所有交易所持仓符号
                    exchange_pos_map = {}
                    for pos in exchange_positions:
                        size = float(pos.get('size', 0) or pos.get('position_amt', 0))
                        if size != 0:
                            symbol = pos.get('symbol', '')
                            exchange_pos_map[symbol] = pos
                            
                    # 2. 检查并添加缺失的持仓
                    for symbol, pos in exchange_pos_map.items():
                        db_pos = db.query(BinancePosition).filter(
                            BinancePosition.account_id == account_id,
                            BinancePosition.symbol == symbol,
                            BinancePosition.status == 'open'
                        ).first()
                        
                        if not db_pos:
                            size = float(pos.get('size', 0) or pos.get('position_amt', 0))
                            logger.info(f"[BINANCE] Auto-sync found missing position {symbol}, adding to DB")
                            new_pos = BinancePosition(
                                account_id=account_id,
                                symbol=symbol,
                                position_id=f"autosync_{int(datetime.now().timestamp())}_{symbol}",
                                side='long' if size > 0 else 'short',
                                size=abs(size),
                                entry_price=float(pos.get('entryPrice', 0)),
                                leverage=int(pos.get('leverage', 1)),
                                unrealized_pnl=float(pos.get('unrealizedProfit', 0)),
                                status='open',
                                created_at=func.now()
                            )
                            db.add(new_pos)
                            
                    db.commit() # 提交同步更改
                    logger.info("[BINANCE] Pre-close sync completed")
                    
                except Exception as sync_err:
                    logger.warning(f"[BINANCE] Pre-close sync failed, proceeding with DB state: {sync_err}")

                # 获取所有开放持仓
                binance_positions = db.query(BinancePosition).filter(
                    BinancePosition.account_id == account_id,
                    BinancePosition.status == 'open'
                ).all()

                logger.info(f"[BINANCE] Found {len(binance_positions)} open positions to close")

                for binance_pos in binance_positions:
                    try:
                        symbol = binance_pos.symbol
                        close_amount = float(binance_pos.size)

                        logger.info(f"[BINANCE] Closing position {binance_pos.position_id}: {symbol} ({close_amount})")

                        # 执行平仓
                        result = client.close_position(db, symbol, close_amount)

                        if result and result.get("status") == "success":
                            # 更新持仓状态
                            binance_pos.status = 'closed'
                            binance_pos.closed_at = func.now()
                            if 'unrealized_pnl' in result:
                                binance_pos.realized_pnl = result.get('unrealized_pnl', 0)
                                total_pnl += float(result.get('unrealized_pnl', 0))

                            closed_positions.append({
                                "symbol": symbol,
                                "position_id": binance_pos.position_id,
                                "realized_pnl": binance_pos.realized_pnl
                            })

                            logger.info(f"[BINANCE] Successfully closed {binance_pos.position_id}")
                        else:
                            error_msg = result.get("message", "平仓失败") if result else "平仓失败"
                            errors.append(f"{symbol} ({binance_pos.position_id}): {error_msg}")
                            logger.error(f"[BINANCE] Failed to close {binance_pos.position_id}: {error_msg}")

                    except Exception as e:
                        error_msg = f"{binance_pos.symbol}: {str(e)}"
                        errors.append(error_msg)
                        logger.error(f"[BINANCE] Error closing position {binance_pos.position_id}: {e}", exc_info=True)

                # 提交数据库更改
                db.commit()

            except Exception as e:
                error_msg = f"Binance批量平仓出错: {str(e)}"
                errors.append(error_msg)
                logger.error(error_msg, exc_info=True)

        # ============ 处理 Hyperliquid 所有持仓 ============
        if account.hyperliquid_enabled == "true":
            try:
                client = get_hyperliquid_client(db, account_id)
                positions = client.get_positions(db, include_timing=False)

                for pos in positions:
                    try:
                        coin = pos.get('coin')
                        position_size = float(pos.get('szi', 0))

                        # 跳过无持仓
                        if not coin or abs(position_size) < 1e-8:
                            continue

                        # 确定平仓方向
                        is_buy = position_size < 0
                        size = abs(position_size)

                        # 获取当前价格
                        from services.market_data import get_last_price
                        current_price = get_last_price(coin, "hyperliquid")

                        # 读取全局保证金模式
                        from backend.database.models import SystemConfig
                        _m_cfg2 = db.query(SystemConfig).filter(SystemConfig.key == "global_margin_mode").first()
                        _is_cross2 = _m_cfg2.value == "cross" if _m_cfg2 else False

                        result = client.place_order(
                            db=db,
                            symbol=coin,
                            is_buy=is_buy,
                            size=size,
                            order_type="market",
                            price=current_price,
                            reduce_only=True,
                            is_cross=_is_cross2,
                        )

                        if result and result.get("status") == "success":
                            closed_positions.append({
                                "symbol": coin,
                                "position_id": result.get("order_id"),
                                "realized_pnl": pos.get('unrealized_pnl', 0)
                            })
                            total_pnl += float(pos.get('unrealized_pnl', 0))
                        else:
                            error_msg = result.get("message", "平仓失败") if result else "平仓失败"
                            errors.append(f"{coin}: {error_msg}")

                    except Exception as e:
                        errors.append(f"{coin}: {str(e)}")
                        logger.error(f"Error closing Hyperliquid position: {e}", exc_info=True)

            except Exception as e:
                errors.append(f"Hyperliquid批量平仓出错: {str(e)}")
                logger.error(f"Error in Hyperliquid batch close: {e}", exc_info=True)

        # 返回结果
        return {
            "status": "success" if len(errors) == 0 else "partial_success",
            "closed_count": len(closed_positions),
            "total_pnl": total_pnl,
            "closed_positions": closed_positions,
            "errors": errors if errors else None,
            "message": f"已平仓 {len(closed_positions)} 个持仓" + (f"，{len(errors)} 个失败" if errors else "")
        }

    except Exception as e:
        logger.error(f"Error in close_all_positions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/accounts/{account_id}/statistics")
def get_trading_statistics(
    account_id: int,
    days: int = 7,
    db: Session = Depends(get_db)
):
    """
    获取交易统计信息

    - 最近N天的决策统计
    - 执行成功率
    - 盈亏统计
    """
    try:
        since_date = datetime.now(timezone.utc) - timedelta(days=days)

        # 查询决策记录
        analytics_db = AnalyticsSessionLocal()
        try:
            decisions = analytics_db.query(AIDecisionLog).filter(
                AIDecisionLog.account_id == account_id,
                AIDecisionLog.decision_time >= since_date,
                AIDecisionLog.operation.in_(["buy", "sell", "close"])
            ).all()
        finally:
            analytics_db.close()

        total_decisions = len(decisions)
        executed_decisions = sum(1 for d in decisions if d.executed == "true")

        # 按操作类型统计
        buy_count = sum(1 for d in decisions if d.operation == "buy")
        sell_count = sum(1 for d in decisions if d.operation == "sell")
        close_count = sum(1 for d in decisions if d.operation == "close")

        # 最近决策
        recent_decisions = []
        for decision in decisions[:10]:
            recent_decisions.append({
                "id": decision.id,
                "time": decision.decision_time.isoformat() if decision.decision_time else None,
                "operation": decision.operation,
                "symbol": decision.symbol,
                "executed": decision.executed == "true",
                "reason": decision.reason
            })

        return {
            "period_days": days,
            "total_decisions": total_decisions,
            "executed_decisions": executed_decisions,
            "execution_rate": f"{executed_decisions / total_decisions * 100:.1f}%" if total_decisions > 0 else "0%",
            "operation_breakdown": {
                "buy": buy_count,
                "sell": sell_count,
                "close": close_count
            },
            "recent_decisions": recent_decisions
        }

    except Exception as e:
        logger.error(f"Error getting statistics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
