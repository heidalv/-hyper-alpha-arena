#!/usr/bin/env python3
"""短线因子（Scalp）滚动样本外验证（walk-forward，只读、不触碰交易主链路）。

背景
----
docs/SCALP_FACTOR_STRATEGY_ANALYSIS_2026-07-06.md 第2.3节指出：Scalp 因子层此前
没有独立的历史回测/验证能力，任何因子或阈值调整只能靠实盘/纸面跑一段时间来验证，
不符合 2026 年主流量化"walk-forward 验证是防过拟合第一道防线"的共识。本脚本补上
这一环：用 `alpha_market.crypto_klines` 的真实 5m K 线，对当前 Scalp 因子路由器里
用到的核心因子做滚动样本外评估。

方法（结合数据现实做的诚实简化）
----
- 本项目 5m 历史目前只有约 1 周（见 analyze_cycle_direction_sensitivity.py 的同类
  说明），不足以做"训练窗口90天/测试窗口30天"式的经典 walk-forward。因子本身
  （RSI/MACD/ADX 等）也是固定公式、没有可拟合参数，不存在"用 IS 拟合参数、冻结后
  测 OOS"这一步。
- 因此这里做的是等价但更适配当前数据量的滚动样本外评估：把时间轴切成连续、不重叠
  的若干折（fold），每一折只用该折自己的数据独立计算 IC / 方向命中率，折与折之间
  完全隔离（不会用未来折的数据"偷看"）。
- 报告重点不是单一整段 IC（容易被某一段行情主导，产生虚假的确定性），而是
  **跨折的 IC 均值、标准差、同号折占比**——这才是"这个因子的预测力是否稳定，还是
  只在某段行情里凑巧有效"的诚实证据。
- 额外加了一个简单的成本模型：只有当"方向 × 前瞻收益"超过一个可配置的往返成本
  (--cost-pct，默认 0.12%，对应 taker 手续费×2 + 保守滑点估计) 时才算真正命中，
  输出"成本调整后命中率"，避免"账面上看着不错、一算手续费就转盈为亏"的常见陷阱。

运行
----
    backend\\.venv\\Scripts\\python.exe scripts\\walk_forward_validate_scalp_factors.py
    ...\\python.exe scripts\\walk_forward_validate_scalp_factors.py --symbols BTC,ETH,SOL --forward-bars 12
    ...\\python.exe scripts\\walk_forward_validate_scalp_factors.py --fold-bars 288 --cost-pct 0.0015
    ...\\python.exe scripts\\walk_forward_validate_scalp_factors.py --with-cycle-prob

输出
----
- 控制台：逐因子的跨折 IC 均值/标准差/同号折占比、成本调整后命中率
- data/scalp_factor_validation/walk_forward_<YYYYMMDD_HHMMSS>.json：完整结果

--with-cycle-prob（2026-07-06 新增，留档对比用，不阻塞上线）
----
额外对比"纯因子合成信号" vs "因子+cycle_prob(AI周期概率引擎)融合信号"在同一段历史上的
样本外表现，对应 `backend/services/scalp/scalp_fusion_scorer.py` 里接入
ScalpFactorRouter 的融合逻辑。做法：
1. 用本脚本已有的方向性因子（rsi/macd_hist/momentum/di_diff/ema_align）等权合成一个
   代理的"composite_factor_only"信号（简化代理，不是生产环境 IC/regime 加权 Top-15
   聚合的精确复刻，仅用于相对比较）；
2. 额外加载该币种的15m K线，用 cycle_direction_probability.build_feature_series 逐根
   算特征，调 cycle_probability_engine.estimate("short", ...) 拿到
   (prob_up-prob_down)×calibration_quality，前向对齐（forward-fill）到5m时间戳；
3. "composite_fused_cycle_prob" = composite_factor_only + 对齐后的 cycle_prob 信号
   （与生产融合公式的加权+校准感知逻辑等价，只是这里两者都不额外归一化）；
4. 两个信号跑同一套折内 IC / 成本调整命中率评估，输出对比行，诚实呈现融合是否真的
   带来提升（当前 short tier 校准质量约0.05，预期提升幅度很小，这是刻意的安全设计，
   不是bug）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass

# 复用概率引擎中的"单一权威"向量化指标实现，避免与 factor_engine 的定义漂移。
from backend.services.cycle_direction_probability import (  # noqa: E402
    ema_series as _ema,
    rsi_series as _rsi,
    atr_series as _atr,
    adx_series as _adx,
)

DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "WIF"]
TIMEFRAME = "5m"

# 与 ScalpFactorRouter/factor_engine 里实际参与打分的核心因子对齐
# (RSI/MACD/Momentum/ADX/EMA排列/ATR%/BB Z-score/量比 —— 覆盖
#  momentum / trend / volatility / mean_reversion / volume 五大类别)
FEATURES = [
    "rsi", "macd_hist", "momentum", "adx", "di_diff",
    "ema_align", "atr_pct", "bb_zscore", "vol_ratio",
]

# 非方向性因子（强度类），只报 IC 不报方向命中率
NON_DIRECTIONAL = {"adx", "atr_pct", "vol_ratio"}

# --with-cycle-prob 模式下额外对比的两个"伪因子"名称
COMPOSITE_ONLY = "composite_factor_only"
COMPOSITE_FUSED = "composite_fused_cycle_prob"


def _psycopg_dsn() -> str:
    raw = os.environ.get(
        "MARKET_DATABASE_URL",
        "postgresql://db_admin:YOUR_DB_PASSWORD@localhost:5432/alpha_market",
    )
    return raw.replace("postgresql+psycopg://", "postgresql://")


def _best_exchange(cur, symbol: str, period: str) -> Optional[str]:
    cur.execute(
        "SELECT exchange, count(*) c FROM crypto_klines WHERE symbol=%s AND period=%s "
        "GROUP BY exchange ORDER BY c DESC LIMIT 1",
        (symbol.upper(), period),
    )
    row = cur.fetchone()
    return row[0] if row else None


def load_klines(cur, symbol: str, period: str) -> Optional[Dict[str, np.ndarray]]:
    exch = _best_exchange(cur, symbol, period)
    if not exch:
        return None
    cur.execute(
        "SELECT timestamp, open_price, high_price, low_price, close_price, volume "
        "FROM crypto_klines WHERE symbol=%s AND period=%s AND exchange=%s "
        "ORDER BY timestamp ASC",
        (symbol.upper(), period, exch),
    )
    rows = cur.fetchall()
    if not rows:
        return None
    ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
    o = np.array([float(r[1] or 0) for r in rows])
    h = np.array([float(r[2] or 0) for r in rows])
    l = np.array([float(r[3] or 0) for r in rows])
    c = np.array([float(r[4] or 0) for r in rows])
    v = np.array([float(r[5] or 0) for r in rows])
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v, "exchange": exch}


def build_features(k: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    h, l, c, v = k["high"], k["low"], k["close"], k["volume"]
    n = len(c)

    adx, pdi, mdi = _adx(h, l, c)
    di_diff = pdi - mdi

    rsi = _rsi(c, 14)

    ema12, ema26 = _ema(c, 12), _ema(c, 26)
    macd_line = ema12 - ema26
    macd_signal = _ema(np.nan_to_num(macd_line), 9)
    macd_hist = macd_line - macd_signal

    momentum = np.full(n, np.nan)
    for i in range(10, n):
        momentum[i] = (c[i] - c[i - 10]) / c[i - 10] if c[i - 10] > 0 else np.nan

    ema9, ema21, ema50 = _ema(c, 9), _ema(c, 21), _ema(c, 50)
    ema_align = np.zeros(n)
    for i in range(n):
        if not (math.isnan(ema9[i]) or math.isnan(ema21[i]) or math.isnan(ema50[i])):
            if ema9[i] > ema21[i] > ema50[i]:
                ema_align[i] = 1.0
            elif ema9[i] < ema21[i] < ema50[i]:
                ema_align[i] = -1.0

    atr = _atr(h, l, c, 14)
    atr_pct = np.where(c > 0, atr / c, np.nan)

    bb_zscore = np.full(n, np.nan)
    for i in range(20, n):
        window = c[i - 20:i]
        m = np.mean(window)
        sd = np.std(window)
        bb_zscore[i] = (c[i] - m) / sd if sd > 1e-12 else 0.0

    vol_ratio = np.full(n, np.nan)
    for i in range(20, n):
        m = np.mean(v[i - 20:i])
        vol_ratio[i] = v[i] / m if m > 0 else np.nan

    return {
        "rsi": rsi,
        "macd_hist": macd_hist,
        "momentum": momentum,
        "adx": adx,
        "di_diff": di_diff,
        "ema_align": ema_align,
        "atr_pct": atr_pct,
        "bb_zscore": bb_zscore,
        "vol_ratio": vol_ratio,
    }


def forward_returns(close: np.ndarray, n_ahead: int) -> np.ndarray:
    n = len(close)
    out = np.full(n, np.nan)
    for i in range(n - n_ahead):
        if close[i] > 0:
            out[i] = (close[i + n_ahead] - close[i]) / close[i]
    return out


def _feature_direction_signal(name: str, val: np.ndarray) -> np.ndarray:
    """与 factor_signal_generator 的方向映射语义对齐（简化版，仅用于统计评估）。"""
    if name == "rsi":
        return np.where(val > 55, 1.0, np.where(val < 45, -1.0, np.where(np.isnan(val), np.nan, 0.0)))
    if name == "bb_zscore":
        # 均值回归：高于均值(正Z)→预期回落→看空；低于均值→看多
        return np.where(val > 0.5, -1.0, np.where(val < -0.5, 1.0, np.where(np.isnan(val), np.nan, 0.0)))
    if name in ("macd_hist", "momentum", "di_diff", "ema_align"):
        return np.where(val > 0, 1.0, np.where(val < 0, -1.0, np.where(np.isnan(val), np.nan, 0.0)))
    # adx / atr_pct / vol_ratio：强度类，无方向
    return np.full(len(val), np.nan)


def _spearman_ic(x: np.ndarray, y: np.ndarray) -> Tuple[Optional[float], int]:
    mask = ~(np.isnan(x) | np.isnan(y))
    n = int(mask.sum())
    if n < 20:
        return None, n
    xr = _rankdata(x[mask])
    yr = _rankdata(y[mask])
    xr = xr - xr.mean()
    yr = yr - yr.mean()
    denom = math.sqrt(np.sum(xr ** 2) * np.sum(yr ** 2))
    if denom < 1e-12:
        return 0.0, n
    return float(np.sum(xr * yr) / denom), n


def _rankdata(a: np.ndarray) -> np.ndarray:
    order = a.argsort()
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(len(a), dtype=float)
    return ranks


def _cost_adjusted_hit_rate(sig: np.ndarray, fwd: np.ndarray, cost_pct: float) -> Tuple[Optional[float], Optional[float], int]:
    """返回 (原始命中率, 成本调整后命中率, 有效信号样本数)。

    成本调整后命中率：只有 方向×前瞻收益 > cost_pct 才算真正的"净赚"命中，
    而不是简单看符号是否一致（那样会把"赚了但不够付手续费"的假胜利也算进去）。
    """
    mask = ~(np.isnan(sig) | np.isnan(fwd)) & (sig != 0)
    n = int(mask.sum())
    if n < 20:
        return None, None, n
    s = sig[mask]
    f = fwd[mask]
    raw_hit = float(np.mean((np.sign(f) == s).astype(float)))
    net = s * f  # 方向正确且赚到的净收益（未扣成本前）
    cost_hit = float(np.mean((net > cost_pct).astype(float)))
    return raw_hit, cost_hit, n


def _to_directional_sign(x: np.ndarray, band: float = 0.05) -> np.ndarray:
    """把连续合成信号折成 ±1/0 方向信号（band 内视为中性），供成本调整命中率复用。"""
    return np.where(
        np.isnan(x), np.nan,
        np.where(x > band, 1.0, np.where(x < -band, -1.0, 0.0)),
    )


def build_composite_signal(feats: Dict[str, np.ndarray]) -> np.ndarray:
    """近似 ScalpFactorRouter 的合成方向信号：方向性因子的方向信号等权平均。

    这是一个简化代理 —— 生产环境的真实聚合是 IC/regime 加权后取 Top-15 + 同类别
    权重上限（见 factor_signal_generator.py），这里为了在历史数据上快速跑出"纯因子
    vs 因子+cycle_prob融合"的相对对比，改用等权平均代替，不是对生产公式的精确复刻。
    """
    dir_feats = [f for f in FEATURES if f not in NON_DIRECTIONAL]
    n = len(feats[dir_feats[0]])
    acc = np.zeros(n)
    cnt = np.zeros(n)
    for f in dir_feats:
        sig = _feature_direction_signal(f, feats[f])
        valid = ~np.isnan(sig)
        acc[valid] += sig[valid]
        cnt[valid] += 1.0
    with np.errstate(invalid="ignore", divide="ignore"):
        composite = np.where(cnt > 0, acc / np.maximum(cnt, 1.0), np.nan)
    return composite


def load_cycle_prob_signal_5m_aligned(cur, symbol: str, ts_5m: np.ndarray) -> Optional[np.ndarray]:
    """加载该币种15m K线，逐根算 cycle_prob(short tier) 估计，再前向对齐到5m时间戳。

    返回与 ts_5m 等长的数组：(P涨-P跌)×校准质量，不可用位置为 nan。与
    `scalp_fusion_scorer.compute_fusion_adjustment` 里生产环境用的量是同一个口径
    （只是生产端是实时查最新一根，这里是历史逐根回放）。
    """
    try:
        from backend.services.cycle_direction_probability import (
            FEATURES as CP_FEATURES,
            build_feature_series,
            cycle_probability_engine,
        )
    except Exception:
        return None

    k15 = load_klines(cur, symbol, "15m")
    if not k15 or len(k15["close"]) < 60:
        return None

    feats15 = build_feature_series({
        "high": k15["high"], "low": k15["low"], "close": k15["close"], "volume": k15["volume"],
    })
    n15 = len(k15["close"])
    net15 = np.full(n15, np.nan)
    for i in range(n15):
        row: Dict[str, float] = {}
        for f in CP_FEATURES:
            v = feats15[f][i] if f in feats15 else None
            if v is None:
                continue
            try:
                vf = float(v)
            except (TypeError, ValueError):
                continue
            if math.isnan(vf):
                continue
            row[f] = vf
        if not row:
            continue
        try:
            r = cycle_probability_engine.estimate("short", row)
        except Exception:
            continue
        if not r.available:
            continue
        net15[i] = (r.prob_up - r.prob_down) * float(r.calibration_quality or 0.0)

    if np.all(np.isnan(net15)):
        return None

    ts15 = k15["ts"]
    idx = np.searchsorted(ts15, ts_5m, side="right") - 1
    aligned = np.full(len(ts_5m), np.nan)
    valid_idx = idx >= 0
    clipped = np.clip(idx, 0, n15 - 1)
    aligned[valid_idx] = net15[clipped[valid_idx]]
    return aligned


@dataclass
class FoldFeatureStat:
    ic: Optional[float]
    raw_hit: Optional[float]
    cost_hit: Optional[float]
    n: int


@dataclass
class FeatureSummary:
    name: str
    fold_ics: List[float] = field(default_factory=list)
    fold_cost_hits: List[float] = field(default_factory=list)
    total_n: int = 0
    ic_mean: Optional[float] = None
    ic_std: Optional[float] = None
    ic_same_sign_ratio: Optional[float] = None
    cost_hit_mean: Optional[float] = None
    verdict: str = ""


def summarize_feature(name: str, per_fold: List[FoldFeatureStat]) -> FeatureSummary:
    fs = FeatureSummary(name=name)
    ics = [f.ic for f in per_fold if f.ic is not None]
    cost_hits = [f.cost_hit for f in per_fold if f.cost_hit is not None]
    fs.fold_ics = [round(x, 4) for x in ics]
    fs.fold_cost_hits = [round(x, 4) for x in cost_hits]
    fs.total_n = sum(f.n for f in per_fold)

    if len(ics) >= 2:
        fs.ic_mean = float(np.mean(ics))
        fs.ic_std = float(np.std(ics))
        sign = 1 if fs.ic_mean >= 0 else -1
        fs.ic_same_sign_ratio = float(np.mean([1.0 if (sign * x) > 0 else 0.0 for x in ics]))
    elif len(ics) == 1:
        fs.ic_mean = ics[0]
        fs.ic_std = 0.0
        fs.ic_same_sign_ratio = 1.0

    if cost_hits:
        fs.cost_hit_mean = float(np.mean(cost_hits))

    # 简单结论判定：|IC均值| 要有一定量级、且跨折同号占比不能太低（否则就是"运气"）
    if fs.ic_mean is None:
        fs.verdict = "样本不足"
    elif fs.ic_same_sign_ratio is not None and fs.ic_same_sign_ratio < 0.5:
        fs.verdict = "不稳定（跨折方向反复翻转，疑似regime噪声而非真实预测力）"
    elif abs(fs.ic_mean) < 0.02:
        fs.verdict = "预测力极弱（|IC|<0.02）"
    elif fs.ic_same_sign_ratio is not None and fs.ic_same_sign_ratio >= 0.7:
        fs.verdict = "稳定有效" if abs(fs.ic_mean) >= 0.04 else "弱但稳定"
    else:
        fs.verdict = "中等/需继续观察"
    return fs


def run_walk_forward(
    cur,
    symbols: List[str],
    forward_bars: int,
    fold_bars: int,
    cost_pct: float,
    min_bars: int,
    with_cycle_prob: bool = False,
) -> Tuple[Dict[str, FeatureSummary], Dict]:
    feature_names = list(FEATURES)
    if with_cycle_prob:
        feature_names += [COMPOSITE_ONLY, COMPOSITE_FUSED]
    per_feature_folds: Dict[str, List[FoldFeatureStat]] = {f: [] for f in feature_names}
    meta = {"symbols_used": [], "n_folds_by_symbol": {}, "bars_by_symbol": {}, "cycle_prob_symbols": []}

    for sym in symbols:
        k = load_klines(cur, sym, TIMEFRAME)
        if not k or len(k["close"]) < max(min_bars, fold_bars + forward_bars + 60):
            continue
        feats = build_features(k)
        fwd = forward_returns(k["close"], forward_bars)
        n = len(k["close"])
        meta["symbols_used"].append(sym)
        meta["bars_by_symbol"][sym] = n

        composite = None
        cycle_prob_aligned = None
        if with_cycle_prob:
            composite = build_composite_signal(feats)
            cycle_prob_aligned = load_cycle_prob_signal_5m_aligned(cur, sym, k["ts"])
            if cycle_prob_aligned is not None:
                meta["cycle_prob_symbols"].append(sym)

        n_folds = 0
        start = 60  # 跳过指标 warmup
        while start + fold_bars <= n - forward_bars:
            end = start + fold_bars
            for name in FEATURES:
                x = feats[name][start:end]
                y = fwd[start:end]
                ic, n_ic = _spearman_ic(x, y)
                sig = _feature_direction_signal(name, x)
                raw_hit, cost_hit, n_hit = _cost_adjusted_hit_rate(sig, y, cost_pct)
                per_feature_folds[name].append(
                    FoldFeatureStat(ic=ic, raw_hit=raw_hit, cost_hit=cost_hit, n=n_ic)
                )

            if with_cycle_prob and composite is not None and cycle_prob_aligned is not None:
                y = fwd[start:end]
                comp_x = composite[start:end]
                ic_c, n_c = _spearman_ic(comp_x, y)
                sig_c = _to_directional_sign(comp_x)
                raw_c, cost_c, n_hit_c = _cost_adjusted_hit_rate(sig_c, y, cost_pct)
                per_feature_folds[COMPOSITE_ONLY].append(
                    FoldFeatureStat(ic=ic_c, raw_hit=raw_c, cost_hit=cost_c, n=n_c)
                )

                cp_x = cycle_prob_aligned[start:end]
                fused_x = np.where(
                    np.isnan(cp_x), comp_x, comp_x + cp_x,
                )
                ic_f, n_f = _spearman_ic(fused_x, y)
                sig_f = _to_directional_sign(fused_x)
                raw_f, cost_f, n_hit_f = _cost_adjusted_hit_rate(sig_f, y, cost_pct)
                per_feature_folds[COMPOSITE_FUSED].append(
                    FoldFeatureStat(ic=ic_f, raw_hit=raw_f, cost_hit=cost_f, n=n_f)
                )

            n_folds += 1
            start = end
        meta["n_folds_by_symbol"][sym] = n_folds

    summaries = {name: summarize_feature(name, folds) for name, folds in per_feature_folds.items()}
    return summaries, meta


def print_report(
    summaries: Dict[str, FeatureSummary], meta: Dict, forward_bars: int, fold_bars: int,
    cost_pct: float, feature_names: Optional[List[str]] = None,
) -> None:
    feature_names = feature_names or FEATURES
    print("\n" + "=" * 100)
    print(f"Scalp 因子滚动样本外验证（5m，前瞻{forward_bars}根≈{forward_bars*5}分钟，"
          f"每折{fold_bars}根≈{fold_bars*5/60:.1f}小时，往返成本假设{cost_pct:.3%}）")
    print("=" * 100)
    print(f"参与币种: {', '.join(meta['symbols_used'])}")
    for sym, nf in meta["n_folds_by_symbol"].items():
        print(f"  {sym}: {meta['bars_by_symbol'][sym]} 根5m K线 → {nf} 折")
    if meta.get("cycle_prob_symbols"):
        print(f"cycle_prob(15m short tier)覆盖币种: {', '.join(meta['cycle_prob_symbols'])}")

    header = f"{'因子':<28}{'折数':>6}{'样本数':>10}{'IC均值':>10}{'IC标准差':>10}{'同号折占比':>12}{'成本调整命中率':>16}   结论"
    print("\n" + header)
    print("-" * len(header.encode("gbk", errors="ignore")))
    for name in feature_names:
        s = summaries.get(name)
        if s is None:
            continue
        n_folds = len(s.fold_ics)
        ic_mean = f"{s.ic_mean:+.4f}" if s.ic_mean is not None else "-"
        ic_std = f"{s.ic_std:.4f}" if s.ic_std is not None else "-"
        same_sign = f"{s.ic_same_sign_ratio:.1%}" if s.ic_same_sign_ratio is not None else "-"
        cost_hit = f"{s.cost_hit_mean:.1%}" if s.cost_hit_mean is not None else "-"
        print(f"{name:<28}{n_folds:>6}{s.total_n:>10}{ic_mean:>10}{ic_std:>10}{same_sign:>12}{cost_hit:>16}   {s.verdict}")

    print("\n注：因子公式本身无可拟合参数，这里的\"折\"是完全隔离、按时间顺序切分的样本外区间，")
    print("跨折 IC 同号占比越高代表这个因子的预测方向越稳定，而不是恰好命中某一段行情。")
    print("成本调整命中率 = 方向×前瞻收益 需超过往返成本才算命中，用于判断因子的边际是否够付手续费/滑点。")
    if COMPOSITE_ONLY in summaries and COMPOSITE_FUSED in summaries:
        print(f"\n{COMPOSITE_ONLY} = 方向性因子等权合成（纯因子代理信号）；"
              f"{COMPOSITE_FUSED} = 该信号 + cycle_prob(AI周期概率引擎,short tier)融合后。")
        print("对比这两行的 IC均值/同号折占比/成本调整命中率，可直接看出融合是否带来提升——")
        print("当前 short tier 校准质量约0.05，预期提升幅度很小，这是刻意的安全设计，不是bug。")


def main() -> int:
    ap = argparse.ArgumentParser(description="短线因子滚动样本外验证（walk-forward）")
    ap.add_argument("--symbols", type=str, default="", help="逗号分隔币种，默认用流动性最好的一批")
    ap.add_argument("--forward-bars", type=int, default=12, help="前瞻K线根数（默认12根5m≈1小时，匹配scalp典型持仓时长）")
    ap.add_argument("--fold-bars", type=int, default=288, help="每折K线根数（默认288根≈1天）")
    ap.add_argument("--cost-pct", type=float, default=0.0012, help="假设的单笔往返成本（默认0.12%%：taker手续费×2+保守滑点）")
    ap.add_argument("--min-bars", type=int, default=500, help="参与分析的币种最少K线数")
    ap.add_argument("--output", type=str, default="", help="输出JSON路径")
    ap.add_argument(
        "--with-cycle-prob", action="store_true",
        help="额外对比 纯因子合成信号 vs 因子+cycle_prob(AI周期概率引擎)融合信号 的样本外表现",
    )
    args = ap.parse_args()

    try:
        import psycopg
    except ImportError:
        print("[FATAL] 缺少 psycopg，请在 backend venv 内运行。")
        return 2

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or DEFAULT_SYMBOLS

    dsn = _psycopg_dsn()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            print(f"[INFO] 分析币种候选: {', '.join(symbols)}")
            summaries, meta = run_walk_forward(
                cur, symbols,
                forward_bars=args.forward_bars,
                fold_bars=args.fold_bars,
                cost_pct=args.cost_pct,
                min_bars=args.min_bars,
                with_cycle_prob=args.with_cycle_prob,
            )

    if not meta["symbols_used"]:
        print("[FATAL] 没有任何币种满足最少K线数要求，检查数据库连接或降低 --min-bars。")
        return 1

    feature_names = list(FEATURES) + ([COMPOSITE_ONLY, COMPOSITE_FUSED] if args.with_cycle_prob else [])
    print_report(summaries, meta, args.forward_bars, args.fold_bars, args.cost_pct, feature_names)

    out_dir = ROOT / "data" / "scalp_factor_validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else (out_dir / f"walk_forward_{ts}.json")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeframe": TIMEFRAME,
        "forward_bars": args.forward_bars,
        "fold_bars": args.fold_bars,
        "cost_pct": args.cost_pct,
        "with_cycle_prob": args.with_cycle_prob,
        "meta": meta,
        "features": {
            name: {
                "fold_ics": s.fold_ics,
                "fold_cost_hits": s.fold_cost_hits,
                "total_n": s.total_n,
                "ic_mean": round(s.ic_mean, 4) if s.ic_mean is not None else None,
                "ic_std": round(s.ic_std, 4) if s.ic_std is not None else None,
                "ic_same_sign_ratio": round(s.ic_same_sign_ratio, 4) if s.ic_same_sign_ratio is not None else None,
                "cost_hit_mean": round(s.cost_hit_mean, 4) if s.cost_hit_mean is not None else None,
                "verdict": s.verdict,
            }
            for name, s in summaries.items()
            if name in feature_names
        },
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[SAVED] {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
