"""SwingAgent — 中线波段交易 Agent（2026-06-18 三层架构）。

替代 DirectionAgent 对 swing nature 的处理。
独立 prompt，关注 1h/4h 尺度，快 LLM（DeepSeek-V3 / GPT-4o）。

设计原则：
- 只关注 1h/4h 时间尺度，不看 5m 噪声，不看 1d 长线
- [阶段3c] 方向判断完全数据驱动：顺势/逆势/回调/突破/均值回归均可，只要证据支持
- 持仓 2-8 小时，TP 达成或趋势减弱就走
- 复用 DirectionAgent 的 LLM 调用框架（call_llm_api_sync + JSON + fallback）
- 遵守 QAA 架构 + 现有仓位管理

============================================================================
[阶段4 — 已废弃 / DEPRECATED] 2026-07-23
============================================================================
本模块的"中线独立分析"执行路径已被废弃：
  - `mlto_cycle._swing_one`（中线独立并行 LLM + 开仓）已删除
  - `master_execution` 中线 SwingAgent 分支已删除
  - `midlong_loop` 不再调度 SwingAgent

中线分析能力现由长线 thesis 的 `mid_view` 子结构统一提供：
  - MLTO qual_layer prompt 同时产出 long 方向 + mid_view（1h/4h）
  - quant_layer 产出 mid_timing 信号
  - decision_hub 已含 mid_timing 权重（0.15）

模块保留原因（不删文件，留一版安全）：
  - `swing_agent.is_swing_nature` 仍用于路由检测（master_execution 的
    MidLongExecutionLane delegate + agent 数据预加载）
  - `derive_swing_side` 仍被 mlto/orchestrator 用于构建 quant brief
  - `_archive_prompt` 仍被 trend_agent 复用做 prompt 落盘
  - `swing_agent.update_thesis` 仍被 mlto/qual_layer 的 tier=mid LLM 分支引用
    （mid-tier thesis 段已 hard-skip，分支实际不可达，保留兼容签名）

新代码不应再调用 `swing_agent.analyze`。导入本模块会触发 DeprecationWarning。
============================================================================
"""
from __future__ import annotations

import json
import logging
import os
import re
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
# [fix] reasoning 模型把深度推理放在 message.reasoning_content，早期 _call_llm 只读
# content 导致整条思维链被丢弃 → 中长线决策"看起来很浅"。统一用公共 helper 捞回。
from backend.services.llm_reasoning_helper import extract_reasoning_content_safe

logger = logging.getLogger(__name__)

# [阶段4] 模块级弃用警告——保留兼容导入，但明确提示新代码不应再用本模块的独立分析路径。
warnings.warn(
    "backend.services.swing_agent is DEPRECATED (Phase 4 mid-into-long merge). "
    "Mid-line analysis now lives in the long thesis's mid_view (MLTO qual_layer + "
    "quant_layer mid_timing). Only routing helpers (is_swing_nature / derive_swing_side / "
    "_archive_prompt) remain in use; swing_agent.analyze must not be called by new code.",
    DeprecationWarning,
    stacklevel=2,
)

_SWING_MAX_TOKENS = int(os.getenv("SWING_LLM_MAX_TOKENS", "4096"))


def _archive_prompt(prompt: str, agent_type: str, symbol: str) -> None:
    """S1-7 落盘 redacted prompt（对应 04 综合方案 §3.3 / 审计 R8）。

    把每次 LLM 调用的 prompt 落盘到 data/prompt_archives/{agent}/{date}/{hash}.json，
    供事后审计"模型到底看见了什么"+ A/B 测试。

    脱敏：不存储 API key、账户余额等敏感字段（prompt 本身已不含这些）。
    限频：同一 hash 的 prompt 只落盘一次（避免重复）。
    """
    try:
        import hashlib
        from datetime import datetime
        _date = datetime.utcnow().strftime("%Y%m%d")
        _hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:12]
        _archive_dir = os.path.join(
            os.getenv("PROMPT_ARCHIVE_DIR", "data/prompt_archives"),
            agent_type, _date,
        )
        os.makedirs(_archive_dir, exist_ok=True)
        _path = os.path.join(_archive_dir, f"{symbol}_{_hash}.json")
        if os.path.exists(_path):
            return  # 同 hash 已落盘,跳过
        with open(_path, "w", encoding="utf-8") as f:
            json.dump({
                "agent_type": agent_type,
                "symbol": symbol,
                "timestamp": datetime.utcnow().isoformat(),
                "prompt_length": len(prompt),
                "prompt": prompt,
            }, f, ensure_ascii=False, indent=2)
    except Exception as _e:
        logger.debug("[PromptArchive] %s/%s 落盘失败: %s", agent_type, symbol, _e)


def infer_swing_direction_from_market(
    symbol: str, market_envs: Optional[Dict[str, Any]] = None,
) -> str:
    """Paper 试单：LLM direction=neutral 时从 1h EMA / MTF / 编排器推断方向。"""
    _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
    _i1 = (_ms.get("indicators_1h") or {}) if isinstance(_ms, dict) else {}
    ema = str(_i1.get("ema_trend") or "").lower()
    if ema in ("bullish", "up", "long", "uptrend"):
        return "long"
    if ema in ("bearish", "down", "short", "downtrend"):
        return "short"
    _mtf = (_ms.get("mtf_resonance") or {}) if isinstance(_ms, dict) else {}
    mdir = str(_mtf.get("direction") or "").lower()
    if mdir in ("long", "short"):
        return mdir
    return derive_swing_side(symbol, market_envs)


def derive_swing_side(symbol: str, market_envs: Optional[Dict[str, Any]] = None) -> str:
    """从编排器 mid_bias / 宏观约束推导 Swing QuantBrief 方向锚点。"""
    _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
    _orch = _ms.get("orchestrator", {}) if isinstance(_ms, dict) else {}
    dc = (_orch.get("macro_direction_constraint") or "").lower()
    if dc == "long_only":
        return "long"
    if dc == "short_only":
        return "short"
    _mb = (_orch.get("mid_bias") or "neutral").lower()
    if _mb == "bearish":
        return "short"
    if _mb == "bullish":
        return "long"
    _lb = (_orch.get("long_bias") or "neutral").lower()
    if _lb == "bearish":
        return "short"
    if _lb == "bullish":
        return "long"
    try:
        from backend.services.macro_regime_service import macro_regime_service
        return macro_regime_service.get_state("GLOBAL").side_hint()
    except Exception:
        return "long"


@dataclass
class SwingDecision:
    """SwingAgent 的决策输出。"""
    action: str = "hold"           # buy / sell / hold
    confidence: int = 0            # 0-100
    direction: str = "neutral"     # long / short / neutral
    should_open: bool = False
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    risk_reward: float = 0.0
    reasoning: str = ""
    reasoning_content: str = ""      # [fix] reasoning 模型完整思维链（不再丢弃）
    source: str = "swing_agent"
    cited_fact_ids: List[str] = field(default_factory=list)
    evidence_audit: Dict[str, Any] = field(default_factory=dict)
    hold_reason: str = ""           # should_open=False 时的可读原因（日志用）
    raw_action: str = "hold"        # LLM 原始 action，便于区分展示 hold
    # ── S1-9 新增字段（v3 schema，对应 04 综合方案 §2.3.4）──
    tp_sl_proposal: Dict[str, Any] = field(default_factory=dict)  # LLM 的 exit_plan
    lifecycle: str = ""              # 启动/加速/衰竭/反转/震荡
    scenarios: Dict[str, Any] = field(default_factory=dict)      # {a/b/c: {prob, trigger, target_pct}}
    invalidation: Dict[str, Any] = field(default_factory=dict)   # {price_level, condition, time_limit_hours}
    self_check: Dict[str, Any] = field(default_factory=dict)     # {counter_argument, confidence_adjustment, max_loss_acceptable_pct}
    expected_hold_hours: float = 0.0  # LLM 建议持仓时长（小时）
    conviction_level: str = ""        # low/medium/high


class SwingAgent:
    """中线波段 Agent — 单例。"""

    _instance: Optional["SwingAgent"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def is_swing_nature(self, nature: str) -> bool:
        return (nature or "").lower() in ("swing",)

    def analyze(
        self,
        *,
        symbol: str,
        reports: Dict[str, Any],
        market_envs: Dict[str, Any],
        account_id: Optional[int] = None,
        portfolio: Optional[Dict[str, Any]] = None,
        db=None,
        light_context: bool = False,
    ) -> SwingDecision:
        """分析中线波段机会。"""
        from backend.services.agent_evidence_builder import (
            build_swing_evidence,
            format_evidence_for_prompt,
        )

        facts = build_swing_evidence(symbol, market_envs or {}, db=db)
        prompt = self._build_prompt(
            symbol, reports, market_envs, portfolio=portfolio, db=db,
            evidence_block=format_evidence_for_prompt(facts),
            account_id=account_id,
            light_context=light_context,
        )
        result = self._call_llm(prompt, account_id=account_id)
        if result:
            decision = self._normalize(result, symbol, market_envs=market_envs)
            return self._apply_fact_guard(decision, result, facts)
        return self._fallback(symbol, market_envs)

    def _apply_fact_guard(
        self, decision: SwingDecision, llm_result: Dict, facts: list,
    ) -> SwingDecision:
        from backend.services.agent_fact_guard import (
            build_evidence_audit,
            verify_agent_decision,
        )

        cited = llm_result.get("cited_fact_ids") or []
        if isinstance(cited, str):
            cited = [cited]
        try:
            from backend.config.settings import PAPER_FAST_TRIAL
            _paper = PAPER_FAST_TRIAL
        except Exception:
            _paper = True
        # S2-2 收紧模拟盘试单：strict 模式抬高 paper hold→open 的信心/RR 门槛
        try:
            from backend.config.settings import MIDLONG_PAPER_PROBE_STRICT
            _strict = bool(MIDLONG_PAPER_PROBE_STRICT)
        except Exception:
            _strict = True
        if _paper and _strict:
            _min_conf = 52
            _min_rr = 1.6
        else:
            _min_conf = 48 if _paper else 55
            _min_rr = 1.5 if _paper else 1.8
        # S2-3 中长线 paper FactGuard 切 enforce（strict 时强制拦截无据/幻觉决策）
        fg = verify_agent_decision(
            action=decision.action,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
            cited_fact_ids=cited,
            facts=facts,
            agent_type="swing",
            min_confidence=_min_conf,
            force_enforce=(_paper and _strict),
        )
        decision.cited_fact_ids = list(cited)
        decision.evidence_audit = build_evidence_audit(facts, cited, fg)
        if fg.mode == "enforce":
            if not fg.allow:
                decision.action = "hold"
                decision.should_open = False
                decision.reasoning = (
                    f"[FactGuard] {','.join(fg.violations)} | {decision.reasoning}"
                )[:200]
            elif fg.adjusted_confidence is not None:
                decision.confidence = fg.adjusted_confidence
                decision.should_open = (
                    decision.action != "hold"
                    and decision.confidence >= _min_conf
                    and decision.risk_reward >= _min_rr
                )
        return decision

    def _build_prompt(
        self, symbol, reports, market_envs, portfolio=None, db=None,
        evidence_block="", account_id=None, light_context: bool = False,
    ) -> str:
        from backend.services.analyst_report_builder import compact_report_text

        context = compact_report_text(
            reports, market_envs=market_envs, symbols=[symbol],
            portfolio=portfolio,
        )
        _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
        _orch = _ms.get("orchestrator", {}) if isinstance(_ms, dict) else {}

        _agent_constraints = ""
        if db is not None:
            try:
                from backend.services.decision_feedback_service import decision_feedback_service
                _agent_constraints = decision_feedback_service.get_agent_constraints(
                    db, agent_type="swing", account_id=account_id,
                )
            except Exception:
                pass

        _regime = "unknown"
        try:
            from backend.services.decision_core.regime_agent import classify_regime
            _regime = classify_regime(_ms if isinstance(_ms, dict) else {}).regime
        except Exception:
            pass

        _mid_opens_today = 0
        if db is not None and account_id:
            try:
                from backend.services.decision_core.fee_context import count_nature_opens
                _mid_opens_today = count_nature_opens(
                    db, int(account_id), nature="swing", symbol=symbol,
                )
            except Exception:
                pass

        # 深度上下文注入（K线/指标/衍生品/情报/记忆/教训/RAG/市场状态）
        _deep_ctx = ""
        if not light_context:
            try:
                from backend.services.agent_deep_context import build_full_deep_context
                _deep_ctx = build_full_deep_context(
                    symbol, db=db, kline_periods=["1h", "4h"], kline_count=30,
                )
            except Exception:
                pass
        else:
            _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
            _i1 = (_ms.get("indicators_1h") or {}) if isinstance(_ms, dict) else {}
            _i4 = (_ms.get("indicators_4h") or {}) if isinstance(_ms, dict) else {}
            _mtf = (_ms.get("mtf_resonance") or {}) if isinstance(_ms, dict) else {}
            _deep_ctx = (
                f"[轻量模式] 1h RSI={_i1.get('rsi')} EMA={_i1.get('ema_trend')} "
                f"vol={_i1.get('vol_ratio')} | 4h RSI={_i4.get('rsi')} EMA={_i4.get('ema_trend')} | "
                f"MTF score={_mtf.get('score')} aligned={_mtf.get('aligned')}"
            )

        # S2-1 量化简报进脑：多周期一致性/结构位/数据完整度前置注入，引导 LLM 先看证据质量。
        try:
            from backend.services.decision_core.quant_brief import build_quant_brief
            _qb = build_quant_brief(symbol, market_envs, nature="swing")
            if _qb:
                _deep_ctx = _qb + "\n\n" + (_deep_ctx or "")
        except Exception:
            pass

        # ── S1-7 新增：量化特征表 + memory_block + ATR + recent_loss_block ──
        # 替代原来分散的注入，统一为标准化特征表（对应 04 综合方案 §2.3.4/§2.3.8）。
        # 这些字段是打断方向偏见循环的关键数据源（same_dir_losses_24h / cooldown_remain_sec / blocked_sides）。
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
            # 1. 量化特征表（含交易记忆+冷却+风控边界，一张表搞定）
            _quant_feature_table = render_quant_feature_table(
                symbol, market_envs or {}, db=db, account_id=account_id, nature="swing",
            )
            # 2. memory_block（兼容旧 {{memory_block}} 占位符,内容同特征表的交易记忆段）
            if db is not None and account_id:
                _memory_block = render_memory_block(db, symbol, "swing", account_id, limit=5)
            # 3. ATR 块（显式展示波动率,让 LLM 算 RR 有依据）
            _atr_block = render_atr_block(symbol, market_envs or {})
            # 4. 同方向冷却块（含 cooldown_active 标志）
            if db is not None and account_id:
                _cd_info = render_recent_loss_block(
                    db, symbol, _orch.get("mid_bias", "neutral") if isinstance(_orch, dict) else "neutral",
                    account_id, window_hours=24,
                )
                _recent_loss_block = _cd_info.get("block_text", "")
                _cooldown_active = bool(_cd_info.get("cooldown_active", False))
        except Exception as _qft_err:
            logger.debug("[SwingAgent] %s 量化特征表注入失败: %s", symbol, _qft_err)

        fallback = self._build_prompt_inline(
            symbol, context, _deep_ctx, _orch, evidence_block, _agent_constraints,
        )
        try:
            from backend.services.agent_prompt_service import render_agent_task
            _prompt = render_agent_task(
                "task_swing_agent",
                {
                    "symbol": symbol,
                    "deep_context": _deep_ctx,
                    "compact_report": context,
                    "orchestrator": _orch,
                    "evidence_block": evidence_block or "",
                    "agent_constraints": _agent_constraints or "",
                    "regime": _regime,
                    "mid_opens_today": _mid_opens_today,
                    # S1-7 新增变量
                    "quant_feature_table": _quant_feature_table,
                    "memory_block": _memory_block,
                    "atr_block": _atr_block,
                    "recent_loss_block": _recent_loss_block,
                    "cooldown_active": str(_cooldown_active).lower(),
                },
                consumer="SwingAgent:analyze",
                fallback_text=fallback,
            )
            # S1-7 落盘 redacted prompt（对应 04 综合方案 §3.3 / 审计 R8）
            # 不阻塞主流程,失败只记日志。落盘路径 data/prompt_archives/swing_agent/{date}/{hash}.json
            try:
                _archive_prompt(_prompt, "swing_agent", symbol)
            except Exception:
                pass
            return _prompt
        except Exception:
            return fallback

    def _build_prompt_inline(self, symbol, context, _deep_ctx, _orch, evidence_block, agent_constraints="") -> str:
        _constraints_block = ""
        if agent_constraints:
            _constraints_block = f"\n## 历史反馈约束（参考，结合本次判断）\n{agent_constraints}\n"
        # [阶段3c] 内联 fallback 对齐 5 段结构（简化版）：删 RR/conf 硬门槛/方向 bias/闸门威胁，
        # 保留数据上下文 + 自由推理 + 5 风险底线。
        return f"""你是 SwingAgent，专注 1h/4h 时间尺度的中线波段交易员，标的：{symbol}，持仓目标 2-8 小时。
你的方向判断（多/空/中性）完全基于数据自主做出——顺势、逆势、回调、突破、均值回归都可以，只要证据支持。

## 自由推理引导（建议覆盖，但不限于；无门槛、无配方）
- 趋势结构：1h/4h 的 EMA9/21/50 排列 + RSI → 当前趋势方向和强度
- 支撑阻力 + 猎杀止损：价格是否刚扫过 swing high/low？是否在止损密集区附近？由你判断含义
- 成交量确认：vol_ratio 放量/缩量的含义
- 衍生品信号：清算磁吸、CVD、OBI、funding → 机构资金在做什么
- 市场状态（regime）：trending/ranging/volatile，由你判断是否适合波段
- 历史经验：看"逐笔战绩"和"亏损教训"，这个币种/regime 下过去表现如何？
- 盈亏比与 SL 宽度：由你根据 ATR 与结构决定，没有外部强制门槛
- 反向假设检验：如果这笔交易亏了，最可能的原因是什么？什么证据会改变你的判断？

## 风险底线（不可妥协的安全网，仅此 5 条）
1. 单笔最大风险金额 ≤ 账户权益的 1.5%（防破产）。
2. 杠杆遵守系统给定 tier cap（你无需输出杠杆）。
3. 只分析固定交易对 {symbol}。
4. 关键数据缺失 → 输出 action=hold 并说明缺什么。
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
  "action": "buy/sell/hold",
  "confidence": 0,
  "direction": "long/short/neutral",
  "sl_pct": 0.035,
  "tp_pct": 0.07,
  "risk_reward": 2.0,
  "cited_fact_ids": ["rsi_1h", "mid_bias"],
  "reasoning": "波段判断（必须包含趋势判断+支撑阻力+衍生品确认的完整分析，最多200字）"
}}
"""

    def _normalize(
        self, result: Dict, symbol: str, market_envs: Optional[Dict] = None,
    ) -> SwingDecision:
        raw_action = (result.get("action") or "hold").lower()
        action = raw_action
        if action not in ("buy", "sell", "hold"):
            action = "hold"
        confidence = int(result.get("confidence", 0) or 0)
        confidence = max(0, min(95, confidence))
        direction = (result.get("direction") or "neutral").lower()
        sl_pct = float(result.get("sl_pct", 0.035) or 0.035)
        tp_pct = float(result.get("tp_pct", 0.07) or 0.07)
        rr = float(result.get("risk_reward", 0) or 0)
        if rr == 0 and sl_pct > 0:
            rr = tp_pct / sl_pct
        reasoning = (result.get("reasoning") or "")[:800]
        reasoning_content = (result.get("_reasoning_content") or "")[:6000]
        regime_fit = (result.get("regime_fit") or "").lower()

        # ── S1-10 新增：解析 v3 schema 字段（对应 04 综合方案 §2.3.4）──
        # 降级兼容：LLM 返回 v2 schema（扁平 sl_pct/tp_pct）时，新字段为空 dict，
        # 不影响原有逻辑；返回 v3 schema 时，从 tp_sl_proposal 提取更精确的 SL/TP。
        tp_sl_proposal = result.get("tp_sl_proposal") or result.get("exit_plan") or {}
        if isinstance(tp_sl_proposal, dict) and tp_sl_proposal:
            # 从 tp_sl_proposal 覆盖 sl_pct/tp_pct（若 LLM 给了更精确的值）
            _prop_sl = float(tp_sl_proposal.get("sl_pct") or 0)
            if _prop_sl > 0:
                sl_pct = _prop_sl
            # tp_stages 数组：取第一档作为 tp_pct（兼容旧字段）
            _stages = tp_sl_proposal.get("tp_stages") or []
            if isinstance(_stages, list) and _stages:
                try:
                    _first_stage = _stages[0]
                    if isinstance(_first_stage, dict):
                        _prop_tp1 = float(_first_stage.get("pct") or 0)
                        if _prop_tp1 > 0:
                            tp_pct = _prop_tp1
                except Exception:
                    pass
            # 重新计算 RR
            if sl_pct > 0:
                rr = tp_pct / sl_pct

        lifecycle = (result.get("lifecycle") or "").strip()
        # scenarios 支持两种格式：v3 对象 {a: {prob,trigger,target_pct}} 或 v2 字符串
        scenarios_raw = result.get("scenarios") or {}
        if not isinstance(scenarios_raw, dict):
            # v2 格式：scenario_a/b/c 是字符串
            scenarios_raw = {
                "a": {"trigger": result.get("scenario_a") or "", "prob": 0},
                "b": {"trigger": result.get("scenario_b") or "", "prob": 0},
                "c": {"trigger": result.get("scenario_c") or "", "prob": 0},
            }
        scenarios = scenarios_raw if isinstance(scenarios_raw, dict) else {}

        invalidation = result.get("invalidation") or {}
        if not isinstance(invalidation, dict):
            invalidation = {}
        # 也支持扁平字段（旧格式）
        if not invalidation:
            _inv_cond = result.get("invalidation_condition") or ""
            if _inv_cond:
                invalidation = {"condition": str(_inv_cond)[:200]}
        # [补齐修复 2026-07-19] task_swing_agent.md 的 v3 schema 把 invalidation_condition
        # 嵌在 exit_plan 内部（result.exit_plan.invalidation_condition），而不是顶层。
        # 此前只读顶层 result["invalidation"]/result["invalidation_condition"]，导致
        # LLM 严格按 prompt 格式只填 exit_plan 内嵌字段时被完全漏读——实测抽样 195 笔
        # buy/sell 里 invalidation 命中率 0%。这里补一层从 exit_plan 兜底读取。
        if not invalidation and isinstance(tp_sl_proposal, dict):
            _inv_cond2 = tp_sl_proposal.get("invalidation_condition") or ""
            if _inv_cond2:
                invalidation = {"condition": str(_inv_cond2)[:200]}

        self_check = result.get("self_check") or {}
        if not isinstance(self_check, dict):
            self_check = {}
        # self_check 的 confidence_adjustment 应用到 confidence
        _conf_adj = 0
        try:
            _conf_adj = int(self_check.get("confidence_adjustment") or 0)
        except Exception:
            pass
        if _conf_adj != 0:
            confidence = max(0, min(95, confidence + _conf_adj))

        expected_hold_hours = 0.0
        try:
            expected_hold_hours = float(result.get("expected_hold_hours") or 0)
        except Exception:
            pass

        conviction_level = (result.get("conviction_level") or "").lower()

        # MTF + Regime 前置（Phase3）：规则分与 LLM conf 融合
        # 2026-07-06 整改：与 trend_agent 同一处修正——只有**确有 4h/1d 指标数据**时
        # 才做 MTF 融合与 regime 降级。此前无论 market_envs 是否含指标都会 blend，
        # 空数据时 compute_mtf_resonance 返回硬编码 neutral=35、classify_regime 默认
        # ranging，把一个正常 conf（如 60）无端稀释/降级（→47）。"没有数据"被当成
        # "MTF 中性 + 震荡市"来惩罚，方向是错的。有真实指标才融合，无则原样透传。
        try:
            from backend.services.decision_core.mtf_resonance import compute_mtf_resonance
            from backend.services.decision_core.regime_agent import classify_regime
            _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
            _ms = _ms if isinstance(_ms, dict) else {}
            _has_mtf_data = isinstance(_ms.get("indicators_4h"), dict) or isinstance(_ms.get("indicators_1d"), dict)
            if _has_mtf_data:
                mtf = compute_mtf_resonance(_ms)
                # [阶段1 止血 Killer B] llm_weight 0.70→0.90，减少 MTF blend 稀释。
                # 原 0.70 把 LLM conf=55 与中性 MTF(35) 融合成 ≈49，跌破 52 地板，
                # 中线 should_open 永远 False（exit_plan 校验甚至跑不到）。
                # 0.90 时同样输入融成 ≈53，重回地板上方，口子重新打开。
                confidence = mtf.blend_with_llm(confidence, llm_weight=0.90)
                regime = classify_regime(_ms)
                if regime.regime == "ranging" and regime_fit == "poor":
                    confidence = max(0, confidence - 8)
                elif regime.regime == "ranging" and not mtf.aligned:
                    _vol = float((_ms.get("indicators_1h") or {}).get("vol_ratio") or 0)
                    if _vol < 1.2:
                        confidence = max(0, confidence - 5)
        except Exception:
            pass

        # 盈亏比硬约束：paper 试单 conf≥52/RR≥1.6；live 保持 conf≥55/RR≥1.8
        #
        # S0-3 止血修复（R4）：删除 Paper 强制开仓 override。
        # 原逻辑：LLM 输出 hold + conf≥48 + RR≥1.5 时，代码强制改成 buy/sell
        #        （甚至对 direction=neutral 也从市场推断方向强行开仓）。
        # 实测：14 天 57 笔已平仓中 0 笔 close_reason=hold，几乎所有信号被透传到开仓。
        # 修复：尊重 LLM 的 hold 决策，不再强制 override 为 buy/sell。
        #      Paper 门槛从 48/1.5 提到 52/1.6（仍比 Live 宽，但不再强迫开仓）。
        try:
            from backend.config.settings import PAPER_FAST_TRIAL
            _paper = PAPER_FAST_TRIAL
        except Exception:
            _paper = True
        if _paper:
            # Paper 试单：LLM 主动输出 buy/sell 时按此门槛放行；
            # LLM 输出 hold 时尊重其决策，不再强制 override。
            should_open = action != "hold" and confidence >= 52 and rr >= 1.6
        else:
            should_open = action != "hold" and confidence >= 55 and rr >= 1.8

        hold_reason = ""
        if not should_open:
            # S0-3 修复后门槛对齐：paper 52/1.6，live 55/1.8
            _conf_min = 52 if _paper else 55
            _rr_min = 1.6 if _paper else 1.8
            if raw_action == "hold" and confidence >= _conf_min and rr >= _rr_min:
                # LLM 主动 hold 且 conf/RR 达标 → 系统尊重 LLM 决策（不再强制开仓）
                hold_reason = "llm_hold_respected"
            elif confidence < _conf_min:
                hold_reason = f"conf_low({confidence}<{_conf_min})"
            elif rr < _rr_min:
                hold_reason = f"rr_low({rr:.2f}<{_rr_min})"
            elif raw_action == "hold":
                hold_reason = "llm_hold"
            else:
                hold_reason = f"action={raw_action}"

        # ── P1-6 硬校验（04 综合方案 §2.3.4 / task_swing_agent.md 末尾"缺 exit_plan 或
        # invalidation_condition → 拒单"要求）：LLM 若完全没给 tp_stages 分批止盈方案
        # 且没给 invalidation 失效条件，说明没有认真按 v3 schema 输出（可能仍在用旧
        # v2 裸 sl_pct/tp_pct 格式敷衍），拒绝开仓，强制降级为 hold，倒逼 prompt 遵从度。
        # 只在 should_open 时校验（hold 不受影响），避免误伤保守观望信号。
        #
        # [2026-07-20 修复] 实测这条硬校验把中线的口子焊死了：LLM 常规仍返回 v2 裸
        # sl_pct/tp_pct（未套 tp_stages 数组/invalidation 对象），哪怕给的 conf 很高
        # (如 BTC=64、ETH=72) 也被一刀切拒单降级 hold，导致中线连续多日 0 成交。
        # 但"没有 tp_stages 数组"≠"没有出场计划"——只要 LLM 给了真实的扁平 sl_pct/
        # tp_pct（不是代码兜底默认值），风险管理所需信息本质上已经齐了，只是没套
        # 进 v3 的 JSON 结构。改为：优先尝试从扁平字段自动合成一档 tp_stages/
        # invalidation（不降低把关标准，只是把"缺结构"和"缺内容"分开处理）；
        # 只有连扁平 sl/tp 都没有(LLM真的什么都没给，只剩代码默认值)才真正拒单。
        if should_open:
            _has_tp_stages = isinstance(tp_sl_proposal, dict) and isinstance(
                tp_sl_proposal.get("tp_stages"), list
            ) and len(tp_sl_proposal.get("tp_stages") or []) > 0
            _has_invalidation = isinstance(invalidation, dict) and bool(invalidation.get("condition"))
            if not _has_tp_stages and not _has_invalidation:
                # [阶段1 止血 Killer A] 接受任意非零扁平 sl_pct/tp_pct（即便恰好等于
                # 代码兜底默认 0.035/0.07 也接受）——不再做"是否等于默认值"的拒单。
                # 只读原始 result 字段（未经上面 or 0.035/0.07 替换）来判断 LLM 是否
                # "什么都没给"：key 缺失/值为 0 才视为真没给出场计划并拒单；只要给了
                # 任意非零值就自动合成单档 tp_stages + invalidation，把口子打开。
                _raw_sl = result.get("sl_pct")
                _raw_tp = result.get("tp_pct")
                _has_flat_plan = (
                    _raw_sl is not None and float(_raw_sl or 0) > 0
                    and _raw_tp is not None and float(_raw_tp or 0) > 0
                )
                if _has_flat_plan:
                    if not _has_tp_stages:
                        tp_sl_proposal = dict(tp_sl_proposal) if isinstance(tp_sl_proposal, dict) else {}
                        tp_sl_proposal.setdefault("sl_pct", sl_pct)
                        tp_sl_proposal["tp_stages"] = [{"pct": tp_pct, "close_ratio": 1.0}]
                    if not _has_invalidation:
                        invalidation = {
                            "condition": f"价格触及止损位(-{sl_pct:.2%})或原入场逻辑失效",
                        }
                    reasoning = (
                        f"[自动补全exit_plan] LLM给了扁平sl={sl_pct:.2%}/tp={tp_pct:.2%}但未套"
                        f"v3结构，已自动合成单档止盈+失效条件，不拒单 | 原: {reasoning}"
                    )[:800]
                else:
                    should_open = False
                    action = "hold"
                    hold_reason = "exit_plan_missing_reject"
                    reasoning = (
                        "[硬校验拒单] LLM 未提供 tp_stages 分批止盈方案或 invalidation 失效条件，"
                        "也没有扁平 sl_pct/tp_pct 可兜底合成（v3 schema 必填项），"
                        f"拒绝开仓降级为 hold | 原: {reasoning}"
                    )[:800]

        # 币圈防守兜底：清算簇磁吸 severity=high 且与 action 反向 → 强制降级 hold。
        # LLM 可能忽略上面的币圈 alpha 区块，这层代码兜底防止在级联清算中逆势开仓。
        if should_open and direction in ("long", "short"):
            try:
                from backend.services.crypto_alpha_signals import crypto_alpha
                _lm = crypto_alpha.liquidation_magnet(symbol)
                if _lm.available and _lm.severity == "high" and _lm.direction != "neutral":
                    _opp = "short" if direction == "long" else "long"
                    if _lm.direction == _opp:
                        should_open = False
                        action = "hold"
                        hold_reason = "liquidation_magnet_veto"
                        reasoning = (
                            f"[清算簇拦截] {_lm.note}，{direction}方向与high级磁吸反向，"
                            f"强制降级hold | 原: {reasoning}"
                        )
            except Exception:
                pass

        return SwingDecision(
            action=action, confidence=confidence, direction=direction,
            should_open=should_open, sl_pct=sl_pct, tp_pct=tp_pct,
            risk_reward=rr, reasoning=reasoning, reasoning_content=reasoning_content,
            hold_reason=hold_reason, raw_action=raw_action,
            # S1-10b 新增字段
            tp_sl_proposal=tp_sl_proposal,
            lifecycle=lifecycle,
            scenarios=scenarios,
            invalidation=invalidation,
            self_check=self_check,
            expected_hold_hours=expected_hold_hours,
            conviction_level=conviction_level,
        )

    def _fallback(self, symbol: str, market_envs) -> SwingDecision:
        """LLM 失败时的规则回退：用编排器中期 bias 做保守判断。"""
        _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
        _orch = _ms.get("orchestrator", {}) if isinstance(_ms, dict) else {}
        mid_bias = (_orch.get("mid_bias") or "neutral").lower() if isinstance(_orch, dict) else "neutral"
        mid_conf = float(
            _orch.get("mid_confidence") or _orch.get("mid_conf") or 0
        ) if isinstance(_orch, dict) else 0
        if mid_bias != "neutral" and mid_conf >= 0.4:
            direction = "long" if mid_bias == "bullish" else "short"
            return self._normalize({
                "action": "buy" if direction == "long" else "sell",
                "confidence": max(55, int(mid_conf * 100)),
                "direction": direction,
                "sl_pct": 0.04,
                "tp_pct": 0.08,
                "risk_reward": 2.0,
                "reasoning": f"[规则回退] 编排器中期bias={mid_bias} conf={mid_conf:.0%}",
            }, symbol, market_envs=market_envs)
        return self._normalize({
            "action": "hold",
            "confidence": 0,
            "direction": "neutral",
            "reasoning": "[规则回退] 中期信号不足",
        }, symbol, market_envs=market_envs)

    def _call_llm(self, prompt: str, account_id: Optional[int]) -> Optional[Dict]:
        try:
            from backend.services.llm_config_service import (
                get_llm_config_for_analysis, call_llm_api_sync,
            )
            cfg = get_llm_config_for_analysis(account_id)
            if not cfg:
                return None
            # Pro reasoning 模型思维链与答案共享 max_completion_tokens 额度，
            # 8192 偏紧易导致 JSON 被截断 → 解析失败走规则回退。放宽到 16384，可经环境变量覆盖。
            _max_tokens = int(os.getenv("SWING_LLM_MAX_TOKENS", "16384"))
            resp = call_llm_api_sync(
                cfg,
                [
                    {"role": "system", "content": (
                        "你是中线波段交易专家 Agent，只返回 JSON。\n"
                        "你擅长在 1h/4h 时间尺度捕捉入场机会。\n"
                        "你的方向判断完全基于数据自主做出：顺势、逆势、回调、突破、均值回归都可以，只要证据支持。\n"
                        "系统不会强制你顺势，也不会禁止你追涨杀跌或逆势——这些是你的专业判断领域。\n"
                        "你必须基于提供的数据做判断，不要编造数据。\n"
                        "当你看到逐笔战绩和亏损教训时，请认真参考——避免重蹈覆辙。\n"
                        "reasoning 必须包含完整分析逻辑（趋势判断+支撑阻力+衍生品确认），不要只写一句话。"
                    )},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=_max_tokens,
                response_format={"type": "json_object"},
                account_id=account_id,
                caller="SwingAgent:analyze",
                # [2026-07-31] MLTO thesis_update 绕过语义缓存，避免跨 symbol 误命中。
                bypass_cache=True,
            )
            content = (((resp or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if isinstance(content, list):
                content = "\n".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in content)
            content = content.strip()
            # [fix] 捞回 reasoning 模型的深度推理（DeepSeek R1/V4-Pro 等），不再整体丢弃。
            reasoning_cot = extract_reasoning_content_safe(resp or {})
            _finish = ((((resp or {}).get("choices") or [{}])[0].get("finish_reason")) or "")
            if _finish == "length":
                logger.warning("[SwingAgent] finish_reason=length 推理/答案被截断，考虑调大 SWING_LLM_MAX_TOKENS=%d", _max_tokens)
            elif not reasoning_cot:
                logger.info("[SwingAgent] reasoning捞回 0 chars（非推理模型或无思维链）| content %d chars | finish=%s", len(content), _finish)
            else:
                logger.info("[SwingAgent] reasoning捞回 %d chars | content %d chars | finish=%s", len(reasoning_cot), len(content), _finish)
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            _s = content.find("{")
            _e = content.rfind("}")
            if _s >= 0 and _e > _s:
                content = content[_s:_e + 1]
            result = json.loads(content)
            # 透传完整思维链供下游 thesis/决策记录持久化（上限保护防超大）
            if isinstance(result, dict):
                result["_reasoning_content"] = reasoning_cot[:6000]
            return result
        except Exception as e:
            logger.warning("[SwingAgent] LLM 调用失败，走规则回退: %s", str(e)[:120])
            return None

    def update_thesis(
        self,
        symbol: str,
        prompt: str,
        account_id: Optional[int] = None,
    ) -> Optional[Dict]:
        """MLTO 专用：仅更新 thesis，不直接开单。"""
        return self._call_llm(prompt, account_id)


# 全局单例
swing_agent = SwingAgent()
