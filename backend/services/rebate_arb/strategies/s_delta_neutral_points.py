"""SDN: delta-neutral 刷积分核心策略（Phase 2）。

2026-07-06 新增——2026 年主流刷分范式的载体：
    在**有 active 积分**的 DEX 开**多**（长腿，赚积分），在**深流动性**场所开**等额空**
    （空腿，对冲方向风险），两腿名义相等、方向相反 → delta 中性、无方向敞口。
    净收益 = 资金费价差（空腿收 - 长腿付）+ 折现后积分价值 - 手续费。

与旧策略的区别：
    - S8(Aster Stage6) 已随活动结束退役；本策略把"刷分"从单腿裸方向升级为对冲中性。
    - 复用 funding_rate_matrix（多场所资金费矩阵）选最优多空腿组合；
    - 复用 points_valuation（FDV 折现）诚实估值积分，宁可低估；
    - 数据来源为离线 program_registry + 传入的资金费率，无网络依赖。

诚实原则：没有资金费率数据时**不臆造机会**，直接返回 not viable 并说明原因，
避免"看着有机会实则空转/幻觉"。
"""

import logging
from typing import Any, Dict, List, Optional

from ..models import RebateStrategyType, StrategyEvaluation

logger = logging.getLogger(__name__)


class DeltaNeutralPointsStrategy:
    """SDN: delta-neutral 多积分DEX做多 + 深场所做空，赚资金费价差 + 刷积分。"""

    MIN_EQUITY = 100.0
    # 每笔组合用的名义（按账户权益 × 杠杆的一部分；delta 中性可适度放大）
    DEFAULT_LEVERAGE = 3
    NOTIONAL_EQUITY_PCT = 0.5      # 单组合用 50% 权益作单腿名义基数
    # 默认/最短持有期（天）：用于摊销一次性手续费、年化净 EV
    HORIZON_DAYS = 7.0
    # 自适应持有期上限（天）：正 carry 但保本期较长时可延长持有以摊平手续费，
    # 但不超过此上限（应 <= 引擎 MAX_HOLDING_DAYS）。
    MAX_HORIZON_DAYS = 21.0
    # 自适应持有期相对保本期的安全系数：持到 breakeven×该系数，确保跨过保本、留正收益。
    HORIZON_SAFETY_FACTOR = 1.5
    # 只接受净年化 >= 此阈值的组合（保守，扣成本后仍需正收益）
    MIN_NET_APR = 0.05
    # 积分估值默认取保守档，避免纸面富贵
    USE_CONSERVATIVE_POINTS = True

    def __init__(self, config: Dict = None):
        self.strategy_llm_config_id: Optional[int] = None
        self.execution_llm_config_id: Optional[int] = None
        self.account_id: Optional[int] = None
        if config:
            self.MIN_EQUITY = config.get("min_equity", self.MIN_EQUITY)
            self.DEFAULT_LEVERAGE = config.get("default_leverage", self.DEFAULT_LEVERAGE)
            self.NOTIONAL_EQUITY_PCT = config.get("notional_equity_pct", self.NOTIONAL_EQUITY_PCT)
            self.HORIZON_DAYS = config.get("horizon_days", self.HORIZON_DAYS)
            self.MAX_HORIZON_DAYS = config.get("max_horizon_days", self.MAX_HORIZON_DAYS)
            self.HORIZON_SAFETY_FACTOR = config.get(
                "horizon_safety_factor", self.HORIZON_SAFETY_FACTOR
            )
            self.MIN_NET_APR = config.get("min_net_apr", self.MIN_NET_APR)
            self.USE_CONSERVATIVE_POINTS = bool(
                config.get("use_conservative_points", self.USE_CONSERVATIVE_POINTS)
            )
            self.strategy_llm_config_id = config.get("strategy_llm_config_id")
            self.execution_llm_config_id = config.get("execution_llm_config_id")
            self.account_id = config.get("account_id")

    def update_params(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            upper_key = key.upper()
            if hasattr(self, upper_key):
                setattr(self, upper_key, value)
            elif hasattr(self, key):
                setattr(self, key, value)

    # ── 数据装配 ──
    def _extract_funding_rates(self, incentive_data: Dict) -> Dict[str, Dict[str, float]]:
        """从 incentive_data 取资金费率矩阵输入。

        约定：incentive_data["funding_rates"] = {exchange: {symbol: rate}}。
        无此字段（如离线/无实时快照）时返回空 → 策略据此判为 not viable。
        """
        fr = incentive_data.get("funding_rates") if isinstance(incentive_data, dict) else None
        if isinstance(fr, dict) and fr:
            return fr
        return {}

    def _best_combo(self, incentive_data: Dict) -> Optional[Dict[str, Any]]:
        """扫描资金费矩阵，返回长腿落在 active 积分场所的最优组合（dict）。"""
        from backend.services.rebate_arb.funding_rate_matrix import scan_funding_matrix

        funding_rates = self._extract_funding_rates(incentive_data)
        if not funding_rates:
            return None
        combos = scan_funding_matrix(
            funding_rates,
            horizon_days=self.HORIZON_DAYS,
            use_taker=True,
            prefer_points_long=True,
            min_net_apr=-1e9,  # 先全取，后按积分/净EV筛
        )
        if not combos:
            return None
        # 优先选长腿有积分的组合；否则退回纯资金费套利的最优组合作兜底。
        points_combos = [c for c in combos if c.points_long_leg]
        chosen = (points_combos or combos)[0]
        return chosen.to_dict()

    def _value_points_usd(self, combo: Dict[str, Any], notional: float) -> float:
        """对组合长腿所在积分项目做诚实积分估值（USD，保守/基准档）。

        [2026-07-06 完善] 由名义×持有期×项目累积速率估算"我方积分数"，再按 FDV 折现估值。
        项目未在 program_registry 填齐 FDV/总积分/累积速率时 → 估值不可估 → 计 0，
        坚持"宁可低估、不臆造积分收益"。
        """
        program_id = combo.get("points_program_id")
        if not program_id:
            return 0.0
        try:
            from backend.services.rebate_arb.points_valuation import (
                value_points_for_program,
            )

            v = value_points_for_program(
                program_id,
                notional_usd=notional,
                horizon_days=self.HORIZON_DAYS,
            )
            if not v.estimable:
                return 0.0
            return (
                v.my_points_value_conservative
                if self.USE_CONSERVATIVE_POINTS
                else v.my_points_value_base
            )
        except Exception as exc:
            logger.debug("[SDN] 积分估值失败: %s", exc)
            return 0.0

    def _adaptive_horizon(self, combo: Dict[str, Any]) -> float:
        """按 combo 的保本天数自适应选择持有期（天）。

        规则（保守、只延长不缩短）：
        - 净 carry <= 0：延长无益（手续费永远摊不平）→ 用默认 HORIZON_DAYS。
        - 有 breakeven_days 且 > 默认窗口：延长到 breakeven × 安全系数，封顶 MAX_HORIZON_DAYS。
        - 其余情况：默认 HORIZON_DAYS。

        例：8% APR、fee_drag 使 breakeven≈8.56 天时，默认 7 天不可行，
        自适应到 min(8.56×1.5, 21)=12.84 天后净 EV 转正，从而变可行。
        """
        default = float(self.HORIZON_DAYS)
        try:
            net_carry = float(combo.get("net_funding_per_day", 0.0) or 0.0)
        except (TypeError, ValueError):
            net_carry = 0.0
        if net_carry <= 0:
            return default

        be = combo.get("breakeven_days")
        try:
            be = float(be) if be is not None else None
        except (TypeError, ValueError):
            be = None
        if be is None or be <= default:
            return default

        target = be * float(self.HORIZON_SAFETY_FACTOR)
        return max(default, min(target, float(self.MAX_HORIZON_DAYS)))

    def evaluate(self, incentive_data: Dict, account_equity: float) -> StrategyEvaluation:
        """评估 delta-neutral 刷积分组合的可行性与扣成本净 EV。"""
        incentive_data = incentive_data or {}
        combo = self._best_combo(incentive_data)

        if combo is None:
            # 精确区分"完全无数据" vs"有单场所数据但凑不齐双腿"，避免误导性告警。
            funding = self._extract_funding_rates(incentive_data)
            venues = [ex for ex, m in funding.items() if m] if funding else []
            if not venues:
                reason = "无资金费率数据（离线/无实时快照）→ 不臆造机会"
            else:
                reason = (
                    f"仅 {len(venues)} 个场所有资金费（{venues}）→ 无法凑齐 delta-neutral 双腿"
                    "（需同一 symbol 在 ≥2 场所都有费率）；补第二场所资金费历史即自动生效"
                )
            return StrategyEvaluation(
                strategy_type=RebateStrategyType.SDN_DELTA_NEUTRAL,
                is_viable=False,
                expected_monthly_value=0.0,
                risk_score=0.2,
                confidence=0.0,
                details={
                    "reason": reason,
                    "needs": "incentive_data['funding_rates'] = {exchange:{symbol:rate}}（≥2场所）",
                    "venues_with_funding": venues,
                    "delta_neutral": True,
                },
            )

        notional = max(account_equity, 0.0) * self.NOTIONAL_EQUITY_PCT * self.DEFAULT_LEVERAGE
        from backend.services.rebate_arb.points_valuation import net_ev

        # [2026-07-06 完善] 自适应持有期：正 carry 但保本期>默认窗口时，延长持有（有上限）
        # 以摊平一次性手续费，让"资金费价差为正但 7 天摊不平费用"的组合也能变可行。
        effective_horizon = self._adaptive_horizon(combo)

        points_usd = self._value_points_usd(combo, notional)
        ev = net_ev(
            notional_usd=notional,
            net_funding_per_day=float(combo.get("net_funding_per_day", 0.0)),
            fee_drag=float(combo.get("fee_drag", 0.0)),
            horizon_days=effective_horizon,
            points_value_usd=points_usd,
        )

        # 月度净值（把持有期净 EV 折算到 30 天口径）
        monthly_value = ev.net_ev_usd * (30.0 / max(effective_horizon, 1e-6))

        is_viable = (
            account_equity >= self.MIN_EQUITY
            and ev.net_ev_apr >= self.MIN_NET_APR
            and combo.get("long_exchange") != combo.get("short_exchange")
        )

        # 把最终采用的持有期写回 combo，供 build_execution_plan 的 hold_phase 使用。
        combo["effective_horizon_days"] = round(effective_horizon, 3)

        return StrategyEvaluation(
            strategy_type=RebateStrategyType.SDN_DELTA_NEUTRAL,
            is_viable=is_viable,
            expected_monthly_value=round(monthly_value, 2),
            required_volume_usd=notional * 2,  # 两腿
            risk_score=0.25,  # delta 中性，方向风险低；主要剩执行/脱钩风险
            confidence=0.55,
            details={
                "combo": combo,
                "notional_usd": round(notional, 2),
                "net_ev_usd_horizon": round(ev.net_ev_usd, 4),
                "net_ev_apr": round(ev.net_ev_apr, 4),
                "gross_funding_pnl_usd": round(ev.gross_funding_pnl_usd, 4),
                "points_value_usd": round(points_usd, 4),
                "fee_cost_usd": round(ev.fee_cost_usd, 4),
                "horizon_days": round(effective_horizon, 3),
                "default_horizon_days": self.HORIZON_DAYS,
                "horizon_adaptive": bool(effective_horizon > self.HORIZON_DAYS + 1e-9),
                "breakeven_days": combo.get("breakeven_days"),
                "delta_neutral": True,
                "source_exchange": combo.get("long_exchange"),
                "hedge_exchange": combo.get("short_exchange"),
                "min_equity": self.MIN_EQUITY,
                "valuation_conservative": self.USE_CONSERVATIVE_POINTS,
            },
        )

    def build_execution_plan(
        self,
        size_usd: float,
        symbol: str = "BTC/USDT:USDT",
        paper_mode: bool = True,
        combo: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """构建 delta-neutral 双腿执行计划：长腿(积分DEX) + 等额空腿(深场所)。

        combo 缺省时用保守占位（长 hyperliquid / 空 binance），实际应由 evaluate
        产出的最优组合注入。两腿名义严格相等以保证 delta 中性。
        """
        combo = combo or {}
        long_ex = combo.get("long_exchange", "hyperliquid")
        short_ex = combo.get("short_exchange", "binance")
        sym = combo.get("symbol", symbol)
        # 采用 evaluate 写回的自适应持有期；缺省回退默认窗口，并夹在 [默认, 上限] 之间。
        hold_days = combo.get("effective_horizon_days")
        try:
            hold_days = float(hold_days) if hold_days is not None else self.HORIZON_DAYS
        except (TypeError, ValueError):
            hold_days = self.HORIZON_DAYS
        hold_days = max(self.HORIZON_DAYS, min(hold_days, self.MAX_HORIZON_DAYS))

        return {
            "strategy": "SDN",
            "delta_neutral": True,
            # [2026-07-06 完善] 存资金费元数据，供 Paper 平仓时按持仓期累计资金费盈亏
            # （delta-neutral 两腿价格波动抵消，真实收益来自持有期资金费价差）。
            "funding_meta": {
                "net_funding_per_day": float(combo.get("net_funding_per_day", 0.0)),
                "long_funding_per_day": float(combo.get("long_funding_per_day", 0.0)),
                "short_funding_per_day": float(combo.get("short_funding_per_day", 0.0)),
                "long_exchange": long_ex,
                "short_exchange": short_ex,
                "symbol": sym,
            },
            "side_a": {
                "exchange": long_ex,
                "symbol": sym,
                "side": "buy",
                "type": "limit",        # 长腿走 Maker（积分/费率更优）
                "size_usd": size_usd,
                "role": "points_long_leg",
            },
            "side_b": {
                "exchange": short_ex,
                "symbol": sym,
                "side": "sell",
                "type": "limit",        # 空腿对冲，等额名义
                "size_usd": size_usd,
                "role": "hedge_short_leg",
            },
            # 中性组合默认按资金费收敛/反转平仓，非固定短持有
            "hold_phase": {
                "total_seconds": int(hold_days * 86400),
                "horizon_days": round(hold_days, 3),
                "reason": "sdn_delta_neutral_funding_capture",
            },
            "close_plan": {
                "side_a": {"exchange": long_ex, "symbol": sym, "side": "sell", "type": "limit", "size_usd": size_usd},
                "side_b": {"exchange": short_ex, "symbol": sym, "side": "buy", "type": "limit", "size_usd": size_usd},
            },
            "paper_mode": paper_mode,
        }
