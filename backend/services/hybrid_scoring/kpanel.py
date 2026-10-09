# -*- coding: utf-8 -*-
"""K线面板装载 —— alpha_market Postgres 直连（无 RLS，规避 alpha_arena 行级安全）。

要点：
    - 连接串复用 real_factor_backtest 的解析纪律（MARKET_DATABASE_URL，+psycopg 前缀剥离）；
    - 查询一律走 (symbol, period, exchange, timestamp) 等值+范围条件（索引友好），
      全库 62M 行，禁无索引聚合（数据中心实测全表 GROUP BY 5min+）；
    - 交易所优先级 binance > bybit > okx > hyperliquid > asterdex，按币逐个回退；
    - symbol 归一化与 coin_rank.features.norm_sym 同规则（剥 USDT/PERP 后缀）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_EXCHANGE_PREF = ("binance", "bybit", "okx", "hyperliquid", "asterdex")

# [2026-09-17 实测] symbol_catalog 各所均含代币化股票/金属/指数永续（SKHYNIX/SNDK/
# SOXL/MU/DRAM/TAC…），加密选币宇宙必须语义排除。沿 08-26 fail-closed 清单先例强化，
# env HYBRID_SCORE_STOCK_BLACKLIST 可追加（逗号分隔）。
_STOCK_BLACKLIST = frozenset({
    "SKHYNIX", "SNDK", "SOXL", "MU", "DRAM", "TAC", "XAU", "XAG", "SPX", "NDX",
    "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "PLTR", "COIN",
    "HOOD", "MSTR", "SPY", "QQQ", "SOXX", "SMH", "GLD", "SLV", "AMD", "INTC",
    "AVGO", "SMCI", "ARM", "SNOW", "CRWD", "NFLX", "BABA", "JD", "PDD", "LCID",
    "RIVN", "MARA", "RIOT", "CLSK", "CORZ", "BTBT",
})


def _stock_blacklist() -> frozenset:
    extra = os.environ.get("HYBRID_SCORE_STOCK_BLACKLIST", "").strip()
    if not extra:
        return _STOCK_BLACKLIST
    return _STOCK_BLACKLIST | {norm_sym(x) for x in extra.split(",") if x.strip()}

_conn_lock = threading.Lock()
_cached_url: Optional[str] = None


def _resolve_db_url() -> str:
    global _cached_url
    with _conn_lock:
        if _cached_url:
            return _cached_url
    url = os.environ.get("MARKET_DATABASE_URL", "").strip()
    if not url:
        # 兼容 .env 未进环境时的最后一搏（backend 惯例：仓库根 .env）
        try:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            with open(os.path.join(root, ".env"), encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.strip().startswith("MARKET_DATABASE_URL"):
                        url = line.strip().split("=", 1)[1].strip().strip('"')
                        break
        except Exception:
            url = ""
    if not url:
        raise RuntimeError("MARKET_DATABASE_URL 未配置（kpanel 无法装载K线面板）")
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    with _conn_lock:
        _cached_url = url
    return url


def norm_sym(s: str) -> str:
    u = (s or "").upper().strip()
    for suf in ("USDT", "USD", "-PERP", "PERP"):
        if u.endswith(suf) and len(u) > len(suf):
            u = u[: -len(suf)]
    return u.replace("-", "").replace("/", "")


def _connect():
    import psycopg

    return psycopg.connect(_resolve_db_url(), connect_timeout=8)


def _sym_variants(symbol: str) -> List[str]:
    s = norm_sym(symbol)
    return [s, f"{s}USDT", f"{s}-USDT", f"{s}-PERP", f"{s}PERP"]


def load_klines(symbol: str, period: str = "1d", bars: int = 400,
                exchange: Optional[str] = None) -> pd.DataFrame:
    """取单币 OHLCV（时间升序，列：open/high/low/close/volume，index=datetime）。

    优先级：指定 exchange → 逐个偏好所。任何一步失败返回空 DataFrame（fail-safe）。
    """
    last_err = ""
    for ex in ([exchange] if exchange else _EXCHANGE_PREF):
        for sym in _sym_variants(symbol):
            try:
                with _connect() as con:
                    cur = con.cursor()
                    cur.execute("SET statement_timeout = '20000'")
                    cur.execute(
                        "SELECT timestamp, open_price, high_price, low_price, close_price, volume "
                        "FROM crypto_klines WHERE symbol=%s AND period=%s AND exchange=%s "
                        "ORDER BY timestamp DESC LIMIT %s",
                        (sym, period, ex, int(bars)),
                    )
                    rows = cur.fetchall()
                if not rows:
                    continue
                rows.reverse()
                df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
                df["datetime"] = pd.to_datetime(df["ts"], unit="s")
                df = df.set_index("datetime").sort_index()
                df = df[~df.index.duplicated(keep="last")]
                return df[["open", "high", "low", "close", "volume"]].astype(float)
            except Exception as e:  # noqa: BLE001
                last_err = f"{ex}/{sym}: {e}"
                continue
    logger.debug("[HybridScore.kpanel] %s 无K线（%s）", symbol, last_err[:120])
    return pd.DataFrame()


def _catalog_symbols() -> set:
    """crypto catalog 白名单（symbol_catalog 全所在市交易对）——宇宙输入侧清洗。

    [2026-09-17] 训练实测 top60 混入 SNDK/SKHYNIX/SOXL/MU 等代币化股票行情，沿 08-26
    选币重设计②的纪律：输入侧过滤；catalog 本身含股票永续，故叠加语义黑名单。
    """
    bl = _stock_blacklist()
    try:
        with _connect() as con:
            cur = con.cursor()
            cur.execute("SET statement_timeout = '15000'")
            cur.execute("SELECT DISTINCT symbol FROM symbol_catalog WHERE status='trading'")
            return {norm_sym(r[0]) for r in cur.fetchall() if r[0]} - bl
    except Exception as e:  # noqa: BLE001
        logger.warning("[HybridScore.kpanel] catalog 白名单读取失败（不过滤，风险自负）: %s", str(e)[:120])
        return set()


def _crypto_whitelist() -> frozenset:
    """正向策展加密白名单：板块映射(56) ∪ 流动性偏好(30) ∪ 核心观察(18) ∪ env 扩展。

    [2026-09-17] 结构判别均不可行（catalog 含代币化股票、klines.market 恒为 CRYPTO），
    v1 训练宇宙 = 流动性 top ∩ 本白名单 − 股票黑名单。L1 200+ 扩容需接入外部
    crypto 资产主数据（TODO，见实施记录）。
    """
    pos: set = set()
    try:
        from backend.services.auto_coin_sectors import SYMBOL_SECTOR_MAP
        pos |= {norm_sym(s) for s in SYMBOL_SECTOR_MAP.keys()}
    except Exception:
        pass
    try:
        from backend.services.coin_rank.features import _LIQUID_PREF
        pos |= {norm_sym(s) for s in _LIQUID_PREF}
    except Exception:
        pass
    try:
        from backend.api.data_center_routes import _DEFAULT_UNIVERSE
        pos |= {norm_sym(s) for s in _DEFAULT_UNIVERSE}
    except Exception:
        pos |= {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "ADA", "TON"}
    extra = os.environ.get("HYBRID_SCORE_CRYPTO_WHITELIST", "").strip()
    if extra:
        pos |= {norm_sym(x) for x in extra.split(",") if x.strip()}
    return frozenset(pos)


def top_liquid_symbols(limit: int = 30, days: int = 30, period: str = "1d") -> List[str]:
    """近 N 日成交额 top 币（索引友好的限定聚合，带超时与静态兜底，过 catalog 白名单）。"""
    try:
        with _connect() as con:
            cur = con.cursor()
            cur.execute("SET statement_timeout = '90000'")
            cur.execute(
                "SELECT symbol, SUM(volume * close_price) AS vol_usd "
                "FROM crypto_klines WHERE period=%s AND timestamp > EXTRACT(EPOCH FROM NOW()) - %s * 86400 "
                "GROUP BY symbol ORDER BY vol_usd DESC NULLS LAST LIMIT %s",
                (period, int(days), int(limit) * 3),
            )
            rows = cur.fetchall()
        allowed = _catalog_symbols() or set()
        wl = _crypto_whitelist()
        bl = _stock_blacklist()
        syms = [norm_sym(r[0]) for r in rows
                if r[0] and norm_sym(r[0]) not in bl
                and norm_sym(r[0]) in allowed
                and norm_sym(r[0]) in wl]
        if len(syms) >= max(5, limit // 3):
            return syms[:limit]
    except Exception as e:  # noqa: BLE001
        logger.warning("[HybridScore.kpanel] 流动性聚合失败，回退静态主流币表: %s", str(e)[:120])
    # 兜底：与 coin_rank.features._LIQUID_PREF 同源的静态表（数据中心应急先例）
    fallback = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "DOT", "ATOM",
                "NEAR", "APT", "SUI", "ARB", "OP", "INJ", "TIA", "SEI", "AAVE", "UNI",
                "LTC", "FIL", "RENDER", "FET", "ONDO", "HYPE", "WIF", "TON", "ADA", "TRX")
    return list(fallback[:limit])
