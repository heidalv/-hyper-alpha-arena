"""
已废弃 — 请改用 backend.services.trading_pairs_config

保留本文件仅为避免旧 import 路径立即报错；新代码勿再引用 CORE_SYMBOLS / DEFAULT_TRADING_PAIRS。
"""

from backend.services.trading_pairs_config import (
    INITIAL_SEED_TRADING_PAIRS,
    get_user_trading_pairs,
)

__all__ = [
    "INITIAL_SEED_TRADING_PAIRS",
    "get_user_trading_pairs",
]
