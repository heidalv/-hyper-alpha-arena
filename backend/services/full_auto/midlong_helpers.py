"""中长线独立 Agent 辅助 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.services.mlto import pnl_basis as _pnl_basis
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _keep0_float(v, default):
    """保留显式 0 的浮点配置读取。

    [2026-09-10 审计轮] 原写法 `float(getattr(settings, X, d) or d)` 会把显式 0 换回默认值，
    使「0 = 关闭该项」的语义静默失效（同类问题见 §38.9/2 的 MIDLONG_MAX_OPEN_POSITIONS）。
    """
    return float(default) if v is None else float(v)


def _keep0_int(v, default):
    """保留显式 0 的整数配置读取（同上）。"""
    return int(default) if v is None else int(v)


#: 真值/假值字符串集合（与项目其它配置解析保持一致）
_TRUTHY = {"1", "true", "yes", "on", "y", "t"}
_FALSY = {"0", "false", "no", "off", "n", "f"}


def _cfg_bool_env(name: str, default: bool) -> bool:
    """读取布尔环境变量：**未设/空串取默认**，无法识别时按默认并告警（不静默）。

    [P12 执行 2026-09-10] 用于 `MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED` 这类"口径开关"：
    写错值（如 `ture`）不得静默按某个方向生效。

    [§68 修复] 初版把空串放进 `_FALSY` ⇒ **未设置时返回 False 而不是默认值**，
    等于把"默认开启"的口径开关静默关掉。空串必须走默认分支。
    """
    raw = (os.getenv(name) or "").strip().lower()
    if raw == "":
        return bool(default)
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    logger.warning("[MidLongCfg] %s=%r 无法识别，按默认 %s 处理", name, raw, default)
    return bool(default)


def build_midlong_health_from_facts(lookback_days: int = 14, account_id: Optional[int] = None) -> Dict[str, Any]:
    """中线/长线健康视图（[2026-08-17] 替代已删除的 midlong_health_report）。

    直接从 trade_facts（真实交易事件流）按 tier 汇总：笔数/胜率/净 PnL。
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text as _sa_text

    from backend.database.connection import SessionLocal

    since = datetime.now(timezone.utc) - timedelta(days=int(lookback_days))
    out: Dict[str, Any] = {
        "lookback_days": int(lookback_days),
        "source": "trade_facts",
        # [P3 执行 2026-09-10] 对外口径改为**净**：`pnl` = 毛 − 手续费（− 资金费，见 pnl_basis）
        "pnl_basis": _pnl_basis.PNL_BASIS,
        "pnl_basis_desc": _pnl_basis.label(),
        "tiers": {},
        "totals": {"trades": 0, "wins": 0, "pnl": 0.0, "pnl_gross": 0.0, "pnl_net": 0.0, "fees": 0.0},
    }
    try:
        with SessionLocal() as db:
            from sqlalchemy import bindparam as _bp, Integer as _Int
            _stmt = _sa_text(
                """
                SELECT tier, COUNT(*) AS n,
                       SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins,
                       SUM(COALESCE(pnl,0)) AS pnl,
                       SUM(COALESCE(fees,0)) AS fees
                FROM trade_facts
                WHERE ts >= :since
                  AND (:acct IS NULL OR account_id = :acct)
                GROUP BY tier
                ORDER BY n DESC
                """
            ).bindparams(
                # [2026-09-01 修复] psycopg3 无法从 None 推断 $2 类型 →
                # AmbiguousParameter。显式 bindparam(Integer) 定型。
                _bp("acct", type_=_Int),
            )
            rows = db.execute(
                _stmt, {"since": since, "acct": account_id},
            ).mappings().all()
        for r in rows:
            n = int(r["n"] or 0)
            w = int(r["wins"] or 0)
            _gross = float(r["pnl"] or 0.0)
            _fees = float(r["fees"] or 0.0)
            _desc = _pnl_basis.describe(_gross, _fees, 0.0)
            out["tiers"][str(r["tier"])] = {
                "trades": n,
                "win_rate": round(w / n, 4) if n else 0.0,
                **_desc,
            }
            out["totals"]["trades"] += n
            out["totals"]["wins"] += w
            out["totals"]["pnl_gross"] = round(out["totals"]["pnl_gross"] + _desc["pnl_gross"], 4)
            out["totals"]["fees"] = round(out["totals"]["fees"] + _desc["fees"], 4)
            out["totals"]["pnl_net"] = round(out["totals"]["pnl_net"] + _desc["pnl_net"], 4)
            out["totals"]["pnl"] = out["totals"]["pnl_net"]
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
    return out


def _clamp_tp_to_tier_max(tp_pct, tier, symbol, action):
    """[P0-1] TP 上限 clamp：复用 mid_long_structure_stop 的 MLTO_*_MAX_TP（long 20% / mid 10%）。

    防止 LLM 拍脑袋设 20%+ 目标（实测 id=2641 TP=+22.4%），全库 peak 上限仅 5.03%，
    超出 tier 上限的 TP 永远触达不了。
    """
    try:
        _key = "LONG" if str(tier or "").lower() == "long" else "MID"
        _max = float(os.getenv(f"MLTO_{_key}_MAX_TP", "0.20" if _key == "LONG" else "0.10"))
        _tp = float(tp_pct or 0)
        if _tp > _max:
            logger.info(
                "[MidLongTPClamp] %s %s tier=%s: tp %.1f%% → %.1f%% (max)",
                symbol, action, tier, _tp * 100, _max * 100,
            )
            return _max
        return _tp
    except Exception as _e:
        logger.debug("[MidLongTPClamp] %s 跳过: %s", symbol, _e)
        return tp_pct


@dataclass
class MidlongHelpersHost:
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    evaluate_and_execute_proposal: Callable = field(repr=False, default=lambda *a, **k: False)


def build_midlong_helpers_host(svc) -> MidlongHelpersHost:
    return MidlongHelpersHost(
        get_trading_account_id=svc._get_trading_account_id,
        append_event=svc._append_event,
        evaluate_and_execute_proposal=svc._evaluate_and_execute_proposal,
    )


# ══════════════════════════════════════════════════════════════════════
# [调研轮16 2026-09-16] **AI 候选的策略按需供给** —— 修「AI 选币 100% 开不了仓」
#
# 事实链（`data/midlong_direction_audit.jsonl` 近 7 天 + DB，session=fa_7e12e7a1b6）：
#   * AI 候选池：mid=['APT','DOT']、long=['FET','APT']；
#   * 策略宇宙：恰好 9 个固定币（BTC,ETH,SOL,BNB,VIRTUAL,ASTER,XPL,UNI,XRP）——
#     **与候选池零交集**；
#   * DOT 25 行、全部 `skip@exec eval_false:no_active_strategy`
#     （`proposal_execution.py:93`，`block_layer=proposal_execution`）：
#     提案在进入**任何风险闸之前**就被丢弃；
#   * 建策略链路 `auto_create_strategy` → `auto_launch_strategy` 与
#     `bg_create_strategy` **全仓无调用点**（后者自带"已废弃，不应被调用"告警）。
# ⇒ 结论："AI 选币"选出来的币在执行层永远找不到 (symbol, tier) 策略行，
#   与论题质量、与任何风险闸都无关 —— 这是**策略供给缺口**，不是风控。
#
# 处置：真的走到开仓时**按需补一条策略行**（克隆同账户同层现役策略的配置，
# 使杠杆/仓位口径/因子集合与现役一致），四重约束：
#   1. `MIDLONG_AI_AUTOCREATE_STRATEGY`（默认 true；false = 完全回滚旧行为）；
#   2. `MIDLONG_AI_AUTOCREATE_MAX_PER_DAY`（默认 3；0 = 关闭）封住扩张速度；
#   3. **只对 AI 候选池内的 symbol 生效**（固定币与其它 symbol 一律不动）；
#   4. 实盘默认关闭（`MIDLONG_AI_AUTOCREATE_LIVE=false`，沿用项目"实盘从严"口径）。
# 风险边界不变：新策略只解锁"允许评估"，开仓仍须过论题 + 全部既有入场闸
# （相关性簇 / 组合预算 / 位置闸 / regime / 持久性…）；异常一律保持历史行为（拒绝）。
# ══════════════════════════════════════════════════════════════════════
_AI_STRAT_ID_PREFIX = "ai_auto_"
_AI_POOL_CACHE: Dict[str, Any] = {"key": "", "ts": 0.0, "syms": frozenset()}


def ai_autocreate_config() -> Dict[str, Any]:
    """按需建策略的配置。

    **以 env 为准**（`.env` 由启动期 load_dotenv 注入 `os.environ`），默认值为字面量
    —— 与本文件既有 `_cfg_bool_env(name, True)` 的约定一致；`settings.py` 里同名声明
    只用于 §73.4「凡环境键必须声明」的可查阅性。

    为什么不把 settings 属性当默认值：settings 在 **import 期**一次性读 env，
    若首次导入发生在 env 被临时改动的上下文（单测、脚本），默认值会被永久污染，
    开关将静默失效 —— 这类"配置了却不生效"正是本项目反复吃亏的形态。
    """
    enabled = _cfg_bool_env("MIDLONG_AI_AUTOCREATE_STRATEGY", True)
    allow_live = _cfg_bool_env("MIDLONG_AI_AUTOCREATE_LIVE", False)
    raw = os.getenv("MIDLONG_AI_AUTOCREATE_MAX_PER_DAY")
    if raw is None or str(raw).strip() == "":
        max_per_day = 3
    else:
        try:
            max_per_day = max(0, int(str(raw).strip()))
        except (TypeError, ValueError):
            logger.warning(
                "[AIStrat] MIDLONG_AI_AUTOCREATE_MAX_PER_DAY=%r 无法识别，按 3 处理", raw)
            max_per_day = 3
    return {"enabled": enabled, "allow_live": allow_live, "max_per_day": max_per_day}


def ai_pool_symbols(db: Session, session, tier: str, *, ttl: float = 60.0) -> set:
    """当前 AI 候选池（mid/long）——与扫描批次同源，带 60s 进程内缓存。

    exec 路径每个 tick 都会问一次，故加短 TTL 缓存；读失败时返回空集
    （fail-closed：不补策略 = 保持历史行为，绝不因本闸异常而多开仓）。
    """
    _tier = (tier or "mid").strip().lower()
    if _tier not in ("mid", "long"):
        return set()
    _sid = str(getattr(session, "session_id", "") or "")
    _key = f"{_sid}:{_tier}"
    _now = time.time()
    if (_AI_POOL_CACHE.get("key") == _key
            and (_now - float(_AI_POOL_CACHE.get("ts") or 0.0)) < ttl):
        return set(_AI_POOL_CACHE.get("syms") or set())
    try:
        from backend.services.auto_coin_selector import (
            get_ai_long_candidates_for_session, get_ai_mid_candidates_for_session,
        )
        _fn = (get_ai_mid_candidates_for_session if _tier == "mid"
               else get_ai_long_candidates_for_session)
        syms = frozenset(str(s).upper() for s in (_fn(_sid, db=db) or []))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AIStrat] AI 候选池读取失败(不补策略): %s", exc)
        return set()
    _AI_POOL_CACHE.update({"key": _key, "ts": _now, "syms": syms})
    return set(syms)


def count_ai_provisioned_today(db: Session) -> int:
    """今日已按需创建的策略数（按命名前缀计数）。**读失败按超限处理**（不建）。"""
    from backend.database.models import AIStrategy as _AIS
    try:
        from sqlalchemy import func as _f

        _start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return int(
            db.query(_f.count(_AIS.id)).filter(
                _AIS.strategy_id.like(f"{_AI_STRAT_ID_PREFIX}%"),
                _AIS.created_at >= _start,
            ).scalar() or 0
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AIStrat] 今日计数失败，按超限处理(不建策略): %s", exc)
        return 10 ** 6


def pick_strategy_donor(db: Session, account_id: int, tier: str):
    """选配置母本：同账户同层 active 策略，优先 full_auto（与本车道一致）。"""
    from backend.database.models import AIStrategy as _AIS

    _q = db.query(_AIS).filter(
        _AIS.account_id == account_id,
        _AIS.timeframe_tier == tier,
        _AIS.status == "active",
        _AIS.primary_symbol.isnot(None),
    )
    try:
        _best = (_q.filter(_AIS.auto_mode == "full_auto")
                 .order_by(_AIS.updated_at.desc()).first())
        if _best is not None:
            return _best
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AIStrat] 母本优选查询失败(回退任意 active): %s", exc)
    try:
        return _q.order_by(_AIS.updated_at.desc()).first()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AIStrat] 母本查询失败: %s", exc)
        return None


def build_cloned_strategy(donor, *, symbol: str, tier: str, account_id: int,
                          session_id: str = ""):
    """克隆母本配置生成新策略行（**未入库**）。字段面 = 下游实际读取面。

    不继承 `genome`：母本 genome 可能内嵌原 symbol 的模板绑定
    （`paper_execution` 会取 `genome["source_template_id"]`），跨 symbol 继承
    语义不成立，故置空并记进 description 以便追溯。
    """
    import uuid

    from backend.database.models import AIStrategy as _AIS

    _sym = str(symbol).upper()
    _sid = f"{_AI_STRAT_ID_PREFIX}{_sym.lower()}{tier}_{uuid.uuid4().hex[:4]}"[:50]

    def _cp(name, default=None):
        return getattr(donor, name, default)

    return _AIS(
        strategy_id=_sid,
        name=f"[AI精选] {_sym} {tier}"[:200],
        description=(
            f"[调研轮16 2026-09-16] AI 选币候选按需建策略：symbol={_sym} tier={tier} "
            f"account={account_id} session={session_id} "
            f"donor={getattr(donor, 'strategy_id', '-')}（克隆其杠杆/仓位/因子口径；"
            f"不继承 genome）"
        )[:2000],
        account_id=account_id,
        primary_symbol=_sym,
        timeframe_tier=tier,
        status="active",
        auto_mode=_cp("auto_mode", "full_auto") or "full_auto",
        max_position_size=_cp("max_position_size"),
        stop_loss_pct=_cp("stop_loss_pct"),
        take_profit_pct=_cp("take_profit_pct"),
        max_leverage=_cp("max_leverage"),
        default_leverage=_cp("default_leverage"),
        leverage_mode=_cp("leverage_mode"),
        enabled_factors=_cp("enabled_factors"),
        factor_weights=_cp("factor_weights"),
        trigger_mode=_cp("trigger_mode"),
        trigger_interval=_cp("trigger_interval"),
        signal_pool_ids=_cp("signal_pool_ids"),
        genome=None,
    )


def provision_ai_strategy(db: Session, session, sym_u: str, tier: str,
                          *, account_id: Optional[int] = None,
                          host: Any = None):
    """AI 候选按需补策略；返回新建的 AIStrategy 或 None（None = 保持历史行为）。"""
    _tier = (tier or "mid").strip().lower()
    if _tier not in ("mid", "long"):
        return None
    _sym = str(sym_u or "").upper()
    if not _sym:
        return None

    cfg = ai_autocreate_config()
    if not cfg["enabled"] or cfg["max_per_day"] <= 0:
        logger.info(
            "[AIStrat] %s tier=%s 未建策略：开关关闭(enabled=%s, max_per_day=%s)",
            _sym, _tier, cfg["enabled"], cfg["max_per_day"])
        return None

    _mode = (getattr(session, "trading_mode", "") or "paper").strip().lower()
    if _mode != "paper" and not cfg["allow_live"]:
        logger.info(
            "[AIStrat] %s tier=%s 未建策略：实盘按需建策略未开启"
            "（MIDLONG_AI_AUTOCREATE_LIVE=false，实盘从严）", _sym, _tier)
        return None

    if _sym not in ai_pool_symbols(db, session, _tier):
        return None  # 只对当前 AI 候选池内的 symbol 生效

    if account_id is None:
        try:
            account_id = (getattr(session, "paper_account_id", None)
                          or getattr(session, "account_id", None))
        except Exception:  # noqa: BLE001
            account_id = None
    if not account_id:
        logger.warning("[AIStrat] %s tier=%s 未建策略：拿不到 account_id", _sym, _tier)
        return None

    _n = count_ai_provisioned_today(db)
    if _n >= cfg["max_per_day"]:
        logger.info(
            "[AIStrat] %s tier=%s 未建策略：今日已达上限 %d/%d",
            _sym, _tier, _n, cfg["max_per_day"])
        return None

    donor = pick_strategy_donor(db, int(account_id), _tier)
    if donor is None:
        logger.warning(
            "[AIStrat] %s tier=%s 未建策略：账户 %s 无同层 active 母本可克隆",
            _sym, _tier, account_id)
        return None

    row = build_cloned_strategy(
        donor, symbol=_sym, tier=_tier, account_id=int(account_id),
        session_id=str(getattr(session, "session_id", "") or ""),
    )
    db.add(row)
    db.flush()

    # 让本会话立即认得它（与 resolve_independent_strategy 的跨账户分支同口径）
    try:
        _ids = list(getattr(session, "active_strategy_ids", None) or [])
        if row.strategy_id not in _ids:
            _ids.append(row.strategy_id)
            session.active_strategy_ids = _ids
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AIStrat] active_strategy_ids 回写失败(非致命): %s", exc)

    logger.warning(
        "[AIStrat] 为 AI 候选按需建策略 %s tier=%s sid=%s (母本=%s, 今日第 %d/%d 个)",
        _sym, _tier, row.strategy_id,
        getattr(donor, "strategy_id", "-"), _n + 1, cfg["max_per_day"],
    )
    try:
        _emit = getattr(host, "append_event", None)
        if callable(_emit):
            _emit(session, "ai_strategy_provisioned",
                  f"🌟 AI 选币 {_sym}[{_tier}] 无可用策略 → 已按需建 "
                  f"{row.strategy_id[:14]}（母本 {str(getattr(donor, 'strategy_id', ''))[:10]}，"
                  f"今日第 {_n + 1}/{cfg['max_per_day']} 个）")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AIStrat] 事件写入失败(非致命): %s", exc)
    return row


def resolve_independent_strategy(
    db: Session, session, sym_u: str, tier: str, host: MidlongHelpersHost,
):
    from backend.database.models import AIStrategy as _AIStrategy

    sym_u = str(sym_u).upper()
    tier = (tier or "mid").lower()
    active_ids = list(getattr(session, "active_strategy_ids", None) or [])
    if active_ids:
        strat = (
            db.query(_AIStrategy)
            .filter(
                _AIStrategy.strategy_id.in_(active_ids),
                _AIStrategy.primary_symbol == sym_u,
                _AIStrategy.timeframe_tier == tier,
                _AIStrategy.status == "active",
            )
            .first()
        )
        if strat:
            return strat

    account_id = host.get_trading_account_id(db, session)
    if account_id:
        strat = (
            db.query(_AIStrategy)
            .filter(
                _AIStrategy.account_id == account_id,
                _AIStrategy.primary_symbol == sym_u,
                _AIStrategy.timeframe_tier == tier,
                _AIStrategy.status == "active",
            )
            .order_by(_AIStrategy.updated_at.desc())
            .first()
        )
        if strat:
            return strat

    # Paper：策略可能落在历史 account_id 上，开单仍走 session.paper_account_id
    _trade_mode = (getattr(session, "trading_mode", "") or "paper").strip().lower()
    if _trade_mode == "paper":
        strat = (
            db.query(_AIStrategy)
            .filter(
                _AIStrategy.primary_symbol == sym_u,
                _AIStrategy.timeframe_tier == tier,
                _AIStrategy.status == "active",
            )
            .order_by(_AIStrategy.updated_at.desc())
            .first()
        )
        if strat:
            logger.info(
                "[Agent独立] %s tier=%s Paper 跨账户策略 %s (strat_acct=%s session_acct=%s)",
                sym_u, tier, (strat.strategy_id or "")[:16],
                getattr(strat, "account_id", "?"), account_id,
            )
            if strat.strategy_id not in active_ids:
                try:
                    active_ids.append(strat.strategy_id)
                    session.active_strategy_ids = active_ids
                except Exception:
                    pass
            return strat

    # [调研轮16 2026-09-16] 最后一道：AI 候选**按需供给策略**。
    # 走到这里说明该 symbol 在本账户/本层没有任何 active 策略；若它正是 AI 选币
    # 选出来的候选（如 DOT/APT/FET），历史上就此被 `no_active_strategy` 永久拒绝
    # （实测 DOT 7 天 25 次全因此被丢弃）。这里补一条克隆策略使其可被评估——
    # 只解锁"允许评估"，入场仍须过论题与全部既有闸门。
    # 异常时**保持历史行为**（返回 None = 仍拒绝），不让本闸成为新的放行口。
    try:
        _prov = provision_ai_strategy(
            db, session, sym_u, tier, account_id=account_id, host=host,
        )
        if _prov is not None:
            return _prov
    except Exception as _prov_err:  # noqa: BLE001
        logger.warning("[AIStrat] %s tier=%s 按需建策略异常(保持拒绝): %s",
                       sym_u, tier, _prov_err)
    return None

def try_execute_independent_agent_open(
    *,
    db: Session,
    session,
    sym: str,
    tier: str,
    action: str,
    confidence: int,
    sl_pct: float = 0.0,
    tp_pct: float = 0.0,
    trade_nature: str,
    market_summary: dict,
    session_mode: str = "running",
    host: MidlongHelpersHost,
    # S2-5 新增：LLM 的 exit_plan（tp_sl_proposal）传入，写入持仓 exit_state_json
    tp_sl_proposal: Optional[Dict] = None,
    invalidation_condition: str = "",
    expected_hold_hours: float = 0.0,
    # [Phase D 修复 Bug1] tranche_gate.compute_margin_pct 计算出的分档保证金比例。
    # < 1.0 时按此比例缩放最终下单 size（乘在 budget/V5Gate/MTF 缩仓之后）。
    # 默认 1.0 = 不缩（向后兼容：未传则保持原行为，整仓下单）。
    tranche_margin_pct: float = 1.0,
    # v6 M6：方向一致性审计（可选）
    thesis_dir: str = "",
    hub_dir: str = "",
    hub_mode: str = "",
    dir_src: str = "",
    authority: str = "",
    # [M1-A 2026-08-21] 入场来源（trend/mlto/factor_route），透传到
    # TradeProposal.extra → 持仓 exit_state_json["entry_source"]，出场分流用。
    entry_source: str = "",
    # [轮146 方案 B] 小仓探针标记：风控官据此不适用"辩论反向否决"（大仓仍适用）
    probe_entry: str = "",
) -> bool:
    from backend.services.decision_core.proposal import TradeProposal

    _sym_u = str(sym).upper()
    _act = (action or "hold").lower()
    _session_id_aud = str(getattr(session, "session_id", "") or "")

    def _audit_skip(_reason: str, *, stage: str = "exec") -> None:
        # [2026-09-18 解冻·选项E] 在执行链的**统一审计出口**登记否决原因，供 brain 的
        # open_execute_false 事件消费（该事件此前完全没有 reason 字段）。
        # 放在这里而不是逐个 `return False`：7 个内部闸（固定币守卫/MTF/冷却/费率/组合/预算/回踩）
        # 全部走本函数，一处即全覆盖。只写审计侧标记，**不改变任何交易判定**。
        try:
            from backend.services.mlto.open_block_reason import (
                mark_open_block,
                remember_open_block,
            )
            mark_open_block(str(_reason or "exec_block"), layer="midlong_helpers")
            # 额外按 (symbol, tier) 留一份：本函数末尾的 record_exec_false_audit() 会 take 掉
            # ContextVar 版，上层 brain 取不到（见 open_block_reason 顶部注释）。
            remember_open_block(_sym_u, str(tier or ""), str(_reason or "exec_block"),
                                layer="midlong_helpers")
        except Exception:
            pass
        try:
            from backend.services.mlto.midlong_direction_audit import (
                record_decision_audit,
            )
            record_decision_audit(
                outcome="skip",
                stage=stage,
                symbol=_sym_u,
                reason=str(_reason or "exec_block")[:160],
                session_id=_session_id_aud,
                tier=str(tier or ""),
                source="exec",
                authority=str(authority or ""),
                action=_act,
                direction=str(hub_dir or ""),
                score=int(confidence or 0),
                mode=str(hub_mode or ""),
            )
        except Exception:
            pass

    # === 固定交易对守卫（阶段0 Task1）：auto-coin 符号绝不能触发长线开仓 ===
    # 长线下单唯一终点（短线 paper_engine.place_order 不经此函数）。
    # tier=long 或 trade_nature ∈ (trend_follow, position) 时，
    # 符号不在 get_fixed_symbols_for_session 正向白名单 → 拒绝开仓。
    # 阶段0 暂不拦截 mid/swing（mid 路径仍在运行），仅守 long/trend/position。
    _tier_l = (tier or "").strip().lower()
    _tn_l = (trade_nature or "").strip().lower()
    # P1：归一保留 swing；禁止把 mid 强制改写成 long（否则 AI 中线撞 FixedSymbolGate）
    try:
        from backend.services.full_auto.midlong_executor import (
            is_midlong_nature,
            normalize_midlong_nature,
        )
        if is_midlong_nature(_tn_l) or _tier_l in ("mid", "long"):
            trade_nature = normalize_midlong_nature(_tn_l, _tier_l)
            _tn_l = trade_nature
            if _tier_l == "mid" or _tn_l == "swing":
                tier = "mid"
                _tier_l = "mid"
            elif _tn_l in ("trend_follow", "position"):
                tier = "long"
                _tier_l = "long"
    except Exception as _norm_err:
        logger.debug("[MidLong] nature 归一跳过: %s", _norm_err)
    # 固定币守卫仅拦长线；中线 AI 选币允许非白名单
    if _tier_l == "long" or _tn_l in ("trend_follow", "position"):
        try:
            from backend.services.auto_coin_selector import get_fixed_symbols_for_session
            _session_id = getattr(session, "session_id", None)
            _fixed = get_fixed_symbols_for_session(_session_id, db, tier="long") if _session_id else set()
            if _fixed and _sym_u not in _fixed:
                logger.warning(
                    "[FixedSymbolGate] auto-coin/非固定符号 %s 在 %s 长线开仓门被拦截 "
                    "(session=%s, tier=%s, trade_nature=%s)",
                    _sym_u, _tier_l or "long", _session_id, _tier_l, _tn_l,
                )
                host.append_event(
                    session, "fixed_symbol_gate_block",
                    f"[固定币守卫] {_sym_u} tier={_tier_l} trade_nature={_tn_l} "
                    f"不在长线白名单，已拒绝开仓",
                )
                _audit_skip(f"fixed_symbol_gate:{_sym_u}")
                return False
        except Exception as _gate_err:
            # 守卫本身异常不应阻断开仓（容错优先）；记录后继续。
            logger.debug("[FixedSymbolGate] %s 守卫检查异常跳过: %s", _sym_u, _gate_err)

    # 开仓前注入多周期指标信封。factor_route 已先注入；LLM 主脑 maybe_open
    # 原先跳过 → open_ready 后被 StrictData 卡死（缺 indicators_1h/4h/1d）。
    # mid/long 统一在此兜底；长线额外要求本币周线（fail-closed，不借大盘）。
    if isinstance(market_summary, dict):
        _want_weekly = (_tier_l == "long") or (str(tier or "").lower() == "long")
        try:
            inject_midlong_indicators(
                market_summary, _sym_u, include_weekly=_want_weekly,
            )
        except Exception as _inj_err:
            logger.debug("[MidLongExec] %s 指标注入跳过: %s", _sym_u, _inj_err)
        _ms = market_summary.get(_sym_u) or {}
        if isinstance(_ms, dict):
            # StrictData 认 price；扫描层常只写 current_price
            if not _ms.get("price") and float(_ms.get("current_price") or 0) > 0:
                _ms["price"] = float(_ms["current_price"])
            # [2026-09-08] StrictData 要求 short 档带 volatility_value；扫描/注入层
            # 对日内档常缺该字段 → V5Gate 卡死（UNI 实证）。缺失时从 1h ATR 兜底计算。
            if not float(_ms.get("volatility_value") or 0):
                try:
                    from backend.services.analysis.context_pack import _atr_pct
                    from backend.services.kline_data_service import kline_service as _ks_vol
                    _kl_vol = _ks_vol.get_aggregated_klines(_sym_u, "1h", count=20)
                    if _kl_vol and len(_kl_vol) >= 15:
                        _atr_v = _atr_pct(_kl_vol, 14)
                        if _atr_v and float(_atr_v) > 0:
                            _ms["volatility_value"] = round(float(_atr_v) / 100.0, 6)
                except Exception:
                    pass
        if _want_weekly and not (_ms.get("indicators_1w") if isinstance(_ms, dict) else None):
            logger.warning(
                "[MidLongExec] %s 本币周线缺失，拒绝长线开仓（fail-closed，不借大盘）",
                _sym_u,
            )
            host.append_event(
                session, "midlong_1w_missing",
                f"[长线1w] {_sym_u} 本币周线缺失，已拒绝开仓",
            )
            _audit_skip("midlong_1w_missing")
            return False

    # 开仓执行前确保 DB 连接健康（防止上游 MLTO LLM 长时间占连接导致事务损坏）
    try:
        from sqlalchemy import text as _sa_text
        db.execute(_sa_text("SELECT 1"))
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

    # ── S1-3 入场多周期一致性约束：逆更高周期强偏向 → 否决/缩仓 ──
    _mtf_size_mult = 1.0
    try:
        from backend.services.decision_core.midlong_mtf_constraint import (
            evaluate_midlong_mtf_constraint,
        )
        _mtf = evaluate_midlong_mtf_constraint(
            symbol=_sym_u,
            tier=(tier or "mid").lower(),
            direction=_act,
            market_data=(market_summary or {}).get(_sym_u) if isinstance(market_summary, dict) else None,
        )
        if _mtf.veto:
            logger.info("[MidLongMTF] BLOCK %s %s: %s", _sym_u, _act, _mtf.reason)
            host.append_event(
                session, "midlong_mtf_block",
                f"[中长线MTF] {_sym_u} {_act} 否决: {_mtf.reason[:120]}",
            )
            _audit_skip(f"midlong_mtf_block:{_mtf.reason}")
            return False
        _mtf_size_mult = float(_mtf.size_multiplier or 1.0)
    except Exception as _mtf_err:
        logger.debug("[MidLongMTF] %s 约束跳过: %s", _sym_u, _mtf_err)

    # ── S0-1 止血修复（R1）：独立路径接入 reentry_cooldown ──
    # 背景：Master 路径开仓前调用 reentry_cooldown.reopen_blocked()，但独立路径
    # （try_execute_independent_agent_open）此前从未调用——同币种同方向平仓后
    # 立即再开，是"开仓→亏损→同向再开→继续亏损"恶性循环的直接代码层根因。
    # 复用现有 reentry_cooldown 模块（tier 隔离 + 连亏倍率 + close_reason 感知），
    # 而非新建独立冷却模块（04 综合方案的"复用现有代码"原则）。
    # Flag: MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE（默认 true，影子模式可关）。
    try:
        from backend.config.settings import MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE
        _cd_enforce = bool(MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE)
    except Exception:
        _cd_enforce = True
    if _cd_enforce and _act in ("buy", "sell"):
        try:
            from backend.services.reentry_cooldown import reopen_blocked
            _account_id_cd = host.get_trading_account_id(db, session)
            if _account_id_cd:
                _tier_cd = (tier or "mid").strip().lower()
                if _tier_cd not in ("short", "mid", "long"):
                    _tier_cd = "mid"
                _blocked, _cd_reason = reopen_blocked(
                    _account_id_cd, _sym_u, _act, _tier_cd,
                )
                if _blocked:
                    logger.info(
                        "[MidLongCooldown] BLOCK %s %s tier=%s: %s",
                        _sym_u, _act, _tier_cd, _cd_reason,
                    )
                    host.append_event(
                        session, "midlong_cooldown_block",
                        f"[中长线冷却] {_sym_u} {_act} tier={_tier_cd}: {_cd_reason}",
                    )
                    # 即使被拦截也持久化决策日志（R8 修复：account_id 兜底落库）
                    try:
                        persist_independent_scan_log(
                            account_id=_account_id_cd,
                            symbol=_sym_u, tier=tier, trade_nature=trade_nature,
                            action="hold", confidence=0,
                            reasoning=f"[冷却拦截] {_cd_reason}",
                            agent_source=f"{trade_nature}_cooldown_blocked",
                            market_summary=market_summary,
                        )
                    except Exception:
                        pass
                    _audit_skip(f"midlong_cooldown_block:{_cd_reason}")
                    return False
                # ── P0-E 分层熔断：周期级日亏预算（只冻本 tier，绝不跨周期）──
                try:
                    from backend.services.tier_circuit_breaker import (
                        is_tier_open_blocked as _tier_cb_blocked,
                    )
                    _tier_blk, _tier_why = _tier_cb_blocked(_account_id_cd, _tier_cd)
                    if _tier_blk:
                        logger.info(
                            "[TierCircuit] BLOCK %s %s tier=%s: %s",
                            _sym_u, _act, _tier_cd, _tier_why,
                        )
                        host.append_event(
                            session, "tier_circuit_block",
                            f"⛔ 周期熔断[{_tier_cd}] {_sym_u} {_act}: {_tier_why[:100]}",
                        )
                        _audit_skip(f"tier_circuit_block:{_tier_why[:60]}")
                        return False
                except Exception as _tier_cb_err:
                    logger.warning("[TierCircuit] 检查跳过(fail-open，分层熔断未校验): %s", _tier_cb_err)
        except Exception as _cd_err:
            logger.warning("[MidLongCooldown] %s 冷却检查跳过(fail-open，冷却未校验): %s", _sym_u, _cd_err)

    # ── v6 M3：LLM exit_plan 止损直通；禁止 max(LLM, structure) 加宽 ──
    # 有 LLM sl → 用之；structure 仅 LLM 缺失时兜底；随后仅 ATR×1.5 地板抬升。
    _sl_source = "llm" if float(sl_pct or 0) > 0 else ""
    _structure_sl_pct = 0.0
    _structure_tp_pct = 0.0
    try:
        from backend.config.settings import MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT
        _use_struct_sl = bool(MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT)
    except Exception:
        _use_struct_sl = True
    if _use_struct_sl and _act in ("buy", "sell") and float(sl_pct or 0) <= 0:
        try:
            from backend.services.mid_long_structure_stop import mid_long_structure_stop
            _ms_for_stop = (market_summary or {}).get(_sym_u) if isinstance(market_summary, dict) else None
            if not isinstance(_ms_for_stop, dict):
                _ms_for_stop = {}
            _ref_price = float(
                _ms_for_stop.get("current_price")
                or _ms_for_stop.get("price")
                or _ms_for_stop.get("mark_price")
                or 0.0
            )
            if _ref_price <= 0:
                try:
                    _klines = _ms_for_stop.get("klines")
                    if _klines is not None and hasattr(_klines, "iloc"):
                        _ref_price = float(_klines.iloc[-1]["close"])
                except Exception:
                    pass
            if _ref_price > 0:
                _agent_src = "trend_agent" if (tier or "").lower() in ("long",) else "swing_agent"
                _sl_p, _tp_p, _sl_price, _tp_price, _sl_src = mid_long_structure_stop.compute(
                    symbol=_sym_u,
                    market_data=_ms_for_stop,
                    side=_act,
                    entry=_ref_price,
                    agent_source=_agent_src,
                )
                if _sl_p > 0:
                    _structure_sl_pct = float(_sl_p)
                    sl_pct = _structure_sl_pct
                    _sl_source = "structure_fallback"
                    logger.info(
                        "[MidLongStructureSL] %s %s tier=%s: LLM 缺失 → structure sl=%.2f%% (fallback)",
                        _sym_u, _act, tier, _structure_sl_pct * 100,
                    )
                if _tp_p > 0 and float(tp_pct or 0) <= 0:
                    _structure_tp_pct = float(_tp_p)
                    tp_pct = _structure_tp_pct
        except Exception as _ss_err:
            logger.debug("[MidLongStructureSL] %s 结构 SL 跳过: %s", _sym_u, _ss_err)

    # [P0-1] LLM 给的 tp_pct 上限 clamp（mid 10% / long 20%）：防 LLM 拍脑袋设 20%+ 目标。
    # [2026-08-23 融合改造] 例外：long_trend_v2（Chandelier 退出、无固定 TP 目标）的 TP 是
    # 2×Chandelier SL 的合成值——被钳到 20% 会 TP=SL → V5 盈亏比 1.0 必拒。V2 时保留 2×SL。
    try:
        from backend.services.long_trend_v2 import long_v2_enabled as _lv2c
        _v2_tp_unclamped = bool(_lv2c()) and (tier or "").lower() == "long"
    except Exception:
        _v2_tp_unclamped = False
    if float(tp_pct or 0) > 0:
        if _v2_tp_unclamped:
            tp_pct = max(float(tp_pct or 0), float(sl_pct or 0) * 2.0)
        else:
            tp_pct = _clamp_tp_to_tier_max(tp_pct, tier, _sym_u, _act)

    # ── P1：ATR 止损地板 + funding 净 RR + ATR 仓位；chop 仅缩仓不否决 ──
    _atr_size_mult = 1.0
    if (tier or "").lower() == "long" and _act in ("buy", "sell"):
        try:
            from backend.services.mlto.midlong_trade_design import (
                apply_structure_atr_floor,
                atr_size_multiplier,
                estimate_atr_1d_pct,
                funding_net_rr_ok,
                is_chop_regime,
            )
            _ms_td = (market_summary or {}).get(_sym_u) if isinstance(market_summary, dict) else {}
            if not isinstance(_ms_td, dict):
                _ms_td = {}
            _orch_td = (_ms_td.get("orchestrator") if isinstance(_ms_td.get("orchestrator"), dict) else {}) or {}
            _chop, _chop_why = is_chop_regime(_ms_td, _orch_td)
            if _chop:
                # v6：chop 不得否决 AI 方向——最多缩仓 + soft_warning
                _atr_size_mult *= 0.5
                logger.info(
                    "[MidLongChop] SOFT %s %s size×0.5: %s",
                    _sym_u, _act, _chop_why,
                )
                host.append_event(
                    session, "midlong_chop_soft",
                    f"[震荡缩仓] {_sym_u}: {_chop_why}",
                )
            _atr = estimate_atr_1d_pct(_ms_td)
            if _atr is not None and not _ms_td.get("atr_1d_pct"):
                _ms_td["atr_1d_pct"] = _atr
                if isinstance(market_summary, dict):
                    market_summary.setdefault(_sym_u, _ms_td)
                    market_summary[_sym_u]["atr_1d_pct"] = _atr
            _sl_before_floor = float(sl_pct or 0)
            # [轮101] ATR 倍数按车道取（中线 1.5 / 长线 3.0，与 Chandelier 同口径）
            sl_pct, _atr_floor_why = apply_structure_atr_floor(
                sl_pct=_sl_before_floor, atr_1d_pct=_atr, tier=tier,
            )
            if "→" in str(_atr_floor_why):
                logger.info("[MidLongATR] %s %s", _sym_u, _atr_floor_why)
                if _sl_source.startswith("llm") or _sl_source == "llm":
                    _sl_source = "llm+atr_floor"
                elif not _sl_source:
                    _sl_source = "atr_floor"
            elif not _sl_source and float(sl_pct or 0) > 0:
                _sl_source = "llm"
            # TP 至少满足净 RR（粗：2×SL）；若原 TP 更宽则保留
            if float(tp_pct or 0) < float(sl_pct or 0) * 2.0:
                tp_pct = float(sl_pct or 0) * 2.0
            # [P0-1] RR 地板可能把 TP 抬过 tier 上限 → 再 clamp 回 max（20%/10%）；
            # V2 长线（Chandelier 管理）豁免钳制。[2026-08-24] 合成 TP 从 2×SL 放宽到
            # LONG_V2_SYNTH_TP_MULT×SL（默认 4.0）——只作 Layer-0 极端行情 failsafe，
            # 正常止盈 = 结构目标减半 + Chandelier 追踪（设计 V2 §4.3.4 不设固定 TP）。
            if _v2_tp_unclamped:
                try:
                    _v2_tp_mult = float(os.environ.get("LONG_V2_SYNTH_TP_MULT", "4.0") or 4.0)
                except Exception:
                    _v2_tp_mult = 4.0
                _v2_tp_mult = max(1.5, min(8.0, _v2_tp_mult))
                tp_pct = max(float(tp_pct or 0), float(sl_pct or 0) * _v2_tp_mult)
            else:
                tp_pct = _clamp_tp_to_tier_max(tp_pct, tier, _sym_u, _act)
            _fr_ok, _nrr, _fr_why = funding_net_rr_ok(
                action=_act,
                tp_pct=float(tp_pct or 0),
                sl_pct=float(sl_pct or 0),
                funding_rate=_ms_td.get("funding_rate"),
            )
            if not _fr_ok:
                # [2026-08-23 融合改造] long_trend_v2 的 Chandelier 止损很宽（1w ATR×2，
                # 可达 20%），而 TP 被钳到 tier 上限 20% → TP=SL → 固定 TP 口径的 RR≈1，
                # 费率闸必然误杀。V2 长线退出 = Chandelier + 结构破坏（无固定 TP 目标），
                # 固定 TP 的净 RR 口径不适用 → 跳过本闸（费率成本 72h 仅 ~0.09%，
                # 相对 20% 级止损可忽略）。
                try:
                    from backend.services.long_trend_v2 import long_v2_enabled as _lv2_en
                    _v2_managed = bool(_lv2_en())
                except Exception:
                    _v2_managed = False
                if _v2_managed:
                    logger.info(
                        "[MidLongFunding] SKIP %s %s (long_trend_v2 Chandelier 管理,无固定TP口径): %s",
                        _sym_u, _act, _fr_why,
                    )
                else:
                    logger.info("[MidLongFunding] BLOCK %s %s: %s", _sym_u, _act, _fr_why)
                    host.append_event(session, "midlong_funding_block", f"[费率RR] {_sym_u}: {_fr_why}")
                    _audit_skip(f"midlong_funding_block:{_fr_why}")
                    return False
            _atr_sz, _atr_sz_why = atr_size_multiplier(
                sl_pct=float(sl_pct or 0), atr_1d_pct=_atr, tier=tier,
            )
            _atr_size_mult *= float(_atr_sz or 1.0)
            if _atr_size_mult < 0.999:
                logger.info("[MidLongATRSize] %s %s", _sym_u, _atr_sz_why)
        except Exception as _td_err:
            logger.debug("[MidLongTradeDesign] %s 跳过: %s", _sym_u, _td_err)

    # [2026-08-26 亏损复盘] 中线(swing)盈亏比下限：结构止损可达 -9%(1w ATR) 而
    # LLM TP 可能只有 +5-6%，形成 RR 0.6 倒挂仓（VIRTUAL 8/26 实测单仓浮亏
    # -9.94）。强制 tp_pct >= MIDLONG_SWING_MIN_RR × sl_pct（默认 1.0，0=关闭）。
    if (tier or "").strip().lower() != "long" and _act in ("buy", "sell")             and float(sl_pct or 0) > 0 and float(tp_pct or 0) > 0:
        try:
            _swing_min_rr = float(os.environ.get("MIDLONG_SWING_MIN_RR", "1.0") or 1.0)
        except Exception:
            _swing_min_rr = 1.0
        if _swing_min_rr > 0 and float(tp_pct) < float(sl_pct) * _swing_min_rr:
            logger.warning(
                "[MidLongRR] %s %s tier=%s RR倒挂修复: tp %.2f%% < sl %.2f%%×%.1f → tp 抬到 %.2f%%",
                _sym_u, _act, tier, float(tp_pct) * 100, float(sl_pct) * 100,
                _swing_min_rr, float(sl_pct) * _swing_min_rr * 100,
            )
            tp_pct = float(sl_pct) * _swing_min_rr

    # [Phase D 修复 Bug1] 把 tranche 分档保证金比例夹紧到 [0,1]，传给 proposal。
    # proposal_execution 会把它作为 size 乘子叠加到 budget/V5Gate/MTF 之后。
    try:
        _tranche_mult = float(tranche_margin_pct)
        if _tranche_mult != _tranche_mult or _tranche_mult < 0:  # NaN 或负数 → 不缩
            _tranche_mult = 1.0
        if _tranche_mult > 1.0:
            _tranche_mult = 1.0
    except (TypeError, ValueError):
        _tranche_mult = 1.0
    # ATR 仓位乘在 tranche 之上（只缩不放）
    try:
        _tranche_mult = max(0.0, min(1.0, float(_tranche_mult) * float(_atr_size_mult or 1.0)))
    except Exception:
        pass
    # ── [轮137 2026-09-20] 六分析师信号的**规模通道**（不是门槛置信度）──────────────
    # 教训（轮136 实测）：把倾向折减写进 `llm_conviction` 会撞 `[V5Gate] rule=confidence`
    # 的 30% 门槛 ⇒ "去风险"变成**硬拦**（辩论 ×0.6 让 40→24，25~28% 被拦 20 次）。
    # 故此处只作用于**规模**，门槛继续看 LLM 原始置信度；乘子有界 [0.80, 1.10]。
    # 开关：ANALYST_BLEND_SIZE_ENABLED（默认 true）；停用即恒为 1.0。
    try:
        from backend.services.analysts.service import size_multiplier as _analyst_size_mult
        _as_mult, _as_note = _analyst_size_mult(_sym_u, tier=str(tier or "mid"))
        if abs(float(_as_mult) - 1.0) > 1e-6:
            logger.info("[AnalystSize] %s tier=%s ×%.3f（%s）", _sym_u, tier, _as_mult, _as_note)
            _tranche_mult = max(0.0, min(1.0, float(_tranche_mult) * float(_as_mult)))
    except Exception as _as_err:
        logger.debug("[AnalystSize] 跳过(fail-open): %s", _as_err)
    # [轮117 2026-09-19 乘子链留痕] 中线"开不出来"排查时，日志里只有各层的 `size×0.xx`
    # 与最终乘积，缺"**这个值从哪来**"。这里把入参/ATR 项/出参一次打全，
    # 以后任何"被压成 0"都能一眼定位是哪一层（而不是靠反推）。
    try:
        logger.info(
            "[TrancheChain] %s tier=%s tranche_in=%.4f atr_mult=%.3f → tranche_out=%.4f",
            _sym_u, tier, float(tranche_margin_pct or 0), float(_atr_size_mult or 1.0),
            float(_tranche_mult),
        )
    except Exception:
        pass

    # ── P2：净方向敞口 + 相关簇同向上限（在仓位乘子确定后估名义）──
    if _act in ("buy", "sell") and (
        (tier or "").lower() in ("mid", "long")
        or _tn_l in ("swing", "trend_follow", "position")
    ):
        try:
            from backend.services.mlto.midlong_portfolio_risk import (
                check_portfolio_open_allowed,
                estimate_open_notional,
            )
            from backend.services.paper_trading_engine import paper_engine

            _acct_pf = host.get_trading_account_id(db, session)
            _portfolio = None
            _est_notional = 0.0
            _equity = 0.0
            _pos_list = None
            _is_live_mid = (getattr(session, "trading_mode", "paper") or "paper").strip().lower() == "live"
            if _acct_pf and _is_live_mid:
                # [2026-08-28 实盘打通] live 会话权益 = 币安真实余额（用户流快照→REST）
                try:
                    from backend.database.models import Account as _AcctEQ
                    from backend.services.full_auto.live_equity import get_live_equity
                    _acct_eq = db.query(_AcctEQ).filter(_AcctEQ.id == _acct_pf).first()
                    _equity = get_live_equity(_acct_eq, _acct_pf) if _acct_eq else 0.0
                except Exception:
                    _equity = 0.0
                # [2026-09-10 第 9 轮审计] **实盘也要把持仓喂给组合闸**：
                # 此前 live 分支只取权益、不取持仓 → `_portfolio=None` →
                # `collect_midlong_positions(None, None)` 返回 [] →
                # **净敞口闸与并发上限（MIDLONG_MAX_OPEN_POSITIONS）在实盘完全不生效**。
                # 实盘持仓不在 paper_positions（实测 7 个 live 账户 0 行），须经
                # live_executor.get_positions() 走交易所 adapter 查询，
                # 其返回字段已与 paper_engine.get_positions 对齐。
                try:
                    from backend.services.exchange.executors import get_executor
                    _ex_live = (
                        getattr(session, "active_exchange", None)
                        or getattr(session, "selected_exchange", None)
                        or "asterdex"
                    )
                    _pos_list = (
                        get_executor("live", exchange=_ex_live).get_positions(
                            db, _acct_pf, status="open"
                        )
                        or []
                    )
                    # 交易所返回的持仓**不含** timeframe_tier / trade_nature
                    # （见 live_executor._get_hl_positions 只填 symbol/side/size/价格），
                    # 而组合闸的 `_is_midlong_pos()` 依赖这两字段 → 不补齐就会被全部过滤掉、
                    # 闸再次形同不存在（这正是"修了但没生效"的隐蔽形态）。
                    # 处置：实盘按**整本持仓**计入并发/敞口上限（对风险闸是保守方向），
                    # 与 paper 的"仅 mid/long 计数"语义不同，已在 §47 报告与日志中明示。
                    for _p in _pos_list:
                        if isinstance(_p, dict):
                            _p.setdefault("timeframe_tier", "long")
                            _p.setdefault("trade_nature", "position")
                    _portfolio = {
                        "balance": {"total_equity": float(_equity or 0)},
                        "positions": _pos_list,
                    }
                    logger.info(
                        "[MidLong] live 组合闸持仓注入 account=%s exchange=%s n_open=%d"
                        "（实盘按整本持仓计入上限）",
                        _acct_pf, _ex_live, len(_pos_list),
                    )
                except Exception as _lpos_err:
                    # 与全仓惯例一致：取不到持仓时放行，但**必须可见**（第 3 轮已把同类
                    # fail-open 从 debug 提升为 warning），否则等于静默无保护。
                    logger.warning(
                        "[MidLong] live 持仓查询失败(fail-open)：组合闸"
                        "（净敞口/并发上限）本次不生效 account=%s: %s",
                        _acct_pf, _lpos_err,
                    )
            elif _acct_pf:
                _bal = paper_engine.get_balance(db, _acct_pf) or {}
                _pos_list = paper_engine.get_positions(db, _acct_pf, status="open") or []
                _equity = float(
                    (_bal or {}).get("total_equity")
                    or (_bal or {}).get("equity")
                    or (_bal or {}).get("balance")
                    or 0
                )
                _portfolio = {"balance": _bal, "positions": _pos_list}
                try:
                    from backend.config import settings as _cfg_pf
                    _risk_pct = _keep0_float(getattr(_cfg_pf, "MIDLONG_RISK_PCT", 0.01), 0.01)
                except Exception:
                    _risk_pct = 0.01
                # 杠杆：与真实成交口径一致 —— 同币已有仓跟仓，否则取该币种统一档位。
                # [2026-09-04] 必须传 symbol：不传则 resolve_leverage 回落到 requested
                # 的 10x，估算名义 = 权益×保证金比×10 会算出 100%+ 权益的提议，必被组合
                # 风控拒（实测 VIRTUAL est=$5435=108% 权益，而单币上限仅 35%），中线因此
                # 一单也开不出来。account 传入后账户级 tier 覆盖由权威内部统一处理。
                _lev = 3.0
                try:
                    from backend.services.leverage_authority import (
                        extract_existing_symbol_leverage,
                        resolve_leverage,
                    )
                    _exist_lev = extract_existing_symbol_leverage(_sym_u, _pos_list)
                    if _exist_lev and float(_exist_lev) > 0:
                        _lev = float(_exist_lev)
                    else:
                        _acct_obj = None
                        try:
                            from backend.database.models import Account as _AcctML
                            _acct_obj = db.query(_AcctML).filter(_AcctML.id == _acct_pf).first()
                        except Exception:
                            _acct_obj = None
                        _lev = float(resolve_leverage(
                            tier=(tier or "mid").lower(),
                            symbol=_sym_u,
                            account=_acct_obj,
                        ))
                except Exception:
                    _lev = 3.0
                # 名义 = 权益 × 保证金比例 × 杠杆（与 ETH 成交口径一致）
                _est_notional = estimate_open_notional(
                    equity=_equity,
                    margin_frac=float(_tranche_mult or 0),
                    leverage=_lev,
                    sl_pct=float(sl_pct or 0),
                    risk_pct=_risk_pct,
                )
            # [P12 执行 2026-09-10] **闸的输入**改用与 PositionConstruction 同口径的名义
            # （equity×risk/SL×tranche）；旧式估算仅保留给诊断日志与一键回滚。
            # 依据：§60.1 —— 旧口径把 $783 的真实建仓喂成 $7,050（9×），22,042 次净敞口
            # 拦截实为"按幻影仓位拒单"（after_pct 无一条 <100%）。
            _aligned_notional = 0.0
            try:
                from backend.services.mlto.midlong_portfolio_risk import (
                    estimate_open_notional_aligned as _est_aligned,
                )
                _aligned_notional = _est_aligned(
                    equity=_equity, sl_pct=float(sl_pct or 0),
                    risk_pct=float(_risk_pct or 0.0075),
                    tranche_mult=float(_tranche_mult or 1.0),
                )
            except Exception as _al_err:  # noqa: BLE001
                logger.warning("[MidLongPortfolio] 同口径名义估算失败(回退旧口径): %s", _al_err)
            _gate_notional = _est_notional
            if _aligned_notional > 0 and _cfg_bool_env("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", True):
                _gate_notional = _aligned_notional
            _is_probe = str(dir_src or "").startswith("nibble_probe")
            _pf_ok, _pf_why = check_portfolio_open_allowed(
                symbol=_sym_u,
                action=_act,
                portfolio=_portfolio,
                new_notional=_gate_notional,
                is_probe=_is_probe,
            )
            if not _pf_ok:
                # [§60 诊断 + P12] 同时给出两种口径的名义估计，暴露倍差（口径切换后可核对
                # 拦截是否"按真实仓位"发生）。
                _ratio = (_est_notional / _aligned_notional) if _aligned_notional > 0 else 0.0
                logger.info(
                    "[MidLongPortfolio] BLOCK %s %s: %s (gate_notional=%.1f "
                    "legacy=%.1f aligned=%.1f 倍差=%.1fx equity=%.1f margin×=%.3f)",
                    _sym_u, _act, _pf_why, _gate_notional, _est_notional, _aligned_notional, _ratio,
                    _equity, float(_tranche_mult or 0),
                )
                host.append_event(
                    session, "midlong_portfolio_block",
                    f"[组合风控] {_sym_u} {_act}: {_pf_why}",
                )
                _audit_skip(f"midlong_portfolio_block:{_pf_why}")
                return False
        except Exception as _pf_err:
            logger.debug("[MidLongPortfolio] %s 跳过: %s", _sym_u, _pf_err)

    # ── 组合级风险预算（v6 计划 阶段1 第4项，下单前最后一道检查）──
    # 组合日 VaR / 单币集中度 / 策略 3σ 熔断 / 冻结信号。持仓/收益序列模块内
    # TTL 缓存；paper fail-open、live fail-closed。
    # [2026-09-11 用户指令] **模拟(paper)账户整段跳过**：纸面亏损=训练数据，
    # 不得被冻结/回撤熔断按住（实测 freeze 台账每 17 分钟刷 midlong
    # BTC/XRP/ASTER "drawdown 23.60σ"）。与短线 scalp_loop 的 PB_PAPER_SKIP
    # 语义对齐；权威判断仍在 portfolio_budget.evaluate_open 内，此处只是省掉调用。
    if _act in ("buy", "sell"):
        _pb_mode = (getattr(session, "trading_mode", "") or "paper").strip().lower()
        try:
            _pb_skip = os.getenv("PB_PAPER_SKIP", "true").strip().lower() in (
                "1", "true", "yes", "on",
            )
        except Exception:
            _pb_skip = True
        if _pb_skip and _pb_mode == "paper":
            logger.debug(
                "[MidLongPortfolio] %s paper 模式跳过组合预算(PB_PAPER_SKIP=true)", _sym_u,
            )
        else:
            try:
                from backend.services.risk_management.portfolio_budget import (
                    portfolio_budget as _pb,
                )
                _pb_strategy = (
                    "midlong"
                    if (tier or "").lower() in ("mid", "long")
                    or _tn_l in ("swing", "trend_follow", "position")
                    else str(trade_nature or "midlong").lower()
                )
                _pb_dec = _pb.evaluate_open(
                    symbol=_sym_u,
                    action=_act,
                    notional_usd=float(_est_notional or 0),
                    equity=float(_equity or 0),
                    strategy=_pb_strategy,
                    mode=_pb_mode,
                    db=db,
                    account_id=int(_acct_pf or 0),
                    positions=_pos_list if "_pos_list" in locals() else None,
                )
                if not _pb_dec.allowed:
                    logger.info(
                        "[MidLongPortfolio] BLOCK %s %s: portfolio_budget %s",
                        _sym_u, _act, ";".join(_pb_dec.reasons[:3]),
                    )
                    host.append_event(
                        session, "portfolio_budget_block",
                        f"[组合预算] {_sym_u} {_act}: {';'.join(_pb_dec.reasons[:3])}",
                    )
                    _audit_skip(
                        "portfolio_budget_block:" + ";".join(_pb_dec.reasons[:3])
                    )
                    return False
            except Exception as _pb_err:
                logger.debug("[MidLongPortfolio] %s 组合预算跳过: %s", _sym_u, _pb_err)

    # ── 风控官（有否决权）—— 架构第 3 环，**下单前最后一道**（轮131）──
    # 为什么放在这里：这是全链路里**唯一**同时拿得到 权益/计划名义/杠杆 的位置，
    # 而 `risk_constitution.constitutional_veto` 的单笔保证金、日亏损、敞口三项检查
    # **全都依赖 equity_usd/margin_usd** —— 此前调用点（brain.py）没传，等于空转。
    # 编排：五分析师 → 牛熊辩论（主周期裁决已落库）→ **风控官** → 交易员。
    # 回滚：RISK_OFFICER_ENABLED=false（整段跳过并记录一条 skipped）。
    if _act in ("buy", "sell"):
        try:
            from backend.services.risk_officer import evaluate_open as _ro_eval
            _ro_thesis_id = ""
            try:
                # 论题 id 顺带落进风控记录：否决时能直接追到"是哪张论题被否的"
                from backend.services.mlto.thesis_store import get as _thesis_ro
                _td_ro = _thesis_ro(str(getattr(session, "session_id", "") or ""),
                                    _sym_u, str(_tier_l or tier or "mid"))
                _ro_thesis_id = str(getattr(_td_ro, "thesis_id", "") or "")
            except Exception:
                pass
            _ro = _ro_eval(
                account_id=int(_acct_pf or 0) or None,
                symbol=_sym_u,
                side=("long" if _act == "buy" else "short"),
                tier=str(_tier_l or tier or "mid"),
                mode=str(getattr(session, "trading_mode", "") or "paper"),
                equity_usd=float(_equity or 0),
                planned_notional_usd=float(_est_notional or 0),
                leverage=float(locals().get("_lev") or 1.0),
                sl_pct=float(sl_pct or 0),
                session_id=str(getattr(session, "session_id", "") or ""),
                thesis_id=_ro_thesis_id,
                # [轮146 方案 B] 小仓探针 ⇒ 不适用辩论否决（其余检查照旧全部执行）
                is_probe=bool(probe_entry) or (float(_tranche_mult or 0) <= 0.20),
            )
            if not _ro["allow"]:
                logger.warning("[RiskOfficer] BLOCK %s %s: %s", _sym_u, _act, _ro["reason"])
                host.append_event(
                    session, "risk_officer_veto",
                    f"[风控官] {_sym_u} {_act} 否决：{_ro['reason']}",
                )
                _audit_skip(f"risk_officer_veto:{_ro['reason']}")
                return False
        except Exception as _ro_err:
            # fail-open 但**可见**（与 live 持仓查询失败同一纪律：不能静默无保护）
            logger.warning("[RiskOfficer] %s 判定失败(fail-open，本次未做风控官检查): %s", _sym_u, _ro_err)

    _extra_kwargs = {
        "mtf_size_mult": _mtf_size_mult,
        "tp_sl_proposal": tp_sl_proposal or None,
        "invalidation_condition": invalidation_condition or "",
        "expected_hold_hours": float(expected_hold_hours or 0),
        "tranche_margin_pct": _tranche_mult,
        # [轮117 2026-09-19] 下传 sl_pct：缩仓链地板需要把"乘子乘积"换算成**名义**才能判断
        # 是不是"名义≈0 的废单"（乘子本身没有绝对含义 —— 六层叠乘下 0.0009 与 0.09% 名义
        # 是两回事）。proposal_execution 用它算 base = equity × risk / sl。
        # ⚠️ 键名**必须**区别于 `sl_pct`：下面 `TradeProposal.from_agent(sl_pct=...)` 已经
        # 显式传了同名形参，`**_extra_kwargs` 再带一个就是
        # `from_agent() got multiple values for argument 'sl_pct'` —— 实测（16:44:21）
        # 这条异常把**所有**中线/长线开仓打成"开仓失败"，比缩仓本身更致命。
        "notional_sl_pct": float(sl_pct or 0),
        # [M1-A] entry_source 只在非空时下发（历史调用方不受影响）
        **({"entry_source": entry_source} if entry_source else {}),
    }
    # 开仓前就把 thesis_id 塞进 proposal.extra → decision → open_metadata，
    # 避免仅靠开仓后异步 tag（重启/跨进程会丢）。
    try:
        from backend.services.mlto.thesis_store import get as _thesis_pre
        _sid_pre = str(getattr(session, "session_id", "") or "")
        _td_pre = _thesis_pre(_sid_pre, _sym_u, _tier_l)
        if _td_pre is not None and getattr(_td_pre, "thesis_id", ""):
            _extra_kwargs["thesis_id"] = str(_td_pre.thesis_id)
            if getattr(_td_pre, "analysis_run_id", ""):
                _extra_kwargs["analysis_run_id"] = str(_td_pre.analysis_run_id)
        if _sid_pre:
            _extra_kwargs["session_id"] = _sid_pre
        _extra_kwargs["timeframe_tier"] = _tier_l or "mid"
    except Exception:
        pass

    # [轮112 2026-09-19] 入场时特征留档（**纯观测**，不参与判定）。
    # 依据：轮110/111 两次想验证"入场质量门槛"都卡在特征缺失上 ——
    # `open_metadata` 里没有 ATR/波动率，regime/冷却间隔/置信度散在别的表，
    # 且只有 39% 的仓位连得上 thesis。这里把入场那一刻的尺度落到 open_metadata，
    # 后续归因直接用库内数据即可。失败一律不影响开仓。
    try:
        from backend.services.analysis.entry_features import entry_feature_snapshot as _efs
        _extra_kwargs["entry_features"] = _efs(
            sym_u, tier=_tier_l or "mid", sl_pct=sl_pct,
            market_summary=market_summary, db=db,
        )
    except Exception as _ef_err:
        logger.debug("[EntryFeatures] %s 留档跳过: %s", _sym_u, _ef_err)

    # [2026-09-16 调研轮7] 止损距离上限（mid 2% / long 3%）——**必须放在最后**：
    # 本函数内 tier=long 分支的 ATR 地板（apply_structure_atr_floor）会把 sl_pct
    # 二次抬高，若上限提前应用就被绕过（实测 E1 6.5% = 3.0% 上限的 2.17×）。
    # 数据依据：9/11 后 mid+long n=34，赢家最大逆行 MAE 1.20%(n=18) vs 输家 2.56~4.85%，
    # 现役 SL 4.50~4.85% ⇒ avg_loss(-17.10) > avg_win(+13.90)、打平需 55.2% 实际 52.9%。
    # 反事实 cap=2%：误杀赢家 0/18，区间净额 -23.49 → +73.54。
    # 详见 midlong_trade_design.clamp_stop_distance。回滚：MIDLONG_MAX_SL_PCT_* = 0。
    try:
        from backend.services.mlto.midlong_trade_design import clamp_stop_distance as _clamp_sl
        _sl_capped, _sl_cap_why = _clamp_sl(sl_pct, _tier_l)
        if float(_sl_capped or 0) != float(sl_pct or 0):
            logger.info(
                "[MidLongSL] %s %s tier=%s %s", _sym_u, _act, _tier_l, _sl_cap_why,
            )
            try:
                host.append_event(session, "midlong_sl_capped", f"[止损距离上限] {_sym_u}: {_sl_cap_why}")
            except Exception:
                pass
        sl_pct = float(_sl_capped or 0)
    except Exception as _cl_err:
        logger.debug("[MidLongSL] 止损上限跳过(fail-open): %s", _cl_err)
    if not _sl_source and float(sl_pct or 0) > 0:
        _sl_source = "llm"
    logger.info(
        "[MidLong] stage=open_ready symbol=%s action=%s sl_source=%s sl=%.2f%% tp=%.2f%%",
        _sym_u, _act, _sl_source or "-", float(sl_pct or 0) * 100, float(tp_pct or 0) * 100,
    )

    proposal = TradeProposal.from_agent(
        sym=_sym_u,
        tier=(tier or "mid").lower(),
        action=_act,
        confidence=int(confidence or 0),
        trade_nature=trade_nature,
        sl_pct=float(sl_pct or 0),
        tp_pct=float(tp_pct or 0),
        source_lane=f"{trade_nature}_independent",
        **_extra_kwargs,
    )
    _eval = getattr(host, "evaluate_and_execute_proposal", None)
    if not callable(_eval):
        logger.error(
            "[MidLong] host missing evaluate_and_execute_proposal (type=%s) symbol=%s",
            type(host).__name__, _sym_u,
        )
        try:
            from backend.services.mlto.midlong_direction_audit import record_decision_audit
            record_decision_audit(
                outcome="skip",
                stage="exec",
                symbol=_sym_u,
                reason="host_missing_evaluate_and_execute_proposal",
                session_id=str(getattr(session, "session_id", "") or ""),
                tier=(tier or "").lower(),
                action=_act,
                mode=hub_mode or "",
                direction=hub_dir or "",
                authority=authority or "",
                extra={"host_type": type(host).__name__},
            )
        except Exception:
            pass
        return False

    # ── [调研轮19 2026-09-17] 回踩入场：同一批信号等一个小回撤再成交（非门禁、不丢单）──
    # 依据（近 7 天 35 笔，15m K 线）：入场后 1h 内 **80% 出现回踩**（均值 +1.11%），
    # 而入场后前 4h MFE +0.24% vs MAE −1.68% ⇒ 市价成交等于买局部高点。
    # 挂 entry×(1−0.3%) 限价：74% 成交、平均改善 0.30%。超时（默认 30min）即市价兜底。
    # 回滚：MIDLONG_PULLBACK_ENTRY_ENABLED=false。
    try:
        from backend.services.full_auto.pullback_entry import evaluate as _pb_evaluate
        _pb_px = 0.0
        try:
            _pb_blk = (market_summary or {}).get(_sym_u) or {}
            if isinstance(_pb_blk, dict):
                _pb_px = float(_pb_blk.get("current_price") or _pb_blk.get("price") or 0)
        except Exception:
            _pb_px = 0.0
        if _pb_px > 0:
            _pb_mid = 0.0
            try:
                _ind = (_pb_blk or {}).get("indicators_1h") or {}
                _hs = list(_ind.get("highs") or [])[-24:]
                _ls = list(_ind.get("lows") or [])[-24:]
                if _hs and _ls:
                    _pb_mid = (max(float(x) for x in _hs) + min(float(x) for x in _ls)) / 2.0
            except Exception:
                _pb_mid = 0.0
            _pb_wait, _pb_why = _pb_evaluate(
                key=f"{getattr(session, 'session_id', '')}:{_sym_u}:{tier}:{_act}",
                side=_act, price=_pb_px, range_mid=(_pb_mid or None),
            )
            if _pb_wait:
                logger.info("[PullbackEntry] %s %s %s", _sym_u, _act, _pb_why)
                try:
                    host.append_event(session, "pullback_wait",
                                      f"[回踩入场] {_sym_u} {_act}: {_pb_why}")
                except Exception:
                    pass
                _audit_skip(f"pullback_wait:{_pb_why}")
                return False
    except Exception as _pb_err:  # noqa: BLE001
        logger.debug("[PullbackEntry] 跳过(fail-open 照常成交): %s", _pb_err)

    try:
        _ok = bool(_eval(
            db=db,
            session=session,
            proposal=proposal,
            market_summary=market_summary,
            session_mode=session_mode,
        ))
    except Exception as _eval_err:
        logger.warning(
            "[MidLong] evaluate_and_execute_proposal failed %s: %s",
            _sym_u, _eval_err,
        )
        try:
            from backend.services.mlto.midlong_direction_audit import record_decision_audit
            record_decision_audit(
                outcome="skip",
                stage="exec",
                symbol=_sym_u,
                reason=("exec_exception:%s:%s" % (type(_eval_err).__name__, _eval_err))[:160],
                session_id=str(getattr(session, "session_id", "") or ""),
                tier=(tier or "").lower(),
                action=_act,
                mode=hub_mode or "",
                direction=hub_dir or "",
                authority=authority or "",
            )
        except Exception:
            pass
        return False

    if _ok and _act in ("buy", "sell"):
        try:
            from backend.services.mlto.midlong_direction_audit import record_open_audit
            from backend.services.mlto.decision_hub import ai_governed_enabled
            _mode = hub_mode or ("ai_governed" if ai_governed_enabled() else "standard")
            record_open_audit(
                symbol=_sym_u,
                fill_dir=_act,
                thesis_dir=thesis_dir,
                hub_dir=hub_dir,
                sl_source=_sl_source or "",
                mode=_mode,
                dir_src=dir_src,
                authority=authority,
                session_id=str(getattr(session, "session_id", "") or ""),
            )
        except Exception as _aud_err:
            logger.debug("[MidLongAudit] open record skip: %s", _aud_err)
        # ── 融合归因（阶段4）：中长线开仓统一来源标签（覆盖 factor_route/trend/mlto/master 全部入口）──
        try:
            from backend.services.source_attribution import attribution as _attr_mh
            from sqlalchemy import text as _sa_text_mh
            _acct_mh = host.get_trading_account_id(db, session)
            if _acct_mh:
                _prow = db.execute(
                    _sa_text_mh(
                        "SELECT id FROM paper_positions WHERE account_id=:a AND symbol=:s "
                        "AND opened_at > now() - interval '120 seconds' ORDER BY id DESC LIMIT 1"
                    ),
                    {"a": int(_acct_mh), "s": _sym_u},
                ).first()
                if _prow:
                    # [U3-2a 2026-08-25] thesis 绑定：把当前活跃 thesis_id 挂进归因标签 meta，
                    # 供平仓学习桥（unified_learning meta.thesis_id 兜底回查）解锁 owm 调权。
                    _t_meta: Dict[str, Any] = {}
                    _sid_bind = str(getattr(session, "session_id", "") or "")
                    try:
                        from backend.services.mlto.thesis_store import get as _thesis_get
                        _t_dto = _thesis_get(_sid_bind, _sym_u, _tier_l)
                        if _t_dto is not None and getattr(_t_dto, "thesis_id", ""):
                            _t_meta["thesis_id"] = str(_t_dto.thesis_id)
                            if getattr(_t_dto, "analysis_run_id", ""):
                                _t_meta["analysis_run_id"] = str(_t_dto.analysis_run_id)
                    except Exception as _tb_err:
                        logger.debug("[FusionAttr] thesis 绑定跳过: %s", _tb_err)
                    if _sid_bind:
                        _t_meta["session_id"] = _sid_bind
                    _t_meta["timeframe_tier"] = _tier_l or "mid"
                    _attr_mh.tag_position(
                        int(_prow[0]),
                        source=str(entry_source or "midlong"),
                        nature=str(trade_nature or ""),
                        symbol=_sym_u,
                        meta=_t_meta or None,
                    )
                    # 耐久落库：PaperPosition 无 metadata_json，thesis_id 必须写进
                    # exit_state_json.open_metadata，否则平仓读不到、OWM 永不调权。
                    if _t_meta.get("thesis_id"):
                        try:
                            import json as _json_bind
                            _pid_bind = int(_prow[0])
                            _row_es = db.execute(
                                _sa_text_mh(
                                    "SELECT exit_state_json FROM paper_positions WHERE id=:i"
                                ),
                                {"i": _pid_bind},
                            ).first()
                            _es_bind: Dict[str, Any] = {}
                            if _row_es and _row_es[0]:
                                try:
                                    _es_bind = _json_bind.loads(_row_es[0]) if isinstance(
                                        _row_es[0], str
                                    ) else dict(_row_es[0] or {})
                                except Exception:
                                    _es_bind = {}
                            if not isinstance(_es_bind, dict):
                                _es_bind = {}
                            _om_bind = _es_bind.get("open_metadata")
                            if not isinstance(_om_bind, dict):
                                _om_bind = {}
                            _om_bind.update({
                                k: v for k, v in _t_meta.items() if v is not None
                            })
                            if entry_source:
                                _om_bind.setdefault("entry_source", str(entry_source)[:40])
                                _es_bind["entry_source"] = str(entry_source)[:40]
                            _es_bind["open_metadata"] = _om_bind
                            db.execute(
                                _sa_text_mh(
                                    "UPDATE paper_positions SET exit_state_json=:j WHERE id=:i"
                                ),
                                {
                                    "i": _pid_bind,
                                    "j": _json_bind.dumps(_es_bind, ensure_ascii=False),
                                },
                            )
                            db.commit()
                        except Exception as _es_err:
                            logger.debug("[FusionAttr] open_metadata 写 thesis 跳过: %s", _es_err)
                            try:
                                db.rollback()
                            except Exception:
                                pass
        except Exception as _tagmh_err:
            logger.debug("[FusionAttr] 中长线标签绑定失败: %s", _tagmh_err)
    elif not _ok and _act in ("buy", "sell"):
        # [§52 修复] 带上**真实**拒单原因：此前一律写通用字符串，实测 1866/4235（44.1%）
        # 的拒仓因此无因可查（其中最大来源是 paper 层 `code=daily_quota` 配额用尽，
        # 见 `_audit_ml/Z69`）。原因由下游各层经 `open_block_reason.mark_open_block` 登记。
        record_exec_false_audit(
            symbol=_sym_u, tier=tier, action=_act, session=session,
            mode=hub_mode or "", direction=hub_dir or "", authority=authority or "",
        )
    return _ok


def record_exec_false_audit(*, symbol: str, tier: str, action: str, session=None,
                            mode: str = "", direction: str = "", authority: str = "") -> Optional[str]:
    """[§52] 把 exec 阶段拒仓写进漏斗审计，带上游登记的**具体**原因码。

    未登记时保持旧字符串 `evaluate_and_execute_returned_false`（向后兼容：
    老看板/周报汇总口径不变）。返回实际写入的 reason（便于测试与调用方观测）。
    """
    _blk: Dict[str, Any] = {}
    try:
        from backend.services.mlto.open_block_reason import take_open_block
        _blk = take_open_block() or {}
    except Exception:
        _blk = {}
    _code = str(_blk.get("code") or "").strip()
    reason = f"eval_false:{_code}" if _code else "evaluate_and_execute_returned_false"
    # [2026-09-18 解冻·选项E 补全] 这里拿到了**真实原因**，但 take() 已把它从 ContextVar 清空
    # ⇒ 上层 brain 的 `open_execute_false` 事件读不到（实测 13:12:59 的 1000PEPE 落到 `<未登记>`，
    # 真实原因是 `[V5Gate] BLOCK rule=regime_extreme`，由 proposal_execution 登记后被本函数取走）。
    # 本函数是 `evaluate_and_execute` 返回 False 的**终端汇合点** ⇒ 在此再留一份按 (symbol,tier)
    # 归位的副本，即可让上层读到，覆盖所有经此汇合的路径（含 V5Gate / paper_execution / 裸 return False）。
    #
    # [轮114 2026-09-19 修无信息事件] 原来是**无条件** remember(event 里的 reason)，
    # 而 `_code` 为空时 reason 就是那句通用兜底串 `evaluate_and_execute_returned_false`
    # ⇒ 上层 brain 读回它、写进台账，"为什么没开成"在库里等于没说
    # （实测近 24h 该类通用串 75 条、reason_detail 全空）。
    #
    # 但**也不能干脆不写**：`last_open_block` 是按 (symbol,tier) 归位的"最近一次"，
    # 不说清楚就会把若干分钟前那条真实原因（如 `size_below_floor`）当成本次原因
    # ——陈旧原因比"未登记"更误导。故：
    #   有真实 code → 副本带 code/detail/layer（原行为）；
    #   无真实 code → **显式**写 `<未登记>` 标记（layer 标明来路是 exec_false 终端），
    #                 既不伪装成一条真实原因，也不残留旧原因。
    if _code:
        _rm_code = reason
        _rm_detail = str(_blk.get("detail") or "")
        _rm_layer = str(_blk.get("layer") or "exec_false")
    else:
        _rm_code = "<未登记>"
        _rm_detail = ""
        _rm_layer = "exec_false_unregistered"
    try:
        from backend.services.mlto.open_block_reason import remember_open_block
        remember_open_block(symbol, tier, _rm_code, detail=_rm_detail, layer=_rm_layer)
    except Exception:
        pass
    try:
        from backend.services.mlto.midlong_direction_audit import record_decision_audit
        record_decision_audit(
            outcome="skip",
            stage="exec",
            symbol=str(symbol or "").upper(),
            reason=reason,
            session_id=str(getattr(session, "session_id", "") or ""),
            tier=str(tier or "").lower(),
            action=str(action or "").lower(),
            mode=mode or "",
            direction=direction or "",
            authority=authority or "",
            extra=(
                {"block_layer": _blk.get("layer") or "", "block_detail": _blk.get("detail") or ""}
                if _code else None
            ),
        )
    except Exception:
        pass
    return reason

def record_midlong_factor_snapshots(
    *,
    db,
    account_id: int,
    trade_id: int,
    symbol: str,
    side: str,
    market_data: dict,
) -> None:
    try:
        mf = (market_data or {}).get("midlong_factors") if isinstance(market_data, dict) else None
        if not isinstance(mf, dict):
            return
        from backend.database.models import SignalTradeFeedback
        _dir = "long" if (side or "").lower() == "buy" else "short"
        n = 0
        for tf in ("4h", "1d"):
            vals = mf.get(tf) or {}
            if not isinstance(vals, dict):
                continue
            for fid, v in vals.items():
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if fv != fv:  # NaN
                    continue
                db.add(SignalTradeFeedback(
                    account_id=account_id,
                    trade_id=trade_id,
                    symbol=(symbol or "").upper()[:20],
                    signal_type=f"factor:{fid}"[:50],
                    signal_value=fv,
                    signal_direction=_dir[:20],
                    trade_side=(side or "")[:10],
                ))
                n += 1
        if n:
            db.commit()
    except Exception as e:
        logger.debug("[MidLongFactorIC] %s 因子快照记录跳过: %s", symbol, e)
        try:
            db.rollback()
        except Exception:
            pass

def persist_independent_scan_log(
    *,
    account_id: Optional[int],
    symbol: str,
    tier: str,
    trade_nature: str,
    action: str,
    confidence: float,
    reasoning: str,
    agent_source: str,
    cited_fact_ids: Optional[List[str]] = None,
    evidence_audit: Optional[dict] = None,
    market_summary: Optional[dict] = None,
    llm_tp_sl_proposal: Optional[dict] = None,
    lifecycle: Optional[str] = None,
    scenarios: Optional[dict] = None,
    invalidation: Optional[dict] = None,
) -> None:
    # ── S1-12 修复（R5）：account_id 为空时不再静默 return ──
    # 原逻辑：if not account_id: return
    # 后果：ai_decision_logs 14 天 0 条记录 → 无法做 conf 校准、无法做 prompt A/B、
    #      无法回溯审计 → confidence_calibrator 永远停在 cold_linear →
    #      midlong_ev_gate 冷启动豁免永久生效 → 低 EV 交易被放行。
    # 修复：account_id 为空时改用 0 兜底（专用 audit account），确保 LLM 决策被持久化。
    if not account_id:
        logger.warning(
            "[ScanLog] %s %s account_id 为空，仍尝试落库（account_id=0）", symbol, tier,
        )
        account_id = 0
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.database.models import AIDecisionLog
        from decimal import Decimal as _Decimal

        _mkt_orch = {}
        if isinstance(market_summary, dict):
            _sym_data = market_summary.get(symbol) or {}
            if isinstance(_sym_data, dict):
                _mkt_orch = _sym_data.get("orchestrator") or {}
        if not isinstance(_mkt_orch, dict):
            _mkt_orch = {}

        _ana_db = AnalyticsSessionLocal()
        try:
            entry = AIDecisionLog(
                account_id=int(account_id),
                reason=(reasoning or f"[{agent_source}] {action}")[:1000],
                operation=(action or "hold").lower(),
                symbol=str(symbol).upper(),
                prev_portion=_Decimal("0"),
                target_portion=_Decimal("0"),
                total_balance=_Decimal("0"),
                executed="false",
                reasoning_snapshot=(reasoning or "")[:4000] or None,
                decision_source=agent_source or "llm",
                decision_snapshot=json.dumps({
                    "trade_nature": trade_nature,
                    "tier": tier,
                    "confidence": confidence,
                    "reasoning": (reasoning or "")[:2000],
                    "agent_source": agent_source,
                    "cited_fact_ids": list(cited_fact_ids or []),
                    **({"agent_evidence": evidence_audit} if evidence_audit else {}),
                    # S1-12 新增：v3 schema 字段持久化（对应 04 综合方案 §2.3.4）
                    **({"llm_tp_sl_proposal": llm_tp_sl_proposal} if llm_tp_sl_proposal else {}),
                    **({"lifecycle": lifecycle} if lifecycle else {}),
                    **({"scenarios": scenarios} if scenarios else {}),
                    **({"invalidation": invalidation} if invalidation else {}),
                    "_scan_log": True,
                }, ensure_ascii=False),
                short_bias=str(_mkt_orch.get("short_bias") or "") or None,
                short_confidence=float(_mkt_orch.get("short_confidence") or 0) or None,
                mid_bias=str(_mkt_orch.get("mid_bias") or "") or None,
                mid_confidence=float(_mkt_orch.get("mid_confidence") or 0) or None,
                long_bias=str(_mkt_orch.get("long_bias") or "") or None,
                long_confidence=float(_mkt_orch.get("long_confidence") or 0) or None,
            )
            _ana_db.add(entry)
            _ana_db.commit()
        finally:
            _ana_db.close()
    except Exception as _log_err:
        logger.debug("[ScanLog] %s %s 审计落库跳过: %s", symbol, tier, _log_err)

def _compute_midlong_indicator_block(kdf, period: Optional[str] = None) -> dict:
    """从 OHLCV DataFrame 计算长线 quant brief / MLTO 所需指标。

    [2026-07-31] 补齐 macd_hist / adx / trend：此前只写 RSI/EMA，导致
    MidLongQuantBrief 永久 missing macd_hist_1h/adx_1d/trend_1w，alignment≤7/15，
    LLM 长期 neutral + recommend_open=False，中长线开不出仓。
    """
    _ind: dict = {}
    if kdf is None or getattr(kdf, "empty", True) or "close" not in kdf.columns:
        return _ind
    _delta = kdf["close"].diff()
    _gain = _delta.where(_delta > 0, 0.0)
    _loss = (-_delta).where(_delta < 0, 0.0)
    _avg_g = _gain.ewm(alpha=1 / 14, adjust=False).mean()
    _avg_l = _loss.ewm(alpha=1 / 14, adjust=False).mean()
    _rs = _avg_g / _avg_l.replace(0, 1e-10)
    _ind["rsi"] = round(float((100 - 100 / (1 + _rs)).iloc[-1]), 1)
    _ema9 = kdf["close"].ewm(span=9, adjust=False).mean().iloc[-1]
    _ema21 = kdf["close"].ewm(span=21, adjust=False).mean().iloc[-1]
    _ema50 = kdf["close"].ewm(span=50, adjust=False).mean().iloc[-1] if len(kdf) >= 50 else _ema21
    _ind["ema9"] = round(float(_ema9), 2)
    _ind["ema21"] = round(float(_ema21), 2)
    _ind["ema50"] = round(float(_ema50), 2)
    _ind["ema_trend"] = (
        "bullish" if _ema9 > _ema21 > _ema50
        else "bearish" if _ema9 < _ema21 < _ema50 else "mixed"
    )
    _ind["trend"] = _ind["ema_trend"]
    # MACD histogram（12/26/9）
    try:
        _ema12 = kdf["close"].ewm(span=12, adjust=False).mean()
        _ema26 = kdf["close"].ewm(span=26, adjust=False).mean()
        _macd = _ema12 - _ema26
        _signal = _macd.ewm(span=9, adjust=False).mean()
        _hist = _macd - _signal
        _ind["macd"] = round(float(_macd.iloc[-1]), 6)
        _ind["macd_signal"] = round(float(_signal.iloc[-1]), 6)
        _ind["macd_hist"] = round(float(_hist.iloc[-1]), 6)
    except Exception:
        pass
    # ADX(14) 简化版
    try:
        if all(c in kdf.columns for c in ("high", "low", "close")) and len(kdf) >= 20:
            _up = kdf["high"].diff()
            _down = -kdf["low"].diff()
            _plus_dm = _up.where((_up > _down) & (_up > 0), 0.0)
            _minus_dm = _down.where((_down > _up) & (_down > 0), 0.0)
            _tr = (kdf["high"] - kdf["low"]).combine(
                (kdf["high"] - kdf["close"].shift()).abs(), max
            ).combine(
                (kdf["low"] - kdf["close"].shift()).abs(), max
            )
            _atr = _tr.ewm(alpha=1 / 14, adjust=False).mean()
            _plus_di = 100 * (_plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / _atr.replace(0, 1e-10))
            _minus_di = 100 * (_minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / _atr.replace(0, 1e-10))
            _dx = 100 * (_plus_di - _minus_di).abs() / (_plus_di + _minus_di).replace(0, 1e-10)
            _adx = _dx.ewm(alpha=1 / 14, adjust=False).mean()
            _ind["adx"] = round(float(_adx.iloc[-1]), 1)
            _ind["plus_di"] = round(float(_plus_di.iloc[-1]), 1)
            _ind["minus_di"] = round(float(_minus_di.iloc[-1]), 1)
            # [P1-补强] 输出 ATR（绝对值），供 midlong_helpers 的 volatility_pct 兜底
            # 补算（atr_1d_pct 缺失时用 atr/current_price）。
            _ind["atr"] = round(float(_atr.iloc[-1]), 6)
    except Exception:
        pass
    if "volume" in kdf.columns and len(kdf) >= 20:
        _vol = kdf["volume"]
        _period_sec_map = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
            "12h": 43200, "1d": 86400, "1w": 604800, "1M": 2592000,
        }
        _period_sec = _period_sec_map.get(period, 0) if period else 0
        _last_ts = (
            int(kdf["timestamp"].iloc[-1])
            if "timestamp" in kdf.columns and kdf["timestamp"].iloc[-1] is not None
            else 0
        )
        _partial = bool(
            _period_sec > 0 and _last_ts > 0
            and int(time.time()) < _last_ts + _period_sec
        )
        if _partial and len(kdf) >= 2:
            _cur_vol = float(_vol.iloc[-2])
            _vol_ma = float(_vol.iloc[-21:-1].mean())
        else:
            _cur_vol = float(_vol.iloc[-1])
            _vol_ma = float(_vol.iloc[-20:].mean())
        _ind["vol_ratio"] = (
            round(_cur_vol / _vol_ma, 2)
            if _vol_ma > 0 and _cur_vol > 0
            else None
        )
    # 最近30根 OHLCV（带时间）喂提示词 / evidence / MLTO brief
    _cols = [c for c in ("datetime", "timestamp", "open", "high", "low", "close", "volume") if c in kdf.columns]
    _recent = kdf.tail(30)[_cols].round(4) if _cols else kdf.tail(30)
    _ind["recent_klines"] = _recent.to_dict("records")
    return _ind


def _indicator_block_incomplete(ind: dict | None) -> bool:
    """已有 indicators_* 但缺 quant brief 关键字段时，需要重算。"""
    if not isinstance(ind, dict) or not ind:
        return True
    return any(ind.get(k) is None for k in ("rsi", "ema_trend", "macd_hist", "adx", "trend"))


def _inject_structure_levels(ms: dict) -> None:
    """从 4h K线最近 8 根摆动高低点补算支撑/阻力，写入 ms['structure_levels']。

    仅供 quant_brief 展示与 LLM 关键位参考；数据不足或结构位不跨现价两侧时
    保持缺失（下游输出诚实的“无明确支撑/阻力数据”文案），绝不虚构数值。
    """
    if not isinstance(ms, dict) or ms.get("structure_levels"):
        return
    _ind4 = ms.get("indicators_4h")
    if not isinstance(_ind4, dict):
        return
    rows = _ind4.get("recent_klines")
    if not isinstance(rows, list) or len(rows) < 8:
        return
    highs: list = []
    lows: list = []
    for row in rows[-8:]:
        if not isinstance(row, dict):
            continue
        try:
            h = float(row.get("high") or 0)
            l = float(row.get("low") or 0)
        except (TypeError, ValueError):
            continue
        if h > 0:
            highs.append(h)
        if l > 0:
            lows.append(l)
    if len(highs) < 3 or len(lows) < 3:
        return
    price = float(ms.get("current_price") or ms.get("price") or 0)
    support = min(lows)
    resistance = max(highs)
    if price > 0 and (support >= price or resistance <= price):
        return
    ms["structure_levels"] = {"support": support, "resistance": resistance}


def inject_midlong_indicators(
    market_summary: dict, symbol: str, include_weekly: bool = False,
) -> None:
    sym = str(symbol).upper()
    if not isinstance(market_summary, dict):
        return
    # 注意：空 dict {} 在 Python 里是 falsy，不能用 `get() or {}`，否则会丢掉
    # market_summary 里已有的引用，指标写到游离 dict、调用方永远看不到。
    ms = market_summary.get(sym)
    if ms is None and symbol != sym:
        ms = market_summary.get(symbol)
    if not isinstance(ms, dict):
        ms = {}
    market_summary[sym] = ms
    try:
        from backend.services.decision_core.regime_agent import classify_regime
        _reg = classify_regime(ms)
        _fresh_regime = {
            "name": _reg.regime,
            "size_multiplier": getattr(_reg, "size_multiplier", 1.0),
            "detail": getattr(_reg, "detail", "") or "",
        }
        _existing_regime = ms.get("regime")
        if isinstance(_existing_regime, dict):
            # 陈旧/占位 name（unknown/空）用新分类愈合；已有有效 name 保留
            if not _existing_regime.get("name") or str(
                _existing_regime.get("name")
            ).strip().lower() in ("", "unknown"):
                _existing_regime["name"] = _fresh_regime["name"]
            _existing_regime.setdefault(
                "size_multiplier", _fresh_regime["size_multiplier"]
            )
            _existing_regime.setdefault("detail", _fresh_regime["detail"])
        else:
            ms["regime"] = _fresh_regime
    except Exception:
        pass
    # S4 基座：把中长线活跃因子在 4h/1d 的读数注入 market_data，供 Swing/Trend 参考。
    try:
        from backend.config.settings import MIDLONG_FACTOR_RESEARCH_ENABLED
        if MIDLONG_FACTOR_RESEARCH_ENABLED and "midlong_factors" not in ms:
            from backend.services.factor_engine.midlong_active_factor_set import (
                midlong_active_factor_set,
            )
            _snap = midlong_active_factor_set.build_snapshot(sym)
            if _snap.get("count"):
                ms["midlong_factors"] = _snap
    except Exception:
        pass
    # 长线(include_weekly)把 1w 也纳入"是否需要补K线"的判断，否则 1h/4h/1d 齐了
    # 就提前 return、周线永远补不上 → 长线继续被 StrictData 卡死。
    # [2026-08-10 v3.1.0] 中线（非 weekly）额外纳入 15m：供入场择时验证，
    # qual_layer 的 K 线摘要段优先读 indicators_15m.recent_klines。
    _tfs = ("1h", "4h", "1d", "1w") if include_weekly else ("15m", "1h", "4h", "1d")
    # 缺整块 或 缺 macd/adx/trend → 都要重算（不能因“有个空壳 indicators_1h”就提前 return）
    _need_klines = any(_indicator_block_incomplete(ms.get(f"indicators_{_tf}")) for _tf in _tfs)
    if not _need_klines:
        # 仍同步 trend_1w 别名，供 quant brief 读取
        _iw = ms.get("indicators_1w") if isinstance(ms.get("indicators_1w"), dict) else {}
        if _iw.get("trend") or _iw.get("ema_trend"):
            ms["trend_1w"] = _iw.get("trend") or _iw.get("ema_trend")
        if ms.get("indicators_1d") and isinstance(ms["indicators_1d"], dict):
            _adx = ms["indicators_1d"].get("adx")
            if _adx is not None:
                ms["adx_1d"] = _adx
        try:
            from backend.services.decision_core.mtf_resonance import inject_mtf_into_market_summary
            inject_mtf_into_market_summary(ms)
        except Exception:
            pass
        try:
            from backend.services.orchestrator_derivatives import inject_derivatives_into_market_summary
            inject_derivatives_into_market_summary(market_summary, sym)
        except Exception:
            pass
        _inject_structure_levels(ms)
        return
    try:
        import pandas as _kp
        from backend.services.kline_data_service import kline_service as _ks
        for _tf in _tfs:
            if not _indicator_block_incomplete(ms.get(f"indicators_{_tf}")):
                continue
            # 周线数据天然稀少，最小根数放宽到 8（对齐主循环 :8776）；其余周期仍要 20 根。
            _min_bars = 8 if _tf == "1w" else 20
            # 决策热路径：只走 data_center(purpose=trade)，禁止 get_kline_data 旁路/过期兜底
            _raw = _ks.get_aggregated_klines(sym, _tf, count=60)
            if not _raw or len(_raw) < _min_bars:
                logger.info(
                    "[MidLong] %s/%s K线不足(%s<%s)，跳过该周期（不跨所/不借大盘）",
                    sym, _tf, len(_raw or []), _min_bars,
                )
                continue
            _kdf = _kp.DataFrame(_raw)
            if "datetime" not in _kdf.columns and "timestamp" in _kdf.columns:
                _kdf["datetime"] = _kp.to_datetime(_kdf["timestamp"], unit="s", utc=True).astype(str)
            ms[f"indicators_{_tf}"] = _compute_midlong_indicator_block(
                _kdf, period=_tf
            )
            # [P1-修复] 从 1h K线补算 price_change_1h/24h_pct 与 volatility_pct：
            # 独立循环 merge 后这三个字段可能缺失，导致 classify_regime 恒判 ranging。
            # 用与 unified_data_pool 一致的 1h 口径（close[-1]/close[-2]、close[-1]/close[-25]）。
            if _tf == "1h" and len(_kdf) >= 2:
                _c = _kdf["close"].astype(float)
                _p_last = float(_c.iloc[-1])
                if _p_last > 0:
                    # StrictData / 下单价：扫描层偶发只带 indicators 不带 price
                    if float(ms.get("price") or 0) <= 0:
                        ms["price"] = _p_last
                    if float(ms.get("current_price") or 0) <= 0:
                        ms["current_price"] = _p_last
                    if ms.get("price_change_1h_pct") is None and float(_c.iloc[-2]) > 0:
                        ms["price_change_1h_pct"] = round(
                            (_p_last / float(_c.iloc[-2]) - 1.0) * 100.0, 4,
                        )
                    if ms.get("price_change_24h_pct") is None and len(_c) >= 25 and float(_c.iloc[-25]) > 0:
                        ms["price_change_24h_pct"] = round(
                            (_p_last / float(_c.iloc[-25]) - 1.0) * 100.0, 4,
                        )
                # [2026-09-09 位置闸数据源] 就地缓存 24h 高低沿（避免位置闸再去拉 K 线）。
                # 依据：_audit_ml/21_timing.py 实测「入场价在 24h 区间 80-100% 分位」
                # 的 33 笔入场后 24h 均值 -3.89%/胜率 0.152。位置闸靠这两个字段算分位。
                try:
                    if "high" in _kdf.columns and "low" in _kdf.columns and len(_kdf) >= 2:
                        _h = _kdf["high"].astype(float).iloc[-24:]
                        _l = _kdf["low"].astype(float).iloc[-24:]
                        if len(_h) > 0 and len(_l) > 0:
                            ms["range_24h_high"] = float(_h.max())
                            ms["range_24h_low"] = float(_l.min())
                except Exception as _rg_err:
                    logger.debug("[MidLong] 24h 高低沿缓存跳过 %s: %s", sym, _rg_err)
        # 补 volatility_pct（口径与 classify_regime 期望一致：ATR/price 小数 0.01~0.05）
        if ms.get("volatility_pct") is None:
            _v = ms.get("atr_1d_pct") or 0
            if float(_v or 0) > 0:
                ms["volatility_pct"] = float(_v)
            else:
                _id1 = ms.get("indicators_1d") if isinstance(ms.get("indicators_1d"), dict) else {}
                _atr = _id1.get("atr")
                _px = ms.get("current_price") or 0
                if _atr and float(_px or 0) > 0:
                    ms["volatility_pct"] = float(_atr) / float(_px)
        # 顶层别名：quant brief 读 md.trend_1w / md.adx_1d
        _iw = ms.get("indicators_1w") if isinstance(ms.get("indicators_1w"), dict) else {}
        if _iw.get("trend") or _iw.get("ema_trend"):
            ms["trend_1w"] = _iw.get("trend") or _iw.get("ema_trend")
        _id = ms.get("indicators_1d") if isinstance(ms.get("indicators_1d"), dict) else {}
        if _id.get("adx") is not None:
            ms["adx_1d"] = _id.get("adx")
        from backend.services.decision_core.mtf_resonance import inject_mtf_into_market_summary
        inject_mtf_into_market_summary(ms)
    except Exception as err:
        logger.debug("[MidLong] 指标注入 %s 跳过: %s", sym, err)
    try:
        from backend.services.orchestrator_derivatives import inject_derivatives_into_market_summary
        inject_derivatives_into_market_summary(market_summary, sym)
    except Exception:
        pass
    _inject_structure_levels(ms)
