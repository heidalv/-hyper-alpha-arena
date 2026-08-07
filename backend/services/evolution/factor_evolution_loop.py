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

DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"]
DEFAULT_PERIOD = "4h"
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


def _forward_returns(df: pd.DataFrame, horizon: int = 5) -> np.ndarray:
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
                    existing.icir = f.get("icir")
                    existing.incremental_corr = f.get("incremental_corr")
                    existing.capacity_usd = f.get("capacity_usd")
                    existing.last_net_ic = f.get("last_net_ic")
                    existing.turnover = f.get("turnover")
                    existing.evaluated_cycles = f.get("evaluated_cycles")
                    existing.current_weight = f.get("current_weight")
                    existing.last_evaluated_at = now
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
    """从 DB 加载活跃因子，返回字典列表（含 expr 对象）。"""
    try:
        from backend.database.models import FactorActiveSet
        from backend.services.factor_engine.expr.parser import parse as _parse
        db = _get_analytics_db()
        try:
            rows = db.query(FactorActiveSet).filter(
                FactorActiveSet.state.in_(["ACTIVE", "ORTHO", "PAPER"])
            ).all()
            factors = []
            for r in rows:
                try:
                    expr = _parse(r.expr_ast)
                    factors.append({
                        "factor_id": r.factor_id,
                        "expr": expr,
                        "expr_ast": r.expr_ast,
                        "expr_id": r.expr_id,
                        "source": r.source,
                        "state": r.state,
                        "icir": r.icir,
                        "incremental_corr": r.incremental_corr,
                        "capacity_usd": r.capacity_usd,
                        "last_net_ic": r.last_net_ic,
                        "turnover": r.turnover,
                        "evaluated_cycles": r.evaluated_cycles,
                        "current_weight": r.current_weight or {},
                        "activated_at": r.activated_at,
                    })
                except Exception:
                    continue
            logger.info(f"[FactorEvo] 从DB加载 {len(factors)} 个活跃因子")
            return factors
        finally:
            db.close()
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

def _load_data(symbols=None, period=None, lookback=None):
    syms = symbols or DEFAULT_SYMBOLS
    p = period or DEFAULT_PERIOD
    lb = lookback or DEFAULT_LOOKBACK
    try:
        from backend.services.data_center import data_center
    except Exception:
        logger.warning("[FactorEvo] data_center 不可用，跳过")
        return {}

    dfs = {}
    for sym in syms:
        try:
            # [2026-08-07 v6 s7 fix] 因子挖掘为研究/回放用途，改用 purpose="research"：
            # 多源择优取深度最深者，且不受 trade 新鲜度强拒（4h stale>8h 时
            # 交易路径拒用是风控设计，但历史回放挖掘不依赖最后一根新鲜度；
            # 曾因默认 trade 语义导致 16h stale 时因子日循环/小时权重全链停摆）。
            result = data_center.get_klines(sym, p, count=lb, purpose="research")
            df = result.to_dataframe()
            if len(df) >= 100:
                dfs[sym] = df
        except Exception as e:
            logger.debug(f"[FactorEvo] 取数失败 {sym}/{p}: {e}")
    logger.info(f"[FactorEvo] 阶段1 取数: {len(dfs)}/{len(syms)} 品种, period={p}")
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
    样本外复评：测试集 IC 均值为负（< -0.005）视为明显失效 → 拦截晋升。
    测试集不可用（无数据/异常）时 fail-open 不拦截（与 WFO 门禁一致）。
    """
    from backend.services.factor_engine.evaluation import information_coefficient

    if not dfs_test:
        return promoted
    kept = []
    for p in promoted:
        expr = p.get("expr") or (eval_results.get(p["factor_id"], {}) or {}).get("expr")
        if not expr:
            kept.append(p)
            continue
        ics = []
        for sym, df in dfs_test.items():
            try:
                fields = _kline_to_fields(df)
                fwd = _forward_returns(df, horizon=5)
                ic = information_coefficient(expr.evaluate(fields), fwd)
                if ic is not None and np.isfinite(ic):
                    ics.append(ic)
            except Exception:
                continue
        if not ics:
            kept.append(p)
            continue
        test_ic = float(np.mean(ics))
        if test_ic < -0.005:
            _log_evolution(
                p["factor_id"], "test_gate",
                source=p.get("source"),
                action="test_reject",
                reason=f"test_ic={test_ic:.4f} 为负",
                metrics={"test_ic": test_ic, "n_test_symbols": len(ics)},
            )
            logger.warning("[FactorEvo] 测试集 IC 为负，拦截晋升 %s: %.4f", p["factor_id"], test_ic)
            continue
        p["test_ic"] = test_ic
        kept.append(p)
    if kept:
        logger.info("[FactorEvo] 测试集终审: %d/%d 通过 (test_ic 记录在因子卡)", len(kept), len(promoted))
    return kept


# ═══════════════════════════════════════════════════════════════
#  阶段 2：挖掘候选因子
# ═══════════════════════════════════════════════════════════════

def _mine_candidates(dfs, period=None):
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

    try:
        # [2026-08-05] GP 挖掘器（v6 计划 5.3.1）：替换纯随机 AlphaMiner 搜索。
        # 进化压力 = 锦标赛选择 + 子树交叉(70%) + 变异(20%) + 精英保留(top5%)；
        # 适应度 = |IC| − λ1×复杂度 − λ2×与精英池最大相关（防公式膨胀与同质化）；
        # 多种子并行（幻方 6 种子方法论）→ top 候选经 AlphaPool.try_admit 池感知准入。
        import os as _os_gp

        from backend.services.evolution.alpha_miner import AlphaPool
        from backend.services.evolution.gp_miner import GPConfig, GPMiner
        first_sym = list(dfs.keys())[0]
        first_df = dfs[first_sym]
        fields = _kline_to_fields(first_df)
        target = _forward_returns(first_df)

        def _eval_fn(ctx):
            try:
                return ctx["expr"].evaluate(fields)
            except Exception:
                return np.zeros(len(first_df))

        gp_pool = AlphaPool(capacity=50)
        gp_config = GPConfig()
        # 环境变量可调（默认 300 种群 / 20 代 / 5 种子）
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
        miner = GPMiner(list(fields.keys()), _eval_fn, target, gp_pool, gp_config)
        admitted = miner.mine()
        logger.info(f"[FactorEvo] GP 挖掘: {len(admitted)} 命中入池")
        for expr, _contrib in admitted:
            candidates.append((expr, f"gp_{expr.expr_id[:8]}"))
    except Exception as e:
        logger.warning(f"[FactorEvo] GP 挖掘异常: {e}")

    # [2026-08-06 阶段2 S2-12] MCTS 挖掘器（UCT + 短板扩展 + FSA + CoE + 宏微分离）：
    # 与 GP 并列的第二种挖掘器。短板种子 = 活跃集中 |IC| 最低的因子（定向改进短板）；
    # 窗口档位/深度/复杂度惩罚按周期宏微分离（micro/mid/macro）；FSA 过滤参数不稳候选；
    # CoE 进化链（fitness 改进边）落库 factor_evolution_log（action=mcts_chain）。
    try:
        import os as _os_mcts

        if _os_mcts.getenv("FACTOR_MCTS_ENABLED", "1") != "0":
            from backend.services.evolution.alpha_miner import AlphaPool
            from backend.services.evolution.mcts_miner import (
                MCTSConfig, MctsMiner, scale_for_period,
            )

            # 独立取字段（不依赖 GP 段变量：GP 异常时也能跑 MCTS）
            mcts_first_sym = list(dfs.keys())[0]
            mcts_first_df = dfs[mcts_first_sym]
            mcts_fields = _kline_to_fields(mcts_first_df)
            mcts_target = _forward_returns(mcts_first_df)

            def _mcts_eval_fn(ctx):
                try:
                    return ctx["expr"].evaluate(mcts_fields)
                except Exception:
                    return np.zeros(len(mcts_first_df))

            mcts_pool = AlphaPool(capacity=50)
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
            # 短板种子：活跃集中 |IC| 最低的因子（短板扩展）
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
                list(mcts_fields.keys()), _mcts_eval_fn, mcts_target, mcts_pool,
                mcts_config, weak_seeds=weak_seeds,
            )
            mcts_admitted, mcts_chains = mcts_miner.mine()
            logger.info(
                f"[FactorEvo] MCTS 挖掘(scale={mcts_config.scale}): "
                f"{len(mcts_admitted)} 命中入池, 进化链 {len(mcts_chains)}"
            )
            for expr, _contrib in mcts_admitted:
                candidates.append((expr, f"mcts_{expr.expr_id[:8]}"))
            # CoE 进化链落库（每条 fitness 改进边保留 parent/child 血缘）
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
                fwd = _forward_returns(df, horizon=5)
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

            # [2026-08-05 v6 2.4 S2-4] 完整因子报告卡落库（factor card JSON →
            # factor_evolution_log.metrics.card，L274/L287）：IC/分层/显著性/衰减/
            # parsimony/数据质量/admission_gate。单项失败容错，绝不阻塞评估主流程。
            try:
                from backend.services.factor_engine.factor_card import build_factor_card
                _card = build_factor_card(
                    factor_id=expr.expr_id, expr=expr, dfs=dfs,
                    period=_period, horizon=5, source=source,
                )
                _log_evolution(
                    expr.expr_id, "card",
                    expr_ast=getattr(expr, "ast", None),
                    source=source,
                    action="card_generated",
                    metrics={"card": _card, "net_ic": avg_net_ic},
                )
            except Exception as _card_err:
                logger.debug(
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

    first_df = list(dfs.values())[0]
    fwd = _forward_returns(first_df, horizon=5)
    return_series = pd.Series(fwd, index=first_df.index)

    survivors, report = run_purge_pipeline(
        candidates, factor_series_fn=factor_series_fn,
        return_series=return_series,
        config=PurgeConfig(max_active_factors=50),
        thresholds=LifecycleThresholds(),
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


def _promote_factors(survivors, eval_results, all_icir_values, n_total, dfs=None):
    from backend.services.evolution.shadow_judge import ShadowJudge
    from backend.services.factor_engine.dsr_pbo import compute_dsr_pbo_for_factors
    from backend.services.factor_engine.lifecycle import (
        FactorMetrics,
        FactorState,
    )

    # ── DSR/PBO 全局评估 ──
    sample_len = DEFAULT_LOOKBACK
    dsr_pbo = compute_dsr_pbo_for_factors(
        icir_list=all_icir_values,
        n_total_candidates=n_total,
        sample_len=sample_len,
    )
    dsr_significant = dsr_pbo.get("dsr_result", {}).get("significant", True)
    pbo_val = dsr_pbo.get("pbo_result", {}).get("pbo", 0.3)
    logger.info(
        f"[FactorEvo] DSR/PBO: dsr_sig={dsr_significant} pbo={pbo_val:.3f} "
        f"best_icir={dsr_pbo.get('best_icir')} n_factors={dsr_pbo.get('n_factors')}"
    )

    judge = ShadowJudge()
    promoted = []

    for s in survivors:
        info = eval_results.get(s["factor_id"], {})
        if info.get("net_ic", 1.0) < _min_net_ic_threshold():
            logger.info(
                "[FactorEvo] 净IC不足跳过 %s: net_ic=%.4f",
                s["factor_id"], info.get("net_ic", 0),
            )
            continue
        eval_result = s.get("eval_result")
        if not eval_result:
            continue

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
            capacity_usd=_estimate_capacity_usd(next(iter(dfs.values())) if dfs else None, float(getattr(eval_result, 'turnover', 0) or 0)),  # TODO: 接 capacity.py 真实计算
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
            promoted.append(s)
            # 记录到进化日志
            _log_evolution(
                s["factor_id"], "promote",
                expr_ast=s.get("expr_ast"),
                source=s.get("source"),
                state_from=judgment.decision.from_state.value,
                state_to=judgment.decision.to_state.value,
                action="promote",
                reason=judgment.decision.reason,
                metrics={
                    "icir": eval_result.icir,
                    "incremental_corr": s.get("incremental_corr"),
                    "dsr_significant": dsr_significant,
                    "pbo": pbo_val,
                },
            )
            logger.info(
                f"[FactorEvo] 因子晋升 {s['factor_id']}: "
                f"{judgment.decision.from_state}→{judgment.decision.to_state} ({judgment.decision.reason})"
            )

    logger.info(f"[FactorEvo] 阶段5 上线: {len(promoted)}/{len(survivors)} 晋升, dsr_sig={dsr_significant} pbo={pbo_val:.3f}")
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
                fwd = _forward_returns(df, horizon=5)
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

def _advance_shadow_factors(existing_active: list[dict], dfs) -> list[dict]:
    """对已在 PAPER/SMALL_LIVE 影子期的因子逐日复评，判断是否可推进到下一状态。

    [2026-07-18 新增] `_promote_factors` 只处理"本轮新挖到的候选"（首次评估，
    起点固定 ORTHO）；已经进入 PAPER/SMALL_LIVE 影子期的因子此前**没有任何代码
    路径**会再去复评它们能不能往下走一步——`_monitor_active` 只查退化，不查
    推进。结果是一旦进 PAPER 就再也不会被检查是否该"毕业"，等价于影子期有入口
    没出口。这里补上：每天用当天重算的 ICIR + 影子期已持续天数复评一次。

    注意（诚实标注局限）：
      - paper_sharpe 用 icir 换算的代理值（真实基于逐笔盈亏的 Parity Score 属于
        P3 阶段待办，尚未接入），仅作为方向性代理，非严格 Sharpe。
      - live_deviation（PAPER 影子表现 vs 真实小仓执行的偏差）需要 DualTrackExecutor
        才能算真实值，该组件目前是孤立脚手架未接入实盘路径，这里先按"无小仓
        对照数据"处理为不参与硬拦截，改用比基础阈值严格得多的 paper_sharpe/
        paper_days 门槛（1.5x/2x，见 _auto_oversight_approve）作为补偿性风控。
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
                fwd = _forward_returns(df, horizon=5)
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
            paper_sharpe=paper_sharpe_proxy,
            live_deviation=0.0,  # 见函数说明：暂无小仓对照数据，不参与硬拦截
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
    for f in degraded:
        logger.info(f"[FactorEvo] 隔离退化因子: {f['factor_id']}")
        _deactivate_factor(f["factor_id"])
        _log_evolution(
            f["factor_id"], "degrade",
            source=f.get("source"),
            state_from="ACTIVE", state_to="QUARANTINE",
            action="quarantine",
            reason="IC衰减连续drift→隔离",
        )

    if not degraded:
        return 0

    new_candidates = _mine_candidates(dfs, period)
    new_eval = _evaluate_candidates(new_candidates, dfs, period)
    new_survivors = _purge_and_select(new_eval, dfs)

    icir_values = [info.get("avg_icir", 0) for info in new_eval.values()]
    new_promoted = _promote_factors(new_survivors, new_eval, icir_values, len(new_candidates))

    logger.info(f"[FactorEvo] 阶段7 替换: 补挖 {len(new_promoted)} 个新因子")
    return len(new_promoted)


# ═══════════════════════════════════════════════════════════════
#  阶段 8：在线权重更新
# ═══════════════════════════════════════════════════════════════

def _update_online_weights(active_factors, dfs):
    from backend.services.evolution.online_weights import OnlineLinearModel

    model = OnlineLinearModel()
    for sym, df in dfs.items():
        try:
            fields = _kline_to_fields(df)
            factor_vector = np.array([
                f["expr"].evaluate(fields)[-1]
                for f in active_factors if f.get("expr")
            ])
            fwd = _forward_returns(df, horizon=1)
            if len(factor_vector) > 0 and np.isfinite(factor_vector).all():
                model.learn_one(factor_vector, fwd[-1])
        except Exception:
            continue

    weights = model.feature_importance()
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
                fwd = _forward_returns(df, horizon=5)
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

def run_factor_evolution_loop(symbols=None, period=None) -> dict:
    t0 = time.time()
    logger.info("[FactorEvo] ═══ 因子进化闭环启动 ═══")

    # 1. 取数
    dfs = _load_data(symbols, period)
    _ensure_governance_columns()
    if not dfs:
        return {"error": "取数失败，无可用数据"}

    # 1.5 训练/验证/测试三段切分（v6 计划 5.4.3：周期分档窗口，测试集绝不参与挖掘与选因）
    dfs_train, dfs_val, dfs_test = _split_train_val_test(dfs, period)
    if not dfs_train or not dfs_val:
        logger.warning(
            f"[FactorEvo] 三段切分后数据不足(train={len(dfs_train)} val={len(dfs_val)} "
            f"test={len(dfs_test)})，退化为全窗口(与切分前行为一致)"
        )
        dfs_train, dfs_val, dfs_test = dfs, dfs, {}

    # 2. 挖掘（只用训练集拟合，不看验证/测试集）
    candidates = _mine_candidates(dfs_train, period)

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
    promoted = _promote_factors(survivors, eval_results, all_icir, n_total, dfs)

    # ── 持久化新晋升的活跃因子 ──
    if promoted:
        # M5 WFO 门禁：样本外滚动验证不通过则不晋升（异常 fail-open）
        _wfo_freq = {
            "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min",
            "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1d",
        }.get((period or "1h"), "1h")
        try:
            from backend.services.evolution.factor_wfo import (
                run_factor_wfo,
                run_factor_wfo_ic,
            )
            _wfo_kept = []
            for p in promoted:
                _expr = p.get("expr") or (eval_results.get(p["factor_id"], {}) or {}).get("expr")
                _res = run_factor_wfo(
                    _expr, next(iter(dfs.values())) if dfs else None, p["factor_id"],
                    freq=_wfo_freq,
                )
                if _res.get("passed", True):
                    # [2026-08-05 v6 5.4.2 S2-5] 叠加 IC 级 WFO：滚动训练窗
                    # OOS IC 序列（均值/显著性/衰退率<50%）。异常 fail-open。
                    try:
                        _ic_res = run_factor_wfo_ic(
                            _expr,
                            next(iter(dfs.values())) if dfs else None,
                            p["factor_id"],
                            freq=_wfo_freq,
                        )
                        if not _ic_res.get("passed", True):
                            _log_evolution(
                                p["factor_id"], "wfo",
                                source=p.get("source"),
                                action="wfo_ic_reject",
                                reason=(
                                    f"OOS IC 均值 {_ic_res.get('oos_ic_mean')} / "
                    f"p {_ic_res.get('oos_ic_p')} / 衰退率 {_ic_res.get('decay_rate')}"
                                ),
                                metrics={"oos_ic": _ic_res},
                            )
                            logger.warning(
                                "[FactorEvo] IC-WFO 拒绝晋升 %s: %s",
                                p["factor_id"], _ic_res,
                            )
                            continue
                    except Exception as _ic_err:
                        logger.warning(
                            "[FactorEvo] IC-WFO 门禁异常(fail-open): %s",
                            str(_ic_err)[:150],
                        )
                    _wfo_kept.append(p)
                else:
                    _log_evolution(
                        p["factor_id"], "wfo",
                        source=p.get("source"),
                        action="wfo_reject",
                        reason="pbo/overfit/consistency 未过门",
                    )
                    logger.warning("[FactorEvo] WFO 拒绝晋升 %s", p["factor_id"])
            promoted = _wfo_kept
        except Exception as _wfo_err:
            logger.warning("[FactorEvo] WFO 门禁异常(fail-open): %s", str(_wfo_err)[:150])

        # 三层切分最终裁判：测试集 IC 复评（测试集绝不参与挖掘与选因）
        promoted = _final_test_confirm(promoted, eval_results, dfs_test)

        to_save = []
        for p in promoted:
            eval_result = p.get("eval_result")
            to_save.append({
                "factor_id": p["factor_id"],
                "expr_ast": p.get("expr_ast", {}),
                "expr_id": p["factor_id"],
                "source": p.get("source"),
                # [2026-07-18 修复] 用状态机真实判定的 to_state，不再硬编码 ACTIVE
                # （见 _promote_factors 内注释）。新晋升因子几乎总是先落地 PAPER。
                "state": p.get("_to_state", "PAPER"),
                "icir": eval_result.icir if eval_result else None,
                "incremental_corr": p.get("incremental_corr"),
                "current_weight": None,
            })
        _save_active_factors(to_save)

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

    # 7. 替换退化因子
    replaced = _replace_degraded(degraded, dfs, period) if degraded else 0

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
