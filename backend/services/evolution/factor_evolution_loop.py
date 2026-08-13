"""
因子进化闭环 — FactorEvolutionLoop。

═══════════════════════════════════════════════════════════════════════
  自动因子挖掘 → 验证 → 清洗 → 上线 → 监控 → 衰退替换 → 在线学习
  每日定时运行，产出可用因子到活跃集。
═══════════════════════════════════════════════════════════════════════

串联 P1 基础设施：
  data_center → AlphaMiner + 种子因子 → evaluation → purge_pipeline
  → lifecycle + shadow_judge → 活跃集 → drift_watcher → 替换
  → online_weights

调度：main.py APScheduler 每日凌晨 3 点 + 每小时在线权重。

持久化：Analytics DB (factor_evolution_log + factor_active_set)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 仅当「无会话固定币、无训练核心、无 FACTOR_EVO_SYMBOLS」时的最后兜底。
# 这不是用户固定币白名单；运维台也不应把它标成「你的固定币」。
DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "ASTER"]
DEFAULT_PERIOD = "4h"


def resolve_evolution_symbols(symbols=None) -> list[str]:
    """解析本轮进化实际用币。

    优先级：
      1. 调用方显式传入
      2. env FACTOR_EVO_SYMBOLS
      3. running 会话固定币 ∪ 全局固定币备选池(user_trading_pairs)
         —— 会话当前启用的固定币 + 你在交易对配置里的备选池
      4. TRAINING_CORE_SYMBOLS
      5. DEFAULT_SYMBOLS 最后兜底（不是备选池）
    """
    import os as _os

    if symbols:
        out = [str(s).strip().upper() for s in symbols if str(s).strip()]
        if out:
            return list(dict.fromkeys(out))

    env_raw = (_os.getenv("FACTOR_EVO_SYMBOLS") or "").strip()
    if env_raw:
        out = [s.strip().upper() for s in env_raw.split(",") if s.strip()]
        if out:
            return list(dict.fromkeys(out))

    session_fixed: list[str] = []
    try:
        from sqlalchemy import text as _sa_text

        from backend.database.connection import SessionLocal
        from backend.services.auto_coin_selector import get_fixed_symbols_for_session

        db = SessionLocal()
        try:
            try:
                db.connection().exec_driver_sql("SET app.is_admin = 'on'")
            except Exception:
                pass
            rows = db.execute(
                _sa_text(
                    "SELECT session_id FROM full_auto_sessions WHERE status = 'running'"
                )
            ).fetchall()
            for r in rows or []:
                sid = str(r[0] or "")
                if not sid:
                    continue
                try:
                    fixed = get_fixed_symbols_for_session(sid, db)
                    # set 无序；按字母稳定一下，避免 SOL 莫名排第一
                    session_fixed.extend(sorted(str(s).upper() for s in (fixed or [])))
                except Exception:
                    continue
        finally:
            db.close()
    except Exception as e:
        logger.debug("[FactorEvo] 读会话固定币失败: %s", e)

    backup_pool: list[str] = []
    try:
        from backend.services.trading_pairs_config import get_user_trading_pairs
        backup_pool = [
            str(s).strip().upper() for s in (get_user_trading_pairs() or []) if s
        ]
    except Exception as e:
        logger.debug("[FactorEvo] 读固定币备选池失败: %s", e)

    # 备选池保留用户配置顺序；会话固定币补在前面（当前在跑的优先）
    merged = list(dict.fromkeys([*session_fixed, *backup_pool]))
    if merged:
        return merged

    try:
        from backend.config.settings import TRAINING_CORE_SYMBOLS
        core = [str(s).strip().upper() for s in (TRAINING_CORE_SYMBOLS or []) if s]
        if core:
            return list(dict.fromkeys(core))
    except Exception:
        pass

    return list(DEFAULT_SYMBOLS)

# [2026-07-18 数据窗口标准化 P1，规划文档§4.2] 原 2000根4h≈333天的训练窗口对
# crypto这种regime快速切换的市场太长，会把过时regime的噪声也当作信号。改为
# "训练90天+验证30天"，且不再是"合并成一个更小的窗口"这种表面改法——下面
# run_factor_evolution_loop 里真正把这120天切成两段：前90天喂给 _mine_candidates
# 做拟合/挖掘，后30天喂给 _evaluate_candidates 做IC评分。此前两个阶段用的是
# 同一份数据（同集合上拟合又评分），是一处隐藏的样本内偏差，现在改成样本外
# 评分，与下游DSR/PBO硬门形成双重防过拟合。
import os as _os_window

TRAIN_DAYS = int(_os_window.getenv("FACTOR_EVO_TRAIN_DAYS", "90"))
VAL_DAYS = int(_os_window.getenv("FACTOR_EVO_VAL_DAYS", "30"))
# [2026-08-05 三层切分 v6 计划 5.4.3] 按周期分档的训练/验证/测试窗口（天）：
#   1m/5m/15m 短窗 30/10/10；30m 60/20/15；1h 中窗 90/30/15；2h 120/45/30；
#   4h/8h/1d 长窗 180/60/30（测试集绝不参与挖掘与选因）。
_PERIOD_SPLIT_DAYS: dict[str, tuple[int, int, int]] = {
    "1m": (30, 10, 10), "5m": (30, 10, 10), "15m": (30, 10, 10),
    "30m": (60, 20, 15), "1h": (90, 30, 15), "2h": (120, 45, 30),
    "4h": (180, 60, 30), "8h": (180, 60, 30), "1d": (180, 60, 30),
}
_BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48,
                 "1h": 24, "2h": 12, "4h": 6, "8h": 3, "1d": 1}


def _split_days_for_period(period: str | None) -> tuple[int, int, int]:
    """周期分档切分天数；env 显式设置（FACTOR_EVO_TRAIN/SPLIT_DAYS）优先覆盖。"""
    p = period or DEFAULT_PERIOD
    env_train = _os_window.getenv("FACTOR_EVO_TRAIN_DAYS")
    env_val = _os_window.getenv("FACTOR_EVO_VAL_DAYS")
    env_test = _os_window.getenv("FACTOR_EVO_TEST_DAYS")
    if env_train and env_val:
        try:
            return (int(env_train), int(env_val),
                    int(env_test) if env_test else 0)
        except (TypeError, ValueError):
            pass
    return _PERIOD_SPLIT_DAYS.get(p, _PERIOD_SPLIT_DAYS["4h"])


def _evo_gate_fail_closed() -> bool:
    """进化链门禁异常时是否 fail-closed（默认 True，与 Paper 交易 fail-open 拆开）。"""
    raw = (_os_window.getenv("FACTOR_EVO_GATE_FAIL_CLOSED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _mine_symbol_keys(dfs: dict) -> list[str]:
    keys = [str(k) for k in dfs.keys()]
    try:
        k = int(_os_window.getenv("FACTOR_MINE_SYMBOLS", "5") or 5)
    except (TypeError, ValueError):
        k = 5
    return keys[: max(1, min(k, len(keys)))]


def _stack_mine_panel(dfs: dict, symbol_keys: list[str]):
    """多币拼接面板：挖矿适应度不再只绑第一币。"""
    field_dicts = []
    targets = []
    for s in symbol_keys:
        df = dfs[s]
        field_dicts.append(_kline_to_fields(df))
        targets.append(np.asarray(_forward_returns(df), dtype=float))
    target = np.concatenate(targets) if targets else np.array([], dtype=float)

    def eval_fn(ctx):
        expr = ctx["expr"]
        parts = []
        for fields, df_len in zip(field_dicts, [len(t) for t in targets]):
            try:
                parts.append(np.asarray(expr.evaluate(fields), dtype=float))
            except Exception:
                parts.append(np.zeros(df_len, dtype=float))
        return np.concatenate(parts) if parts else np.array([], dtype=float)

    field_names = sorted({k for fd in field_dicts for k in fd.keys()})
    return eval_fn, target, field_names


def _lookback_for_period(period: str | None) -> int:
    """按周期取三段窗口总和（根数）+ 50 根安全缓冲，供取数 lookback。

    必须与 _split_train_val_test 的 need（三段和 + 50）对齐，否则取数不足
    → 三段切分必然退化为全窗口（2026-08-06 审计实锤：DEFAULT_LOOKBACK=1620
    < need=1670，四段切分从未真实生效）。
    """
    p = period or DEFAULT_PERIOD
    td, vd, ted = _split_days_for_period(p)
    bpd = _BARS_PER_DAY.get(p, 6)
    return (td + vd + ted) * bpd + 50


TRAIN_BARS = TRAIN_DAYS * _BARS_PER_DAY.get(DEFAULT_PERIOD, 6)
VAL_BARS = VAL_DAYS * _BARS_PER_DAY.get(DEFAULT_PERIOD, 6)
DEFAULT_LOOKBACK = _lookback_for_period(DEFAULT_PERIOD)

# 当前进化任务周期（run_factor_evolution_loop 进入时设置；供目标标签切换）
_ACTIVE_EVO_PERIOD: str | None = None

# [2026-08-13 P1-5] 标签前瞻期按周期对齐实盘 scalp ATR 持仓节奏：
# 1m/3m/5m 持仓分钟~小时级 → 12 根；15m → 6 根；30m → 4 根；
# 1h → 2 根（2h 持仓）；2h/4h/8h/1d → 1 根（随 bar 拉长）。
_PERIOD_FWD_BARS: dict[str, int] = {
    "1m": 12, "3m": 12, "5m": 12, "15m": 6, "30m": 4,
    "1h": 2, "2h": 1, "4h": 1, "8h": 1, "1d": 1,
}


def _fwd_bars_for_period(period: str | None = None, fallback: int = 5) -> int:
    """前瞻期分档；env FACTOR_EVO_FWD_BARS 显式覆盖时优先（可回滚）。"""
    env = _os_window.getenv("FACTOR_EVO_FWD_BARS")
    if env:
        try:
            return max(1, int(env))
        except (TypeError, ValueError):
            pass
    p = (period or _ACTIVE_EVO_PERIOD or "").strip().lower()
    return _PERIOD_FWD_BARS.get(p, fallback)


def _forward_returns(df: pd.DataFrame, horizon: int | None = None) -> np.ndarray:
    """评估/清洗目标序列。

    默认：未来 horizon 根简单收益；horizon 由周期分档（_PERIOD_FWD_BARS，
    对应 scalp ATR 持仓节奏），FACTOR_EVO_FWD_BARS 可显式覆盖。
    当 FEATURE_FACTOR_LABELS_ENABLED 且当前进化 period∈{5m,15m}：改用三重障碍
    标签（-1/0/+1 → float），使挖矿目标更贴近短线 SL/TP/超时结算。
    """
    horizon = int(horizon) if horizon is not None else _fwd_bars_for_period()
    period = (_ACTIVE_EVO_PERIOD or "").strip().lower()
    use_tb = False
    try:
        from backend.services.evolution.factor_labels import (
            FEATURE_FACTOR_LABELS_ENABLED,
            build_triple_barrier_labels,
        )
        use_tb = bool(FEATURE_FACTOR_LABELS_ENABLED) and period in (
            "5m", "5min", "15m", "15min",
        )
    except Exception:
        use_tb = False

    if use_tb:
        try:
            labels = build_triple_barrier_labels(df, horizon_bars=max(int(horizon), 12))
            arr = labels.astype(float).to_numpy()
            if arr is not None and len(arr) == len(df) and np.any(arr != 0):
                return arr
            logger.debug("[FactorEvo] 三重障碍标签为空，回退 forward returns")
        except Exception as e:
            logger.debug("[FactorEvo] 三重障碍标签失败，回退 forward returns: %s", e)

    close = df["close"].values.astype(float)
    if len(close) <= horizon:
        return np.zeros(len(close))
    fwd = np.zeros(len(close))
    fwd[:-horizon] = (close[horizon:] / close[:-horizon] - 1.0)
    return fwd


def _kline_to_fields(df: pd.DataFrame) -> dict[str, np.ndarray]:
    from backend.services.alpha.factor_compute import kline_df_to_fields
    return kline_df_to_fields(df)


# ═══════════════════════════════════════════════════════════════
#  DB 持久化
# ═══════════════════════════════════════════════════════════════

def _get_analytics_db():
    """获取 Analytics DB session（带超时保护）。"""
    from backend.database.connection import AnalyticsSessionLocal
    return AnalyticsSessionLocal()


def _log_evolution(factor_id: str, phase: str, **kwargs):
    """记录因子进化事件到 factor_evolution_log。"""
    try:
        from backend.database.models import FactorEvolutionLog
        db = _get_analytics_db()
        try:
            log_entry = FactorEvolutionLog(
                factor_id=factor_id,
                phase=phase,
                expr_ast=kwargs.get("expr_ast"),
                source=kwargs.get("source"),
                state_from=kwargs.get("state_from"),
                state_to=kwargs.get("state_to"),
                action=kwargs.get("action"),
                reason=kwargs.get("reason"),
                metrics=kwargs.get("metrics"),
            )
            db.add(log_entry)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.debug(f"[FactorEvo] 日志写入失败 {factor_id}: {e}")
        finally:
            db.close()
    except Exception:
        pass


def _save_active_factors(factors: list[dict]):
    """持久化活跃因子集到 factor_active_set（upsert by factor_id）。"""
    try:
        from backend.database.models import FactorActiveSet
        db = _get_analytics_db()
        try:
            now = datetime.now(timezone.utc)
            for f in factors:
                # [2026-08-06 2.3 修复] expr_ast 为空/缺失的因子拒绝写入：
                # 07-23 曾写入 100 行 expr_ast=NULL 的 QUARANTINE 死数据（永远无法
                # 解析求值，污染活跃集统计且每轮复评必失败）。缺表达式=不可评估=无意义。
                _ast = f.get("expr_ast")
                if not _ast:
                    logger.warning(
                        "[FactorEvo] 跳过写入空表达式因子 %s (source=%s)",
                        f.get("factor_id"), f.get("source"),
                    )
                    continue
                existing = db.query(FactorActiveSet).filter(
                    FactorActiveSet.factor_id == f["factor_id"]
                ).first()
                if existing:
                    existing.state = f.get("state", "ACTIVE")
                    # 隔离后再晋级必须刷回表达式，否则永久卡在不可求值的死行
                    if f.get("expr_ast"):
                        existing.expr_ast = f.get("expr_ast")
                    if f.get("expr_id") is not None:
                        existing.expr_id = f.get("expr_id")
                    if f.get("source") is not None:
                        existing.source = f.get("source")
                    existing.icir = f.get("icir")
                    existing.incremental_corr = f.get("incremental_corr")
                    existing.capacity_usd = f.get("capacity_usd")
                    existing.last_net_ic = f.get("last_net_ic")
                    existing.turnover = f.get("turnover")
                    existing.evaluated_cycles = f.get("evaluated_cycles")
                    existing.current_weight = f.get("current_weight")
                    existing.last_evaluated_at = now
                    # 重新进入可交易/影子态时清停用戳
                    if str(existing.state) in ("PAPER", "SMALL_LIVE", "ACTIVE", "ORTHO"):
                        existing.deactivated_at = None
                        if existing.activated_at is None:
                            existing.activated_at = now
                else:
                    db.add(FactorActiveSet(
                        factor_id=f["factor_id"],
                        expr_ast=f.get("expr_ast", {}),
                        expr_id=f.get("expr_id"),
                        source=f.get("source"),
                        state=f.get("state", "ACTIVE"),
                        icir=f.get("icir"),
                        incremental_corr=f.get("incremental_corr"),
                        capacity_usd=f.get("capacity_usd"),
                        last_net_ic=f.get("last_net_ic"),
                        turnover=f.get("turnover"),
                        evaluated_cycles=f.get("evaluated_cycles"),
                        current_weight=f.get("current_weight"),
                        activated_at=now,
                        last_evaluated_at=now,
                    ))
            db.commit()
            logger.info(f"[FactorEvo] 持久化 {len(factors)} 个活跃因子")
        except Exception as e:
            db.rollback()
            logger.warning(f"[FactorEvo] 活跃因子持久化失败: {e}")
        finally:
            db.close()
    except Exception:
        pass


def _load_active_factors() -> list[dict]:
    """从 DB 加载研究池因子（含 SMALL_LIVE），返回字典列表（含 expr 对象）。"""
    try:
        from backend.services.factor_engine.active_set_policy import (
            ActiveSetRole,
            load_factor_active_rows,
        )
        factors = load_factor_active_rows(ActiveSetRole.RESEARCH, parse_expr=True)
        logger.info(f"[FactorEvo] 从DB加载 {len(factors)} 个活跃因子(RESEARCH)")
        return factors
    except Exception:
        return []


def _deactivate_factor(factor_id: str):
    """标记因子为隔离/停用。"""
    try:
        from backend.database.models import FactorActiveSet
        db = _get_analytics_db()
        try:
            f = db.query(FactorActiveSet).filter(
                FactorActiveSet.factor_id == factor_id
            ).first()
            if f:
                f.state = "QUARANTINE"
                f.deactivated_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"[FactorEvo] 因子停用失败 {factor_id}: {e}")
        finally:
            db.close()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  阶段 1：取数
# ═══════════════════════════════════════════════════════════════

def _required_coverage_days(period: str | None) -> int:
    """三段切分所需覆盖天数（train+val+test），供深度门槛使用。"""
    td, vd, ted = _split_days_for_period(period)
    return int(td + vd + ted)


def _check_split_depth(dfs: dict[str, pd.DataFrame], period: str | None) -> dict:
    """P0-2：进化前检查库深是否够真三段切分。

    返回 {ok, need_days, need_bars, by_symbol, short_symbols}。
    ok=False 时调用方应中止并 nudge 深度回填，禁止假 OOS。
    """
    p = period or DEFAULT_PERIOD
    need_days = _required_coverage_days(p)
    need_bars = _lookback_for_period(p)
    by_symbol: dict[str, dict] = {}
    short: list[str] = []
    for sym, df in (dfs or {}).items():
        n = len(df)
        bpd = _BARS_PER_DAY.get(p, 6)
        days = float(n) / float(bpd) if bpd else 0.0
        ok_sym = n >= need_bars
        by_symbol[sym] = {"bars": n, "days": round(days, 2), "ok": ok_sym}
        if not ok_sym:
            short.append(sym)
    ok = bool(dfs) and not short
    return {
        "ok": ok,
        "period": p,
        "need_days": need_days,
        "need_bars": need_bars,
        "by_symbol": by_symbol,
        "short_symbols": short,
    }


def _nudge_depth_backfill(symbols, period: str | None) -> None:
    """数据不足时催促 DepthBackfillRunner（失败不影响主流程返回）。"""
    try:
        from backend.services.kline_history_sync import depth_backfill_runner
        syms = resolve_evolution_symbols(symbols)
        depth_backfill_runner.nudge(symbols=syms, periods=[period or DEFAULT_PERIOD])
    except Exception as e:
        logger.warning("[FactorEvo] depth backfill nudge 失败: %s", e)


def _load_data(symbols=None, period=None, lookback=None):
    """按周期取足三段切分所需 K 线（v6 5.4.3）。

    [2026-08-08 P0-1] 此前 `lookback or DEFAULT_LOOKBACK` 在 period=5m 时仍用
    4h 档 ≈1670 根，远小于 5m 需要的 ≈14450 根 → 三段切分必失败并静默退化。
    现改为 `_lookback_for_period(p)`，并记录 need/got。
    """
    syms = resolve_evolution_symbols(symbols)
    p = period or DEFAULT_PERIOD
    need = _lookback_for_period(p)
    lb = int(lookback) if lookback is not None else need
    logger.info("[FactorEvo] 本轮进化币池(%d): %s", len(syms), ",".join(syms))
    try:
        from backend.services.data_center import data_center
    except Exception:
        logger.warning("[FactorEvo] data_center 不可用，跳过")
        return {}

    dfs = {}
    got_bars: dict[str, int] = {}
    for sym in syms:
        try:
            # [2026-08-07 v6 s7 fix] 因子挖掘为研究/回放用途，改用 purpose="research"：
            # 多源择优取深度最深者，且不受 trade 新鲜度强拒（4h stale>8h 时
            # 交易路径拒用是风控设计，但历史回放挖掘不依赖最后一根新鲜度；
            # 曾因默认 trade 语义导致 16h stale 时因子日循环/小时权重全链停摆）。
            result = data_center.get_klines(sym, p, count=lb, purpose="research")
            df = result.to_dataframe()
            n = len(df)
            got_bars[sym] = n
            if n >= 100:
                dfs[sym] = df
        except Exception as e:
            logger.debug(f"[FactorEvo] 取数失败 {sym}/{p}: {e}")
            got_bars[sym] = 0
    max_got = max(got_bars.values()) if got_bars else 0
    logger.info(
        f"[FactorEvo] 阶段1 取数: {len(dfs)}/{len(syms)} 品种, period={p}, "
        f"need={need} got_max={max_got} lookback={lb}"
    )
    if dfs and max_got < need:
        logger.warning(
            f"[FactorEvo] 数据深度不足 period={p}: need={need} bars, "
            f"got_max={max_got} ({ {k: v for k, v in got_bars.items() if v > 0} })"
        )
    return dfs


def _split_train_val_test(
    dfs: dict[str, pd.DataFrame], period: str | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """三段式切分：训练 / 验证 / 测试（v6 计划 5.4.3）。

    - 训练集：仅喂给挖掘阶段做拟合（GP/模板挖掘）
    - 验证集：喂给评估阶段做样本外 IC 评分与选因（purge/DSR/PBO）
    - 测试集：绝不参与挖掘与选因，仅作为最终晋升前的 IC 复评裁判
    - 窗口按周期分档（_PERIOD_SPLIT_DAYS）；任一段不足 50 根时该品种整体跳过
    """
    td, vd, ted = _split_days_for_period(period)
    bpd = _BARS_PER_DAY.get(period or DEFAULT_PERIOD, 6)
    train_bars, val_bars, test_bars = td * bpd, vd * bpd, ted * bpd
    train, val, test = {}, {}, {}
    for sym, df in dfs.items():
        need = train_bars + val_bars + test_bars + 50
        if len(df) < need:
            continue
        test[sym] = df.iloc[-test_bars:] if test_bars else pd.DataFrame()
        val[sym] = df.iloc[-(test_bars + val_bars):-test_bars] if test_bars else df.iloc[-val_bars:]
        train[sym] = df.iloc[:train_bars]
    return train, val, test


def _split_train_val(dfs: dict[str, pd.DataFrame], val_bars: int = VAL_BARS):
    """两段式切分（兼容旧签名，内部走三段式前两段：训练 + 验证）。"""
    train, val, _test = _split_train_val_test(dfs)
    return train, val


def _final_test_confirm(promoted: list[dict], eval_results: dict, dfs_test: dict) -> list[dict]:
    """三层切分最终裁判（v6 计划 5.4.3）：测试集 IC 复评。

    测试集绝不参与挖掘与选因；仅在 WFO 通过后、落库前，用测试集做最后一次
    样本外复评：测试集 IC 均值低于 +0.01（FACTOR_EVO_TEST_IC_MIN，近零即无预测力）
    → 拦截晋升。fail-closed（默认）：测试集不可用 / 算不出 IC → 拦截（FACTOR_EVO_GATE_FAIL_CLOSED）。
    """
    from backend.services.factor_engine.evaluation import information_coefficient

    fail_closed = _evo_gate_fail_closed()
    if not dfs_test:
        if fail_closed:
            for p in promoted:
                _log_evolution(
                    p.get("factor_id"), "test_gate",
                    source=p.get("source"),
                    action="test_reject",
                    reason="test_set_missing_fail_closed",
                )
            logger.warning("[FactorEvo] 测试集缺失，fail-closed 拦截全部晋升 (%d)", len(promoted))
            return []
        return promoted
    kept = []
    for p in promoted:
        expr = p.get("expr") or (eval_results.get(p["factor_id"], {}) or {}).get("expr")
        if not expr:
            if fail_closed:
                _log_evolution(
                    p.get("factor_id"), "test_gate",
                    source=p.get("source"),
                    action="test_reject",
                    reason="no_expr_fail_closed",
                )
                continue
            kept.append(p)
            continue
        ics = []
        for sym, df in dfs_test.items():
            try:
                fields = _kline_to_fields(df)
                fwd = _forward_returns(df)
                ic = information_coefficient(expr.evaluate(fields), fwd)
                if ic is not None and np.isfinite(ic):
                    ics.append(ic)
            except Exception:
                continue
        if not ics:
            if fail_closed:
                _log_evolution(
                    p.get("factor_id"), "test_gate",
                    source=p.get("source"),
                    action="test_reject",
                    reason="test_ic_unavailable_fail_closed",
                )
                continue
            kept.append(p)
            continue
        test_ic = float(np.mean(ics))
        # [2026-08-13 P0-4] test_gate 收紧：test_ic < +0.01 拦截（近零 IC 无预测力）。
        # 原阈值 -0.005 只拦明显失效，噪声因子可蒙混晋升。参数化 FACTOR_EVO_TEST_IC_MIN。
        _test_ic_min = float(_os_window.getenv("FACTOR_EVO_TEST_IC_MIN", "0.01"))
        if test_ic < _test_ic_min:
            _log_evolution(
                p["factor_id"], "test_gate",
                source=p.get("source"),
                action="test_reject",
                reason=f"test_ic={test_ic:.4f} < {_test_ic_min}",
                metrics={"test_ic": test_ic, "n_test_symbols": len(ics)},
            )
            logger.warning(
                "[FactorEvo] 测试集 IC 低于门槛 %.4f，拦截晋升 %s: %.4f",
                _test_ic_min, p["factor_id"], test_ic,
            )
            continue
        p["test_ic"] = test_ic
        kept.append(p)
    if kept:
        logger.info("[FactorEvo] 测试集终审: %d/%d 通过 (test_ic 记录在因子卡)", len(kept), len(promoted))
    return kept


# ═══════════════════════════════════════════════════════════════
#  阶段 2：挖掘候选因子
# ═══════════════════════════════════════════════════════════════

def _mine_candidates(dfs, period=None, quick: bool = False):
    from backend.services.factor_engine.expr.parser import FactorExpr, parse
    candidates: list[tuple[FactorExpr, str]] = []

    for window in [5, 10, 20, 50]:
        try:
            rev_ast = {"op": "mul", "args": [
                {"c": -1},
                {"op": "mean", "args": [{"f": "returns"}, {"c": window}]},
            ]}
            candidates.append((parse(rev_ast), f"rev{window}"))
        except Exception:
            pass

    for window in [5, 10, 20]:
        try:
            mom_ast = {"op": "mean", "args": [{"f": "returns"}, {"c": window}]}
            candidates.append((parse(mom_ast), f"mom{window}"))
        except Exception:
            pass

    for window in [10, 20, 50]:
        try:
            vol_ast = {"op": "std", "args": [{"f": "returns"}, {"c": window}]}
            candidates.append((parse(vol_ast), f"vol{window}"))
        except Exception:
            pass

    for window in [10, 20]:
        try:
            vp_ast = {"op": "rank", "args": [
                {"op": "corr", "args": [{"f": "close"}, {"f": "volume"}, {"c": window}]}
            ]}
            candidates.append((parse(vp_ast), f"vp_corr{window}"))
        except Exception:
            pass

    for window in [20, 50]:
        try:
            ts_ast = {"op": "ts_rank", "args": [{"f": "close"}, {"c": window}]}
            candidates.append((parse(ts_ast), f"ts_rank{window}"))
        except Exception:
            pass

    try:
        from backend.services.factor_engine.perp_factors import PERP_FACTOR_EXPRS
        first_df = list(dfs.values())[0]
        fields = _kline_to_fields(first_df)
        if "funding" in fields or "oi" in fields:
            for name, ast in PERP_FACTOR_EXPRS.items():
                try:
                    candidates.append((parse(ast), f"perp_{name}"))
                except Exception:
                    pass
    except Exception:
        pass

    shared_pool = None
    _eval_fn = target = field_names = sym_keys = None

    # [根因修复] quick=止血模式：只保留种子/永续公式，禁止 GP/MCTS。
    # 此前 quick 仍跑 GP+MCTS（仅跳过 LLM），loky 常驻 15–25 分钟占满 CPU/GIL，
    # 表现为运维台「运行中 quick」卡死 + 前端 API 全超时。
    if quick:
        logger.info(
            "[FactorEvo] quick 模式：跳过 GP/MCTS，仅种子候选 n=%d",
            len(candidates),
        )
        logger.info(f"[FactorEvo] 阶段2 挖掘: {len(candidates)} 个候选")
        return candidates

    try:
        # [2026-08-05] GP 挖掘器（v6 计划 5.3.1）
        # [根因修复] 四路共享同一 AlphaPool；多币拼接面板适应度
        import os as _os_gp

        from backend.services.evolution.alpha_miner import AlphaPool
        from backend.services.evolution.gp_miner import GPConfig, GPMiner

        shared_pool = AlphaPool(capacity=80)
        sym_keys = _mine_symbol_keys(dfs)
        _eval_fn, target, field_names = _stack_mine_panel(dfs, sym_keys)
        logger.info(
            "[FactorEvo] 挖矿面板 symbols=%s n=%d shared_pool_cap=%d",
            sym_keys, len(target), shared_pool.capacity,
        )

        gp_config = GPConfig()
        for _env, _attr in (("FACTOR_GP_POPULATION", "population_size"),
                            ("FACTOR_GP_GENERATIONS", "generations"),
                            ("FACTOR_GP_SEEDS", "n_seeds"),
                            ("FACTOR_GP_MAX_WORKERS", "max_workers")):
            _v = _os_gp.getenv(_env)
            if _v:
                try:
                    setattr(gp_config, _attr, int(_v))
                except (TypeError, ValueError):
                    pass
        miner = GPMiner(field_names, _eval_fn, target, shared_pool, gp_config)
        admitted = miner.mine()
        logger.info(f"[FactorEvo] GP 挖掘: {len(admitted)} 命中入池")
        for expr, _contrib in admitted:
            candidates.append((expr, f"gp_{expr.expr_id[:8]}"))
    except Exception as e:
        logger.warning(f"[FactorEvo] GP 挖掘异常: {e}")

    # [2026-08-06 阶段2 S2-12] MCTS 挖掘器 — 复用 shared_pool / 多币面板
    try:
        import os as _os_mcts

        if _os_mcts.getenv("FACTOR_MCTS_ENABLED", "1") != "0":
            from backend.services.evolution.alpha_miner import AlphaPool
            from backend.services.evolution.mcts_miner import (
                MCTSConfig, MctsMiner, scale_for_period,
            )

            if shared_pool is None:
                shared_pool = AlphaPool(capacity=80)
                sym_keys = _mine_symbol_keys(dfs)
                _eval_fn, target, field_names = _stack_mine_panel(dfs, sym_keys)

            mcts_pool = shared_pool
            mcts_config = MCTSConfig(scale=scale_for_period(period))
            for _env, _attr in (("FACTOR_MCTS_ITERATIONS", "n_iterations"),
                                ("FACTOR_MCTS_ROOTS", "n_roots"),
                                ("FACTOR_MCTS_CHILDREN", "n_children"),
                                ("FACTOR_MCTS_MAX_WORKERS", "max_workers")):
                _v = _os_mcts.getenv(_env)
                if _v:
                    try:
                        setattr(mcts_config, _attr, int(_v))
                    except (TypeError, ValueError):
                        pass
            weak_seeds: list[dict] = []
            try:
                actives = _load_active_factors()
                actives.sort(key=lambda f: abs(float(f.get("icir") or 0.0)))
                weak_seeds = [
                    f["expr_ast"] for f in actives[: mcts_config.n_weak_seeds]
                    if f.get("expr_ast")
                ]
            except Exception:
                pass
            mcts_miner = MctsMiner(
                list(field_names), _eval_fn, target, mcts_pool,
                mcts_config, weak_seeds=weak_seeds,
            )
            mcts_admitted, mcts_chains = mcts_miner.mine()
            logger.info(
                f"[FactorEvo] MCTS 挖掘(scale={mcts_config.scale}): "
                f"{len(mcts_admitted)} 命中入池, 进化链 {len(mcts_chains)}"
            )
            for expr, _contrib in mcts_admitted:
                candidates.append((expr, f"mcts_{expr.expr_id[:8]}"))
            for ch in mcts_chains[:50]:
                try:
                    child_expr = parse(ch["child_ast"])
                    _log_evolution(
                        child_expr.expr_id, "mine",
                        expr_ast=ch["child_ast"],
                        source="mcts_chain",
                        action="chain_step",
                        reason=(
                            f"parent_fitness={ch['parent_fitness']:.4f}->"
                            f"child_fitness={ch['child_fitness']:.4f} "
                            f"(parent_id={parse(ch['parent_ast']).expr_id})"
                        ),
                        metrics={"child_ic": ch.get("child_ic")},
                    )
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"[FactorEvo] MCTS 挖掘异常: {e}")

    # [2026-08-08 P1-1] Codegen LLM — 复用 shared_pool
    try:
        import os as _os_llm
        _llm_on = _os_llm.getenv("FACTOR_CODEGEN_ENABLED", "1") != "0"
        if _llm_on and not quick:
            from backend.services.evolution.alpha_miner import AlphaMiner, AlphaPool, CodegenCritic

            if shared_pool is None:
                shared_pool = AlphaPool(capacity=80)
                sym_keys = _mine_symbol_keys(dfs)
                _eval_fn, target, field_names = _stack_mine_panel(dfs, sym_keys)

            fail_hints = []
            try:
                for f in (_load_active_factors() or [])[:5]:
                    fail_hints.append(
                        f"id={f.get('factor_id')} icir={f.get('icir')} "
                        f"source={f.get('source')}"
                    )
            except Exception:
                pass
            period_tag = period or DEFAULT_PERIOD
            # [2026-08-13 P1-10] 领域约束注入：目标持仓周期（=该周期 × TP/SL 节奏）、
            # taker+funding 成本、换手/容量自述——避免 LLM 生成脱离实盘成本结构的因子。
            _fwd_bars = _fwd_bars_for_period(period_tag)
            try:
                _taker_fee = float(_os_llm.getenv("FACTOR_CODEGEN_TAKER_FEE", "0.0021") or 0.0021)
                _funding_8h = float(_os_llm.getenv("FACTOR_CODEGEN_FUNDING_8H", "0.0001") or 0.0001)
            except (TypeError, ValueError):
                _taker_fee, _funding_8h = 0.0021, 0.0001
            _domain_hint = (
                f"Domain constraints: period={period_tag} → target holding horizon ≈ "
                f"{_fwd_bars} bars (scalp ATR-based TP/SL, minutes-to-hours). "
                f"Round-trip cost ≈ taker {_taker_fee:.4f}/side + perp funding "
                f"{_funding_8h:.4f}/8h on overnight holds; the signal must clear this "
                f"net of costs. State expected turnover (bars per flip) and capacity "
                f"(liquid majors only vs altcoins). "
            )
            prompt = (
                f"Generate crypto alpha factor AST for period={period_tag}. "
                f"{_domain_hint}"
                f"Prefer complementary hypotheses (momentum/reversal/vol/volume-price/"
                f"microstructure). Existing weak factors to improve: "
                f"{fail_hints or ['none']}. "
                f"Output JSON AST only; do NOT evaluate quality."
            )
            n_llm = int(_os_llm.getenv("FACTOR_CODEGEN_N", "8"))
            miner = AlphaMiner(shared_pool)
            critic = CodegenCritic()
            admitted_llm = miner.mine_llm_candidates(
                list(field_names), _eval_fn, target,
                prompt=prompt, n_candidates=n_llm, critic=critic,
            )
            logger.info(f"[FactorEvo] Codegen LLM 挖掘: {len(admitted_llm)} 命中入池")
            for expr, _contrib in admitted_llm:
                candidates.append((expr, f"llm_{expr.expr_id[:8]}"))
                try:
                    _log_evolution(
                        expr.expr_id, "mine",
                        expr_ast=expr.ast,
                        source="codegen_llm",
                        action="llm_admit",
                        reason=f"period={period_tag} prompt_weak={len(fail_hints)}",
                    )
                except Exception:
                    pass
        elif quick:
            logger.info("[FactorEvo] Codegen LLM 跳过（quick 修复模式）")
    except Exception as e:
        logger.warning(f"[FactorEvo] Codegen LLM 挖掘异常（显式降级）: {e}")

    logger.info(f"[FactorEvo] 阶段2 挖掘: {len(candidates)} 个候选")
    return candidates


# ═══════════════════════════════════════════════════════════════
#  阶段 3：验证
# ═══════════════════════════════════════════════════════════════

def _evaluate_candidates(candidates, dfs, period=None):
    from backend.services.factor_engine.evaluation import evaluate_factor

    results: dict[str, dict] = {}
    _period = period or DEFAULT_PERIOD
    for expr, source in candidates:
        sym_results = {}
        for sym, df in dfs.items():
            try:
                fields = _kline_to_fields(df)
                factor_values = expr.evaluate(fields)
                fwd = _forward_returns(df)
                mask = np.isfinite(factor_values) & np.isfinite(fwd)
                if mask.sum() < 50:
                    continue
                fs = pd.Series(factor_values[mask], index=df.index[mask])
                rs = pd.Series(fwd[mask], index=df.index[mask])
                r = evaluate_factor(expr.expr_id, fs, rs)
                sym_results[sym] = r
            except Exception:
                continue

        if sym_results:
            avg_icir = np.mean([r.icir for r in sym_results.values()])
            avg_ic = np.mean([r.ic_mean for r in sym_results.values()])
            avg_turnover = float(np.mean(
                [getattr(r, "turnover", 0) or 0 for r in sym_results.values()]
            ))
            from backend.services.evolution.factor_labels import net_ic as _net_ic
            avg_net_ic = float(_net_ic(avg_ic, avg_turnover))
            results[expr.expr_id] = {
                "expr": expr, "source": source,
                "avg_icir": float(avg_icir), "avg_ic": float(avg_ic),
                "avg_turnover": avg_turnover,
                "net_ic": avg_net_ic,
                "n_symbols": len(sym_results),
                "best_sym": max(sym_results, key=lambda s: abs(sym_results[s].icir)),
                "best_result": sym_results[max(sym_results, key=lambda s: abs(sym_results[s].icir))],
                "all_results": sym_results,
            }

            # [2026-08-05 v6 2.4 S2-4 / P1-2] 完整因子报告卡落库（factor card JSON →
            # factor_evolution_log.metrics.card）：IC/分层/显著性/衰减/
            # parsimony/数据质量/admission_gate。单项失败容错，绝不阻塞评估主流程。
            try:
                from backend.services.factor_engine.factor_card import build_factor_card
                _card = build_factor_card(
                    factor_id=expr.expr_id, expr=expr, dfs=dfs,
                    period=_period, horizon=5, source=source,
                )
                results[expr.expr_id]["factor_card"] = {
                    "admission": (_card or {}).get("admission"),
                    "quantile": {
                        k: (_card or {}).get("quantile", {}).get(k)
                        for k in ("long_short_sharpe", "top_excess_annual", "monotonic_r")
                    },
                    "ic_p": ((_card or {}).get("ic") or {}).get("p_value"),
                }
                _log_evolution(
                    expr.expr_id, "card",
                    expr_ast=getattr(expr, "ast", None),
                    source=source,
                    action="card_generated",
                    metrics={"card": _card, "net_ic": avg_net_ic},
                )
                _adm = ((_card or {}).get("admission") or {})
                if _adm and not _adm.get("passed", True):
                    logger.info(
                        "[FactorEvo] admission_gate 未过 %s: %s",
                        expr.expr_id, _adm.get("reasons"),
                    )
            except Exception as _card_err:
                # 短周期报告卡失败升为 warning，便于短线闭环验收
                _lvl = logger.warning if _period in ("1m", "3m", "5m", "15m") else logger.debug
                _lvl(
                    "[FactorEvo] 报告卡生成失败 %s: %s", expr.expr_id, str(_card_err)[:120]
                )

    logger.info(f"[FactorEvo] 阶段3 验证: {len(results)}/{len(candidates)} 有效")
    return results


# ═══════════════════════════════════════════════════════════════
#  阶段 4：清洗
# ═══════════════════════════════════════════════════════════════

def _purge_and_select(eval_results, dfs):
    from backend.services.factor_engine.lifecycle import LifecycleThresholds
    from backend.services.factor_engine.purge_pipeline import (
        CandidateFactor,
        PurgeConfig,
        run_purge_pipeline,
    )

    candidates = []
    for fid, info in eval_results.items():
        candidates.append(CandidateFactor(
            factor_id=fid, source_name=info["source"],
            expr_ast=info["expr"].ast,
        ))

    def factor_series_fn(c: CandidateFactor) -> pd.Series:
        from backend.services.factor_engine.expr.parser import parse as _parse
        info = eval_results.get(c.factor_id)
        if not info:
            return pd.Series()
        best_sym = info.get("best_sym")
        df = dfs.get(best_sym) if best_sym and best_sym in dfs else list(dfs.values())[0]
        try:
            fields = _kline_to_fields(df)
            expr = _parse(c.expr_ast)
            vals = expr.evaluate(fields)
            return pd.Series(vals, index=df.index)
        except Exception:
            return pd.Series()

    def factor_matrix_fn(cs: list) -> np.ndarray:
        cols = []
        for c in cs:
            s = factor_series_fn(c)
            cols.append(np.asarray(s.values, dtype=float))
        if not cols:
            return np.zeros((0, 0))
        # 对齐到最短长度
        m = min(len(x) for x in cols)
        return np.column_stack([x[-m:] for x in cols])

    first_df = list(dfs.values())[0]
    fwd = _forward_returns(first_df)
    return_series = pd.Series(fwd, index=first_df.index)
    sample_len = max(50, len(return_series))

    survivors, report = run_purge_pipeline(
        candidates,
        factor_series_fn=factor_series_fn,
        return_series=return_series,
        factor_matrix_fn=factor_matrix_fn,
        config=PurgeConfig(max_active_factors=50),
        thresholds=LifecycleThresholds(),
        dsr_pbo_gate=None,  # 走内置 default_dsr_pbo_gate
        sample_len=sample_len,
        n_total_candidates=len(candidates),
    )

    enriched = []
    for s in survivors:
        info = eval_results.get(s.factor_id, {})
        enriched.append({
            "expr": info.get("expr"), "source": s.source_name,
            "factor_id": s.factor_id, "eval_result": info.get("best_result"),
            "incremental_corr": s.incremental_corr, "expr_ast": s.expr_ast,
        })

    logger.info(f"[FactorEvo] 阶段4 清洗: {report.summary()}")
    return enriched


# ═══════════════════════════════════════════════════════════════
#  阶段 5：上线（lifecycle + shadow_judge + DSR/PBO）
# ═══════════════════════════════════════════════════════════════

def _auto_oversight_approve(metrics, judgment) -> bool:
    """无人工运营团队场景下的自动化 OversightAgent 复核。

    [2026-07-18 新增] lifecycle.py 设计上 SMALL_LIVE/ACTIVE 是高破坏性转换，
    要求 OversightAgent 审批、超时默认拒（见 shadow_judge.py 文档）——但项目
    没有 7x24 人工运营，若真的什么都不 approve()，因子会永久卡死在 PAPER，
    这跟"完全无法晋升"没有本质区别，等同于系统性判死刑（用户此前吐槽"系统
    瘫痪"就是同类问题）。

    这里用一层比 lifecycle 基础阈值更严格、独立的规则化复核替代人工审批：
    只有显著优于基础晋升线的因子才会被自动批准，既不放水，又给出真实出口。
    """
    from backend.services.factor_engine.lifecycle import FactorState, LifecycleThresholds
    t = LifecycleThresholds()
    if not metrics.dsr_significant or metrics.pbo > 0.30:  # 比基础 0.5 门槛更严
        return False
    if metrics.icir < t.min_icir:
        return False
    to_state = judgment.decision.to_state
    if to_state == FactorState.SMALL_LIVE:
        return (
            metrics.paper_sharpe >= t.min_paper_sharpe * 1.5
            and metrics.paper_days >= t.paper_min_days * 2
        )
    if to_state == FactorState.ACTIVE:
        return metrics.small_live_days >= t.small_live_min_days * 1.5
    return False


def _min_net_ic_threshold() -> float:
    import os as _os
    try:
        return float(_os.getenv("FACTOR_MIN_NET_IC", "0.02"))
    except (TypeError, ValueError):
        return 0.02


def _estimate_volume_usd(df: pd.DataFrame, bars: int = 288) -> float:
    """近 bars 根 K 线的成交额估算（volume × close），用于容量计算。"""
    try:
        if df is None or df.empty or "volume" not in df.columns or "close" not in df.columns:
            return 0.0
        tail = df.tail(bars)
        return float((tail["volume"].astype(float) * tail["close"].astype(float)).sum())
    except Exception:
        return 0.0


def _estimate_capacity_usd(df: pd.DataFrame, factor_turnover: float) -> float:
    from backend.services.evolution.factor_labels import capacity_usd as _cap
    vol = _estimate_volume_usd(df)
    # 单日成交额 ≈ 近24h（5m 为 288 根）估算；容量按设计公式
    return _cap(vol, factor_turnover)


def _estimate_capacity_usd_from_dfs(dfs, factor_turnover: float) -> float:
    """跨品种取最大可交易容量，避免 next(iter(dfs)) 抽到薄币把整轮晋升杀死。"""
    if not dfs:
        return 0.0
    best = 0.0
    for df in dfs.values():
        try:
            best = max(best, float(_estimate_capacity_usd(df, factor_turnover) or 0.0))
        except Exception:
            continue
    return best


def _promoted_rows_for_save(promoted: list[dict], period=None) -> list[dict]:
    """晋升通过门禁后的落库行（缺 expr_ast 的跳过，避免假晋升）。"""
    rows = []
    for p in promoted or []:
        ast = p.get("expr_ast")
        if not ast:
            # 兜底：从 eval_result / expr 取
            expr = p.get("expr")
            ast = getattr(expr, "ast", None) if expr is not None else None
        if not ast:
            logger.warning(
                "[FactorEvo] 晋升跳过落库(无表达式) %s source=%s",
                p.get("factor_id"), p.get("source"),
            )
            continue
        eval_result = p.get("eval_result")
        row = {
            "factor_id": p["factor_id"],
            "expr_ast": ast,
            "expr_id": p.get("expr_id") or p["factor_id"],
            "source": p.get("source"),
            "state": p.get("_to_state", "PAPER"),
            "icir": eval_result.icir if eval_result else p.get("icir"),
            "incremental_corr": p.get("incremental_corr"),
            "capacity_usd": p.get("capacity_usd"),
            "current_weight": None,
        }
        rows.append(row)
        # 回写，供后续监控/权重用
        p["expr_ast"] = ast
    return _tag_short_horizon_factors(rows, period)


def _log_promote_committed(promoted: list[dict], *, via: str = "main") -> None:
    """仅在落库成功后记 promote，避免「日志已晋级、库里没有」的假成功。"""
    for p in promoted or []:
        eval_result = p.get("eval_result")
        _log_evolution(
            p["factor_id"], "promote",
            expr_ast=p.get("expr_ast"),
            source=p.get("source"),
            state_from="ORTHO",
            state_to=p.get("_to_state", "PAPER"),
            action="promote",
            reason=p.get("_promote_reason") or "门禁通过并已落库",
            metrics={
                "icir": getattr(eval_result, "icir", None) if eval_result else p.get("icir"),
                "incremental_corr": p.get("incremental_corr"),
                "dsr_significant": p.get("_dsr_significant"),
                "pbo": p.get("_pbo"),
                "capacity_usd": p.get("capacity_usd"),
                "via": via,
            },
        )


def _trigger_meta_retrain_after_promote(n_promoted: int) -> None:
    """新因子进 PAPER 后异步拉一把元标签重训（与日调度解耦，补「挖→训」闭环）。"""
    if n_promoted <= 0:
        return
    try:
        import threading

        def _run():
            try:
                from backend.services.scalp_meta_trainer import train_and_validate
                report = train_and_validate()
                logger.info(
                    "[FactorEvo] 晋升后元标签重训完成 usable=%s auc=%s",
                    (report or {}).get("usable"),
                    (report or {}).get("oos_auc_lgbm"),
                )
            except Exception as e:
                logger.warning("[FactorEvo] 晋升后元标签重训失败: %s", str(e)[:200])

        threading.Thread(target=_run, name="scalp-meta-after-promote", daemon=True).start()
    except Exception as e:
        logger.debug("[FactorEvo] 元标签重训线程启动失败: %s", e)


def _promote_factors(
    survivors, eval_results, all_icir_values, n_total, dfs=None, period=None,
):
    from backend.services.evolution.shadow_judge import ShadowJudge
    from backend.services.factor_engine.dsr_pbo import compute_dsr_pbo_for_factors
    from backend.services.factor_engine.lifecycle import (
        FactorMetrics,
        FactorState,
        LifecycleThresholds,
    )

    # ── DSR/PBO 全局评估（P0-3：sample_len 用验证窗根数，对齐真 OOS）──
    _td, _vd, _ted = _split_days_for_period(period)
    _bpd = _BARS_PER_DAY.get(period or DEFAULT_PERIOD, 6)
    sample_len = max(50, int(_vd * _bpd))
    # n_trials = 实际参与 IC 评估的因子数（含池内已有），不用未评估模板虚增
    n_trials = max(len(all_icir_values), 1)
    # 空可交易池冷启动：全量搜索 breadth 会把 DSR 期望最大 SR 抬到天文数字，
    # 导致 ICIR>1 的 survivor 仍 dsr_significant=False、整轮零晋升。
    # 冷启动时用「过净IC的 survivor 数」作多重检验分母（仍防单点作弊，但不自杀）。
    try:
        from backend.services.factor_engine.active_set_policy import (
            ActiveSetRole,
            load_factor_active_rows,
        )
        _tradable_n = len(load_factor_active_rows(ActiveSetRole.TRADABLE))
    except Exception:
        _tradable_n = 0
    if _tradable_n == 0 and survivors:
        n_trials = max(len(survivors), 1)
        logger.info(
            "[FactorEvo] 空 TRADABLE 冷启动：DSR n_trials=%d（原搜索广度=%d）",
            n_trials, n_total,
        )
    dsr_pbo = compute_dsr_pbo_for_factors(
        icir_list=all_icir_values,
        n_total_candidates=n_trials,
        sample_len=sample_len,
    )
    dsr_significant = dsr_pbo.get("dsr_result", {}).get("significant", True)
    pbo_val = dsr_pbo.get("pbo_result", {}).get("pbo", 0.3)
    logger.info(
        f"[FactorEvo] DSR/PBO: dsr_sig={dsr_significant} pbo={pbo_val:.3f} "
        f"best_icir={dsr_pbo.get('best_icir')} n_factors={dsr_pbo.get('n_factors')} "
        f"sample_len={sample_len} n_trials={n_trials} (search_breadth={n_total})"
    )

    judge = ShadowJudge()
    promoted = []
    reject_reasons: list[dict] = []
    _cap_floor = LifecycleThresholds().min_capacity_usd

    for s in survivors:
        info = eval_results.get(s["factor_id"], {})
        if info.get("net_ic", 1.0) < _min_net_ic_threshold():
            reject_reasons.append({
                "factor_id": s["factor_id"], "reason": "net_ic",
                "detail": float(info.get("net_ic", 0) or 0),
            })
            logger.info(
                "[FactorEvo] 净IC不足跳过 %s: net_ic=%.4f",
                s["factor_id"], info.get("net_ic", 0),
            )
            continue
        eval_result = s.get("eval_result")
        if not eval_result:
            reject_reasons.append({"factor_id": s["factor_id"], "reason": "no_eval_result"})
            continue

        cap = _estimate_capacity_usd_from_dfs(
            dfs,
            float(getattr(eval_result, "turnover", 0) or 0),
        )
        # 成交额缺失：fail-closed 跳过（数据缺口≠数学过门）
        if cap <= 0:
            if _evo_gate_fail_closed():
                reject_reasons.append({
                    "factor_id": s["factor_id"], "reason": "capacity_missing",
                })
                logger.warning(
                    "[FactorEvo] capacity 无法估计 %s，fail-closed 跳过",
                    s["factor_id"],
                )
                continue
            logger.warning(
                "[FactorEvo] capacity 无法估计 %s，fail-open 用门槛地板 %.0f",
                s["factor_id"], _cap_floor,
            )
            cap = float(_cap_floor)

        metrics = FactorMetrics(
            factor_id=s["factor_id"],
            state=FactorState.ORTHO,
            audit_passed=True,
            has_bug=False,
            icir=eval_result.icir,
            monotonicity_p=eval_result.monotonicity_p,
            turnover=eval_result.turnover,
            halflife_bars=eval_result.halflife_bars,
            incremental_corr=s.get("incremental_corr", 1.0),
            dsr_significant=dsr_significant,
            pbo=pbo_val,
            capacity_usd=cap,
        )

        judgment = judge.judge(metrics)

        # [2026-07-18 新增] 需审批(SMALL_LIVE/ACTIVE)的转换过一层自动化复核，
        # 避免"从未 approve() → 永久卡在 PAPER"；不需审批的(→PAPER)维持原有
        # 自动执行逻辑不变。
        if judgment.pending_approval and _auto_oversight_approve(metrics, judgment):
            judge.approve(s["factor_id"])
            judgment.executed = True
            judgment.pending_approval = False
            logger.info(
                f"[FactorEvo] 自动化复核通过 {s['factor_id']}: "
                f"{judgment.decision.from_state}→{judgment.decision.to_state}"
            )

        if judgment.executed and judgment.decision.to_state != judgment.decision.from_state:
            # [2026-07-18 修复] 此前无条件落库 state="ACTIVE"，与这里真实算出的
            # judgment.decision.to_state（几乎总是 PAPER，因为 metrics.state 每次
            # 都从 ORTHO 起评）完全脱节——晋升等于"绕过影子期直接实盘"。现在真实
            # 状态随 to_state 走，只有状态机判定到 ACTIVE 才会写 ACTIVE。
            s["_to_state"] = judgment.decision.to_state.value
            s["capacity_usd"] = cap
            s["_dsr_significant"] = dsr_significant
            s["_pbo"] = pbo_val
            s["_promote_reason"] = judgment.decision.reason
            promoted.append(s)
            # 门禁通过先记 gate_pass；真正 promote 等落库后再写，避免假晋级日志
            _log_evolution(
                s["factor_id"], "promote",
                expr_ast=s.get("expr_ast"),
                source=s.get("source"),
                state_from=judgment.decision.from_state.value,
                state_to=judgment.decision.to_state.value,
                action="gate_pass",
                reason=judgment.decision.reason,
                metrics={
                    "icir": eval_result.icir,
                    "incremental_corr": s.get("incremental_corr"),
                    "dsr_significant": dsr_significant,
                    "pbo": pbo_val,
                    "capacity_usd": cap,
                },
            )
            logger.info(
                f"[FactorEvo] 门禁通过(待落库) {s['factor_id']}: "
                f"{judgment.decision.from_state}→{judgment.decision.to_state} ({judgment.decision.reason})"
            )
        else:
            failed = dict(judgment.decision.conditions_failed or {})
            reject_reasons.append({
                "factor_id": s["factor_id"],
                "reason": judgment.decision.reason or "gate",
                "conditions_failed": failed,
                "dsr_significant": dsr_significant,
                "pbo": pbo_val,
            })
            _log_evolution(
                s["factor_id"], "promote",
                expr_ast=s.get("expr_ast"),
                source=s.get("source"),
                action="promote_reject",
                reason=judgment.decision.reason,
                metrics={
                    "conditions_failed": failed,
                    "dsr_significant": dsr_significant,
                    "pbo": pbo_val,
                    "capacity_usd": cap,
                    "icir": getattr(eval_result, "icir", None),
                },
            )
            logger.info(
                "[FactorEvo] 晋升拒绝 %s: %s failed=%s",
                s["factor_id"], judgment.decision.reason, failed,
            )

    logger.info(
        f"[FactorEvo] 阶段5 上线: {len(promoted)}/{len(survivors)} 门禁通过, "
        f"dsr_sig={dsr_significant} pbo={pbo_val:.3f} rejects={len(reject_reasons)}"
    )
    # 供 quick 快路径把可审计拒绝原因带回报告
    for s in survivors:
        s.setdefault("_promote_rejects", reject_reasons)
    return promoted


# ═══════════════════════════════════════════════════════════════
#  阶段 6：监控（drift_watcher IC 衰减检测）
# ═══════════════════════════════════════════════════════════════

def _monitor_active(active_factors, dfs):
    from backend.services.evolution.drift_watcher import DriftWatcher
    from backend.services.factor_engine.evaluation import information_coefficient

    watcher = DriftWatcher()
    degraded = []

    for f in active_factors:
        expr = f.get("expr")
        if not expr:
            continue

        drifts = 0
        for sym, df in dfs.items():
            try:
                fields = _kline_to_fields(df)
                vals = expr.evaluate(fields)
                fwd = _forward_returns(df)
                ic = information_coefficient(vals, fwd)
                event = watcher.observe_error(f["factor_id"], ic, baseline=0.0)
                if event:
                    drifts += 1
            except Exception:
                continue

        if watcher.should_rollback(f["factor_id"]):
            degraded.append(f)
            _log_evolution(
                f["factor_id"], "monitor",
                source=f.get("source"),
                action="drift_detected",
                reason=f"IC衰减连续drifts={drifts}",
                metrics={"drift_count": drifts},
            )
            logger.warning(f"[FactorEvo] 因子退化 {f['factor_id']}: drifts={drifts}")

    logger.info(f"[FactorEvo] 阶段6 监控: {len(degraded)}/{len(active_factors)} 退化")
    return degraded


# ═══════════════════════════════════════════════════════════════
#  阶段 6.5：影子期推进（PAPER→SMALL_LIVE→ACTIVE，逐日复评）
# ═══════════════════════════════════════════════════════════════

def _shadow_paper_metrics(expr, dfs: dict, period: str | None = None) -> dict:
    """[2026-08-13 P2-11] 影子组合真实指标（替换 icir 代理 Sharpe）。

    对每个 symbol：z-score(|z|≥1) 定方向仓位 → 每 bar 净值收益 =
    pos[t] × fwd[t] − taker 成本 × |Δpos|。年化 Sharpe 按持仓周期
    （bars/day × 365 / fwd_bars）折算。序列不足时 sharpe=None（调用方回退 icir 代理）。
    """
    _cost = float(_os_window.getenv("FACTOR_EVO_SHADOW_COST", "0.0021") or 0.0021)
    _fwd_bars = _fwd_bars_for_period(period)
    _bpd = _BARS_PER_DAY.get(period or DEFAULT_PERIOD, 24)
    _rets: list[float] = []
    for _sym, _df in (dfs or {}).items():
        try:
            _fields = _kline_to_fields(_df)
            _vals = pd.Series(expr.evaluate(_fields))
            _z = (
                (_vals - _vals.rolling(30, min_periods=10).mean())
                / _vals.rolling(30, min_periods=10).std().replace(0, np.nan)
            )
            _pos = np.where(_z.abs().ge(1.0), np.sign(_z), 0.0)
            _fwd = pd.Series(_forward_returns(_df), index=_df.index)
            _dpos = np.abs(_pos - np.roll(_pos, 1))
            if len(_dpos):
                _dpos[0] = abs(float(_pos[0]))
            _net = _pos * _fwd.values - _cost * _dpos
            _net = _net[np.isfinite(_net)]
            _rets.extend(float(v) for v in _net)
        except Exception:
            continue
    if len(_rets) < 30:
        return {"sharpe": None, "mean_ret": None, "n_bars": len(_rets)}
    _arr = np.asarray(_rets, dtype=float)
    _mean = float(np.mean(_arr))
    _std = float(np.std(_arr))
    _periods_per_year = max(1.0, _bpd * 365.0 / max(1, _fwd_bars))
    _sharpe = (_mean / _std) * (_periods_per_year ** 0.5) if _std > 1e-12 else 0.0
    _sharpe = float(max(-5.0, min(5.0, _sharpe)))
    return {"sharpe": _sharpe, "mean_ret": _mean, "n_bars": len(_rets)}


def _live_backtest_deviation() -> "float | None":
    """[2026-08-13 P2-11] 实盘信号日志回溯偏差（scalp_signal_log 结算结果）。

    取 scalp_signal_log 最近 N 天已结算信号的平均净收益，作为「影子预测 vs
    真实执行」对照的实盘侧。样本不足（默认 <50 条）返回 None，调用方保持
    live_deviation 不参与硬拦截的旧行为。
    """
    try:
        _min_n = max(30, int(_os_window.getenv("FACTOR_EVO_LIVE_DEV_MIN_SAMPLES", "50") or 50))
        _days = int(_os_window.getenv("FACTOR_EVO_LIVE_DEV_LOOKBACK_DAYS", "30") or 30)
    except (TypeError, ValueError):
        _min_n, _days = 50, 30
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ScalpSignalLog
        _db = SessionLocal()
        try:
            _cutoff = int(time.time()) - _days * 86400
            _rows = (
                _db.query(ScalpSignalLog)
                .filter(ScalpSignalLog.settled == True,  # noqa: E712
                        ScalpSignalLog.signal_ts >= _cutoff,
                        ScalpSignalLog.net_ret.isnot(None))
                .all()
            )
        finally:
            _db.close()
        _rets = [float(r.net_ret) for r in _rows if r.net_ret is not None]
        if len(_rets) < _min_n:
            return None
        return float(np.mean(_rets))
    except Exception as exc:
        logger.debug("[FactorEvo] live 回溯偏差读取失败: %s", exc)
        return None


def _advance_shadow_factors(existing_active: list[dict], dfs) -> list[dict]:
    """对已在 PAPER/SMALL_LIVE 影子期的因子逐日复评，判断是否可推进到下一状态。

    [2026-07-18 新增] `_promote_factors` 只处理"本轮新挖到的候选"（首次评估，
    起点固定 ORTHO）；已经进入 PAPER/SMALL_LIVE 影子期的因子此前**没有任何代码
    路径**会再去复评它们能不能往下走一步——`_monitor_active` 只查退化，不查
    推进。结果是一旦进 PAPER 就再也不会被检查是否该"毕业"，等价于影子期有入口
    没出口。这里补上：每天用当天重算的 ICIR + 影子期已持续天数复评一次。

    注意（诚实标注局限）：
      - [2026-08-13 P2-11] paper_sharpe 改为影子组合真实 Sharpe（z-score 仓位 ×
        前瞻收益 − taker 成本，按持仓周期年化）；序列不足时回退 icir 代理。
      - [2026-08-13 P2-11] live_deviation 改为「影子组合平均净收益 vs
        scalp_signal_log 实盘结算平均净收益」的偏差；无实盘结算样本时保持 0
        （不参与硬拦截）。
    """
    from datetime import datetime
    from datetime import timezone as _tz

    from backend.services.evolution.shadow_judge import ShadowJudge
    from backend.services.factor_engine.evaluation import information_coefficient
    from backend.services.factor_engine.lifecycle import FactorMetrics, FactorState

    judge = ShadowJudge()
    advanced = []
    now = datetime.now(_tz.utc)

    for f in existing_active:
        state_str = f.get("state")
        if state_str not in ("PAPER", "SMALL_LIVE"):
            continue
        expr = f.get("expr")
        if not expr:
            continue

        # 重算当前 ICIR（跨可用品种取均值）
        ics = []
        for sym, df in dfs.items():
            try:
                fields = _kline_to_fields(df)
                vals = expr.evaluate(fields)
                fwd = _forward_returns(df)
                ic = information_coefficient(vals, fwd)
                if ic is not None and np.isfinite(ic):
                    ics.append(ic)
            except Exception:
                continue
        icir = float(np.mean(ics)) if ics else float(f.get("icir") or 0.0)

        activated_at = f.get("activated_at")
        days_in_state = 0
        if activated_at:
            try:
                if activated_at.tzinfo is None:
                    activated_at = activated_at.replace(tzinfo=_tz.utc)
                days_in_state = max(0, (now - activated_at).days)
            except Exception:
                days_in_state = 0

        paper_sharpe_proxy = max(0.0, min(3.0, icir * (252 ** 0.5)))
        # [2026-08-13 P2-11] paper_sharpe：影子组合真实 Sharpe（回退 icir 代理）；
        # live_deviation：影子组合平均净收益 vs 实盘 scalp_signal_log 结算平均
        # 净收益的偏差（无实盘结算样本时保持 0，不参与硬拦截）。
        _shadow = _shadow_paper_metrics(expr, dfs)
        paper_sharpe_real = _shadow.get("sharpe")
        if paper_sharpe_real is None:
            paper_sharpe_real = paper_sharpe_proxy
        _live_ret = _live_backtest_deviation()
        if _live_ret is None:
            live_deviation = 0.0
        else:
            _shadow_ret = _shadow.get("mean_ret")
            live_deviation = (
                abs(_shadow_ret - _live_ret) if _shadow_ret is not None else 1.0
            )

        metrics = FactorMetrics(
            factor_id=f["factor_id"],
            state=FactorState(state_str),
            icir=icir,
            monotonicity_p=0.01,
            turnover=0.3,
            halflife_bars=10,
            incremental_corr=f.get("incremental_corr", 0.3) or 0.3,
            dsr_significant=True,
            pbo=0.3,
            capacity_usd=f.get("capacity_usd") or 1e5,
            paper_sharpe=paper_sharpe_real,
            live_deviation=live_deviation,
            paper_days=days_in_state if state_str == "PAPER" else 999,
            small_live_days=days_in_state if state_str == "SMALL_LIVE" else 0,
        )

        judgment = judge.judge(metrics)
        if judgment.pending_approval and _auto_oversight_approve(metrics, judgment):
            judge.approve(f["factor_id"])
            judgment.executed = True

        if judgment.executed and judgment.decision.to_state != judgment.decision.from_state:
            f["state"] = judgment.decision.to_state.value
            f["icir"] = icir
            advanced.append(f)
            _log_evolution(
                f["factor_id"], "advance",
                source=f.get("source"),
                state_from=state_str,
                state_to=f["state"],
                action="advance",
                reason=judgment.decision.reason,
                metrics={"icir": icir, "days_in_state": days_in_state},
            )
            logger.info(
                f"[FactorEvo] 影子期推进 {f['factor_id']}: {state_str}→{f['state']} "
                f"(days={days_in_state}, icir={icir:.3f})"
            )

    if advanced:
        logger.info(f"[FactorEvo] 阶段6.5 影子期推进: {len(advanced)} 个因子状态前进")
    return advanced


# ═══════════════════════════════════════════════════════════════
#  阶段 7：替换（退化→隔离→补挖）
# ═══════════════════════════════════════════════════════════════

def _replace_degraded(degraded, dfs, period=None):
    """隔离退化因子后补挖；**必须落库**，否则日志有 promote、池永远空。"""
    for f in degraded:
        logger.info(f"[FactorEvo] 隔离退化因子: {f['factor_id']}")
        _deactivate_factor(f["factor_id"])
        _log_evolution(
            f["factor_id"], "degrade",
            source=f.get("source"),
            state_from=str(f.get("state") or "ACTIVE"), state_to="QUARANTINE",
            action="quarantine",
            reason="IC衰减连续drift→隔离",
        )

    if not degraded:
        return []

    # 补挖用验证窗逻辑与主链一致：先在全量上挖/评，再走同一套晋升门禁
    # 注意：替换补挖走完整 GP 很重；若调用方处于 quick 止血轮，应避免走到这里。
    new_candidates = _mine_candidates(dfs, period, quick=False)
    new_eval = _evaluate_candidates(new_candidates, dfs, period)
    new_survivors = _purge_and_select(new_eval, dfs)

    icir_values = [info.get("avg_icir", 0) for info in new_eval.values()]
    new_promoted = _promote_factors(
        new_survivors, new_eval, icir_values, len(new_candidates), dfs, period=period,
    )
    if not new_promoted:
        logger.info("[FactorEvo] 阶段7 替换: 补挖门禁全拒，无可落库因子")
        return []

    to_save = _promoted_rows_for_save(new_promoted, period)
    if not to_save:
        logger.warning("[FactorEvo] 阶段7 替换: 门禁通过但无有效表达式，放弃落库")
        return []
    for p in new_promoted:
        _tag_one_short_horizon(p, period)
    _save_active_factors(to_save)
    _log_promote_committed(new_promoted, via="replace_degraded")
    _trigger_meta_retrain_after_promote(len(to_save))

    logger.info(f"[FactorEvo] 阶段7 替换: 补挖并落库 {len(to_save)} 个新因子")
    return new_promoted


# ═══════════════════════════════════════════════════════════════
#  阶段 8：在线权重更新
# ═══════════════════════════════════════════════════════════════

def _update_online_weights(active_factors, dfs):
    from backend.services.evolution.online_weights import OnlineLinearModel

    model = OnlineLinearModel()
    # 与向量同序保留 factor_id，避免 feature_importance 用 f0/f1 导致回写永不命中
    factor_ids: list[str] = []
    for f in active_factors or []:
        if not f.get("expr"):
            continue
        fid = str(f.get("factor_id") or "").strip()
        if not fid:
            continue
        factor_ids.append(fid)

    for sym, df in dfs.items():
        try:
            fields = _kline_to_fields(df)
            vals = []
            for f in active_factors or []:
                if not f.get("expr"):
                    continue
                if not str(f.get("factor_id") or "").strip():
                    continue
                vals.append(f["expr"].evaluate(fields)[-1])
            factor_vector = np.array(vals)
            fwd = _forward_returns(df, horizon=1)
            if len(factor_vector) > 0 and len(factor_vector) == len(factor_ids) and np.isfinite(factor_vector).all():
                model.learn_one(factor_vector, fwd[-1])
        except Exception:
            continue

    weights = model.feature_importance(names=factor_ids if factor_ids else None)
    logger.info(f"[FactorEvo] 阶段8 在线权重: {len(weights)} 个因子")
    return weights


def _ensure_governance_columns() -> None:
    """M2 治理：幂等补齐 factor_active_set 治理列。"""
    try:
        from sqlalchemy import text as _sa_text
        db = _get_analytics_db()
        try:
            for col, typ in (
                ("last_net_ic", "DOUBLE PRECISION"),
                ("turnover", "DOUBLE PRECISION"),
                ("evaluated_cycles", "INTEGER"),
            ):
                db.execute(_sa_text(
                    f"ALTER TABLE factor_active_set ADD COLUMN IF NOT EXISTS {col} {typ}"
                ))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


def _review_active_factors(active_factors: list[dict], dfs) -> tuple[list[dict], list[dict]]:
    """M2 治理：全量 ACTIVE 复评 净IC/换手/容量；返回 (保留, 退化)。"""
    from backend.services.factor_engine.evaluation import information_coefficient
    from backend.services.evolution.factor_labels import (
        capacity_usd as _cap,
        net_ic as _nic,
        turnover as _turn,
    )

    kept: list[dict] = []
    degraded: list[dict] = []
    vol_usd = _estimate_volume_usd(next(iter(dfs.values())) if dfs else None)
    n_dfs = max(len(dfs), 1)
    for f in active_factors:
        expr = f.get("expr")
        if not expr:
            kept.append(f)
            continue
        t = 0.0
        ic_mean = 0.0
        n = 0
        for _sym, df in dfs.items():
            try:
                fields = _kline_to_fields(df)
                vals = expr.evaluate(fields)
                fwd = _forward_returns(df)
                ic = information_coefficient(vals, fwd)
                if ic is not None:
                    ic_mean += float(ic)
                    n += 1
                t += _turn(pd.Series(vals))
            except Exception:
                continue
        if n > 0:
            ic_mean /= n
        t = t / n_dfs
        net = _nic(ic_mean, t)
        f["last_net_ic"] = round(net, 6)
        f["turnover"] = round(t, 6)
        f["capacity_usd"] = _cap(vol_usd, t)
        f["evaluated_cycles"] = int(f.get("evaluated_cycles") or 0) + 1
        if n == 0:
            # [2026-08-06 2.3 修复] 全部 symbol 求值异常（表达式损坏/字段缺失/数据断裂）
            # 时，不能按 net_ic=0 判退化——这正是 07-23 批量误杀 100 个因子的机制
            # （expr_ast 损坏后每轮复评 n=0 → net=0 < 阈值 → 永久 QUARANTINE）。
            # 改为保留原状 + 告警，等待人工修复或表达式重建。
            logger.warning(
                "[FactorEvo] M2治理: %s 全部symbol求值异常，跳过退化判定(保留现状)",
                f.get("factor_id"),
            )
            _log_evolution(
                f.get("factor_id"), "review",
                source=f.get("source"),
                action="eval_all_failed",
                reason="全部symbol求值异常，跳过退化判定(保留现状)",
            )
            kept.append(f)
        elif net < _min_net_ic_threshold():
            degraded.append(f)
            _deactivate_factor(f["factor_id"])
            f["state"] = "QUARANTINE"
            _log_evolution(
                f["factor_id"], "review",
                source=f.get("source"),
                action="net_ic_low",
                reason=f"net_ic={net:.4f}",
            )
        else:
            kept.append(f)
    return kept, degraded


def _enforce_active_cap(active_factors: list[dict]) -> int:
    """M2 治理：ACTIVE 数量上限强制淘汰。"""
    import os as _os
    try:
        cap = int(_os.getenv("FACTOR_ACTIVE_CAP", "50"))
    except (TypeError, ValueError):
        cap = 50
    actives = [f for f in active_factors if f.get("state") == "ACTIVE"]
    if len(actives) <= cap:
        return 0
    actives.sort(
        key=lambda f: (
            float(f.get("last_net_ic") or f.get("icir") or 0),
            float(f.get("evaluated_cycles") or 0),
        ),
        reverse=True,
    )
    excess = actives[cap:]
    for f in excess:
        _deactivate_factor(f["factor_id"])
        f["state"] = "QUARANTINE"
        _log_evolution(
            f["factor_id"], "review",
            source=f.get("source"),
            action="active_cap",
            reason=f"ACTIVE={len(actives)}>cap={cap}",
        )
    return len(excess)


# ═══════════════════════════════════════════════════════════════
#  主循环
# ═══════════════════════════════════════════════════════════════

def run_factor_evolution_loop(symbols=None, period=None, quick=False, source: str | None = None) -> dict:
    """因子进化闭环主入口。

    quick=True：快速修复模式（portfolio_budget 修复流水线专用，2026-08-07 加固）——
    压缩 GP/MCTS 挖掘规模（1 种子 5 代 100 个体 / MCTS 60 iter 1 根，经环境变量
    FACTOR_GP_*/FACTOR_MCTS_* 传导），跳过 WFO 双门禁与测试集复评，全程生效
    （含阶段7 补挖的二次挖掘），函数结束恢复环境变量；定时进化链路不受影响。
    """
    from backend.services.evolution import evo_runtime

    t0 = time.time()
    period_eff = period or DEFAULT_PERIOD
    src = source or ("quick" if quick else "cron")
    owned = evo_runtime.mark_start(period=str(period_eff), quick=bool(quick), source=src)
    if not owned:
        return {
            "error": "already_running",
            "message": "因子进化已在运行",
            "runtime": evo_runtime.snapshot(),
        }

    logger.info(
        "[FactorEvo] ═══ 因子进化闭环启动 ═══" + (" [快速修复模式]" if quick else "")
    )

    if not quick:
        boost = evo_runtime.ensure_mining_boost_if_auto()
        if boost is not None:
            evo_runtime.mark_boost(boost)

    _env_backup: dict = {}
    if quick:
        for _ev in ("FACTOR_GP_SEEDS", "FACTOR_GP_GENERATIONS", "FACTOR_GP_POPULATION",
                    "FACTOR_MCTS_ITERATIONS", "FACTOR_MCTS_ROOTS"):
            _env_backup[_ev] = _os_window.getenv(_ev)
        _os_window.environ["FACTOR_GP_SEEDS"] = "1"
        _os_window.environ["FACTOR_GP_GENERATIONS"] = "5"
        _os_window.environ["FACTOR_GP_POPULATION"] = "100"
        _os_window.environ["FACTOR_MCTS_ITERATIONS"] = "60"
        _os_window.environ["FACTOR_MCTS_ROOTS"] = "1"
    global _ACTIVE_EVO_PERIOD
    _prev_period = _ACTIVE_EVO_PERIOD
    _ACTIVE_EVO_PERIOD = period_eff
    report: dict = {}
    err: str | None = None
    try:
        report = _run_evolution_loop_impl(symbols, period, quick, t0)
        if isinstance(report, dict) and report.get("error"):
            err = str(report.get("message") or report.get("error"))[:400]
        return report
    except Exception as e:
        err = str(e)[:400]
        raise
    finally:
        _ACTIVE_EVO_PERIOD = _prev_period
        if quick:
            for _ev, _old in _env_backup.items():
                if _old is None:
                    _os_window.environ.pop(_ev, None)
                else:
                    _os_window.environ[_ev] = _old
        evo_runtime.mark_end(report=report if isinstance(report, dict) else None, error=err)


def _run_evolution_loop_impl(symbols, period, quick, t0) -> dict:
    """原闭环主体（quick=True 时跳过 WFO 双门禁与测试集复评）。"""
    # 1. 取数
    dfs = _load_data(symbols, period)
    _ensure_governance_columns()
    if not dfs:
        return {"error": "取数失败，无可用数据"}

    # 1.4 P0-2 深度门槛：不足则催促回填并中止（禁止假 OOS）
    depth = _check_split_depth(dfs, period)
    if not depth.get("ok"):
        _nudge_depth_backfill(list(dfs.keys()), period)
        msg = (
            f"数据深度不足 period={depth.get('period')} "
            f"need_days>={depth.get('need_days')} need_bars>={depth.get('need_bars')} "
            f"short={depth.get('short_symbols')} detail={depth.get('by_symbol')}"
        )
        logger.error(f"[FactorEvo] {msg} — 已 nudge 深度回填，本轮中止")
        return {
            "error": "depth_insufficient",
            "message": msg,
            "period": depth.get("period"),
            "need_days": depth.get("need_days"),
            "need_bars": depth.get("need_bars"),
            "by_symbol": depth.get("by_symbol"),
            "short_symbols": depth.get("short_symbols"),
            "quick": bool(quick),
            "elapsed_sec": round(time.time() - t0, 1),
        }

    # 1.5 训练/验证/测试三段切分（v6 计划 5.4.3：周期分档窗口，测试集绝不参与挖掘与选因）
    # [2026-08-08 P0-1] 禁止静默退化为 train=val=全窗（假 OOS）。数据不足直接失败，
    # 由调用方（PB quick / 调度）解冻或触发回填，绝不在同集上挖评。
    dfs_train, dfs_val, dfs_test = _split_train_val_test(dfs, period)
    if not dfs_train or not dfs_val:
        need = _lookback_for_period(period)
        got = {sym: len(df) for sym, df in dfs.items()}
        _nudge_depth_backfill(list(dfs.keys()), period)
        msg = (
            f"三段切分数据不足(train={len(dfs_train)} val={len(dfs_val)} "
            f"test={len(dfs_test)}) period={period or DEFAULT_PERIOD} "
            f"need>={need} got={got}"
        )
        logger.error(f"[FactorEvo] {msg} — 拒绝假 OOS，本轮中止")
        return {
            "error": "split_insufficient_data",
            "message": msg,
            "period": period or DEFAULT_PERIOD,
            "need_bars": need,
            "got_bars": got,
            "quick": bool(quick),
            "elapsed_sec": round(time.time() - t0, 1),
        }

    # 2. 挖掘（只用训练集拟合，不看验证/测试集）
    candidates = _mine_candidates(dfs_train, period, quick=bool(quick))

    # 3. 验证（样本外：用训练阶段没见过的验证集算IC，而非在训练集上自证）
    eval_results = _evaluate_candidates(candidates, dfs_val, period)

    # ── 收集 ICIR 用于 DSR/PBO ──
    all_icir = [info.get("avg_icir", 0) for info in eval_results.values()]
    # 加载已有活跃因子的 ICIR（加入总候选数）
    existing_active = _load_active_factors()
    existing_icir = [f.get("icir", 0) for f in existing_active if f.get("icir")]
    all_icir.extend(existing_icir)
    n_total = len(candidates) + len(existing_active)

    # 4. 清洗（选因必须基于验证集样本外数据：eval_results 的 IC/ICIR 是
    # 验证集计算的，purge 的因子序列去相关也应取自验证集，避免训练+测试
    # 数据混入选因决策。退化路径 dfs_val==dfs 时行为与旧版一致。）
    survivors = _purge_and_select(eval_results, dfs_val)

    # 5. 上线（DSR/PBO 真实计算代替硬编码）
    promoted = _promote_factors(
        survivors, eval_results, all_icir, n_total, dfs, period=period,
    )

    # [2026-08-08 P0-3] quick 修复：无晋升时带可审计拒绝原因快速返回，
    # 跳过监控/影子推进/替换等长尾，避免 PB 空转 17–40min。
    if quick and not promoted:
        rejects = []
        if survivors:
            rejects = list(survivors[0].get("_promote_rejects") or [])
        logger.warning(
            "[FactorEvo] quick 无晋升，快失败返回 survivors=%d rejects=%d",
            len(survivors), len(rejects),
        )
        return {
            "error": "promote_rejected" if survivors else "no_survivors",
            "message": "quick 修复无因子晋升（门禁拒绝可审计）",
            "period": period or DEFAULT_PERIOD,
            "quick": True,
            "candidates": len(candidates),
            "evaluated": len(eval_results),
            "survivors": len(survivors),
            "promoted": 0,
            "promote_rejects": rejects[:20],
            "dsr_note": "见 promote_rejects / factor_evolution_log action=promote_reject",
            "elapsed_sec": round(time.time() - t0, 1),
        }

    # ── 持久化新晋升的活跃因子 ──
    if promoted:
        # [2026-08-07 quick] 快速修复模式（修复流水线）跳过 WFO 双门禁与测试集复评：
        # WFO 全量评估 4 个候选耗时 ~9min，与"止血后 ~10min 内补完"的目标冲突；
        # 修复链路自有 PB_REPAIR_TIMEOUT_SEC 超时兜底，且 DSR/PBO 门已在阶段5 执行。
        # 定时进化链路不受影响，仍走完整门禁。
        if not quick:
            # M5 WFO 门禁：样本外滚动验证不通过则不晋升
            # 异常默认 fail-closed（FACTOR_EVO_GATE_FAIL_CLOSED=1）
            _wfo_freq = {
                "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min",
                "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1d",
            }.get((period or "1h"), "1h")
            _fail_closed = _evo_gate_fail_closed()
            try:
                from backend.services.evolution.factor_wfo import (
                    run_factor_wfo,
                    run_factor_wfo_ic,
                )
                _wfo_kept = []
                # [2026-08-13 P1-8] WFO 多币验证：遍历训练面板全部 symbol（不再只验第一个），
                # 任一币不达标即拒（FACTOR_EVO_WFO_REQUIRE_ALL=1 默认）；
                # 关闭后改为 ≥ FACTOR_EVO_WFO_MIN_SYMBOL_RATIO（默认 2/3）币通过。
                _wfo_require_all = (
                    (_os_window.getenv("FACTOR_EVO_WFO_REQUIRE_ALL") or "1").strip().lower()
                    not in ("0", "false", "no", "off")
                )
                try:
                    _wfo_min_ratio = float(
                        _os_window.getenv("FACTOR_EVO_WFO_MIN_SYMBOL_RATIO", "0.667") or 0.667
                    )
                except (TypeError, ValueError):
                    _wfo_min_ratio = 0.667
                _wfo_symbols = list((dfs or {}).keys())
                try:
                    _wfo_max_syms = int(_os_window.getenv("FACTOR_EVO_WFO_SYMBOLS", "0") or 0)
                except (TypeError, ValueError):
                    _wfo_max_syms = 0
                if _wfo_max_syms > 0:
                    _wfo_symbols = _wfo_symbols[:_wfo_max_syms]
                if not _wfo_symbols:
                    raise RuntimeError("WFO 面板为空：无 symbol 可验证")
                for p in promoted:
                    _expr = p.get("expr") or (eval_results.get(p["factor_id"], {}) or {}).get("expr")
                    _n_pass = 0
                    _n_fail = 0
                    _fail_reasons: list[str] = []
                    _factor_err: Exception | None = None
                    for _sym in _wfo_symbols:
                        _df = (dfs or {}).get(_sym)
                        if _df is None or len(_df) == 0:
                            _n_fail += 1
                            _fail_reasons.append(f"{_sym}:no_data")
                            continue
                        try:
                            _res = run_factor_wfo(
                                _expr, _df, p["factor_id"],
                                freq=_wfo_freq,
                            )
                        except Exception as _wfo_one_err:
                            _factor_err = _wfo_one_err
                            if _fail_closed:
                                _n_fail += 1
                                _fail_reasons.append(f"{_sym}:wfo_error:{str(_wfo_one_err)[:80]}")
                            else:
                                _n_pass += 1  # fail-open：异常币视为通过（旧行为）
                            continue
                        if not _res.get("passed", True):
                            _n_fail += 1
                            _fail_reasons.append(f"{_sym}:wfo_reject:{_res.get('reason', '未过门')}")
                            continue
                        try:
                            _ic_res = run_factor_wfo_ic(
                                _expr, _df, p["factor_id"],
                                freq=_wfo_freq,
                            )
                        except Exception as _ic_err:
                            _factor_err = _ic_err
                            if _fail_closed:
                                _n_fail += 1
                                _fail_reasons.append(f"{_sym}:wfo_ic_error:{str(_ic_err)[:80]}")
                            else:
                                _n_pass += 1  # fail-open：异常币视为通过（旧行为）
                            continue
                        if not _ic_res.get("passed", True):
                            _n_fail += 1
                            _fail_reasons.append(
                                f"{_sym}:wfo_ic_reject:oos_ic={_ic_res.get('oos_ic_mean')}"
                                f"/p={_ic_res.get('oos_ic_p')}/decay={_ic_res.get('decay_rate')}"
                            )
                            continue
                        _n_pass += 1
                    if _wfo_require_all:
                        _passed_ok = _n_fail == 0 and _n_pass == len(_wfo_symbols)
                    else:
                        _passed_ok = _n_pass / max(1, len(_wfo_symbols)) >= _wfo_min_ratio
                    if not _passed_ok:
                        _log_evolution(
                            p["factor_id"], "wfo",
                            source=p.get("source"),
                            action=(
                                ("wfo_error_fail_closed" if _fail_closed else "wfo_error_fail_open")
                                if _factor_err is not None else "wfo_reject"
                            ),
                            reason=("; ".join(_fail_reasons) or "多币验证未通过")[:300],
                            metrics={
                                "pass": _n_pass, "fail": _n_fail,
                                "symbols": len(_wfo_symbols),
                                "require_all": _wfo_require_all,
                            },
                        )
                        logger.warning(
                            "[FactorEvo] WFO 多币验证拒绝晋升 %s: %s",
                            p["factor_id"], _fail_reasons,
                        )
                        continue
                    _wfo_kept.append(p)
                promoted = _wfo_kept
            except Exception as _wfo_err:
                logger.warning(
                    "[FactorEvo] WFO 门禁异常(%s): %s",
                    "fail-closed" if _fail_closed else "fail-open",
                    str(_wfo_err)[:150],
                )
                if _fail_closed:
                    for p in promoted:
                        _log_evolution(
                            p.get("factor_id"), "wfo",
                            source=p.get("source"),
                            action="wfo_error_fail_closed",
                            reason=str(_wfo_err)[:150],
                        )
                    promoted = []

            # 三层切分最终裁判：测试集 IC 复评（测试集绝不参与挖掘与选因）
            promoted = _final_test_confirm(promoted, eval_results, dfs_test)

        to_save = _promoted_rows_for_save(promoted, period)
        # 与 to_save 对齐：无表达式的门禁通过项不计入「已晋升」
        _ok_ids = {r["factor_id"] for r in to_save}
        promoted = [p for p in promoted if p.get("factor_id") in _ok_ids]
        for p in promoted:
            _tag_one_short_horizon(p, period)
        if to_save:
            _save_active_factors(to_save)
            _log_promote_committed(promoted, via="main")
            _trigger_meta_retrain_after_promote(len(to_save))

        # 事件驱动回测触发（规划文档§4.1）：新因子晋升后不用等次日3点调度，
        # 立即在后台跑一次独立的单因子交易模拟，5分钟内产出可查报告。
        # enqueue是异步的，即使这里异常也不能拖垮主流程。
        try:
            from backend.services.backtest.trigger import backtest_event_trigger
            for p in promoted:
                backtest_event_trigger.on_factor_promoted(p["factor_id"], source=p.get("source", ""))
        except Exception as e:
            logger.debug(f"[FactorEvo] 事件驱动回测触发失败(不影响晋升本身): {e}")

    # 6. 监控已有活跃因子
    all_active = existing_active + promoted
    if all_active:
        degraded = _monitor_active(all_active, dfs)
    else:
        degraded = []

    # 6.5 影子期推进（PAPER/SMALL_LIVE 逐日复评，避免有入口无出口）
    advanced = _advance_shadow_factors(existing_active, dfs) if existing_active else []
    if advanced:
        _save_active_factors(advanced)

    # M2 治理：全量 ACTIVE 复评（净IC/换手/容量）+ 上限强制
    if all_active:
        kept, degraded_review = _review_active_factors(all_active, dfs)
        all_active = kept
        _seen = set()
        _merged = list(degraded) + list(degraded_review)
        degraded = []
        for _f in _merged:
            _fid = _f.get("factor_id")
            if _fid not in _seen:
                _seen.add(_fid)
                degraded.append(_f)
        capped = _enforce_active_cap(all_active)
        if capped:
            all_active = [f for f in all_active if f.get("state") != "QUARANTINE"]
        if degraded_review or capped:
            _save_active_factors(all_active)
            logger.info(
                "[FactorEvo] M2治理: 复评退化=%d 上限淘汰=%d",
                len(degraded_review), capped,
            )

    # 7. 替换退化因子（返回新落库列表；此前只 return 计数 → 假补挖）
    # quick 止血轮禁止二次完整 GP 补挖（否则又卡 15–25 分钟）
    if quick:
        replaced_raw = []
        replaced = 0
        replaced_list = []
    else:
        replaced_raw = _replace_degraded(degraded, dfs, period) if degraded else []
        if isinstance(replaced_raw, list):
            replaced_list = replaced_raw
        else:
            # 兼容旧测试 mock 返回 int
            replaced_list = []
            logger.debug("[FactorEvo] _replace_degraded 非 list 返回: %r", replaced_raw)
        replaced = len(replaced_list) if isinstance(replaced_raw, list) else int(replaced_raw or 0)
    if replaced_list:
        # 退化项从热池剔除，补上新晋级
        _deg_ids = {f.get("factor_id") for f in degraded}
        all_active = [f for f in all_active if f.get("factor_id") not in _deg_ids]
        all_active.extend(replaced_list)
        promoted = list(promoted) + list(replaced_list)

    # 8. 在线权重
    if all_active:
        weights = _update_online_weights(all_active, dfs)
        # 回写权重到活跃集
        for f in all_active:
            fid = f["factor_id"]
            if fid in weights:
                f["current_weight"] = {"4h": float(weights[fid])}
        _save_active_factors(all_active)

    elapsed = time.time() - t0
    report = {
        "elapsed_sec": round(elapsed, 1),
        "symbols": list(dfs.keys()),
        "period": period or DEFAULT_PERIOD,
        "quick": bool(quick),
        "candidates": len(candidates),
        "evaluated": len(eval_results),
        "survivors": len(survivors),
        "promoted": len(promoted),
        "advanced": len(advanced),
        "degraded": len(degraded),
        "replaced": replaced,
        "active_total": len(all_active),
        "promoted_factors": [{"id": p["factor_id"], "source": p["source"]} for p in promoted],
    }
    logger.info(f"[FactorEvo] ═══ 因子进化完成: {report} ═══")
    return report


_SHORT_HORIZON_PERIODS = frozenset({"1m", "3m", "5m", "15m"})
_SHORT_FACTOR_PREFIX = "s5m_"


def _tag_one_short_horizon(factor: dict, period: str | None) -> dict:
    """短周期因子打 s5m_ 前缀 + source 含 horizon=scalp（与 4h 池隔离）。"""
    p = period or DEFAULT_PERIOD
    if p not in _SHORT_HORIZON_PERIODS:
        return factor
    fid = str(factor.get("factor_id") or "")
    if fid and not fid.startswith(_SHORT_FACTOR_PREFIX):
        factor["factor_id"] = f"{_SHORT_FACTOR_PREFIX}{fid}"
    if factor.get("expr_id") and not str(factor["expr_id"]).startswith(_SHORT_FACTOR_PREFIX):
        factor["expr_id"] = f"{_SHORT_FACTOR_PREFIX}{factor['expr_id']}"
    src = str(factor.get("source") or "")
    tag = f"horizon=scalp|period={p}"
    if tag not in src:
        factor["source"] = f"{src}|{tag}" if src else tag
    return factor


def _tag_short_horizon_factors(factors: list[dict], period: str | None) -> list[dict]:
    return [_tag_one_short_horizon(f, period) for f in (factors or [])]


def run_scalp_factor_evolution_loop(symbols=None, source: str | None = None) -> dict:
    """短线专用完整进化入口（v6 5.4.3 / P0-4）：period=5m，非 quick，走完整 WFO。

    与日级 4h `run_factor_evolution_loop` 并行；产物 factor_id 带 s5m_ 前缀。
    调度：main.py cron 每日 04:00（避开 03:00 的 4h 进化）。
    """
    logger.info("[FactorEvo] ═══ 短线 5m 完整进化启动（非 quick）═══")
    return run_factor_evolution_loop(symbols=symbols, period="5m", quick=False, source=source)


def run_online_weight_update(symbols=None) -> dict:
    dfs = _load_data(symbols, period="1h", lookback=500)
    if not dfs:
        return {"error": "取数失败"}

    active_factors = _load_active_factors()
    if not active_factors:
        return {"skipped": "无活跃因子"}

    # 记录旧权重，供更新后计算变化幅度（事件驱动回测触发的判定依据）
    old_weights = {}
    for f in active_factors:
        cw = f.get("current_weight")
        if isinstance(cw, dict):
            old_weights[f["factor_id"]] = float(cw.get("1h", 0.0) or 0.0)
        elif cw is not None:
            old_weights[f["factor_id"]] = float(cw)

    weights = _update_online_weights(active_factors, dfs)
    if weights:
        for f in active_factors:
            fid = f["factor_id"]
            if fid in weights:
                f["current_weight"] = {"1h": float(weights[fid])}
        _save_active_factors(active_factors)

        # 事件驱动回测触发（规划文档§4.1第二挂点）：权重变化超20%的因子
        # 立即补一次针对性回测，而不是被动等次日调度才发现权重巨变的原因。
        try:
            from backend.services.backtest.trigger import backtest_event_trigger
            for fid, new_w in weights.items():
                old_w = old_weights.get(fid, 0.0)
                if old_w == 0.0:
                    # 首次赋权重(而非"变化")，晋升时已有 on_factor_promoted 覆盖，
                    # 这里跳过避免 denom≈0 导致 delta_pct 虚高触发噪声风暴。
                    continue
                backtest_event_trigger.on_weight_delta_exceeds_threshold(fid, old_w, float(new_w))
        except Exception as e:
            logger.debug(f"[FactorEvo] 权重变化事件触发失败(不影响权重更新本身): {e}")

    return {"weights_updated": len(weights), "active_factors": len(active_factors)}
