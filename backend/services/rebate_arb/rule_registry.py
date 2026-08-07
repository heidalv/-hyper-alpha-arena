"""RuleRegistry — 六所规则源注册表。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
from typing import Any, Dict, List


@dataclass(frozen=True)
class RuleSource:
    source_id: str
    exchange: str
    rule_type: str
    title: str
    url: str
    affected_strategies: List[str]
    auto_pause_enabled: bool

    def to_dict(self) -> Dict:
        return asdict(self)


RULE_SOURCES: List[RuleSource] = [
    RuleSource("hl_points_docs", "hyperliquid", "points", "Hyperliquid Points", "https://hyperliquid.gitbook.io/hyperliquid-docs/", ["S3", "S5"], True),
    RuleSource("hl_fees_docs", "hyperliquid", "fees", "Hyperliquid Fees", "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees", ["S5"], True),
    RuleSource("binance_alpha_rules", "binance", "alpha_points", "Binance Alpha Points", "https://www.binance.com/en/support/announcement", ["S7"], True),
    RuleSource("binance_fees", "binance", "fees", "Binance Fee Schedule", "https://www.binance.com/en/fee/trading", ["S1", "S7"], True),
    RuleSource("asterdex_rh_rules", "asterdex", "rh_points", "Asterdex Rh/ASTER Rules", "https://www.asterdex.com/", ["S8"], True),
    RuleSource("asterdex_fees", "asterdex", "fees", "Asterdex Fees", "https://www.asterdex.com/", ["S1", "S6", "S8"], True),
    RuleSource("okx_vip_fees", "okx", "vip_fees", "OKX VIP/Fee Rules", "https://www.okx.com/fees", ["S2", "S4"], False),
    RuleSource("okx_campaigns", "okx", "campaigns", "OKX Campaign Rules", "https://www.okx.com/help/section/announcements", ["S4"], False),
    RuleSource("bybit_fees", "bybit", "fees", "Bybit Fee Rules", "https://www.bybit.com/en/announcement-info/", ["S4", "S6"], False),
    RuleSource("bybit_campaigns", "bybit", "campaigns", "Bybit Campaign Rules", "https://announcements.bybit.com/", ["S4"], False),
    RuleSource("gateio_fees", "gateio", "fees", "Gate.io Fee Rules", "https://www.gate.io/fee", ["S4", "S6"], False),
    RuleSource("gateio_campaigns", "gateio", "campaigns", "Gate.io Campaign Rules", "https://www.gate.io/announcements", ["S4"], False),
]


# ══════════════════════════════════════════════════════════════════
# Asterdex Stage 6 Convergence 积分模型（官方现行规则）
#
#   总积分 = (交易积分 + 持仓积分 + Aster资产积分 + 清算积分 + 盈亏积分)
#            × 团队加成(1.05-1.2x) + 推荐积分
#
# 与旧版差异：
# - 旧「80x 乘数模型」(Taker 2x × 持仓 2x × USDF 20x) 已废弃
# - Maker 挂单 0% 手续费且同样赚取积分（流动性贡献）
# - 持仓积分/资产积分已取消上限（T+1 结算）
# - 官方明确惩罚对冲刷分（wash trade 取消资格）
# - 真实费率：USDT 永续 0% maker / 0.04% taker；USD1 永续 taker 0.005%
#
# 注意：官方未公开各类别精确权重，以下数值为可调估算值（estimate），
# 通过 YAML strategies.S8_asterdex_rh.stage6_model 可覆盖。
# ══════════════════════════════════════════════════════════════════
STAGE6_POINT_MODEL: Dict[str, Any] = {
    "version": "stage6_convergence_v1",
    "formula": "(trading + position + aster_asset + liquidation + pnl) * team_boost + referral",
    # ── 费率单一来源（fee_schedule）：策略/YAML/EV 模型统一从这里读 ──
    "fee_schedule": {
        "usdt_perp": {"maker": 0.0, "taker": 0.0004},
        "usd1_perp": {"maker": 0.0, "taker": 0.00005},
        # 用 ASTER 支付手续费再省 5%
        "aster_fee_discount": 0.05,
    },
    # ── 交易积分：手续费贡献 + Maker 流动性贡献，乘以币种加成 ──
    "trading": {
        # 每 $1 手续费贡献的积分（估算可调）
        "points_per_usd_fee": 100.0,
        # Maker 挂单每 $1k 成交名义的流动性积分（0 费率仍计分，估算可调）
        "maker_points_per_1k_usd": 1.0,
    },
    # ── 持仓积分：规模 × 时长，无上限，T+1 ──
    "position": {
        "points_per_1k_usd_hour": 0.5,
    },
    # ── Aster 资产积分：USDF/ASTER/asBNB 保证金余额，需全仓模式，无上限 ──
    "aster_asset": {
        "points_per_1k_usd_hour": 1.0,
        "requires_cross_margin": True,
        "eligible_assets": ["USDF", "ASTER", "asBNB"],
    },
    # ── 盈亏积分：每小时净盈亏（不含资金费）计入；双向都算但真实亏损是真亏 ──
    "pnl": {
        "points_per_usd_abs_pnl": 0.5,
    },
    # ── 清算积分：清算费计分 → 对交易者是纯损失，策略上必须避免清算 ──
    "liquidation": {
        "enabled": False,
        "note": "清算积分是清算费的补偿，主动追求等于烧钱，杠杆控制避免清算",
    },
    # ── 团队加成 / 推荐积分（账号运营层面，代码内只做展示） ──
    "team_boost": {"min": 1.05, "max": 1.20, "default": 1.05},
    "referral": {"note": "推荐积分独立累加，不进入 EV 模型"},
    # ── 积分估值（投机性！官方未承诺兑换比例，按周空投池摊算的估值折扣后使用）──
    "point_valuation": {
        "usd_per_point_estimate": 0.01,
        "speculative_discount": 0.5,
        "note": "估值 = usd_per_point_estimate × speculative_discount，前端需标注投机性",
    },
    "wash_trade_policy": "官方明确惩罚对冲刷分，wash trade 直接取消资格 → 仅做单边方向仓",
}


STRATEGY_RULE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "S7": {
        "MODE": "monitor_only",
        "TOKENS_PER_POINT": 22,
        "AVG_TOKEN_VALUE": 0.50,
        "GRADUATION_RATE": 0.475,
        "FIRST_DAY_PREMIUM": 3.0,
        "rule_source_ids": ["binance_alpha_rules", "binance_fees"],
        "note": "S7 remains monitor_only until Rule Sync has enough stable snapshots.",
    },
    "S8": {
        # ── Stage 6 费率校正（旧值 taker 0.005% 与官方差 8 倍，已校正）──
        "TAKER_FEE": 0.0004,    # USDT 永续 0.04% taker
        "MAKER_FEE": 0.0,       # Stage 6 Maker 0% 且赚积分
        "REBATE_RATE": 0.0,     # 旧赛季 10% 返佣假设废弃（保守按 0，邀请返佣另算）
        "MIN_HOLD_SECONDS": 3600,
        "HOLD_BUFFER_SECONDS": 300,
        "STAGE_6_ALLOCATION": 64_000_000,
        "STAGE_EPOCHS": 12,
        "ASTER_PRICE": 0.70,
        # Stage 6 积分类别模型（取代旧 80x 乘数模型）
        "STAGE6_MODEL": STAGE6_POINT_MODEL,
        # ── 旧 80x 乘数模型参数：仅兼容保留，EV 不再使用 ──
        "USDF_AU_MULTIPLIER": 20,
        "TAKER_RH_WEIGHT": 2.0,
        "HOLD_TIME_BOOST": 2.0,
        "LEGACY_MULTIPLIER_DEPRECATED": True,
        "rule_source_ids": ["asterdex_rh_rules", "asterdex_fees"],
    },
}


class RuleRegistry:
    """Static MVP registry; DB-backed source editing can be added later."""

    def __init__(self):
        self._sources = {s.source_id: s for s in RULE_SOURCES}

    def list_sources(self) -> List[Dict]:
        return [s.to_dict() for s in RULE_SOURCES]

    def get_source(self, source_id: str) -> RuleSource:
        source = self._sources.get(source_id)
        if not source:
            raise KeyError(f"unknown rule source: {source_id}")
        return source

    def get_strategy_rule_params(self, strategy_id: str) -> Dict[str, Any]:
        """Return rule-backed defaults for a strategy."""
        sid = (strategy_id or "").upper()
        params = deepcopy(STRATEGY_RULE_DEFAULTS.get(sid, {}))
        params["strategy_type"] = sid
        params["source"] = "rule_registry_defaults"
        return params

    def list_strategy_rule_params(self) -> Dict[str, Dict[str, Any]]:
        return {
            sid: self.get_strategy_rule_params(sid)
            for sid in sorted(STRATEGY_RULE_DEFAULTS)
        }


rule_registry = RuleRegistry()
