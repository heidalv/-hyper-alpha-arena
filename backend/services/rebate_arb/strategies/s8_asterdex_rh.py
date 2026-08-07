"""
S8: Asterdex Stage 6 Rh积分最大化策略

Stage 6 Convergence 官方规则:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
总积分 = (交易积分 + 持仓积分 + Aster资产积分 + 清算积分 + 盈亏积分)
         × 团队加成(1.05-1.2x) + 推荐积分

1. 交易积分: 手续费贡献 + Maker 流动性贡献(0费率也计分) × 币种加成
2. 持仓积分: 规模 × 时长，已取消上限，T+1 结算
3. 资产积分: USDF/ASTER/asBNB 保证金（需全仓模式），已取消上限
4. 盈亏积分: 每小时净盈亏（不含资金费）计入，双向都算
5. 费率: USDT 永续 0% maker / 0.04% taker；ASTER 付费再省 5%
6. 官方明确惩罚对冲刷分 → 只做单边方向仓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
旧「80x 乘数模型」(Taker 2x × 持仓 2x × USDF 20x) 已废弃，
相关常量仅作兼容保留，EV 计算以 rule_registry.STAGE6_POINT_MODEL 为单一来源。

AI信号增强:
- AI决定开仓方向(bullish→buy, bearish→sell)；neutral 默认 skip
- AI从候选池选最强信号币种
- AI置信度控制仓位大小, danger级别跳过本轮
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from ..models import RebateStrategyType, StrategyEvaluation

logger = logging.getLogger(__name__)


class S8AsterdexRhStrategy:
    """S8: Asterdex Stage 6 积分套利合约策略（积分类别模型）"""

    # ═══════════════════════════════════════════
    # 费率参数 (Stage 6 官方: USDT 永续 0% maker / 0.04% taker)
    # 单一来源 = rule_registry.STAGE6_POINT_MODEL.fee_schedule
    # ═══════════════════════════════════════════
    TAKER_FEE = 0.0004          # 0.04% Taker fee（旧值 0.005% 差 8 倍，已校正）
    MAKER_FEE = 0.0             # 0% Maker fee（Stage 6 挂单零费率且赚积分）
    REBATE_RATE = 0.0           # 旧赛季 10% 返佣假设废弃
    NET_TAKER_FEE = 0.0004      # 派生值，_recompute_derived 维护

    # ═══════════════════════════════════════════
    # USDF 参数
    # ═══════════════════════════════════════════
    USDF_APY_LOW = 0.07         # USDF 年化下限 7%
    USDF_APY_HIGH = 0.10        # USDF 年化上限 10%
    USDF_AU_MULTIPLIER = 20     # [废弃] 旧 20x Au 乘数，仅兼容保留

    # ═══════════════════════════════════════════
    # 持仓参数（持仓积分无上限，时长动态化见 stage6_optimal）
    # ═══════════════════════════════════════════
    TAKER_RH_WEIGHT = 2.0       # [废弃] 旧 Taker 2x 权重，仅兼容保留
    HOLD_TIME_BOOST = 2.0       # [废弃] 旧持仓 2x 加成，仅兼容保留
    MIN_HOLD_SECONDS = 3600     # 最低持仓60分钟
    HOLD_BUFFER_SECONDS = 300   # 5分钟buffer确保达标

    # Stage 6 积分类别模型（rule_registry 注入；None 时惰性加载）
    STAGE6_MODEL: Optional[Dict[str, Any]] = None

    # 月交易量阶梯 (额外tier乘数)
    VOLUME_TIER_MULTIPLIERS = [
        (0, 1.0),               # <$1M: 1x
        (1_000_000, 1.5),       # $1M-$5M: 1.5x
        (5_000_000, 3.0),       # $5M-$10M: 3x
        (10_000_000, 5.0),      # >$10M: 5x
    ]

    # ═══════════════════════════════════════════
    # ASTER 空投参数 (Stage 6)
    # ═══════════════════════════════════════════
    STAGE_6_ALLOCATION = 64_000_000     # 64M ASTER (总供应量4%)
    STAGE_EPOCHS = 12                    # 约12周, 每周一个epoch
    WEEKLY_ALLOCATION = 64_000_000 / 12  # ~5.33M ASTER/epoch
    ASTER_PRICE = 0.70                   # 当前市价 $0.70

    # ═══════════════════════════════════════════
    # 策略运行参数
    # ═══════════════════════════════════════════
    MIN_EQUITY = 100            # 最低资金100U (杠杆放大)
    DEFAULT_LEVERAGE = 10       # 默认杠杆
    MAX_LEVERAGE = 20           # 最大杠杆
    ROUNDS_PER_DAY = 3          # 每日3轮 (65min×3=195min, 留余量)
    ROUND_DURATION_SECONDS = MIN_HOLD_SECONDS + HOLD_BUFFER_SECONDS  # 65min/轮

    # 积分最大化：方向由 AI 定，规模尽量用满子池保证金
    POINTS_MAXIMIZATION_MODE = True
    POINTS_MIN_POSITION_SCALE = 0.90   # 积分模式下最低仓位比例（相对满配保证金）
    # AI 方向为 neutral 时的动作：
    #   skip（默认，不开仓）
    #   half（半仓做多，需显式配置）
    #   follow_macro（跟随宏观趋势方向开仓：积分策略收益来自积分而非方向盈亏，
    #                 中性信号轮跳过会大幅减少积分轮次；方向由宏观过滤器背书，
    #                 仓位缩至 NEUTRAL_MACRO_POSITION_SCALE，宏观也无方向时仍跳过）
    NEUTRAL_DIRECTION_ACTION = "skip"
    NEUTRAL_MACRO_POSITION_SCALE = 0.60  # follow_macro 模式下的仓位比例
    RH_OPTIMIZATION_MODE = "stage6_optimal"  # stage6_optimal | safe | quick | paper_experiment
    SAFE_MAX_LEVERAGE = 12

    # ═══════════════════════════════════════════
    # stage6_optimal 模式参数（Stage 6 最优打法）
    # Maker 优先 0 费率 + 动态持仓 + USDF 全仓
    # ═══════════════════════════════════════════
    STAGE6_MAKER_FIRST = True               # 开/平仓限价挂单优先
    STAGE6_TAKER_FALLBACK_SECONDS = 90      # 挂单超时回退 Taker
    STAGE6_EXPECTED_MAKER_RATIO = 0.7       # EV 估算用的预期 Maker 成交占比
    STAGE6_HOLD_MIN_SECONDS = 2 * 3600      # 持仓下限 2h（资金费为成本时）
    STAGE6_HOLD_MAX_SECONDS = 8 * 3600      # 持仓上限 8h（资金费为收益时）
    STAGE6_HOLD_DEFAULT_SECONDS = 4 * 3600  # 默认 4h（持仓积分无上限）
    STAGE6_FUNDING_THRESHOLD = 0.0001       # 资金费率阈值（8h 0.01%）
    STAGE6_REQUIRE_CROSS_MARGIN = True      # 资产积分要求全仓模式
    QUICK_MAX_LEVERAGE = 15
    QUICK_MIN_CONFIDENCE = 70
    QUICK_MIN_SYMBOL_BOOST = 1.5
    QUICK_MARGIN_SCALE = 0.75
    PAPER_EXPERIMENT_MARGIN_SCALE = 0.30
    PAPER_EXPERIMENT_HOLD_SECONDS = 900
    PAPER_EXPERIMENT_HOLDS = [300, 900, 1800, 3900]
    DEFAULT_SLIPPAGE_BPS = 2.0
    MIN_RH_PER_FEE = 10.0

    # Stage 3 Symbol Boost（可 YAML 覆盖；选币时优先高分币种）
    SYMBOL_BOOST_MAP: Dict[str, float] = {
        "ASTER/USDT": 2.0,
        "BTC/USDT": 1.5,
        "ETH/USDT": 1.2,
        "SOL/USDT": 1.2,
        "BNB/USDT": 1.1,
    }

    def __init__(self, config: Dict = None):
        """初始化, 支持config覆盖."""
        try:
            from backend.services.rebate_arb.rule_registry import rule_registry
            self.update_params(rule_registry.get_strategy_rule_params("S8"))
        except Exception as e:
            logger.debug("[S8] rule registry params fallback: %s", e)

        self.strategy_llm_config_id: Optional[int] = None
        self.execution_llm_config_id: Optional[int] = None
        self.account_id: Optional[int] = None

        if config:
            self.TAKER_FEE = config.get("asterdex_taker", self.TAKER_FEE)
            self.REBATE_RATE = config.get("asterdex_rebate", self.REBATE_RATE)
            self.ASTER_PRICE = config.get("aster_price", self.ASTER_PRICE)
            self.STAGE_6_ALLOCATION = config.get("stage_allocation", self.STAGE_6_ALLOCATION)
            self.DEFAULT_LEVERAGE = config.get("default_leverage", self.DEFAULT_LEVERAGE)
            self.ROUNDS_PER_DAY = config.get("rounds_per_day", self.ROUNDS_PER_DAY)
            self.USDF_AU_MULTIPLIER = config.get("usdf_au_multiplier", self.USDF_AU_MULTIPLIER)
            self.POINTS_MAXIMIZATION_MODE = bool(
                config.get("points_maximization_mode", self.POINTS_MAXIMIZATION_MODE)
            )
            self.RH_OPTIMIZATION_MODE = str(
                config.get("rh_optimization_mode", self.RH_OPTIMIZATION_MODE)
            ).lower()
            self.NEUTRAL_DIRECTION_ACTION = str(
                config.get("neutral_direction_action", self.NEUTRAL_DIRECTION_ACTION)
            ).lower()
            # YAML 可覆盖 Stage 6 模型参数（深合并到 rule_registry 默认值）
            s6_override = config.get("stage6_model")
            if isinstance(s6_override, dict) and s6_override:
                base = dict(self.stage6_model())
                for k, v in s6_override.items():
                    if isinstance(v, dict) and isinstance(base.get(k), dict):
                        base[k] = {**base[k], **v}
                    else:
                        base[k] = v
                self.STAGE6_MODEL = base
            for key in (
                "safe_max_leverage", "quick_max_leverage", "quick_min_confidence",
                "quick_min_symbol_boost", "quick_margin_scale",
                "paper_experiment_margin_scale", "paper_experiment_hold_seconds",
                "default_slippage_bps", "min_rh_per_fee",
                "neutral_macro_position_scale",
                "stage6_maker_first", "stage6_taker_fallback_seconds",
                "stage6_expected_maker_ratio", "stage6_hold_min_seconds",
                "stage6_hold_max_seconds", "stage6_hold_default_seconds",
                "stage6_funding_threshold", "stage6_require_cross_margin",
            ):
                if key in config:
                    setattr(self, key.upper(), config[key])
            boost = config.get("symbol_boost_map")
            if isinstance(boost, dict) and boost:
                self.SYMBOL_BOOST_MAP = {str(k): float(v) for k, v in boost.items()}
            self.strategy_llm_config_id = config.get("strategy_llm_config_id")
            self.execution_llm_config_id = config.get("execution_llm_config_id")
            self.account_id = config.get("account_id")
            self._recompute_derived()

        # M8: 参数学习回流 — 用历史轮次实际数据校准估值折扣/持仓时长/兜底仓位
        # （样本不足时不覆盖；学习值在 s8_param_learner 内做硬边界 clamp）
        try:
            from backend.services.rebate_arb.s8_param_learner import apply_learned_params
            apply_learned_params(self)
        except Exception as _learn_err:
            logger.debug("[S8] 学习参数应用跳过: %s", _learn_err)

    def update_params(self, params: Dict[str, Any]) -> None:
        """运行时更新策略参数"""
        for key, value in params.items():
            upper_key = key.upper()
            if hasattr(self, upper_key):
                setattr(self, upper_key, value)
            elif hasattr(self, key):
                setattr(self, key, value)
        self._recompute_derived()

    def _recompute_derived(self) -> None:
        """重算派生参数"""
        self.NET_TAKER_FEE = self.TAKER_FEE * (1 - self.REBATE_RATE)
        self.ROUND_DURATION_SECONDS = self.MIN_HOLD_SECONDS + self.HOLD_BUFFER_SECONDS

    def _is_paper_trading(self) -> bool:
        try:
            from backend.services.rebate_arb.capital_coordinator import capital_coordinator
            return bool(capital_coordinator.is_paper_mode())
        except Exception:
            return False

    def _effective_paper(self, paper_mode: Optional[bool] = None) -> bool:
        """本次操作是否按 Paper 处理。

        显式传入的 paper_mode（来自 build_execution_plan 等调用）优先；
        未传时回退全局 capital_coordinator。修复"显式 paper 计划被全局 live
        学习门禁误降级为 paper_experiment"的不一致。
        """
        if paper_mode is not None:
            return bool(paper_mode)
        return self._is_paper_trading()

    def _learning_gate(self, *, paper_mode: Optional[bool] = None) -> Dict[str, Any]:
        try:
            from backend.services.rebate_arb.s8_param_learner import get_learning_gate

            return get_learning_gate(paper_mode=self._effective_paper(paper_mode))
        except Exception:
            return {}

    def _normalize_mode(
        self, mode: Optional[str] = None, *, paper_mode: Optional[bool] = None
    ) -> str:
        raw = str(mode or self.RH_OPTIMIZATION_MODE or "stage6_optimal").lower()
        valid = {"stage6_optimal", "safe", "quick", "paper_experiment"}
        picked = raw if raw in valid else "stage6_optimal"
        # 仅实盘：学习结果为负 EV 时降级；Paper 保持 stage6_optimal 继续试错
        if self._effective_paper(paper_mode):
            return picked
        try:
            from backend.services.rebate_arb.s8_param_learner import get_learning_gate

            gate = get_learning_gate(paper_mode=False)
            if gate.get("recovery_mode") and picked == "stage6_optimal":
                logger.info(
                    "[S8] Live 学习门禁: cash/pt=%.4f samples=%s → 强制 paper_experiment",
                    float(gate.get("cash_per_point") or 0),
                    gate.get("samples"),
                )
                return "paper_experiment"
        except Exception:
            pass
        return picked

    def _pessimistic_round_net(
        self, round_metrics: Dict[str, Any], *, discount: Optional[float] = None
    ) -> float:
        """单轮悲观净额 = 积分×学习折扣 − 手续费/滑点/资金费。"""
        gate = self._learning_gate()
        model = self.stage6_model()
        valuation = model.get("point_valuation") or {}
        usd_per_point = float(valuation.get("usd_per_point_estimate") or 0.01)
        learned_disc = discount
        if learned_disc is None:
            learned_disc = float(gate.get("speculative_discount") or 0.5)
        points_val = float(round_metrics.get("estimated_rh") or 0) * usd_per_point * float(learned_disc)
        cost = float(round_metrics.get("estimated_cost_usd") or 0)
        return points_val - cost

    def check_paper_open_gate(
        self,
        *,
        account_equity: float,
        paper_account_id: Optional[int] = None,
        symbol: str = "BTC/USDT",
    ) -> tuple:
        """
        Paper 开仓检查。模拟盘**不因学习结果拒开**——只校验权益并返回 advisory 告警供面板展示。
        """
        leverage = min(self.SAFE_MAX_LEVERAGE, max(5, self.DEFAULT_LEVERAGE))
        resolved = self.resolve_target_margin(
            account_equity=account_equity,
            paper_account_id=paper_account_id,
            exchange="asterdex",
            leverage=leverage,
        )
        eval_margin = float(resolved.get("margin_usd") or max(account_equity * 0.30, 30.0))
        mode = self._normalize_mode()
        expected_maker_ratio = (
            float(self.STAGE6_EXPECTED_MAKER_RATIO)
            if mode == "stage6_optimal" and self.STAGE6_MAKER_FIRST
            else 0.0
        )
        round_metrics = self.estimate_round_metrics(
            margin_usd=eval_margin,
            leverage=leverage,
            symbol=symbol,
            maker_ratio=expected_maker_ratio,
        )
        pessimistic_net = self._pessimistic_round_net(round_metrics)
        rh_per_fee = float(round_metrics.get("rh_per_fee_usd") or 0)
        gate = self._learning_gate()
        advisory: List[str] = []
        if pessimistic_net <= 0:
            advisory.append(
                f"悲观单轮净额 ${pessimistic_net:.4f}≤0（仅告警，Paper 仍开仓收样本）"
            )
        if rh_per_fee < self.MIN_RH_PER_FEE:
            advisory.append(f"rh_per_fee {rh_per_fee:.1f}<{self.MIN_RH_PER_FEE}（仅告警）")
        if gate.get("paper_advisory"):
            advisory.append(
                f"cash/pt={float(gate.get('cash_per_point') or 0):.4f} 为负（学习告警，不拦开仓）"
            )
        details = {
            "eval_margin_usd": eval_margin,
            "pessimistic_net_usd": round(pessimistic_net, 4),
            "rh_per_fee_usd": rh_per_fee,
            "learning_gate": gate,
            "effective_mode": mode,
            "advisory_warnings": advisory,
            "paper_blocks_open": False,
        }
        if account_equity < self.MIN_EQUITY:
            return False, f"权益不足 {account_equity:.0f} < {self.MIN_EQUITY}", details
        reason = "paper_open_gate_advisory" if advisory else "paper_open_gate_ok"
        return True, reason, details

    def stage6_model(self) -> Dict[str, Any]:
        """Stage 6 积分模型（单一来源 rule_registry，可被 YAML 覆盖）。"""
        if isinstance(self.STAGE6_MODEL, dict) and self.STAGE6_MODEL:
            return self.STAGE6_MODEL
        try:
            from backend.services.rebate_arb.rule_registry import STAGE6_POINT_MODEL

            self.STAGE6_MODEL = dict(STAGE6_POINT_MODEL)
        except Exception:
            self.STAGE6_MODEL = {}
        return self.STAGE6_MODEL

    def stage6_fee_rates(self, market: str = "usdt_perp", pay_with_aster: bool = False) -> Dict[str, float]:
        """Stage 6 费率（maker/taker），可选 ASTER 抵扣。"""
        model = self.stage6_model()
        sched = (model.get("fee_schedule") or {})
        rates = dict(sched.get(market) or {"maker": self.MAKER_FEE, "taker": self.TAKER_FEE})
        if pay_with_aster:
            discount = float(sched.get("aster_fee_discount") or 0)
            rates = {k: v * (1 - discount) for k, v in rates.items()}
        return rates

    def _hold_multiplier(self, hold_seconds: float) -> float:
        """[废弃] 旧 2x 时间加成模型，仅兼容旧展示字段。"""
        if hold_seconds >= self.MIN_HOLD_SECONDS:
            return self.HOLD_TIME_BOOST
        progress = max(0.0, min(float(hold_seconds or 0) / max(self.MIN_HOLD_SECONDS, 1), 1.0))
        return 1.0 + progress * (self.HOLD_TIME_BOOST - 1.0)

    def estimate_round_metrics(
        self,
        *,
        margin_usd: float,
        leverage: float,
        symbol: str,
        hold_seconds: Optional[float] = None,
        confidence: float = 50.0,
        risk_level: str = "normal",
        slippage_bps: Optional[float] = None,
        funding_rate: float = 0.0,
        maker_ratio: Optional[float] = None,
        pay_fee_with_aster: bool = False,
    ) -> Dict[str, Any]:
        """
        S8 单轮 Stage 6 净 EV 模型。用于计划、QAA 反馈和前端展示。

        积分 = (交易积分 + 持仓积分 + 资产积分) × 团队加成
        净EV = 积分估值（投机性折扣后） − Taker 费 − 滑点 − 资金费成本
        盈亏积分 ex-ante 期望为 0，不计入预估（平仓后由实际数据回填）。

        Args:
            maker_ratio: 预计 Maker 成交占比 0-1（stage6_optimal 模式传高值；
                         None 时按当前模式默认：纯 Taker = 0）
        """
        model = self.stage6_model()
        margin = max(float(margin_usd or 0), 0.0)
        lev = max(float(leverage or self.DEFAULT_LEVERAGE), 1.0)
        hold_sec = float(hold_seconds if hold_seconds is not None else self.ROUND_DURATION_SECONDS)
        hold_hours = max(hold_sec / 3600.0, 1 / 60)
        notional = margin * lev
        round_volume = notional * 2
        sym_boost = self.symbol_boost(symbol)
        mk_ratio = max(0.0, min(float(maker_ratio if maker_ratio is not None else 0.0), 1.0))

        # ── 费用（Stage 6 真实费率，Maker 0%）──
        fees = self.stage6_fee_rates(pay_with_aster=pay_fee_with_aster)
        taker_volume = round_volume * (1.0 - mk_ratio)
        maker_volume = round_volume * mk_ratio
        gross_fee = taker_volume * float(fees.get("taker") or 0) + maker_volume * float(fees.get("maker") or 0)
        rebate = gross_fee * self.REBATE_RATE
        net_fee = gross_fee - rebate
        slip_bps = float(slippage_bps if slippage_bps is not None else self.DEFAULT_SLIPPAGE_BPS)
        # 滑点只发生在 Taker 腿；Maker 挂单按无滑点计
        slippage_cost = taker_volume * max(slip_bps, 0.0) / 10000.0
        funding_cost = abs(notional * float(funding_rate or 0.0) * hold_hours / 8.0)
        estimated_cost = net_fee + slippage_cost + funding_cost

        # ── Stage 6 积分类别 ──
        trading_cfg = model.get("trading") or {}
        position_cfg = model.get("position") or {}
        asset_cfg = model.get("aster_asset") or {}
        team_cfg = model.get("team_boost") or {}
        trading_points = (
            gross_fee * float(trading_cfg.get("points_per_usd_fee") or 0)
            + (maker_volume / 1000.0) * float(trading_cfg.get("maker_points_per_1k_usd") or 0)
        ) * sym_boost
        position_points = (notional / 1000.0) * float(
            position_cfg.get("points_per_1k_usd_hour") or 0
        ) * hold_hours
        asset_points = (margin / 1000.0) * float(
            asset_cfg.get("points_per_1k_usd_hour") or 0
        ) * hold_hours
        team_boost = float(team_cfg.get("default") or 1.0)
        estimated_rh = (trading_points + position_points + asset_points) * team_boost

        # ── 积分估值（投机性，需折扣） ──
        valuation = model.get("point_valuation") or {}
        usd_per_point = float(valuation.get("usd_per_point_estimate") or 0)
        spec_discount = float(valuation.get("speculative_discount") or 1.0)
        points_value_usd = estimated_rh * usd_per_point * spec_discount
        net_ev_usd = points_value_usd - estimated_cost

        rh_per_fee = estimated_rh / max(estimated_cost, 0.0001)
        rh_per_margin_hour = estimated_rh / max(margin * hold_hours, 0.0001)
        confidence_score = max(0.0, min(float(confidence or 0), 100.0)) / 100.0
        boost_score = min(sym_boost / max(self.QUICK_MIN_SYMBOL_BOOST, 1.0), 1.5) / 1.5
        cost_score = min(rh_per_fee / max(self.MIN_RH_PER_FEE, 0.1), 1.2) / 1.2
        risk_penalty = 0.25 if str(risk_level).lower() == "warning" else 0.0
        leverage_penalty = max(0.0, (lev - self.SAFE_MAX_LEVERAGE) / max(self.MAX_LEVERAGE, 1)) * 0.35
        short_hold_penalty = 0.15 if hold_sec < self.MIN_HOLD_SECONDS else 0.0
        round_quality_score = max(
            0.0,
            min(100.0, (confidence_score * 0.45 + boost_score * 0.25 + cost_score * 0.30) * 100),
        )
        safety_score = max(0.0, min(100.0, (1.0 - risk_penalty - leverage_penalty - short_hold_penalty) * 100))

        # 兼容旧展示字段（combined_multiplier 不再驱动 EV，仅显示参考）
        hold_mult = self._hold_multiplier(hold_sec)
        legacy_combined = self.TAKER_RH_WEIGHT * hold_mult * self.USDF_AU_MULTIPLIER * sym_boost

        return {
            "margin_usd": round(margin, 2),
            "leverage": round(lev, 2),
            "notional_usd": round(notional, 2),
            "round_volume_usd": round(round_volume, 2),
            "hold_seconds": int(round(hold_sec)),
            "hold_hours": round(hold_hours, 3),
            "symbol_boost": sym_boost,
            "hold_multiplier": round(hold_mult, 3),
            "combined_multiplier": round(legacy_combined, 2),
            "estimated_rh": round(estimated_rh, 2),
            "gross_fee_usd": round(gross_fee, 6),
            "rebate_usd": round(rebate, 6),
            "net_fee_usd": round(net_fee, 6),
            "slippage_cost_usd": round(slippage_cost, 6),
            "funding_cost_usd": round(funding_cost, 6),
            "estimated_cost_usd": round(estimated_cost, 6),
            "rh_per_fee_usd": round(rh_per_fee, 2),
            "rh_per_margin_hour": round(rh_per_margin_hour, 4),
            "round_quality_score": round(round_quality_score, 1),
            "safety_score": round(safety_score, 1),
            # ── Stage 6 明细 ──
            "stage6": {
                "trading_points": round(trading_points, 2),
                "position_points": round(position_points, 2),
                "asset_points": round(asset_points, 2),
                "pnl_points": 0.0,
                "team_boost": team_boost,
                "maker_ratio": round(mk_ratio, 2),
                "maker_volume_usd": round(maker_volume, 2),
                "taker_volume_usd": round(taker_volume, 2),
                "points_value_usd": round(points_value_usd, 4),
                "net_ev_usd": round(net_ev_usd, 4),
                "valuation_speculative": True,
            },
            "points_value_usd": round(points_value_usd, 4),
            "net_ev_usd": round(net_ev_usd, 4),
            "formula_version": "s8_stage6_ev_v1",
        }

    def build_paper_ab_test_matrix(
        self,
        *,
        margin_usd: float,
        leverage: Optional[float] = None,
        symbol: str = "BTC/USDT",
    ) -> List[Dict[str, Any]]:
        """Paper 实验矩阵：比较 5/15/30/65 分钟单位效率。"""
        lev = float(leverage or self.DEFAULT_LEVERAGE)
        holds = getattr(self, "PAPER_EXPERIMENT_HOLDS", [300, 900, 1800, 3900])
        return [
            self.estimate_round_metrics(
                margin_usd=margin_usd,
                leverage=lev,
                symbol=symbol,
                hold_seconds=float(h),
            )
            for h in holds
        ]

    def _stage6_dynamic_hold_seconds(
        self, *, direction: str, funding_rate: float
    ) -> Tuple[int, str]:
        """
        Stage 6 动态持仓时长：持仓积分无上限（T+1），时长由资金费率方向决定。

        - 资金费对持仓方向是成本 → 缩到下限
        - 资金费对持仓方向是收益 → 拉到上限
        - 不显著 → 默认时长
        """
        fr = float(funding_rate or 0.0)
        # 多头支付正资金费，空头收取正资金费
        cost_rate = fr if (direction or "").lower() != "bearish" else -fr
        threshold = float(self.STAGE6_FUNDING_THRESHOLD or 0)
        if cost_rate > threshold:
            return int(self.STAGE6_HOLD_MIN_SECONDS), "funding_cost_shorten"
        if cost_rate < -threshold:
            return int(self.STAGE6_HOLD_MAX_SECONDS), "funding_income_extend"
        return int(self.STAGE6_HOLD_DEFAULT_SECONDS), "funding_neutral_default"

    def _build_optimizer_decision(
        self,
        *,
        paper_mode: bool,
        margin_usd: float,
        symbol: str,
        ai_signal: Optional[Dict[str, Any]],
        requested_leverage: float,
        funding_rate: float = 0.0,
    ) -> Dict[str, Any]:
        """把模式、AI 信号和收益模型合成一份可执行参数。"""
        ai_signal = ai_signal or {}
        mode = self._normalize_mode(paper_mode=paper_mode)
        gate = self._learning_gate(paper_mode=paper_mode)
        reasons: List[str] = []
        if gate.get("recovery_mode"):
            reasons.append("learning_recovery_mode")
        risk_level = str(ai_signal.get("risk_level") or "normal").lower()
        confidence = float(ai_signal.get("confidence") or 50)
        sym_boost = self.symbol_boost(symbol)
        if mode == "paper_experiment" and not paper_mode:
            mode = "safe"
            reasons.append("paper_experiment_live_downgrade")
        if mode == "quick":
            if risk_level == "warning":
                mode = "safe"
                reasons.append("warning_risk_downgrade")
            if confidence < float(self.QUICK_MIN_CONFIDENCE):
                mode = "safe"
                reasons.append("confidence_below_quick_threshold")
            if sym_boost < float(self.QUICK_MIN_SYMBOL_BOOST):
                mode = "safe"
                reasons.append("symbol_boost_below_quick_threshold")

        maker_ratio = 0.0
        if mode == "stage6_optimal":
            # Maker 优先 + 动态持仓 + 安全杠杆（避免清算，清算积分对我们是纯损失）
            hold_seconds, hold_reason = self._stage6_dynamic_hold_seconds(
                direction=str(ai_signal.get("direction") or "neutral"),
                funding_rate=funding_rate,
            )
            reasons.append(hold_reason)
            max_lev = float(self.SAFE_MAX_LEVERAGE)
            margin_scale = 1.0
            if self.STAGE6_MAKER_FIRST:
                maker_ratio = float(self.STAGE6_EXPECTED_MAKER_RATIO)
        elif mode == "paper_experiment":
            hold_seconds = float(self.PAPER_EXPERIMENT_HOLD_SECONDS)
            max_lev = float(self.QUICK_MAX_LEVERAGE)
            margin_scale = float(self.PAPER_EXPERIMENT_MARGIN_SCALE)
            if gate.get("recovery_mode"):
                max_lev = min(max_lev, float(gate.get("recovery_max_leverage") or 3))
                margin_scale = min(margin_scale, 0.30)
        elif mode == "quick":
            hold_seconds = float(self.ROUND_DURATION_SECONDS)
            max_lev = float(self.QUICK_MAX_LEVERAGE)
            margin_scale = float(self.QUICK_MARGIN_SCALE)
        else:
            hold_seconds = float(self.ROUND_DURATION_SECONDS)
            max_lev = float(self.SAFE_MAX_LEVERAGE)
            margin_scale = 1.0

        leverage = max(1, min(float(requested_leverage or self.DEFAULT_LEVERAGE), max_lev, float(self.MAX_LEVERAGE)))
        adjusted_margin = max(float(margin_usd or 0) * margin_scale, 0.0)
        metrics = self.estimate_round_metrics(
            margin_usd=adjusted_margin,
            leverage=leverage,
            symbol=symbol,
            hold_seconds=hold_seconds,
            confidence=confidence,
            risk_level=risk_level,
            funding_rate=funding_rate,
            maker_ratio=maker_ratio,
        )
        return {
            "mode": mode,
            "requested_mode": self.RH_OPTIMIZATION_MODE,
            "hold_seconds": int(hold_seconds),
            "leverage": int(round(leverage)),
            "margin_scale": round(margin_scale, 3),
            "maker_ratio": round(maker_ratio, 2),
            "reasons": reasons,
            "metrics": metrics,
            "paper_ab_test_matrix": self.build_paper_ab_test_matrix(
                margin_usd=adjusted_margin,
                leverage=leverage,
                symbol=symbol,
            ) if paper_mode else [],
        }

    def symbol_boost(self, symbol_pair: str) -> float:
        """
        Symbol Boost 乘数（1.0 = 无加成）。

        优先读运行时动态 map（规则同步任务定期刷新，官方 boost 每期会变），
        过期或为空时回退实例静态 map。
        """
        key = (symbol_pair or "").upper()
        boost_map = self.SYMBOL_BOOST_MAP
        try:
            from backend.services.rebate_arb.symbol_boost_store import (
                get_runtime_symbol_boost_map,
            )

            runtime_map = get_runtime_symbol_boost_map()
            if runtime_map:
                boost_map = runtime_map
        except Exception:
            pass

        if key in boost_map:
            return float(boost_map[key])
        base = key.split("/")[0]
        for sym, mult in boost_map.items():
            if sym.split("/")[0].upper() == base:
                return float(mult)
        return 1.0

    @classmethod
    def resolve_target_margin(
        cls,
        *,
        account_equity: float = 0.0,
        paper_account_id: Optional[int] = None,
        exchange: str = "asterdex",
        leverage: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        解析 S8 本轮目标保证金（非名义）。

        Paper：读分所配额 × 策略子限额 − 已占用保证金。
        无 Paper 账户时：总权益 × 30% 子池粗估。
        """
        lev = int(leverage or cls.DEFAULT_LEVERAGE)
        margin = max(float(account_equity or 0) * 0.30, 30.0)
        details: Dict[str, Any] = {
            "exchange": exchange,
            "leverage": lev,
            "source": "equity_estimate",
        }

        if paper_account_id:
            try:
                from backend.database.connection import SessionLocal
                from backend.services.rebate_arb.arbitrage_paper_account_service import (
                    arbitrage_paper_account_service,
                )

                db = SessionLocal()
                try:
                    cap = arbitrage_paper_account_service.compute_max_open_size(
                        db,
                        int(paper_account_id),
                        exchange,
                        "S8",
                        999_999.0,
                    )
                    allowed = float(cap.get("allowed_usd") or 0)
                    if allowed > 0:
                        margin = allowed
                        details.update({
                            "source": "paper_quota",
                            "strategy_cap_usd": cap.get("strategy_cap_usd"),
                            "exposure_usd": cap.get("exposure_usd"),
                            "exchange_cap_usd": cap.get("exchange_cap_usd"),
                        })
                finally:
                    db.close()
            except Exception as exc:
                logger.debug("[S8] resolve_target_margin paper skip: %s", exc)

        margin = round(max(margin, 0.0), 2)
        return {
            "margin_usd": margin,
            "notional_usd": round(margin * lev, 2),
            "leverage": lev,
            **details,
        }

    def evaluate(self, incentive_data: Dict, account_equity: float) -> StrategyEvaluation:
        """
        评估S8策略可行性（Stage 6 净 EV 模型）

        月收益 = USDF APY收益 + 积分估值（投机性折扣后） − 手续费/滑点/资金费成本
        积分按 Stage 6 类别模型（trading/position/asset × team_boost）估算。
        """
        asterdex_data = incentive_data.get("asterdex", {})
        aster_price = asterdex_data.get("aster_price", self.ASTER_PRICE)
        usdf_minted = asterdex_data.get("usdf_balance", 0.0)
        current_rh = asterdex_data.get("rh_points", 0.0)

        # ─── 杠杆和交易量计算 ───
        leverage = min(self.SAFE_MAX_LEVERAGE, max(5, self.DEFAULT_LEVERAGE))
        paper_id: Optional[int] = None
        try:
            from backend.services.rebate_arb.capital_coordinator import capital_coordinator

            paper_id = capital_coordinator.get_arbitrage_paper_account_id()
        except Exception:
            paper_id = None
        resolved = self.resolve_target_margin(
            account_equity=account_equity,
            paper_account_id=paper_id,
            exchange="asterdex",
            leverage=leverage,
        )
        eval_margin = float(resolved.get("margin_usd") or max(account_equity * 0.30, 30.0))
        eval_notional = float(resolved.get("notional_usd") or eval_margin * leverage)
        volume_per_round = eval_notional * 2
        daily_volume = volume_per_round * self.ROUNDS_PER_DAY
        monthly_volume = daily_volume * 30
        monthly_rounds = self.ROUNDS_PER_DAY * 30

        # ─── 单轮 Stage 6 指标 → 月度外推（与执行层同一保证金口径）───
        mode = self._normalize_mode()
        expected_maker_ratio = (
            float(self.STAGE6_EXPECTED_MAKER_RATIO)
            if mode == "stage6_optimal" and self.STAGE6_MAKER_FIRST
            else 0.0
        )
        round_metrics = self.estimate_round_metrics(
            margin_usd=eval_margin,
            leverage=leverage,
            symbol="BTC/USDT",
            maker_ratio=expected_maker_ratio,
        )
        stage6 = round_metrics.get("stage6") or {}
        monthly_rh_earned = round_metrics["estimated_rh"] * monthly_rounds
        monthly_points_value = float(round_metrics.get("points_value_usd") or 0) * monthly_rounds
        monthly_cost = float(round_metrics.get("estimated_cost_usd") or 0) * monthly_rounds
        net_fee_cost = float(round_metrics.get("net_fee_usd") or 0) * monthly_rounds

        # ─── USDF APY (7-10% 年化, 相对无风险收益) ───
        usdf_capital = max(usdf_minted, account_equity)  # 预计全部mint为USDF
        usdf_apy = (self.USDF_APY_LOW + self.USDF_APY_HIGH) / 2  # 取中值8.5%
        usdf_monthly_income = usdf_capital * usdf_apy / 12

        # ─── 空投份额参考估算（与积分估值并列展示，不重复计入 EV）───
        total_rh_pool = asterdex_data.get("total_rh_pool", 1_000_000)
        my_weekly_share = monthly_rh_earned / 4 / max(total_rh_pool, 1)
        monthly_aster_earned = my_weekly_share * self.WEEKLY_ALLOCATION * 4
        airdrop_value_monthly = monthly_aster_earned * aster_price

        # ─── 总月净 EV ───
        total_monthly = usdf_monthly_income + monthly_points_value - monthly_cost
        monthly_roi = total_monthly / max(account_equity, 1) * 100

        # Paper：必须用悲观单轮净额 + 学习门禁；禁止仅靠 rh_per_fee 负 EV 盲开。
        rh_per_fee = float(round_metrics.get("rh_per_fee_usd") or 0)
        pessimistic_net_round = self._pessimistic_round_net(round_metrics)
        gate = self._learning_gate()
        is_paper = False
        try:
            from backend.services.rebate_arb.capital_coordinator import capital_coordinator

            is_paper = capital_coordinator.is_paper_mode()
        except Exception:
            is_paper = False

        paper_data_collection = is_paper
        if is_paper:
            # Paper：持续收样本学习；cash/pt 负值只告警，不拒开
            is_viable = account_equity >= self.MIN_EQUITY
        else:
            is_viable = account_equity >= self.MIN_EQUITY and total_monthly > 0

        return StrategyEvaluation(
            strategy_type=RebateStrategyType.S8_ASTERDEX_RH,
            is_viable=is_viable,
            expected_monthly_value=round(total_monthly, 2),
            required_volume_usd=monthly_volume,
            risk_score=0.15,
            confidence=0.75,
            details={
                # Stage 6 模型
                "formula_version": round_metrics.get("formula_version"),
                "stage6_round": stage6,
                "monthly_points_value": round(monthly_points_value, 2),
                "valuation_speculative": True,
                "expected_maker_ratio": expected_maker_ratio,
                "paper_data_collection": paper_data_collection,
                "pessimistic_net_round_usd": round(pessimistic_net_round, 4),
                "learning_gate": gate,
                "effective_mode": mode,
                # 收益分解
                "usdf_monthly_income": round(usdf_monthly_income, 2),
                "airdrop_value_monthly": round(airdrop_value_monthly, 2),
                "monthly_aster_earned": round(monthly_aster_earned, 1),
                "net_fee_cost": round(net_fee_cost, 2),
                "monthly_cost": round(monthly_cost, 2),
                # 积分
                "monthly_rh_earned": round(monthly_rh_earned, 1),
                "my_weekly_share_pct": round(my_weekly_share * 100, 4),
                "current_rh_points": current_rh,
                # 交易参数
                "leverage": leverage,
                "position_size_usd": eval_notional,
                "eval_margin_usd": eval_margin,
                "daily_volume": daily_volume,
                "monthly_volume": monthly_volume,
                "rounds_per_day": self.ROUNDS_PER_DAY,
                "hold_minutes": self.MIN_HOLD_SECONDS // 60,
                # ROI
                "monthly_roi_pct": round(monthly_roi, 1),
                "aster_price": aster_price,
                # 系统信息
                "source_exchange": "asterdex",
                "collateral_type": "USDF",
                "order_type": "maker_first" if self._normalize_mode() == "stage6_optimal" else "taker_market",
                "min_equity": self.MIN_EQUITY,
                "api_automatable": True,
            },
        )

    def _macro_fallback_direction(self, symbol: str) -> Tuple[str, Dict[str, Any]]:
        """
        AI 信号中性时，用宏观过滤器（V5 Regime + 多周期 bias）兜底方向。

        返回 ("bullish"|"bearish"|"neutral", macro_detail)。
        宏观极端态/数据不可用/无明确方向时返回 neutral（调用方应跳过本轮）。
        """
        try:
            from backend.services.rebate_arb.macro_direction_filter import evaluate_macro_filter

            macro = evaluate_macro_filter(symbol, "neutral")
        except Exception as exc:
            logger.warning("[S8] macro fallback 不可用: %s", exc)
            return "neutral", {"reason": f"macro_unavailable:{exc}"}

        if macro.get("action") == "skip":
            return "neutral", macro

        allowed = str(macro.get("allowed_direction") or "both").lower()
        if allowed == "long_only":
            return "bullish", macro
        if allowed == "short_only":
            return "bearish", macro

        long_bias = str(macro.get("long_bias") or "neutral")
        mid_bias = str(macro.get("mid_bias") or "neutral")
        if long_bias == mid_bias and long_bias in ("bullish", "bearish"):
            return long_bias, macro
        for bias in (long_bias, mid_bias):
            if bias in ("bullish", "bearish"):
                return bias, macro
        return "neutral", macro

    def build_execution_plan(
        self, size_usd: float, symbol: str = "ETH/USDT", paper_mode: bool = True,
        ai_signal: Optional[Dict] = None,
        execution_hint: Optional[Dict] = None,
        points_maximization: Optional[bool] = None,
        funding_rate: float = 0.0,
    ) -> Dict[str, Any]:
        """
        构建 S8 执行计划 (AI信号增强)

        stage6_optimal 模式：Maker 限价优先（超时回退 Taker）+ 动态持仓 + USDF 全仓。
        旧 safe/quick 模式：Taker 市价单 + 65min 固定持仓。
        size_usd 表示目标名义价值；保证金由名义价值 ÷ 杠杆计算。
        """
        points_mode = (
            self.POINTS_MAXIMIZATION_MODE
            if points_maximization is None
            else bool(points_maximization)
        )
        target_notional_usd = max(float(size_usd or 0), 0.0)
        open_side = "buy"   # 默认做多
        close_side = "sell"
        position_scale = 1.0
        ai_metadata = {}
        leverage = self.DEFAULT_LEVERAGE
        gate = self._learning_gate(paper_mode=paper_mode)

        if ai_signal:
            if not ai_signal.get("available", True):
                return {
                    "strategy": "S8",
                    "strategy_version": "v3_ai_enhanced",
                    "skip": True,
                    "skip_reason": "AI 信号不可用，禁止默认开单",
                    "ai_signal": {"ai_action": "skip", "ai_reason": ai_signal.get("reasoning", "")},
                    "paper_mode": paper_mode,
                }

            direction = ai_signal.get("direction", "neutral")
            confidence = ai_signal.get("confidence", 50)
            risk_level = ai_signal.get("risk_level", "normal")

            # 风控门控: danger级别跳过本轮
            if risk_level == "danger":
                return {
                    "strategy": "S8",
                    "strategy_version": "v3_ai_enhanced",
                    "skip": True,
                    "skip_reason": f"AI risk_level=danger (confidence={confidence}%)",
                    "ai_signal": {
                        "ai_action": "skip",
                        "ai_reason": f"risk_level=danger, confidence={confidence}",
                    },
                    "paper_mode": paper_mode,
                }

            # AI方向决策
            direction_source = "ai"
            if direction == "bullish":
                open_side = "buy"
                close_side = "sell"
            elif direction == "bearish":
                open_side = "sell"
                close_side = "buy"
            else:
                # neutral → 默认 skip：无明确方向不开仓（修复旧版隐式默认做多）
                neutral_action = str(
                    getattr(self, "NEUTRAL_DIRECTION_ACTION", "skip") or "skip"
                ).lower()
                if neutral_action == "follow_macro":
                    # 积分策略收益主体是积分，中性轮全部跳过会损失大量积分轮次。
                    # 用宏观趋势方向兜底（有背书才开），仓位降档；宏观也无方向才跳过。
                    macro_dir, macro_meta = self._macro_fallback_direction(
                        ai_signal.get("symbol") or symbol
                    )
                    if macro_dir == "bullish":
                        open_side, close_side = "buy", "sell"
                    elif macro_dir == "bearish":
                        open_side, close_side = "sell", "buy"
                    else:
                        return {
                            "strategy": "S8",
                            "strategy_version": "v4_points_max" if points_mode else "v3_ai_enhanced",
                            "skip": True,
                            "skip_reason": (
                                f"AI 中性且宏观无明确方向 (confidence={confidence}%)，跳过本轮"
                            ),
                            "ai_signal": {
                                "ai_action": "skip",
                                "ai_reason": "neutral_and_macro_no_edge",
                                "ai_direction": direction,
                                "ai_confidence": confidence,
                            },
                            "macro_fallback": macro_meta,
                            "paper_mode": paper_mode,
                        }
                    direction_source = "macro_fallback"
                    position_scale = float(self.NEUTRAL_MACRO_POSITION_SCALE)
                    logger.info(
                        "[S8] 中性信号宏观兜底: direction=%s scale=%.2f reason=%s",
                        macro_dir, position_scale,
                        macro_meta.get("reason") or macro_meta.get("allowed_direction"),
                    )
                elif neutral_action != "half":
                    return {
                        "strategy": "S8",
                        "strategy_version": "v4_points_max" if points_mode else "v3_ai_enhanced",
                        "skip": True,
                        "skip_reason": f"AI 方向中性 (confidence={confidence}%)，默认不开仓",
                        "ai_signal": {
                            "ai_action": "skip",
                            "ai_reason": "neutral_direction",
                            "ai_direction": direction,
                            "ai_confidence": confidence,
                        },
                        "paper_mode": paper_mode,
                    }
                else:
                    # 显式配置 neutral_direction_action=half 时才允许半仓（方向默认做多）
                    direction_source = "neutral_half"
                    position_scale = 0.5

            if points_mode:
                # 积分优先：仅 danger 跳过；warning 不大幅缩仓。
                # recovery 期降低最低仓位，避免继续满配赌方向。
                min_scale = self.POINTS_MIN_POSITION_SCALE
                if paper_mode and gate.get("recovery_mode"):
                    min_scale = min(min_scale, 0.50)
                if risk_level == "warning":
                    position_scale = min(position_scale, 0.95)
                if direction_source == "ai":
                    position_scale = max(position_scale, min_scale)
            else:
                # 置信度 → 仓位缩放
                if confidence >= 75:
                    position_scale *= 1.0
                elif confidence >= 60:
                    position_scale *= 0.8
                elif confidence >= 45:
                    position_scale *= 0.6
                else:
                    position_scale *= 0.4
                if risk_level == "warning":
                    position_scale *= 0.7

            ai_metadata = {
                "ai_direction": direction,
                "direction_source": direction_source,
                "effective_side": open_side,
                "ai_confidence": confidence,
                "ai_risk_level": risk_level,
                "ai_position_scale": round(position_scale, 2),
                "ai_symbol": ai_signal.get("symbol", ""),
                "ai_reasoning": ai_signal.get("reasoning", "")[:200],
                "strategy_model_id": ai_signal.get("llm_config_id"),
                "points_maximization_mode": points_mode,
            }

        leverage = self.DEFAULT_LEVERAGE
        if execution_hint and execution_hint.get("available"):
            exec_scale = float(execution_hint.get("position_scale", 1.0))
            if points_mode:
                exec_scale = max(exec_scale, self.POINTS_MIN_POSITION_SCALE)
            position_scale *= exec_scale
            leverage = int(execution_hint.get("leverage", self.DEFAULT_LEVERAGE))
            ai_metadata = ai_metadata or {}
            ai_metadata.update({
                "execution_model_id": execution_hint.get("llm_config_id"),
                "execution_position_scale": execution_hint.get("position_scale"),
                "execution_leverage": leverage,
                "execution_reasoning": (execution_hint.get("reasoning") or "")[:200],
            })

        optimizer = self._build_optimizer_decision(
            paper_mode=paper_mode,
            margin_usd=(target_notional_usd * position_scale) / max(float(leverage or 1), 1.0),
            symbol=symbol,
            ai_signal=ai_signal,
            requested_leverage=leverage,
            funding_rate=funding_rate,
        )
        leverage = int(optimizer["leverage"])
        hold_total_seconds = int(optimizer["hold_seconds"])
        notional_usd = target_notional_usd * position_scale * float(optimizer.get("margin_scale", 1.0) or 1.0)
        adjusted_margin = notional_usd / max(float(leverage or 1), 1.0)
        sym_boost = self.symbol_boost(symbol)
        maker_ratio = float(optimizer.get("maker_ratio", 0.0) or 0.0)
        rh_metrics = self.estimate_round_metrics(
            margin_usd=adjusted_margin,
            leverage=leverage,
            symbol=symbol,
            hold_seconds=hold_total_seconds,
            confidence=(ai_signal or {}).get("confidence", 50),
            risk_level=(ai_signal or {}).get("risk_level", "normal"),
            funding_rate=funding_rate,
            maker_ratio=maker_ratio,
        )

        # stage6_optimal: Maker 限价优先（0 费率 + Maker 流动性积分），超时回退 Taker
        is_stage6 = optimizer["mode"] == "stage6_optimal"
        maker_first = bool(is_stage6 and self.STAGE6_MAKER_FIRST)
        order_type = "limit" if maker_first else "market"
        order_extras: Dict[str, Any] = {}
        if maker_first:
            order_extras = {
                "post_only": True,
                "taker_fallback": True,
                "taker_fallback_seconds": int(self.STAGE6_TAKER_FALLBACK_SECONDS),
            }

        pre_steps: List[Dict[str, Any]] = [
            {
                "action": "mint_usdf",
                "exchange": "asterdex",
                "description": "USDT→USDF铸造, 激活资产积分（USDF保证金）",
                "amount_usd": adjusted_margin,
                "skip_if_sufficient": True,
            }
        ]
        if is_stage6 and self.STAGE6_REQUIRE_CROSS_MARGIN:
            # Stage 6 资产积分要求全仓（cross-margin）模式
            pre_steps.append({
                "action": "ensure_cross_margin",
                "exchange": "asterdex",
                "symbol": symbol,
                "description": "确认全仓保证金模式（资产积分要求 cross-margin）",
            })

        return {
            "strategy": "S8",
            "strategy_version": "v4_points_max" if points_mode else "v3_ai_enhanced",
            "points_maximization_mode": points_mode,
            "rh_optimization_mode": optimizer["mode"],
            "rh_optimizer": {
                "mode": optimizer["mode"],
                "requested_mode": optimizer["requested_mode"],
                "margin_scale": optimizer["margin_scale"],
                "maker_ratio": maker_ratio,
                "reasons": optimizer["reasons"],
            },
            "margin_usd": round(adjusted_margin, 2),
            "symbol_boost": sym_boost,
            "rh_metrics": rh_metrics,
            "paper_ab_test_matrix": optimizer.get("paper_ab_test_matrix", []),

            # ─── 前置步骤: USDF铸造 (+ stage6 全仓模式确认) ───
            "pre_steps": pre_steps,

            # ─── 开仓: stage6=Maker限价优先(超时回退Taker)，旧模式=Taker市价 ───
            "side_a": {
                "exchange": "asterdex",
                "symbol": symbol,
                "side": open_side,
                "type": order_type,
                "margin_usd": round(adjusted_margin, 2),
                "size_usd": round(notional_usd, 2),
                "collateral": "USDF",
                "leverage": leverage,
                "margin_mode": "cross" if is_stage6 else None,
                **order_extras,
            },

            # ─── 单所策略, 无对冲腿 ───
            "side_b": None,

            # ─── 持仓阶段: stage6 动态 2-8h，旧模式 ≥60min ───
            "hold_phase": {
                "min_hold_seconds": min(self.MIN_HOLD_SECONDS, hold_total_seconds),
                "buffer_seconds": max(hold_total_seconds - self.MIN_HOLD_SECONDS, 0),
                "total_seconds": hold_total_seconds,
                "reason": (
                    "Stage6 动态持仓：持仓积分无上限，时长按资金费率方向调节"
                    if is_stage6
                    else (
                        "Paper实验短持仓，用于校准Trade/Position Rh效率"
                        if optimizer["mode"] == "paper_experiment"
                        else "持仓>60min触发2x时间加成"
                    )
                ),
                "check_interval_seconds": min(300, max(30, hold_total_seconds // 4)),
            },

            # ─── 平仓: 与开仓同类型（stage6=Maker限价优先），反向 ───
            "close_plan": {
                "exchange": "asterdex",
                "symbol": symbol,
                "side": close_side,
                "type": order_type,
                "margin_usd": round(adjusted_margin, 2),
                "size_usd": round(notional_usd, 2),
                "trigger": "time_elapsed",
                "min_elapsed_seconds": hold_total_seconds,
                **order_extras,
            },

            # ─── 后置步骤: 积分快照 ───
            "post_steps": [
                {
                    "action": "snapshot_rh_points",
                    "exchange": "asterdex",
                    "description": "记录本轮Rh积分获取",
                }
            ],

            # ─── AI信号元数据 ───
            "ai_signal": ai_metadata,

            # ─── 积分模型（Stage 6；multiplier_stack 仅作旧版展示兼容） ───
            "target": "rh_points_stage6" if is_stage6 else ("rh_points_max" if points_mode else "rh_points_80x"),
            "stage6_breakdown": rh_metrics.get("stage6", {}),
            "multiplier_stack": {
                "taker": 2,
                "hold_time": 2,
                "usdf": 20,
                "symbol_boost": sym_boost,
                "combined_max": rh_metrics["combined_multiplier"],
                "deprecated": True,
            },
            "estimated_round_rh": rh_metrics["estimated_rh"],
            "rounds_per_day": self.ROUNDS_PER_DAY,
            "paper_mode": paper_mode,
        }

    def estimate_daily_schedule(self, account_equity: float) -> Dict[str, Any]:
        """
        估算每日交易排程

        每轮65分钟(60min持仓+5min buffer), 每日3轮
        总计约3.25小时活跃交易时间
        """
        leverage = min(self.MAX_LEVERAGE, self.DEFAULT_LEVERAGE)
        position_size = account_equity * leverage

        rounds = []
        for i in range(self.ROUNDS_PER_DAY):
            round_start_offset = i * (self.ROUND_DURATION_SECONDS + 600)  # 10min间隔
            rounds.append({
                "round": i + 1,
                "open_offset_seconds": round_start_offset,
                "hold_seconds": self.ROUND_DURATION_SECONDS,
                "close_offset_seconds": round_start_offset + self.ROUND_DURATION_SECONDS,
                "position_size_usd": position_size,
                "volume_this_round": position_size * 2,
            })

        total_volume_daily = position_size * 2 * self.ROUNDS_PER_DAY
        total_active_minutes = self.ROUNDS_PER_DAY * (self.ROUND_DURATION_SECONDS / 60 + 10)

        return {
            "rounds": rounds,
            "total_daily_volume": total_volume_daily,
            "total_active_minutes": total_active_minutes,
            "position_size_per_round": position_size,
            "leverage": leverage,
        }

    def _get_volume_tier_multiplier(self, monthly_volume: float) -> float:
        """获取月交易量对应的tier乘数"""
        multiplier = 1.0
        for threshold, mult in self.VOLUME_TIER_MULTIPLIERS:
            if monthly_volume >= threshold:
                multiplier = mult
        return multiplier

    # ══════════════════════════════════════════════════
    # AI信号集成: 方向决策 + 选币 + 风控
    # ══════════════════════════════════════════════════

    # Asterdex上可交易的候选币种 (永续合约)
    CANDIDATE_SYMBOLS = [
        "ASTER/USDT", "ETH/USDT", "BTC/USDT", "SOL/USDT", "BNB/USDT",
        "ARB/USDT", "DOGE/USDT", "AVAX/USDT", "MATIC/USDT",
    ]

    def query_ai_signal(self, symbol: str = "ETH") -> Dict[str, Any]:
        """
        查询AI情报信号引擎获取交易方向

        调用 IntelligenceSignalEngine.compute_trading_signal()
        返回: direction, confidence, risk_level, reasoning
        """
        try:
            from backend.services.intelligence_signal_engine import intelligence_signal_engine
            sig = intelligence_signal_engine.compute_trading_signal(symbol)

            return {
                "available": True,
                "direction": sig.direction,
                "confidence": sig.confidence,
                "risk_level": sig.risk_level,
                "symbol": sig.symbol,
                "reasoning": sig.ai_reasoning or "",
                "funding_regime": sig.funding.regime if sig.funding else "unknown",
                "oi_signal": sig.oi.signal if sig.oi else "unknown",
                "whale_direction": sig.whale_direction,
                "fear_greed": sig.fear_greed_index,
                "timestamp": sig.timestamp,
            }
        except Exception as e:
            logger.warning(f"[S8] AI signal query failed for {symbol}: {e}")
            return {
                "available": False,
                "direction": "neutral",
                "confidence": 0,
                "risk_level": "danger",
                "symbol": symbol,
                "reasoning": f"AI signal unavailable: {e}",
            }

    def select_best_symbol(
        self, candidates: Optional[List[str]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        AI选币: 从候选池中选择信号最强的币种

        策略:
        1. 查询每个候选币种的AI信号
        2. 过滤掉 risk_level=danger 的
        3. 按 confidence+方向性 综合得分降序排列
        4. 返回信号最强的 (symbol, ai_signal)
        """
        if candidates is None:
            candidates = self.CANDIDATE_SYMBOLS

        scored: List[Tuple[float, str, Dict]] = []

        for symbol_pair in candidates:
            base_symbol = symbol_pair.split("/")[0]
            sig = self.query_ai_signal(base_symbol)

            if not sig.get("available", True):
                logger.debug(f"[S8] Skip {base_symbol}: AI signal unavailable")
                continue

            if sig.get("risk_level") == "danger":
                logger.debug(f"[S8] Skip {base_symbol}: danger risk level")
                continue

            confidence = sig.get("confidence", 0)
            direction = sig.get("direction", "neutral")
            boost = self.symbol_boost(symbol_pair)
            direction_bonus = 10 if direction in ("bullish", "bearish") else -5
            boost_bonus = (boost - 1.0) * 25.0
            score = confidence + direction_bonus + boost_bonus

            scored.append((score, symbol_pair, {**sig, "symbol_boost": boost}))

        if not scored:
            logger.warning("[S8] No viable symbol/signal — skip round (no default fallback)")
            return "", {
                "available": False,
                "direction": "neutral",
                "confidence": 0,
                "risk_level": "danger",
                "symbol": "",
                "reasoning": "no_viable_ai_signal",
            }

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_symbol, best_signal = scored[0]

        logger.info(
            f"[S8] AI选币: {best_symbol} score={best_score:.0f} "
            f"direction={best_signal['direction']} "
            f"confidence={best_signal['confidence']}%"
        )

        return best_symbol, best_signal

    def _select_with_strategy_model(
        self,
        size_usd: float,
        candidates: Optional[List[str]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """专用套利：策略分析模型选币 + 方向。"""
        from backend.services.rebate_arb.arb_llm_planner import call_strategy_model

        if not self.strategy_llm_config_id:
            return self.select_best_symbol(candidates)

        pool = candidates or self.CANDIDATE_SYMBOLS
        pool = sorted(pool, key=lambda s: self.symbol_boost(s), reverse=True)
        intel_rows: List[Dict[str, Any]] = []
        for symbol_pair in pool:
            base_symbol = symbol_pair.split("/")[0]
            row = {"symbol": symbol_pair, **self.query_ai_signal(base_symbol)}
            row["symbol_boost"] = self.symbol_boost(symbol_pair)
            intel_rows.append(row)

        sig = call_strategy_model(
            int(self.strategy_llm_config_id),
            pool,
            intel_rows,
            size_usd,
            account_id=self.account_id,
        )
        if not sig.get("available"):
            return "", sig

        symbol = sig.get("symbol") or ""
        if "/" not in symbol:
            symbol = f"{symbol}/USDT"
        logger.info(
            "[S8] 策略模型 #%s 选币: %s direction=%s confidence=%s",
            self.strategy_llm_config_id,
            symbol,
            sig.get("direction"),
            sig.get("confidence"),
        )
        return symbol, sig

    def build_ai_enhanced_plan(
        self, size_usd: float, paper_mode: bool = True,
        candidates: Optional[List[str]] = None,
        trader_profile: Optional[Dict[str, Any]] = None,
        target_margin_usd: Optional[float] = None,
        account_equity: Optional[float] = None,
        paper_account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        一键构建AI增强的S8执行计划

        size_usd 表示名义价值；target_margin_usd 仅作为资金上限输入，
        会先按默认杠杆换算为目标名义价值。
        """
        if trader_profile:
            self.strategy_llm_config_id = trader_profile.get("strategy_llm_config_id")
            self.execution_llm_config_id = trader_profile.get("execution_llm_config_id")
            self.account_id = trader_profile.get("account_id")

        if target_margin_usd is not None:
            margin_budget = float(target_margin_usd or 0) * float(self.DEFAULT_LEVERAGE or 1)
        else:
            margin_budget = float(size_usd or 0)
        points_mode = self.POINTS_MAXIMIZATION_MODE

        # 分析与执行允许共用同一个模型（单模型部署是常态），不再因相同而跳过

        best_symbol, ai_signal = self._select_with_strategy_model(margin_budget, candidates)
        if not best_symbol or not ai_signal.get("available", True):
            return {
                "strategy": "S8",
                "strategy_version": "v3_ai_enhanced",
                "skip": True,
                "skip_reason": "策略分析失败，本轮跳过",
                "ai_signal": {"ai_action": "skip", "ai_reason": ai_signal.get("reasoning", "")},
                "paper_mode": paper_mode,
            }

        gate = self._learning_gate()
        if paper_mode and account_equity is not None and account_equity > 0:
            _allowed, gate_reason, gate_details = self.check_paper_open_gate(
                account_equity=float(account_equity),
                paper_account_id=paper_account_id,
                symbol=best_symbol,
            )
            # Paper 不因 learning / 悲观 EV 拒开；check 仅写 advisory
            if not _allowed:
                return {
                    "strategy": "S8",
                    "strategy_version": "v4_points_max" if points_mode else "v3_ai_enhanced",
                    "skip": True,
                    "skip_reason": gate_reason,
                    "paper_open_gate": gate_details,
                    "paper_mode": paper_mode,
                }

        base_symbol = best_symbol.split("/")[0]
        macro_filter: Dict[str, Any] = {"passed": True, "action": "allow"}
        short_tactical: Dict[str, Any] = {}
        try:
            from backend.services.rebate_arb.macro_direction_filter import evaluate_macro_filter

            macro_filter = evaluate_macro_filter(base_symbol, ai_signal.get("direction", "neutral"))
            if macro_filter.get("action") == "skip":
                # Paper 与 Live 行为统一：宏观 skip 即跳过本轮，
                # 保证 Paper 验证结果可外推到实盘（旧版 Paper 满配/半仓 override 已移除）
                logger.info(
                    "[S8][%s] 宏观过滤 skip: %s",
                    "Paper" if paper_mode else "Live",
                    macro_filter.get("reason") or "macro_counter_trend",
                )
                return {
                    "strategy": "S8",
                    "strategy_version": "v4_points_max" if points_mode else "v3_ai_enhanced",
                    "skip": True,
                    "skip_reason": macro_filter.get("reason") or "macro_counter_trend",
                    "macro_filter": macro_filter,
                    "paper_mode": paper_mode,
                }
            adj = float(macro_filter.get("confidence_adjust") or 0)
            if adj and ai_signal.get("confidence") is not None and not points_mode:
                ai_signal["confidence"] = max(
                    0.0,
                    min(100.0, float(ai_signal["confidence"]) + adj * 100),
                )
            if macro_filter.get("action") == "half" and not points_mode:
                margin_budget *= 0.5
        except Exception as exc:
            # fail-closed：宏观过滤模块本身异常时跳过本轮，不再静默放行
            logger.warning("[S8] 宏观过滤异常，本轮 fail-closed skip: %s", exc)
            return {
                "strategy": "S8",
                "strategy_version": "v4_points_max" if points_mode else "v3_ai_enhanced",
                "skip": True,
                "skip_reason": f"macro_filter_error:{exc}",
                "macro_filter": {"passed": False, "action": "skip", "reason": str(exc)},
                "paper_mode": paper_mode,
            }

        try:
            from backend.services.strategy_orchestrator.short_term_tactician import ShortTermTactician
            from backend.services.strategy_orchestrator.short_term_tactician import ShortTermContext

            tactician = ShortTermTactician()
            ctx = ShortTermContext(symbol=base_symbol)
            signal = tactician.analyze(ctx)
            short_tactical = {
                "allowed_direction": getattr(signal, "allowed_direction", "both"),
                "confidence": getattr(signal, "confidence", 0),
            }
            allowed = (short_tactical.get("allowed_direction") or "both").lower()
            direction = ai_signal.get("direction", "neutral")
            if allowed == "long_only" and direction == "bearish":
                return {
                    "strategy": "S8",
                    "skip": True,
                    "skip_reason": "short_tactician_long_only",
                    "short_tactical": short_tactical,
                    "paper_mode": paper_mode,
                }
            if allowed == "short_only" and direction == "bullish":
                return {
                    "strategy": "S8",
                    "skip": True,
                    "skip_reason": "short_tactician_short_only",
                    "short_tactical": short_tactical,
                    "paper_mode": paper_mode,
                }
        except Exception as exc:
            logger.debug("[S8] short tactician skip: %s", exc)

        execution_hint: Optional[Dict[str, Any]] = None
        if self.execution_llm_config_id:
            from backend.services.rebate_arb.arb_llm_planner import call_execution_model

            execution_hint = call_execution_model(
                int(self.execution_llm_config_id),
                ai_signal,
                margin_budget,
                {
                    "default_leverage": self.DEFAULT_LEVERAGE,
                    "max_leverage": self.MAX_LEVERAGE,
                    "min_hold_seconds": self.MIN_HOLD_SECONDS,
                    "rh_optimization_mode": self._normalize_mode(),
                    "quick_min_confidence": self.QUICK_MIN_CONFIDENCE,
                    "quick_min_symbol_boost": self.QUICK_MIN_SYMBOL_BOOST,
                    "macro_filter": macro_filter,
                    "short_tactical": short_tactical,
                    "points_maximization_mode": points_mode,
                },
                account_id=self.account_id,
            )
            if not execution_hint.get("available"):
                return {
                    "strategy": "S8",
                    "strategy_version": "v3_ai_enhanced",
                    "skip": True,
                    "skip_reason": "执行规划模型不可用",
                    "ai_signal": {"ai_action": "skip", "ai_reason": execution_hint.get("reasoning", "")},
                    "paper_mode": paper_mode,
                }
            if not execution_hint.get("execute_now", True):
                return {
                    "strategy": "S8",
                    "strategy_version": "v3_ai_enhanced",
                    "skip": True,
                    "skip_reason": execution_hint.get("reasoning") or "执行模型建议跳过",
                    "ai_signal": {"ai_action": "skip", "ai_reason": execution_hint.get("reasoning", "")},
                    "paper_mode": paper_mode,
                }

        plan = self.build_execution_plan(
            size_usd=margin_budget,
            symbol=best_symbol,
            paper_mode=paper_mode,
            ai_signal=ai_signal,
            execution_hint=execution_hint,
            points_maximization=points_mode,
            funding_rate=self._fetch_symbol_funding_rate(best_symbol),
        )
        if isinstance(plan, dict):
            plan["macro_filter"] = macro_filter
            plan["short_tactical"] = short_tactical

        return plan

    def _fetch_symbol_funding_rate(self, symbol: str) -> float:
        """
        拉取 asterdex 上该币种的当前资金费率（stage6_optimal 动态持仓用）。

        数据不可用时返回 0（持仓时长退回默认值，不影响开仓决策）。
        """
        try:
            from backend.services.rebate_arb.tick_context import fetch_funding_rates

            rates = fetch_funding_rates([symbol]) or {}
            # 结构可能是 {exchange: {symbol: rate}} 或扁平 {symbol: rate}
            for key, value in rates.items():
                if isinstance(value, dict):
                    for sym, rate in value.items():
                        if str(sym).split("/")[0].split("-")[0] == symbol.split("/")[0]:
                            return float(rate or 0)
                elif str(key).split("/")[0].split("-")[0] == symbol.split("/")[0]:
                    return float(value or 0)
        except Exception as exc:
            logger.debug("[S8] funding rate fetch skip: %s", exc)
        return 0.0
