"""CryptoAlphaSignals — 币圈永续合约原生 alpha 信号统一接口。

把系统里已采集但"算而不用"的币圈独有信号封装成统一接口，供短/中/长
三层策略调用。这些 alpha 是学术论文证实、币圈永续合约独有的，传统股
票/外汇市场没有对应物：

  1. 清算簇磁吸 (liquidation magnet)  —— 币圈最大可预测波动源
     多/空清算不对称时，价格倾向朝清算密集的方向移动（级联清算）。
     参考：CryptoCred 期货指标指南；SSRN《Fundamentals of Perpetual Futures》
  2. 资金费率-OI 背离 (funding-oi divergence)
     OI 上升 + funding 未跟上 = 多头在悄悄积累（看多前瞻信号）。
     参考：SSRN《Funding Rate Mechanism in Perpetual Futures》(2026)
  3. 累计成交量差 (CVD) 压力
     主动买/卖吃单净流，短线微观结构最有效的方向信号。
     参考：Order Flow Imbalance (OFI) 文献；Bookmap/Coinalyze CVD 策略
  4. 订单簿失衡 (orderbook imbalance, OBI)
     买/卖盘挂单失衡，预测短期价格漂移。
     参考：HFTBacktest《Market Making with Alpha - Order Book Imbalance》

设计原则：
- 单例 + TTL 缓存，避免高频调用打爆 DB/API。
- 所有方法 try/except 优雅降级，数据缺失返回 neutral，绝不阻塞主流程。
- 整体可通过 CRYPTO_ALPHA_ENABLED=false 关闭（出问题快速回退）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── 全局开关 ──
CRYPTO_ALPHA_ENABLED = os.getenv("CRYPTO_ALPHA_ENABLED", "true").lower() in ("1", "true", "yes", "on")

# 缓存 TTL（秒）。清算簇/OI/funding 走 derivatives_analytics 自己的缓存，
# 这里只缓存 DB 直查的 CVD/OBI 结果。
_CACHE_TTL = 30


@dataclass
class AlphaReading:
    """单个 alpha 信号的标准化读数。

    direction: "long" / "short" / "neutral"  —— 信号指向的方向
    strength:  0.0 - 1.0                      —— 信号强度（0=无信号）
    severity:  "low"/"medium"/"high"          —— 币圈特有，标记清算簇等极端事件
    raw: Dict                                 —— 原始数据（调试/日志用）
    available: bool                           —— 是否有真实数据（False=降级中性）
    note: str                                 —— 人类可读说明
    """
    direction: str = "neutral"
    strength: float = 0.0
    severity: str = "low"
    raw: Dict[str, Any] = field(default_factory=dict)
    available: bool = False
    note: str = "no data"


@dataclass
class CryptoAlphaBundle:
    """一个 symbol 的全部 alpha 读数打包，供三层策略一次取用。"""
    liquidation_magnet: AlphaReading = field(default_factory=AlphaReading)
    funding_oi_divergence: AlphaReading = field(default_factory=AlphaReading)
    cvd_pressure: AlphaReading = field(default_factory=AlphaReading)
    orderbook_imbalance: AlphaReading = field(default_factory=AlphaReading)

    def to_prompt_block(self) -> str:
        """生成注入 LLM prompt 的结构化区块。只有 available 的信号才输出。"""
        lines = []
        if self.liquidation_magnet.available:
            lm = self.liquidation_magnet
            lines.append(
                f"- 清算磁吸: {lm.note} → 偏{lm.direction}"
                f"(强度{lm.strength:.0%},severity={lm.severity})"
            )
        if self.funding_oi_divergence.available:
            foid = self.funding_oi_divergence
            lines.append(
                f"- 资金费率-OI背离: {foid.note} → 偏{foid.direction}"
                f"(强度{foid.strength:.0%})"
            )
        if self.cvd_pressure.available:
            cvd = self.cvd_pressure
            lines.append(
                f"- CVD压力: {cvd.note} → 偏{cvd.direction}"
                f"(强度{cvd.strength:.0%})"
            )
        if self.orderbook_imbalance.available:
            obi = self.orderbook_imbalance
            lines.append(
                f"- 订单簿失衡: {obi.note} → 偏{obi.direction}"
                f"(强度{obi.strength:.0%})"
            )
        if not lines:
            return ""
        return "## 币圈衍生品 alpha（币圈独有，权重高于普通技术指标）\n" + "\n".join(lines)


def _neutral(note: str = "no data") -> AlphaReading:
    return AlphaReading(direction="neutral", strength=0.0, note=note, available=False)


class CryptoAlphaSignals:
    """币圈原生 alpha 信号聚合器（单例）。"""

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
        # 缓存: (symbol, method) → (timestamp, AlphaReading)
        self._cache: Dict[tuple, tuple] = {}
        logger.info(
            "[CryptoAlpha] 币圈 alpha 信号服务初始化 (enabled=%s)", CRYPTO_ALPHA_ENABLED
        )

    # ════════════════════════════════════════════════════════════
    #  统一入口：一次取全部 alpha
    # ════════════════════════════════════════════════════════════

    def get_bundle(self, symbol: str) -> CryptoAlphaBundle:
        """获取一个 symbol 的全部 alpha 读数。供三层策略调用。"""
        if not CRYPTO_ALPHA_ENABLED:
            return CryptoAlphaBundle()
        return CryptoAlphaBundle(
            liquidation_magnet=self.liquidation_magnet(symbol),
            funding_oi_divergence=self.funding_oi_divergence(symbol),
            cvd_pressure=self.cumulative_cvd_pressure(symbol),
            orderbook_imbalance=self.orderbook_imbalance(symbol),
        )

    # ════════════════════════════════════════════════════════════
    #  1. 清算簇磁吸 —— 币圈最大可预测波动源
    # ════════════════════════════════════════════════════════════

    def liquidation_magnet(self, symbol: str) -> AlphaReading:
        """清算磁吸方向。

        多/空清算严重不对称时，价格倾向朝清算密集方向移动（级联清算）：
        - 空头清算远超多头 → 上方磁吸（价格倾向上涨，空头被强平推高）
        - 多头清算远超空头 → 下方磁吸（价格倾向下跌，多头被强平砸低）

        数据源：derivatives_analytics_service.get_liquidation_clusters（已聚合
        Binance/Hyperliquid/Coinalyze 免费源，带 60s 缓存）。
        """
        if not CRYPTO_ALPHA_ENABLED:
            return _neutral("disabled")
        cached = self._get_cached("liq", symbol)
        if cached is not None:
            return cached
        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics
            # 只读缓存 + 后台刷新：绝不在 scalp 热路径同步拉 Hyperliquid/Binance/Coinalyze
            # （原 get_snapshot miss 时串行网络实测 ~12s，是 evaluate 偶发 8~68s 的元凶）
            cluster = derivatives_analytics.get_liquidation_clusters(symbol, cached_only=True)
            if not cluster:
                return self._cache_and_return("liq", symbol, _neutral("无清算簇数据"))

            bias = cluster.get("bias", "balanced")
            signal = cluster.get("signal", "neutral")
            severity = cluster.get("severity", "low")
            total = float(cluster.get("total_1h", 0) or 0)
            liq_long = float(cluster.get("liquidation_long_1h", 0) or 0)
            liq_short = float(cluster.get("liquidation_short_1h", 0) or 0)

            # 方向：磁吸 upward_magnet → 价格倾向上涨 → long
            #       磁吸 downward_magnet → 价格倾向下跌 → short
            if "upward" in bias:
                direction = "long"
            elif "downward" in bias:
                direction = "short"
            else:
                direction = "neutral"

            # 强度：severity 映射 + 清算绝对量加成
            sev_strength = {"low": 0.2, "medium": 0.5, "high": 0.85}.get(severity, 0.2)
            # 清算量 > $5M 时加成（量越大级联动能越强）
            if total > 5_000_000:
                sev_strength = min(1.0, sev_strength + 0.15)
            elif total > 20_000_000:
                sev_strength = 1.0

            note = (
                f"{'上方' if 'upward' in bias else '下方' if 'downward' in bias else '无'}"
                f"磁吸(severity={severity},清算${total/1e6:.1f}M,"
                f"多空清={liq_long/1e6:.1f}/{liq_short/1e6:.1f}M)"
            )
            reading = AlphaReading(
                direction=direction,
                strength=sev_strength if direction != "neutral" else 0.0,
                severity=severity,
                raw=cluster,
                available=True,
                note=note,
            )
            return self._cache_and_return("liq", symbol, reading)
        except Exception as e:
            logger.debug("[CryptoAlpha] liquidation_magnet %s 失败: %s", symbol, str(e)[:100])
            return _neutral(f"查询失败:{type(e).__name__}")

    # ════════════════════════════════════════════════════════════
    #  2. 资金费率-OI 背离 —— 前瞻建仓信号
    # ════════════════════════════════════════════════════════════

    def funding_oi_divergence(self, symbol: str) -> AlphaReading:
        """资金费率与 OI 变化的背离（前瞻建仓信号）。

        - OI↑ + funding↓/中性 → 多头在悄悄积累（funding 还没反映仓位），看多
        - OI↑ + funding↑     → 过度杠杆（多头拥挤），反转警告，看空
        - OI↓ + funding↑     → 空头平仓推动，看多

        数据源：derivatives_analytics_service.DerivativesSnapshot（funding_rate,
        oi_change_1h, oi_total）。
        """
        if not CRYPTO_ALPHA_ENABLED:
            return _neutral("disabled")
        cached = self._get_cached("foid", symbol)
        if cached is not None:
            return cached
        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics
            # 只读缓存 + 后台刷新：热路径不阻塞（同上，避免 evaluate 被同步网络拖到几十秒）
            snap = derivatives_analytics.get_cached_snapshot(symbol)
            if snap is None:
                return self._cache_and_return("foid", symbol, _neutral("无衍生品快照"))

            funding = float(snap.funding_rate or 0)
            oi_change_1h = float(snap.oi_change_1h or 0)
            oi_total = float(snap.oi_total or 0)

            if oi_total <= 0:
                return self._cache_and_return("foid", symbol, _neutral("OI=0"))

            direction = "neutral"
            strength = 0.0
            note = ""

            # OI 上升（资金在进场）
            if oi_change_1h > 0.02:  # OI +2% 以上算显著进场
                if funding < 0.00003:  # funding 接近中性或负
                    # 多头积累，funding 没跟上 → 看多
                    direction = "long"
                    strength = min(0.7, 0.3 + abs(oi_change_1h) * 5)
                    note = f"OI+{oi_change_1h:.1%}但funding={funding*100:.3f}%(低)→多头悄悄积累,看多"
                elif funding > 0.00015:  # funding 显著正
                    # 多头拥挤，反转风险 → 看空
                    direction = "short"
                    strength = min(0.6, 0.2 + abs(funding) * 1500)
                    note = f"OI+{oi_change_1h:.1%}且funding={funding*100:.3f}%(高)→多头拥挤,反转警告,看空"
            # OI 下降（资金在离场）
            elif oi_change_1h < -0.02:
                if funding > 0.00015:
                    # 高 funding + OI 降 → 空头未平但多头在撤，看空
                    direction = "short"
                    strength = min(0.5, 0.2 + abs(oi_change_1h) * 4)
                    note = f"OI{oi_change_1h:.1%}且funding高→多头撤离,看空"

            if not note:
                note = f"OI{oi_change_1h:+.1%},funding={funding*100:.3f}%→无显著背离"

            reading = AlphaReading(
                direction=direction, strength=strength, severity="low",
                raw={"funding": funding, "oi_change_1h": oi_change_1h, "oi_total": oi_total},
                available=True, note=note,
            )
            return self._cache_and_return("foid", symbol, reading)
        except Exception as e:
            logger.debug("[CryptoAlpha] funding_oi_divergence %s 失败: %s", symbol, str(e)[:100])
            return _neutral(f"查询失败:{type(e).__name__}")

    # ════════════════════════════════════════════════════════════
    #  3. CVD 压力 —— 短线微观结构方向
    # ════════════════════════════════════════════════════════════

    def cumulative_cvd_pressure(self, symbol: str, lookback_minutes: int = 60) -> AlphaReading:
        """累计成交量差（CVD）压力。

        CVD = sum(taker_buy_notional - taker_sell_notional) over lookback。
        正值大 → 主动买盘占优（看多）；负值大 → 主动卖盘占优（看空）。

        数据源：MarketTradesAggregated（Binance/采集器写入的 15s 聚合吃单）。
        """
        if not CRYPTO_ALPHA_ENABLED:
            return _neutral("disabled")
        cache_key = f"cvd:{lookback_minutes}"
        cached = self._get_cached(cache_key, symbol)
        if cached is not None:
            return cached
        try:
            from backend.database.connection import MarketSessionLocal
            from backend.database.models import MarketTradesAggregated
            from sqlalchemy import func

            db = MarketSessionLocal()
            try:
                now_ms = int(time.time() * 1000)
                since_ms = now_ms - lookback_minutes * 60 * 1000
                row = db.query(
                    func.sum(MarketTradesAggregated.taker_buy_notional).label("buy"),
                    func.sum(MarketTradesAggregated.taker_sell_notional).label("sell"),
                ).filter(
                    MarketTradesAggregated.symbol == symbol.upper(),
                    MarketTradesAggregated.timestamp >= since_ms,
                ).first()

                if row is None or row.buy is None or row.sell is None:
                    return self._cache_and_return(cache_key, symbol, _neutral("无CVD数据"))

                buy = float(row.buy or 0)
                sell = float(row.sell or 0)
                total = buy + sell
                if total <= 0:
                    return self._cache_and_return(cache_key, symbol, _neutral("CVD总量=0"))

                cvd = buy - sell
                # 归一化到 [-1, 1]：cvd/total
                cvd_ratio = cvd / total
                direction = "long" if cvd_ratio > 0.08 else "short" if cvd_ratio < -0.08 else "neutral"
                strength = min(1.0, abs(cvd_ratio) * 3.0) if direction != "neutral" else 0.0
                note = (
                    f"CVD({lookback_minutes}min)={cvd/1e6:+.2f}M"
                    f"(买${buy/1e6:.1f}M/卖${sell/1e6:.1f}M,ratio={cvd_ratio:+.2%})"
                )
                reading = AlphaReading(
                    direction=direction, strength=strength, severity="low",
                    raw={"cvd": cvd, "buy": buy, "sell": sell, "ratio": cvd_ratio},
                    available=True, note=note,
                )
                return self._cache_and_return(cache_key, symbol, reading)
            finally:
                db.close()
        except Exception as e:
            logger.debug("[CryptoAlpha] cvd_pressure %s 失败: %s", symbol, str(e)[:100])
            return _neutral(f"查询失败:{type(e).__name__}")

    # ════════════════════════════════════════════════════════════
    #  4. 订单簿失衡 (OBI) —— 挂单压力
    # ════════════════════════════════════════════════════════════

    def orderbook_imbalance(self, symbol: str) -> AlphaReading:
        """订单簿失衡（OBI）。

        OBI = (bid_depth - ask_depth) / (bid_depth + ask_depth)。
        用 10 档深度（比 5 档更稳健）。正值 → 买盘挂单厚（看多）。

        数据源：MarketOrderbookSnapshots（采集器写入的最新快照）。
        """
        if not CRYPTO_ALPHA_ENABLED:
            return _neutral("disabled")
        cached = self._get_cached("obi", symbol)
        if cached is not None:
            return cached
        try:
            from backend.database.connection import MarketSessionLocal
            from backend.database.models import MarketOrderbookSnapshots

            db = MarketSessionLocal()
            try:
                snap = db.query(MarketOrderbookSnapshots).filter(
                    MarketOrderbookSnapshots.symbol == symbol.upper(),
                ).order_by(
                    MarketOrderbookSnapshots.timestamp.desc()
                ).first()

                if snap is None:
                    return self._cache_and_return("obi", symbol, _neutral("无订单簿快照"))

                bid = float(snap.bid_depth_10 or snap.bid_depth_5 or 0)
                ask = float(snap.ask_depth_10 or snap.ask_depth_5 or 0)
                total = bid + ask
                if total <= 0:
                    return self._cache_and_return("obi", symbol, _neutral("订单簿深度=0"))

                obi = (bid - ask) / total
                direction = "long" if obi > 0.12 else "short" if obi < -0.12 else "neutral"
                strength = min(1.0, abs(obi) * 2.5) if direction != "neutral" else 0.0
                note = (
                    f"OBI={obi:+.2%}"
                    f"(买盘10档${bid/1e3:.0f}K/卖盘${ask/1e3:.0f}K)"
                )
                reading = AlphaReading(
                    direction=direction, strength=strength, severity="low",
                    raw={"obi": obi, "bid": bid, "ask": ask},
                    available=True, note=note,
                )
                return self._cache_and_return("obi", symbol, reading)
            finally:
                db.close()
        except Exception as e:
            logger.debug("[CryptoAlpha] orderbook_imbalance %s 失败: %s", symbol, str(e)[:100])
            return _neutral(f"查询失败:{type(e).__name__}")

    # ════════════════════════════════════════════════════════════
    #  缓存工具
    # ════════════════════════════════════════════════════════════

    def _get_cached(self, method: str, symbol: str) -> Optional[AlphaReading]:
        key = (method, symbol.upper())
        entry = self._cache.get(key)
        if entry and (time.time() - entry[0]) < _CACHE_TTL:
            return entry[1]
        return None

    def _cache_and_return(self, method: str, symbol: str, reading: AlphaReading) -> AlphaReading:
        key = (method, symbol.upper())
        self._cache[key] = (time.time(), reading)
        # 清理过期项（防内存增长）
        if len(self._cache) > 500:
            cutoff = time.time() - _CACHE_TTL * 3
            self._cache = {k: v for k, v in self._cache.items() if v[0] > cutoff}
        return reading

    def invalidate(self, symbol: str = "") -> None:
        """清空缓存（数据刷新后可调）。symbol 为空则全清。"""
        if not symbol:
            self._cache.clear()
        else:
            sym = symbol.upper()
            self._cache = {k: v for k, v in self._cache.items() if k[1] != sym}


# 全局单例
crypto_alpha = CryptoAlphaSignals()
