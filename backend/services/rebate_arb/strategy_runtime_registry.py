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


# M4 注销（2026-06）：S1/S5 已下线，从运行时注册表移除。
# - S1 Maker返佣对冲：负 EV，与 S6 重复且更差，且 Stage 6 惩罚对冲刷分
# - S5 资金费率+积分：数据结构假设错误，与 V3 资金费套利重复
# 历史仓位/数据解读请看 strategies/s1_maker_hedge.py、s5_funding_points.py（文件保留）。
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
    "S6": StrategyRuntimeSpec(
        strategy_id="S6",
        name="跨所费率差",
        category="trade_points",
        execution_mode="hedge",
        required_exchanges=("asterdex", "binance"),
        min_equity_usd=200.0,
        paper_auto_executable=True,
        requires_trader_profile=True,
        requires_ai_signal=False,
        requires_funding_signal=False,
        direction_rule="fixed_hedge",
        hold_model="hedge_until_close",
        summary="与 S1 类似双所对冲，赚费率差与积分；腿方向固定，不需 AI。",
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
