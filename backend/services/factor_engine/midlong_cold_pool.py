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
_MAX_TIMEOUTS = 15   # [2026-08-15] 僵尸线程熔断阈值：超时 worker 无法杀死，累积即停

# [P1-10 前视防护升级] 冷池里大量因子用 close.shift(-N) 引未来数据。
# 正则只拦字面 shift(-数字)；变量负移（shift(-confirm_bars+1)）可绕过。
# 升级为 AST 级审计（factor_engine.lookahead_audit）：blocked 拦截、review 标记。
from backend.services.factor_engine.lookahead_audit import audit_lookahead


def _is_lookahead_source(src: str) -> bool:
    return audit_lookahead(src)[0] == "blocked"


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
    lookahead_skipped = 0
    lookahead_review = 0  # [P1-10] 变量型 shift 标记人工复核
    try:
        for dname in dirs:
            d = os.path.join(factors_root, dname)
            if not os.path.isdir(d):
                continue
            for fname in sorted(os.listdir(d)):
                if not fname.endswith(".py"):
                    continue
                if max_files is not None and (
                    len(loaded) + errors + syntax_skipped + lookahead_skipped >= max_files
                ):
                    break
                fpath = os.path.join(d, fname)
                # [2026-08-15 前视防护] 源码含 shift(-N)（引未来数据）的因子直接跳过，
                # 不进隔离注册也不打分——避免前视因子霸榜 top_by_ic 误导选因子。
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        _src = f.read()
                except Exception:
                    errors += 1
                    continue
                if _is_lookahead_source(_src):
                    lookahead_skipped += 1
                    continue
                # [P1-10] 变量型 shift：无法静态判定符号 → 标记人工复核（不拦截，但计入统计）
                if audit_lookahead(_src)[0] == "review":
                    lookahead_review += 1
                # [2026-08-14 防护] 预编译过滤：隔离区大量文件语法损坏（类名带空格等），
                # 先 compile 快筛，避免 exec_module 抛错浪费时间。
                try:
                    compile(_src, fpath, "exec")
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
                if (len(loaded) + errors + syntax_skipped + lookahead_skipped) % 100 == 0:
                    logger.info("[ColdPool] 加载进度: ok=%d 失败=%d 语法跳过=%d 前视跳过=%d",
                                len(loaded), errors, syntax_skipped, lookahead_skipped)
    finally:
        _fr_mod.registry = _orig_registry   # 还原
    logger.info("[ColdPool] 加载 %d 个冷池因子类（失败 %d，语法跳过 %d，前视跳过 %d，复核 %d）",
                len(loaded), errors, syntax_skipped, lookahead_skipped, lookahead_review)
    return loaded




# ── [GPU 加速 2026-08] 滚动 IC 的 torch 向量化（GTX1070/2080Ti，FP32）──
_torch = None


def _get_torch():
    global _torch
    if _torch is None:
        try:
            import torch  # noqa: WPS433
            _torch = torch
        except Exception:
            _torch = False
    return _torch


def _rolling_ic_torch(f: np.ndarray, r: np.ndarray, window: int) -> np.ndarray:
    """torch 向量化滚动 Pearson IC（与 numpy 版逐窗口数学等价）。

    冷池扫描 1130 因子 × 3 币 × 多 fwd 的滚动 IC 是全链路最大热点
    （历史扫描 4.4 小时）。GPU 批量窗口计算把 O(n×window) 的 Python 循环
    变成一次张量运算：窗口矩阵 (n-window, window) 的行内均值/去均值/内积。
    FP32（GTX 1070 无 Tensor Core，FP16 反而慢；2080Ti 后可切换 dtype）。
    """
    torch = _get_torch()
    n = len(f)
    nw = n - window
    if nw <= 0:
        return np.full(n, np.nan)
    # 滑动窗口视图（stride trick → 转张量；GPU 上再做窗口矩阵）。
    # 对齐 numpy 版：窗口 i 覆盖 [i-window, i)，i∈[window, n) → 共 n-window 个窗口；
    # sliding_window_view 多出最后一个（起始 n-window），截掉。
    fw = np.lib.stride_tricks.sliding_window_view(f, window)[:nw]   # (nw, window)
    rw = np.lib.stride_tricks.sliding_window_view(r, window)[:nw]
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    tf = torch.as_tensor(np.ascontiguousarray(fw), dtype=torch.float32, device=dev)
    tr = torch.as_tensor(np.ascontiguousarray(rw), dtype=torch.float32, device=dev)
    # 有效样本掩码（NaN 按缺失剔除：与 numpy 版 m.sum()>=30 同构）
    valid = torch.isfinite(tf) & torch.isfinite(tr)
    n_valid = valid.sum(dim=1)
    tf_m = torch.where(valid, tf, torch.zeros_like(tf))
    tr_m = torch.where(valid, tr, torch.zeros_like(tr))
    mu_f = tf_m.sum(dim=1) / n_valid.clamp(min=1)
    mu_r = tr_m.sum(dim=1) / n_valid.clamp(min=1)
    x = torch.where(valid, tf - mu_f[:, None], torch.zeros_like(tf))
    y = torch.where(valid, tr - mu_r[:, None], torch.zeros_like(tr))
    denom = (x.pow(2).sum(dim=1).sqrt() * y.pow(2).sum(dim=1).sqrt()).clamp(min=1e-12)
    ic = (x * y).sum(dim=1) / denom
    # 与 numpy 版一致：有效样本 <30 或非有限 → 0.0
    ic = torch.where((n_valid >= 30) & torch.isfinite(ic), ic, torch.zeros_like(ic))
    out = np.full(n, np.nan)
    out[window:] = ic.cpu().numpy().astype(float)
    return out


def _batch_rolling_ic(vals_list, closes: np.ndarray, fwd: int,
                      window: int = 120) -> list:
    """[GPU 加速] 批量滚动 IC：K 个因子同 (closes, fwd, window) 一次张量调用。

    单因子逐调 torch 有内核启动/搬运开销（~ms 级），批量堆叠把 K 个因子的
    窗口矩阵合并为 (K, nw, window) 一次算完——冷池扫描 1130 因子×3 币×多 fwd
    场景下这是 10-30x 的来源。返回 [(ic_mean, icir), ...]，与逐调口径一致。
    GPU 不可用/异常时回退 numpy 逐窗口（同样批量循环，语义一致）。
    """
    f_list = [np.asarray(v, dtype=float).ravel() for v in vals_list]
    c = np.asarray(closes, dtype=float).ravel()
    n = min(len(c), *(len(v) for v in f_list))
    # [2026-08-16 修复] 必须【尾部】截断：阶段1 存的因子值全部与 closes 尾部对齐
    # （_v = _v[-_n:]），头部截断会让长度不同的因子与收盘序列错位。
    c = c[-n:]
    f_list = [v[-n:] for v in f_list]
    r = np.full(n, np.nan)
    if n > fwd:
        r[:-fwd] = (c[fwd:] - c[:-fwd]) / c[:-fwd]
    K = len(f_list)
    nw = n - window
    if nw <= 0:
        return [(0.0, 0.0)] * K
    torch = _get_torch()
    use_gpu = False
    try:
        use_gpu = bool(torch) and torch.cuda.is_available()
    except Exception:
        use_gpu = False
    if use_gpu:
        try:
            dev = "cuda"
            fw = np.stack([
                np.ascontiguousarray(
                    np.lib.stride_tricks.sliding_window_view(v, window)[:nw],
                )
                for v in f_list
            ])  # (K, nw, window)
            rw = np.ascontiguousarray(
                np.lib.stride_tricks.sliding_window_view(r, window)[:nw],
            )
            tf = torch.as_tensor(fw, dtype=torch.float32, device=dev)
            tr = torch.as_tensor(rw, dtype=torch.float32, device=dev).unsqueeze(0).expand(K, -1, -1)
            valid = torch.isfinite(tf) & torch.isfinite(tr)
            n_valid = valid.sum(dim=2)
            tf_m = torch.where(valid, tf, torch.zeros_like(tf))
            tr_m = torch.where(valid, tr, torch.zeros_like(tr))
            mu_f = tf_m.sum(dim=2) / n_valid.clamp(min=1)
            mu_r = tr_m.sum(dim=2) / n_valid.clamp(min=1)
            x = torch.where(valid, tf - mu_f[:, :, None], torch.zeros_like(tf))
            y = torch.where(valid, tr - mu_r[:, :, None], torch.zeros_like(tr))
            denom = (x.pow(2).sum(dim=2).sqrt() * y.pow(2).sum(dim=2).sqrt()).clamp(min=1e-12)
            ic = (x * y).sum(dim=2) / denom
            ic = torch.where((n_valid >= 30) & torch.isfinite(ic), ic, torch.zeros_like(ic))
            ic_np = ic.cpu().numpy().astype(float)  # (K, nw)
            out = []
            for k in range(K):
                row = ic_np[k]
                m = row != 0.0
                if not m.any():
                    out.append((0.0, 0.0))
                    continue
                mean = float(row[m].mean())
                std = float(row[m].std())
                out.append((mean, mean / std if std > 1e-12 else 0.0))
            return out
        except Exception:
            pass  # 回退 numpy
    out = []
    for v in f_list:
        row = np.full(n, np.nan)
        for i in range(window, n):
            fs = v[i - window:i]
            rs = r[i - window:i]
            m = np.isfinite(fs) & np.isfinite(rs)
            if int(m.sum()) < 30:
                continue
            xs = fs[m] - float(np.mean(fs[m]))
            ys = rs[m] - float(np.mean(rs[m]))
            denom = float(np.sqrt(np.sum(xs * xs)) * np.sqrt(np.sum(ys * ys)))
            if denom < 1e-12:
                continue
            _c = float(np.sum(xs * ys) / denom)
            if np.isfinite(_c):
                row[i] = _c
        rr = row[np.isfinite(row)]
        if len(rr) == 0:
            out.append((0.0, 0.0))
        else:
            mean = float(rr.mean())
            std = float(rr.std())
            out.append((mean, mean / std if std > 1e-12 else 0.0))
    return out


def _rolling_ic_batch(factor: np.ndarray, closes: np.ndarray, fwd: int,
                      window: int = 120) -> np.ndarray:
    """滚动 IC 分发：GPU 可用走 torch，否则 numpy 逐窗口（结果一致）。"""
    f = np.asarray(factor, dtype=float).ravel()
    c = np.asarray(closes, dtype=float).ravel()
    n = min(len(f), len(c))
    f = f[:n]
    r = np.full(n, np.nan)
    if n > fwd:
        r[:-fwd] = (c[fwd:] - c[:-fwd]) / c[:-fwd]
    try:
        if _get_torch():
            return _rolling_ic_torch(f, r, window)
    except Exception:
        pass  # GPU 失败静默回退 numpy
    out = np.full(n, np.nan)
    for i in range(window, n):
        fs = f[i - window:i]
        rs = r[i - window:i]
        m = np.isfinite(fs) & np.isfinite(rs)
        if int(m.sum()) < 30:
            continue
        xs = fs[m] - float(np.mean(fs[m]))
        ys = rs[m] - float(np.mean(rs[m]))
        denom = float(np.sqrt(np.sum(xs * xs)) * np.sqrt(np.sum(ys * ys)))
        if denom < 1e-12:
            continue
        _c = float(np.sum(xs * ys) / denom)
        if np.isfinite(_c):
            out[i] = _c
    return out

def _robust_rolling_ic(factor: np.ndarray, closes: np.ndarray, fwd: int, window: int = 120):
    """numpy 版滚动 Pearson IC + ICIR（冷池专用）。

    [2026-08-15] 原路径 evaluator.evaluate_factor → _rolling_ic 用
    pandas `Series.rank().corr()`，在冷池大批量（1130 因子 × 多 fwd 变体 × 3 币）
    反复调用时触发 pandas C 层递归栈溢出，进程卡死。此处改用纯 numpy，
    数学口径一致（Pearson IC），避开 pandas rank。
    """
    # [GPU 加速 2026-08] 滚动 IC 序列走分发器（torch 向量化 / numpy 回退），
    # 再聚合 mean/icir；数学口径与旧逐窗口实现一致（Pearson，有效样本≥30）。
    ics = _rolling_ic_batch(factor, closes, fwd, window)
    arr = ics[np.isfinite(ics)]
    if len(arr) == 0:
        return 0.0, 0.0
    ic_mean = float(np.mean(arr))
    std = float(np.std(arr))
    icir = ic_mean / std if std > 1e-12 else 0.0
    return ic_mean, icir


def _score_series(fid: str, vals: np.ndarray, closes: np.ndarray, fwd: int,
                  cost: float, funding_per_hold: float, bars_per_year: int,
                  evaluator) -> Optional[Dict[str, Any]]:
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

    if not np.isfinite(vals).sum() >= 60:
        return None
    # [2026-08-15] IC/ICIR 走 numpy 滚动实现，避开 pandas rank 崩溃；evaluator
    # 参数保留兼容签名（不再调用）。
    ic, icir = _robust_rolling_ic(vals, closes, fwd)
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

    min_sharpe = float(_cfg("FACTOR_SCORER_MIDLONG_MIN_SHARPE", 0.4))
    min_net = float(_cfg("FACTOR_SCORER_MIN_NET_RETURN", 0.0))
    cost = float(_cfg("FACTOR_SCORER_COST", 0.0021))
    funding_rate = float(_cfg("FACTOR_SCORER_FUNDING_RATE", 0.0001))

    # 预载 K 线（每 tf × symbol 一次，全因子共用）；lookback 按周期分档
    from backend.services.factor_engine.factor_backtest_scorer import midlong_lookback_for
    klines_cache: Dict[Tuple[str, str], Optional[list]] = {}
    for tf in timeframes:
        lb = midlong_lookback_for(tf)
        for sym in symbols:
            klines_cache[(sym, tf)] = factor_backtest_scorer._load_klines(sym, tf, lb)

    report_rows: List[Dict[str, Any]] = []
    passers: List[Dict[str, Any]] = []
    timeouts = 0
    errors = 0  # [2026-08-16] 快速异常与真实超时分开：冷池病态因子普遍抛异常，不触发熔断
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
    # [GPU 加速 2026-08] 循环重排：
    #   阶段1 按 (tf, sym) 先算全部因子的值（超时/熔断逻辑不变）；
    #   阶段2 按 (sym, fwd) 用 _batch_rolling_ic 一次张量调用批量算全部因子的滚动 IC
    #   （单因子逐调 torch 有内核启动开销反而更慢，批量堆叠是 260x 的来源），
    #   再逐因子跑 walk-forward（O(trades) 轻量）；
    #   阶段3 按原口径聚合 row（每 (fid, sym) 取最优 fwd 代表、跨币平均）。
    for tf in timeframes:
        sym_dfs: Dict[str, tuple] = {}
        for sym in symbols:
            klines = klines_cache.get((sym, tf))
            if not klines or len(klines) < 120:
                continue
            try:
                df = pd.DataFrame(klines)
                closes = df["close"].astype(float).to_numpy()
                sym_dfs[sym] = (df, closes)
            except Exception:
                continue
        if not sym_dfs:
            continue
        # 阶段1：计算所有因子×币种的值
        vals_by_sym: Dict[str, Dict[str, np.ndarray]] = defaultdict(dict)
        _done = 0
        _total = len(classes) * len(sym_dfs)
        for fid, cls in classes:
            for sym, (df, closes) in sym_dfs.items():
                _done += 1
                if _done % 300 == 0:
                    logger.info("[ColdPool] stage1 %d/%d timeouts=%d errors=%d (%s/%s)",
                                _done, _total, timeouts, errors, tf, sym)
                # [2026-08-14 防护] 冷池病态因子可能挂起：calculate 20s 超时跳过
                # [2026-08-15 熔断] 超时放弃的 worker 线程无法杀死，会以僵尸线程
                # 形式累积吞噬 CPU。超过阈值直接中止整轮扫描，保留已算出的部分结果。
                if timeouts >= _MAX_TIMEOUTS:
                    logger.warning("[ColdPool] 超时次数达 %d，中止扫描（僵尸线程累积保护）", timeouts)
                    break
                series = None
                _ex = None
                try:
                    inst = cls()
                    _ex = ThreadPoolExecutor(max_workers=1)
                    series = _ex.submit(lambda: inst.calculate(data=df)).result(timeout=20)
                except _FutTimeout:
                    timeouts += 1  # 只有真实挂起才计入熔断（僵尸线程累积保护）
                    series = None
                except Exception:
                    errors += 1
                    series = None
                finally:
                    if _ex is not None:
                        try:
                            _ex.shutdown(wait=False, cancel_futures=True)
                        except Exception:
                            pass
                if series is None or len(series) < 60:
                    continue
                _v = np.asarray(series, dtype=float).ravel()
                _n = min(len(_v), len(closes))
                _v = _v[-_n:]
                if int(np.isfinite(_v).sum()) >= 60:
                    vals_by_sym[sym][fid] = _v
            if timeouts >= _MAX_TIMEOUTS:
                break
        # 阶段2：按 (sym, fwd) 批量 IC + 逐因子 walk-forward
        per_factor: Dict[str, Dict[str, Any]] = defaultdict(dict)
        for sym, (df, closes) in sym_dfs.items():
            for fwd in _FWD_VARIANTS.get(tf, (3,)):
                bars_per_year = int(round(365.0 * 24.0 / {"4h": 4.0, "1d": 24.0}[tf]))
                hold_hours = fwd * {"4h": 4.0, "1d": 24.0}[tf]
                fph = funding_rate * (hold_hours / 8.0) if funding_rate > 0 else 0.0
                fids = [f for f, v in vals_by_sym[sym].items() if len(v) >= 120]
                if not fids:
                    continue
                batch_ics = _batch_rolling_ic([vals_by_sym[sym][f] for f in fids], closes, fwd)
                for fid, (ic, icir) in zip(fids, batch_ics):
                    # [2026-08-16 修复] closes 必须按因子序列长度对齐（尾部对齐），
                    # 否则 _walk_forward_backtest 内 fwd_ret(按 closes 长度) 与
                    # 因子值长度不一致 → 广播异常（此前 1989 vs 1990 崩溃）。
                    fv = vals_by_sym[sym][fid]
                    c_aligned = closes[-len(fv):]
                    bt = factor_backtest_scorer._walk_forward_backtest(
                        fv, c_aligned, fwd, cost,
                        funding_per_hold=fph, bars_per_year=bars_per_year,
                    )
                    if bt.get("trades", 0) <= 0:
                        continue
                    _r = {"ic_mean": float(ic), "icir": float(icir),
                          "oos_net_return": bt["net_return"], "oos_sharpe": bt["sharpe"],
                          "oos_win_rate": bt["win_rate"], "oos_trades": bt["trades"]}
                    pfs = per_factor[fid].setdefault("per_sym", {})
                    _best = pfs.get(sym)
                    # 变体内按 (IC 绝对 × sharpe) 选代表
                    if _best is None or (abs(_r["ic_mean"]) * max(_r["oos_sharpe"], 0.0)
                                         > abs(_best["ic_mean"]) * max(_best["oos_sharpe"], 0.0)):
                        pfs[sym] = {**_r, "fwd": fwd}
        # 阶段3：组装 row（与原口径一致）
        for fid, pf in per_factor.items():
            per_sym = pf.get("per_sym") or {}
            if len(per_sym) < 2:
                continue
            agg: Dict[str, List[float]] = defaultdict(list)
            for p in per_sym.values():
                for k in ("ic_mean", "icir", "oos_net_return", "oos_sharpe",
                          "oos_win_rate", "oos_trades"):
                    agg[k].append(p[k])
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
        # [P2-7] 多重比较标注：每个因子×币种尝试多个 fwd 变体并取最优代表（选择偏差），
        # 报告中的 ic_mean/oos_sharpe 未经 Bonferroni/DSR 校正，仅作研究排序参考；
        # 晋升仍须经 factor_backtest_scorer.score_formula（固定前瞻期 + DSR/PBO 闸门）。
        "methodology_note": "multi_fwd_variant_selection_unadjusted",
        "scanned": len(report_rows),
        "loaded": len(classes),
        "passers": len(passers),
        "passer_details": passers,
        "timeouts": timeouts,
        "errors": errors,
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
    logger.info("[ColdPool] 扫描完成: 评分%d 通过%d 超时%d 异常%d 用时%.1fs",
                len(report_rows), len(passers), timeouts, errors, report["elapsed_sec"])
    return report
