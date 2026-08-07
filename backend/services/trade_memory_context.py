"""
交易记忆上下文 — 为 Master LLM 决策注入「逐笔战绩 + 连亏状态 + 亏损教训」

设计动机（2026-06 审查结论）：
  Master LLM 的 prompt 此前只有聚合胜率（session 级），看不到
  「最近 10 笔在哪个币亏了多少、为什么平仓」，刚连亏 3 笔下一轮也不知道 —
  等于失忆开单。本模块从 strategy_trades 取逐笔记录，让 AI 带着记忆决策。

理论依据：
  - Reflexion (NeurIPS 2023)：把环境反馈转成文字注入下一轮上下文，
    等效「口头强化学习」，不需要微调模型
  - FinMem (AAAI 2024)：分层记忆 — 重大亏损教训放深层（衰减慢），
    日常信息放浅层（衰减快）

注意：rebate_% 开头的策略（套利中心积分单）单独走套利上下文，
      不注入主 AI，避免 S8 刷积分仓位干扰方向交易判断。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 深层教训判定阈值：单笔亏损占账户权益比例超过该值视为「重大亏损」
DEEP_LESSON_LOSS_PCT_OF_EQUITY = 0.02
# 浅层教训保留天数（FinMem 浅层衰减）
SHALLOW_LESSON_TTL_DAYS = 30
# 深层教训保留天数（接近不衰减）
DEEP_LESSON_TTL_DAYS = 365


# 连亏战绩的时间窗口：只看最近 N 小时的平仓交易。
# 超过该窗口的旧亏损不再约束当前决策——行情已变，陈旧连亏会误导 AI 拒绝本应开仓的机会。
RECENT_TRADES_WINDOW_HOURS = 48

# agent_focus / nature 过滤别名（decision_context.nature 落库值）
NATURE_FILTER_GROUPS = {
    "swing": frozenset({"swing"}),
    "trend": frozenset({"trend_follow", "position"}),
    "intraday": frozenset({"intraday", "scalp"}),
}


def _trade_nature_of(trade) -> str:
    """从 decision_context 读取 trade_nature（StrategyTrade 无独立列）。"""
    ctx = trade.decision_context if isinstance(getattr(trade, "decision_context", None), dict) else {}
    return (ctx.get("nature") or ctx.get("trade_nature") or "").strip().lower()


def _matches_nature_filter(trade, nature: str) -> bool:
    if not nature:
        return True
    allowed = NATURE_FILTER_GROUPS.get(nature, frozenset({nature.lower()}))
    return _trade_nature_of(trade) in allowed


def _fetch_recent_closed_trades(
    db,
    limit: int = 50,
    *,
    window_hours: int = RECENT_TRADES_WINDOW_HOURS,
    nature: str = None,
) -> List:
    """取最近平仓的主交易记录（排除套利中心 rebate_% 策略）。

    时间窗口控制：默认只取最近 window_hours 小时内的平仓，避免把几天前的旧亏损
    当成"当前连亏"喂给 LLM（导致 AI 在新行情下仍被陈旧记忆束缚、不敢开仓）。
    窗口内不足时回退取最近 limit 笔（保证至少有战绩可参考）。
    nature: 可选过滤 swing/trend/intraday（读 decision_context.nature）。
    """
    from backend.database.models import StrategyTrade

    _fetch_limit = max(limit * 4, 50) if nature else limit
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    trades = (
        db.query(StrategyTrade)
        .filter(
            StrategyTrade.status == "closed",
            ~StrategyTrade.strategy_id.like("rebate_%"),
            StrategyTrade.closed_at >= cutoff,
        )
        .order_by(StrategyTrade.closed_at.desc())
        .limit(_fetch_limit)
        .all()
    )
    # 窗口内无交易时回退：取最近 limit 笔（不卡时间），保证 AI 有战绩可看
    if not trades:
        trades = (
            db.query(StrategyTrade)
            .filter(
                StrategyTrade.status == "closed",
                ~StrategyTrade.strategy_id.like("rebate_%"),
            )
            .order_by(StrategyTrade.closed_at.desc())
            .limit(_fetch_limit)
            .all()
        )
        if trades:
            logger.info(
                "[TradeMemoryContext] 最近%dh内无平仓记录，回退取最近%d笔(最新:%s)",
                window_hours, len(trades), trades[0].closed_at,
            )
    if nature:
        trades = [t for t in trades if _matches_nature_filter(t, nature)]
    return trades[:limit]


def _close_reason_of(trade) -> str:
    ctx = trade.decision_context if isinstance(trade.decision_context, dict) else {}
    reason = ctx.get("close_reason") or trade.ai_reasoning or ""
    return str(reason)[:40]


def _holding_str(seconds) -> str:
    try:
        s = int(seconds or 0)
    except Exception:
        return "?"
    if s < 3600:
        return f"{s // 60}分钟"
    return f"{s / 3600:.1f}小时"


def compute_symbol_loss_streaks(trades: List) -> Dict[str, Tuple[int, float]]:
    """
    按 symbol 计算「最近连续亏损」状态。

    Returns: {symbol: (连亏笔数, 连亏累计金额)} — 只包含连亏 >= 2 的 symbol。
    trades 须按 closed_at 倒序（最新在前）。
    """
    streaks: Dict[str, Tuple[int, float]] = {}
    seen_break: set = set()
    acc: Dict[str, Tuple[int, float]] = {}
    for t in trades:
        sym = (t.symbol or "").upper()
        if not sym or sym in seen_break:
            continue
        pnl = float(t.pnl or 0)
        if pnl < 0:
            cnt, total = acc.get(sym, (0, 0.0))
            acc[sym] = (cnt + 1, total + pnl)
        else:
            # 最近一笔盈利 → 该 symbol 连亏链终止
            seen_break.add(sym)
    for sym, (cnt, total) in acc.items():
        if cnt >= 2:
            streaks[sym] = (cnt, total)
    return streaks


# 连亏「新鲜」阈值：距今 <= 该小时数的连亏才作为强约束，超过则降级为历史参考。
# 行情变化快，超过此阈值的旧连亏不应再无差别压制当前开仓决策。
STREAK_FRESH_HOURS = 12


def _trade_age_hours(closed_at, now: datetime) -> str:
    """返回交易距今的人类可读时长，注入 prompt 让 LLM 感知时效。"""
    if not closed_at:
        return ""
    try:
        ts = closed_at if closed_at.tzinfo else closed_at.replace(tzinfo=timezone.utc)
        delta = now - ts
        h = delta.total_seconds() / 3600
    except Exception:
        return ""
    if h < 1:
        return f"{int(delta.total_seconds()//60)}分钟前"
    if h < 24:
        return f"{h:.1f}h前"
    return f"{h/24:.1f}天前"


def _split_streaks_by_freshness(trades: List, streaks: Dict[str, Tuple[int, float]], now: datetime) -> Tuple[Dict, Dict]:
    """把连亏结果按时效分成「近期强约束」和「历史参考」两组。

    判定依据：该 symbol 连亏链中最近一笔亏损的平仓时间距今是否 <= STREAK_FRESH_HOURS。
    trades 须按 closed_at 倒序（最新在前）。
    """
    if not streaks:
        return {}, {}
    # 取每个 symbol 在连亏链中最近一笔的时间
    latest_loss_ts: Dict[str, datetime] = {}
    for t in trades:
        sym = (t.symbol or "").upper()
        if sym not in streaks or sym in latest_loss_ts:
            continue
        if float(t.pnl or 0) < 0 and t.closed_at:
            try:
                latest_loss_ts[sym] = t.closed_at if t.closed_at.tzinfo else t.closed_at.replace(tzinfo=timezone.utc)
            except Exception:
                pass

    fresh, stale = {}, {}
    cutoff = now - timedelta(hours=STREAK_FRESH_HOURS)
    for sym, payload in streaks.items():
        ts = latest_loss_ts.get(sym)
        if ts and ts >= cutoff:
            fresh[sym] = payload
        else:
            stale[sym] = payload
    return fresh, stale


def build_recent_trades_section(db, limit: int = 15, *, nature: str = None) -> str:
    """
    构建「最近逐笔战绩」prompt 段（M1）。

    nature: 可选 swing/trend/intraday，只注入同 nature 战绩（Agent 分库记忆）。
    """
    try:
        trades = _fetch_recent_closed_trades(db, limit=max(limit, 30), nature=nature)
        if not trades:
            return ""

        recent = trades[:limit]
        wins = sum(1 for t in recent if float(t.pnl or 0) > 0)
        net = sum(float(t.pnl or 0) for t in recent)

        _nature_label = {
            "swing": "中线(swing)",
            "trend": "长线(trend_follow/position)",
            "intraday": "短线(intraday/scalp)",
        }.get(nature or "", "")
        _header = "### 🧾 你自己的最近战绩（逐笔，最新在前 — 这是你过去决策的真实结果）"
        if _nature_label:
            _header += f" — **仅{_nature_label}**"

        lines = [
            _header,
            f"- 近 {len(recent)} 笔: 胜 {wins} 负 {len(recent) - wins} "
            f"(胜率 {wins / len(recent) * 100:.0f}%) | 净盈亏 ${net:+,.2f}",
        ]
        _now = datetime.now(timezone.utc)
        for t in recent:
            pnl = float(t.pnl or 0)
            pnl_pct = float(t.pnl_pct or 0)
            icon = "✅" if pnl > 0 else "❌"
            # [fix] 标注每笔距今多久，让 LLM 自行判断时效性
            age_h = _trade_age_hours(t.closed_at, _now)
            _tn = _trade_nature_of(t) or "?"
            lines.append(
                f"  {icon} {t.symbol} {t.side} [{_tn}] PnL=${pnl:+.2f}({pnl_pct:+.1f}%) "
                f"持仓{_holding_str(t.holding_period)} {age_h} "
                f"平仓原因={_close_reason_of(t)}"
            )

        # 连亏警告分级：近期(<=STREAK_FRESH_HOURS)强警告，陈旧降级为参考
        streaks = compute_symbol_loss_streaks(trades)
        fresh_streaks, stale_streaks = _split_streaks_by_freshness(trades, streaks, _now)
        if fresh_streaks:
            lines.append("")
            lines.append(f"⚠️ **近期连亏警告（{STREAK_FRESH_HOURS}h内连续做错，再开仓必须给出与之前不同的关键证据）**:")
            for sym, (cnt, total) in sorted(fresh_streaks.items(), key=lambda kv: kv[1][1]):
                lines.append(f"  - {sym}: 连亏 {cnt} 笔，累计 ${total:+,.2f}")
        if stale_streaks:
            lines.append("")
            lines.append(f"ℹ️ **历史连亏参考（超过{STREAK_FRESH_HOURS}h，行情已变，仅供参考不再强制约束）**:")
            for sym, (cnt, total) in sorted(stale_streaks.items(), key=lambda kv: kv[1][1]):
                lines.append(f"  - {sym}: 曾连亏 {cnt} 笔 ${total:+,.2f}（已陈旧）")

        lines.append(
            "\n📌 决策要求：开仓前先对照上面战绩 — 同币种/同方向「近期」刚亏过的，"
            "说明你近期的判断依据失效了，必须在 reasoning 中说明这次有什么不同；"
            "但「历史」连亏行情已变，不应作为拒绝开仓的唯一理由。"
        )
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"[TradeMemoryContext] 逐笔战绩注入失败(跳过): {exc}")
        return ""


# ══════════════════════════════════════════════════
#  M2: Reflexion 亏损教训（分层记忆）
# ══════════════════════════════════════════════════

def store_loss_lesson(
    db,
    *,
    strategy_id: str,
    symbol: str,
    side: str,
    pnl: float,
    lesson_text: str,
    account_equity: float = 0.0,
    regime: str = "",
) -> None:
    """
    把一条亏损教训写入 StrategyMemory.key_lessons（结构化条目）。

    分层规则（FinMem）：
      - 单笔亏损 > 账户权益 2% → layer="deep"（保留 365 天，排序权重高）
      - 其他 → layer="shallow"（30 天后检索时过滤）
    """
    try:
        from backend.database.models import StrategyMemory

        # [2026-07-11 修复] strategy_memories.strategy_id 是 ai_strategies.strategy_id 的
        # 外键。原逻辑直接用传入的 strategy_id 建行，系统策略(scalp_router/cross_cycle_*等)
        # 或已删除策略会触发 FK 违例，被下面的 except 静默吞掉——教训看似"存了"，实际每次
        # 都失败回滚，日志里只有一句 warning。复用与 evolution_scheduler 一致的解析逻辑。
        from backend.services.unified_learning_service import unified_learning
        resolved_sid = unified_learning._resolve_strategy_id_for_fk(db, strategy_id)
        if not resolved_sid:
            logger.debug(
                f"[TradeMemoryContext] 跳过Reflexion教训存储: strategy_id={str(strategy_id)[:20]} "
                f"不在ai_strategies中且非已知系统策略前缀"
            )
            return
        strategy_id = resolved_sid

        if account_equity > 0:
            is_deep = abs(pnl) / account_equity >= DEEP_LESSON_LOSS_PCT_OF_EQUITY
        else:
            # 无权益信息时用绝对额兜底：单笔亏 $100+ 视为重大教训
            is_deep = abs(pnl) >= 100.0
        layer = "deep" if is_deep else "shallow"
        entry = {
            "type": "reflexion",
            "layer": layer,
            "symbol": (symbol or "").upper(),
            "side": side,
            "pnl": round(float(pnl), 2),
            "regime": regime or "",
            "lesson": str(lesson_text)[:300],
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        mem = (
            db.query(StrategyMemory)
            .filter(StrategyMemory.strategy_id == strategy_id)
            .first()
        )
        if mem is None:
            mem = StrategyMemory(strategy_id=strategy_id, key_lessons=[entry])
            db.add(mem)
        else:
            lessons = list(mem.key_lessons or [])
            lessons.append(entry)
            # 容量控制：deep 全保留，shallow 只留最近 30 条
            deep = [l for l in lessons if isinstance(l, dict) and l.get("layer") == "deep"]
            shallow = [l for l in lessons if not (isinstance(l, dict) and l.get("layer") == "deep")]
            mem.key_lessons = deep[-50:] + shallow[-30:]
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(mem, "key_lessons")
        db.commit()
        logger.info(
            f"[TradeMemoryContext] Reflexion 教训已存({layer}): "
            f"{strategy_id}/{symbol} pnl={pnl:+.2f}"
        )
    except Exception as exc:
        logger.warning(f"[TradeMemoryContext] 教训存储失败: {exc}")
        try:
            db.rollback()
        except Exception:
            pass


def _lesson_fresh(entry: dict, now: datetime) -> bool:
    """FinMem 衰减：浅层教训 30 天过期，深层 365 天。"""
    ts_raw = entry.get("ts")
    if not ts_raw:
        return True
    try:
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except Exception:
        return True
    ttl_days = DEEP_LESSON_TTL_DAYS if entry.get("layer") == "deep" else SHALLOW_LESSON_TTL_DAYS
    return (now - ts) <= timedelta(days=ttl_days)


def generate_loss_reflection_async(
    *,
    strategy_id: str,
    symbol: str,
    side: str,
    pnl: float,
    pnl_pct: float,
    exit_reason: str = "",
    regime: str = "",
    duration_seconds: int = 0,
    confidence: float = 0.0,
    account_equity: float = 0.0,
) -> None:
    """
    Reflexion 入口：平仓亏损后在后台线程生成一句结构化教训并入库。

    - 用 LLM 做反思（短超时 45s），失败降级为规则文本（保证教训必定入库）
    - 后台 daemon 线程执行，绝不阻塞平仓主路径
    """
    import threading

    def _worker():
        lesson = ""
        try:
            from backend.services.llm_config_service import (
                call_llm_api_sync,
                get_llm_config_for_analysis,
            )

            cfg = get_llm_config_for_analysis()
            if cfg is not None:
                hold = _holding_str(duration_seconds)
                prompt = (
                    f"你是交易复盘助手。一笔交易亏损了，请用一句话（<=60字）总结教训，"
                    f"必须指出错在哪一环：方向判断/入场时机/止损设置/仓位过大/持仓过久。"
                    f"只输出教训本身，不要客套。\n"
                    f"交易：{symbol} {side}，亏损 ${abs(pnl):.2f}（{pnl_pct:+.1f}%），"
                    f"持仓{hold}，开仓置信度{confidence:.0f}%，"
                    f"市场状态={regime or '未知'}，平仓原因={exit_reason or '未知'}"
                )
                resp = call_llm_api_sync(
                    cfg,
                    [{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=200,
                    timeout=45,
                    caller="trade_reflexion",
                )
                if resp:
                    content = (
                        resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                        if isinstance(resp, dict) else ""
                    )
                    lesson = str(content).strip()[:200]
        except Exception as exc:
            logger.debug(f"[TradeMemoryContext] LLM 反思失败，用规则文本兜底: {exc}")

        if not lesson:
            # 规则兜底：没有 LLM 也要留下可检索的教训
            hold = _holding_str(duration_seconds)
            lesson = (
                f"{symbol} {side} 亏${abs(pnl):.0f}({pnl_pct:+.1f}%) "
                f"持仓{hold} 平仓={exit_reason or '?'} regime={regime or '?'} "
                f"— 同条件再开仓需要新证据"
            )

        try:
            from backend.database.connection import SessionLocal

            db = SessionLocal()
            try:
                store_loss_lesson(
                    db,
                    strategy_id=strategy_id,
                    symbol=symbol,
                    side=side,
                    pnl=pnl,
                    lesson_text=lesson,
                    account_equity=account_equity,
                    regime=regime,
                )

                # Phase 5: 条件触发 OpenCode 深度复盘（异步，不阻塞主路径）
                _should_deep = abs(pnl) >= 50.0 or _recent_win_rate_dropped(db, strategy_id)
                if _should_deep:
                    try:
                        _trigger_opencode_deep_review(
                            strategy_id=strategy_id,
                            symbol=symbol,
                            side=side,
                            pnl=pnl,
                            pnl_pct=pnl_pct,
                            exit_reason=exit_reason,
                            regime=regime,
                            duration_seconds=duration_seconds,
                            confidence=confidence,
                        )
                    except Exception as _dr_err:
                        logger.debug(f"[TradeMemoryContext] OpenCode深度复盘触发失败: {_dr_err}")
            finally:
                db.close()
        except Exception as exc:
            logger.warning(f"[TradeMemoryContext] 反思入库失败: {exc}")

    threading.Thread(target=_worker, daemon=True, name="trade-reflexion").start()


def build_loss_lessons_section(
    db, symbols: Optional[List[str]] = None, regime: str = "", limit: int = 8
) -> str:
    """
    检索与当前 symbol/regime 匹配的 Reflexion 教训，构建 prompt 段（M2）。

    优先级：deep 层 > 匹配 symbol > 匹配 regime > 时间新。
    """
    try:
        from backend.database.models import StrategyMemory

        mems = db.query(StrategyMemory).filter(
            StrategyMemory.key_lessons.isnot(None)
        ).all()
        now = datetime.now(timezone.utc)
        sym_set = {s.upper() for s in (symbols or [])}

        candidates: List[Tuple[float, dict]] = []
        for mem in mems:
            for entry in (mem.key_lessons or []):
                if not isinstance(entry, dict) or entry.get("type") != "reflexion":
                    continue
                if not _lesson_fresh(entry, now):
                    continue
                score = 0.0
                if entry.get("layer") == "deep":
                    score += 10.0
                if sym_set and entry.get("symbol") in sym_set:
                    score += 5.0
                if regime and entry.get("regime") == regime:
                    score += 2.0
                try:
                    ts = datetime.fromisoformat(str(entry.get("ts")).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    age_days = max((now - ts).total_seconds() / 86400, 0)
                    score += max(0.0, 3.0 - age_days / 10)
                except Exception:
                    pass
                candidates.append((score, entry))

        qaa_section = ""
        try:
            from backend.services.qaa_trade_memory_bridge import build_qaa_rag_lessons_section

            qaa_section = build_qaa_rag_lessons_section(
                symbols=symbols,
                regime=regime,
                limit=min(5, max(1, limit)),
            )
        except Exception as qaa_err:
            logger.debug(f"[TradeMemoryContext] QAA RAG 教训检索跳过: {qaa_err}")

        if not candidates:
            return qaa_section
        candidates.sort(key=lambda x: x[0], reverse=True)

        lines = ["### 💉 亏损教训（来自你过去的真实亏损反思，违反需在 reasoning 中说明理由）"]
        for _, e in candidates[:limit]:
            tag = "🩸深刻" if e.get("layer") == "deep" else "·"
            lines.append(
                f"  {tag} [{e.get('symbol', '?')} {e.get('side', '?')} "
                f"${float(e.get('pnl') or 0):+.0f}] {e.get('lesson', '')}"
            )
        if qaa_section:
            lines.extend(["", qaa_section])
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"[TradeMemoryContext] 教训检索失败(跳过): {exc}")
        return ""


# ══════════════════════════════════════════════════════
#  Phase 5: 胜率骤降检测 + OpenCode 深度复盘触发器（异步）
# ══════════════════════════════════════════════════════

def _recent_win_rate_dropped(db, strategy_id: str) -> bool:
    """
    检查最近5笔交易中是否胜率骤降（赢≤1笔）。
    用于判定是否需要触发 OpenCode 深度复盘。
    """
    try:
        from backend.database.models import StrategyTrade
        recent_5 = (
            db.query(StrategyTrade)
            .filter(
                StrategyTrade.strategy_id == strategy_id,
                StrategyTrade.status == "closed",
            )
            .order_by(StrategyTrade.closed_at.desc())
            .limit(5)
            .all()
        )
        if len(recent_5) < 5:
            return False
        wins = sum(1 for t in recent_5 if (t.pnl or 0) > 0)
        if wins <= 1:
            logger.info(
                f"[TradeMemoryContext] {strategy_id} 最近5笔仅{wins}胜，触发深度复盘"
            )
            return True
        return False
    except Exception as _rr_err:
        logger.debug(f"[TradeMemoryContext] 胜率检查跳过: {_rr_err}")
        return False


def _trigger_opencode_deep_review(
    *,
    strategy_id: str,
    symbol: str,
    side: str,
    pnl: float,
    pnl_pct: float,
    exit_reason: str = "",
    regime: str = "",
    duration_seconds: int = 0,
    confidence: float = 0.0,
) -> None:
    """
    异步触发 OpenCode 单笔交易深度复盘。
    - 在独立 daemon 线程中运行，不阻塞 Reflexion 主路径
    - 调用 OpenCode plan agent 做深度归因分析
    - 结果通过 MasterController.inject_opencode_lesson() 注入到实时缓存
    """
    import threading

    def _deep_worker():
        try:
            from backend.services.opencode_bridge import _is_enabled, run_http_agent_message
            if not _is_enabled():
                logger.debug("[OpenCodeDeepReview] OpenCode 未启用，跳过深度复盘")
                return

            prompt_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "prompts", "opencode_per_trade_review_system.md",
            )
            system_prompt = ""
            if os.path.isfile(prompt_path):
                with open(prompt_path, encoding="utf-8") as f:
                    system_prompt = f.read()

            hold = _holding_str(duration_seconds)
            user_prompt = (
                f"## 单笔交易深度复盘\n\n"
                f"- Symbol: {symbol}\n"
                f"- Side: {side}\n"
                f"- PnL: ${pnl:.2f} ({pnl_pct:+.2f}%)\n"
                f"- 持仓时长: {hold}\n"
                f"- 开仓置信度: {confidence:.0f}%\n"
                f"- 平仓原因: {exit_reason or '未知'}\n"
                f"- 市场状态: {regime or '未知'}\n"
                f"- 策略ID: {strategy_id}\n"
            )

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": user_prompt})

            agent = os.getenv("OPENCODE_AGENT_PLAN", "plan")
            result = run_http_agent_message(messages, agent=agent, timeout=120)

            if result and isinstance(result, dict):
                lesson_text = str(result.get("lesson") or result.get("root_cause") or "")
                mistake_cat = str(result.get("mistake_category") or "")
                if lesson_text:
                    from backend.services.trading_analysts import MasterController
                    MasterController.inject_opencode_lesson({
                        "symbol": symbol,
                        "side": side,
                        "pnl": pnl,
                        "root_cause": result.get("root_cause", "?"),
                        "mistake_category": mistake_cat,
                        "lesson": lesson_text[:200],
                        "confidence": float(result.get("confidence") or 0),
                        "strategy_id": strategy_id,
                    })
                    logger.info(
                        f"[OpenCodeDeepReview] 深度复盘完成: {symbol} "
                        f"归因={result.get('root_cause', '?')} 教训={lesson_text[:60]}"
                    )
        except Exception as exc:
            logger.debug(f"[OpenCodeDeepReview] 深度复盘异常(非致命): {exc}")

    threading.Thread(
        target=_deep_worker, daemon=True, name="opencode-deep-review"
    ).start()


# ══════════════════════════════════════════════════════
#  P0.3: 即时教训闭环升级 — 加密市场增强
# ══════════════════════════════════════════════════════

def build_recent_trades_section_v2(
    db, limit: int = 15, *, include_crypto_context: bool = True
) -> str:
    """P0.3 增强版 M1 逐笔战绩：追加加密市场特有上下文。

    与 v1 的区别：
    - 追加资金费率极端提醒（|funding_rate|>0.05% 的交易高亮）
    - 追加周末低流动性警告
    - 追加 BTC 联动风险提示
    - 追加爆仓级联警告
    """
    # 基础战绩段（复用 v1）
    base = build_recent_trades_section(db, limit=limit)
    if not base:
        return ""

    if not include_crypto_context:
        return base

    try:
        trades = _fetch_recent_closed_trades(db, limit=max(limit, 30))
        if not trades:
            return base

        recent = trades[:limit]

        # 加密专有上下文
        crypto_warnings = []

        # 1. 资金费率极端提醒
        extreme_funding_trades = []
        for t in recent:
            dc = t.decision_context if isinstance(t.decision_context, dict) else {}
            fp = dc.get("fingerprint_at_entry", {}) or {}
            fr = fp.get("funding_rate", 0)
            if abs(fr) > 0.0005:  # |rate| > 0.05%
                extreme_funding_trades.append((t, fr))

        if extreme_funding_trades:
            crypto_warnings.append(
                "🔴 **资金费率极端警告**: 以下交易入场时费率异常，"
                "高费率=反向开仓成本高+与全市场反向"
            )
            for t, fr in extreme_funding_trades[:3]:
                direction = "正(做多拥挤)" if fr > 0 else "负(做空拥挤)"
                crypto_warnings.append(
                    f"  - {t.symbol} {t.side} 费率={fr*100:.3f}% {direction} "
                    f"PnL=${float(t.pnl or 0):+.2f}"
                )

        # 2. 周末交易表现
        weekend_trades = []
        for t in recent:
            if t.closed_at and t.closed_at.weekday() >= 5:
                weekend_trades.append(t)

        if weekend_trades and len(weekend_trades) >= 3:
            weekend_wins = sum(1 for t in weekend_trades if (t.pnl or 0) > 0)
            weekend_pnl = sum(float(t.pnl or 0) for t in weekend_trades)
            weekend_wr = weekend_wins / len(weekend_trades)
            crypto_warnings.append(
                f"⚠️ **周末交易**: {len(weekend_trades)}笔 胜率{weekend_wr:.0%} "
                f"累计${weekend_pnl:+.2f} — 低流动性环境下信号可靠性降低"
            )

        # 3. 爆仓级联检测
        liquidation_trades = []
        for t in recent:
            dc = t.decision_context if isinstance(t.decision_context, dict) else {}
            fp = dc.get("fingerprint_at_entry", {}) or {}
            liq_imb = fp.get("liquidation_imbalance", 0)
            if abs(liq_imb) > 0.6:
                liquidation_trades.append(t)

        if liquidation_trades:
            crypto_warnings.append(
                "⚠️ **爆仓级联风险**: 以下交易入场时市场出现单边爆仓（"
                "多空爆仓比例严重失衡），短期反转概率高"
            )
            for t in liquidation_trades[:2]:
                crypto_warnings.append(
                    f"  - {t.symbol} {t.side} PnL=${float(t.pnl or 0):+.2f}"
                )

        if crypto_warnings:
            return base + "\n\n" + "\n".join(crypto_warnings)
        return base

    except Exception as exc:
        logger.warning(f"[TradeMemoryContext] 加密上下文注入失败(跳过): {exc}")
        return base


def store_loss_lesson_v2(
    db,
    *,
    strategy_id: str,
    symbol: str,
    side: str,
    pnl: float,
    lesson_text: str,
    account_equity: float = 0.0,
    regime: str = "",
    # P0.3 新增：事件绑定 + Hebbian 衰减
    condition_tags: Optional[List[str]] = None,
    fingerprint: Optional[Dict] = None,
) -> None:
    """P0.3 增强版教训存储：追加事件绑定和 Hebbian 衰减字段。

    与 v1 的区别：
    - condition_tags: 标注这条教训依赖的市场条件（用于后续"事件驱动过期"）
    - recall_count: 被检索命中的次数（命中的越多衰减越慢 -> Hebbian 衰减）
    - fingerprint_snapshot: 入场时刻的加密因子快照
    """
    try:
        from backend.database.models import StrategyMemory

        # [2026-07-11 修复] 同 store_loss_lesson v1：先解析/校验 FK，避免系统策略/已删除
        # 策略的教训被静默丢弃。
        from backend.services.unified_learning_service import unified_learning
        resolved_sid = unified_learning._resolve_strategy_id_for_fk(db, strategy_id)
        if not resolved_sid:
            logger.debug(
                f"[TradeMemoryContext] 跳过Reflexion教训存储(v2): strategy_id={str(strategy_id)[:20]} "
                f"不在ai_strategies中且非已知系统策略前缀"
            )
            return
        strategy_id = resolved_sid

        if account_equity > 0:
            is_deep = abs(pnl) / account_equity >= DEEP_LESSON_LOSS_PCT_OF_EQUITY
        else:
            is_deep = abs(pnl) >= 100.0
        layer = "deep" if is_deep else "shallow"

        # 推断条件标签
        tags = list(condition_tags or [])
        if fingerprint:
            # 从因子数据推断市场条件
            fr = fingerprint.get("funding_rate", 0)
            if abs(fr) > 0.0005:
                tags.append("extreme_funding")
            vol = fingerprint.get("volatility_30d", 0)
            if vol > 0.8:
                tags.append("high_volatility")
            elif vol < 0.3:
                tags.append("low_volatility")
            reg = fingerprint.get("regime_at_entry", "")
            if reg:
                tags.append(f"regime:{reg}")

            # 周末标记
            ts_str = fingerprint.get("timestamp") or fingerprint.get("opened_at")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                    if ts.weekday() >= 5:
                        tags.append("weekend")
                except Exception:
                    pass

        entry = {
            "type": "reflexion",
            "layer": layer,
            "symbol": (symbol or "").upper(),
            "side": side,
            "pnl": round(float(pnl), 2),
            "regime": regime or "",
            "lesson": str(lesson_text)[:300],
            "ts": datetime.now(timezone.utc).isoformat(),
            # P0.3 新增
            "condition_tags": tags[:8],
            "recall_count": 0,            # Hebbian 衰减：命中次数
            "last_recalled_at": None,      # 最后命中时间
            "fingerprint_snapshot": {      # 入场因子快照（轻量）
                k: fingerprint.get(k, 0)
                for k in ("funding_rate", "oi_change_pct", "liquidation_imbalance",
                          "volatility_30d", "regime_at_entry")
            } if fingerprint else {},
        }

        mem = (
            db.query(StrategyMemory)
            .filter(StrategyMemory.strategy_id == strategy_id)
            .first()
        )
        if mem is None:
            mem = StrategyMemory(strategy_id=strategy_id, key_lessons=[entry])
            db.add(mem)
        else:
            lessons = list(mem.key_lessons or [])
            lessons.append(entry)
            deep = [l for l in lessons if isinstance(l, dict) and l.get("layer") == "deep"]
            shallow = [l for l in lessons if not (isinstance(l, dict) and l.get("layer") == "deep")]
            mem.key_lessons = deep[-50:] + shallow[-30:]
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(mem, "key_lessons")
        db.commit()
        logger.info(
            f"[TradeMemoryContext] Reflexion 教训已存({layer}): "
            f"{strategy_id}/{symbol} pnl={pnl:+.2f} tags={tags[:3]}"
        )
    except Exception as exc:
        logger.warning(f"[TradeMemoryContext] 教训存储失败: {exc}")
        try:
            db.rollback()
        except Exception:
            pass


def mark_lesson_recalled(db, strategy_id: str, lesson_index: int) -> None:
    """P0.3 Hebbian 衰减：标记一条教训被检索命中。

    命中的次数越多，衰减越慢 — 模拟"频繁回忆的记忆更持久"。
    由 build_loss_lessons_section 在返回每条教训时调用。
    """
    try:
        from backend.database.models import StrategyMemory
        mem = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == strategy_id
        ).first()
        if not mem or not mem.key_lessons:
            return

        lessons = list(mem.key_lessons)
        if 0 <= lesson_index < len(lessons):
            entry = lessons[lesson_index]
            if isinstance(entry, dict):
                entry["recall_count"] = entry.get("recall_count", 0) + 1
                entry["last_recalled_at"] = datetime.now(timezone.utc).isoformat()
                mem.key_lessons = lessons
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(mem, "key_lessons")
                db.commit()
    except Exception as exc:
        logger.debug(f"[TradeMemoryContext] Hebbian 标记失败: {exc}")


def _calculate_hebbian_decay_multiplier(entry: dict) -> float:
    """P0.3 计算 Hebbian 衰减乘数。

    基于召回次数：
    - 0次召回: 1.0 (正常衰减)
    - 1-2次: 0.7 (衰减减慢30%)
    - 3-5次: 0.5 (衰减减慢50%)
    - 6+次: 0.3 (衰减减慢70%，近乎不衰减)
    """
    recall_count = entry.get("recall_count", 0) if isinstance(entry, dict) else 0
    if recall_count >= 6:
        return 0.3
    elif recall_count >= 3:
        return 0.5
    elif recall_count >= 1:
        return 0.7
    return 1.0


def _check_condition_expired(entry: dict) -> bool:
    """P0.3 事件驱动过期：检查条件标签是否已失效。

    - 如果条件标签包含 'weekend' 且当前非周末 → 不直接过期（周末还会再来）
    - 如果条件标签包含 'extreme_funding' → 检查该因子模式的持久性
    - 其他条件标签默认不过期

    Returns:
        True if the lesson's conditions are expired
    """
    tags = entry.get("condition_tags", []) if isinstance(entry, dict) else []
    if not tags:
        return False

    # 当前不自动过期任何条件标签（由因果发现引擎的 superseded 规则管理）
    # 此方法预留为未来手动/半自动过期使用
    return False
