"""
auto_coin_sectors — 币种板块分类模块

提供轻量级的板块分类，用于自动选币的多样性评分。
"""

# ═══════════════════════════════════════════════════════════════════════════
# 板块映射表
# ═══════════════════════════════════════════════════════════════════════════

SYMBOL_SECTOR_MAP: dict[str, str] = {
    # L1 / 主流
    "BTC": "major", "ETH": "major", "SOL": "major", "BNB": "major",
    "XRP": "major", "ADA": "major", "TRX": "major",

    # DeFi
    "UNI": "defi", "AAVE": "defi", "CRV": "defi", "MKR": "defi",
    "DYDX": "defi", "JUP": "defi", "PENDLE": "defi", "SNX": "defi",
    "COMP": "defi", "LDO": "defi", "ENA": "defi",

    # L2 / 基础设施
    "ARB": "infra", "OP": "infra", "AVAX": "infra", "NEAR": "infra",
    "SUI": "infra", "SEI": "infra", "APT": "infra", "TON": "infra",
    "FTM": "infra", "MATIC": "infra", "INJ": "infra", "TIA": "infra",
    "ATOM": "infra", "DOT": "infra", "LINK": "infra",

    # Meme
    "DOGE": "meme", "SHIB": "meme", "PEPE": "meme", "WIF": "meme",
    "BONK": "meme", "FLOKI": "meme", "BOME": "meme", "KPEPE": "meme",

    # AI
    "FET": "ai", "RENDER": "ai", "VIRTUAL": "ai", "OCEAN": "ai",
    "AKT": "ai",

    # Gaming / NFT
    "IMX": "gaming", "GALA": "gaming", "AXS": "gaming", "SAND": "gaming",
    "ENJ": "gaming", "MAGIC": "gaming",

    # Storage / Oracle / Data
    "FIL": "data", "PYTH": "data", "API3": "data",

    # RWA / Payment
    "ONDO": "rwa", "XRP": "rwa",
}


def get_sector(symbol: str) -> str:
    """获取币种所属板块，未知币种归为 'other'"""
    return SYMBOL_SECTOR_MAP.get(symbol.upper(), "other")


def get_diversity_score(symbol: str, pool_symbols: list[str]) -> float:
    """
    计算币种的多样性评分（0-1）。

    同一板块内已有的币种越多，该币种的多样性评分越低。
    - 板块内无其他币种 → 1.0（高价值，引入多样性）
    - 板块内已有 1 个 → 0.6
    - 板块内已有 2+ 个 → 0.2
    """
    sym_sector = get_sector(symbol)
    same_sector_count = sum(
        1 for s in pool_symbols
        if s != symbol and get_sector(s) == sym_sector
    )
    if same_sector_count == 0:
        return 1.0
    if same_sector_count == 1:
        return 0.6
    return 0.2


def get_pool_sector_summary(pool_symbols: list[str]) -> dict[str, list[str]]:
    """返回当前池的板块分布概览 {sector: [symbols...]}"""
    result: dict[str, list[str]] = {}
    for sym in pool_symbols:
        sector = get_sector(sym)
        result.setdefault(sector, []).append(sym)
    return result
