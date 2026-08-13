"""ScalpScoreCalibration — 因子分数-胜率校准（2026-08-13 短线因子根因修复 P0-2）。

实证背景（docs/短线因子亏损根因诊断报告.md）：
21.7 万已结算信号中 factor_score 与真实胜率零相关（score≥70 段胜率 36.5%、
平均净收益 -0.241%，反而低于 <50 段）。静态 CONFIRM 门槛已不可信，需要
用真实信号日志对分数做分桶胜率校准，让门槛与仓位跟随实证胜率。

流程：
1. 从 scalp_signal_log 取已结算 (factor_score, win) 样本（近 N 天）；
2. 按 10 分桶统计各桶胜率与样本数；
3. PAV 等渗回归单调化桶胜率（消除分桶噪声的倒挂）；
4. 找「胜率 ≥ 盈亏平衡胜率」的最低桶下界 → 建议门槛（threshold）；
5. score≥70 高分段单独评估：历史胜率不达标 → high_score_ok=False；
6. 结果写 data/scalp_calibration.json，每日由 scheduler 重跑；router 读文件生效。

回滚：SCALP_CALIBRATION_ENABLED=0|false|off（整体关闭）；
       SCALP_CALIBRATED_THRESHOLD>0 时以该静态值覆盖校准结果。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")

_CALIB_FILE = os.path.join("data", "scalp_calibration.json")


def calibration_enabled() -> bool:
    raw = (os.getenv("SCALP_CALIBRATION_ENABLED", "true") or "true").strip().lower()
    if raw in _FALSY:
        return False
    return raw in _TRUTHY or raw == ""


def _break_even_winrate() -> float:
    """盈亏平衡胜率：TP/SL 比与费用决定的保本线（诊断实证约需 ≥40-45%）。"""
    try:
        return float(os.getenv("SCALP_CALIB_BREAKEVEN_WINRATE", "0.42") or 0.42)
    except (TypeError, ValueError):
        return 0.42


def _pav_monotone(vals: List[float]) -> List[float]:
    """PAV 等渗回归（单调不减），输出非降序列。"""
    if not vals:
        return []
    out: List[float] = []
    blocks: List[List[float]] = []
    for v in vals:
        blocks.append([v])
        # 相邻块均值违反单调性时合并
        while len(blocks) >= 2 and (sum(blocks[-2]) / len(blocks[-2])) > (
            sum(blocks[-1]) / len(blocks[-1])
        ):
            merged = blocks[-2] + blocks[-1]
            blocks = blocks[:-2] + [merged]
    for b in blocks:
        out.extend([sum(b) / len(b)] * len(b))
    return out


def _load_samples(days: int) -> List[tuple]:
    """取近 days 天已结算信号的 (factor_score, win)。"""
    from sqlalchemy import text as _text
    from backend.database.connection import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(_text(
            "SELECT factor_score, win FROM scalp_signal_log "
            "WHERE settled = true AND win IS NOT NULL AND factor_score IS NOT NULL "
            "AND created_at >= NOW() - INTERVAL '" + str(int(days)) + " days'"
        )).fetchall()
        out = []
        for fs, win in rows:
            try:
                out.append((float(fs), bool(win)))
            except (TypeError, ValueError):
                continue
        return out
    finally:
        db.close()


def calibrate() -> Dict[str, Any]:
    """重跑分数-胜率校准，写 data/scalp_calibration.json，返回结果摘要。"""
    if not calibration_enabled():
        return {"enabled": False, "reason": "SCALP_CALIBRATION_ENABLED=0"}

    days = int(os.getenv("SCALP_CALIB_LOOKBACK_DAYS", "60") or 60)
    min_bucket = int(os.getenv("SCALP_CALIB_MIN_BUCKET_SAMPLES", "100") or 100)
    high_band = int(os.getenv("SCALP_HIGH_SCORE_BAND", "70") or 70)
    static_thr = float(os.getenv("SCALP_CALIBRATED_THRESHOLD", "0") or 0)
    breakeven = _break_even_winrate()

    samples = _load_samples(days)
    if len(samples) < min_bucket * 2:
        logger.warning(
            "[ScalpCalib] 有效样本不足（%d），保留上次校准结果", len(samples),
        )
        prev = load_calibration()
        if prev:
            return {**prev, "stale": True, "n_samples": len(samples)}
        return {"enabled": True, "error": "insufficient_samples", "n_samples": len(samples)}

    # 10 分桶统计
    buckets: Dict[int, Dict[str, Any]] = {}
    for fs, win in samples:
        lo = int(fs // 10) * 10
        b = buckets.setdefault(lo, {"lo": lo, "wins": 0, "n": 0})
        b["n"] += 1
        if win:
            b["wins"] += 1

    ordered = sorted(buckets.keys())
    winrates = [buckets[k]["wins"] / max(buckets[k]["n"], 1) for k in ordered]
    mono = _pav_monotone(winrates)

    # 建议门槛：单调化胜率首次 ≥ 盈亏平衡的桶下界
    threshold: Optional[int] = None
    for k, wr in zip(ordered, mono):
        if wr >= breakeven and buckets[k]["n"] >= min_bucket:
            threshold = int(k)
            break

    # 高分段（score≥high_band）：合并统计历史胜率是否达标
    high_wins = sum(b["wins"] for k, b in buckets.items() if k >= high_band)
    high_n = sum(b["n"] for k, b in buckets.items() if k >= high_band)
    high_ok = (high_n >= min_bucket) and (high_wins / max(high_n, 1)) >= breakeven

    bucket_table = [
        {
            "lo": int(k), "n": buckets[k]["n"],
            "winrate": round(winrates[i], 4),
            "winrate_mono": round(mono[i], 4),
        }
        for i, k in enumerate(ordered)
    ]

    result = {
        "enabled": True,
        "updated_at": time.time(),
        "lookback_days": days,
        "n_samples": len(samples),
        "breakeven_winrate": breakeven,
        # SCALP_CALIBRATED_THRESHOLD>0 时以静态值覆盖（手动兜底）
        "threshold": int(static_thr) if static_thr > 0 else threshold,
        "high_score_ok": high_ok,
        "high_score_band": high_band,
        "buckets": bucket_table,
    }

    try:
        os.makedirs(os.path.dirname(_CALIB_FILE) or ".", exist_ok=True)
        with open(_CALIB_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(
            "[ScalpCalib] 校准完成 n=%d threshold=%s high_score_ok=%s",
            len(samples), result["threshold"], high_ok,
        )
    except Exception as e:
        logger.warning("[ScalpCalib] 结果写入失败: %s", e)
    return result


def load_calibration() -> Dict[str, Any]:
    """读最近一次校准结果（router 热路径读文件，无 DB 开销）。"""
    try:
        with open(_CALIB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("enabled"):
            return data
    except Exception:
        pass
    return {}


def effective_threshold(confirm: int) -> int:
    """校准后的生效门槛 = max(静态 CONFIRM, 校准建议门槛)。"""
    thr = confirm
    try:
        static_thr = float(os.getenv("SCALP_CALIBRATED_THRESHOLD", "0") or 0)
        if static_thr > 0:
            return max(int(confirm), int(static_thr))
    except (TypeError, ValueError):
        pass
    try:
        calib = load_calibration()
        t = calib.get("threshold")
        if isinstance(t, (int, float)) and t > 0:
            thr = max(int(confirm), int(t))
    except Exception:
        pass
    return thr


def high_score_cap(score: int) -> tuple:
    """[P0-2] score≥70 高分段历史胜率条件：不达标则封顶到 69。

    Returns:
        (capped_score, note)；note 为空表示无需封顶。
    """
    if score < 70:
        return score, ""
    try:
        calib = load_calibration()
        if calib.get("enabled") and calib.get("high_score_ok") is False:
            return min(score, 69), "高分段历史胜率不达标，封顶69"
    except Exception:
        pass
    return score, ""


# 全局单例（无状态，模块函数即可；保留单例入口便于路由引用）
scalp_score_calibration = calibrate
