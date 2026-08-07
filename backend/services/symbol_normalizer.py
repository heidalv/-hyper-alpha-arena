"""
symbol_normalizer — 交易对 symbol 统一规范化（全局唯一真相）

背景：
系统历史上存在多种 symbol 格式并存（BTC / BTCUSDT / BTC-USDT / BTC/USDT /
BTC/USDT:USDT / BTC-PERP / btc），导致同一交易对在不同表/接口中以多个
"身份"出现，产生重复条目与价格口径冲突。

本模块定义**唯一**规范化规则：任何输入 symbol → 纯 BASE 大写格式。
所有写入端（kline collector / ticker poller / market_flow / symbol_catalog）
与查询端（/overview/all、data_center、各 API）必须经此函数归一化，
保证同一「交易所 + 交易对」全局唯一。

保留规则：
- 1000SATS / 1000PEPE / 1000000MOG 等带数量前缀的真实币种原样保留
- BTCDOM / PUMPBTC / ETHBTC 等独立交易标的原样保留（与 BTC 不是同一交易对）
- 仅去除交易对语法层：分隔符（- / _ :）、PERP 后缀、USDT/USDC/USD 报价后缀
"""

from __future__ import annotations

import re
from typing import Optional

# 报价后缀按长度降序，优先去掉更具体的
_QUOTE_SUFFIXES = ("USDT", "USDC", "USD")

_SEP_RE = re.compile(r"[-/_:]")


def normalize_symbol(symbol: Optional[str]) -> str:
    """任意 symbol → 统一 BASE 大写格式。

    >>> normalize_symbol("BTC")
    'BTC'
    >>> normalize_symbol("btcusdt")
    'BTC'
    >>> normalize_symbol("BTC-USDT")
    'BTC'
    >>> normalize_symbol("BTC/USDT:USDT")
    'BTC'
    >>> normalize_symbol("BTC-PERP")
    'BTC'
    >>> normalize_symbol("1000SATSUSDT")
    '1000SATS'
    >>> normalize_symbol("BTCDOM")
    'BTCDOM'
    """
    if not symbol:
        return ""
    original = str(symbol).strip().upper()
    if not original:
        return ""

    # 1) ccxt 格式 "BTC/USDT:USDT" → 取斜杠前段
    s = original.split("/")[0].split(":")[0].strip()
    # 2) 去剩余分隔符（- _ :）
    s = _SEP_RE.sub("", s)
    # 3) 去 PERP 后缀（BTC-PERP 在去分隔符后为 BTCPERP）
    if s.endswith("PERP") and len(s) > 4:
        s = s[:-4]
    # 4) 去报价后缀（仅当剩余部分非空）
    for suf in _QUOTE_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            s = s[:-len(suf)]
            break

    return s or original


def is_valid_base_symbol(symbol: Optional[str]) -> bool:
    """规范化后是否为合法交易对标识（仅字母数字，长度 2-20）。"""
    norm = normalize_symbol(symbol)
    return bool(norm) and 2 <= len(norm) <= 20 and re.fullmatch(r"[A-Z0-9]+", norm) is not None


def dedupe_symbols(symbols) -> list:
    """按规范化结果去重，保留顺序。"""
    seen = set()
    out = []
    for s in symbols or []:
        norm = normalize_symbol(s)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out
