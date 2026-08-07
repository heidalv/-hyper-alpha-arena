"""
ExchangeManager — 统一交易所连接管理器

职责:
1. 根据 DB ExchangeCredential 记录创建/缓存适配器实例
2. 提供 get_client(exchange, account_id) 统一入口
3. 管理连接健康检查和资源清理
4. Hyperliquid 走独立 HyperliquidWallet 系统，其余统一走 ExchangeCredential
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.services.exchange.base_exchange_client import BaseExchangeClient, ExchangeBalance
from backend.services.exchange.exchange_factory import ExchangeClientFactory

logger = logging.getLogger(__name__)

SUPPORTED_EXCHANGES = ["binance", "bybit", "okx", "gateio", "asterdex"]


class ExchangeManager:
    """多交易所连接管理器"""

    def __init__(self):
        self._clients: Dict[str, BaseExchangeClient] = {}
        self._health: Dict[str, Dict[str, Any]] = {}
        self._last_health_check: float = 0
        self._health_interval = 300  # 5min

    # ── Client Lifecycle ──────────────────────────

    def get_client(self, exchange: str, account_id: int = 0) -> Optional[BaseExchangeClient]:
        """
        获取已缓存的适配器实例。
        key = "exchange:account_id"
        """
        key = f"{exchange}:{account_id}"
        return self._clients.get(key)

    def create_client(
        self,
        exchange: str,
        account_id: int = 0,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        testnet: bool = False,
    ) -> BaseExchangeClient:
        """创建适配器并缓存。"""
        key = f"{exchange}:{account_id}"

        if exchange == "hyperliquid":
            client = self._create_hyperliquid_client(account_id)
        else:
            client = ExchangeClientFactory.create(
                exchange,
                api_key=api_key,
                secret=secret,
                password=password,
                testnet=testnet,
            )

        self._clients[key] = client
        self._health[key] = {
            "exchange": exchange,
            "account_id": account_id,
            "status": "created",
            "last_check": 0,
        }
        return client

    def _create_hyperliquid_client(self, account_id: int) -> BaseExchangeClient:
        """Hyperliquid 走已有的 HyperliquidTradingClient 系统。"""
        try:
            from backend.database.connection import SessionLocal
            from backend.services.hyperliquid_environment import get_hyperliquid_client

            db = SessionLocal()
            try:
                hl_client = get_hyperliquid_client(db, account_id)
            finally:
                db.close()

            from backend.services.exchange.hyperliquid_adapter import HyperliquidAdapter
            return HyperliquidAdapter(existing_client=hl_client)
        except Exception as e:
            logger.warning("Failed to create HL adapter for account %d: %s", account_id, e)
            from backend.services.exchange.hyperliquid_adapter import HyperliquidAdapter
            return HyperliquidAdapter()

    def remove_client(self, exchange: str, account_id: int = 0):
        key = f"{exchange}:{account_id}"
        self._clients.pop(key, None)
        self._health.pop(key, None)

    # ── Bulk Operations ───────────────────────────

    def load_all_from_db(self, db=None):
        """从 DB 加载所有已启用的 ExchangeCredential 并创建客户端。"""
        if db is None:
            try:
                from backend.database.connection import SessionLocal
                db = SessionLocal()
                own_db = True
            except Exception:
                return
        else:
            own_db = False

        try:
            from backend.database.models import ExchangeCredential
            from backend.utils.encryption import decrypt_private_key

            creds = db.query(ExchangeCredential).filter(
                ExchangeCredential.enabled == True  # noqa: E712
            ).all()

            for cred in creds:
                try:
                    api_key = decrypt_private_key(cred.api_key_encrypted) if cred.api_key_encrypted else ""
                    api_secret = decrypt_private_key(cred.api_secret_encrypted) if cred.api_secret_encrypted else ""
                    passphrase = decrypt_private_key(cred.passphrase_encrypted) if cred.passphrase_encrypted else ""

                    self.create_client(
                        exchange=cred.exchange,
                        account_id=cred.account_id,
                        api_key=api_key,
                        secret=api_secret,
                        password=passphrase,
                        testnet=cred.testnet,
                    )
                    logger.info(
                        "Loaded %s client for account %d", cred.exchange, cred.account_id
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to load %s for account %d: %s",
                        cred.exchange, cred.account_id, e,
                    )
        finally:
            if own_db:
                db.close()

    def get_all_clients(self) -> Dict[str, BaseExchangeClient]:
        return dict(self._clients)

    def get_enabled_exchanges(self) -> List[str]:
        """返回已有活跃客户端的交易所列表（去重）。"""
        return list({k.split(":")[0] for k in self._clients})

    def get_or_create_global_client(
        self, exchange: str, user_id: int = 1
    ) -> Optional[BaseExchangeClient]:
        """
        获取或创建全局交易所客户端（按 user_id + exchange 查找凭证）。
        用于 AI 交易员统一执行 — 凭证在交易所配置中全局管理。
        Hyperliquid 不适用此方法（使用独立的 HyperliquidWallet 系统）。
        """
        if exchange == "hyperliquid":
            logger.warning("Hyperliquid uses per-account wallets, not global credentials")
            return None

        # 先查缓存
        cache_key = f"{exchange}:global:{user_id}"
        if cache_key in self._clients:
            return self._clients[cache_key]

        # 从 DB 查全局凭证
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import ExchangeCredential
            from backend.utils.encryption import decrypt_private_key

            db = SessionLocal()
            try:
                cred = db.query(ExchangeCredential).filter(
                    ExchangeCredential.user_id == user_id,
                    ExchangeCredential.exchange == exchange,
                    ExchangeCredential.enabled == True,  # noqa: E712
                ).first()

                if not cred:
                    logger.debug("No global credential found for %s user_id=%d", exchange, user_id)
                    return None

                api_key = decrypt_private_key(cred.api_key_encrypted) if cred.api_key_encrypted else ""
                api_secret = decrypt_private_key(cred.api_secret_encrypted) if cred.api_secret_encrypted else ""
                passphrase = decrypt_private_key(cred.passphrase_encrypted) if cred.passphrase_encrypted else ""

                client = ExchangeClientFactory.create(
                    exchange,
                    api_key=api_key,
                    secret=api_secret,
                    password=passphrase,
                    testnet=cred.testnet,
                )

                self._clients[cache_key] = client
                self._health[cache_key] = {
                    "exchange": exchange,
                    "user_id": user_id,
                    "status": "created",
                    "last_check": 0,
                }
                logger.info("Created global %s client for user_id=%d", exchange, user_id)
                return client
            finally:
                db.close()
        except Exception as e:
            logger.error("Failed to create global %s client: %s", exchange, e)
            return None

    # ── Health Check ──────────────────────────────

    async def check_health(self, exchange: str, account_id: int = 0) -> Dict[str, Any]:
        key = f"{exchange}:{account_id}"
        client = self._clients.get(key)
        if client is None:
            return {"exchange": exchange, "status": "not_configured", "connected": False}

        try:
            balance = await client.get_balance()
            result = {
                "exchange": exchange,
                "account_id": account_id,
                "status": "connected",
                "connected": True,
                "total_equity": balance.total_equity,
                "available_balance": balance.available_balance,
                "last_check": time.time(),
            }
            self._health[key] = result
            return result
        except Exception as e:
            result = {
                "exchange": exchange,
                "account_id": account_id,
                "status": "error",
                "connected": False,
                "error": str(e),
                "last_check": time.time(),
            }
            self._health[key] = result
            return result

    async def check_all_health(self) -> List[Dict[str, Any]]:
        """并行检查所有已注册客户端的连通性。"""
        tasks = []
        for key in list(self._clients.keys()):
            parts = key.split(":")
            exchange = parts[0]
            account_id = int(parts[1]) if len(parts) > 1 else 0
            tasks.append(self.check_health(exchange, account_id))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            r if isinstance(r, dict) else {"status": "error", "error": str(r)}
            for r in results
        ]

    def get_health_report(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._health)

    # ── Funding Rate Comparison ───────────────────

    async def get_cross_exchange_funding_rates(
        self, symbols: Optional[List[str]] = None
    ) -> Dict[str, Dict[str, float]]:
        """
        并行获取所有交易所的资金费率，返回 {exchange: {symbol: rate}}。
        """
        result: Dict[str, Dict[str, float]] = {}
        tasks: List[Tuple[str, Any]] = []

        for key, client in self._clients.items():
            exchange = key.split(":")[0]
            tasks.append((exchange, client.get_all_funding_rates()))

        if not tasks:
            return result

        gathered = await asyncio.gather(
            *[t[1] for t in tasks], return_exceptions=True
        )

        for (exchange, _), rates in zip(tasks, gathered):
            if isinstance(rates, dict):
                if symbols:
                    rates = {s: r for s, r in rates.items() if s in symbols}
                result[exchange] = rates
            else:
                logger.warning("Funding rates from %s failed: %s", exchange, rates)

        return result


# ── Global Singleton ──────────────────────────────

_manager: Optional[ExchangeManager] = None


def get_exchange_manager() -> ExchangeManager:
    global _manager
    if _manager is None:
        _manager = ExchangeManager()
    return _manager
