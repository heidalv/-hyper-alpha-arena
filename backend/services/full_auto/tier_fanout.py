"""多周期扇出 — 从 monolith _expand_multi_tier_decisions 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class TierFanoutHost:
    nature_to_tier_map: Dict[str, str]
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)


def build_tier_fanout_host(svc) -> TierFanoutHost:
    return TierFanoutHost(
        nature_to_tier_map=svc._NATURE_TO_TIER_MAP,
        append_event=svc._append_event,
    )


def expand_multi_tier_decisions(
    decisions: List[Dict],
    strat_tier_map: dict,
    orch_directions: dict,
    session,
    host: TierFanoutHost,
) -> List[Dict]:
    # [tier-fix v12] 放宽扇出门槛：只要该 tier 有策略就至少产出一条独立决策，
    # 避免历史 bug（tier_bias 必须严格同向→全部 continue→回退原始无tier决策→塌缩 long）。
    # 方向/置信度按 tier 情况分档：
    #   A. 同向(bullish 对 buy / bearish 对 sell) 且 conf>=0.20：正常扇出，置信度融合加权
    #   B. 同向但 conf<0.20 / 中性：降权扇出（下游置信度门槛会自行拦截无效档）
    #   C. 反向：强烈反向(strongly_bearish 对 buy)时跳过该档；一般反向降至最低置信度交由下游拦截
    _fan_tier_nature = {"short": "intraday", "mid": "swing", "long": "trend_follow"}
    _expanded: list = []
    for _dec in decisions:
        _sym = (_dec.get("symbol") or "").upper()
        _op = str(_dec.get("operation", _dec.get("action", "hold"))).lower()

        if _op not in ("buy", "sell") or not _sym:
            _expanded.append(_dec)
            continue

        # [tier-fix v13] TierParallelExecutor 生成的决策（含 _source_tier）已经是
        # tier 专属的，不再需要扇出；补齐 _fan_tier 以便下游 tier 识别
        _src_tier = (_dec.get("_source_tier") or "").strip().lower()
        if _src_tier in ("short", "mid", "long"):
            _dec.setdefault("_fan_tier", _src_tier)
            _dec.setdefault("tier", _src_tier)
            # TierParallelExecutor 决策也需注入编排器数据
            _orch_dec = orch_directions.get(_sym, {})
            if _orch_dec:
                _dec["_orchestrator"] = _orch_dec
            # AI 自动选币强制隔离：TierParallelExecutor 路径也需限制 trade_nature
            try:
                from backend.services.auto_coin_selector import get_fixed_symbols_for_session
                from backend.config.settings import AUTO_COIN_ALLOWED_NATURES
                _sid = getattr(session, "session_id", None)
                # [2026-07-21 修复] 改为正向白名单+现查DB（理由见 mlto_cycle.py 同名修复注释），
                # 不用只查内存池、且无DB兜底的 is_auto_coin_symbol。
                _is_ai_coin = bool(_sid) and _sym not in get_fixed_symbols_for_session(_sid)
                if _is_ai_coin:
                    _tn = _dec.get("trade_nature", "")
                    if _tn not in AUTO_COIN_ALLOWED_NATURES:
                        _dec["trade_nature"] = "scalp"
                        logger.info(
                            f"[FanOut] {_sym}[{_src_tier}]: auto_coin force nature "
                            f"{_tn}→scalp"
                        )
                    # [中长线升级同步] auto-coin 只进短线,tier 强制降为 short
                    if _dec.get("tier") not in ("short",) or _dec.get("_fan_tier") not in ("short",):
                        _dec["tier"] = "short"
                        _dec["_fan_tier"] = "short"
            except ImportError:
                pass
            _expanded.append(_dec)
            continue

        _active_tiers = sorted(set(
            t for (s, t) in strat_tier_map if s == _sym
        ))
        if not _active_tiers:
            _orch_dec = orch_directions.get(_sym, {})
            if _orch_dec:
                _dec["_orchestrator"] = _orch_dec
            _expanded.append(_dec)
            continue

        # 单 tier 策略：也做一次显式封装，给它写上 _fan_tier/tier 以保证下游 tier 正确
        _orch = orch_directions.get(_sym, {})
        _target_dir = "bullish" if _op == "buy" else "bearish"
        _opposite_strong = "strongly_bearish" if _target_dir == "bullish" else "strongly_bullish"

        # ── P1-FIX: 编排器全局结论检查 ──
        # 编排器的加权共识 (final_action/allowed_direction) 比单周期 bias 更权威。
        # 如果编排器整体判定 wait/frozen 或方向限制与 AI 矛盾，整笔决策跳过。
        _orch_final = str(_orch.get("final_action", "") or "")
        _orch_allowed = str(_orch.get("allowed_direction", "both") or "both")
        if _orch_final in ("wait", "frozen"):
            logger.info(
                f"[FanOut] {_sym}: skip(编排器全局 {_orch_final}, "
                f"AI={_op} L/M/S={_orch.get('long_bias','?')}/{_orch.get('mid_bias','?')}/{_orch.get('short_bias','?')})"
            )
            _expanded.append(_dec)
            continue
        if (_orch_allowed == "short_only" and _op == "buy") or \
           (_orch_allowed == "long_only" and _op == "sell") or \
           (_orch_allowed == "none"):
            logger.info(
                f"[FanOut] {_sym}: skip(编排器方向限制 {_orch_allowed} vs AI={_op})"
            )
            _expanded.append(_dec)
            continue

        _fanned_any = False
        for _tier in _active_tiers:
            _bias = _orch.get(f"{_tier}_bias", "neutral")
            _conf = float(_orch.get(f"{_tier}_confidence", 0) or 0)

            # 反向强烈——跳过该 tier 避免硬冲突
            if _bias == _opposite_strong and _conf >= 0.30:
                logger.info(
                    f"[FanOut] {_sym}[{_tier}]: skip(强反向 bias={_bias} conf={_conf:.2f})"
                )
                continue

            _same_dir = (
                (_target_dir == "bullish" and _bias in ("bullish", "strongly_bullish"))
                or (_target_dir == "bearish" and _bias in ("bearish", "strongly_bearish"))
            )
            _weak_oppose = (
                (_target_dir == "bullish" and _bias == "bearish")
                or (_target_dir == "bearish" and _bias == "bullish")
            )

            _llm_conf = float(_dec.get("confidence", 0.5))
            if _llm_conf > 1:
                _llm_conf /= 100.0

            # 2026-06-18: AI 主驾改造。原 FanOut 把 AI 置信度与编排器置信度按
            # 0.35/0.65 混合稀释（AI 只占 35%），导致 AI 75% 信心进风控变成 ~55%。
            # 现：保留 AI 原始置信度（_llm_conf），编排器置信度降为旁路参考（写入溯源字段
            # _orch_conf_note 供日志，不参与 confidence 数值）。
            # 方向性 veto（强反向 SKIP）保留 —— 那是硬安全网，不是数值改写。
            if _same_dir and _conf >= 0.20:
                _blended = _llm_conf  # AI 主驾，不再稀释
                _branch = "match"
            elif _same_dir or _bias == "neutral":
                _blended = _llm_conf  # AI 主驾
                _branch = "soft"
            elif _weak_oppose:
                logger.info(
                    f"[FanOut] {_sym}[{_tier}]: skip(温和反向 bias={_bias} conf={_conf:.2f})"
                )
                continue
            else:
                _oppose_side = (
                    "bearish" if _target_dir == "bullish" else "bullish"
                )
                if _bias in (f"strongly_{_oppose_side}", _oppose_side) and _conf < 0.30:
                    logger.info(
                        f"[FanOut] {_sym}[{_tier}]: skip(反向 bias={_bias} conf={_conf:.2f})"
                    )
                    continue
                _blended = _llm_conf  # AI 主驾
                _branch = "other"

            _blended_pct = round(max(5.0, _blended * 100), 1)

            _td = dict(_dec)
            _td["_orchestrator"] = _orch  # 注入编排器数据，供 save_ai_decision fallback 使用
            _td["trade_nature"] = _fan_tier_nature.get(_tier, "swing")
            # 优先使用编排器推荐的 nature（保留 scalp 等细分分类），
            # 仅当 recommended_nature 映射到的 tier 与当前扇出 tier 一致时覆盖
            _orch_rec_nature = (_orch.get("recommended_nature") or "").strip()
            _orch_rec_tier = host.nature_to_tier_map.get(_orch_rec_nature, "")
            if _orch_rec_tier == _tier and _orch_rec_nature in ("scalp", "intraday", "swing", "trend_follow", "position"):
                _td["trade_nature"] = _orch_rec_nature
            # AI 自动选币强制隔离：只进短线 scalp(中长线升级后 swing/intraday 已废)
            _final_tier = _tier
            try:
                from backend.services.auto_coin_selector import get_fixed_symbols_for_session
                from backend.config.settings import AUTO_COIN_ALLOWED_NATURES
                _sid = getattr(session, "session_id", None)
                _is_ai_coin = bool(_sid) and _sym not in get_fixed_symbols_for_session(_sid)
                if _is_ai_coin:
                    if _td.get("trade_nature") not in AUTO_COIN_ALLOWED_NATURES:
                        _old_n = _td.get("trade_nature")
                        _td["trade_nature"] = "scalp"
                        logger.info(
                            f"[FanOut] {_sym}[{_tier}]: auto_coin force nature "
                            f"{_old_n}→scalp (只允许短线)"
                        )
                    # [中长线升级同步] auto-coin tier 强制降为 short
                    if _tier != "short":
                        _final_tier = "short"
                        logger.info(f"[FanOut] {_sym}[{_tier}→short]: auto_coin tier 降级")
            except ImportError:
                pass
            _td["tier"] = _final_tier
            _td["_fan_out"] = True
            _td["_fan_tier"] = _final_tier
            _td["_fan_orch_conf"] = _conf
            _td["_fan_branch"] = _branch
            _td["confidence"] = _blended_pct
            # 溯源：记录编排器置信度供日志/归因，但不改写 AI 置信度
            _td["_orch_conf_note"] = f"orch_conf={_conf:.2f}(branch={_branch},未稀释AI置信度)"

            _expanded.append(_td)
            _fanned_any = True
            logger.info(
                f"[FanOut] {_sym}[{_tier}]: {_branch} bias={_bias} orch_conf={_conf:.2f} "
                f"-> {_op} confidence={_blended_pct:.1f}%(AI主驾,orch未稀释) "
                f"nature={_td['trade_nature']}"
            )

        if not _fanned_any:
            logger.info(
                f"[FanOut] {_sym}: 所有tier被反向跳过，不产出原始决策"
            )

    _fan_count = sum(1 for d in _expanded if d.get("_fan_out"))
    if _fan_count > 0:
        host.append_event(session, "fan_out",
            f"多周期扇出: {_fan_count} 个tier决策")
    logger.info(f"[FanOut] 扇出完成: {len(_expanded)} 个决策(其中扇出{_fan_count}个)")
    return _expanded
