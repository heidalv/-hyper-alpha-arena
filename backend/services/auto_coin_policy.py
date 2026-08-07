"""AI 自动选币 vs 训练核心币 — 策略分界。

训练核心币（BTC/ETH/SOL/BNB/ASTER 等）按重点训练口径：
  - 正常仓位与门禁
  - 不计入 auto_coin_symbols 严选池
  - 自动选币扫描不会注入这些币
"""

from __future__ import annotations

from typing import Iterable, List, Set


def training_core_symbols() -> Set[str]:
    from backend.config.settings import TRAINING_CORE_SYMBOLS

    return {str(s).strip().upper() for s in TRAINING_CORE_SYMBOLS if s}


def is_training_core_symbol(symbol: str) -> bool:
    return str(symbol or "").strip().upper() in training_core_symbols()


def applies_strict_auto_coin_rules(symbol: str, auto_coin_symbols: Iterable[str]) -> bool:
    """是否对该 symbol 启用自动选币严选（缩仓 + 加严门禁）。"""
    sym = str(symbol or "").strip().upper()
    if is_training_core_symbol(sym):
        return False
    auto_set = {str(s).strip().upper() for s in (auto_coin_symbols or []) if s}
    return sym in auto_set


def filter_strict_auto_symbols(symbols: Iterable[str]) -> List[str]:
    """从 auto_coin 列表中剔除训练核心币。"""
    core = training_core_symbols()
    return sorted(
        {str(s).strip().upper() for s in symbols if s and str(s).strip().upper() not in core}
    )
