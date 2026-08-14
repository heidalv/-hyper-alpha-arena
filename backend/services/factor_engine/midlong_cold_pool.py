"""midlong_cold_pool — 冷池因子（归档/隔离）中线 4h/1d 研究扫描（2026-08-14）。

背景：_ai_gen_archive（57 个）与 _ai_gen_quarantine（1122 个）里的历史因子
都是按短线 1h 口径淘汰/隔离的，从未在 4h/1d 上评过分。当前中线弹药只有 1 颗，
把冷池拿到中线重评是性价比最高的扩源方向。

安全设计：
- 隔离加载：临时把 factor_registry 模块级 `registry` 变量重定向到独立实例，
  导入冷池文件后立即还原 —— 不污染实盘全局 registry/FACTORS。
- 报告优先：默认只写报告（data/midlong_cold_pool_report.json）；
  promote=True 时把 A/B 通过者登记为引用候选（仍经闸门后续复评）。
- 前瞻期探索：同一数据上并行评估多个 fwd 变体（1d: 1/2/3；4h: 3/4/6），
  报告每变体指标，便于参数选优。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import time
import warnings
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# [2026-08-14] 冷池因子大量退化序列会刷 np.corrcoef 除零警告（每秒数千行，
# 足以压垮 stderr 管道）。扫描是研究用途，指标侧已用 isfinite/std 防护，
# 此处抑制警告噪音。
warnings.filterwarnings("ignore", message="invalid value encountered")
warnings.filterwarnings("ignore", message="divide by zero")

logger = logging.getLogger(__name__)

_COLD_DIRS = ("_ai_gen_archive", "_ai_gen_quarantine")
_SYMBOLS = ("BTC", "ETH", "SOL")
_FWD_VARIANTS = {"4h": (3, 4, 6), "1d": (1, 2, 3)}
_REPORT_PATH = os.path.join("data", "midlong_cold_pool_report.json")


def _cfg(name: str, default):
    from backend.config import settings as _s
    return getattr(_s, name, default)


def _admin_tenant() -> Optional[int]:
    try:
        from backend.services.coin_select_platform_service import resolve_admin_tenant_id
        return resolve_admin_tenant_id()
    except Exception:
        return None


def _isolated_registry():
    """构造独立 FactorRegistry 实例（不触碰全局单例的 _instance 指针）。"""
    from backend.services.factor_engine.factor_registry import FactorRegistry

    iso = object.__new__(FactorRegistry)
    iso._factors = {}
    iso._metadata_cache = {}
    iso._category_index = defaultdict(set)
    iso._dependency_graph = defaultdict(set)
    iso._alias_index = {}
    return iso


def load_cold_factor_classes(
    dirs: Tuple[str, ...] = _COLD_DIRS,
    max_files: Optional[int] = None,
) -> List[Tuple[str, type]]:
    """隔离加载冷池因子类，返回 [(factor_id, cls), ...]。"""
    from backend.services.factor_engine.factor_base import BaseFactor
    from backend.services.factor_engine import factor_registry as _fr_mod

    factors_root = os.path.join(os.path.dirname(os.path.abspath(_fr_mod.__file__)), "factors")
    iso = _isolated_registry()
    _orig_registry = _fr_mod.registry
    _fr_mod.registry = iso          # 装饰器 @register_factor() 目标重定向

    loaded: List[Tuple[str, type]] = []
    errors = 0
    syntax_skipped = 0
    try:
        for dname in dirs:
            d = os.path.join(factors_root, dname)
            if not os.path.isdir(d):
                continue
            for fname in sorted(os.listdir(d)):
                if not fname.endswith(".py"):
                    continue
                if max_files is not None and len(loaded) + errors + syntax_skipped >= max_files:
                    break
                fpath = os.path.join(d, fname)
                # [2026-08-14 防护] 预编译过滤：隔离区大量文件语法损坏（类名带空格等），
                # 先 compile 快筛，避免 exec_module 抛错浪费时间。
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        compile(f.read(), fpath, "exec")
                except Exception:
                    syntax_skipped += 1
                    continue
                modname = f"cold_{dname}_{fname[:-3]}"
                try:
                    spec = importlib.util.spec_from_file_location(modname, fpath)
                    if spec is None or spec.loader is None:
                        errors += 1
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[modname] = mod
                    spec.loader.exec_module(mod)
                    for attr in dir(mod):
                        cls = getattr(mod, attr)
                        if (isinstance(cls, type) and issubclass(cls, BaseFactor)
                                and cls is not BaseFactor and cls.__module__ == modname):
                            try:
                                meta = cls().metadata
                                fid = str(getattr(meta, "factor_id", "") or attr)
                                loaded.append((fid, cls))
                            except Exception:
                                errors += 1
                except Exception:
                    errors += 1
                if (len(loaded) + errors + syntax_skipped) % 100 == 0:
                    logger.info("[ColdPool] 加载进度: ok=%d 失败=%d 语法跳过=%d",
                                len(loaded), errors, syntax_skipped)
    finally:
        _fr_mod.registry = _orig_registry   # 还原
    logger.info("[ColdPool] 加载 %d 个冷池因子类（失败 %d，语法跳过 %d）",
                len(loaded), errors, syntax_skipped)
    return loaded


def _score_series(fid: str, vals: np.ndarray, closes: np.ndarray, fwd: int,
                  cost: float, funding_per_hold: float, bars_per_year: int,
                  evaluator) -> Optional[Dict[str, Any]]:
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

    if not np.isfinite(vals).sum() >= 60:
        return None
    ic = None
    try:
        rep = evaluator.evaluate_factor(fid, pd.Series(vals), pd.Series(closes), forward_period=fwd)
        if rep.data_points >= 30:
            ic = rep.ic_mean
            icir = rep.icir
        else:
            ic = icir = 0.0
    except Exception:
        ic = icir = 0.0
    bt = factor_backtest_scorer._walk_forward_backtest(
        vals, closes, fwd, cost,
        funding_per_hold=funding_per_hold, bars_per_year=bars_per_year,
    )
    if bt.get("trades", 0) <= 0:
        return None
    return {
        "ic_mean": float(ic), "icir": float(icir),
        "oos_net_return": bt["net_return"], "oos_sharpe": bt["sharpe"],
        "oos_win_rate": bt["win_rate"], "oos_trades": bt["trades"],
    }


def scan_cold_pool_midlong(
    symbols: Tuple[str, ...] = _SYMBOLS,
    timeframes: Tuple[str, ...] = ("4h", "1d"),
    promote: bool = False,
    max_files: Optional[int] = None,
) -> Dict[str, Any]:
    """冷池因子中线扫描（报告 + 可选晋升）。"""
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
    from backend.services.factor_engine.factor_evaluator import get_factor_evaluator
    from backend.services.factor_engine.midlong_registry_factors import _grade_from_metrics

    t0 = time.time()
    classes = load_cold_factor_classes(max_files=max_files)
    if not classes:
        return {"error": "无冷池因子可加载", "loaded": 0}

    lookback = int(_cfg("FACTOR_SCORER_MIDLONG_LOOKBACK", 900))
    min_sharpe = float(_cfg("FACTOR_SCORER_MIDLONG_MIN_SHARPE", 0.4))
    min_net = float(_cfg("FACTOR_SCORER_MIN_NET_RETURN", 0.0))
    cost = float(_cfg("FACTOR_SCORER_COST", 0.0021))
    funding_rate = float(_cfg("FACTOR_SCORER_FUNDING_RATE", 0.0001))

    # 预载 K 线（每 tf × symbol 一次，全因子共用）
    klines_cache: Dict[Tuple[str, str], Optional[list]] = {}
    for tf in timeframes:
        for sym in symbols:
            klines_cache[(sym, tf)] = factor_backtest_scorer._load_klines(sym, tf, lookback)

    report_rows: List[Dict[str, Any]] = []
    passers: List[Dict[str, Any]] = []
    timeouts = 0
    from concurrent.futures import ThreadPoolExecutor
    for fid, cls in classes:
        for tf in timeframes:
            per_sym = {}
            agg: Dict[str, List[float]] = defaultdict(list)
            for sym in symbols:
                klines = klines_cache.get((sym, tf))
                if not klines or len(klines) < 120:
                    continue
                try:
                    df = pd.DataFrame(klines)
                except Exception:
                    continue
                # [2026-08-14 防护] 冷池病态因子可能挂起：calculate 20s 超时跳过
                # （线程不可杀，shutdown(wait=False) 后线程在进程退出时终止）。
                series = None
                _ex = None
                try:
                    inst = cls()
                    _ex = ThreadPoolExecutor(max_workers=1)
                    series = _ex.submit(lambda: inst.calculate(data=df)).result(timeout=20)
                except Exception:
                    timeouts += 1
                    series = None
                finally:
                    if _ex is not None:
                        try:
                            _ex.shutdown(wait=False, cancel_futures=True)
                        except Exception:
                            pass
                if series is None or len(series) < 60:
                    continue
                vals = np.asarray(series, dtype=float)
                closes = df["close"].astype(float).to_numpy()
                n = min(len(vals), len(closes))
                vals, closes = vals[-n:], closes[-n:]
                best: Optional[Dict[str, Any]] = None
                for fwd in _FWD_VARIANTS.get(tf, (3,)):
                    bars_per_year = int(round(365.0 * 24.0 / {"4h": 4.0, "1d": 24.0}[tf]))
                    hold_hours = fwd * {"4h": 4.0, "1d": 24.0}[tf]
                    fph = funding_rate * (hold_hours / 8.0) if funding_rate > 0 else 0.0
                    r = _score_series(
                        fid, vals, closes, fwd, cost, fph, bars_per_year,
                        get_factor_evaluator(forward_period=fwd),
                    )
                    if r is None:
                        continue
                    # 变体内按 (IC 绝对 × sharpe) 选代表
                    if best is None or (abs(r["ic_mean"]) * max(r["oos_sharpe"], 0.0)
                                        > abs(best["ic_mean"]) * max(best["oos_sharpe"], 0.0)):
                        best = {**r, "fwd": fwd}
                if best is None:
                    continue
                per_sym[sym] = best
                for k in ("ic_mean", "icir", "oos_net_return", "oos_sharpe",
                          "oos_win_rate", "oos_trades"):
                    agg[k].append(best[k])
            if len(agg["ic_mean"]) < 2:
                continue
            row = {
                "factor_id": fid,
                "timeframe": tf,
                "ic_mean": round(float(np.mean(agg["ic_mean"])), 5),
                "icir": round(float(np.mean(agg["icir"])), 4),
                "oos_net_return": round(float(np.mean(agg["oos_net_return"])), 6),
                "oos_sharpe": round(float(np.mean(agg["oos_sharpe"])), 4),
                "oos_win_rate": round(float(np.mean(agg["oos_win_rate"])), 4),
                "oos_trades": int(np.sum(agg["oos_trades"])),
                "fwd": int(np.round(np.mean([p["fwd"] for p in per_sym.values()]))) if per_sym else None,
                "per_symbol": per_sym,
            }
            grade = _grade_from_metrics(
                row["ic_mean"], row["icir"], row["oos_sharpe"], row["oos_net_return"],
                row["oos_net_return"] / max(row["oos_trades"], 1), cost, min_sharpe, min_net,
            )
            row["grade"] = grade
            row["admitted"] = grade in ("A", "B")
            report_rows.append(row)
            if row["admitted"]:
                passers.append(row)

    report = {
        "scanned": len(report_rows),
        "loaded": len(classes),
        "passers": len(passers),
        "passer_details": passers,
        "timeouts": timeouts,
        "top_by_ic": sorted(report_rows, key=lambda r: -abs(r["ic_mean"]))[:20],
        "elapsed_sec": round(time.time() - t0, 1),
    }
    try:
        with open(_REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("[ColdPool] 报告写入失败: %s", e)

    if promote and passers:
        from backend.services.factor_engine.custom_factor_store import custom_factor_store
        _tid = _admin_tenant()
        for p in passers:
            fid = str(p["factor_id"])
            tf = str(p["timeframe"])
            custom_factor_store.register_reference(
                f"cold_{fid}@{tf}", registry_factor_id=fid,
                tenant_id=_tid, horizon="midlong", timeframe=tf,
                source="cold_pool",
            )
            # 冷池因子不在 live registry：登记为候选，待后续人工复核晋升，
            # 避免把未在 FACTORS 中的类直接置 active。
            logger.info("[ColdPool] 通过因子登记候选: %s@%s grade=%s", fid, tf, p["grade"])
    logger.info("[ColdPool] 扫描完成: 评分%d 通过%d 用时%.1fs",
                len(report_rows), len(passers), report["elapsed_sec"])
    return report
