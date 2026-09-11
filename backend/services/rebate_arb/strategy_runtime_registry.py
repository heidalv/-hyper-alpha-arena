"""
S1–S8 策略运行规格 — 每种策略如何决策、如何执行、Paper 能否自动跑。

用于：
- Paper 启动前检查（validate_start）
- tick 自动执行过滤（禁止未接入引擎的策略静默开单）
- 前端展示策略运行说明
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class StrategyRuntimeSpec:
    strategy_id: str
    name: str
    category: str  # points_arb | trade_points | monitor
    execution_mode: str
    # hedge=双所对冲 fixed legs
    # directional=单腿方向仓（需信号）
    # maker_roundtrip=单所 maker 开平刷积分
    # volume_program=刷量/VIP/活动（规划型，非标准下单）
    # monitor_only=只监控
    required_exchanges: tuple
    min_equity_usd: float
    paper_auto_executable: bool
    requires_trader_profile: bool
    requires_ai_signal: bool
    requires_funding_signal: bool
    direction_rule: str
    hold_model: str
    summary: str
    not_ready_reason: str = ""
    ai_decision_mode: str = "none"
    coordination_group: str = ""
    macro_filter_required: bool = False
    qaa_agent_chain: tuple = ()


# M4 注销（2026-06）：S1/S5 已下线，从运行时注册表移除；S6 于 2026-08-13（R3）一并移除。
# - S1 Maker返佣对冲：负 EV，与 S6 重复且更差，且 Stage 6 惩罚对冲刷分
# - S5 资金费率+积分：数据结构假设错误，与 V3 资金费套利重复
# - S6 跨所费率差：负 EV，伪可行门槛
# 历史实现经 git 历史找回（R3 死代码清除前 commit）。
STRATEGY_RUNTIME: Dict[str, StrategyRuntimeSpec] = {
    "S2": StrategyRuntimeSpec(
        strategy_id="S2",
        name="VIP等级冲刺",
        category="trade_points",
        execution_mode="volume_program",
        required_exchanges=("okx",),
        min_equity_usd=10_000.0,
        paper_auto_executable=True,
        requires_trader_profile=True,
        requires_ai_signal=False,
        requires_funding_signal=False,
        direction_rule="volume_target",
        hold_model="program",
        ai_decision_mode="optional_deep",
        coordination_group="volume_program",
        summary="OKX 30 日成交量冲刺下一 VIP 档；volume_program 执行器接入 QAA vip_sprint 管道。",
    ),
    "S3": StrategyRuntimeSpec(
        strategy_id="S3",
        name="HL积分挖矿",
        category="points_arb",
        execution_mode="maker_roundtrip",
        required_exchanges=("hyperliquid",),
        min_equity_usd=100.0,
        paper_auto_executable=True,
        requires_trader_profile=True,
        requires_ai_signal=False,
        requires_funding_signal=False,
        direction_rule="fixed_roundtrip",
        hold_model="scheduled_close",
        summary="Hyperliquid Maker 限价开 + 限价平，刷 Points；方向固定 round-trip，不需 AI 定多空。",
    ),
    "S4": StrategyRuntimeSpec(
        strategy_id="S4",
        name="活动套利",
        category="trade_points",
        execution_mode="volume_program",
        required_exchanges=("okx", "bybit", "gateio"),
        min_equity_usd=500.0,
        paper_auto_executable=True,
        requires_trader_profile=True,
        requires_ai_signal=False,
        requires_funding_signal=False,
        direction_rule="campaign_rules",
        hold_model="program",
        ai_decision_mode="optional_deep",
        coordination_group="volume_program",
        summary="依赖交易所活动 campaign；volume_program 执行器 + QAA campaign 管道。",
    ),
    "S7": StrategyRuntimeSpec(
        strategy_id="S7",
        name="Binance Alpha",
        category="monitor",
        execution_mode="monitor_only",
        required_exchanges=("binance",),
        min_equity_usd=3000.0,
        paper_auto_executable=False,
        requires_trader_profile=False,
        requires_ai_signal=False,
        requires_funding_signal=False,
        direction_rule="none",
        hold_model="monitor",
        summary="仅监控 Alpha 积分与规则变化，不参与 Paper 自动执行。",
        not_ready_reason="S7 为 monitor_only，禁止加入 Paper 自动验证。",
    ),
    "S8": StrategyRuntimeSpec(
        strategy_id="S8",
        name="Asterdex Rh+ASTER",
        category="points_arb",
        execution_mode="directional",
        required_exchanges=("asterdex",),
        min_equity_usd=100.0,
        paper_auto_executable=True,
        requires_trader_profile=True,
        requires_ai_signal=True,
        requires_funding_signal=False,
        direction_rule="ai_signal",
        hold_model="hold_60min_taker_close",
        ai_decision_mode="required_deep_quick",
        coordination_group="directional_mutex",
        macro_filter_required=True,
        summary="Asterdex 合约单腿方向仓；QAA analyst+planner，macro 过滤，持仓≥60min Taker 平仓。",
        not_ready_reason="S8 需要有效 AI 信号；信号不可用或 risk=danger 时必须跳过，不能默认开单。",
    ),
    # [2026-09-04 p2-arb-infra] SDN 此前在 ALL_STRATEGIES 可扫可评，但 STRATEGY_RUNTIME
    # 缺席 → Paper validate_start / 自动执行过滤会当成「未知策略」拒掉。补上后与 YAML
    # rebate_arb_config 的 SDN_delta_neutral.enabled 对齐，Paper 才能合法跑。
    "SDN": StrategyRuntimeSpec(
        strategy_id="SDN",
        name="Delta-Neutral 资金费+积分",
        category="points_arb",
        execution_mode="hedge",
        required_exchanges=("asterdex", "binance"),  # 典型：积分所做多 + 深所做空；实际腿由矩阵选定
        min_equity_usd=100.0,
        paper_auto_executable=True,
        requires_trader_profile=False,
        requires_ai_signal=False,
        requires_funding_signal=True,
        direction_rule="funding_matrix",
        hold_model="adaptive_7_21d",
        ai_decision_mode="none",
        coordination_group="delta_neutral",
        summary="多场所资金费矩阵选最优 combo：积分/高费率所做多 + 深流动所做空，"
                "赚净资金费并刷积分；持有期 7–21 天自适应摊平手续费。",
        not_ready_reason="需 MULTI_VENUE_FUNDING_COLLECTOR 覆盖 ≥2 场所且净 APR 过门；"
                         "Live 仍受 arb_switches.live_trading_enabled 硬关。",
    ),
    # [2026-09-09 F69] 做市（影子期）纳入统一模拟账户。
    # 用户决策：影子期不应是独立账户，而应作为**统一账户里的一条策略配置**，
    # 资金/风控/盈亏与 S3/S8/SDN 同账管理，账户层面看到整体交易与各策略配合。
    # 与其它策略的差异：本策略的成交来自 `lane_ledger`（做市驱动器），
    # 通过 `record_paper_leg_fill(strategy_type="MM")` 汇入账户总账。
    "MM": StrategyRuntimeSpec(
        strategy_id="MM",
        name="做市 · Asterdex（影子期）",
        category="points_arb",
        execution_mode="maker_roundtrip",
        required_exchanges=("asterdex",),
        min_equity_usd=1_000.0,
        paper_auto_executable=True,
        requires_trader_profile=False,
        requires_ai_signal=False,
        requires_funding_signal=False,
        direction_rule="inventory_neutral",
        hold_model="intraday_seconds",
        ai_decision_mode="none",
        coordination_group="delta_neutral",
        summary="Aster maker 0% 下双边挂宽 8bp 做市；库存偏斜 + 超时平仓；"
                "成交与盈亏汇入统一模拟账户（strategy_type=MM）。",
        not_ready_reason="需 Asterdex 盘口/成交采集器在线（数据年龄 ≤180s），"
                         "且 maker 费率 ≤0.5bp；否则驱动器拒绝报价。",
    ),
}


def get_runtime_spec(strategy_id: str) -> Optional[StrategyRuntimeSpec]:
    return STRATEGY_RUNTIME.get((strategy_id or "").upper())


def is_paper_auto_executable(strategy_id: str) -> bool:
    spec = get_runtime_spec(strategy_id)
    return bool(spec and spec.paper_auto_executable)


def runtime_spec_to_dict(strategy_id: str) -> Optional[Dict[str, Any]]:
    spec = get_runtime_spec(strategy_id)
    if not spec:
        return None
    row = asdict(spec)
    try:
        from backend.services.rebate_arb.qaa_strategy_constants import (
            AI_DECISION_MODE,
            COORDINATION_GROUPS,
            MACRO_FILTER_REQUIRED,
            QAA_AGENT_CHAINS,
        )

        sid = spec.strategy_id
        row["ai_decision_mode"] = AI_DECISION_MODE.get(sid, row.get("ai_decision_mode") or "none")
        row["coordination_group"] = COORDINATION_GROUPS.get(sid, row.get("coordination_group") or "")
        row["macro_filter_required"] = sid in MACRO_FILTER_REQUIRED
        row["qaa_agent_chain"] = QAA_AGENT_CHAINS.get(sid, [])
    except Exception:
        pass
    return row


def list_runtime_specs(strategy_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    ids = strategy_ids or list(STRATEGY_RUNTIME.keys())
    out: List[Dict[str, Any]] = []
    for sid in ids:
        row = runtime_spec_to_dict(sid)
        if row:
            out.append(row)
    return out


def check_ai_signal_available(symbol: str = "ETH", direction: str = "neutral") -> Dict[str, Any]:
    """S8 开单前预检：信号必须可用且非 danger；含 macro 逆势检查。"""
    try:
        from backend.services.rebate_arb.strategies.s8_asterdex_rh import S8AsterdexRhStrategy

        sig = S8AsterdexRhStrategy().query_ai_signal(symbol)
        ok = (
            bool(sig)
            and sig.get("available", True) is not False
            and sig.get("risk_level") != "danger"
        )
        macro = {"passed": True, "action": "allow"}
        if ok:
            try:
                from backend.services.rebate_arb.macro_direction_filter import evaluate_macro_filter

                ai_dir = sig.get("direction", direction)
                macro = evaluate_macro_filter(symbol, ai_dir)
                if macro.get("action") == "skip":
                    ok = False
            except Exception:
                pass
        return {"ok": ok, "signal": sig, "macro_filter": macro}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "signal": None}
