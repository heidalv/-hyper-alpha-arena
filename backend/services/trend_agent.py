"""TrendAgent — 趋势交易智能体（2026-06-18 新增）。

专门负责 trend_follow / position 仓位的完整生命周期深度思考：
1. 开仓方向分析（4h/1d 级趋势强度评分）
2. 持仓定期复查（平仓/减仓/继续持有判断，90min 节流）
3. 补仓时机判断（回调到支撑 + 趋势仍成立）
4. 止盈止损优化（根据趋势强度动态调整 trailing/staged TP）

设计原则：
- 只关注 4h-1d 级趋势，不看短期噪声（5m/15m）
- 趋势单核心是"让利润奔跑"——除非趋势明确反转，否则倾向持有
- 平仓建议是"软"的，不覆盖硬 SL/爆仓（PositionExitOrchestrator 硬执行保留）
- 复用 DirectionAgent 的 LLM 调用框架（call_llm_api_sync + JSON 响应 + fallback）
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional
# [fix] reasoning 模型把深度推理放在 message.reasoning_content，早期 _call_llm 只读
# content 导致整条思维链被丢弃 → 中长线决策"看起来很浅"。统一用公共 helper 捞回。
from backend.services.llm_reasoning_helper import extract_reasoning_content_safe

logger = logging.getLogger(__name__)

# 趋势持仓复查间隔（秒），控制 LLM 调用成本
TREND_REVIEW_INTERVAL_SEC = int(os.getenv("TREND_REVIEW_INTERVAL_SEC", "5400"))  # 90 分钟
# 每 tick 最多复查的趋势持仓数
TREND_REVIEW_MAX_PER_TICK = int(os.getenv("TREND_REVIEW_MAX_PER_TICK", "2"))
# 趋势方向分析的最低评分（低于此值 veto 开仓；纸盘见 settings.get_trend_min_score_to_open）
TREND_MIN_SCORE_TO_OPEN = int(os.getenv("TREND_MIN_SCORE_TO_OPEN", "50"))


def resolve_trend_min_score(trading_mode: Optional[str] = None) -> int:
    try:
        from backend.config.settings import get_trend_min_score_to_open
        return get_trend_min_score_to_open(trading_mode or "paper")
    except Exception:
        return TREND_MIN_SCORE_TO_OPEN

TREND_NATURES = ("trend_follow", "position")


def derive_trend_side(symbol: str, market_envs: Optional[Dict[str, Any]] = None) -> str:
    """从编排器 long_bias / 宏观周期心智推导 TrendAgent side 锚点。"""
    _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
    _orch = _ms.get("orchestrator", {}) if isinstance(_ms, dict) else {}
    dc = (_orch.get("macro_direction_constraint") or "").lower()
    if dc == "long_only":
        return "long"
    if dc == "short_only":
        return "short"
    _lb = (_orch.get("long_bias") or "neutral").lower()
    if _lb == "bearish":
        return "short"
    if _lb == "bullish":
        return "long"
    _mb = (_orch.get("mid_bias") or "neutral").lower()
    if _mb == "bearish":
        return "short"
    if _mb == "bullish":
        return "long"
    _sb = (_orch.get("short_bias") or "neutral").lower()
    if _sb == "bearish":
        return "short"
    if _sb == "bullish":
        return "long"
    try:
        from backend.services.macro_regime_service import macro_regime_service
        return macro_regime_service.get_state("GLOBAL").side_hint()
    except Exception:
        return "long"


class TrendAgent:
    """趋势交易智能体。单例。"""

    _instance: Optional["TrendAgent"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def is_trend_nature(self, nature: str) -> bool:
        return (nature or "").lower() in TREND_NATURES

    # ──────────────────────────────────────────────────────────────
    # 职责 1：趋势方向分析（开仓时）
    # ──────────────────────────────────────────────────────────────

    def analyze_direction(
        self,
        *,
        symbol: str,
        side: str,
        reports: Dict[str, Any],
        market_envs: Dict[str, Any],
        account_id: Optional[int] = None,
        portfolio: Optional[Dict[str, Any]] = None,
        db=None,
        trading_mode: Optional[str] = None,
        light_context: bool = False,
    ) -> Dict[str, Any]:
        """分析趋势方向强度，决定是否值得开趋势仓。

        Returns:
            {
                "score": 0-100,          # 趋势强度评分
                "direction": "long"/"short"/"neutral",
                "should_open": bool,      # 是否值得开（score >= TREND_MIN_SCORE_TO_OPEN）
                "suggested_sl_pct": float,# 建议止损距离
                "reasoning": str,
            }
        """
        from backend.services.agent_evidence_builder import (
            build_trend_evidence,
            format_evidence_for_prompt,
        )

        # [2026-08-11 修复] rollback 必须放在 prompt 构建之前：
        # _build_direction_prompt 内部会调 build_trend_deep_context → onchain 网络请求，
        # 若事务仍开着，10-20s 的网络阻塞就会触发 LeakGuard。
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass

        facts = build_trend_evidence(symbol, market_envs or {}, db=db)
        evidence_block = format_evidence_for_prompt(facts)
        min_score = resolve_trend_min_score(trading_mode)
        prompt = self._build_direction_prompt(
            symbol, side, reports, market_envs, portfolio=portfolio, db=db,
            evidence_block=evidence_block, min_score=min_score, account_id=account_id,
            light_context=light_context,
        )
        # [2026-08-06 事务卫生] LLM 长调用前释放只读事务：build_trend_evidence /
        # _build_direction_prompt 的查询已打开事务，若直接进入 60-90s LLM，连接将
        # idle-in-transaction（LeakGuard 每 2 分钟报挂起、>120s 强杀中断 LLM）。
        # 此处仅回滚只读事务，无数据丢失；LLM 后的写入会重新开启事务。
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        result = self._call_llm(prompt, account_id=account_id, caller="TrendAgent:direction")
        if result:
            normalized = self._normalize_direction(
                result, symbol, side, min_score=min_score, market_envs=market_envs,
                trading_mode=trading_mode,
            )
            return self._apply_fact_guard_direction(normalized, result, facts, min_score=min_score)
        return self._fallback_direction(symbol, side, market_envs, min_score=min_score)

    def _apply_fact_guard_direction(
        self, normalized: Dict[str, Any], llm_result: Dict, facts: list,
        min_score: int = TREND_MIN_SCORE_TO_OPEN,
    ) -> Dict[str, Any]:
        from backend.services.agent_fact_guard import (
            build_evidence_audit,
            verify_agent_decision,
        )

        cited = llm_result.get("cited_fact_ids") or []
        if isinstance(cited, str):
            cited = [cited]
        action = "buy" if normalized.get("direction") == "long" else (
            "sell" if normalized.get("direction") == "short" else "hold"
        )
        if not normalized.get("should_open"):
            action = "hold"
        try:
            from backend.config.settings import MIDLONG_PAPER_PROBE_STRICT, PAPER_FAST_TRIAL
            _tr_strict = bool(MIDLONG_PAPER_PROBE_STRICT) and bool(PAPER_FAST_TRIAL)
        except Exception:
            _tr_strict = True
        # S2-3 中长线 paper FactGuard 切 enforce（strict 时强制拦截无据/幻觉决策）
        fg = verify_agent_decision(
            action=action,
            confidence=normalized.get("score", 0),
            reasoning=normalized.get("reasoning", ""),
            cited_fact_ids=cited,
            facts=facts,
            agent_type="trend",
            min_confidence=min_score,
            force_enforce=_tr_strict,
        )
        normalized["cited_fact_ids"] = list(cited)
        normalized["evidence_audit"] = build_evidence_audit(facts, cited, fg)
        if fg.mode == "enforce":
            if not fg.allow:
                normalized["should_open"] = False
                normalized["reasoning"] = (
                    f"[FactGuard] {','.join(fg.violations)} | {normalized.get('reasoning', '')}"
                )[:300]
            elif fg.adjusted_confidence is not None:
                normalized["score"] = fg.adjusted_confidence
                # P1-6：FactGuard 调分重算 should_open 时，不应绕过 exit_plan 硬校验——
                # 若已被判定缺 tp_stages/invalidation 而拒单，这里不应重新放行。
                _exit_plan_ok = bool(normalized.get("hold_reason") != "exit_plan_missing_reject")
                normalized["should_open"] = _exit_plan_ok and (
                    normalized["score"] >= min_score
                    and normalized.get("direction") in ("long", "short")
                )
                # MidLong v2：保留软通道（FactGuard 调分后仍可能落在 floor−5）
                if (
                    not normalized["should_open"]
                    and _exit_plan_ok
                    and normalized.get("soft_open")
                    and normalized.get("raw_should_open") is True
                    and normalized.get("direction") in ("long", "short")
                    and int(normalized.get("score") or 0) >= max(0, int(min_score) - 5)
                ):
                    normalized["should_open"] = True
                    normalized["size_hint_mult"] = 0.6
                elif not normalized.get("should_open"):
                    normalized["soft_open"] = False
                    normalized["size_hint_mult"] = 1.0
        if llm_result.get("lifecycle"):
            facts_map = {f.id: f for f in facts}
            if "lifecycle_stage" in facts_map:
                facts_map["lifecycle_stage"].value = llm_result.get("lifecycle")
                facts_map["lifecycle_stage"].available = True
            if "scenario_a_trigger" in facts_map and llm_result.get("scenario_a"):
                facts_map["scenario_a_trigger"].value = llm_result.get("scenario_a")
                facts_map["scenario_a_trigger"].available = True
        return normalized

    def _build_direction_prompt(
        self, symbol, side, reports, market_envs, portfolio=None, db=None,
        evidence_block="", min_score: int = TREND_MIN_SCORE_TO_OPEN, account_id=None,
        light_context: bool = False,
    ) -> str:
        from backend.services.analyst_report_builder import compact_report_text

        _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
        _orch = _ms.get("orchestrator", {}) if isinstance(_ms, dict) else {}
        context = compact_report_text(
            reports, market_envs=market_envs, symbols=[symbol],
            portfolio=portfolio,
        )

        _agent_constraints = ""
        if db is not None:
            try:
                from backend.services.decision_feedback_service import decision_feedback_service
                _agent_constraints = decision_feedback_service.get_agent_constraints(
                    db, agent_type="trend", account_id=account_id,
                )
            except Exception:
                pass

        # 宏观周期心智（慢变量锚点）
        _macro_block = ""
        try:
            from backend.services.macro_regime_service import macro_regime_service
            _macro_block = macro_regime_service.get_state("GLOBAL").prompt_block()
        except Exception:
            pass

        _side_hint = (side or "long").lower()
        if _side_hint not in ("long", "short"):
            _side_hint = derive_trend_side(symbol, market_envs)

        _regime = "unknown"
        try:
            from backend.services.decision_core.regime_agent import classify_regime
            _regime = classify_regime(_ms if isinstance(_ms, dict) else {}).regime
        except Exception:
            pass

        # MidLong v2 Phase4：概念信念 / 失败 Intent 注入
        try:
            from backend.services.mlto.midlong_belief_loop import format_beliefs_for_prompt
            _belief_block = format_beliefs_for_prompt(
                symbol=symbol, regime=_regime, limit=4,
            )
            if _belief_block:
                _agent_constraints = (
                    ((_agent_constraints or "") + "\n" + _belief_block).strip()
                )
        except Exception:
            pass

        _long_opens_week = 0
        if db is not None and account_id:
            try:
                from backend.services.decision_core.fee_context import count_nature_opens
                _long_opens_week = count_nature_opens(
                    db, int(account_id), nature="trend_follow", since_days=7,
                )
            except Exception:
                pass

        # 长线专属深度上下文（比中线更深：宏观价位+趋势生命周期+链上数据）
        _deep_ctx = ""
        if not light_context:
            try:
                from backend.services.agent_deep_context import build_trend_deep_context
                _deep_ctx = build_trend_deep_context(symbol, db=db)
            except Exception:
                pass
        else:
            _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
            _i4 = (_ms.get("indicators_4h") or {}) if isinstance(_ms, dict) else {}
            _i1d = (_ms.get("indicators_1d") or {}) if isinstance(_ms, dict) else {}
            _mtf = (_ms.get("mtf_resonance") or {}) if isinstance(_ms, dict) else {}
            _deep_ctx = (
                f"[轻量模式] 4h EMA={_i4.get('ema_trend')} RSI={_i4.get('rsi')} | "
                f"1d EMA={_i1d.get('ema_trend')} RSI={_i1d.get('rsi')} | "
                f"MTF={_mtf.get('detail')}"
            )

        # S2-1 量化简报进脑：多周期一致性/结构位/数据完整度前置注入。
        try:
            from backend.services.decision_core.quant_brief import build_quant_brief
            _qb = build_quant_brief(symbol, market_envs, nature="trend_follow")
            if _qb:
                _deep_ctx = _qb + "\n\n" + (_deep_ctx or "")
        except Exception:
            pass

        fallback = self._build_direction_prompt_inline(
            symbol, context, _deep_ctx, _orch, _macro_block, _side_hint, evidence_block,
            min_score=min_score, agent_constraints=_agent_constraints,
        )
        # ── S1-8 新增：量化特征表 + memory_block + ATR + recent_loss_block ──
        # （与 swing_agent._build_prompt 同构,对应 04 综合方案 §2.3.4/§2.3.8）
        _quant_feature_table = ""
        _memory_block = ""
        _atr_block = ""
        _recent_loss_block = ""
        _cooldown_active = False
        try:
            from backend.services.agent_quant_feature_table import (
                render_quant_feature_table,
                render_memory_block,
                render_atr_block,
                render_recent_loss_block,
            )
            _quant_feature_table = render_quant_feature_table(
                symbol, market_envs or {}, db=db, account_id=account_id, nature="trend_follow",
            )
            if db is not None and account_id:
                _memory_block = render_memory_block(db, symbol, "trend", account_id, limit=5)
            _atr_block = render_atr_block(symbol, market_envs or {})
            if db is not None and account_id:
                _cd_info = render_recent_loss_block(
                    db, symbol, _side_hint or "long", account_id, window_hours=24,
                    nature="trend_follow",
                )
                _recent_loss_block = _cd_info.get("block_text", "")
                _cooldown_active = bool(_cd_info.get("cooldown_active", False))
        except Exception as _qft_err:
            logger.debug("[TrendAgent] %s 量化特征表注入失败: %s", symbol, _qft_err)

        try:
            from backend.services.agent_prompt_service import render_agent_task
            _prompt = render_agent_task(
                "task_trend_agent_direction",
                {
                    "symbol": symbol,
                    # [阶段3c] side_hint / min_score 不再注入：prompt 已不再引用这些变量
                    # （方向由 LLM 自主判断，置信度由 LLM 自评）。_side_hint 仍内部用于冷却检测。
                    "macro_block": _macro_block,
                    "deep_context": _deep_ctx,
                    "compact_report": context,
                    "orchestrator": _orch,
                    "evidence_block": evidence_block or "",
                    "agent_constraints": _agent_constraints or "",
                    "regime": _regime,
                    "long_opens_week": _long_opens_week,
                    # S1-8 新增变量
                    "quant_feature_table": _quant_feature_table,
                    "memory_block": _memory_block,
                    "atr_block": _atr_block,
                    "recent_loss_block": _recent_loss_block,
                    "cooldown_active": str(_cooldown_active).lower(),
                },
                consumer="TrendAgent:direction",
                fallback_text=fallback,
            )
            # S1-8 落盘 redacted prompt
            try:
                from backend.services.swing_agent import _archive_prompt
                _archive_prompt(_prompt, "trend_agent", symbol)
            except Exception:
                pass
            return _prompt
        except Exception:
            return fallback

    def _build_direction_prompt_inline(
        self, symbol, context, _deep_ctx, _orch, _macro_block, _side_hint, evidence_block,
        min_score: int = TREND_MIN_SCORE_TO_OPEN, agent_constraints: str = "",
    ) -> str:
        _constraints_block = ""
        if agent_constraints:
            _constraints_block = f"\n## 历史反馈约束（参考，结合本次判断）\n{agent_constraints}\n"
        # [阶段3c] 内联 fallback 对齐 5 段结构（简化版）：删 side_hint/min_score/规则配方/闸门威胁，
        # 保留数据上下文 + 自由推理 + 5 风险底线。_side_hint 仍作为签名参数保留（冷却检测内部使用）。
        return f"""
你是 TrendAgent，专注 4h-1d-1w（真周线）级别的趋势战略分析师，标的：{symbol}。
你的方向判断（多/空/中性）完全基于数据自主做出——顺势、逆势、中性都可以，只要证据支持。

任务：深度评估 {symbol} 的大级别趋势，判断是否值得开趋势仓，并给出未来 1-2 周的走势预测。

## 宏观周期背景（参考，非方向指令）
{_macro_block}

## 自由推理引导（建议覆盖，但不限于；无门槛、无配方）
- 宏观结构：当前价格在 90 天/52 周范围的什么位置？
- 趋势方向与生命周期：trend_4h/1d/1w 的 EMA 排列是否一致？加速还是衰竭？
- 多周期共振：4h/1d/1w 矛盾时以哪个为主？由你判断。
- 猎杀止损：大级别 swing high/low 是否刚被击穿回收？
- 成交量确认：vol_ratio 放量/缩量的含义。
- 衍生品深度：funding/OI/清算簇/CVD 是否支持你的方向假设？
- 市场状态（regime）：当前 regime 的置信度如何？由你判断是否适合趋势仓。
- 宏观资金流向：恐贪指数、鲸鱼买卖、链上流入流出。
- 历史经验：看"逐笔战绩"和"亏损教训"，类似结构下过去表现如何？
- 择时：即便大方向已定，现在是否是好时机？
- 反向假设检验：如果你错了，最可能的原因是什么？什么证据会改变你的判断？
- 未来 1-2 周走势预测：场景 A（基准）/ B（次要）/ C（尾部风险）。

## 风险底线（不可妥协的安全网，仅此 5 条）
1. 单笔最大风险金额 ≤ 账户权益的 1.5%（防破产）。
2. 杠杆遵守系统给定 tier cap（你无需输出杠杆）。
3. 只分析固定交易对 {symbol}。
4. 关键数据缺失 → 输出中性/hold 并说明缺什么。
5. 同币种同方向持仓达上限或处于冷却 → 不得再同向开仓。
除以上 5 条外，方向/择时/置信度/RR/SL/TP/是否开仓，均由你自主决定。

## 深度市场数据
{_deep_ctx}

{evidence_block}
{_constraints_block}
## 基础上下文（持仓/编排器/分析师报告摘要）
{context}

编排器评估：{_orch}

只返回 JSON：
{{
  "trend_score": 0,
  "trend_direction": "long/short/neutral",
  "should_open_trend": true,
  "suggested_sl_pct": 0.08,
  "lifecycle": "启动/加速/衰竭/反转/震荡",
  "scenario_a": "概率最高的走势预测（含触发条件）",
  "scenario_b": "备选走势预测",
  "scenario_c": "尾部风险走势",
  "cited_fact_ids": ["trend_4h", "trend_1d", "trend_1w", "trend_4h_1d_resonance"],
  "reasoning": "完整趋势分析逻辑（含宏观结构+趋势生命周期+多周期共振+衍生品+择时+预测，最多300字）"
}}
"""

    def _normalize_direction(
        self, result: Dict, symbol: str, side: str,
        min_score: int = TREND_MIN_SCORE_TO_OPEN,
        market_envs: Optional[Dict] = None,
        trading_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        score = int(result.get("trend_score", 0) or 0)
        multi_tf = result.get("multi_tf_aligned")
        direction = (result.get("trend_direction") or "neutral").lower()
        should = result.get("should_open_trend", score >= min_score)
        raw_should_open = bool(should)
        try:
            from backend.config.settings import PAPER_FAST_TRIAL
            _paper = (
                (trading_mode or "").strip().lower() == "paper"
                or bool(PAPER_FAST_TRIAL)
            )
        except Exception:
            _paper = True
        soft_open = False
        _ranging_pad = 5 if _paper else 12
        _mtf_pad = 5 if _paper else 8
        # 确定性多周期共振与 LLM 分融合（Phase3）
        # 2026-07-06 整改：只在**确实拿到 4h/1d 指标数据**时才融合。此前无论
        # market_envs 是否为空都会 blend，空数据时 compute_mtf_resonance 返回
        # 硬编码的 neutral=35 分，把一个强 LLM 信号（如 85）稀释到 68——等于
        # "没有 MTF 数据"被当成"MTF 判定为中性"来惩罚 LLM，方向是错的。
        # 有真实指标才 blend，无数据则原样透传 LLM 分。
        try:
            from backend.services.decision_core.mtf_resonance import compute_mtf_resonance
            _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
            _ms = _ms if isinstance(_ms, dict) else {}
            _has_mtf_data = isinstance(_ms.get("indicators_4h"), dict) or isinstance(_ms.get("indicators_1d"), dict)
            if _has_mtf_data:
                mtf = compute_mtf_resonance(_ms)
                score = mtf.blend_with_llm(score, llm_weight=0.65)
                if multi_tf is None:
                    multi_tf = mtf.aligned
                if mtf.aligned and direction == "neutral" and mtf.direction != "neutral":
                    direction = mtf.direction
        except Exception:
            pass
        sl = float(result.get("suggested_sl_pct", 0.08) or 0.08)
        # [fix] 放宽 reasoning 截断 300→1000；同时捞回 reasoning 模型完整思维链。
        reasoning = (result.get("reasoning") or "")[:1000]
        reasoning_content = (result.get("_reasoning_content") or "")[:6000]
        # 长线预测字段（新增 2026-06-26）
        lifecycle = (result.get("lifecycle") or "")
        # S1-11 修复：scenario 截断从 150 放宽到 500/300（04 综合方案 S7）
        scenario_a = (result.get("scenario_a") or "")[:500]
        scenario_b = (result.get("scenario_b") or "")[:300]
        scenario_c = (result.get("scenario_c") or "")[:300]

        # ── S1-11 新增：解析 v3 schema 字段 ──
        # tp_sl_proposal / exit_plan（兼容两种命名）
        tp_sl_proposal = result.get("tp_sl_proposal") or result.get("exit_plan") or {}
        if isinstance(tp_sl_proposal, dict) and tp_sl_proposal:
            # 从 tp_sl_proposal 覆盖 sl（若 LLM 给了更精确的值）
            _prop_sl = float(tp_sl_proposal.get("sl_pct") or 0)
            if _prop_sl > 0:
                sl = _prop_sl

        # scenarios 支持对象格式 {a/b/c: {prob, trigger, target_pct, hold_days}}
        scenarios_raw = result.get("scenarios") or {}
        if not isinstance(scenarios_raw, dict):
            scenarios_raw = {}
        scenarios = scenarios_raw
        # 若 scenarios 为空但 scenario_a/b/c 有值，从字符串构建对象（降级兼容）
        if not scenarios and (scenario_a or scenario_b or scenario_c):
            scenarios = {
                "a": {"trigger": scenario_a, "prob": 0},
                "b": {"trigger": scenario_b, "prob": 0},
                "c": {"trigger": scenario_c, "prob": 0},
            }

        invalidation = result.get("invalidation") or {}
        if not isinstance(invalidation, dict):
            invalidation = {}
        if not invalidation:
            _inv_cond = result.get("invalidation_condition") or ""
            if _inv_cond:
                invalidation = {"condition": str(_inv_cond)[:300]}
        # [补齐修复 2026-07-19] 与 swing_agent 同一根因：v3 schema 把
        # invalidation_condition 嵌在 exit_plan/tp_sl_proposal 内部，此前只读顶层字段。
        if not invalidation and isinstance(tp_sl_proposal, dict):
            _inv_cond2 = tp_sl_proposal.get("invalidation_condition") or ""
            if _inv_cond2:
                invalidation = {"condition": str(_inv_cond2)[:300]}

        self_check = result.get("self_check") or {}
        if not isinstance(self_check, dict):
            self_check = {}
        # self_check.confidence_adjustment 应用到 score
        _score_adj = 0
        try:
            _score_adj = int(self_check.get("confidence_adjustment") or 0)
        except Exception:
            pass
        if _score_adj != 0:
            score = max(0, min(100, score + _score_adj))

        # expected_hold_days → 转 expected_hold_hours
        expected_hold_hours = 0.0
        try:
            _hold_days = float(result.get("expected_hold_days") or 0)
            if _hold_days > 0:
                expected_hold_hours = _hold_days * 24
            else:
                expected_hold_hours = float(result.get("expected_hold_hours") or 0)
        except Exception:
            pass

        lifecycle_evidence = (result.get("lifecycle_evidence") or "")[:400]
        conviction_level = (result.get("conviction_level") or "").lower()
        # 安全网：sl 不能太小（趋势仓至少 4%）
        sl = max(sl, 0.04)

        # 币圈尾部风险调整（入场前）—— 防插针打损 + 防余震市重仓。
        # 这是币圈独有的风险：永续合约的闪崩插针和清算级联余震远比传统市场频繁。
        _crypto_note = ""
        try:
            from backend.services.crypto_alpha_signals import crypto_alpha
            _bundle = crypto_alpha.get_bundle(symbol)

            # 1. 清算簇 severity=high 且与方向反向 → 趋势可能被级联清算打断，降级
            _lm = _bundle.liquidation_magnet
            if _lm.available and _lm.severity == "high" and _lm.direction != "neutral" and direction != "neutral":
                _opp = "short" if direction == "long" else "long"
                if _lm.direction == _opp:
                    # 反向 high 清算簇：趋势单持仓时间长，更危险 → 直接 veto
                    should = False
                    _crypto_note = f"[尾部风险] {_lm.note}，{direction}趋势仓与high级清算磁吸反向，veto开仓"

            # 2. funding-OI 背离与趋势方向冲突 → 降分（建仓信号不支持趋势）
            _foid = _bundle.funding_oi_divergence
            if _foid.available and _foid.strength > 0.4 and _foid.direction != "neutral" and direction != "neutral":
                _opp = "short" if direction == "long" else "long"
                if _foid.direction == _opp:
                    score = max(0, score - 15)
                    _crypto_note = (f"{_crypto_note} | " if _crypto_note else "") + \
                        f"[funding-OI背离冲突] {_foid.note}，趋势分-15"
        except Exception:
            pass

        if _crypto_note and not reasoning.endswith(_crypto_note):
            reasoning = f"{reasoning} | {_crypto_note}"
        # Regime 前置：ranging 且无多周期共振 → 默认不开趋势仓
        try:
            from backend.services.decision_core.regime_agent import classify_regime
            _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
            _reg = classify_regime(_ms if isinstance(_ms, dict) else {})
            if _reg.regime == "ranging" and multi_tf is False and score < min_score + _ranging_pad:
                should = False
                if direction != "neutral" and score < (55 if _paper else 65):
                    direction = "neutral"
        except Exception:
            pass
        if multi_tf is False and score < min_score + _mtf_pad:
            should = False
        # Paper 试单：score≥min_score 有方向时允许开（减门；清算 veto 已在上方处理）
        #
        # S0-4 止血修复（R4）：删除 Paper 强制开仓 override。
        # 原逻辑：LLM 应开仓（should_open_trend=true）但 score≥min_score 时强制 override；
        #        更糟糕的是即使 LLM 输出 should_open_trend=false，只要 score≥50 也会被改成 true。
        # 修复：尊重 LLM 的 should_open_trend 决策；Paper 只降低 min_score 门槛，不强制 override。
        hold_reason = ""
        should_open = bool(should) and score >= min_score and direction in ("long", "short")
        # MidLong v2：Paper 软通道 — LLM 明确要开且分数接近地板时允许开（size 由下游缩）
        if (
            not should_open
            and _paper
            and raw_should_open is True
            and direction in ("long", "short")
        ):
            try:
                from backend.config.settings import TREND_TRUST_SHOULD_OPEN_SOFT
                _soft = bool(TREND_TRUST_SHOULD_OPEN_SOFT)
            except Exception:
                _soft = True
            if _soft and score >= max(0, int(min_score) - 5):
                should_open = True
                soft_open = True
                hold_reason = ""
                reasoning = (
                    f"[软通道] LLM should_open=true score={score}≥{min_score}-5，"
                    f"允许缩仓观察(size×0.6) | 原: {reasoning}"
                )[:1000]
        if not should_open:
            if raw_should_open is False and score >= min_score:
                hold_reason = "llm_should_open_false_respected"  # 尊重 LLM 的 false 决策
            elif score < min_score:
                hold_reason = f"score_low({score}<{min_score})"
            elif direction == "neutral":
                hold_reason = "direction_neutral"
            elif multi_tf is False:
                hold_reason = "no_mtf_resonance"
            else:
                hold_reason = "regime_or_crypto"

        # ── P1-6 硬校验（同 swing_agent，04 综合方案 §2.3.4）：should_open 但
        # 完全没有 tp_stages/invalidation → 拒单降级为 hold ──
        if should_open:
            _has_tp_stages = isinstance(tp_sl_proposal, dict) and isinstance(
                tp_sl_proposal.get("tp_stages"), list
            ) and len(tp_sl_proposal.get("tp_stages") or []) > 0
            _has_invalidation = isinstance(invalidation, dict) and bool(invalidation.get("condition"))
            if not _has_tp_stages and not _has_invalidation:
                should_open = False
                hold_reason = "exit_plan_missing_reject"
                reasoning = (
                    "[硬校验拒单] LLM 未提供 tp_stages 分批止盈方案或 invalidation 失效条件"
                    f"（v3 schema 必填项），拒绝开仓降级为 hold | 原: {reasoning}"
                )[:1000]

        # 将预测信息追加到 reasoning 供下游使用
        if lifecycle or scenario_a:
            reasoning = f"{reasoning} | [生命周期]{lifecycle} [主场景]{scenario_a}"

        return {
            "score": score,
            "direction": direction,
            "should_open": should_open,
            "soft_open": soft_open,
            "size_hint_mult": 0.6 if soft_open else 1.0,
            "suggested_sl_pct": sl,
            "reasoning": reasoning,
            "reasoning_content": reasoning_content,
            "lifecycle": lifecycle,
            "scenario_a": scenario_a,
            "scenario_b": scenario_b,
            "scenario_c": scenario_c,
            "hold_reason": hold_reason,
            "raw_should_open": raw_should_open,
            # S1-11 新增字段
            "tp_sl_proposal": tp_sl_proposal,
            "scenarios": scenarios,
            "invalidation": invalidation,
            "self_check": self_check,
            "expected_hold_hours": expected_hold_hours,
            "lifecycle_evidence": lifecycle_evidence,
            "conviction_level": conviction_level,
        }

    def _fallback_direction(
        self, symbol: str, side: str, market_envs,
        min_score: int = TREND_MIN_SCORE_TO_OPEN,
    ) -> Dict[str, Any]:
        """LLM 失败时的规则回退：用编排器长期 bias 做保守判断。

        注意：调用方传 side="long"（不是 "buy"），旧代码检查 side=="buy"/"sell"
        导致 fallback 永远 neutral → LLM 故障时长线完全停摆。
        修复：不依赖 side 参数，直接用编排器 bias 判断方向。
        """
        _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
        _orch = _ms.get("orchestrator", {}) if isinstance(_ms, dict) else {}
        long_bias = (_orch.get("long_bias") or "neutral").lower() if isinstance(_orch, dict) else "neutral"
        long_conf = float(
            _orch.get("long_confidence") or _orch.get("long_conf") or 0
        ) if isinstance(_orch, dict) else 0
        # 保守：只有编排器长期强方向才给中等分
        score = int(long_conf * 60) if long_bias != "neutral" else 20
        # 优先 side 参数（来自 derive_trend_side），其次编排器 bias
        _side_l = (side or "long").lower()
        if _side_l in ("long", "short"):
            direction = _side_l
        else:
            direction = "long" if long_bias == "bullish" else (
                "short" if long_bias == "bearish" else "neutral"
            )
        return {
            "score": score,
            "direction": direction,
            "should_open": score >= min_score,
            "suggested_sl_pct": 0.08,
            "reasoning": f"[规则回退] 编排器长期bias={long_bias} conf={long_conf:.0%}",
        }

    # ──────────────────────────────────────────────────────────────
    # 职责 2：持仓定期复查（平仓/减仓/继续）
    # ──────────────────────────────────────────────────────────────

    def review_position(
        self,
        *,
        symbol: str,
        side: str,
        position: Dict[str, Any],
        reports: Dict[str, Any],
        market_envs: Dict[str, Any],
        account_id: Optional[int] = None,
        db=None,
    ) -> Dict[str, Any]:
        """复查趋势持仓，给出 hold/reduce/close/tighten 建议。

        Returns:
            {
                "action": "hold"/"reduce"/"close"/"tighten_trailing",
                "reduce_ratio": 0.0-1.0,  # reduce 时的减仓比例
                "reasoning": str,
                "trend_adjustment": {  # 止盈止损优化（写入 exit_state_json）
                    "trailing_atr_mult": float or None,
                    "staged_tp_adjust": "raise"/"lower"/None,
                }
            }
        """
        # [2026-08-11 修复] 先释放只读事务再构建 prompt：
        # _build_review_prompt 内部会调 build_trend_deep_context → onchain 网络请求，
        # 事务开着会在网络阻塞期间 idle-in-transaction。
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        prompt = self._build_review_prompt(
            symbol, side, position, reports, market_envs, db=db,
        )
        # [2026-08-11 修复] 释放只读事务再进 LLM，避免 idle-in-transaction。
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        result = self._call_llm(prompt, account_id=account_id, caller="TrendAgent:review")
        if result:
            return self._normalize_review(result, symbol)
        return self._fallback_review(symbol, position)

    def _build_review_prompt(self, symbol, side, position, reports, market_envs, db=None) -> str:
        from backend.services.analyst_report_builder import compact_report_text

        entry = float(position.get("entry_price", 0) or 0)
        mark = float(position.get("mark_price", 0) or entry)
        pnl_pct = float(position.get("pnl_pct", 0) or 0)
        hold_hours = float(position.get("hold_hours", 0) or 0)
        lev = int(position.get("leverage", 1) or 1)
        context = compact_report_text(
            reports, market_envs=market_envs, symbols=[symbol],
        )

        _macro_block = ""
        try:
            from backend.services.macro_regime_service import macro_regime_service
            _macro_block = macro_regime_service.get_state("GLOBAL").prompt_block()
        except Exception:
            pass

        _deep_ctx = ""
        try:
            from backend.services.agent_deep_context import build_trend_deep_context
            _deep_ctx = build_trend_deep_context(symbol, db=db)
        except Exception:
            pass

        _inline = f"""
你是 TrendAgent，正在复查一个趋势持仓。只看 4h-1d 级别趋势，不看短期噪声。

{_macro_block}

持仓信息：
- {symbol} {side}，入场价 {entry}，当前价 {mark}
- 浮盈亏 {pnl_pct:+.2f}%，已持仓 {hold_hours:.1f}h，杠杆 {lev}x

核心原则：
- **让利润奔跑**：除非趋势明确反转，否则倾向 hold。
- 趋势减弱（非反转）→ 建议 reduce 部分（30-50%），不全平。
- 趋势明确反转 / 突破失败 → 建议 close 全平。
- 趋势强劲但接近重大阻力 → 建议 tighten_trailing（收紧追踪止损锁定利润）。
- 持仓不足 12h 且趋势仍在 → 强烈倾向 hold（趋势单需要时间）。

趋势复查问题（逐一思考后回答）：
1. 4h/1d 趋势方向是否与持仓方向一致？
2. 趋势是加速、匀速、还是减速？当前处于生命周期的哪一阶段？
3. 有没有突破失败的迹象（假突破回落）？
4. 资金费率是否仍支持这个方向？
5. 当前浮盈是否到了该部分止盈的阶段？
6. 对照开仓时的 scenario A/B/C 预测，当前走势偏离主场景多少？

## 深度市场数据（与开仓同级）
{_deep_ctx}

## 基础上下文
{context}

只返回 JSON：
{{
  "action": "hold/reduce/close/tighten_trailing",
  "reduce_ratio": 0.3,
  "trend_still_valid": true,
  "trend_strength": "strong/moderate/weak/broken",
  "trailing_atr_mult": 2.0,
  "staged_tp_adjust": "raise/lower/none",
  "reasoning": "趋势复查结论（须含生命周期判断，最多150字）"
}}
"""
        try:
            from backend.services.agent_prompt_service import render_agent_task
            return render_agent_task(
                "task_trend_agent_review",
                {
                    "symbol": symbol,
                    "side": side,
                    "entry_price": f"{entry:.4f}",
                    "mark_price": f"{mark:.4f}",
                    "pnl_pct": f"{pnl_pct:+.2f}",
                    "hold_hours": f"{hold_hours:.1f}",
                    "leverage": str(lev),
                    "macro_block": _macro_block,
                    "compact_report": context,
                    "deep_context": _deep_ctx,
                },
                consumer="TrendAgent:review",
                fallback_text=_inline,
            )
        except Exception:
            pass

        return _inline

    def _normalize_review(self, result: Dict, symbol: str) -> Dict[str, Any]:
        action = (result.get("action") or "hold").lower()
        if action not in ("hold", "reduce", "close", "tighten_trailing"):
            action = "hold"
        reduce_ratio = float(result.get("reduce_ratio", 0.3) or 0.3)
        reduce_ratio = max(0.1, min(0.8, reduce_ratio))  # 安全范围
        # [fix] 放宽 reasoning 截断 200→600；捞回思维链供持仓复查记录。
        reasoning = (result.get("reasoning") or "")[:600]
        reasoning_content = (result.get("_reasoning_content") or "")[:6000]
        trailing_mult = result.get("trailing_atr_mult")
        staged_adj = (result.get("staged_tp_adjust") or "none").lower()
        trend_adj = {}
        if trailing_mult is not None:
            trend_adj["trailing_atr_mult"] = max(0.5, min(4.0, float(trailing_mult)))
        if staged_adj in ("raise", "lower"):
            trend_adj["staged_tp_adjust"] = staged_adj
        return {
            "action": action,
            "reduce_ratio": reduce_ratio,
            "reasoning": reasoning,
            "reasoning_content": reasoning_content,
            "trend_adjustment": trend_adj,
        }

    def _fallback_review(self, symbol: str, position: Dict) -> Dict[str, Any]:
        """LLM 失败时规则回退：浮亏超 SL 一半建议 close，否则 hold。

        币圈尾部风险兜底：即使"让利润奔跑"，若持仓期间出现反向 high 级清算簇
        磁吸，倾向 close 而非 hold（级联清算会打断趋势，硬扛代价大）。
        """
        pnl_pct = float(position.get("pnl_pct", 0) or 0)
        side = (position.get("side") or "").lower()
        direction = "long" if side in ("long", "buy") else "short" if side in ("short", "sell") else "neutral"

        # 浮亏超 6% → 保守平仓
        if pnl_pct < -6:
            return {"action": "close", "reduce_ratio": 0, "reasoning": "[规则回退] 浮亏超6%，保守平仓",
                    "trend_adjustment": {}}

        # 币圈清算簇反向 high 风险 → 倾向 close（让利润奔跑的例外）
        if direction != "neutral":
            try:
                from backend.services.crypto_alpha_signals import crypto_alpha
                _lm = crypto_alpha.liquidation_magnet(symbol)
                if _lm.available and _lm.severity == "high" and _lm.direction != "neutral":
                    _opp = "short" if direction == "long" else "long"
                    if _lm.direction == _opp:
                        return {
                            "action": "close", "reduce_ratio": 0,
                            "reasoning": f"[规则回退] 反向high级清算磁吸({_lm.note})，趋势可能被级联打断，平仓",
                            "trend_adjustment": {},
                        }
            except Exception:
                pass

        return {"action": "hold", "reduce_ratio": 0, "reasoning": "[规则回退] LLM不可用，保守持有",
                "trend_adjustment": {}}

    # ──────────────────────────────────────────────────────────────
    # 职责 3：补仓时机判断（在 review_position 里顺带输出，或独立调用）
    # ──────────────────────────────────────────────────────────────

    def evaluate_pyramid(
        self,
        *,
        symbol: str,
        side: str,
        position: Dict[str, Any],
        reports: Dict[str, Any],
        market_envs: Dict[str, Any],
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """判断趋势持仓是否到了补仓时机。

        Returns:
            {"action": "add"/"wait"/"skip", "pyramid_ratio": 0.0-0.5, "reasoning": str}
        """
        prompt = self._build_pyramid_prompt(symbol, side, position, reports, market_envs)
        result = self._call_llm(prompt, account_id=account_id, caller="TrendAgent:pyramid")
        if result:
            action = (result.get("action") or "skip").lower()
            ratio = float(result.get("pyramid_ratio", 0.25) or 0.25)
            ratio = max(0.1, min(0.5, ratio))
            return {
                "action": action if action in ("add", "wait", "skip") else "skip",
                "pyramid_ratio": ratio,
                "reasoning": (result.get("reasoning") or "")[:200],
            }
        # 回退：浮盈 + 回调才考虑
        pnl_pct = float(position.get("pnl_pct", 0) or 0)
        if pnl_pct > 3:
            return {"action": "add", "pyramid_ratio": 0.25, "reasoning": "[规则回退] 浮盈>3%，可小比例补"}
        return {"action": "skip", "pyramid_ratio": 0, "reasoning": "[规则回退] 未达补仓条件"}

    def _build_pyramid_prompt(self, symbol, side, position, reports, market_envs) -> str:
        from backend.services.analyst_report_builder import compact_report_text

        entry = float(position.get("entry_price", 0) or 0)
        mark = float(position.get("mark_price", 0) or entry)
        pnl_pct = float(position.get("pnl_pct", 0) or 0)
        context = compact_report_text(reports, market_envs=market_envs, symbols=[symbol])
        return f"""
你是 TrendAgent，判断趋势持仓是否到了补仓时机。

持仓：{symbol} {side}，入场 {entry}，现价 {mark}，浮盈 {pnl_pct:+.2f}%

补仓原则：
- 只在**浮盈且多周期结构未破坏**时补仓（顺势金字塔）；趋势初期 ADX 偏低也允许补仓，不要求 ADX≥20 硬门槛。
- 浮盈已覆盖 2 倍手续费时优先考虑 add（避免手续费侵蚀小浮盈仓）。
- **绝不**在浮亏时补仓（加密永续亏损加仓=自杀）。
- 第一笔补仓比例建议 25-30%，不要一次性加太多。
- 趋势加速（非回调）时不补——等回调。

上下文：
{context}

只返回 JSON：
{{
  "action": "add/wait/skip",
  "pyramid_ratio": 0.25,
  "support_level": 0,
  "reasoning": "补仓判断（最多80字）"
}}
"""

    # ──────────────────────────────────────────────────────────────
    # LLM 调用（复用 DirectionAgent 的模式）
    # ──────────────────────────────────────────────────────────────

    def _call_llm(self, prompt: str, account_id: Optional[int], caller: str) -> Optional[Dict]:
        try:
            from backend.services.llm_config_service import get_llm_config_for_analysis, call_llm_api_sync

            cfg = get_llm_config_for_analysis(account_id)
            if not cfg:
                logger.warning("[%s] 无 LLM 配置，走规则回退", caller)
                return None
            # Pro reasoning 模型思维链与答案共享 max_completion_tokens 额度，
            # 8192 偏紧易导致 JSON 被截断 → 解析失败走规则回退。放宽到 16384，可经环境变量覆盖。
            _max_tokens = int(os.getenv("TREND_LLM_MAX_TOKENS", "16384"))
            _sys = (
                "你是趋势交易专家 Agent，只返回 JSON。\n"
                "你专注于 4h-1d 级别趋势分析，忽略短期噪声。\n"
                "你的方向判断完全基于数据自主做出：顺势、逆势、中性都可以，只要证据支持。\n"
                "系统不会强制你顺势，也不会因为方向与宏观锚点不一致而拒单。\n"
                "你必须基于提供的数据做判断，不要编造数据。\n"
                "当你看到逐笔战绩和亏损教训时，请认真参考——避免重蹈覆辙。\n"
                "reasoning 必须包含完整分析逻辑（趋势判断+多周期共振+衍生品确认+择时），不要只写一句话。"
            )
            _messages = [
                {"role": "system", "content": _sys},
                {"role": "user", "content": prompt},
            ]

            def _one_shot(_msgs):
                return call_llm_api_sync(
                    cfg,
                    _msgs,
                    temperature=0.2,
                    max_tokens=_max_tokens,
                    response_format={"type": "json_object"},
                    account_id=account_id,
                    caller=caller,
                    # [2026-07-31] MLTO thesis_update 必须绕过语义缓存：
                    # SOL/ETH/BTC 的 prompt 结构相似度 >95%，HashingEmbedder 会误判为同一查询，
                    # 导致 BTC/ETH 命中 SOL 的缓存 → 返回相同方向 → 中性死循环。
                    bypass_cache=("thesis_update" in (caller or "")),
                )

            def _extract_content(_resp) -> tuple:
                content = (((_resp or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                if isinstance(content, list):
                    content = "\n".join(
                        str(x.get("text", x)) if isinstance(x, dict) else str(x)
                        for x in content
                    )
                content = (content or "").strip()
                reasoning_cot = extract_reasoning_content_safe(_resp or {})
                _finish = ((((_resp or {}).get("choices") or [{}])[0].get("finish_reason")) or "")
                return content, reasoning_cot, _finish

            def _parse_json_obj(_content: str) -> Dict:
                content = (_content or "").strip()
                if content.startswith("```"):
                    content = re.sub(r"^```(?:json)?\s*", "", content)
                    content = re.sub(r"\s*```$", "", content)
                _start = content.find("{")
                _end = content.rfind("}")
                if _start >= 0 and _end > _start:
                    content = content[_start:_end + 1]
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    _fixed = content
                    _fixed = re.sub(r",\s*([}\]])", r"\1", _fixed)
                    _fixed = _fixed.replace("'", '"')
                    _fixed = re.sub(r"//[^\n]*", "", _fixed)
                    _fixed = re.sub(r"[\x00-\x1f\x7f]", "", _fixed)
                    try:
                        result = json.loads(_fixed)
                        logger.info("[%s] JSON 容错解析成功（原始格式有误，已修复）", caller)
                        return result
                    except json.JSONDecodeError as e2:
                        logger.warning(
                            "[%s] JSON 容错解析仍失败: %s | content前200字符: %.200s",
                            caller, str(e2)[:80], content,
                        )
                        raise e2

            resp = _one_shot(_messages)
            content, reasoning_cot, _finish = _extract_content(resp)
            if _finish == "length":
                logger.warning(
                    "[%s] finish_reason=length 推理/答案被截断，考虑调大 TREND_LLM_MAX_TOKENS=%d",
                    caller, _max_tokens,
                )
            elif not reasoning_cot:
                logger.info(
                    "[%s] reasoning捞回 0 chars（非推理模型或无思维链）| content %d chars | finish=%s",
                    caller, len(content), _finish,
                )
            else:
                logger.info(
                    "[%s] reasoning捞回 %d chars | content %d chars | finish=%s",
                    caller, len(reasoning_cot), len(content), _finish,
                )

            # P1：空 content / 坏 JSON → 再请求一次「只吐合法 JSON」
            result = None
            try:
                if not content:
                    raise json.JSONDecodeError("empty content", "", 0)
                result = _parse_json_obj(content)
            except (json.JSONDecodeError, TypeError, ValueError) as _parse_err:
                logger.warning(
                    "[%s] 首次 JSON 失败，发起 1 次结构化重试: %s",
                    caller, str(_parse_err)[:80],
                )
                _retry_msgs = list(_messages) + [
                    {
                        "role": "assistant",
                        "content": content[:1500] if content else "",
                    },
                    {
                        "role": "user",
                        "content": (
                            "上一次输出不是合法 JSON（空内容或截断/语法错误）。"
                            "请只返回一个完整 JSON 对象，不要 markdown，不要注释，"
                            "字符串用双引号，不要尾随逗号。"
                        ),
                    },
                ]
                try:
                    resp2 = _one_shot(_retry_msgs)
                    content2, reasoning_cot2, _finish2 = _extract_content(resp2)
                    if reasoning_cot2:
                        reasoning_cot = reasoning_cot2
                    logger.info(
                        "[%s] JSON 重试 content %d chars finish=%s",
                        caller, len(content2), _finish2,
                    )
                    if not content2:
                        raise json.JSONDecodeError("empty retry content", "", 0)
                    result = _parse_json_obj(content2)
                except Exception as _retry_err:
                    logger.warning(
                        "[%s] JSON 重试仍失败，走规则回退: %s",
                        caller, str(_retry_err)[:120],
                    )
                    return None

            if isinstance(result, dict):
                result["_reasoning_content"] = (reasoning_cot or "")[:6000]
                return result
            return None
        except Exception as e:
            logger.warning("[%s] LLM 调用失败，走规则回退: %s", caller, str(e)[:120])
            return None

    def update_thesis(
        self,
        symbol: str,
        prompt: str,
        account_id: Optional[int] = None,
    ) -> Optional[Dict]:
        """MLTO 专用：仅更新 thesis，不直接开单。"""
        return self._call_llm(prompt, account_id=account_id, caller="MLTO:thesis_update")


# 全局单例
trend_agent = TrendAgent()
