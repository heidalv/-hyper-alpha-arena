"""
OnchainDataCollector - 链上与宏观数据采集器

采集链上数据（TVL、交易所流量等）和宏观指标（恐惧贪婪指数、BTC主导率），
注入到 unified_data_pool 的 K线 DataFrame 中供因子计算使用。

数据源:
- TVL: DefiLlama API (免费、无限制)
- Fear & Greed: alternative.me API (免费)
- BTC Dominance: CoinGecko API (免费)
- Mempool.space: BTC mempool + fee rate (免费)
- Blockchain.info: BTC 交易统计 (免费, 无需key)
- Etherscan: ETH gas / tx count (免费key, 5 calls/sec)
- Coinglass（v6 2.3 数据源抽象层）: exchange_net_flow / stablecoin_mint_burn
  （免费→付费无缝切换；无 key 时自动跳过，行为与未接入前一致）

缓存策略:
- TVL: 2小时 TTL
- Fear & Greed: 1小时 TTL
- BTC Dominance: 1小时 TTL
- Mempool / Blockchain.info: 30min TTL
- Etherscan: 30min TTL
- 异常时返回零值，不阻塞主流程
"""

import os
import time
import logging
from typing import Dict, List, Any, Tuple, Optional

logger = logging.getLogger(__name__)


class OnchainDataCollector:
    """链上与宏观数据采集器"""

    # 缓存TTL（秒）
    TVL_TTL = 7200          # 2小时
    FEAR_GREED_TTL = 3600   # 1小时
    BTC_DOM_TTL = 3600      # 1小时
    MEMPOOL_TTL = 1800      # 30min
    ETHERSCAN_TTL = 1800    # 30min
    BLOCKCHAIN_TTL = 1800   # 30min

    def __init__(self):
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._etherscan_key: Optional[str] = os.getenv("ETHERSCAN_API_KEY")
        self._coinglass_netflow_ttl = 1800      # 30min（Coinglass 免费额度有限）
        self._coinglass_stable_ttl = 3600       # 1h

    def collect_all(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        采集所有链上+宏观数据

        Args:
            symbols: 交易对列表

        Returns:
            {symbol: {field: value}} 字典
        """
        macro = self._collect_macro()
        btc_chain = self._collect_blockchain_info()
        mempool = self._collect_mempool()
        eth_chain = self._collect_etherscan() if self._etherscan_key else {}
        # v6 2.3：Coinglass 统一 DataProvider（netflow / stablecoin，有 key 才采集）
        cg_chain = self._collect_coinglass()

        result = {}
        for symbol in symbols:
            base = symbol.replace('USDT', '').replace('USDC', '').replace('USD', '')
            tvl = self._collect_tvl(symbol)

            if base == "BTC":
                chain_data = dict(btc_chain)
            elif base == "ETH":
                chain_data = dict(eth_chain)
            else:
                chain_data = {}
            # 合并 Coinglass 链上数据（净流入按资产归属；稳定币为全局字段）
            if cg_chain:
                if base in cg_chain:
                    chain_data['exchange_net_flow'] = cg_chain[base].get('exchange_net_flow')
                if 'stablecoin_mint_burn' in cg_chain:
                    result.setdefault('stablecoin_mint_burn', cg_chain['stablecoin_mint_burn'])

            result[symbol] = {
                'tvl': tvl,
                'fear_greed': macro.get('fear_greed', 50),
                'btc_dominance': macro.get('btc_dominance', 0.0),
                'mempool_size': mempool.get('mempool_size', 0),
                'mempool_fee_rate': mempool.get('fee_rate', 0.0),
                'network_congestion': mempool.get('congestion', 0.0),
            }
            # [2026-07-10 数据修复] exchange_net_flow/whale_tx_*/active_addresses 只在
            # 采集器真实产出时才填（原 .get(key, 0) 会把缺失填成 0，虽被下游 !=0 过滤，
            # 但更干净的做法是根本不输出这些字段，让 AI 明确知道"无此项数据"）。
            # chain_data 现在只含采集器真实拿到的字段（gas_price_gwei/eth_supply/n_tx/total_btc_sent）。
            for _k in ('exchange_net_flow', 'whale_tx_count', 'whale_tx_volume', 'active_addresses'):
                if _k in chain_data and chain_data[_k]:
                    result[symbol][_k] = chain_data[_k]
            # 保留链上采集器真实拿到的其他字段（gas/supply/n_tx/total_btc_sent）
            for _k, _v in chain_data.items():
                if _k not in result[symbol] and _v is not None:
                    result[symbol][_k] = _v

        # 稳定币净铸造挂到全局结果（symbols 首项带出，供因子层取用）
        if symbols and 'stablecoin_mint_burn' in cg_chain:
            result.setdefault('stablecoin_mint_burn', cg_chain['stablecoin_mint_burn'])

        return result

    def _collect_tvl(self, symbol: str) -> float:
        """
        从 DefiLlama 获取 TVL 数据

        Args:
            symbol: 交易对 (如 'ETH')

        Returns:
            TVL 数值，失败返回 0.0
        """
        cache_key = f'tvl_{symbol}'
        cached = self._get_cached(cache_key, self.TVL_TTL)
        if cached is not None:
            return cached

        try:
            import requests
            chain = self._symbol_to_chain(symbol)
            url = f"https://api.llama.fi/v2/historicalChainTvl/{chain}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list):
                    tvl = float(data[-1].get('tvl', 0))
                    self._set_cache(cache_key, tvl)
                    return tvl
        except Exception as e:
            logger.debug(f"[OnchainDataCollector] TVL采集失败({symbol}): {e}")

        return 0.0

    def _collect_coinglass(self) -> Dict[str, Any]:
        """v6 2.3：Coinglass 统一 DataProvider 采集链上净流入 + 稳定币净铸造。

        无 key 时自动跳过（行为与未接入前一致）；有 key 时产出：
          { "BTC": {"exchange_net_flow": float}, "ETH": {...},
            "stablecoin_mint_burn": float }
        每次调用记录到 DataQualityMonitor（链上链路健康卡）。
        """
        try:
            from backend.services.data.data_provider import get_coinglass_provider
            from backend.services.data_quality_monitor import get_data_quality_monitor
            provider = get_coinglass_provider()
        except Exception:
            return {}
        if not provider.has_key:
            return {}

        dq = get_data_quality_monitor()
        out: Dict[str, Any] = {}

        # 1) 交易所净流入（BTC / ETH）
        for asset in ("BTC", "ETH"):
            cache_key = f"coinglass_netflow_{asset}"
            cached = self._get_cached(cache_key, self._coinglass_netflow_ttl)
            if cached is not None:
                out[asset] = {"exchange_net_flow": cached}
                continue
            t0 = time.time()
            nf = provider.fetch_netflow(asset)
            dq.record_source_call(
                "onchain_netflow", success=(nf is not None),
                latency_ms=(time.time() - t0) * 1000,
                error="" if nf is not None else provider.stats.last_error,
            )
            if nf is not None:
                self._set_cache(cache_key, nf)
                out[asset] = {"exchange_net_flow": nf}

        # 2) 稳定币净铸造（USDT；Coinglass 端点未开放时 fetch 返回 None 不阻塞）
        cache_key = "coinglass_stablecoin"
        cached = self._get_cached(cache_key, self._coinglass_stable_ttl)
        if cached is not None:
            out["stablecoin_mint_burn"] = cached
        else:
            t0 = time.time()
            mint = provider.fetch_stablecoin_mint("USDT")
            dq.record_source_call(
                "onchain_stablecoin", success=(mint is not None),
                latency_ms=(time.time() - t0) * 1000,
                error="" if mint is not None else provider.stats.last_error,
            )
            if mint is not None:
                self._set_cache(cache_key, mint)
                out["stablecoin_mint_burn"] = mint

        return out

    def _collect_macro(self) -> Dict[str, Any]:
        """采集宏观数据 (恐惧贪婪指数 + BTC主导率)"""
        return {
            'fear_greed': self._collect_fear_greed(),
            'btc_dominance': self._collect_btc_dominance(),
        }

    def _collect_fear_greed(self) -> float:
        """
        从 alternative.me 获取恐惧贪婪指数

        Returns:
            0-100 的指数值，失败返回 50 (中性)
        """
        cache_key = 'fear_greed'
        cached = self._get_cached(cache_key, self.FEAR_GREED_TTL)
        if cached is not None:
            return cached

        try:
            import requests
            url = "https://api.alternative.me/fng/?limit=1"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                value = float(data['data'][0]['value'])
                self._set_cache(cache_key, value)
                return value
        except Exception as e:
            logger.debug(f"[OnchainDataCollector] Fear&Greed采集失败: {e}")

        return 50.0

    def _collect_btc_dominance(self) -> float:
        """
        从 CoinGecko 获取 BTC 市值占比

        Returns:
            BTC主导率 (0-100)，失败返回 0.0
        """
        cache_key = 'btc_dominance'
        cached = self._get_cached(cache_key, self.BTC_DOM_TTL)
        if cached is not None:
            return cached

        try:
            import requests
            url = "https://api.coingecko.com/api/v3/global"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                dominance = float(data['data']['market_cap_percentage']['btc'])
                self._set_cache(cache_key, dominance)
                return dominance
        except Exception as e:
            logger.debug(f"[OnchainDataCollector] BTC Dominance采集失败: {e}")

        return 0.0

    # ── Blockchain.info (BTC) ──────────────────

    def _collect_blockchain_info(self) -> Dict[str, Any]:
        """BTC chain stats from blockchain.info (free, no key)."""
        cache_key = "blockchain_info"
        cached = self._get_cached(cache_key, self.BLOCKCHAIN_TTL)
        if cached is not None:
            return cached

        result: Dict[str, Any] = {}
        try:
            import urllib.request
            import json

            url = "https://api.blockchain.info/stats"
            req = urllib.request.Request(url, headers={"User-Agent": "HyperAlphaArena/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            # [2026-07-10 数据修复] blockchain.info 只提供 n_tx(交易总数) 和
            # estimated_btc_sent(估算总转账量)，这是真实字段。但原代码把 estimated_btc_sent
            # 的 15% 当 exchange_net_flow、30% 当 whale_tx_volume、n_tx//500 当 whale_tx_count，
            # 这些比例纯属瞎猜，无依据。删除合成拆分，只保留真实字段：
            # n_tx → 作为链上活跃度参考；estimated_btc_sent → 总转账量。
            result["active_addresses"] = int(data.get("n_tx", 0))
            total_sent = float(data.get("estimated_btc_sent", 0)) / 1e8
            result["total_btc_sent"] = total_sent
            self._set_cache(cache_key, result)

        except Exception as e:
            logger.debug(f"[OnchainDataCollector] Blockchain.info failed: {e}")

        return result

    # ── Mempool.space (BTC) ──────────────────

    def _collect_mempool(self) -> Dict[str, Any]:
        """BTC mempool stats from mempool.space (free, no key)."""
        cache_key = "mempool"
        cached = self._get_cached(cache_key, self.MEMPOOL_TTL)
        if cached is not None:
            return cached

        result: Dict[str, Any] = {}
        try:
            import urllib.request
            import json

            url = "https://mempool.space/api/mempool"
            req = urllib.request.Request(url, headers={"User-Agent": "HyperAlphaArena/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            count = int(data.get("count", 0))
            vsize = float(data.get("vsize", 0))
            result["mempool_size"] = count
            result["vsize_mb"] = vsize / 1e6
            # congestion: 0=empty, 1=full (100k+ tx = congested)
            result["congestion"] = min(1.0, count / 100000.0)

            # Fee estimates
            fee_url = "https://mempool.space/api/v1/fees/recommended"
            req2 = urllib.request.Request(fee_url, headers={"User-Agent": "HyperAlphaArena/1.0"})
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                fees = json.loads(resp2.read().decode())
            result["fee_rate"] = float(fees.get("halfHourFee", 0))

            self._set_cache(cache_key, result)

        except Exception as e:
            logger.debug(f"[OnchainDataCollector] Mempool.space failed: {e}")

        return result

    # ── Etherscan (ETH) ──────────────────────

    def _collect_etherscan(self) -> Dict[str, Any]:
        """ETH chain stats from Etherscan (free key, 5 calls/sec)."""
        cache_key = "etherscan"
        cached = self._get_cached(cache_key, self.ETHERSCAN_TTL)
        if cached is not None:
            return cached

        result: Dict[str, Any] = {}
        if not self._etherscan_key:
            return result

        try:
            import urllib.request
            import json

            base = "https://api.etherscan.io/api"

            # Gas price
            gas_url = f"{base}?module=gastracker&action=gasoracle&apikey={self._etherscan_key}"
            req = urllib.request.Request(gas_url, headers={"User-Agent": "HyperAlphaArena/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                gas_data = json.loads(resp.read().decode())
            if gas_data.get("status") == "1":
                result["gas_price_gwei"] = float(gas_data["result"].get("ProposeGasPrice", 0))

            # ETH supply (proxy for activity)
            supply_url = f"{base}?module=stats&action=ethsupply&apikey={self._etherscan_key}"
            req2 = urllib.request.Request(supply_url, headers={"User-Agent": "HyperAlphaArena/1.0"})
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                supply_data = json.loads(resp2.read().decode())
            if supply_data.get("status") == "1":
                result["eth_supply"] = float(supply_data["result"]) / 1e18

            # [2026-07-10 数据修复] 删除 ETH 合成假数据。
            # 原代码用 block_num%100000 当 active_addresses、gas_gwei/5 当 whale_tx_count、
            # gas_gwei*100 当 whale_tx_volume —— 这些是纯启发式捏造，无任何真实依据，
            # 却通过因子层流入 AI 决策（且非零值不会被下游 != 0 过滤掉）。
            # Etherscan 免费 API 只能拿到 gas_price_gwei 和 eth_supply（已在上方真实获取）；
            # active_addresses/exchange_net_flow/whale_tx_* 需要付费 API，取不到就不填，
            # 让下游 agent_deep_context 的 `float(v)!=0` 过滤自然跳过这些缺失项。
            # （原 blockNumber 请求也一并移除——它只为合成假数据服务）

            self._set_cache(cache_key, result)

        except Exception as e:
            logger.debug(f"[OnchainDataCollector] Etherscan failed: {e}")

        return result

    def _symbol_to_chain(self, symbol: str) -> str:
        """将交易对映射到链名称"""
        mapping = {
            'BTC': 'Bitcoin',
            'ETH': 'Ethereum',
            'SOL': 'Solana',
            'AVAX': 'Avalanche',
            'MATIC': 'Polygon',
            'ARB': 'Arbitrum',
            'OP': 'Optimism',
        }
        base = symbol.replace('USDT', '').replace('USDC', '').replace('USD', '')
        return mapping.get(base, 'Ethereum')

    def _get_cached(self, key: str, ttl: float) -> Optional[Any]:
        """获取缓存数据"""
        if key in self._cache:
            ts, value = self._cache[key]
            if time.time() - ts < ttl:
                return value
        return None

    def _set_cache(self, key: str, value: Any) -> None:
        """设置缓存"""
        self._cache[key] = (time.time(), value)

    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()


# 模块级单例
onchain_collector = OnchainDataCollector()
