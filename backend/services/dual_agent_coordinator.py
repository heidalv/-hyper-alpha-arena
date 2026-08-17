"""DualAgentCoordinator — DirectionAgent + TradeRiskAgent 主决策协调器。

模拟盘默认 primary：直接由 Direction/Risk 产出决策，不经 shadow 对比。
仓位仍由下游 PositionSizingAgent 统一计算。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


EXIT_ACTIONS = {"reduce", "close"}
ENTRY_ACTIONS = {"buy", "sell", "pyramid", "dca"}


class DualAgentCoordinator:
    def coordinate(
        self,
        *,
        master_controller: Any,
        reports: Dict[str, Any],
        symbols: List[str],
        mode: str = "running",
        portfolio: Optional[Dict[str, Any]] = None,
        market_envs: Optional[Dict[str, Any]] = None,
        strategies: Optional[List[Dict[str, Any]]] = None,
        db=None,
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        from backend.config.settings import DUAL_AGENT_MODE

        dual_mode = (DUAL_AGENT_MODE or "off").lower()
        if dual_mode not in ("shadow", "advisory", "primary"):
            return master_controller.synthesize(
                reports=reports,
                symbols=symbols,
                mode=mode,
                portfolio=portfolio,
                market_envs=market_envs,
                strategies=strategies,
                db=db,
                account_id=account_id,
            )

        master_result = None
        if dual_mode in ("shadow", "advisory"):
            master_result = master_controller.synthesize(
                reports=reports,
                symbols=symbols,
                mode=mode,
                portfolio=portfolio,
                market_envs=market_envs,
                strategies=strategies,
                db=db,
                account_id=account_id,
            )

        self._db = db
        try:
            dual_result = self._run_dual(
                reports=reports,
                symbols=symbols,
                portfolio=portfolio or {},
                market_envs=market_envs or {},
                strategies=strategies,
                account_id=account_id,
            )
        except Exception as dual_err:
            logger.warning("[DualAgent] failed, fallback to MasterController: %s", dual_err, exc_info=True)
            if master_result:
                return master_result
            return master_controller.synthesize(
                reports=reports,
                symbols=symbols,
                mode=mode,
                portfolio=portfolio,
                market_envs=market_envs,
                strategies=strategies,
                db=db,
                account_id=account_id,
            )

        if dual_mode == "shadow":
            self._log_shadow_diff(master_result or {}, dual_result)
            out = dict(master_result or {})
            out["_dual_agent_shadow"] = dual_result
            return out

        if dual_mode == "advisory":
            return self._merge_advisory(master_result or {}, dual_result, portfolio or {})

        # primary
        if dual_result and dual_result.get("decisions"):
            return dual_result
        return master_result or {"overall_assessment": "DualAgent无决策", "risk_level": "medium", "decisions": []}

    def _run_dual(
        self,
        *,
        reports: Dict[str, Any],
        symbols: List[str],
        portfolio: Dict[str, Any],
        market_envs: Dict[str, Any],
        strategies: Optional[List[Dict[str, Any]]],
        account_id: Optional[int],
    ) -> Dict[str, Any]:
        # [2026-08-17 删除] direction_agent / trade_risk_agent 已移除（DUAL_AGENT_MODE
        # 默认 off，三层专家 + MasterController 已覆盖该职责）。保留方法骨架返回空，
        # 由调用方的 fallback 分支接管。
        return {}

    def _merge_primary(self, direction: Dict[str, Any], risk: Dict[str, Any], positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        pos_symbols = {str(p.get("symbol", "")).upper() for p in positions or []}
        dir_map = {str(d.get("symbol", "")).upper(): d for d in direction.get("decisions", [])}
        risk_map = {str(d.get("symbol", "")).upper(): d for d in risk.get("decisions", [])}
        symbols = list(dict.fromkeys([*dir_map.keys(), *risk_map.keys()]))
        decisions = []
        for sym in symbols:
            d = dict(dir_map.get(sym, {"symbol": sym, "action": "hold", "confidence": 0, "reasoning": "无方向"}))
            r = dict(risk_map.get(sym, {}))
            if sym in pos_symbols or str(r.get("action", "")).lower() in EXIT_ACTIONS:
                d.update(r)
            else:
                # 新开仓必须通过 Risk 审核；Risk 若改成 hold，则按 hold。
                if str(r.get("action", "")).lower() == "hold" and str(d.get("action", "")).lower() in ENTRY_ACTIONS:
                    d.update({"action": "hold", "reasoning": r.get("reasoning", "Risk拒绝开仓")})
                else:
                    d.update({k: v for k, v in r.items() if k in ("size_multiplier", "leverage_cap") and v is not None})
            d["symbol"] = sym
            decisions.append(self._master_compatible_decision(d))
        return {
            "overall_assessment": direction.get("market_assessment") or "DualAgent primary",
            "risk_level": risk.get("risk_level", "medium"),
            "decisions": decisions,
            "_decision_source": "dual_agent_primary",
        }

    def _merge_advisory(self, master_result: Dict[str, Any], dual_result: Dict[str, Any], portfolio: Dict[str, Any]) -> Dict[str, Any]:
        pos_symbols = {str(p.get("symbol", "")).upper() for p in (portfolio.get("positions") or [])}
        dual_map = {str(d.get("symbol", "")).upper(): d for d in dual_result.get("decisions", [])}
        merged = dict(master_result or {})
        decisions = []
        for raw in (master_result or {}).get("decisions", []):
            d = dict(raw)
            sym = str(d.get("symbol", "")).upper()
            dual = dual_map.get(sym)
            if sym in pos_symbols and dual and str(dual.get("action", "")).lower() in EXIT_ACTIONS | {"hold"}:
                action = str(dual.get("action", "hold")).lower()
                if action in EXIT_ACTIONS:
                    d.update(dual)
            decisions.append(self._master_compatible_decision(d))
        merged["decisions"] = decisions
        merged["_dual_agent_advisory"] = dual_result
        return merged

    def _master_compatible_decision(self, d: Dict[str, Any]) -> Dict[str, Any]:
        action = str(d.get("action") or "hold").lower()
        if action not in {"hold", "buy", "sell", "close", "reduce", "pyramid", "dca"}:
            action = "hold"
        return {
            "symbol": d.get("symbol", ""),
            "action": action,
            "confidence": int(max(0, min(100, float(d.get("confidence", 0) or 0)))),
            "reasoning": str(d.get("reasoning") or "")[:300],
            "reasoning_content": str(d.get("_reasoning_content") or "")[:6000],  # [fix] 透传深度思维链
            "trade_nature": d.get("trade_nature") or "swing",
            "expected_hold_hours": d.get("expected_hold_hours"),
            "stop_loss_pct": d.get("stop_loss_pct"),
            "take_profit_pct": d.get("take_profit_pct"),
            "risk_reward_ratio": d.get("risk_reward_ratio"),
            "leverage": d.get("leverage"),
            "position_pct": d.get("position_pct"),
            "target_portion_of_balance": d.get("target_portion_of_balance"),
            "_sizing_notional_usd": d.get("_sizing_notional_usd"),
            "_sizing_margin_usd": d.get("_sizing_margin_usd"),
            "_sizing_source": d.get("_sizing_source"),
            "_respect_sizing_plan": d.get("_respect_sizing_plan"),
            "adjust_tp": d.get("adjust_tp"),
            "adjust_sl": d.get("adjust_sl"),
            "partial_close_pct": d.get("partial_close_pct"),
            "extend_hold_hours": d.get("extend_hold_hours") or 0,
            "size_multiplier": d.get("size_multiplier"),
            "leverage_cap": d.get("leverage_cap"),
        }

    def _log_shadow_diff(self, master_result: Dict[str, Any], dual_result: Dict[str, Any]) -> None:
        try:
            master_map = {str(d.get("symbol", "")).upper(): d.get("action") for d in master_result.get("decisions", [])}
            dual_map = {str(d.get("symbol", "")).upper(): d.get("action") for d in dual_result.get("decisions", [])}
            diffs = [
                f"{sym}: master={master_map.get(sym)} dual={dual_map.get(sym)}"
                for sym in sorted(set(master_map) | set(dual_map))
                if master_map.get(sym) != dual_map.get(sym)
            ]
            if diffs:
                logger.info("[DualAgent][shadow] decision diffs: %s", "; ".join(diffs[:20]))
        except Exception:
            pass


dual_agent_coordinator = DualAgentCoordinator()
