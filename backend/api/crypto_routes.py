"""
Crypto-specific API routes
"""
import asyncio
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
import logging

from backend.services.market_data import get_all_symbols, get_last_price, get_market_status
from backend.services.trading_pairs_config import get_user_trading_pairs

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/crypto", tags=["crypto"])


@router.get("/symbols")
async def get_crypto_symbols() -> List[str]:
    """Get all available crypto trading pairs"""
    try:
        symbols = get_all_symbols()
        return symbols
    except Exception as e:
        logger.error(f"Error getting crypto symbols: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/price/{symbol}")
async def get_crypto_price(symbol: str) -> Dict[str, Any]:
    """Get current price for a crypto symbol"""
    try:
        price = get_last_price(symbol, "CRYPTO")
        return {
            "symbol": symbol,
            "price": price,
            "market": "CRYPTO"
        }
    except Exception as e:
        logger.error(f"Error getting price for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{symbol}")
async def get_crypto_market_status(symbol: str) -> Dict[str, Any]:
    """Get market status for a crypto symbol"""
    try:
        status = get_market_status(symbol, "CRYPTO")
        return status
    except Exception as e:
        logger.error(f"Error getting market status for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/popular")
async def get_popular_cryptos() -> List[Dict[str, Any]]:
    """Get popular crypto trading pairs with current prices.

    [2026-08-31 性能] 原实现串行逐币取价：N 个币 × 每个缓存未命中的 DC REST
    往返（0.3-1.2s）叠加，且同步调用阻塞事件循环。改为 asyncio.to_thread
    并发取价（gather 保序），最坏耗时≈单币一次往返。
    """
    popular_symbols = get_user_trading_pairs()

    async def _fetch(symbol: str) -> Dict[str, Any] | None:
        try:
            price = await asyncio.to_thread(get_last_price, symbol, "CRYPTO")
            return {
                "symbol": symbol,
                "name": symbol.split("/")[0],  # Extract base currency
                "price": price,
                "market": "CRYPTO",
            }
        except Exception as e:
            logger.warning(f"Could not get price for {symbol}: {e}")
            return None

    results = await asyncio.gather(*(_fetch(s) for s in popular_symbols))
    return [r for r in results if r is not None]