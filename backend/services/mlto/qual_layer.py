"""LLM thesis_update layer."""

from __future__ import annotations



import json

import logging

from typing import Dict, List, Optional



from backend.services.mlto import layered_memory

from backend.services.mlto.types import MemoryEventDTO, PerceptionPacket, QualUpdateResult, ThesisDTO



logger = logging.getLogger(__name__)



def _build_wisdom_injection(db, tier: str):
    """[v6 4.2] 注入闸门：按活跃模板取回测智慧文本 + 解析 wisdom_ids。

    返回 (注入文本, 去重后的 wisdom_id 列表)；失败返回 ("", [])。
    文本带 `<!-- wisdom_ids:[...] -->` 标记（backtest_insight_compiler 格式），
    供平仓结算时追溯哪些智慧参与了本次决策。
    """
    if db is None:
        return "", []
    try:
        import re as _re
        from backend.services.backtest_insight_compiler import insight_compiler
        from backend.database.models import StrategyTemplate

        templates = db.query(StrategyTemplate).filter(
            StrategyTemplate.is_active == True  # noqa: E712
        ).order_by(StrategyTemplate.rating.desc()).limit(3).all()
        if not templates:
            return "", []

        parts = []
        for tpl in templates:
            w = insight_compiler.get_active_wisdom(db, tpl.template_id)
            if w:
                parts.append(w)
        if not parts:
            return "", []

        text = "\n\n".join(parts)
        ids: List[int] = []
        for m in _re.finditer(r'<!-- wisdom_ids:(\[[\d,\s]*\]) -->', text):
            try:
                ids.extend(json.loads(m.group(1)))
            except Exception:
                pass
        return text, sorted(set(ids))
    except Exception as e:
        logger.debug(f"[Qual] wisdom injection skip: {e}")
        return "", []


def _resolve_wisdom_db(db):
    """[v6 4.2] TradingWisdom/StrategyTemplate 在 core 库（alpha_arena），
    而生产链 update_thesis 收到的是 analytics 连接（alpha_analytics），
    直接用它查 core 表会 UndefinedTable → 注入静默跳过。
    此处探测：传入 db 可查 StrategyTemplate 则复用，否则回退 core SessionLocal。
    返回 (session, need_close)。
    """
    from backend.database.connection import SessionLocal as _CoreSessionLocal
    from backend.database.models import StrategyTemplate
    if db is not None:
        try:
            db.query(StrategyTemplate).first()
            return db, False
        except Exception:
            # 探测失败会置 PG 事务为 aborted，必须 rollback 以免污染调用方事务
            try:
                db.rollback()
            except Exception:
                pass
    return _CoreSessionLocal(), True



def update_thesis(

    packet: PerceptionPacket,

    thesis: ThesisDTO,

    memory_events: List[MemoryEventDTO],

    new_events: List[MemoryEventDTO],

    db=None,

) -> QualUpdateResult:

    agent = "swing_agent" if packet.tier == "mid" else "trend_agent"

    memory_block = layered_memory.format_for_prompt(memory_events)

    delta_block = layered_memory.format_for_prompt(new_events) if new_events else "（无新证据）"



    constraints = ""

    if db is not None:

        try:

            from backend.services.decision_feedback_service import decision_feedback_service

            constraints = decision_feedback_service.get_agent_constraints(

                db, agent_type="swing" if packet.tier == "mid" else "trend", account_id=packet.account_id,

            )

            thesis_c = decision_feedback_service.get_thesis_constraints(

                db, agent_type="swing" if packet.tier == "mid" else "trend",

            )

            if thesis_c:

                constraints = (constraints + "\n" + thesis_c).strip()

        except Exception:

            pass

    # MidLong v2 Phase4：概念信念 / 失败 Intent
    try:
        from backend.services.mlto.midlong_belief_loop import format_beliefs_for_prompt
        _reg = ""
        try:
            from backend.services.decision_core.regime_agent import classify_regime
            _ms = getattr(packet, "market_summary_sym", None) or {}
            if isinstance(_ms, dict):
                _reg = classify_regime(_ms).regime
        except Exception:
            pass
        _belief = format_beliefs_for_prompt(
            symbol=getattr(packet, "symbol", ""),
            regime=_reg,
            limit=4,
        )
        if _belief:
            constraints = ((constraints or "") + "\n" + _belief).strip()
    except Exception:
        pass

    # [v6 4.2] 注入闸门：回测智慧注入 LLM 决策 prompt + 记录注入计数。
    # 智慧文本带 wisdom_ids 标记，注入后解析 id 写入 thesis（平仓时据此验证），
    # 并同步给 trading_wisdom.applied_count +1（注入计数转正）。
    # 注意：trading_wisdom/strategy_templates 在 core 库，生产链 db 是 analytics 连接，
    # 必须经 _resolve_wisdom_db 解析到 core 连接后再查（否则 UndefinedTable 静默跳过）。
    _core_db, _need_close = _resolve_wisdom_db(db)
    try:
        _wisdom_text, _wisdom_ids = _build_wisdom_injection(_core_db, packet.tier)
        if _wisdom_text:
            constraints = ((constraints or "") + "\n\n" + _wisdom_text).strip()
        if _wisdom_ids:
            thesis.wisdom_ids = _wisdom_ids
            try:
                from backend.services.wisdom_tracker import wisdom_tracker
                _n = wisdom_tracker.record_wisdom_ids(_core_db, _wisdom_ids)
                if _need_close:
                    _core_db.commit()  # fallback 会话自行提交计数
                if _n:
                    logger.info(
                        "[Qual] 注入回测智慧 %d 条 (ids=%s) → applied_count +1", _n, _wisdom_ids
                    )
            except Exception as _we:
                logger.debug(f"[Qual] wisdom applied count skip: {_we}")
    finally:
        if _need_close:
            try:
                _core_db.close()
            except Exception:
                pass

    prompt = _build_prompt(thesis, memory_block, delta_block, constraints, packet, db=db)

    raw = _call_llm(agent, prompt, packet.account_id)

    result = _parse_result(raw, getattr(packet, "market_summary_sym", None))

    if result.thesis_summary:

        layered_memory.store_event(

            thesis,

            "intermediate",

            "llm",

            "thesis_update",

            result.thesis_summary[:200],

            result.raw,

            db=db,

        )

    return result





def _build_thesis_block(thesis: ThesisDTO) -> str:

    return (

        f"- direction: {thesis.direction}\n"

        f"- llm_conviction: {thesis.llm_conviction}\n"

        f"- hub_adjusted: {thesis.hub_adjusted:.2f}\n"

        f"- review_count: {thesis.review_count}\n"

        f"- summary: {thesis.thesis_summary or '（尚无）'}\n"

        f"- invalidation: {json.dumps(thesis.invalidation, ensure_ascii=False)}"

    )


def _build_market_brief(packet) -> str:
    """从 PerceptionPacket 提取实时市场数据摘要，注入 prompt 供 LLM 判断方向。

    [2026-07-31 修复] 此前 prompt 只含 thesis_block/memory_block/delta_block，
    完全没有实时行情——LLM 看不到价格/RSI/MACD/EMA/funding，只能看到自己上轮的
    neutral thesis → 自我强化 neutral 死循环 + Cache#13 命中（prompt 不变）。
    注入实时行情后：LLM 有证据判方向，且每轮价格变动天然打破缓存。
    """
    ms = packet.market_summary_sym or {}
    orch = packet.orchestrator or {}
    qb = packet.quant_brief or {}
    lines: list[str] = []

    # ── 价格 & 波动率 ──
    price = ms.get("current_price") or ms.get("price") or 0
    lines.append(f"价格: {price}")
    if ms.get("atr_1d_pct"):
        lines.append(f"ATR日波幅: {ms['atr_1d_pct']:.1%}")
    if ms.get("volatility_regime"):
        lines.append(f"波动率regime: {ms['volatility_regime']}")

    # ── 市场周期 & 趋势 ──
    if ms.get("market_cycle"):
        lines.append(f"市场周期: {ms['market_cycle']}")
    if ms.get("trend_direction"):
        lines.append(f"趋势方向: {ms['trend_direction']}")
    if ms.get("trend_strength") is not None:
        lines.append(f"趋势强度: {ms['trend_strength']:.2f}")

    # ── 多周期指标（核心判据）──
    for tf in ("1h", "4h", "1d", "1w"):
        ind = ms.get(f"indicators_{tf}")
        if not isinstance(ind, dict):
            continue
        parts: list[str] = []
        if ind.get("rsi") is not None:
            parts.append(f"RSI={ind['rsi']:.1f}")
        if ind.get("macd_hist") is not None:
            parts.append(f"MACD_hist={ind['macd_hist']:.4f}")
        if ind.get("ema_trend"):
            parts.append(f"EMA趋势={ind['ema_trend']}")
        if ind.get("adx") is not None:
            parts.append(f"ADX={ind['adx']:.1f}")
        if ind.get("ema9") is not None and ind.get("ema21") is not None:
            parts.append(f"EMA9={'%.2f' % ind['ema9']}/EMA21={'%.2f' % ind['ema21']}")
        if ind.get("vol_ratio"):
            parts.append(f"量比={ind['vol_ratio']:.2f}")
        if ind.get("oi_delta") is not None:
            parts.append(f"OI变化={ind['oi_delta']:.2f}")
        if ind.get("taker_ratio") is not None:
            parts.append(f"多空比={ind['taker_ratio']:.2f}")
        if parts:
            lines.append(f"[{tf}] {' | '.join(parts)}")
    if ms.get("trend_1w"):
        lines.append(f"周线趋势别名: {ms['trend_1w']}")
    if ms.get("adx_1d") is not None:
        lines.append(f"日线ADX别名: {ms['adx_1d']}")

    # ── 多频对齐 ──
    mfa = ms.get("multi_freq_alignment")
    if mfa:
        lines.append(
            f"多频对齐: {mfa} (15m={ms.get('freq_15m_direction','?')}"
            f" 1h={ms.get('freq_1h_direction','?')}"
            f" 4h={ms.get('freq_4h_direction','?')})"
        )

    # ── 衍生品 & 情绪 ──
    if ms.get("funding_rate") is not None:
        lines.append(f"资金费率: {ms['funding_rate']:.4%}")
    if ms.get("fear_greed") is not None:
        lines.append(f"恐慌贪婪: {ms['fear_greed']:.0f}")
    if ms.get("sentiment_zone"):
        lines.append(f"情绪区: {ms['sentiment_zone']}")
    if ms.get("whale_direction"):
        lines.append(f"鲸鱼方向: {ms['whale_direction']}")

    # ── VPVR 关键位 ──
    vpvr: list[str] = []
    for k in ("poc_price", "vah_price", "val_price"):
        v = ms.get(k)
        if v is not None:
            vpvr.append(f"{k.upper()}={v}")
    if vpvr:
        lines.append(f"VPVR: {' | '.join(vpvr)}")

    # ── Fusion 信号 ──
    if ms.get("fusion_direction") and ms.get("fusion_strength") is not None:
        lines.append(f"Fusion: {ms['fusion_direction']} (强度={ms['fusion_strength']:.2f})")

    # ── 编排器 bias ──
    bias_parts: list[str] = []
    if orch.get("long_bias"):
        bias_parts.append(f"长向={orch['long_bias']}(conf={orch.get('long_confidence', 0):.2f})")
    if orch.get("mid_bias"):
        bias_parts.append(f"中向={orch['mid_bias']}(conf={orch.get('mid_confidence', 0):.2f})")
    if orch.get("macro_direction_constraint") and orch["macro_direction_constraint"] != "none":
        bias_parts.append(f"宏观约束={orch['macro_direction_constraint']}")
    if orch.get("macro_cycle_phase"):
        bias_parts.append(f"宏观周期={orch['macro_cycle_phase']}")
    if bias_parts:
        lines.append(f"编排器: {' | '.join(bias_parts)}")

    # ── QuantBrief 对齐度 ──
    align = qb.get("alignment_score")
    if align is not None:
        lines.append(f"量化对齐: {align}/15 (avail={qb.get('evidence_available_ratio', 0):.2f})")
    if qb.get("direction"):
        lines.append(f"量化方向: {qb['direction']}")

    # ── P1：短线 overlay / 因子快照 / 分析师 / 持仓（2026-07-31）──
    ov = ms.get("short_overlay")
    if isinstance(ov, dict) and ov:
        lines.append(
            f"短线overlay: dir={ov.get('direction')} conf={ov.get('confidence')} "
            f"age={ov.get('age_sec')}s"
        )
    mf = ms.get("midlong_factors")
    if isinstance(mf, dict) and mf.get("count"):
        # v6 M4：因子只作 LLM 证据摘要（禁止进 Hub 投票），给深度分析看读数
        bits = [f"count={mf.get('count')}"]
        for tf in ("4h", "1d"):
            vals = mf.get(tf) or {}
            if not isinstance(vals, dict) or not vals:
                continue
            ranked = []
            for k, v in list(vals.items())[:12]:
                try:
                    ranked.append((str(k), float(v)))
                except (TypeError, ValueError):
                    continue
            ranked.sort(key=lambda x: abs(x[1]), reverse=True)
            top = ", ".join(f"{k}={v:+.3f}" for k, v in ranked[:6])
            if top:
                bits.append(f"{tf}[{top}]")
        fw_sig = ms.get("framework_signals") or ms.get("orch_bias") or orch.get("bias")
        if fw_sig is not None:
            bits.append(f"framework_ref={fw_sig}")
        lines.append("中长线因子证据(仅供分析,不投票): " + " | ".join(bits))
    reports = packet.analyst_reports or {}
    if isinstance(reports, dict) and reports:
        bits = []
        for name, rep in list(reports.items())[:6]:
            if name.startswith("_") or not isinstance(rep, dict):
                continue
            d = rep.get("direction") or rep.get("bias") or rep.get("signal")
            c = rep.get("confidence") or rep.get("score")
            if d is not None:
                bits.append(f"{name}={d}({c})")
        if bits:
            lines.append("分析师: " + " | ".join(bits[:6]))
    port = packet.portfolio or {}
    positions = port.get("positions") or port.get("open_positions") or []
    if isinstance(positions, list) and positions:
        same = [
            p for p in positions
            if isinstance(p, dict) and str(p.get("symbol") or "").upper() == str(packet.symbol or "").upper()
        ]
        lines.append(
            f"组合: 总持仓={len(positions)} 本币持仓={len(same)} "
            f"权益={(port.get('balance') or {}).get('total_equity', port.get('equity', '?'))}"
        )

    # ── 原始 K 线摘要（本币 × data_center trade；优先用 inject 写入的 recent_klines）──
    # [2026-08-10 v3.1.0] K线 8→30 根/周期，周期 +1h；mid 额外 15m（入场验证），
    # long 额外 1M 月线锚（asterdex 已回填，_min_bars=12，不足标注不阻塞）。
    kline_tfs = ("15m", "1h", "4h", "1d") if packet.tier == "mid" else ("1h", "4h", "1d", "1w")
    for tf in kline_tfs:
        ind = ms.get(f"indicators_{tf}")
        recent = []
        if isinstance(ind, dict):
            recent = ind.get("recent_klines") or []
        if not isinstance(recent, list) or len(recent) < 5:
            # 兜底：15m 注入未跑时直查 data_center（1h/4h/1d/1w 由指标块保证）
            if tf in ("15m", "1M"):
                try:
                    from backend.services.kline_data_service import kline_service as _ks
                    recent = _ks.get_klines_from_db(packet.symbol, tf, count=30) or []
                except Exception:
                    recent = []
        if not isinstance(recent, list) or len(recent) < 5:
            continue
        tail = recent[-30:]
        candle_bits = []
        for row in tail:
            if not isinstance(row, dict):
                continue
            dt = str(row.get("datetime") or row.get("timestamp") or "")[:16]
            candle_bits.append(
                f"{dt} O={row.get('open')} H={row.get('high')} "
                f"L={row.get('low')} C={row.get('close')} V={row.get('volume')}"
            )
        if candle_bits:
            lines.append(f"[{tf} K线×{len(tail)}]\n" + "\n".join(candle_bits))

    # 长线 1M 月线锚（asterdex 主所已回填 60 根；不足 12 根标注不报错）
    if packet.tier == "long":
        try:
            from backend.services.kline_data_service import kline_service as _ks
            m1 = _ks.get_klines_from_db(packet.symbol, "1M", count=20) or []
            if len(m1) >= 12:
                candle_bits = []
                for row in m1[-20:]:
                    if not isinstance(row, dict):
                        continue
                    dt = str(row.get("datetime") or row.get("timestamp") or "")[:7]
                    candle_bits.append(
                        f"{dt} O={row.get('open')} H={row.get('high')} "
                        f"L={row.get('low')} C={row.get('close')} V={row.get('volume')}"
                    )
                if candle_bits:
                    lines.append(f"[1M K线×{len(candle_bits)}]\n" + "\n".join(candle_bits))
            else:
                lines.append("[1M] 月线数据不足（<12 根），暂缺月线锚")
        except Exception as _m1_err:
            logger.debug("[Qual] 1M kline skip: %s", _m1_err)
            lines.append("[1M] 月线数据不可用")

    return "\n".join(lines) if lines else "（无实时行情数据）"



def _build_cross_views(packet, db=None) -> Dict[str, str]:
    """[2026-08-10 v3.1.0] 中长线互验段构建。

    mid prompt 注入同标的长线 thesis.mid_view（direction/timing_score/key_levels）；
    long prompt 注入同标的中线 thesis 最新方向与确信度。
    任一查询失败 → 返回占位文本，不阻塞主流程。
    """
    out = {"long_timing_view": "（无长线择时视图）", "mid_thesis_view": "（无中线观点）"}
    session_id = getattr(packet, "session_id", None)
    symbol = getattr(packet, "symbol", "")
    if not session_id or not symbol:
        return out
    try:
        from backend.services.mlto import thesis_store
        if packet.tier == "mid":
            lt = thesis_store.get(session_id, symbol, "long", db=db)
            if lt is not None:
                mv = getattr(lt, "mid_view", None)
                if mv is not None:
                    d = mv.to_dict() if hasattr(mv, "to_dict") else {}
                    out["long_timing_view"] = (
                        f"- direction: {d.get('direction', 'neutral')}（相对长线方向）\n"
                        f"- timing_score: {d.get('timing_score', 50)}/100\n"
                        f"- timing_rationale: {d.get('timing_rationale') or '（无）'}\n"
                        f"- key_levels: {d.get('key_levels') or '（无）'}\n"
                        f"- invalidation_for_timing: {d.get('invalidation_for_timing') or '（无）'}"
                    )
        else:
            mt = thesis_store.get(session_id, symbol, "mid", db=db)
            if mt is not None:
                out["mid_thesis_view"] = (
                    f"- direction: {getattr(mt, 'direction', 'neutral')}\n"
                    f"- llm_conviction: {getattr(mt, 'llm_conviction', 0)}\n"
                    f"- summary: {(getattr(mt, 'thesis_summary', '') or '（无）')[:200]}"
                )
    except Exception as e:
        logger.debug("[Qual] cross thesis view skip: %s", e)
    return out





def _build_prompt(thesis, memory_block, delta_block, constraints, packet, db=None) -> str:

    # [2026-07-31 修复] 注入实时行情摘要——此前 prompt 完全没有市场数据，
    # LLM 无法判断趋势方向 → 永远 neutral + Cache#13 命中。
    market_brief = _build_market_brief(packet)
    logger.info("[MLTO] market_brief %s %s: %d chars | %.200s", packet.symbol, packet.tier, len(market_brief), market_brief)

    # [2026-08-10 v3.1.0] 深度市场数据（与老链路同源，消除重复实现）：
    # mid → build_full_deep_context(15m/1h/4h/1d×30)；long → build_trend_deep_context(4h/1d/1w/1M×52+结构)。
    deep_context = ""
    try:
        from backend.services.agent_deep_context import (
            build_full_deep_context,
            build_trend_deep_context,
        )
        if packet.tier == "mid":
            deep_context = build_full_deep_context(
                packet.symbol,
                db=db,
                account_id=getattr(packet, "account_id", None),
                kline_periods=["15m", "1h", "4h", "1d"],
                kline_count=30,
            )
        else:
            deep_context = build_trend_deep_context(packet.symbol, db=db)
    except Exception as _dc_err:
        logger.debug("[Qual] deep_context skip: %s", _dc_err)
        deep_context = ""
    if not deep_context:
        deep_context = "（深度市场数据不可用）"

    # [2026-08-10 v3.1.0] 短线建议独立成段（market_brief 内已有摘要行，模板要求独立可见）
    _ov = (packet.market_summary_sym or {}).get("short_overlay")
    if isinstance(_ov, dict) and _ov:
        short_overlay = (
            f"- direction: {_ov.get('direction', '?')}\n"
            f"- confidence: {_ov.get('confidence', '?')}\n"
            f"- age_sec: {_ov.get('age_sec', '?')}\n"
            f"- summary: {_ov.get('summary') or _ov.get('signal') or _ov.get('text') or '（无描述）'}"
        )
    else:
        short_overlay = "（当前无 2 小时内短线建议）"

    # [2026-08-10 v3.1.0] 中长线互验段（mid 看长线 mid_view；long 看中线观点）
    cross = _build_cross_views(packet, db)
    long_timing_view = cross.get("long_timing_view", "（无长线择时视图）")
    mid_thesis_view = cross.get("mid_thesis_view", "（无中线观点）")
    cross_view = long_timing_view if packet.tier == "mid" else mid_thesis_view

    # [阶段2] 中周期子视图请求片段：长线 thesis 同时产出 mid_view（择时子结构）。
    # 中线 tier 本身就是中周期，无需再嵌 mid_view，仅在长线时附上。
    mid_view_request = ""
    if packet.tier == "long":
        mid_view_request = """
  "mid_view": {
    "direction": "align|counter|neutral",
    "timing_score": 0,
    "timing_rationale": "基于 1h/4h 结构的择时判断，2-3 句",
    "key_levels": {"support": 0, "resistance": 0},
    "invalidation_for_timing": "中周期择时失效的条件（价格/形态）"
  },
"""

    fallback = f"""你是 {'SwingAgent(中线波段)' if packet.tier == 'mid' else 'TrendAgent(长线趋势)'}。

职责：更新方向研判 thesis，禁止直接输出 buy/sell 开仓指令。



## 实时行情数据（{packet.symbol}）

{market_brief}



## 深度市场数据

{deep_context}



## 短线建议（近2小时 scalp 信号）

{short_overlay}



## 互验参考（同标的中线/长线 thesis 视图）

{cross_view}



## 当前研判账本

{_build_thesis_block(thesis)}



## 检索记忆 Top-8

{memory_block}



## 本轮新证据

{delta_block}



## 历史反馈约束

{constraints or '（无）'}



## 输出 JSON（仅 JSON）

{{

  "direction": "long|short|neutral",

  "conviction_delta": -20..+20,

  "thesis_summary": "2-3句连贯叙事，引用 cited_event_ids",

  "cited_event_ids": ["event_id"],

  "missing_evidence": ["还缺什么"],

  "invalidation": {{"price": 0, "condition": "..."}},

  "recommend_open": true,

  "should_close": false,
{mid_view_request}}}

## regime 参数建议（v6 S2-7，选填；参考市场行情给出档位建议）

{{
  "regime_suggestion": {{
    "regime": "trend|ranging|extreme|unknown",
    "sl_multiplier": 1.0,
    "tp_trigger": 2.0,
    "trailing": false,
    "addon_rhythm": "none|conservative|aggressive",
    "rationale": "一句话理由"
  }}
}}

仅返回 JSON，不要其他文字。
"""

    task_id = "task_swing_thesis_update" if packet.tier == "mid" else "task_trend_thesis_update"

    try:

        from backend.services.agent_prompt_service import render_agent_task

        return render_agent_task(

            task_id,

            {

                "symbol": packet.symbol,

                "market_brief": market_brief,

                "thesis_block": _build_thesis_block(thesis),

                "memory_block": memory_block,

                "delta_block": delta_block,

                "constraints": constraints or "",

                # [2026-08-10 v3.1.0] 深度市场数据 / 短线建议 / 互验段
                "deep_context": deep_context,

                "short_overlay": short_overlay,

                "long_timing_view": long_timing_view,

                "mid_thesis_view": mid_thesis_view,

                # [阶段2] 把 mid_view 请求片段透传给 prompt 模板（仅长线）。
                # 模板可引用 {{mid_view_request}}；若模板未使用此变量也不影响渲染。
                "mid_view_request": mid_view_request,

            },

            consumer=f"mlto.qual_layer.update_thesis:{packet.tier}",

            fallback_text=fallback,

        )

    except Exception:

        return fallback





def _call_llm(agent: str, prompt: str, account_id: Optional[int]) -> dict:

    try:

        if agent == "swing_agent":

            from backend.services.swing_agent import swing_agent

            return swing_agent.update_thesis("", prompt, account_id=account_id) or {}

        from backend.services.trend_agent import trend_agent

        return trend_agent.update_thesis("", prompt, account_id=account_id) or {}

    except Exception as exc:
        # [P5] LLM 调用失败曾长期静默（debug 级别）——失败时 direction 默认
        # neutral 且 review_count 依旧上涨，是 thesis 方向失真的一大来源。
        # 升为 warning 便于在日志中直接观测 LLM 可用性。
        logger.warning("[MLTO] qual LLM fail: %s", exc)

        return {}





def _parse_result(raw: dict, market_data=None) -> QualUpdateResult:

    if not isinstance(raw, dict):

        return QualUpdateResult()

    cited = raw.get("cited_event_ids") or raw.get("cited_fact_ids") or []

    if isinstance(cited, str):

        cited = [cited]

    inv = raw.get("invalidation") or {}

    if not isinstance(inv, dict):

        inv = {}

    miss = raw.get("missing_evidence") or []

    if isinstance(miss, str):

        miss = [miss]

    # [阶段2] 解析中周期子视图。LLM 未返回 mid_view / 格式错 → None（向后兼容，不报错）。
    mv = raw.get("mid_view")
    if not isinstance(mv, dict) or not mv:
        mv = None
    else:
        # 规范化为标准字段；timing_score 强制 0-100。
        try:
            ts = int(mv.get("timing_score") or 0)
        except (TypeError, ValueError):
            ts = 0
        ts = max(0, min(100, ts))
        kl = mv.get("key_levels")
        mv = {
            "direction": str(mv.get("direction") or "neutral").lower(),
            "timing_score": ts,
            "timing_rationale": str(mv.get("timing_rationale") or ""),
            "key_levels": kl if isinstance(kl, dict) else None,
            "invalidation_for_timing": str(mv.get("invalidation_for_timing") or ""),
            "updated_at": float(mv.get("updated_at") or 0.0),
        }

    return QualUpdateResult(

        direction=str(raw.get("direction") or "neutral").lower(),

        conviction_delta=int(raw.get("conviction_delta") or 0),

        thesis_summary=str(raw.get("thesis_summary") or "")[:500],

        # [add] 从 agent 透传的 _reasoning_content 捞回思维链（阶段1已挂载）。
        reasoning_content=str(raw.get("_reasoning_content") or "")[:6000],

        cited_event_ids=[str(x) for x in cited][:12],

        missing_evidence=[str(x) for x in miss][:8],

        invalidation=inv,

        # [2026-07-31] 缺字段 → None（open_gate 放行）；仅显式 false 拦截。
        # 旧 bool(raw.get(...)) 把缺省当成 False，叠加 prompt 示例 false，近一周
        # recommend_open=False 拦截 457 次，长线系统性开不出仓。
        recommend_open=(
            None if "recommend_open" not in raw
            else bool(raw.get("recommend_open"))
        ),

        # [Phase A 修复 Bug2] 解析 LLM 的 should_close（thesis 完全失效 → 主动离场）。
        should_close=bool(raw.get("should_close")),

        mid_view=mv,

        # [v6 S2-7] regime 参数建议通道：解析 LLM regime_suggestion 块并规则校验
        #（regime 与 classify_regime 冲突以规则为准；数值档位 clamp；None=未提供）。
        regime_suggestion=_parse_regime_suggestion(raw, market_data),

        # [2026-08-05 v6 6.3 第3项] LLM exit_plan 止损参数直通：
        # 优先取 exit_plan/tp_sl_proposal 内嵌 sl_pct（v3 schema，参考
        # swing_agent._parse_result 的降级兼容），兜底取扁平 sl_pct/tp_pct（v2）。
        # 两者都缺失 → 0.0（不覆盖 thesis 历史有效值，执行层走 structure_stops 兜底）。
        sl_pct=_parse_exit_plan_sl(raw),
        tp_pct=_parse_exit_plan_tp(raw),

        raw=raw,

    )


# ─────────────────────────────────────────────────────────────────────
# [v6 阶段2 S2-7] regime 参数建议通道：LLM 输出 → 解析 → 规则校验 → applied
# ─────────────────────────────────────────────────────────────────────
def _parse_regime_suggestion(raw: dict, market_data=None) -> Optional[Dict[str, Any]]:
    """解析 LLM regime_suggestion 块并规则校验；未提供/异常 → None（不阻断）。"""
    try:
        from backend.services.mlto.regime_suggestion import (
            parse_regime_suggestion,
            validate_regime_suggestion,
        )
        rs = raw.get("regime_suggestion") if isinstance(raw, dict) else None
        sugg = parse_regime_suggestion(rs)
        if sugg is None:
            return None
        validated = validate_regime_suggestion(sugg, market_data)
        if validated.get("conflicts") or validated.get("rejected"):
            logger.debug(
                "[MLTO-S2-7] %s regime_suggestion 校验: conflicts=%s rejected=%s",
                sugg.regime, validated["conflicts"], validated["rejected"],
            )
        return validated["applied"]
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# [2026-08-05 v6 6.3 第3项] LLM exit_plan 止损参数直通解析（v3/v2 双格式）
# ─────────────────────────────────────────────────────────────────────
def _parse_exit_plan_sl(raw: dict) -> float:
    """LLM 止损：优先 exit_plan/tp_sl_proposal.sl_pct，兜底扁平 sl_pct。"""
    if not isinstance(raw, dict):
        return 0.0
    _plan = raw.get("exit_plan") or raw.get("tp_sl_proposal") or {}
    if isinstance(_plan, dict):
        try:
            v = float(_plan.get("sl_pct") or 0)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    try:
        v = float(raw.get("sl_pct") or 0)
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _parse_exit_plan_tp(raw: dict) -> float:
    """LLM 止盈：优先 exit_plan/tp_sl_proposal.tp_stages[0].pct，再 tp_pct，兜底扁平。"""
    if not isinstance(raw, dict):
        return 0.0
    _plan = raw.get("exit_plan") or raw.get("tp_sl_proposal") or {}
    if isinstance(_plan, dict):
        try:
            _stages = _plan.get("tp_stages")
            if isinstance(_stages, list) and _stages and isinstance(_stages[0], dict):
                v = float(_stages[0].get("pct") or 0)
                if v > 0:
                    return v
        except (TypeError, ValueError, IndexError):
            pass
        try:
            v = float(_plan.get("tp_pct") or 0)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    try:
        v = float(raw.get("tp_pct") or 0)
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0

