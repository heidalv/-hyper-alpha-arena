#!/usr/bin/env python3
"""周期方向识别 —— K线参数敏感度实证分析（只读，不触碰交易主链路）。

目标
----
用 `alpha_market.crypto_klines` 的真实历史 K 线，量化"每个技术参数在不同周期下，
对未来'周期方向'（涨/跌/震荡）的预测价值"，输出一张 `参数 × 周期` 敏感度矩阵，
作为 `docs/CYCLE_DIRECTION_RESEARCH_*.md` 调研报告与 `cycle_direction_probability`
概率引擎特征加权的数据依据。

方法
----
对每个 (symbol, timeframe)：
  1. 从深度最深的交易所拉全量历史 K 线（hyperliquid 优先，其次按行数最多）。
  2. 向量化计算逐根特征：ADX、DI 差、EMA 排列、RSI、MACD 柱、ATR%、量比、动量、价格结构。
  3. 标签 = 未来 N 根 K 线的前瞻收益（N 随周期而变）。
     - 方向标签：涨/跌/震荡（|前瞻收益| < 自适应阈值 = 该周期 |收益| 中位数的一半 → 震荡）。
  4. 逐特征评估：
     - IC：特征值与前瞻收益的 Spearman 秩相关（预测"幅度+方向"）。
     - dir_lift：按特征方向信号切多/空后的方向命中率相对基线的提升（预测"方向"）。
     - MI：特征分桶与三态方向标签的互信息（捕捉非线性可分性）。
  多 symbol 在同一周期下汇池（pool）后统计，减少单币噪声。

输出
----
- `data/cycle_sensitivity/matrix_<YYYYMMDD_HHMMSS>.json`：完整矩阵 + 元数据。
- 控制台：可读的 `参数 × 周期` 敏感度表（按 tier 主周期高亮）。

运行
----
    backend\\.venv\\Scripts\\python.exe scripts\\analyze_cycle_direction_sensitivity.py
    ...\\python.exe scripts\\analyze_cycle_direction_sensitivity.py --symbols BTC,ETH,SOL --timeframes 1h,4h,1d
    ...\\python.exe scripts\\analyze_cycle_direction_sensitivity.py --top-symbols 20

注意
----
- 5m/1m 历史仅约 1 周，样本少、结论置信度低，报告需标注。
- 本脚本为纯读分析，不写交易库、不调用 LLM、不下单。
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

# 复用概率引擎中的"单一权威"向量化指标实现，避免两套 EMA/ADX/RSI 定义漂移。
from backend.services.cycle_direction_probability import (  # noqa: E402
    ema_series as _ema,
    rsi_series as _rsi,
    atr_series as _atr,
    adx_series as _adx,
    hh_hl_series as _hh_hl_score,
)

# ─────────────────── 周期与 tier 配置 ───────────────────

# 三周期 → 主周期（与 backend/config/tier_timeframe_map.py 保持一致）
TIER_PRIMARY = {"short": "15m", "mid": "1h", "long": "4h"}

# 分析所有列（矩阵横轴），从超短到日线
ALL_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]

# 每个周期的"前瞻窗口"（未来多少根 K 线定义该周期方向）
FORWARD_BARS = {
    "1m": 15,   # 15 分钟
    "5m": 12,   # 1 小时
    "15m": 8,   # 2 小时
    "1h": 6,    # 6 小时
    "4h": 6,    # 24 小时
    "1d": 5,    # 5 天
}

# 每个周期用于分析的默认最少 K 线数
MIN_BARS = {"1m": 300, "5m": 300, "15m": 200, "1h": 150, "4h": 120, "1d": 120}

DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK"]

# 参数（矩阵纵轴）
FEATURES = [
    "adx",          # 趋势强度
    "di_diff",      # +DI - -DI（方向性）
    "ema_align",    # EMA9/21/50 排列（-1/0/+1）
    "rsi",          # 相对强弱
    "macd_hist",    # MACD 柱
    "atr_pct",      # 波动率
    "vol_ratio",    # 量比（放量/缩量）
    "mom",          # 近期动量（收益率）
    "hh_hl",        # 价格结构（更高高点/更高低点）
]


# ─────────────────── 数据加载 ───────────────────

def _psycopg_dsn() -> str:
    raw = os.environ.get(
        "MARKET_DATABASE_URL",
        "postgresql://laobao:alpha_pass@localhost:5432/alpha_market",
    )
    # SQLAlchemy 方言前缀 postgresql+psycopg:// → psycopg 可直接吃 postgresql://
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
    """加载单 (symbol, period) 的全量历史，取行数最多的交易所。"""
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


# ─────────────────── 特征与标签 ───────────────────

def build_features(k: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    h, l, c, v = k["high"], k["low"], k["close"], k["volume"]
    adx, pdi, mdi = _adx(h, l, c)
    ema9, ema21, ema50 = _ema(c, 9), _ema(c, 21), _ema(c, 50)
    rsi = _rsi(c, 14)
    atr = _atr(h, l, c, 14)
    ema12, ema26 = _ema(c, 12), _ema(c, 26)
    macd_line = ema12 - ema26
    macd_signal = _ema(np.nan_to_num(macd_line), 9)
    macd_hist = macd_line - macd_signal

    n = len(c)
    ema_align = np.zeros(n)
    for i in range(n):
        if not (math.isnan(ema9[i]) or math.isnan(ema21[i]) or math.isnan(ema50[i])):
            if ema9[i] > ema21[i] > ema50[i]:
                ema_align[i] = 1.0
            elif ema9[i] < ema21[i] < ema50[i]:
                ema_align[i] = -1.0

    di_diff = pdi - mdi
    atr_pct = np.where(c > 0, atr / c, np.nan)

    vol_sma = np.full(n, np.nan)
    for i in range(20, n):
        m = np.mean(v[i - 20:i])
        vol_sma[i] = v[i] / m if m > 0 else np.nan

    mom = np.full(n, np.nan)
    for i in range(10, n):
        mom[i] = (c[i] - c[i - 10]) / c[i - 10] if c[i - 10] > 0 else np.nan

    hh_hl = _hh_hl_score(h, l)

    return {
        "adx": adx,
        "di_diff": di_diff,
        "ema_align": ema_align,
        "rsi": rsi,
        "macd_hist": macd_hist,
        "atr_pct": atr_pct,
        "vol_ratio": vol_sma,
        "mom": mom,
        "hh_hl": hh_hl,
    }


def forward_returns(close: np.ndarray, n_ahead: int) -> np.ndarray:
    """未来 n_ahead 根的前瞻收益率（对齐到当前 bar）。"""
    n = len(close)
    out = np.full(n, np.nan)
    for i in range(n - n_ahead):
        if close[i] > 0:
            out[i] = (close[i + n_ahead] - close[i]) / close[i]
    return out


# ─────────────────── 指标计算 ───────────────────

def _spearman_ic(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman 秩相关。"""
    mask = ~(np.isnan(x) | np.isnan(y))
    if mask.sum() < 30:
        return float("nan")
    xr = _rankdata(x[mask])
    yr = _rankdata(y[mask])
    xr -= xr.mean()
    yr -= yr.mean()
    denom = math.sqrt(np.sum(xr ** 2) * np.sum(yr ** 2))
    if denom < 1e-12:
        return 0.0
    return float(np.sum(xr * yr) / denom)


def _rankdata(a: np.ndarray) -> np.ndarray:
    order = a.argsort()
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(len(a), dtype=float)
    # 处理并列（average rank）——近似即可
    return ranks


def _feature_direction_signal(name: str, val: np.ndarray) -> np.ndarray:
    """把特征值映射为方向信号 +1(看多)/-1(看空)/0(中性)，用于命中率 lift。"""
    sig = np.zeros(len(val))
    if name == "rsi":
        sig = np.where(val > 55, 1.0, np.where(val < 45, -1.0, 0.0))
    elif name in ("di_diff", "macd_hist", "ema_align", "mom", "hh_hl"):
        sig = np.where(val > 0, 1.0, np.where(val < 0, -1.0, 0.0))
    elif name in ("adx", "atr_pct", "vol_ratio"):
        # 强度类特征本身无方向；不产生方向信号（lift 记 NaN）
        sig = np.full(len(val), np.nan)
    sig = np.where(np.isnan(val), np.nan, sig)
    return sig


def _dir_hit_lift(sig: np.ndarray, fwd: np.ndarray) -> Tuple[float, float, int]:
    """方向命中率 lift：signal 非零处，方向与前瞻收益符号一致的比例 - 基线胜率。
    返回 (hit_rate, lift, n_samples)。"""
    mask = ~(np.isnan(sig) | np.isnan(fwd)) & (sig != 0)
    if mask.sum() < 30:
        return float("nan"), float("nan"), int(mask.sum())
    s = sig[mask]
    f = fwd[mask]
    hit = np.mean((np.sign(f) == s).astype(float))
    # 基线：无脑跟随该信号方向 vs 市场整体上涨概率的对称基线 0.5
    baseline = 0.5
    return float(hit), float(hit - baseline), int(mask.sum())


def _mutual_info(x: np.ndarray, label: np.ndarray, bins: int = 5) -> float:
    """特征分桶与三态方向标签(-1/0/1)的互信息（bits）。"""
    mask = ~(np.isnan(x) | np.isnan(label))
    x = x[mask]
    label = label[mask]
    if len(x) < 60:
        return float("nan")
    try:
        qs = np.quantile(x, np.linspace(0, 1, bins + 1))
        qs = np.unique(qs)
        if len(qs) < 3:
            return 0.0
        xb = np.clip(np.digitize(x, qs[1:-1]), 0, len(qs) - 2)
    except Exception:
        return float("nan")
    lab = label.astype(int)
    total = len(x)
    mi = 0.0
    for xv in np.unique(xb):
        px = np.mean(xb == xv)
        for lv in np.unique(lab):
            pl = np.mean(lab == lv)
            pxl = np.mean((xb == xv) & (lab == lv))
            if pxl > 0 and px > 0 and pl > 0:
                mi += pxl * math.log2(pxl / (px * pl))
    return float(mi)


@dataclass
class TFResult:
    timeframe: str
    n_samples: int = 0
    symbols_used: List[str] = field(default_factory=list)
    range_threshold: float = 0.0
    up_rate: float = 0.0
    down_rate: float = 0.0
    range_rate: float = 0.0
    features: Dict[str, Dict[str, float]] = field(default_factory=dict)


def analyze_timeframe(cur, timeframe: str, symbols: List[str], min_bars: int) -> Optional[TFResult]:
    n_ahead = FORWARD_BARS[timeframe]
    pooled_feat: Dict[str, List[np.ndarray]] = {f: [] for f in FEATURES}
    pooled_fwd: List[np.ndarray] = []
    used: List[str] = []

    for sym in symbols:
        k = load_klines(cur, sym, timeframe)
        if not k or len(k["close"]) < min_bars:
            continue
        feats = build_features(k)
        fwd = forward_returns(k["close"], n_ahead)
        # 只保留特征与标签都齐全的区间（跳过 warmup 与末尾 n_ahead）
        for f in FEATURES:
            pooled_feat[f].append(feats[f])
        pooled_fwd.append(fwd)
        used.append(sym)

    if not used:
        return None

    fwd_all = np.concatenate(pooled_fwd)
    feat_all = {f: np.concatenate(pooled_feat[f]) for f in FEATURES}

    valid_fwd = fwd_all[~np.isnan(fwd_all)]
    if len(valid_fwd) < 100:
        return None
    range_thr = 0.5 * float(np.median(np.abs(valid_fwd)))

    # 三态标签
    label = np.full(len(fwd_all), np.nan)
    label[fwd_all > range_thr] = 1.0
    label[fwd_all < -range_thr] = -1.0
    label[(fwd_all >= -range_thr) & (fwd_all <= range_thr)] = 0.0

    res = TFResult(timeframe=timeframe, symbols_used=used, range_threshold=range_thr)
    valid_lab = label[~np.isnan(label)]
    res.n_samples = int(len(valid_lab))
    if res.n_samples:
        res.up_rate = float(np.mean(valid_lab == 1))
        res.down_rate = float(np.mean(valid_lab == -1))
        res.range_rate = float(np.mean(valid_lab == 0))

    for f in FEATURES:
        x = feat_all[f]
        ic = _spearman_ic(x, fwd_all)
        sig = _feature_direction_signal(f, x)
        hit, lift, nsig = _dir_hit_lift(sig, fwd_all)
        mi = _mutual_info(x, label)
        # 综合敏感度得分：|IC| 归一 + |lift|*2 + MI，缺项按 0 计
        score = 0.0
        score += abs(ic) if not math.isnan(ic) else 0.0
        score += (abs(lift) * 2.0) if not math.isnan(lift) else 0.0
        score += mi if not math.isnan(mi) else 0.0
        res.features[f] = {
            "ic": round(ic, 4) if not math.isnan(ic) else None,
            "dir_hit_rate": round(hit, 4) if not math.isnan(hit) else None,
            "dir_lift": round(lift, 4) if not math.isnan(lift) else None,
            "mutual_info": round(mi, 4) if not math.isnan(mi) else None,
            "signal_samples": nsig,
            "sensitivity_score": round(score, 4),
        }
    return res


# ─────────────────── 主流程 ───────────────────

def get_top_symbols(cur, n: int, timeframe: str = "1h") -> List[str]:
    cur.execute(
        "SELECT symbol, count(*) c FROM crypto_klines WHERE period=%s "
        "GROUP BY symbol ORDER BY c DESC LIMIT %s",
        (timeframe, n),
    )
    return [r[0] for r in cur.fetchall()]


def print_matrix(results: Dict[str, TFResult]) -> None:
    tfs = [t for t in ALL_TIMEFRAMES if t in results]
    if not tfs:
        print("无可用结果。")
        return
    tier_by_tf = {v: k for k, v in TIER_PRIMARY.items()}

    print("\n" + "=" * 90)
    print("参数敏感度矩阵（数值 = 综合敏感度得分：|IC| + 2*|命中率lift| + 互信息；越大越敏感）")
    print("=" * 90)
    header = f"{'参数':<12}" + "".join(
        f"{(tf + ('*' + tier_by_tf[tf][0].upper() if tf in tier_by_tf else '')):>12}" for tf in tfs
    )
    print(header)
    print("-" * len(header))
    for f in FEATURES:
        line = f"{f:<12}"
        for tf in tfs:
            fr = results[tf].features.get(f, {})
            s = fr.get("sensitivity_score")
            line += f"{(f'{s:.3f}' if s is not None else '-'):>12}"
        print(line)
    print("-" * len(header))
    print("（* 标记该周期为某 tier 主周期：S=short 15m / M=mid 1h / L=long 4h）")

    print("\n样本与方向分布：")
    for tf in tfs:
        r = results[tf]
        print(f"  {tf:>4}: n={r.n_samples:>7}  币={len(r.symbols_used):>2}  "
              f"涨={r.up_rate:.2%} 跌={r.down_rate:.2%} 震荡={r.range_rate:.2%}  "
              f"range阈值={r.range_threshold:.4%}")


def main() -> int:
    ap = argparse.ArgumentParser(description="周期方向 K线参数敏感度实证分析")
    ap.add_argument("--symbols", type=str, default="", help="逗号分隔币种，如 BTC,ETH,SOL")
    ap.add_argument("--top-symbols", type=int, default=0, help="取行数最多的前 N 个币种")
    ap.add_argument("--timeframes", type=str, default=",".join(ALL_TIMEFRAMES))
    ap.add_argument("--output", type=str, default="", help="输出 JSON 路径（默认 data/cycle_sensitivity/matrix_<ts>.json）")
    args = ap.parse_args()

    try:
        import psycopg
    except ImportError:
        print("[FATAL] 缺少 psycopg，请在 backend venv 内运行。")
        return 2

    dsn = _psycopg_dsn()
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip() in ALL_TIMEFRAMES]

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            if args.symbols:
                symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            elif args.top_symbols > 0:
                symbols = get_top_symbols(cur, args.top_symbols)
            else:
                symbols = DEFAULT_SYMBOLS
            print(f"[INFO] 分析币种({len(symbols)}): {', '.join(symbols)}")
            print(f"[INFO] 周期: {', '.join(timeframes)}")

            results: Dict[str, TFResult] = {}
            for tf in timeframes:
                r = analyze_timeframe(cur, tf, symbols, MIN_BARS.get(tf, 120))
                if r:
                    results[tf] = r
                    print(f"[OK] {tf}: 样本 {r.n_samples}，币 {len(r.symbols_used)}")
                else:
                    print(f"[SKIP] {tf}: 数据不足")

    if not results:
        print("[FATAL] 无任何周期产出结果。")
        return 1

    print_matrix(results)

    # 输出 JSON
    out_dir = ROOT / "data" / "cycle_sensitivity"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else (out_dir / f"matrix_{ts}.json")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tier_primary": TIER_PRIMARY,
        "forward_bars": FORWARD_BARS,
        "features": FEATURES,
        "symbols_requested": symbols,
        "timeframes": {
            tf: {
                "n_samples": r.n_samples,
                "symbols_used": r.symbols_used,
                "range_threshold": r.range_threshold,
                "up_rate": r.up_rate,
                "down_rate": r.down_rate,
                "range_rate": r.range_rate,
                "features": r.features,
            }
            for tf, r in results.items()
        },
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[SAVED] {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
