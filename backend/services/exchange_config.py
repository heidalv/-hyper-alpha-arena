"""
Exchange Configuration Service — 多交易所市场数据源选择

历史：本模块曾硬编码返回 "hyperliquid"，导致 K线/行情/CVD 采集层锁死单所。
现改造为：默认返回 settings.DEFAULT_EXCHANGE（asterdex），并提供按账户解析的接口。

兼容性策略：
- get_active_exchange() 保留（被 ~20 处调用），但不再硬编码 hyperliquid
- get_exchange_for_account(account_id) 新增真实实现（之前缺失导致 ImportError）
- is_binance_active() / set_active_exchange() 保留为兼容桩
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def get_active_exchange() -> str:
    """
    获取当前决策用交易所。

    优先级：
      1. 正在 running 的 full_auto 会话的 active_exchange（AI 交易员可切换）
      2. settings.DEFAULT_EXCHANGE（默认 asterdex）

    注意：调用方有明确 account/session 上下文时，优先用
    get_exchange_for_account / 会话字段；本函数是无上下文时的统一兜底。
    """
    try:
        from sqlalchemy import text as sa_text

        from backend.database.connection import SessionLocal
        with SessionLocal() as db:
            row = db.execute(sa_text(
                "SELECT active_exchange FROM full_auto_sessions "
                "WHERE status = 'running' AND active_exchange IS NOT NULL "
                "AND active_exchange != '' "
                "ORDER BY started_at DESC NULLS LAST LIMIT 1"
            )).first()
            if row and row[0]:
                ex = str(row[0]).strip().lower()
                if ex == "aster":
                    ex = "asterdex"
                if ex:
                    return ex
    except Exception:
        pass
    try:
        from config import settings
        return getattr(settings, "DEFAULT_EXCHANGE", "asterdex") or "asterdex"
    except Exception:
        return "asterdex"


def get_exchange_for_account(account_id: Optional[int] = None) -> str:
    """
    解析某账户实际配置的交易所。

    优先级：
      1. account.selected_exchange（DB 字段，per-trader 绑定）
      2. settings.DEFAULT_EXCHANGE（兜底）

    Args:
        account_id: 账户 ID。None 或查不到时返回全局默认。

    Returns:
        交易所标识字符串（如 "asterdex" / "hyperliquid"）
    """
    if account_id is None:
        return get_active_exchange()
    try:
        from database.connection import SessionLocal
        from database.models import Account
        db = SessionLocal()
        try:
            account = db.query(Account).filter(Account.id == account_id).first()
            if account is not None:
                sel = getattr(account, "selected_exchange", None)
                if sel:
                    return sel.strip().lower()
        finally:
            db.close()
    except Exception as e:
        logger.debug("get_exchange_for_account(%s) 查询失败，用默认: %s", account_id, e)
    return get_active_exchange()


def is_hyperliquid_active() -> bool:
    """检查默认交易所是否为 Hyperliquid。"""
    return get_active_exchange() == "hyperliquid"


def is_binance_active() -> bool:
    """DEPRECATED: 保留兼容桩。Binance 已不再是默认数据源。"""
    return False


def set_active_exchange(exchange: str) -> None:
    """DEPRECATED: 全局激活交易所已改为按账户/会话配置，此函数仅打日志。"""
    logger.warning(
        "set_active_exchange(%s) 已弃用：交易所选择改为 per-account/per-session，"
        "请改用 Account.selected_exchange 或 FullAutoSession.active_exchange",
        exchange,
    )
