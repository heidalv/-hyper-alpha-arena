"""
实盘管线离线回放引擎 — LivePipelineBacktestEngine

与 AI 自主交易（Full Auto）使用完全相同的决策管线：
  多周期编排器 → 三维信号确认 → 规则决策引擎

信号逻辑 100% 对齐实盘，仓位管理复用 backtest_evolution_engine 的框架。
进化器优化的参数直接控制实盘行为。
"""
import logging
import math
import os
import threading
import time
import uuid
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from backend.services.backtest_evolution_engine import (
    Bar, Position, TradeRecord, BacktestResult, TIER_CONFIG,
    TAKER_FEE, SLIPPAGE,
)

# 从统一参数注册表导入管线参数
from backend.services.strategy_params_registry import (
    DEFAULT_PIPELINE_PARAMS,
    PIPELINE_PARAM_RANGES,
)

logger = logging.getLogger(__name__)

# [2026-08-23 M0-P2v2] 因子方向序列缓存（进程级，TTL 30min，key=K线窗口指纹）。
# 背景：回测每根 bar 对 30 根窗口跑全因子引擎；factor_engine 的
# _factor_normalizer 是进程级历史缓冲，fitness 随调用历史漂移（跨 eval 不可比）。
# 本缓存在首个回测中按 interval=1 密度预计算整条方向序列并冻结——
# 1) 后续全部 fitness 复用，短线模板每代从 ~50-90min 降至 ~2-3min；
# 2) fitness 确定化：同一窗口内所有 genome 在同一方向序列上比较（对 NSGA-II
#    更公平——旧行为下 genome 间比较被归一化漂移污染）。
# 方向序列只依赖 K 线窗口（与 genome 无关），按 (len,首尾时间戳) 指纹复用。
#
# [2026-09-09 F38] 跨模板复用修复。实测（logs/standalone_weekly_scheduled.log）：
# 每周进化 8 个模板，每个模板开头都触发一次全量预计算（bars≈62k，单次 ≈6.7h），
# 整轮 ~54h，而真正的适应度计算只占约 10min。根因两条：
#   ① TTL=1800s 远小于「模板间隔(≈6.7h)」→ 下一个模板必然 miss；
#   ② strategy_evolver._load_bars 的 cutoff=now-days*86400 随时间滑动 →
#      bars[0].timestamp 漂移 → 缓存键失配，连前缀扩展都命中不了。
# 另修一处潜在串用 bug：旧键 (_ts0, len) 不含 symbol/timeframe，不同币种若
# 首时间戳与长度相同会复用彼此的因子方向序列。
# 现键改为 (symbol, timeframe, ts0, len)；配合 _load_bars 的按天对齐 cutoff
# （见 strategy_evolver._load_bars）与 TTL 提升，每轮预计算次数 8 → 1。
_FACTOR_DIR_CACHE: Dict[tuple, tuple] = {}  # (symbol, timeframe, ts0, n) -> (written_at, series)
_FACTOR_DIR_TTL = int(os.getenv("PIPELINE_FACTOR_DIR_TTL_SEC", str(12 * 3600)))
# [F341 2026-09-18 · B4 phase 2 step 1] 因子归因（因子名级）开关，**默认关**：
# 开启后 `_compute_factor_direction_windowed` 会为每根 bar 旁路保存**全因子值**，
# 内存与耗时随 bars 线性增长（而该函数正跑在预计算循环里，见 §6.4 B4 的 phase 2 规格）。
_FACTOR_ATTR_ENABLED = os.getenv("BACKTEST_FACTOR_ATTR", "0").strip().lower() in (
    "1", "true", "yes", "on")


def attribute_trades_by_factor(trades: List[Any], sidecar: Dict[int, Dict[str, float]]
                               ) -> Dict[str, Dict[str, float]]:
    """[F342 2026-09-18 · B4 phase 2 step 2] 因子名级**覆盖度归因**（可单测的纯函数）。

    ⚠️ 口径声明（重要，避免重犯第 4 路审计指出的错误）：这是**覆盖度归因，不是增量 alpha**——
    它比较"该因子在开仓 bar 上活跃（v>0 / v<0）的那批交易"的盈亏，**没有对照组、无反事实、无加性分解**。
    审计已实证 `SignalFeedbackTracker` 的同类口径会让 1,474 个因子名只产生 238 个不同取值。
    因此：字段名用 `coverage_attr`，不得当作"因子贡献"对外表述；真增量归因需反事实（见报告 §6.4 phase 2 后续）。

    返回 {factor_id: {"n_long","n_short","pnl_long","pnl_short","avg_bp_long","avg_bp_short",
                      "coverage"}}；coverage = 该因子在开仓 bar 上有值（非 0）的交易占比。
    """
    out: Dict[str, Dict[str, float]] = {}
    total = 0
    for t in trades or []:
        bar_i = int(getattr(t, "entry_bar", -1) or -1)
        vals = sidecar.get(bar_i) if isinstance(sidecar, dict) else None
        if not vals:
            continue
        total += 1
        pnl = float(getattr(t, "pnl", 0.0) or 0.0)
        notional = abs(float(getattr(t, "quantity", 0.0) or 0.0)
                       * float(getattr(t, "entry_price", 0.0) or 0.0))
        for fid, v in vals.items():
            try:
                v = float(v)
            except Exception:
                continue
            b = out.setdefault(str(fid), {"n_long": 0, "n_short": 0, "pnl_long": 0.0,
                                          "pnl_short": 0.0, "bp_long": 0.0, "bp_short": 0.0,
                                          "coverage": 0.0})
            if v > 0:
                b["n_long"] += 1
                b["pnl_long"] += pnl
                b["bp_long"] += (pnl / notional * 1e4) if notional > 0 else 0.0
            elif v < 0:
                b["n_short"] += 1
                b["pnl_short"] += pnl
                b["bp_short"] += (pnl / notional * 1e4) if notional > 0 else 0.0
    for fid, b in out.items():
        n_l, n_s = int(b["n_long"]), int(b["n_short"])
        b["avg_bp_long"] = round(b.pop("bp_long") / n_l, 4) if n_l else 0.0
        b["avg_bp_short"] = round(b.pop("bp_short") / n_s, 4) if n_s else 0.0
        b["pnl_long"] = round(b["pnl_long"], 8)
        b["pnl_short"] = round(b["pnl_short"], 8)
        b["coverage"] = round((n_l + n_s) / total, 4) if total else 0.0
    return out


# ── [F343 2026-09-18 · B4 phase 2 step 3] 归因结果落盘（append-only JSONL，免 schema 迁移） ──
# 为什么用 JSONL 而不是建表：本仓库对"append-only 证据链"的既有纪律就是 JSONL
# （`data/mm_trials.jsonl`、`factors_lab/rounds_index.jsonl`、`data/promotion_gate_decisions.jsonl`），
# 免迁移即可落地、可 diff、可回溯；等 phase 2 稳定后再考虑入库（报告 §6.4）。
_FACTOR_ATTR_PATH_DEFAULT = "data/backtest_factor_attr.jsonl"


def factor_attr_path() -> str:
    return os.getenv("BACKTEST_FACTOR_ATTR_PATH", _FACTOR_ATTR_PATH_DEFAULT)


def persist_factor_attr(*, run_id: str, symbol: str = "", tier: str = "",
                        by_dir: Optional[Dict[str, Dict]] = None,
                        by_name: Optional[Dict[str, Dict]] = None,
                        extra: Optional[Dict] = None) -> Optional[str]:
    """把一次回测的因子归因追加到 JSONL（返回写入路径；失败返回 None，不影响回测）。

    ⚠️ 口径：`by_name` 是**覆盖度归因**（见 `attribute_trades_by_factor` docstring），不是增量 alpha。
    """
    try:
        import json as _json
        import time as _time

        path = factor_attr_path()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        rec = {"run_id": str(run_id), "symbol": str(symbol or ""), "tier": str(tier or ""),
               "ts": _time.time(), "by_dir": by_dir or {}, "by_name": by_name or {},
               "attr_note": "by_name=coverage attribution (NOT incremental alpha)"}
        if extra:
            rec.update(extra)
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return path
    except Exception as e:      # 落盘失败不得影响回测本身
        logger.warning("[PipelineBT] 因子归因落盘失败(忽略): %s", e)
        return None


def load_factor_attr(run_id: Optional[str] = None, limit: int = 50) -> List[Dict]:
    """读回归因记录（按 run_id 过滤；返回最近 limit 条，时间升序）。只读、异常返回空。"""
    try:
        import json as _json

        path = factor_attr_path()
        if not os.path.exists(path):
            return []
        out: List[Dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                except Exception:
                    continue
                if run_id and str(rec.get("run_id")) != str(run_id):
                    continue
                out.append(rec)
        return out[-max(1, int(limit)):]
    except Exception:
        return []


# ═══════════ [2026-09-11 F38e] 因子方向序列磁盘缓存 ═══════════
# 背景：单币全量预计算 ~6.7h（bars≈62k），每周进化 8 模板 ≈54h，而适应度
# 计算仅 ~10min —— 周进化永远跑不完（9/9 实测 8h 才 40%）。内存缓存随进程
# 死亡清空，每周都是全新 54h。本磁盘缓存按 (symbol, timeframe) 存整条
# 方向序列（锚定最早 K 线），任何后续窗口只要首时间戳命中序列即可按后缀
# 复用（窗口计算只回看 30 根，j≥30 的值与窗口起点无关）。
# 配合 scripts/warm_factor_dir_cache.py 多进程预热，周一进化从 54h 降到分钟级。
import json as _json
from bisect import bisect_left as _bisect_left
from pathlib import Path as _Path

_FACTOR_DIR_DISK_DIR = _Path(__file__).resolve().parents[2] / "data" / "factor_dir_cache"
_FACTOR_DIR_DISK_ENABLED = os.getenv(
    "PIPELINE_FACTOR_DIR_DISK_ENABLED", "true"
).strip().lower() not in ("0", "false", "no", "off")
_FACTOR_DIR_DISK_TTL = int(os.getenv("PIPELINE_FACTOR_DIR_DISK_TTL_SEC", str(7 * 24 * 3600)))


def _factor_dir_mode_tag() -> str:
    """缓存按因子集模式分桶：FACTOR_LIVE_ALLOWLIST_ONLY=true（受治理 3-4 因子）
    与 false（全量 191 因子）的计算结果不同，混用会污染进化 fitness。"""
    _v = os.getenv("FACTOR_LIVE_ALLOWLIST_ONLY", "true").strip().lower()
    return "gov" if _v in ("1", "true", "yes", "on") else "full"


#: [F359 2026-09-18] 方向序列的**语义版本**。凡改动方向计算/因子集合口径，必须 +1。
#:
#: 为什么必须有：缓存键原为 `(symbol, timeframe, ts0, n)` + `gov|full`，**不含任何
#: 计算语义标识**。实测后果（本次）：我在 F349 重构中途有一个版本把 `FactorValue`
#: 对象误降级成 float 再喂 `generate_signals`，那一版跑出的方向序列被**写进磁盘缓存
#: （10:46）**；改回正确实现后，缓存命中的回测仍复用那份**旧语义**序列
#: ⇒ 同一个命令（BTC 4h 30d mid）在不同时刻给出不同的 `factor_dir_at_entry`
#: 分桶（`1:6/-1:2/0:2` vs `1:5/-1:2/0:3`），且**没有任何告警**。
#: 版本号进入文件名与文件内容双重校验 ⇒ 语义变更后旧缓存一律作废重算。
_FACTOR_DIR_SERIES_VERSION = "v2-20260918"


_DIR_CACHE_VER_WARNED: set = set()
_DIR_CACHE_VER_GUARD = threading.Lock()


def _log_dir_cache_version_mismatch_once(p, cached_ver: str) -> None:
    """旧的/异版本的缓存被忽略时留一条日志（不删文件，便于排查）。"""
    key = str(p)
    with _DIR_CACHE_VER_GUARD:
        if key in _DIR_CACHE_VER_WARNED:
            return
        _DIR_CACHE_VER_WARNED.add(key)
    logger.warning(
        "[PipelineBT] 因子方向磁盘缓存语义版本不匹配，已忽略并重算: %s（缓存 ver=%r，当前 ver=%r）",
        p.name, cached_ver or "<无>", _FACTOR_DIR_SERIES_VERSION,
    )


def _factor_dir_disk_path(sym: str, tf: str) -> _Path:
    return _FACTOR_DIR_DISK_DIR / f"{sym}_{tf}_{_factor_dir_mode_tag()}_{_FACTOR_DIR_SERIES_VERSION}.json"


def _factor_dir_disk_load(sym: str, tf: str):
    """读磁盘缓存（未过期），返回 (tss, series) 或 None。

    [F359] 版本双重校验：文件名带版本，文件**内容**也带版本；
    两者任一不匹配即视为缓存作废（旧文件不删，便于排查）。
    """
    if not _FACTOR_DIR_DISK_ENABLED:
        return None
    try:
        p = _factor_dir_disk_path(sym, tf)
        if not p.exists():
            return None
        if time.time() - p.stat().st_mtime > _FACTOR_DIR_DISK_TTL:
            return None
        data = _json.loads(p.read_text(encoding="utf-8"))
        cached_ver = str(data.get("ver") or "")
        if cached_ver != _FACTOR_DIR_SERIES_VERSION:
            _log_dir_cache_version_mismatch_once(p, cached_ver)
            return None
        tss = data.get("tss") or []
        series = data.get("series") or []
        if len(tss) == len(series) and tss:
            return tss, series
    except Exception as e:
        logger.debug("[PipelineBT] 因子方向磁盘缓存读取失败(fail-open): %s", e)
    return None


def _factor_dir_disk_save(sym: str, tf: str, tss, series) -> None:
    """写磁盘缓存（新锚定序列或与既有序列按时间戳合并扩展）。"""
    if not _FACTOR_DIR_DISK_ENABLED:
        return
    # [2026-09-11 修复] pytest 进程不得写生产缓存：全量单测中某个用例用假 bar
    # 调 engine.run() → 磁盘合并逻辑被假锚点触发整文件重写 → 真实预热缓存
    # （BTC/ETH 1h/4h 共 4 个文件）被 1.7KB 假数据覆盖（21:48:49 实测）。
    # 与 midlong_direction_audit._skip_write_under_pytest（§82）同款判据；
    # 测试需要验证磁盘缓存的用例显式重定向 _FACTOR_DIR_DISK_DIR 到临时目录
    # （test_factor_dir_*_20260911 已如此）。
    if os.getenv("PYTEST_CURRENT_TEST") and not os.getenv("FACTOR_DIR_DISK_ALLOW_PYTEST"):
        return
    try:
        p = _factor_dir_disk_path(sym, tf)
        _FACTOR_DIR_DISK_DIR.mkdir(parents=True, exist_ok=True)
        if p.exists():
            try:
                old = _json.loads(p.read_text(encoding="utf-8"))
                old_tss = old.get("tss") or []
                old_series = old.get("series") or []
                if old_tss and old_tss[0] <= tss[0] and len(old_tss) == len(old_series):
                    # 既有序列锚点更早：把新值按时间戳对齐合并回旧序列
                    k = _bisect_left(old_tss, tss[0])
                    if k < len(old_tss) and old_tss[k] == tss[0]:
                        for j, ts in enumerate(tss):
                            idx = k + j
                            if idx < len(old_series):
                                old_series[idx] = series[j]
                            else:
                                old_series.append(series[j])
                                old_tss.append(ts)
                        tss, series = old_tss, old_series
            except Exception:
                pass
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            # [F359] 写入时带语义版本：改名/换实现后旧文件想被复用也会被内容校验拦下
            _json.dumps({"ver": _FACTOR_DIR_SERIES_VERSION, "tss": tss, "series": series,
                         "updated": time.time()},
                        separators=(",", ":")),
            encoding="utf-8",
        )
        tmp.replace(p)
    except Exception as e:
        logger.debug("[PipelineBT] 因子方向磁盘缓存写入失败(不影响回测): %s", e)


# ═══════════════════ 默认管线参数（从注册表导入） ═══════════════════

# Legacy: TIER_RISK_DEFAULTS 保留空字典以兼容旧 import
TIER_RISK_DEFAULTS: Dict[str, Dict[str, float]] = {}


# ═══════════════════ 纯函数：实盘管线离线版 ═══════════════════

def _calc_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """与实盘 RSI 相同的计算"""
    n = len(closes)
    rsi = np.full(n, 50.0)
    if n < period + 1:
        return rsi
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100.0 - 100.0 / (1.0 + rs)
    return rsi


def _calc_macd(closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """与实盘 MACD 相同的计算"""
    def ema(data, span):
        out = np.zeros_like(data)
        out[0] = data[0]
        k = 2.0 / (span + 1)
        for i in range(1, len(data)):
            out[i] = data[i] * k + out[i - 1] * (1 - k)
        return out
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line


def replay_mid_signal(rsi: float, macd: float, p: Dict) -> tuple:
    """回放编排器中期信号 → (bias, confidence)"""
    if rsi > p["mid_rsi_bull"] and macd > 0:
        return "bullish", min(p["mid_conf_strong"], (rsi - 45) / 40 + abs(macd) * 8)
    elif rsi < p["mid_rsi_bear"] and macd < 0:
        return "bearish", min(p["mid_conf_strong"], (55 - rsi) / 40 + abs(macd) * 8)
    elif rsi > p["mid_rsi_weak_bull"] and macd > 0:
        return "bullish", p["mid_conf_weak"]
    elif rsi < p["mid_rsi_weak_bear"] and macd < 0:
        return "bearish", p["mid_conf_weak"]
    return "neutral", p["mid_conf_neutral"]


def replay_long_signal(fgi: float, intel_dir: str, intel_conf_pct: float, p: Dict) -> tuple:
    """回放编排器长期信号 → (bias, confidence)"""
    if fgi < p["long_fgi_extreme_fear"]:
        return "bearish", 0.5
    elif fgi > p["long_fgi_extreme_greed"]:
        return "bullish", 0.5
    elif fgi < p["long_fgi_fear"]:
        return "bearish", 0.35
    elif fgi > p["long_fgi_greed"]:
        return "bullish", 0.35
    elif intel_dir in ("bullish", "bearish") and intel_conf_pct > p["long_intel_min_conf"]:
        return intel_dir, 0.3
    return "neutral", 0.05


def replay_short_signal(whale_dir: float, funding_signal: str, p: Dict) -> tuple:
    """回放编排器短期信号 → (bias, confidence)"""
    wt = p["short_whale_threshold"]
    if whale_dir > wt and funding_signal != "bearish":
        return "bullish", 0.3
    elif whale_dir < -wt and funding_signal != "bullish":
        return "bearish", 0.3
    return "neutral", 0.0


def replay_intel_fusion(mid_bias: str, mid_conf: float,
                        intel_dir: str, intel_conf_pct: float, p: Dict) -> tuple:
    """情报信号对中期的融合修正 → (bias, confidence)"""
    intel_conf = intel_conf_pct / 100.0
    if intel_dir in ("bullish", "bearish") and intel_conf > p["intel_fusion_min_conf"]:
        if mid_bias == "neutral":
            return intel_dir, max(mid_conf, p["intel_fusion_neutral_boost"] + intel_conf * 0.5)
        elif mid_bias == intel_dir:
            return mid_bias, min(1.0, mid_conf + p["intel_fusion_agree_boost"] + intel_conf * 0.3)
        else:
            return mid_bias, mid_conf * p["intel_fusion_conflict_mult"]
    return mid_bias, mid_conf


def replay_finalize(long_bias: str, long_conf: float,
                    mid_bias: str, mid_conf: float,
                    short_bias: str, short_conf: float,
                    p: Dict) -> tuple:
    """回放 _finalize → (action, side, position_pct)
    action: "enter" or "wait"
    """
    # 方向判定
    final_side = ""
    if short_bias == "bullish":
        final_side = "long"
    elif short_bias == "bearish":
        final_side = "short"
    elif mid_bias == "bullish" and mid_conf >= p["finalize_mid_fallback_conf"]:
        final_side = "long"
    elif mid_bias == "bearish" and mid_conf >= p["finalize_mid_fallback_conf"]:
        final_side = "short"
    elif long_bias == "bullish" and long_conf >= p["finalize_long_fallback_conf"]:
        final_side = "long"
    elif long_bias == "bearish" and long_conf >= p["finalize_long_fallback_conf"]:
        final_side = "short"

    if not final_side:
        return "wait", "", 0.0

    # 置信度
    weighted_conf = (
        long_conf * p["finalize_long_weight"]
        + mid_conf * p["finalize_mid_weight"]
        + short_conf * p["finalize_short_weight"]
    )
    active_confs = []
    for bias, conf in [(long_bias, long_conf), (mid_bias, mid_conf), (short_bias, short_conf)]:
        if bias != "neutral" and conf > 0:
            active_confs.append(conf)
    max_active = max(active_confs) if active_confs else 0
    ratio = p["finalize_max_active_ratio"]
    avg_conf = max_active * ratio + weighted_conf * (1 - ratio)

    if avg_conf < p["finalize_min_conf"]:
        return "wait", "", 0.0

    pos_pct = p["max_position_size"] * avg_conf
    pos_pct = max(0.02, min(0.5, pos_pct))
    return "enter", final_side, pos_pct


def replay_confirmation(tech_dir: int, flow_dir: int, sent_dir: int,
                        min_dims: int = 2) -> tuple:
    """回放三维信号确认 → (action, direction, level)
    tech_dir/flow_dir/sent_dir: +1 看多, -1 看空, 0 中性
    """
    non_zero = [(d, 1.0) for d in [tech_dir, flow_dir, sent_dir] if d != 0]
    if len(non_zero) < min_dims:
        return "HOLD", 0, "none"
    directions = [d for d, _ in non_zero]
    if not all(d == directions[0] for d in directions):
        return "HOLD", 0, "none"
    confirmed = directions[0]
    level = "strong" if len(non_zero) == 3 else "normal"
    action = "BUY" if confirmed > 0 else "SELL"
    return action, confirmed, level


def replay_rule_decision(confirm_action: str, confirm_dir: int,
                         mid_bias: str, mid_conf: float) -> str:
    """回放规则引擎覆盖 → "buy" / "sell" / "hold"
    简化版：三维确认通过则用确认结果，否则回退到编排器中期
    """
    if confirm_action in ("BUY", "SELL"):
        return confirm_action.lower()
    # 三维确认为 HOLD 时，回退到编排器中期方向（弱化仓位）
    if mid_bias == "bullish" and mid_conf >= 0.2:
        return "buy"
    elif mid_bias == "bearish" and mid_conf >= 0.2:
        return "sell"
    return "hold"


def factor_dimension_inert(p: Dict, funding_rates: Optional[Dict] = None,
                           fgi_map: Optional[Dict] = None) -> bool:
    """[F358 2026-09-18] 因子维度是否**结构性失效**（纯函数，便于单测）。

    机制（`_pipeline_signal`）：因子方向唯一的作用路径是影响 `tech_dir`，而 `tech_dir`
    要真正决定方向，必须先过 `replay_confirmation(tech_dir, flow_dir, sent_dir, min_dims)`：
    **至少 `confirmation_min_dims` 个维度非零且同向**。其中：
      - `tech_dir` 由 RSI/MACD 决定（K 线存在即可有值）；
      - `flow_dir` 由资金费率决定 ⇒ 不传 `funding_rate_series` 时**恒为 0**；
      - `sent_dir` 由恐贪指数决定 ⇒ 不传 `fgi_series` 时**恒为 0**。

    因此当 `factor_signal_weight>0`（因子通道开着、甚至已付出预计算代价）而
    funding/fgi 都缺时，非因子维度最多只有 tech 一个 ⇒ 永远凑不齐 `min_dims`
    ⇒ 确认恒为 HOLD ⇒ 方向退化为 `mid_bias` ⇒ **因子无论怎么变都不影响任何决策**。

    这正是 F355 的"假零"与 F356 的"看起来通道死了"的共同根因；
    此前它**完全静默**（`data_dims_used` 只被汇总成一个计数，明细被丢弃）。
    """
    try:
        if float(p.get("factor_signal_weight", 0.3) or 0) <= 0:
            return False        # 本来就关着，不算"失效"
        _min_dims = int(p.get("confirmation_min_dims", 2) or 2)
        non_factor_dims = 1 + int(bool(funding_rates)) + int(bool(fgi_map))
        return non_factor_dims < _min_dims
    except Exception:
        return False


_FACTOR_INERT_WARNED: set = set()
_FACTOR_INERT_GUARD = threading.Lock()


def _warn_factor_inert_once(symbol: str, timeframe: str, tier: str) -> None:
    """同一 (symbol, timeframe) 只告警一次，避免进化循环刷屏。"""
    key = (str(symbol or "-"), str(timeframe or "-"))
    with _FACTOR_INERT_GUARD:
        if key in _FACTOR_INERT_WARNED:
            return
        _FACTOR_INERT_WARNED.add(key)
    logger.warning(
        "[PipelineBT] 因子维度**结构性失效**（symbol=%s tf=%s tier=%s）："
        "factor_signal_weight>0 但未传 funding_rate_series/fgi_series ⇒ flow_dir=sent_dir=0 "
        "⇒ replay_confirmation 永远凑不齐 min_dims ⇒ 因子方向无法影响任何决策（结果会与"
        "factor_signal_weight=0 逐位相同）。要让因子真正参与，请传这两条序列。",
        symbol or "-", timeframe or "-", tier,
    )


# ═══════════════════ 引擎主体 ═══════════════════


class LivePipelineBacktestEngine:
    """用实盘同款管线在历史数据上回放"""

    def __init__(self, initial_capital: float = 10000.0):
        self.initial_capital = initial_capital

    def run(
        self,
        bars: List[Bar],
        pipeline_params: Dict[str, Any],
        run_id: Optional[str] = None,
        progress_callback=None,
        tier: str = "mid",
        funding_rate_series: Optional[Dict[int, float]] = None,
        fgi_series: Optional[Dict[int, float]] = None,
        symbol: str = "",
        timeframe: str = "",
    ) -> BacktestResult:
        """
        主回测循环 — 逐 bar 调用实盘同款决策管线

        Args:
            bars: K线序列
            pipeline_params: 管线参数（编排器阈值+情报权重+风控）
            funding_rate_series: {timestamp: rate} 历史资金费率
            fgi_series: {timestamp: fgi_value} 历史恐贪指数
            symbol / timeframe: [F38] 因子方向序列缓存的键维度。
                不传则退化为仅按窗口指纹复用（旧行为），建议调用方一律传。
        """
        run_id = run_id or f"lp_{uuid.uuid4().hex[:10]}"
        result = BacktestResult(run_id=run_id, bars_total=len(bars))
        result._bars_ref = bars
        t0 = time.time()

        if len(bars) < 50:
            result.error = "K线数据不足（需要至少50根）"
            return result

        p = {**DEFAULT_PIPELINE_PARAMS}
        p.update(pipeline_params)

        # 风控参数
        sl_pct = p["stop_loss_pct"]
        tp_pct = p["take_profit_pct"]
        max_pos_pct = p["max_position_size"]
        trailing_act = p["trailing_activation_pct"]
        trailing_dist = p["trailing_distance_pct"]
        be_activation = p.get("breakeven_activation_pct", 0.01)
        be_buffer = p.get("breakeven_buffer_pct", 0.002)
        lev = p["default_leverage"]

        tier_cfg = TIER_CONFIG.get(tier, TIER_CONFIG["mid"])
        max_holding = tier_cfg["max_holding_bars"]
        daily_loss_limit = p["max_daily_loss"]

        funding_rates = funding_rate_series or {}
        fgi_map = fgi_series or {}

        # 预计算指标
        closes = np.array([b.c for b in bars], dtype=np.float64)
        rsi_arr = _calc_rsi(closes, 14)
        macd_line, _ = _calc_macd(closes)

        warmup = 30
        min_bars_gap = 3

        # 状态
        equity = self.initial_capital
        peak_equity = equity
        position: Optional[Position] = None
        trades: List[TradeRecord] = []
        equity_curve = [equity]
        last_exit_bar = -min_bars_gap
        day_equity_start = equity
        last_day_ts = bars[0].timestamp if bars else 0
        circuit_breaker_until = 0
        total_funding_fees = 0.0

        # 资金费率间隔估算
        if len(bars) > 1:
            bar_interval = bars[1].timestamp - bars[0].timestamp
            bars_per_8h = max(1, int(28800 / max(bar_interval, 1)))
        else:
            bars_per_8h = 8

        data_dims_used = {"rsi_macd": True, "funding": bool(funding_rates), "fgi": bool(fgi_map), "factor_signal": float(p.get("factor_signal_weight", 0.3)) > 0}
        # [F358] 明细必须随结果带出去：此前 `data_dims_used` 只被 `sum(...)` 成
        # `data_completeness` 一个计数（:884），"funding/fgi 缺失导致因子维度结构性失效"
        # 这一关键事实被丢掉 ⇒ 调用方无法分辨"因子算了但没用"与"因子真的没用"。
        result.data_dims_used = dict(data_dims_used)
        if factor_dimension_inert(p, funding_rates, fgi_map):
            _warn_factor_inert_once(symbol, timeframe, tier)

        # [M0-P2v2] 预取/预计算因子方向序列：整轮 NSGA-II 数千次 fitness 共用
        # 同一批 K 线窗口。方向只依赖 K 线（与 genome 无关），一次性算好冻结，
        # 后续查表（见模块级注释：性能 + fitness 确定化）。
        # [2026-08-23 M0-P2v3] 前缀扩展：bars 窗口随数据更新向右滑动（尾部新增
        # K 线、首时间戳不变），此前按 (len,首尾ts) 精确指纹命中失败会触发整段
        # 重算——预计算耗时（生产 ~2h）> bars 缓存 TTL（30min）时，每轮评估都
        # 重载出新窗口 → 新指纹 → 无限重算（实测整轮进化卡死于此）。
        # 现按首时间戳锚定：同前缀的缓存序列只补算新增尾部窗口。
        self._factor_dir_series = None
        if float(p.get("factor_signal_weight", 0.3)) > 0 and len(bars) > warmup:
            _ts0 = int(bars[0].timestamp)
            # [F38] 键含 symbol/timeframe，避免不同币种窗口指纹相同时串用方向序列
            _sym = str(symbol or "").upper()
            _tf = str(timeframe or "")
            _best_ln, _best_series = -1, None
            for _k, (_kt, _ks) in list(_FACTOR_DIR_CACHE.items()):
                if not isinstance(_k, tuple) or len(_k) != 4:
                    continue  # 兼容旧格式键（仅存在于旧进程内存）
                _k_sym, _k_tf, _k0, _kln = _k
                if _k_sym != _sym or _k_tf != _tf:
                    continue
                if _k0 == _ts0 and _kln <= len(bars) and _kln > _best_ln and (time.time() - _kt) <= _FACTOR_DIR_TTL:
                    _best_ln, _best_series = _kln, _ks
            if _best_series is not None:
                _series = list(_best_series)
                _start_i = max(len(_series), warmup)
                if _start_i < len(bars):
                    logger.info(
                        "[PipelineBT] 因子方向序列前缀扩展 +%d 窗口 (cached=%d, bars=%d, sym=%s, tf=%s)",
                        len(bars) - _start_i, len(_series), len(bars), _sym or "-", _tf or "-",
                    )
                    for _i in range(_start_i, len(bars)):
                        # [M0-P2v4] 同款 GIL 让渡（见全量预计算循环）。
                        if _i % 32 == 0:
                            time.sleep(0)
                        _series.append(self._compute_factor_direction_windowed(_i, bars))
                self._factor_dir_series = _series
                _FACTOR_DIR_CACHE[(_sym, _tf, _ts0, len(bars))] = (time.time(), _series)
            else:
                # [F38e 2026-09-11] 内存 miss → 磁盘缓存（锚定最早 K 线的整条序列，
                # 按首时间戳后缀复用）。命中则只需补算 warmup 内的窗口与新增尾部，
                # 单模板预计算从 ~6.7h 降到分钟级。
                _series = None
                _disk_tss = None
                _disk_off = 0
                _disk_hit = _factor_dir_disk_load(_sym, _tf)
                if _disk_hit is not None:
                    _disk_tss, _disk_series = _disk_hit
                    _off = _bisect_left(_disk_tss, _ts0)
                    if _off < len(_disk_tss) and int(_disk_tss[_off]) == _ts0:
                        _disk_off = _off
                        _covered = len(_disk_series) - _off
                        _series = list(_disk_series[_off:_off + len(bars)])
                        if _covered < len(bars):
                            _series.extend([None] * (len(bars) - _covered))
                        logger.info(
                            "[PipelineBT] 因子方向序列磁盘缓存命中 bars=%d sym=%s tf=%s "
                            "(offset=%d, covered=%d/%d, 需补算=%d)",
                            len(bars), _sym or "-", _tf or "-",
                            _disk_off, _covered, len(bars),
                            max(0, len(bars) - _covered),
                        )
                if _series is None:
                    logger.info(
                        "[PipelineBT] 预计算因子方向序列 bars=%d sym=%s tf=%s（一次性，之后复用/前缀扩展）",
                        len(bars), _sym or "-", _tf or "-",
                    )
                    _series = [0] * warmup + [None] * (len(bars) - warmup)
                    _compute_from = warmup
                else:
                    # 磁盘复用：warmup 内窗口被截断，需要重算；warmup 之后且已覆盖
                    # 的位置直接复用（窗口计算只回看 30 根，j≥30 与窗口起点无关）。
                    # 默认全复用（_compute_from=len(bars) → 计算循环空转），
                    # 只有找到第一个未覆盖位置（None/超出磁盘序列）才从那里补算。
                    _compute_from = len(bars)
                    for _j in range(warmup, len(bars)):
                        if _j >= len(_series) or _series[_j] is None:
                            _compute_from = _j
                            break
                    if len(_series) < len(bars):
                        _series.extend([None] * (len(bars) - len(_series)))
                for _i in range(_compute_from, len(bars)):
                    if (_i - warmup) % 5000 == 0:
                        logger.info(
                            "[PipelineBT] 预计算进度 %d/%d (%.0f%%)",
                            _i - warmup, len(bars) - warmup,
                            100.0 * (_i - warmup) / max(len(bars) - warmup, 1),
                        )
                    # [2026-08-23 M0-P2v4] 定期让渡 GIL：预计算是长达数十分钟的
                    # 纯 Python 循环，不让渡会饿死同进程的 HTTP 健康探测
                    # （/api/health 超时）→ 外部进程监管（DSH web）误判宕机并
                    # 反复重启后端，整轮进化随进程死亡。与主循环每 32 根 bar
                    # 让渡同款模式（实测开销 <1%）。
                    if _i % 32 == 0:
                        time.sleep(0)
                    _series[_i] = self._compute_factor_direction_windowed(_i, bars)
                # [F38e] 计算完成后写磁盘缓存（合并扩展），下次任何进程可直接复用。
                try:
                    _bar_tss = [int(b.timestamp) for b in bars]
                    _factor_dir_disk_save(_sym, _tf, _bar_tss, _series)
                except Exception as _disk_err:
                    logger.debug("[PipelineBT] 磁盘缓存保存跳过: %s", _disk_err)
                _FACTOR_DIR_CACHE[(_sym, _tf, _ts0, len(bars))] = (time.time(), _series)
                self._factor_dir_series = _series

        for i in range(warmup, len(bars)):
            bar = bars[i]
            # [perf 2026-08-18] 回测风暴期间向 API 请求线程让渡 GIL：
            # 进化/晋升会在后台连续跑数千根 bar 的纯 Python 循环把 GIL 占死，
            # HTTP 请求线程排队 3~26s。每 32 根 bar 让一次（sleep(0) 只释放
            # 一个调度窗口，回测总耗时影响 <0.5%）。
            if i % 32 == 0:
                time.sleep(0)

            # 跨日重置
            if bar.timestamp - last_day_ts >= 86400:
                day_equity_start = equity
                last_day_ts = bar.timestamp
                if circuit_breaker_until and bar.timestamp >= circuit_breaker_until:
                    circuit_breaker_until = 0

            # 熔断期
            if circuit_breaker_until and bar.timestamp < circuit_breaker_until:
                if position:
                    equity, trade = self._close_position(position, bar, equity, "circuit_breaker")
                    trades.append(trade)
                    position = None
                    last_exit_bar = i
                equity_curve.append(equity)
                continue

            # 持仓管理
            if position:
                # 资金费率（[P0-5 相位修复] 按 8h UTC 边界 00/08/16 结算：跨边界即结算一次。
                # 原 i % bars_per_8h 相对序列起点对齐，结算相位与交易所真实时刻脱钩。）
                _prev_ts = bars[i - 1].timestamp if i > 0 else bar.timestamp
                if (bar.timestamp // 28800) != (_prev_ts // 28800):
                    fr = self._get_funding_rate(bar.timestamp, funding_rates)
                    fee_usd = position.quantity * bar.c * fr
                    if position.side == "long":
                        equity -= fee_usd
                    else:
                        equity += fee_usd
                    total_funding_fees += abs(fee_usd)

                # 极值追踪
                position.highest_since_entry = max(position.highest_since_entry, bar.h)
                position.lowest_since_entry = min(position.lowest_since_entry, bar.l)

                # 超时平仓
                if (i - position.entry_bar) >= max_holding:
                    equity, trade = self._close_position(position, bar, equity, "timeout")
                    trades.append(trade)
                    position = None
                    last_exit_bar = i
                    equity_curve.append(equity)
                    continue

                # 止损止盈
                closed = False
                if position.side == "long":
                    if bar.l <= position.sl_price:
                        equity, trade = self._close_position(position, bar, equity, "stop_loss", position.sl_price)
                        trades.append(trade)
                        position = None
                        last_exit_bar = i
                        closed = True
                    elif bar.h >= position.tp_price:
                        equity, trade = self._close_position(position, bar, equity, "take_profit", position.tp_price)
                        trades.append(trade)
                        position = None
                        last_exit_bar = i
                        closed = True
                else:
                    if bar.h >= position.sl_price:
                        equity, trade = self._close_position(position, bar, equity, "stop_loss", position.sl_price)
                        trades.append(trade)
                        position = None
                        last_exit_bar = i
                        closed = True
                    elif bar.l <= position.tp_price:
                        equity, trade = self._close_position(position, bar, equity, "take_profit", position.tp_price)
                        trades.append(trade)
                        position = None
                        last_exit_bar = i
                        closed = True

                # 保本止损 + 移动止损（与实盘 paper_trading_engine 对齐）
                if position and not closed:
                    if position.side == "long":
                        profit_pct = (bar.c - position.entry_price) / position.entry_price
                        # 保本止损推进
                        if profit_pct >= be_activation:
                            be_sl = position.entry_price * (1 + be_buffer)
                            if position.sl_price < be_sl:
                                position.sl_price = be_sl
                        # 追踪止损
                        if profit_pct >= trailing_act:
                            position.trailing_activated = True
                        if position.trailing_activated:
                            new_trail = bar.c * (1 - trailing_dist)
                            position.trailing_price = max(position.trailing_price, new_trail)
                            if bar.l <= position.trailing_price:
                                equity, trade = self._close_position(position, bar, equity, "trailing_stop", position.trailing_price)
                                trades.append(trade)
                                position = None
                                last_exit_bar = i
                    else:
                        profit_pct = (position.entry_price - bar.c) / position.entry_price
                        # 保本止损推进
                        if profit_pct >= be_activation:
                            be_sl = position.entry_price * (1 - be_buffer)
                            if position.sl_price > be_sl or position.sl_price == 0:
                                position.sl_price = be_sl
                        # 追踪止损
                        if profit_pct >= trailing_act:
                            position.trailing_activated = True
                        if position.trailing_activated:
                            new_trail = bar.c * (1 + trailing_dist)
                            if position.trailing_price == 0:
                                position.trailing_price = new_trail
                            else:
                                position.trailing_price = min(position.trailing_price, new_trail)
                            if bar.h >= position.trailing_price:
                                equity, trade = self._close_position(position, bar, equity, "trailing_stop", position.trailing_price)
                                trades.append(trade)
                                position = None
                                last_exit_bar = i

                equity_curve.append(self._mark_equity(equity, position, bar))
                peak_equity = max(peak_equity, equity_curve[-1])
                continue

            # 单日熔断
            if day_equity_start > 0 and (day_equity_start - equity) / day_equity_start > daily_loss_limit:
                circuit_breaker_until = bar.timestamp + 86400
                equity_curve.append(equity)
                continue

            # ═══════ 核心：实盘管线信号检测 ═══════
            if equity > 0 and (i - last_exit_bar) >= min_bars_gap:
                signal = self._pipeline_signal(i, bars, rsi_arr, macd_line, p, funding_rates, fgi_map)

                if signal in ("long", "short"):
                    # [P0-5 前视修复] 信号在 bar i 收盘产生，默认按【下一根开盘】成交
                    # （next_open）；原实现按 bar i 收盘成交 = 收盘决策按收盘成交的前视，
                    # 系统性高估回测收益并误导 GA/晋升。env BACKTEST_LP_FILL_MODEL=close
                    # 仅用于旧口径对比。
                    _fill_model = os.getenv("BACKTEST_LP_FILL_MODEL", "next_open").lower()
                    if _fill_model == "next_open" and i + 1 < len(bars):
                        _fill_bar = bars[i + 1]
                        _entry_bar = i + 1
                    else:
                        _fill_bar = bar
                        _entry_bar = i
                    _fill_price = float(getattr(_fill_bar, "o", _fill_bar.c) or _fill_bar.c)
                    pos_size_usd = equity * max_pos_pct * lev
                    qty = pos_size_usd / _fill_price
                    open_fee = pos_size_usd * TAKER_FEE
                    equity -= open_fee

                    if signal == "long":
                        entry = _fill_price * (1 + SLIPPAGE)
                        sl = entry * (1 - sl_pct)
                        tp = entry * (1 + tp_pct)
                    else:
                        entry = _fill_price * (1 - SLIPPAGE)
                        sl = entry * (1 + sl_pct)
                        tp = entry * (1 - tp_pct)

                    position = Position(
                        side=signal, entry_price=entry, quantity=qty,
                        leverage=lev, entry_bar=_entry_bar, entry_time=_fill_bar.dt_str,
                        sl_price=sl, tp_price=tp,
                        highest_since_entry=_fill_bar.h, lowest_since_entry=_fill_bar.l,
                        # [F340 2026-09-18 · B4 phase 1] 因子方向票（候选 A：预先算好的整条序列；
                        # 缺失时退回实时窗口计算，两者语义相同）。`factor_signal_weight=0` 时为 0。
                        factor_dir_at_entry=int(
                            (self._factor_dir_series[i] if (
                                getattr(self, "_factor_dir_series", None) is not None
                                and i < len(self._factor_dir_series)
                                and self._factor_dir_series[i] is not None)
                             else self._compute_factor_direction(i, bars, p))
                            or 0),
                    )

            equity_curve.append(self._mark_equity(equity, position, bar))
            peak_equity = max(peak_equity, equity_curve[-1])

            if progress_callback and i % 500 == 0:
                progress_callback(i / len(bars))

        # 结束时强制平仓
        if position:
            equity, trade = self._close_position(position, bars[-1], equity, "end_of_data")
            trades.append(trade)

        result.trades = trades
        result.equity_curve = equity_curve
        result.final_equity = equity
        result.total_trades = len(trades)
        result.funding_fees_total = total_funding_fees
        result.duration_seconds = time.time() - t0

        # [F340 2026-09-18 · B4 phase 1] 因子归因（方向级）：按开仓当根的因子方向票分桶。
        # 口径与验收（报告 §6.4 B4）：
        #   ① 三桶 n 之和 == total_trades；② Σ(桶 n×桶均值) == 总 PnL（容差内）；③ 同输入可复现。
        # `factor_signal_weight=0` 时全部落在 "0" 桶（预期），此时本块仍提供总口径校验。
        try:
            _buckets: Dict[str, Dict[str, float]] = {}
            for _t in trades:
                _k = str(int(getattr(_t, "factor_dir_at_entry", 0) or 0))
                _b = _buckets.setdefault(_k, {"n": 0, "wins": 0, "pnl": 0.0, "notional": 0.0})
                _b["n"] += 1
                _b["wins"] += 1 if float(getattr(_t, "pnl", 0.0) or 0.0) > 0 else 0
                _b["pnl"] += float(getattr(_t, "pnl", 0.0) or 0.0)
                _b["notional"] += abs(float(getattr(_t, "quantity", 0.0) or 0.0)
                                      * float(getattr(_t, "entry_price", 0.0) or 0.0))
            for _k, _b in _buckets.items():
                _n = int(_b["n"]) or 1
                _b["win_rate"] = round(_b["wins"] / _n, 4)
                _b["avg_pnl"] = round(_b["pnl"] / _n, 8)
                _b["avg_bp"] = round((_b["pnl"] / _b["notional"] * 1e4)
                                     if _b["notional"] > 0 else 0.0, 4)
                _b["pnl"] = round(_b["pnl"], 8)
                _b["notional"] = round(_b["notional"], 4)
                _b.pop("wins", None)
            result.factor_attr_by_dir = _buckets
            # [F342 · B4 phase 2 step 2] 因子名级**覆盖度归因**（仅当 sidecar 有数据时）。
            # 字段名刻意用 coverage_attr：它**不是**增量 alpha（口径声明见函数 docstring）。
            # [F349] 先把"开仓 bar"补进旁路——方向序列磁盘缓存命中时预计算循环整体空转，
            # 旁路会全空（实跑实测），必须按需补算而不是静默给出 {}。
            _attr_cov = self._ensure_attr_sidecar(bars, trades)
            result.factor_attr_coverage = dict(_attr_cov)
            _side = getattr(self, "_factor_attr_sidecar", None)
            if _FACTOR_ATTR_ENABLED and _side:
                result.factor_attr_by_name = attribute_trades_by_factor(trades, _side)
            # [F343 · B4 phase 2 step 3] 归因结果落盘（仅在开关打开时，避免默认写入无用数据）
            if _FACTOR_ATTR_ENABLED:
                result.factor_attr_path = persist_factor_attr(
                    run_id=result.run_id, symbol=symbol or "", tier=tier,
                    by_dir=result.factor_attr_by_dir, by_name=result.factor_attr_by_name,
                    extra={"coverage_stat": dict(_attr_cov)})
        except Exception as _attr_err:      # 归因失败不得影响回测结果本身
            logger.warning("[PipelineBT] 因子归因(方向级)失败: %s", _attr_err)
            result.factor_attr_by_dir = {}

        if not hasattr(result, 'data_completeness'):
            result.data_completeness = sum(1 for v in data_dims_used.values() if v)

        self._calculate_metrics(result)
        return result

    # ═══════ 管线信号 — 核心 ═══════

    def _pipeline_signal(self, i: int, bars: List[Bar],
                         rsi_arr: np.ndarray, macd_arr: np.ndarray,
                         p: Dict, funding_rates: Dict, fgi_map: Dict) -> Optional[str]:
        """调用实盘同款管线判定开仓信号"""
        bar = bars[i]
        rsi_val = float(rsi_arr[i])
        macd_val = float(macd_arr[i])

        # 1. 中期信号（RSI/MACD — 与实盘编排器完全相同）
        mid_bias, mid_conf = replay_mid_signal(rsi_val, macd_val, p)

        # 2. 长期信号（恐贪指数）
        fgi = self._get_fgi(bar.timestamp, fgi_map)
        # 简化情报方向：用最近N根的 RSI 趋势估算
        intel_dir = "neutral"
        intel_conf_pct = 0.0
        if i >= 10:
            rsi_avg_recent = float(np.mean(rsi_arr[max(0, i - 5):i + 1]))
            rsi_avg_past = float(np.mean(rsi_arr[max(0, i - 10):max(0, i - 5)]))
            if rsi_avg_recent > rsi_avg_past + 3:
                intel_dir = "bullish"
                intel_conf_pct = min(40, (rsi_avg_recent - rsi_avg_past) * 3)
            elif rsi_avg_recent < rsi_avg_past - 3:
                intel_dir = "bearish"
                intel_conf_pct = min(40, (rsi_avg_past - rsi_avg_recent) * 3)

        long_bias, long_conf = replay_long_signal(fgi, intel_dir, intel_conf_pct, p)

        # 3. 情报融合（修正中期）
        mid_bias, mid_conf = replay_intel_fusion(mid_bias, mid_conf, intel_dir, intel_conf_pct, p)

        # 4. 短期信号（鲸鱼用 0，资金费率从历史数据）
        whale_dir = 0.0
        fr = self._get_funding_rate(bar.timestamp, funding_rates)
        if fr > 0.0003:
            funding_signal = "bullish"
        elif fr < -0.0003:
            funding_signal = "bearish"
        else:
            funding_signal = "neutral"
        short_bias, short_conf = replay_short_signal(whale_dir, funding_signal, p)

        # 5. 编排器最终决策
        action, side, pos_pct = replay_finalize(
            long_bias, long_conf, mid_bias, mid_conf, short_bias, short_conf, p
        )
        if action != "enter":
            return None

        # ═══════ V3 整合：因子信号作为额外维度 ═══════
        factor_dir = self._compute_factor_direction(i, bars, p)

        # 6. 三维确认（技术面 / 订单流 / 情绪面）— 融入 weight_* 参数
        w_funding = float(p.get("weight_funding", 0.22))
        w_oi = float(p.get("weight_oi", 0.22))
        w_fgi = float(p.get("weight_fear_greed", 0.06))
        w_whale = float(p.get("weight_whale", 0.10))

        tech_dir = 0
        if rsi_val > 55 and macd_val > 0:
            tech_dir = 1
        elif rsi_val < 45 and macd_val < 0:
            tech_dir = -1

        # 订单流方向（资金费率加权）
        flow_raw = 0.0
        if fr > 0.0002:
            flow_raw = -1.0 * w_funding
        elif fr < -0.0002:
            flow_raw = 1.0 * w_funding
        flow_dir = 1 if flow_raw > 0.05 else (-1 if flow_raw < -0.05 else 0)

        # 情绪面方向（恐贪加权）
        sent_raw = 0.0
        if fgi < 35:
            sent_raw = -1.0 * w_fgi
        elif fgi > 65:
            sent_raw = 1.0 * w_fgi
        sent_dir = 1 if sent_raw > 0.01 else (-1 if sent_raw < -0.01 else 0)

        # 因子信号融合到技术面维度
        factor_signal_weight = float(p.get("factor_signal_weight", 0.3))
        if factor_dir != 0 and factor_signal_weight > 0:
            # 因子方向与已有技术面融合
            if tech_dir == 0:
                # 技术面无方向时，因子单独提供方向
                tech_dir = factor_dir
            elif tech_dir == factor_dir:
                # 方向一致时，增强（不改变方向，仅影响后续决策的置信度感知）
                pass
            else:
                # 方向冲突时，按因子权重衰减技术面
                # 如果因子权重足够大，可以翻转技术面方向
                if factor_signal_weight > 0.5:
                    tech_dir = factor_dir

        confirm_action, confirm_dir, confirm_level = replay_confirmation(
            tech_dir, flow_dir, sent_dir, int(p["confirmation_min_dims"])
        )

        # 7. 规则引擎覆盖
        final_op = replay_rule_decision(confirm_action, confirm_dir, mid_bias, mid_conf)

        if final_op == "buy":
            return "long"
        elif final_op == "sell":
            return "short"
        return None

    # ═══════ V3 整合：因子信号计算 ═══════

    def _compute_factor_direction(
        self, i: int, bars: List[Bar], p: Dict
    ) -> int:
        """
        V3 整合：使用因子引擎计算当前 bar 的因子信号方向。
        
        [M0-P2v2] 因子方向序列已由 run() 预计算并冻结（与 genome 无关），
        interval 步进时直接查表。相比旧行为（逐窗口现算、归一化漂移），
        fitness 变得确定且可跨 genome 公平比较。
        当 factor_signal_weight == 0 时跳过（完全关闭因子信号）。
        
        Returns:
            1 (看多), -1 (看空), 0 (中性)
        """
        factor_signal_weight = float(p.get("factor_signal_weight", 0.3))
        factor_signal_interval = int(p.get("factor_signal_interval", 6))

        # 因子信号关闭时跳过
        if factor_signal_weight <= 0:
            return 0

        # 按间隔计算因子（避免每根 bar 都计算，提升性能）
        if i % factor_signal_interval != 0:
            # 使用缓存的因子方向
            cached = getattr(self, '_cached_factor_dir', 0)
            return cached

        # [M0-P2v2] 命中冻结序列 → 查表（序列与窗口计算同构，但结果确定）
        _series = getattr(self, '_factor_dir_series', None)
        if _series is not None and i < len(_series):
            _d = int(_series[i])
            self._cached_factor_dir = _d
            return _d

        _d = self._compute_factor_direction_windowed(i, bars)
        self._cached_factor_dir = _d
        return _d

    def _factor_values_at(self, i: int, bars: List[Bar]) -> Optional[Dict[str, Any]]:
        """[F349 2026-09-18] 以第 i 根 bar 结尾的 30 根窗口的**原始全因子值**（无值返回 None）。

        ⚠️ 返回的是 `factor_engine.compute_all_factors` 的**原样** dict —— 值是
        `FactorValue` 对象（含 `.value` / `.has_data` / `.is_directional`），**不是 float**。
        这正是 F349 的第二个坑：B4 phase 2 初版旁路用 `isinstance(v, (int, float))` 过滤，
        而值永远是对象 ⇒ 过滤后恒为空 ⇒ 因子名级归因恒为 `{}`（实跑实测），
        且单测全绿（单测直接喂 float dict，**没经过真实 compute_all_factors**）。
        要 float 请用 `_floats_of()`。

        抽成独立方法的另一个原因：方向序列**磁盘缓存命中**时预计算循环整体空转，
        `_compute_factor_direction_windowed` 一次都不调用 ⇒ 旁路永远为空。
        """
        try:
            from backend.services.factor_engine import factor_engine

            window_start = max(0, i - 29)
            window_bars = bars[window_start:i + 1]
            if len(window_bars) < 15:
                return None
            import pandas as pd
            klines_df = pd.DataFrame([{
                'open': b.o, 'high': b.h, 'low': b.l,
                'close': b.c, 'volume': b.v,
                'timestamp': b.timestamp,
            } for b in window_bars])
            factor_values = factor_engine.compute_all_factors(klines_df)
            return dict(factor_values) if factor_values else None
        except Exception as exc:      # 因子计算失败不打断回测（与旧行为一致）
            logger.debug("[PipelineBT] _factor_values_at(%d) 失败: %s", i, exc)
            return None

    @staticmethod
    def _floats_of(factor_values: Optional[Dict[str, Any]]) -> Dict[str, float]:
        """把 `compute_all_factors` 的原始 dict 转成 {因子名: float}，供归因旁路使用。

        规则（每条都影响归因正确性）：
          - `FactorValue` 取 `.value`；已经是数值的原样用；
          - `has_data is False` 的**跳过**：管线对它们权重置 0、`_aggregate` 直接 continue，
            把它们记成"活跃"会虚增覆盖率（把"没数据"算成"投过票"）；
          - 非有限值（NaN/Inf）跳过，避免污染 bp 统计。
        """
        import math
        out: Dict[str, float] = {}
        for k, v in (factor_values or {}).items():
            if getattr(v, "has_data", True) is False:
                continue
            raw = getattr(v, "value", v)
            try:
                f = float(raw)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(f):
                continue
            out[str(k)] = f
        return out

    def _ensure_attr_sidecar(self, bars: List[Bar], trades: List[Any]) -> Dict[str, int]:
        """[F349] 保证"开仓 bar"都在归因旁路里；缺的**按需补算**（成本 ∝ 交易数，不随 bars 增长）。

        返回 {"needed","computed","covered"} —— 覆盖率必须可读，**不允许静默为空**：
        旧实现下磁盘缓存命中会产出 `by_name={}` 而**没有任何日志**，调用方会以为"这套参数
        没有因子信息"，实际是数据源压根没被访问。
        """
        stat = {"needed": 0, "computed": 0, "covered": 0}
        if not _FACTOR_ATTR_ENABLED:
            return stat
        try:
            needed = {int(getattr(t, "entry_bar", -1) or -1) for t in (trades or [])}
            needed = {i for i in needed if i >= 0}
            stat["needed"] = len(needed)
            side = getattr(self, "_factor_attr_sidecar", None)
            if side is None:
                side = {}
                self._factor_attr_sidecar = side
            missing = sorted(i for i in needed if i not in side)
            for i in missing:
                vals = self._floats_of(self._factor_values_at(i, bars))
                if vals:
                    side[int(i)] = vals
                    stat["computed"] += 1
            stat["covered"] = sum(1 for i in needed if i in side)
            if stat["needed"] and stat["covered"] < stat["needed"]:
                logger.warning(
                    "[PipelineBT] 因子归因覆盖率不足: %d/%d 笔开仓 bar 拿到因子值"
                    "（其余 bar 无因子值，其交易不进 by_name 统计）",
                    stat["covered"], stat["needed"],
                )
            elif stat["needed"]:
                logger.info(
                    "[PipelineBT] 因子归因旁路就绪: %d/%d 笔开仓 bar，按需补算 %d 个窗口",
                    stat["covered"], stat["needed"], stat["computed"],
                )
        except Exception as exc:
            logger.warning("[PipelineBT] 因子归因旁路补算失败: %s", exc)
        return stat

    def _compute_factor_direction_windowed(self, i: int, bars: List[Bar]) -> int:
        """原慢路径：对以 i 结尾的 30 根窗口跑全因子引擎并合成方向。"""
        try:
            from backend.services.factor_engine import FactorSignalGenerator

            # 原样 FactorValue dict：下游 generate_signals 需要对象（含 has_data/is_directional）
            factor_values = self._factor_values_at(i, bars)
            if not factor_values:
                return 0

            # [F341 2026-09-18 · B4 phase 2 step 1] 旁路记录**因子值**（默认关，见 `_FACTOR_ATTR_ENABLED`）：
            # 这是"因子名级归因"的唯一数据来源——没有它，回测只能回答"哪个方向赚了"（phase 1），
            # 回答不了"哪个因子赚了"。默认关是为了不拖慢进化（本函数在预计算循环里按 bar 调用）。
            if _FACTOR_ATTR_ENABLED:
                try:
                    _side = getattr(self, "_factor_attr_sidecar", None)
                    if _side is None:
                        _side = {}
                        self._factor_attr_sidecar = _side
                    _vals = self._floats_of(factor_values)
                    if _vals:
                        _side[int(i)] = _vals
                except Exception:
                    pass

            # 生成因子信号
            signal_gen = FactorSignalGenerator()
            composite = signal_gen.generate_signals(factor_values)

            # 根据合成信号方向返回
            threshold = 0.3
            if composite.direction > threshold and composite.strength > 0.3:
                return 1
            elif composite.direction < -threshold and composite.strength > 0.3:
                return -1
            else:
                return 0
        except Exception as e:
            # 因子计算失败时静默降级，不影响原有信号管线
            return 0

    # ═══════ 工具函数 ═══════

    @staticmethod
    def _get_funding_rate(ts: int, rates: Dict[int, float]) -> float:
        """[P0-5 前视修复] 只取 ≤ ts 的最近历史样本（backward）。

        原实现 min(abs(t-ts)) 会命中决策点之后的未来样本（±1 天），
        回放引擎因此系统性高估收益、与实盘不一致。未来样本一律不可用。
        """
        if not rates:
            return 0.0
        past = [t for t in rates.keys() if t <= ts]
        if not past:
            return 0.0
        closest = max(past)
        if ts - closest < 86400:
            return rates[closest]
        return 0.0

    @staticmethod
    def _get_fgi(ts: int, fgi_map: Dict[int, float]) -> float:
        """[P0-5 前视修复] 同 funding：只取 ≤ ts 的最近样本。"""
        if not fgi_map:
            return 50.0
        past = [t for t in fgi_map.keys() if t <= ts]
        if not past:
            return 50.0
        closest = max(past)
        if ts - closest < 86400 * 2:
            return fgi_map[closest]
        return 50.0

    @staticmethod
    def _mark_equity(cash_equity: float, position: Optional[Position], bar: Bar) -> float:
        if not position:
            return cash_equity
        if position.side == "long":
            unrealized = (bar.c - position.entry_price) * position.quantity
        else:
            unrealized = (position.entry_price - bar.c) * position.quantity
        return cash_equity + unrealized

    @staticmethod
    def _close_position(position: Position, bar: Bar, equity: float,
                        reason: str, exit_price: float = None) -> tuple:
        if exit_price is None:
            exit_price = bar.c
        notional = position.quantity * exit_price
        close_fee = notional * TAKER_FEE
        if position.side == "long":
            pnl = (exit_price - position.entry_price) * position.quantity - close_fee
        else:
            pnl = (position.entry_price - exit_price) * position.quantity - close_fee
        margin = position.quantity * position.entry_price / position.leverage
        pnl_pct = pnl / margin if margin > 0 else 0
        equity += pnl
        trade = TradeRecord(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            leverage=position.leverage,
            entry_bar=position.entry_bar,
            exit_bar=bar.idx,
            entry_time=position.entry_time,
            exit_time=bar.dt_str,
            pnl=pnl,
            pnl_pct=pnl_pct,
            fee=close_fee + (notional * TAKER_FEE),
            exit_reason=reason,
            # [F340 2026-09-18 · B4 phase 1] 把开仓当根的因子方向票带进成交记录，
            # 供 `run()` 收尾按 +1/-1/0 分桶做因子归因（验收：Σ桶贡献 == 总 PnL）。
            factor_dir_at_entry=int(getattr(position, "factor_dir_at_entry", 0) or 0),
        )
        return equity, trade

    def _calculate_metrics(self, result: BacktestResult):
        if not result.trades:
            return
        wins = [t for t in result.trades if t.pnl > 0]
        losses = [t for t in result.trades if t.pnl <= 0]
        result.win_rate = len(wins) / len(result.trades) if result.trades else 0
        total_profit = sum(t.pnl for t in wins)
        total_loss = abs(sum(t.pnl for t in losses))
        result.profit_factor = total_profit / total_loss if total_loss > 0 else 999
        result.total_return = (result.final_equity - self.initial_capital) / self.initial_capital

        if hasattr(result, '_bars_ref') and result._bars_ref and len(result._bars_ref) > 1:
            ts_range = result._bars_ref[-1].timestamp - result._bars_ref[0].timestamp
            years = max(ts_range / (365.25 * 86400), 0.01)
            ratio = result.final_equity / self.initial_capital
            result.annualized_return = (ratio ** (1 / years) - 1) if ratio > 0 else -1
        else:
            result.annualized_return = result.total_return

        # 最大回撤
        eq = np.array(result.equity_curve)
        if len(eq) > 0:
            peaks = np.maximum.accumulate(eq)
            dd = (peaks - eq) / np.where(peaks > 0, peaks, 1)
            result.max_drawdown = float(np.max(dd)) if len(dd) > 0 else 0
        # Sharpe
        pnl_pcts = [t.pnl_pct for t in result.trades]
        if len(pnl_pcts) > 1:
            avg_r = np.mean(pnl_pcts)
            std_r = np.std(pnl_pcts)
            trades_per_year = len(result.trades) / max(0.01,
                (result._bars_ref[-1].timestamp - result._bars_ref[0].timestamp) / (365.25 * 86400)) if hasattr(result, '_bars_ref') else 100
            result.sharpe_ratio = (avg_r / std_r * math.sqrt(trades_per_year)) if std_r > 0 else 0
            result.avg_trade_return = avg_r
        # 连续胜负
        streak_w = streak_l = max_w = max_l = 0
        for t in result.trades:
            if t.pnl > 0:
                streak_w += 1
                streak_l = 0
            else:
                streak_l += 1
                streak_w = 0
            max_w = max(max_w, streak_w)
            max_l = max(max_l, streak_l)
        result.max_consecutive_wins = max_w
        result.max_consecutive_losses = max_l
        if result.trades:
            bars_held = [t.exit_bar - t.entry_bar for t in result.trades]
            result.avg_holding_bars = np.mean(bars_held) if bars_held else 0
