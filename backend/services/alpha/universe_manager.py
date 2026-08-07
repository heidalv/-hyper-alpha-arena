"""
UniverseManager — 品种筛选五步管线（P1，规划文档 §5.3，真实重新实现）。

背景：`services/alpha/universe.py` 里的 `UniverseAgent` 只是一个"输入 ADV 直接分层"
的空壳（无流动性硬门/无波动率适配/无相关性去重/无动态降级），且生产调用方为0
（规划文档§0.2已核实）。本文件不信任那个空壳，参照其接口思路但从零重新实现
文档§5.3 定义的五步管线，并真正接入 auto_coin_selector（AI选币入口）与
ScalpExecutionGate（执行门）两处生产路径。

五步管线：
    STEP1 初始池: 交易所全量可交易品种（实时meta/ticker API，非本地缓存陈列）
    STEP2 流动性硬门: 24h 美元成交量 >= MIN_ADV_USD（默认$500万，来自交易所
          实时ticker，真实数据）。L2 1%盘口深度门因项目暂无独立深度采集
          管道，本版本诚实跳过并在结果里标注 depth_check="unavailable"，
          不伪造该项通过。
    STEP3 波动率适配 + 流动性分位 + 低相关性奖励 → 复合得分
          composite = 0.4*vol_fitness + 0.3*liquidity_percentile + 0.3*low_corr_bonus
    STEP4 相关性去重: 30天(取本地K线可用长度)收益相关矩阵，贪心选择，
          max(|corr|)>0.7 与已选品种冲突则跳过，最终 <= MAX_UNIVERSE_SIZE(15)
    STEP5 动态降级: 独立的 recheck_liquidity() 供调度器高频调用（默认4h），
          仅重新核对已入选品种的流动性，跌破阈值标记 DEGRADED（仅允许平仓，
          不允许新开仓)；rebuild() 供调度器低频调用（默认每周）做全量重建。

集成点（真正接入生产路径，而非只是新增一个孤立类）：
    1. auto_coin_selector.scan_candidates() —— 对AI选币候选做硬性流动性/
       相关性过滤（只会让候选池变严格，不会新增任何交易权限，对已持仓/
       手动选币/已激活策略零影响，因为那些走 _resolve_session_trade_symbols
       的另一条路径，本管理器完全不touch）。
    2. ScalpExecutionGate.evaluate() —— 若品种当前处于 DEGRADED，新开仓请求
       直接 block（已有持仓的平仓走独立的退出状态机，不经过 evaluate()，
       不受影响）。
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_ADV_USD = float(os.getenv("UNIVERSE_MIN_ADV_USD", "5000000"))
MAX_UNIVERSE_SIZE = int(os.getenv("UNIVERSE_MAX_SIZE", "15"))
MAX_PAIRWISE_CORR = float(os.getenv("UNIVERSE_MAX_PAIRWISE_CORR", "0.7"))
VOL_SWEET_LOW = float(os.getenv("UNIVERSE_VOL_SWEET_LOW", "0.60"))   # 年化波动率下限(60%)
VOL_SWEET_HIGH = float(os.getenv("UNIVERSE_VOL_SWEET_HIGH", "2.00"))  # 年化波动率上限(200%)
MIN_HISTORY_DAYS_SHADOW = float(os.getenv("UNIVERSE_MIN_HISTORY_DAYS", "7"))
KLINE_LOOKBACK_HOURS = int(os.getenv("UNIVERSE_KLINE_LOOKBACK_HOURS", "1440"))  # 60天
CORR_LOOKBACK_HOURS = int(os.getenv("UNIVERSE_CORR_LOOKBACK_HOURS", "720"))     # 30天

_STATE_PATH = Path(__file__).resolve().parents[3] / "data" / "universe_state.json"


@dataclass
class UniverseSymbolResult:
    symbol: str
    adv_usd: float = 0.0
    annualized_vol: float = 0.0
    vol_fitness: float = 0.0
    liquidity_percentile: float = 0.0
    low_corr_bonus: float = 0.0
    composite_score: float = 0.0
    history_days: float = 0.0
    status: str = "rejected"  # active / shadow / degraded / rejected
    reject_reason: str = ""


@dataclass
class UniverseState:
    generated_at: float = 0.0
    depth_check: str = "unavailable"  # 诚实标注：无L2深度采集管道
    selected: List[UniverseSymbolResult] = field(default_factory=list)
    all_evaluated: List[UniverseSymbolResult] = field(default_factory=list)

    def active_symbols(self) -> List[str]:
        return [r.symbol for r in self.selected if r.status in ("active", "shadow")]

    def degraded_symbols(self) -> List[str]:
        return [r.symbol for r in self.selected if r.status == "degraded"]


class UniverseManager:
    """单例：品种筛选五步管线。"""

    _instance: Optional["UniverseManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._state = None
            cls._instance._load_state()
        return cls._instance

    # ------------------------------------------------------------------
    # 持久化（与 factor_runtime_weights.json 同一套约定：JSON 落 data/ 目录）
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        try:
            if _STATE_PATH.exists():
                raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
                selected = [UniverseSymbolResult(**r) for r in raw.get("selected", [])]
                self._state = UniverseState(
                    generated_at=raw.get("generated_at", 0.0),
                    depth_check=raw.get("depth_check", "unavailable"),
                    selected=selected,
                )
        except Exception as e:
            logger.debug(f"[UniverseManager] 加载历史状态失败(不影响首次重建): {e}")

    def _save_state(self) -> None:
        try:
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "generated_at": self._state.generated_at,
                "depth_check": self._state.depth_check,
                "selected": [r.__dict__ for r in self._state.selected],
            }
            _STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[UniverseManager] 保存状态失败: {e}")

    # ------------------------------------------------------------------
    # STEP1+2+3+4：全量重建（默认每周一次，人工也可随时触发）
    # ------------------------------------------------------------------

    def rebuild(self, exchange: str = "hyperliquid") -> UniverseState:
        t0 = time.time()
        try:
            candidates = self._step1_initial_pool(exchange)
            qualified = self._step2_liquidity_gate(candidates)
            scored = self._step3_composite_score(qualified)
            selected = self._step4_correlation_dedup(scored)
        except Exception as e:
            logger.error(f"[UniverseManager] rebuild 失败，保留上次状态: {e}", exc_info=True)
            return self._state or UniverseState()

        state = UniverseState(
            generated_at=time.time(),
            depth_check="unavailable",  # 诚实标注，见模块 docstring
            selected=selected,
            all_evaluated=scored,
        )
        self._state = state
        self._save_state()
        logger.info(
            f"[UniverseManager] rebuild 完成 耗时{time.time()-t0:.1f}s "
            f"候选={len(candidates)} 通过流动性门={len(qualified)} 最终入选={len(selected)}: "
            f"{[r.symbol for r in selected]}"
        )
        return state

    def _step1_initial_pool(self, exchange: str) -> List[str]:
        """STEP1: 交易所全量可交易品种（实时API，非本地陈列缓存）。"""
        try:
            from backend.services.market_scanner import MarketScanner
            symbols = MarketScanner.get_all_tradable_symbols(exchange)
            if symbols and len(symbols) >= 5:
                return [s.upper() for s in symbols]
        except Exception as e:
            logger.warning(f"[UniverseManager] STEP1 实时symbol列表获取失败,降级用本地K线陈列: {e}")
        # 降级：本地 crypto_klines 里出现过的品种（至少还能跑，不是空池）
        try:
            from backend.database.connection import MarketSessionLocal
            from sqlalchemy import text as _sa_text
            db = MarketSessionLocal()
            try:
                rows = db.execute(_sa_text(
                    "SELECT DISTINCT symbol FROM crypto_klines WHERE period='1h'"
                )).fetchall()
                return [r[0].upper() for r in rows]
            finally:
                db.close()
        except Exception as e2:
            logger.error(f"[UniverseManager] STEP1 本地降级也失败: {e2}")
            return []

    def _step2_liquidity_gate(self, candidates: List[str]) -> Dict[str, float]:
        """STEP2: 24h美元成交量硬门（真实交易所ticker数据）。返回 {symbol: adv_usd}。

        [2026-07-18] 不用 data_center.get_all_market_tickers()——它以 binance
        fetch_tickers() 兜底全量列举，binance 不可达（网络/代理受限）时会整体
        退化为只查 3 个默认symbol，导致STEP1的232个候选品种大部分拿不到ticker。
        这里直接对 STEP1 产出的候选列表批量查 Hyperliquid ticker（真实数据，
        不受 binance 可用性影响），分批请求避免单次请求过大。
        """
        adv_map: Dict[str, float] = {}
        # 修复：asterdex 主所优先用数据中心 ticker 成交额，不再一律查 HL
        try:
            from backend.services.exchange_config import get_active_exchange
            _ex = (get_active_exchange() or "asterdex").strip().lower()
            if _ex in ("aster", "asterdex"):
                from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
                _stats = asterdex_ticker_poller.get_all_stats()
                for _sym in candidates:
                    _st = _stats.get(_sym.upper())
                    if _st and _st.get("quote_volume_24h"):
                        adv_map[_sym.upper()] = float(_st["quote_volume_24h"])
        except Exception:
            pass
        try:
            # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连 Hyperliquid
            # 补流动性（HL 是备选源，asterdex 主所数据已由 ticker poller 提供）。
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                return adv_map
            from backend.services.hyperliquid_market_data import HyperliquidClient
            client = HyperliquidClient()
            batch_size = 200
            for i in range(0, len(candidates), batch_size):
                batch = candidates[i:i + batch_size]
                bulk = client.get_bulk_ticker_data(batch)
                for sym, data in bulk.items():
                    adv_map[sym.upper()] = float(data.get("volume24h") or 0.0)
        except Exception as e:
            logger.warning(f"[UniverseManager] STEP2 Hyperliquid批量ticker获取失败: {e}")

        qualified: Dict[str, float] = {
            sym: adv for sym, adv in adv_map.items() if adv >= MIN_ADV_USD
        }
        return qualified

    def _step3_composite_score(self, qualified: Dict[str, float]) -> List[UniverseSymbolResult]:
        """STEP3: 波动率适配 + 流动性分位 + 低相关性奖励 → 复合得分。"""
        from backend.services.data_center import data_center

        returns_by_symbol: Dict[str, "object"] = {}
        results: List[UniverseSymbolResult] = []

        import pandas as pd

        for sym, adv in qualified.items():
            try:
                df = data_center.get_klines(sym, "1h", count=KLINE_LOOKBACK_HOURS).to_dataframe()
            except Exception as e:
                logger.debug(f"[UniverseManager] {sym} K线获取失败: {e}")
                df = pd.DataFrame()

            history_days = len(df) / 24.0
            if history_days < MIN_HISTORY_DAYS_SHADOW:
                results.append(UniverseSymbolResult(
                    symbol=sym, adv_usd=adv, history_days=history_days,
                    status="rejected", reject_reason=f"本地历史仅{history_days:.1f}天,不足{MIN_HISTORY_DAYS_SHADOW}天无法评估",
                ))
                continue

            rets = df["close"].astype(float).pct_change().dropna()
            annualized_vol = float(rets.std() * math.sqrt(24 * 365)) if len(rets) > 2 else 0.0
            returns_by_symbol[sym] = rets.tail(CORR_LOOKBACK_HOURS)

            vol_fitness = self._vol_fitness_score(annualized_vol)
            results.append(UniverseSymbolResult(
                symbol=sym, adv_usd=adv, annualized_vol=annualized_vol,
                vol_fitness=vol_fitness, history_days=history_days,
                status="shadow" if history_days < 60 else "active",
            ))

        if not results:
            return results

        # 流动性分位（qualified 里所有通过STEP2的品种排名，含被历史不足淘汰的也在分母里更公允）
        advs_sorted = sorted(qualified.values())
        n = len(advs_sorted)
        for r in results:
            if r.status == "rejected":
                continue
            rank = sum(1 for v in advs_sorted if v <= r.adv_usd)
            r.liquidity_percentile = rank / n if n else 0.0

        # 低相关性奖励：与其余(已通过流动性门+历史门)品种的平均绝对相关系数越低越好
        corr_df = pd.DataFrame(returns_by_symbol).dropna(how="all")
        if corr_df.shape[1] >= 2 and corr_df.shape[0] >= 10:
            corr_matrix = corr_df.corr().abs()
            for r in results:
                if r.status == "rejected" or r.symbol not in corr_matrix.columns:
                    continue
                others = corr_matrix[r.symbol].drop(labels=[r.symbol], errors="ignore").dropna()
                avg_corr = float(others.mean()) if len(others) else 0.0
                r.low_corr_bonus = max(0.0, 1.0 - avg_corr)
        else:
            for r in results:
                if r.status != "rejected":
                    r.low_corr_bonus = 1.0  # 数据不足以判定相关性时不惩罚

        for r in results:
            if r.status == "rejected":
                continue
            r.composite_score = (
                0.4 * r.vol_fitness + 0.3 * r.liquidity_percentile + 0.3 * r.low_corr_bonus
            )

        results.sort(key=lambda r: r.composite_score, reverse=True)
        self._corr_matrix_cache = corr_df.corr() if corr_df.shape[1] >= 2 else None
        return results

    @staticmethod
    def _vol_fitness_score(annualized_vol: float) -> float:
        """年化波动率60%-200%为scalp最优区间，区间内=1.0，越远离越低（三角衰减）。"""
        if annualized_vol <= 0:
            return 0.0
        if VOL_SWEET_LOW <= annualized_vol <= VOL_SWEET_HIGH:
            return 1.0
        if annualized_vol < VOL_SWEET_LOW:
            return max(0.0, annualized_vol / VOL_SWEET_LOW)
        # > VOL_SWEET_HIGH：衰减到2倍上限时降为0
        span = VOL_SWEET_HIGH
        return max(0.0, 1.0 - (annualized_vol - VOL_SWEET_HIGH) / span)

    def _step4_correlation_dedup(self, scored: List[UniverseSymbolResult]) -> List[UniverseSymbolResult]:
        """STEP4: 按复合得分贪心选择，max(|corr|)>阈值与已选冲突则跳过。"""
        corr_matrix = getattr(self, "_corr_matrix_cache", None)
        selected: List[UniverseSymbolResult] = []
        for r in scored:
            if r.status == "rejected":
                continue
            if len(selected) >= MAX_UNIVERSE_SIZE:
                r.status = "rejected"
                r.reject_reason = f"已达Universe上限{MAX_UNIVERSE_SIZE}"
                continue
            conflict = False
            if corr_matrix is not None and r.symbol in corr_matrix.columns:
                for s in selected:
                    if s.symbol in corr_matrix.columns:
                        c = corr_matrix.loc[r.symbol, s.symbol]
                        if c is not None and abs(float(c)) > MAX_PAIRWISE_CORR:
                            conflict = True
                            break
            if conflict:
                r.status = "rejected"
                r.reject_reason = f"与已选品种相关性>{MAX_PAIRWISE_CORR}"
                continue
            selected.append(r)
        return selected

    # ------------------------------------------------------------------
    # STEP5a：高频动态降级检查（默认4h，只重查流动性，代价低）
    # ------------------------------------------------------------------

    def recheck_liquidity(self) -> None:
        if not self._state or not self._state.selected:
            return
        try:
            symbols = [r.symbol for r in self._state.selected]
            adv_map: Dict[str, float] = {}
            # 按需取数：asterdex 主所优先用数据中心 ticker 成交额
            try:
                from backend.services.exchange_config import get_active_exchange
                _ex = (get_active_exchange() or "asterdex").strip().lower()
                if _ex in ("aster", "asterdex"):
                    from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
                    _stats = asterdex_ticker_poller.get_all_stats()
                    for _sym in symbols:
                        _st = _stats.get(_sym.upper())
                        if _st and _st.get("quote_volume_24h"):
                            adv_map[_sym.upper()] = float(_st["quote_volume_24h"])
            except Exception:
                pass
            if not adv_map:
                from backend.services.hyperliquid_market_data import HyperliquidClient
                client = HyperliquidClient()
                bulk = client.get_bulk_ticker_data(symbols)
                adv_map = {
                    sym.upper(): float(data.get("volume24h") or 0.0)
                    for sym, data in bulk.items()
                }
        except Exception as e:
            logger.warning(f"[UniverseManager] recheck_liquidity ticker获取失败,跳过本轮: {e}")
            return

        changed = False
        for r in self._state.selected:
            adv = adv_map.get(r.symbol, 0.0)
            if adv <= 0:
                continue  # 拿不到数据时不误判降级（安全放行）
            was_degraded = r.status == "degraded"
            r.adv_usd = adv
            if adv < MIN_ADV_USD:
                if not was_degraded:
                    logger.warning(f"[UniverseManager] {r.symbol} 流动性跌破门槛(${adv:,.0f}<${MIN_ADV_USD:,.0f}),标记DEGRADED(仅平仓)")
                    r.status = "degraded"
                    changed = True
            elif was_degraded:
                logger.info(f"[UniverseManager] {r.symbol} 流动性恢复(${adv:,.0f}),解除DEGRADED")
                r.status = "active"
                changed = True

        if changed:
            self._state.generated_at = time.time()
            self._save_state()

    # ------------------------------------------------------------------
    # 供其它模块查询
    # ------------------------------------------------------------------

    def get_state(self) -> UniverseState:
        return self._state or UniverseState()

    def is_degraded(self, symbol: str) -> bool:
        if not self._state:
            return False
        sym = (symbol or "").upper()
        return sym in self._state.degraded_symbols()

    def is_qualified(self, symbol: str) -> bool:
        """symbol 是否在当前 Universe 里（active/shadow）。未重建过时默认放行
        （不能因为管理器还没跑过就把所有AI选币都拦掉——fail-open on cold start）。
        """
        if not self._state or not self._state.selected:
            return True
        sym = (symbol or "").upper()
        return sym in self._state.active_symbols()


universe_manager = UniverseManager()


def run_universe_rebuild() -> None:
    """供调度器调用：全量重建（默认每周）。"""
    try:
        universe_manager.rebuild()
    except Exception as e:
        logger.error(f"[UniverseManager] 定时全量重建失败: {e}", exc_info=True)


def run_universe_liquidity_recheck() -> None:
    """供调度器调用：高频流动性复查（默认4h）。"""
    try:
        universe_manager.recheck_liquidity()
    except Exception as e:
        logger.error(f"[UniverseManager] 定时流动性复查失败: {e}", exc_info=True)
