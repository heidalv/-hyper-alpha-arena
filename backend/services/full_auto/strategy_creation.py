"""策略自动创建 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StrategyCreationHost:
    strategy_creation_ts: Dict[str, float] = field(default_factory=dict)
    STRATEGY_CREATION_COOLDOWN: float = 600.0

    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    session_trading_mode: Callable = field(repr=False, default=lambda *a, **k: "paper")


def build_strategy_creation_host(svc) -> StrategyCreationHost:
    return StrategyCreationHost(
        strategy_creation_ts=getattr(svc, "_strategy_creation_ts", None) or {},
        STRATEGY_CREATION_COOLDOWN=float(getattr(svc, "_STRATEGY_CREATION_COOLDOWN", 600) or 600),
        append_event=svc._append_event,
        get_trading_account_id=svc._get_trading_account_id,
        session_trading_mode=svc._session_trading_mode,
    )


def try_create_from_template(db, symbol: str, tier: str,
                            account_id: int, risk_level: str,
                            trading_mode: str) -> Optional[str]:
    try:
        from backend.database.models import StrategyTemplate
        # 查找匹配 tier 的高评分模板（rating >= 3.0）
        candidates = db.query(StrategyTemplate).filter(
            StrategyTemplate.is_active == True,
            StrategyTemplate.rating >= 3.0,
        ).order_by(StrategyTemplate.rating.desc()).limit(10).all()
        for tpl in candidates:
            # 检查 tier 是否匹配
            tpl_tier = (tpl.tier or "").strip().lower()
            if tpl_tier and tpl_tier != tier.lower():
                continue
            # 检查 symbol 是否在模板的 strategy_config 中
            tpl_config = tpl.strategy_config or {}
            tpl_symbols = tpl_config.get("symbols", []) or []
            if tpl_symbols and symbol not in tpl_symbols:
                continue
            # 使用 strategy_library 从模板创建策略
            from backend.services.strategy_library import strategy_library
            strat = strategy_library.create_strategy_from_template(
                db=db,
                template_id=tpl.template_id,
                account_id=account_id,
                symbol=symbol,
            )
            if strat and hasattr(strat, 'strategy_id'):
                logger.info(
                    f"[FullAuto] P1-4: 模板优先创建策略 "
                    f"{strat.strategy_id[:8]}... (tpl={tpl.template_id[:8]}, "
                    f"rating={tpl.rating}, tier={tpl_tier}) for {symbol}/{tier}"
                )
                return strat.strategy_id
            elif strat:
                return str(strat)
    except Exception as _tpl_err:
        logger.debug(f"[FullAuto] P1-4 模板优先创建失败(回退auto): {_tpl_err}")
    return None

def auto_create_strategy(db, session, symbol: str,
                        market_info: dict,
                        host: StrategyCreationHost,
                        _account_id: int = None,
                        _risk_level: str = None,
                        _trading_mode: str = None,
                        _symbols: list = None) -> Optional[str]:
    from backend.database.connection import SessionLocal
    import time as _time

    account_id = _account_id
    if not account_id and session:
        if (getattr(session, "trading_mode", "") or "").lower() == "paper":
            account_id = getattr(session, "paper_account_id", None) or session.account_id
        else:
            account_id = session.account_id
    risk_level = _risk_level or (session.risk_level if session else "moderate")
    trading_mode = _trading_mode or (session.trading_mode if session else "paper")
    symbols = _symbols or (list(session.symbols or []) if session else [symbol])

    if not account_id:
        logger.error(f"[FullAuto] 无法创建策略: 缺少 account_id")
        return None

    MAX_RETRIES = 2
    _slot = market_info.pop("_force_slot", None) or infer_timeframe_slot(market_info)

    for attempt in range(MAX_RETRIES + 1):
        independent_db = SessionLocal()
        try:
            from backend.api.ai_strategy_routes import auto_launch_strategy, AutoLaunchRequest
            from backend.database.models import AIStrategy as _AIStrategy

            # 【硬限制】创建前再次查 DB，确认该 symbol:tier 没有 active/paused 策略
            # 使用 timeframe_tier 字段精确匹配（而非名称字符串推断）
            dup_check = independent_db.query(_AIStrategy).filter(
                _AIStrategy.account_id == account_id,
                _AIStrategy.primary_symbol == symbol,
                _AIStrategy.status.in_(["active", "paused"]),
                _AIStrategy.timeframe_tier == _slot,
            ).first()
            if dup_check:
                logger.info(
                    f"[FullAuto] {symbol}/{_slot} 已存在策略 "
                    f"{dup_check.strategy_id[:8]}...（tier={dup_check.timeframe_tier}），跳过创建"
                )
                return None

            # P1-4: 模板优先 — 查找匹配的高分模板替代自动策略
            _template_sid = try_create_from_template(
                independent_db, symbol, _slot, account_id, risk_level, trading_mode
            )
            if _template_sid:
                return _template_sid

            existing_strats = independent_db.query(_AIStrategy).filter(
                _AIStrategy.account_id == account_id,
                _AIStrategy.status.in_(["active", "paused"]),
                _AIStrategy.auto_mode == "full_auto",
            ).all()
            used_pct = sum(getattr(s, "max_position_size", 0.1) or 0.1 for s in existing_strats)
            available_pct = max(0.05, 1.0 - used_pct)

            num_symbols = max(len(symbols), 1)
            capital_pct = round(min(1.0 / num_symbols, available_pct), 2)

            req = AutoLaunchRequest(
                account_id=account_id,
                target_symbols=[symbol],
                risk_preference=risk_level or "moderate",
                capital_pct=capital_pct,
                trading_mode=trading_mode or "paper",
                timeframe_slot=_slot,
            )

            result = auto_launch_strategy(request=req, db=independent_db)

            if hasattr(result, 'strategy_id'):
                return result.strategy_id
            elif isinstance(result, dict) and result.get('strategy_id'):
                return result['strategy_id']
            else:
                logger.warning(f"[FullAuto] 创建策略返回异常: {result}")
                return None
        except Exception as e:
            err_msg = str(e).lower()
            is_db_lock = "database is locked" in err_msg or "pendingrollback" in err_msg
            if is_db_lock and attempt < MAX_RETRIES:
                logger.warning(f"[FullAuto] 为 {symbol} 创建策略遇到DB锁，{attempt+1}/{MAX_RETRIES} 重试...")
                try:
                    independent_db.rollback()
                except Exception:
                    pass
                independent_db.close()
                _time.sleep(2 + attempt * 3)
                continue
            logger.error(f"[FullAuto] 为 {symbol} 创建策略失败: {e}", exc_info=True)
            return None
        finally:
            try:
                independent_db.close()
            except Exception:
                pass
    return None

    # ── _NATURE_TO_SLOT 映射 ──
    _NATURE_TO_SLOT = {
    "scalp": "short",
    "intraday": "short",
    "swing": "mid",
    "trend_follow": "long",
    "position": "long",
    }

def infer_timeframe_slots(market_info: dict) -> list:
    if not isinstance(market_info, dict):
        return ["short", "mid", "long"]

    cycle = market_info.get("market_cycle", "")

    # 强趋势行情：long + mid（跟趋势+抓回调）
    if cycle in ("bull", "bear"):
        return ["long", "mid"]
    # 震荡行情：short + mid（快进快出+区间波段）
    elif cycle in ("sideways", "ranging"):
        return ["short", "mid"]
    # 高波动/突破：全周期覆盖
    elif cycle in ("volatile", "breakout"):
        return ["short", "mid", "long"]

    # 默认：全周期覆盖
    return ["short", "mid", "long"]

def infer_timeframe_slot(market_info: dict) -> str:
    """兼容旧调用：返回单个最优 slot"""
    slots = infer_timeframe_slots(market_info)
    return slots[0] if slots else "mid"


def bg_create_strategy(session_id: str, account_id: int, symbol: str,
                      market_info: dict,
                      risk_level: str, trading_mode: str, symbols: list,
                      reason: str) -> None:
    logger.warning("[FullAuto-BG] _bg_create_strategy 已废弃，不应被调用")
