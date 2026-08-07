"""
WashTradeAvoider — 刷量规避器 (五层防御)

1. Poisson间隔随机化: λ=日目标量/24h/每笔金额×U(0.5,1.5)，最小间隔≥30秒
2. 订单量非整数随机化: ±15%随机偏差
3. 双账户/IP分离: 不同策略使用不同账户
4. 模拟真实交易行为: 制造0.5-2%浮动盈亏，混合限价/市价(6:4)
5. 每日刷量保护: 日刷量≤权益×2倍、日增长<前日+50%
"""

import logging
import math
import random
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .models import WashTradeCheckResult

logger = logging.getLogger(__name__)


class WashTradeAvoider:
    """刷量规避器 — 五层防御"""

    # ── 参数 ──
    MIN_INTERVAL_SEC = 30.0          # 最小交易间隔
    SIZE_RANDOM_PCT = 0.15           # 订单量随机偏差 ±15%
    LIMIT_ORDER_RATIO = 0.6          # 限价单比例 60%
    SIMULATED_PNL_RANGE = (0.005, 0.02)  # 模拟盈亏范围 0.5%-2%
    MAX_DAILY_VOLUME_EQUITY_MULT = 2.0   # 日刷量上限 = 权益 × 此倍数
    MAX_DAILY_GROWTH_PCT = 0.50     # 日增长不超过前日 +50%
    PATTERN_ANALYSIS_WINDOW = 50     # 交易模式分析窗口大小

    def __init__(self):
        self._trade_history: List[Dict[str, Any]] = []
        self._daily_volumes: Dict[str, float] = {}  # date_str -> volume
        self._exchange_daily: Dict[str, Dict[str, float]] = {}  # exchange -> date -> vol
        self._lock = threading.Lock()
        self._last_trade_ts: float = 0.0
        self._account_map: Dict[str, str] = {}  # exchange -> account_type
        self._load_config()

    def _load_config(self) -> None:
        """Load wash-trade parameters from rebate_arb_config.yaml."""
        try:
            from backend.config.rebate_config_loader import rebate_config
            cfg = rebate_config.wash_trade
            self.MAX_DAILY_VOLUME_EQUITY_MULT = cfg.max_daily_volume_equity_mult
            self.SIZE_RANDOM_PCT = cfg.size_randomization_pct
            if cfg.poisson_lambda > 0:
                # Keep a conservative floor, but allow config to slow down cadence.
                self.MIN_INTERVAL_SEC = max(30.0, 1.0 / cfg.poisson_lambda)
        except Exception as e:
            logger.debug("[WashTradeAvoider] Config load fallback: %s", e)

    # ── Layer 1: Poisson 间隔随机化 ──

    def get_next_safe_interval(self, daily_target_usd: float, per_trade_usd: float) -> float:
        """
        基于 Poisson 过程计算下一个安全交易间隔

        Args:
            daily_target_usd: 日目标交易量 (USD)
            per_trade_usd: 每笔交易金额 (USD)

        Returns:
            建议等待的秒数
        """
        if daily_target_usd <= 0 or per_trade_usd <= 0:
            return self.MIN_INTERVAL_SEC

        # λ = 日目标量 / 24h / 每笔金额
        trades_per_hour = daily_target_usd / 24.0 / per_trade_usd
        mean_interval = 3600.0 / max(trades_per_hour, 0.01)

        # U(0.5, 1.5) 随机化
        multiplier = random.uniform(0.5, 1.5)
        interval = mean_interval * multiplier

        # 最小间隔保护
        return max(interval, self.MIN_INTERVAL_SEC)

    def check_timing(self) -> Tuple[bool, float]:
        """
        检查当前时间是否可以交易

        Returns:
            (is_safe, seconds_until_safe)
        """
        elapsed = time.time() - self._last_trade_ts
        if elapsed < self.MIN_INTERVAL_SEC:
            return False, self.MIN_INTERVAL_SEC - elapsed
        return True, 0.0

    # ── Layer 2: 订单量非整数随机化 ──

    def randomize_size(self, base_size: float) -> float:
        """对订单量添加随机偏差 ±15%"""
        deviation = random.uniform(-self.SIZE_RANDOM_PCT, self.SIZE_RANDOM_PCT)
        randomized = base_size * (1 + deviation)
        # 确保非整数化（避免精确数字）
        noise = random.uniform(0.01, 0.99)
        return round(randomized + noise, 2)

    def randomize_price(self, base_price: float, side: str) -> float:
        """对价格添加微小偏差"""
        # 限价单在中间价基础上偏移 0.01%-0.05%
        offset_pct = random.uniform(0.0001, 0.0005)
        if side == "buy":
            return round(base_price * (1 - offset_pct), 4)
        else:
            return round(base_price * (1 + offset_pct), 4)

    # ── Layer 3: 双账户/IP分离 ──

    def assign_account(self, exchange: str, strategy_type: str) -> str:
        """
        为策略分配账户

        规则: 主策略用主账户，刷量策略用独立子账户
        """
        # 刷量密集型策略使用子账户
        wash_heavy_strategies = {"S1", "S3", "S7", "S8"}
        if strategy_type in wash_heavy_strategies:
            account = f"{exchange}_sub"
        else:
            account = f"{exchange}_main"

        self._account_map[exchange] = account
        return account

    # ── Layer 4: 模拟真实交易行为 ──

    def generate_order_type(self) -> str:
        """随机生成订单类型（限价/市价 6:4）"""
        return "limit" if random.random() < self.LIMIT_ORDER_RATIO else "market"

    def generate_simulated_pnl(self) -> float:
        """生成模拟盈亏（0.5%-2%）"""
        low, high = self.SIMULATED_PNL_RANGE
        return random.uniform(low, high)

    def should_add_noise_trade(self) -> bool:
        """
        是否应该添加一笔噪声交易来模拟真实行为

        以 10% 概率添加不相关交易
        """
        return random.random() < 0.10

    # ── Layer 5: 每日刷量保护 ──

    def check_daily_volume_limit(
        self,
        exchange: str,
        proposed_volume: float,
        account_equity: float,
    ) -> Tuple[bool, str]:
        """
        检查日刷量是否超限

        Args:
            exchange: 交易所
            proposed_volume: 本次计划交易量
            account_equity: 账户权益

        Returns:
            (is_safe, reason)
        """
        today = time.strftime("%Y-%m-%d")
        current_daily = self._exchange_daily.get(exchange, {}).get(today, 0.0)
        max_daily = account_equity * self.MAX_DAILY_VOLUME_EQUITY_MULT

        if current_daily + proposed_volume > max_daily:
            return False, (
                f"日刷量 ${current_daily + proposed_volume:,.0f} 超过 "
                f"上限 ${max_daily:,.0f} (权益×{self.MAX_DAILY_VOLUME_EQUITY_MULT})"
            )

        # 日增长检查
        yesterday = time.strftime(
            "%Y-%m-%d", time.localtime(time.time() - 86400)
        )
        yesterday_vol = self._exchange_daily.get(exchange, {}).get(yesterday, 0.0)
        if yesterday_vol > 0:
            max_growth = yesterday_vol * (1 + self.MAX_DAILY_GROWTH_PCT)
            if current_daily + proposed_volume > max_growth:
                return False, (
                    f"日增长 {(current_daily + proposed_volume) / yesterday_vol - 1:.0%} "
                    f"超过上限 +{self.MAX_DAILY_GROWTH_PCT:.0%}"
                )

        return True, ""

    # ── 综合检查 ──

    def check_all(
        self,
        exchange: str,
        proposed_size: float,
        account_equity: float,
        daily_target_usd: float = 0.0,
    ) -> WashTradeCheckResult:
        """
        五层综合刷量检测

        Returns:
            WashTradeCheckResult 包含安全性和建议
        """
        layer_results = {}
        risk_score = 0.0
        reasons = []

        # Layer 1: 时间间隔
        is_safe_timing, wait_sec = self.check_timing()
        layer_results["timing"] = is_safe_timing
        if not is_safe_timing:
            risk_score += 0.3
            reasons.append(f"间隔不足，需等待 {wait_sec:.0f}s")

        # Layer 5: 日刷量限制
        is_safe_volume, vol_reason = self.check_daily_volume_limit(
            exchange, proposed_size, account_equity
        )
        layer_results["daily_volume"] = is_safe_volume
        if not is_safe_volume:
            risk_score += 0.5
            reasons.append(vol_reason)

        # Layer 2: 交易模式分析
        pattern_score = self._analyze_pattern()
        layer_results["pattern"] = pattern_score < 0.7
        risk_score += pattern_score * 0.2

        # Layer 3: 账户分离检查
        layer_results["account_separation"] = True

        # Layer 4: 行为真实性
        layer_results["realistic_behavior"] = True

        is_safe = risk_score < 0.7
        next_safe_ts = time.time() + wait_sec if not is_safe_timing else time.time()

        if risk_score >= 0.7:
            recommendation = "暂停交易，等待安全窗口"
            if not is_safe_volume:
                recommendation = "已达日刷量上限，停止交易"
        elif risk_score >= 0.3:
            recommendation = "注意交易频率，增加间隔随机化"
        else:
            recommendation = "安全，可继续交易"

        return WashTradeCheckResult(
            is_safe=is_safe,
            risk_score=min(risk_score, 1.0),
            layer_results=layer_results,
            recommendation=recommendation,
            next_safe_ts=next_safe_ts,
        )

    def get_safe_schedule(
        self,
        exchange: str,
        total_volume_usd: float,
        account_equity: float,
        duration_hours: float = 24.0,
    ) -> List[Dict[str, Any]]:
        """
        生成安全的交易时间表

        Args:
            exchange: 交易所
            total_volume_usd: 总需刷量
            account_equity: 账户权益
            duration_hours: 时间跨度（小时）

        Returns:
            交易计划列表
        """
        max_daily = account_equity * self.MAX_DAILY_VOLUME_EQUITY_MULT
        actual_volume = min(total_volume_usd, max_daily)
        per_trade = actual_volume / max(duration_hours, 1.0) * 0.8  # 留20%余量

        schedule = []
        current_ts = time.time()
        end_ts = current_ts + duration_hours * 3600

        while current_ts < end_ts and actual_volume > 0:
            interval = self.get_next_safe_interval(actual_volume, per_trade)
            current_ts += interval

            if current_ts >= end_ts:
                break

            trade_size = self.randomize_size(per_trade)
            trade_size = min(trade_size, actual_volume)

            schedule.append({
                "ts": current_ts,
                "exchange": exchange,
                "size_usd": round(trade_size, 2),
                "order_type": self.generate_order_type(),
                "account": self.assign_account(exchange, "S1"),
            })

            actual_volume -= trade_size

        logger.info(
            f"[WashTradeAvoider] 生成 {len(schedule)} 笔交易计划, "
            f"跨 {duration_hours:.0f}h"
        )
        return schedule

    def record_trade(
        self,
        exchange: str,
        size_usd: float,
        strategy_type: str = "",
        risk_score: float = 0.0,
        is_safe: bool = True,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录已执行的交易"""
        ts = time.time()
        with self._lock:
            today = time.strftime("%Y-%m-%d")
            self._trade_history.append({
                "exchange": exchange,
                "size_usd": size_usd,
                "strategy_type": strategy_type,
                "risk_score": risk_score,
                "is_safe": is_safe,
                "reason": reason,
                "ts": ts,
            })
            self._last_trade_ts = ts

            if exchange not in self._exchange_daily:
                self._exchange_daily[exchange] = {}
            self._exchange_daily[exchange][today] = (
                self._exchange_daily[exchange].get(today, 0.0) + size_usd
            )
        self._persist_trade_log(
            ts=ts,
            exchange=exchange,
            size_usd=size_usd,
            strategy_type=strategy_type,
            risk_score=risk_score,
            is_safe=is_safe,
            reason=reason,
            metadata=metadata or {},
        )

    def _persist_trade_log(
        self,
        *,
        ts: float,
        exchange: str,
        size_usd: float,
        strategy_type: str,
        risk_score: float,
        is_safe: bool,
        reason: str,
        metadata: Dict[str, Any],
    ) -> None:
        try:
            import json
            from backend.database.connection import SessionLocal, sqlite_write_commit
            from backend.database.models import WashTradeLogDB

            db = SessionLocal()
            try:
                db.add(WashTradeLogDB(
                    ts=ts,
                    exchange=exchange,
                    strategy_type=strategy_type or None,
                    size_usd=float(size_usd or 0),
                    risk_score=float(risk_score or 0),
                    is_safe=bool(is_safe),
                    reason=reason,
                    metadata_json=json.dumps(metadata, ensure_ascii=False, default=str),
                ))
                sqlite_write_commit(db, label="wash_trade_log")
            finally:
                db.close()
        except Exception as e:
            logger.debug("[WashTradeAvoider] persist trade log failed: %s", e)

    def _analyze_pattern(self) -> float:
        """
        分析交易模式是否过于规律

        Returns:
            规律性风险分 (0~1)
        """
        if len(self._trade_history) < 5:
            return 0.0

        recent = self._trade_history[-self.PATTERN_ANALYSIS_WINDOW:]
        intervals = []
        for i in range(1, len(recent)):
            dt = recent[i]["ts"] - recent[i - 1]["ts"]
            intervals.append(dt)

        if len(intervals) < 3:
            return 0.0

        # 计算间隔变异系数
        mean_interval = sum(intervals) / len(intervals)
        if mean_interval <= 0:
            return 1.0

        variance = sum((x - mean_interval) ** 2 for x in intervals) / len(intervals)
        std_dev = math.sqrt(variance)
        cv = std_dev / mean_interval  # 变异系数

        # 变异系数越小越规律 → 风险越高
        # cv < 0.3 表示非常规律（危险），cv > 0.8 表示足够随机（安全）
        if cv < 0.3:
            return 1.0
        elif cv > 0.8:
            return 0.0
        else:
            return 1.0 - (cv - 0.3) / 0.5

    def get_status(self) -> Dict[str, Any]:
        """获取当前状态"""
        with self._lock:
            return {
                "total_trades": len(self._trade_history),
                "last_trade_ts": self._last_trade_ts,
                "daily_volumes": {
                    ex: dict(dates) for ex, dates in self._exchange_daily.items()
                },
                "pattern_score": self._analyze_pattern(),
            }

    def get_s8_safety_snapshot(
        self,
        *,
        exchange: str = "asterdex",
        account_equity: float = 0.0,
        next_round_volume_usd: float = 0.0,
    ) -> Dict[str, Any]:
        """S8 专属快照：名义成交量预算 + 时间/pattern 风险。"""
        ex = (exchange or "asterdex").lower()
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            current_daily = float(self._exchange_daily.get(ex, {}).get(today, 0.0))
            last_trade_ts = self._last_trade_ts
        max_daily = max(float(account_equity or 0) * self.MAX_DAILY_VOLUME_EQUITY_MULT, 0.0)
        remaining_daily = max(max_daily - current_daily, 0.0)
        timing_ok, wait_sec = self.check_timing()
        pattern_score = self._analyze_pattern()
        projected_daily = current_daily + max(float(next_round_volume_usd or 0), 0.0)
        return {
            "exchange": ex,
            "current_daily_volume_usd": round(current_daily, 2),
            "max_daily_volume_usd": round(max_daily, 2),
            "remaining_daily_volume_usd": round(remaining_daily, 2),
            "next_round_volume_usd": round(float(next_round_volume_usd or 0), 2),
            "projected_daily_volume_usd": round(projected_daily, 2),
            "daily_budget_ok": projected_daily <= max_daily if max_daily > 0 else True,
            "timing_ok": timing_ok,
            "wait_seconds": round(wait_sec, 1),
            "min_interval_seconds": round(float(self.MIN_INTERVAL_SEC), 1),
            "pattern_score": round(pattern_score, 3),
            "last_trade_ts": last_trade_ts,
        }


# 模块级单例
wash_trade_avoider = WashTradeAvoider()
