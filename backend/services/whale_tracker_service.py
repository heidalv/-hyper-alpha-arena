"""
鲸鱼追踪服务 — 大资金异动检测 + AI解读

免费数据源（替代已停用的 Whale Alert）:
1. blockchain.info — BTC 最新区块大额交易扫描（免费/无Key）
2. mempool.space — BTC 内存池大额交易（免费/无Key）
3. 本地 MarketFlowCollector — 交易所大单推断（CVD）
"""
import logging
import os
import re
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

BLOCKCHAIN_INFO = "https://blockchain.info"
MEMPOOL_SPACE = "https://mempool.space/api"
WHALE_BTC_THRESHOLD = 10        # >=10 BTC 认为是鲸鱼交易
WHALE_USD_THRESHOLD = 500_000   # >=50万美元


@dataclass
class WhaleSignal:
    symbol: str = "BTC"
    direction: float = 0.0      # -1(看空) ~ +1(看多)
    confidence: float = 0.0
    activities_count: int = 0
    total_usd: float = 0.0
    summary: str = ""
    # [2026-08-15 消费端验收] 补 available 语义：docstring 早已声称
    # 「无数据返回 available=False」，但 dataclass 从未有此字段——
    # 下游把「无数据」当「真实中性 0」参与汇流。无真实鲸鱼数据时
    # available=False，消费方据此跳过/标注，不冒充中性。
    available: bool = True


class WhaleTrackerService:
    """鲸鱼追踪服务（单例）— 使用免费链上数据"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._btc_price: float = 0
        self._btc_price_ts: float = 0
        self._cache: Dict[str, WhaleSignal] = {}
        self._cache_ts: float = 0
        self._txs_cache: List[Dict] = []
        self._txs_cache_ts: float = 0
        self._txs_cache_ttl: float = 180  # 3分钟缓存
        logger.info("[WhaleTracker] 鲸鱼追踪服务初始化完成 (blockchain.info + mempool.space)")

    def _get_btc_price(self) -> float:
        """获取当前BTC价格（带5分钟缓存）"""
        now = time.time()
        if self._btc_price > 0 and now - self._btc_price_ts < 300:
            return self._btc_price
        try:
            from backend.database.connection import MarketSessionLocal
            from backend.database.models import MarketAssetMetrics
            db = MarketSessionLocal()
            try:
                row = db.query(MarketAssetMetrics).filter(
                    MarketAssetMetrics.symbol == "BTC",
                    MarketAssetMetrics.mark_price.isnot(None),
                ).order_by(MarketAssetMetrics.timestamp.desc()).first()
                if row and float(row.mark_price) > 0:
                    self._btc_price = float(row.mark_price)
                    self._btc_price_ts = now
                    return self._btc_price
            finally:
                db.close()
        except Exception:
            pass
        try:
            r = httpx.get(f"{MEMPOOL_SPACE}/v1/prices", timeout=10)
            if r.status_code == 200:
                data = r.json()
                self._btc_price = float(data.get("USD", 0) or 0)
                self._btc_price_ts = now
                return self._btc_price if self._btc_price > 0 else 0.0
        except Exception:
            pass
        # [2026-08-15 消费端验收] 原兜底 `return self._btc_price or 70000` 在
        # 全部价格源失败时伪造 70000 美元单价，链上金额换算全部失真。
        # 现返回 0.0：调用方见到 price<=0 即跳过本轮鲸鱼采集（诚实无数据），
        # 绝不编造价格。
        return self._btc_price if self._btc_price > 0 else 0.0

    # ────────────────────────── public ──────────────────────────

    async def fetch_and_record(self, db: Session) -> List[Dict]:
        """拉取最新鲸鱼交易 → AI解读 → 存DB"""
        txs = self._fetch_whale_transactions()
        if not txs:
            return []

        results = []
        for tx in txs[:20]:
            interpretation = await self._interpret_with_llm(tx)
            record = self._save_to_db(db, tx, interpretation)
            results.append(record)

        logger.info(f"[WhaleTracker] 记录了 {len(results)} 条鲸鱼异动")
        return results

    def get_whale_signal(self, symbol: str = "BTC") -> WhaleSignal:
        """获取鲸鱼信号（带120秒缓存）"""
        now = time.time()
        key = symbol.upper()
        if key in self._cache and now - self._cache_ts < 120:
            return self._cache[key]

        signal = self._calculate_signal(symbol)
        self._cache[key] = signal
        self._cache_ts = now
        return signal

    def get_recent_activities(self, symbol: str = "BTC", hours: int = 4) -> List[Dict]:
        try:
            from backend.database.connection import MarketSessionLocal
            from backend.database.models import WhaleActivity
            db = MarketSessionLocal()
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                rows = (
                    db.query(WhaleActivity)
                    .filter(WhaleActivity.created_at >= cutoff)
                    .order_by(WhaleActivity.created_at.desc())
                    .limit(30)
                    .all()
                )
                results = []
                for r in rows:
                    if r.symbol and r.symbol.upper() != symbol.upper():
                        continue
                    results.append({
                        "id": r.id,
                        "type": r.activity_type,
                        "symbol": r.symbol,
                        "direction": r.direction,
                        "amount_usd": r.amount_usd,
                        "from": r.from_entity,
                        "to": r.to_entity,
                        "signal_direction": r.signal_direction,
                        "interpretation": r.ai_interpretation,
                        "created_at": str(r.created_at),
                    })
                return results
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[WhaleTracker] get_recent_activities 异常: {e}")
            return []

    # ────────────────────────── 数据采集 ──────────────────────────

    def _fetch_whale_transactions(self) -> List[Dict]:
        """从多个免费数据源采集鲸鱼交易（带3分钟缓存）"""
        now = time.time()
        if self._txs_cache and now - self._txs_cache_ts < self._txs_cache_ttl:
            return self._txs_cache

        # [2026-08-15 消费端验收] BTC 价格拿不到时无法换算美元金额——
        # 直接返回空（诚实无数据），不再用伪造价格折算。
        if self._get_btc_price() <= 0:
            logger.warning("[WhaleTracker] BTC 价格不可用，本轮跳过链上鲸鱼采集")
            return []

        txs = []

        # Source 1: blockchain.info 最新区块扫描
        txs.extend(self._fetch_from_blockchain_info())

        # Source 2: mempool.space 内存池大额交易
        txs.extend(self._fetch_from_mempool_space())

        # Source 3: 本地交易所大单推断
        txs.extend(self._infer_from_market_flow())

        txs.sort(key=lambda x: x.get("amount_usd", 0), reverse=True)
        result = txs[:20]
        self._txs_cache = result
        self._txs_cache_ts = time.time()
        return result

    def _fetch_from_blockchain_info(self) -> List[Dict]:
        """从 mempool.space 扫描最新区块的大额BTC交易（替代blockchain.info rawblock，更快）"""
        whale_txs = []
        btc_price = self._get_btc_price()
        try:
            r = httpx.get(f"{MEMPOOL_SPACE}/blocks/tip/hash", timeout=8)
            if r.status_code != 200:
                return []
            block_hash = r.text.strip()

            r2 = httpx.get(f"{MEMPOOL_SPACE}/block/{block_hash}/txs/0", timeout=10)
            if r2.status_code != 200:
                return []
            txs_data = r2.json()

            for tx in txs_data:
                vout = tx.get("vout", [])
                total_sat = sum(o.get("value", 0) for o in vout)
                total_btc = total_sat / 1e8
                total_usd = total_btc * btc_price

                if total_btc < WHALE_BTC_THRESHOLD:
                    continue

                vin = tx.get("vin", [])
                from_type = self._classify_address_type(vin, is_input=True)
                to_type = self._classify_address_type_from_outputs(vout)

                whale_txs.append({
                    "blockchain": "bitcoin",
                    "symbol": "BTC",
                    "amount": total_btc,
                    "amount_usd": total_usd,
                    "from_owner": from_type["label"],
                    "from_type": from_type["type"],
                    "to_owner": to_type["label"],
                    "to_type": to_type["type"],
                    "tx_hash": tx.get("txid", "")[:16],
                    "timestamp": tx.get("status", {}).get("block_time", int(time.time())),
                    "source": "mempool.space/block",
                })

            logger.debug(f"[WhaleTracker] mempool.space/block: {len(whale_txs)} whale txs")
        except Exception as e:
            logger.debug(f"[WhaleTracker] mempool.space/block 异常: {e}")
        return whale_txs[:10]

    def _fetch_from_mempool_space(self) -> List[Dict]:
        """从 mempool.space 获取内存池中的大额未确认交易"""
        whale_txs = []
        btc_price = self._get_btc_price()
        try:
            r = httpx.get(f"{MEMPOOL_SPACE}/mempool/recent", timeout=15)
            if r.status_code != 200:
                return []
            recent = r.json()

            for tx in recent:
                value_sat = tx.get("value", 0)
                value_btc = value_sat / 1e8
                value_usd = value_btc * btc_price

                if value_btc < WHALE_BTC_THRESHOLD:
                    continue

                whale_txs.append({
                    "blockchain": "bitcoin",
                    "symbol": "BTC",
                    "amount": value_btc,
                    "amount_usd": value_usd,
                    "from_owner": "unknown",
                    "from_type": "unknown",
                    "to_owner": "unknown",
                    "to_type": "unknown",
                    "tx_hash": tx.get("txid", "")[:16],
                    "timestamp": int(time.time()),
                    "source": "mempool.space",
                    "unconfirmed": True,
                })

            logger.debug(f"[WhaleTracker] mempool.space: {len(whale_txs)} whale txs in mempool")
        except Exception as e:
            logger.debug(f"[WhaleTracker] mempool.space 异常: {e}")
        return whale_txs[:5]

    def _infer_from_market_flow(self) -> List[Dict]:
        """从本地 MarketFlow 数据推断交易所大单"""
        try:
            from backend.services.market_flow_indicators import get_indicator_value
            from backend.database.connection import MarketSessionLocal
            db = MarketSessionLocal()
            try:
                cvd = get_indicator_value(db, "BTC", "CVD", "15m") or 0
            finally:
                db.close()

            if abs(cvd) < 100:
                return []

            direction = "buy" if cvd > 0 else "sell"
            return [{
                "blockchain": "exchange",
                "symbol": "BTC",
                "amount": 0,
                "amount_usd": abs(cvd) * 1000,
                "from_owner": "exchange_orderbook",
                "from_type": "exchange",
                "to_owner": "exchange_orderbook",
                "to_type": "exchange",
                "tx_hash": "",
                "timestamp": int(time.time()),
                "source": "local_cvd",
                "inferred": True,
                "direction_hint": direction,
            }]
        except Exception:
            return []

    # ────────────────────────── 地址分类 ──────────────────────────

    @staticmethod
    def _classify_address_type(inputs: list, is_input: bool = True) -> Dict[str, str]:
        """简单启发式分类BTC地址（交易所 vs 个人钱包）"""
        if not inputs:
            return {"type": "unknown", "label": "未知"}
        n_inputs = len(inputs)
        if n_inputs > 20:
            return {"type": "exchange", "label": "交易所(多输入)"}
        elif n_inputs > 5:
            return {"type": "possible_exchange", "label": "疑似交易所"}
        else:
            return {"type": "wallet", "label": "个人钱包"}

    @staticmethod
    def _classify_address_type_from_outputs(outputs: list) -> Dict[str, str]:
        if not outputs:
            return {"type": "unknown", "label": "未知"}
        n_outputs = len(outputs)
        if n_outputs > 20:
            return {"type": "exchange", "label": "交易所(多输出)"}
        elif n_outputs == 2:
            return {"type": "wallet", "label": "个人钱包(找零)"}
        elif n_outputs > 5:
            return {"type": "possible_exchange", "label": "疑似交易所"}
        else:
            return {"type": "wallet", "label": "个人钱包"}

    # ────────────────────────── AI解读 ──────────────────────────

    # V5 M4: LLM 调用节流（此前 3 天 3,668 次调用，算力重分配给决策核心）
    _llm_last_call_ts: float = 0.0

    def _llm_prefilter(self, tx: Dict) -> bool:
        """规则预筛：只有「大额 + 涉及交易所」的事件才值得花 LLM 算力。"""
        import os as _os

        min_usd = float(_os.getenv("WHALE_LLM_MIN_USD", "5000000"))
        if float(tx.get("amount_usd", 0) or 0) < min_usd:
            return False
        types = f"{tx.get('from_type', '')}|{tx.get('to_type', '')}".lower()
        return "exchange" in types

    async def _interpret_with_llm(self, tx: Dict) -> Dict:
        try:
            import os as _os

            # 节流闸1：规则预筛不通过 → 启发式解读（零成本）
            if not self._llm_prefilter(tx):
                return self._heuristic_interpret(tx)

            # 节流闸2：15 分钟全局最小间隔
            min_interval = float(_os.getenv("WHALE_LLM_MIN_INTERVAL_SEC", "900"))
            now = time.time()
            if now - self._llm_last_call_ts < min_interval:
                return self._heuristic_interpret(tx)

            from backend.services.llm_config_service import call_llm_api_sync, get_llm_config
            config = get_llm_config()
            if not config:
                return self._heuristic_interpret(tx)
            self._llm_last_call_ts = now
            messages = [
                {"role": "system", "content": (
                    "你是加密货币链上分析专家。分析鲸鱼转账的意图和市场影响。\n"
                    "返回严格JSON:\n"
                    '{"interpretation": "一句话解读", "signal_direction": 0.3, '
                    '"activity_type": "transfer"}\n'
                    "signal_direction: -1(极度利空)~+1(极度利多)\n"
                    "activity_type: transfer/exchange_deposit/exchange_withdrawal/large_order"
                )},
                {"role": "user", "content": (
                    f"币种: {tx.get('symbol')}\n"
                    f"金额: {tx.get('amount', 0):.4f} BTC (${tx.get('amount_usd', 0):,.0f})\n"
                    f"从: {tx.get('from_owner')} ({tx.get('from_type')})\n"
                    f"到: {tx.get('to_owner')} ({tx.get('to_type')})\n"
                    f"来源: {tx.get('source', 'blockchain')}"
                )},
            ]
            resp = call_llm_api_sync(config, messages=messages)
            content = resp["choices"][0]["message"]["content"]
            import json
            m = re.search(r'\{.*\}', content, re.DOTALL)
            if m:
                parsed = json.loads(m.group())
                return {
                    "interpretation": parsed.get("interpretation", ""),
                    "signal_direction": max(-1, min(1, float(parsed.get("signal_direction", 0)))),
                    "activity_type": parsed.get("activity_type", "transfer"),
                }
        except Exception as e:
            logger.debug(f"[WhaleTracker] LLM解读失败: {e}")

        return self._heuristic_interpret(tx)

    def _heuristic_interpret(self, tx: Dict) -> Dict:
        """启发式解读（无LLM时使用）"""
        to_type = tx.get("to_type", "")
        from_type = tx.get("from_type", "")
        amount = tx.get("amount_usd", 0)
        amount_btc = tx.get("amount", 0)

        if "exchange" in to_type and "exchange" not in from_type:
            return {
                "interpretation": f"{amount_btc:.2f} BTC (${amount:,.0f}) 转入交易所，可能准备抛售",
                "signal_direction": -0.3,
                "activity_type": "exchange_deposit",
            }
        elif "exchange" in from_type and "exchange" not in to_type:
            return {
                "interpretation": f"{amount_btc:.2f} BTC (${amount:,.0f}) 从交易所提出，可能长期持有",
                "signal_direction": 0.3,
                "activity_type": "exchange_withdrawal",
            }
        elif tx.get("direction_hint") == "buy":
            return {
                "interpretation": f"交易所检测到大额买单 (${amount:,.0f})",
                "signal_direction": 0.2,
                "activity_type": "large_order",
            }
        elif tx.get("direction_hint") == "sell":
            return {
                "interpretation": f"交易所检测到大额卖单 (${amount:,.0f})",
                "signal_direction": -0.2,
                "activity_type": "large_order",
            }
        else:
            return {
                "interpretation": f"{amount_btc:.2f} BTC (${amount:,.0f}) 钱包间转移",
                "signal_direction": 0.0,
                "activity_type": "transfer",
            }

    # ────────────────────────── signal ──────────────────────────

    def _calculate_signal(self, symbol: str) -> WhaleSignal:
        activities = self.get_recent_activities(symbol, hours=4)

        # 如果DB里没有历史记录，做一次实时快照。
        # 【数据真实性铁律】_fetch_whale_transactions() 只采集 BTC 链上数据（mempool.space/blockchain.info），
        # 对非 BTC 币种不能张冠李戴——ETH/SOL/ARB 等没有链上鲸鱼数据源时，返回空信号（available=False），
        # 绝不用 BTC 的全局数据冒充其他币种的鲸鱼信号。
        if not activities:
            if symbol.upper() != "BTC":
                return WhaleSignal(symbol=symbol, summary="暂无该币种链上鲸鱼数据", available=False)
            txs = self._fetch_whale_transactions()
            if txs:
                total_usd = sum(t.get("amount_usd", 0) for t in txs)
                deposit_count = sum(1 for t in txs if "exchange" in t.get("to_type", "") and "exchange" not in t.get("from_type", ""))
                withdraw_count = sum(1 for t in txs if "exchange" in t.get("from_type", "") and "exchange" not in t.get("to_type", ""))
                if deposit_count > withdraw_count * 1.5:
                    direction = -0.3
                    direction_label = "偏空(转入交易所多)"
                elif withdraw_count > deposit_count * 1.5:
                    direction = 0.3
                    direction_label = "偏多(提出交易所多)"
                else:
                    direction = 0.0
                    direction_label = "中性"
                return WhaleSignal(
                    symbol=symbol,
                    direction=direction,
                    confidence=min(1.0, len(txs) / 10),
                    activities_count=len(txs),
                    total_usd=total_usd,
                    summary=f"{len(txs)}笔链上大额 ${total_usd:,.0f} {direction_label}",
                )
            return WhaleSignal(symbol=symbol, summary="暂无鲸鱼数据", available=False)

        total_dir = 0.0
        total_usd = 0.0
        for a in activities:
            d = a.get("signal_direction", 0) or 0
            u = a.get("amount_usd", 0) or 0
            total_dir += d * (u / 1_000_000)
            total_usd += u

        avg_dir = total_dir / max(1, total_usd / 1_000_000) if total_usd > 0 else 0
        return WhaleSignal(
            symbol=symbol,
            direction=max(-1, min(1, avg_dir)),
            confidence=min(1.0, len(activities) / 10),
            activities_count=len(activities),
            total_usd=total_usd,
            summary=f"{len(activities)}笔异动 ${total_usd:,.0f} {'偏多' if avg_dir > 0 else '偏空' if avg_dir < 0 else '中性'}",
        )

    # ────────────────────────── DB ──────────────────────────

    def _save_to_db(self, db: Session, tx: Dict, interpretation: Dict) -> Dict:
        from backend.database.models import WhaleActivity
        record = WhaleActivity(
            activity_type=interpretation.get("activity_type", "transfer"),
            symbol=tx.get("symbol", "BTC"),
            direction="buy" if interpretation.get("signal_direction", 0) > 0 else "sell",
            amount_usd=tx.get("amount_usd", 0),
            from_entity=tx.get("from_owner", ""),
            to_entity=tx.get("to_owner", ""),
            blockchain=tx.get("blockchain", ""),
            tx_hash=tx.get("tx_hash", ""),
            ai_interpretation=interpretation.get("interpretation", ""),
            signal_direction=interpretation.get("signal_direction", 0),
        )
        # [2026-08-15 P0-1 修复] WhaleActivity 是 MarketBase 模型，必须写 Market DB
        # （alpha_market）。此前调用方传入核心库 SessionLocal → commit 静默失败被吞，
        # 导致 whale_activities 长期 0 行而日志谎报成功。此处改用 MarketSessionLocal，
        # 与 news_events 修复同源；commit 失败显式告警，不再静默吞错。
        session_factory = None
        if db is not None and getattr(db, "bind", None) is not None:
            try:
                bind_url = str(db.bind.url)
                if "alpha_market" in bind_url:
                    session_factory = lambda: db  # noqa: E731
            except Exception:
                pass
        if session_factory is None:
            from backend.database.connection import MarketSessionLocal
            session_factory = MarketSessionLocal
        _s = session_factory()
        try:
            try:
                _s.add(record)
                _s.commit()
            except Exception as e:
                _s.rollback()
                logger.error(f"[WhaleTracker] 鲸鱼记录落库失败（Market DB）: {e}")
        finally:
            if _s is not db:
                _s.close()
        return {
            "symbol": record.symbol,
            "type": record.activity_type,
            "amount_usd": record.amount_usd,
            "direction": record.direction,
            "signal": record.signal_direction,
            "interpretation": record.ai_interpretation,
        }


whale_tracker = WhaleTrackerService()
