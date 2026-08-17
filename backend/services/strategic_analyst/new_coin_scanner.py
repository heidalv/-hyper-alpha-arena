"""
Strategic Analyst - 新币打新套利扫描器

三阶段流水线：
1. Discovery（发现）- 监控交易所新币上线
2. Evaluation（评估）- 多维度打分
3. Strategy（策略建议）- 生成操作建议

数据源：
- Hyperliquid get_all_symbols() 比对已知币种
- CoinGecko /coins/list 获取新上线币种信息
"""

import logging
import time
import threading
from typing import Dict, List, Optional, Set
from datetime import datetime

import httpx

from .models import NewCoinOpportunity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 已知币种快照缓存
# ---------------------------------------------------------------------------
_known_symbols_cache: Dict[str, Set[str]] = {}    # exchange -> known symbols
_known_symbols_lock = threading.Lock()
_last_scan_time: float = 0.0


# ---------------------------------------------------------------------------
# 新币打新扫描器
# ---------------------------------------------------------------------------
class NewCoinScanner:
    """
    新币打新套利扫描器

    发现新上线币种，评估打新机会，生成策略建议
    """

    # 项目类别权重（历史首日表现参考）
    CATEGORY_SCORES = {
        "DeFi": 65,
        "L2": 70,
        "Infra": 60,
        "GameFi": 50,
        "Meme": 45,
        "AI": 75,
        "RWA": 55,
        "unknown": 40,
    }

    # Hyperliquid 上新币通常的波动率范围
    DEFAULT_VOLATILITY = {
        "DeFi": 0.30,
        "L2": 0.25,
        "Infra": 0.20,
        "GameFi": 0.40,
        "Meme": 0.60,
        "AI": 0.35,
        "RWA": 0.25,
        "unknown": 0.35,
    }

    def __init__(self):
        self._initialized = False

    def scan(self) -> List[NewCoinOpportunity]:
        """
        执行新币扫描流水线

        Returns:
            发现的新币机会列表
        """
        global _last_scan_time

        # 1. 发现新币
        new_coins = self._discover()

        if not new_coins:
            return []

        # 2. 评估每个新币
        opportunities = []
        for coin in new_coins:
            evaluated = self._evaluate(coin)
            if evaluated:
                # 3. 生成策略建议
                self._generate_strategy(evaluated)
                opportunities.append(evaluated)

        _last_scan_time = time.time()

        if opportunities:
            logger.info(f"[NewCoinScanner] 发现 {len(opportunities)} 个新币机会: "
                       f"{[o.symbol for o in opportunities]}")

        return opportunities

    def get_known_symbols(self) -> Set[str]:
        """获取已知币种集合"""
        with _known_symbols_lock:
            return _known_symbols_cache.get("hyperliquid", set()).copy()

    # -----------------------------------------------------------------------
    # 阶段 1: Discovery（发现）
    # -----------------------------------------------------------------------

    def _discover(self) -> List[Dict]:
        """发现新币"""
        new_coins = []

        # 尝试从 Hyperliquid 获取当前所有交易对
        current_symbols = self._fetch_hyperliquid_symbols()

        if current_symbols is None:
            return []

        with _known_symbols_lock:
            known = _known_symbols_cache.get("hyperliquid", set())

            if not known:
                # 首次扫描，只记录，不报告新币
                _known_symbols_cache["hyperliquid"] = current_symbols.copy()
                self._initialized = True
                logger.info(f"[NewCoinScanner] 初始化已知币种: {len(current_symbols)} 个")
                return []

            # 比对发现新币
            new_symbols = current_symbols - known

            if new_symbols:
                logger.info(f"[NewCoinScanner] 发现 {len(new_symbols)} 个新币: {new_symbols}")
                # 更新已知集合
                _known_symbols_cache["hyperliquid"] = current_symbols.copy()

                for symbol in new_symbols:
                    # 从 CoinGecko 获取额外信息
                    coin_info = self._fetch_coin_info(symbol)
                    new_coins.append({
                        "symbol": symbol,
                        "exchange": "hyperliquid",
                        **coin_info,
                    })

        return new_coins

    def _fetch_hyperliquid_symbols(self) -> Optional[Set[str]]:
        """获取 Hyperliquid 所有交易对"""
        try:
            # 使用项目已有的 hyperliquid_market_data
            try:
                from backend.services.hyperliquid_market_data import get_hyperliquid_client
            except ImportError:
                from services.hyperliquid_market_data import get_hyperliquid_client
            client = get_hyperliquid_client()
            all_symbols = client.get_all_symbols()
            if all_symbols:
                return set(s.upper() for s in all_symbols)
        except ImportError:
            logger.debug("[NewCoinScanner] hyperliquid_market_data 不可用，尝试直接调用")
        except Exception as e:
            logger.warning(f"[NewCoinScanner] 获取 Hyperliquid 交易对失败: {e}")

        # 回退方案：直接调用 API
        # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连 HL API，
        # 统一从数据中心 symbol_catalog 目录读取。
        try:
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                from backend.services.kline_sync_meta import list_catalog_symbols
                catalog = list_catalog_symbols("hyperliquid")
                if catalog:
                    return set(catalog)
                logger.warning(
                    "[NewCoinScanner] DC_ONLY: hyperliquid symbol_catalog 为空，禁止直连兜底"
                )
                return None
        except Exception:
            pass
        try:
            resp = httpx.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "metaAndAssetCtxs"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) >= 1:
                    universe = data[0] if isinstance(data[0], list) else []
                    # universe 的每个元素包含 name 字段
                    symbols = set()
                    for item in universe:
                        if isinstance(item, dict) and "name" in item:
                            symbols.add(item["name"].upper())
                    return symbols
        except Exception as e:
            logger.warning(f"[NewCoinScanner] 直接调用 Hyperliquid API 失败: {e}")

        return None

    def _fetch_coin_info(self, symbol: str) -> Dict:
        """从 CoinGecko 获取币种信息"""
        info = {
            "project_category": "unknown",
            "team_background": "unknown",
            "funding_info": {},
        }

        try:
            # 搜索币种
            resp = httpx.get(
                "https://api.coingecko.com/api/v3/search",
                params={"query": symbol},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                coins = data.get("coins", [])
                for coin in coins:
                    if coin.get("symbol", "").upper() == symbol.upper():
                        info["project_category"] = self._guess_category(coin)
                        info["coingecko_id"] = coin.get("id", "")
                        break
        except Exception as e:
            logger.debug(f"[NewCoinScanner] CoinGecko 查询 {symbol} 失败: {e}")

        return info

    def _guess_category(self, coin_data: Dict) -> str:
        """根据 CoinGecko 数据猜测项目类别"""
        categories = coin_data.get("categories", [])
        if not categories:
            return "unknown"

        category_lower = " ".join(str(c).lower() for c in categories)

        if any(k in category_lower for k in ["defi", "lending", "dex"]):
            return "DeFi"
        elif any(k in category_lower for k in ["layer 2", "l2", "scaling"]):
            return "L2"
        elif any(k in category_lower for k in ["gaming", "gamefi", "metaverse"]):
            return "GameFi"
        elif any(k in category_lower for k in ["meme", "dog", "pepe", "inu"]):
            return "Meme"
        elif any(k in category_lower for k in ["artificial intelligence", "ai "]):
            return "AI"
        elif any(k in category_lower for k in ["rwa", "real world"]):
            return "RWA"
        elif any(k in category_lower for k in ["infrastructure", "scaling"]):
            return "Infra"
        return "unknown"

    # -----------------------------------------------------------------------
    # 阶段 2: Evaluation（评估）
    # -----------------------------------------------------------------------

    def _evaluate(self, coin_data: Dict) -> Optional[NewCoinOpportunity]:
        """评估新币，生成打分"""
        opp = NewCoinOpportunity(
            symbol=coin_data.get("symbol", ""),
            exchange=coin_data.get("exchange", "unknown"),
            listing_date=datetime.utcnow(),
            status="listing",
            project_category=coin_data.get("project_category", "unknown"),
            team_background=coin_data.get("team_background", "unknown"),
            funding_info=coin_data.get("funding_info", {}),
        )

        # 多维度打分 (0-100)
        hype_score = self._calculate_hype_score(coin_data)
        opp.hype_score = hype_score

        # 估算波动率
        # [2026-08-15 消费端验收] 有真实 K 线历史时用数据中心落库数据实测
        # 波动率（1h 收益 std，年化近似）；无历史（真·新币）才用类别默认
        # 假设值，且标注为假设（volatility_is_estimate=True）。
        _real_vol = self._volatility_from_klines(opp.symbol)
        if _real_vol is not None:
            opp.estimated_volatility = _real_vol
            opp.volatility_is_estimate = False
        else:
            opp.estimated_volatility = self.DEFAULT_VOLATILITY.get(
                opp.project_category, 0.35
            )
            opp.volatility_is_estimate = True

        # 计算置信度
        opp.confidence = float(min(1.0, hype_score / 80.0))

        return opp

    def _volatility_from_klines(self, symbol: str) -> Optional[float]:
        """从数据中心 1h K 线实测波动率（收益 std × √24 日年化近似）。

        历史不足（<24 根）返回 None（调用方用类别默认假设并标注 estimate）。
        """
        try:
            from backend.services.data_center import data_center
            kr = data_center.get_klines(symbol, "1h", count=168, purpose="research")
            if kr.count < 24:
                return None
            import math
            closes = [float(r.get("close") or 0) for r in kr.rows if (r.get("close") or 0) > 0]
            if len(closes) < 24:
                return None
            rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
            if not rets:
                return None
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
            std_1h = math.sqrt(var)
            return round(min(2.0, std_1h * math.sqrt(24)), 4)  # 日年化近似，上限 200%
        except Exception:
            return None

    def _calculate_hype_score(self, coin_data: Dict) -> float:
        """
        计算新币热度评分 (0-100)

        维度：
        - 项目类别权重 (30%)
        - 交易所信誉 (30%) - Hyperliquid 上新通常质量较高
        - CoinGecko 存在性 (20%) - 有 CoinGecko 页面说明有一定关注度
        - 融资背景 (20%)
        """
        score = 0.0

        # 项目类别 (30%)
        category = coin_data.get("project_category", "unknown")
        category_score = self.CATEGORY_SCORES.get(category, 40)
        score += category_score * 0.30

        # 交易所信誉 (30%) - Hyperliquid 上新通常经过筛选
        exchange = coin_data.get("exchange", "")
        if exchange == "hyperliquid":
            score += 70 * 0.30  # Hyperliquid 上新通常较优质
        else:
            score += 50 * 0.30

        # CoinGecko 存在性 (20%)
        if coin_data.get("coingecko_id"):
            score += 70 * 0.20
        else:
            score += 30 * 0.20

        # 融资背景 (20%)
        funding = coin_data.get("funding_info", {})
        if funding:
            score += 65 * 0.20
        else:
            score += 35 * 0.20

        return float(min(100.0, score))

    # -----------------------------------------------------------------------
    # 阶段 3: Strategy（策略建议）
    # -----------------------------------------------------------------------

    def _generate_strategy(self, opp: NewCoinOpportunity) -> None:
        """根据评估结果生成策略建议"""
        hype = opp.hype_score
        vol = opp.estimated_volatility or 0.35

        if hype >= 70 and vol >= 0.30:
            # 高热度 + 高波动 → 快进快出
            opp.recommended_strategy = "scalp_first"
            opp.recommended_position_pct = 0.02   # 小仓位试水
            opp.stop_loss_pct = 0.05
            opp.take_profit_pct = 0.15
        elif hype >= 50:
            # 中等热度 → 等待观察
            opp.recommended_strategy = "wait_and_see"
            opp.recommended_position_pct = 0.01
            opp.stop_loss_pct = 0.05
            opp.take_profit_pct = 0.10
        else:
            # 低热度 → 不参与
            opp.recommended_strategy = "avoid"
            opp.recommended_position_pct = 0.0
            opp.stop_loss_pct = 0.0
            opp.take_profit_pct = 0.0
