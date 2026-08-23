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
        # [2026-08-22 M3-5] 无边际观察态：threshold=None（任何分数段都到不了盈亏
        # 平衡）时记录继续天数，供停摆条件/告警使用；连续 3 天触发 CRITICAL 提示
        # （短线应人工决议：继续观察 or 永久停用）。
        "no_edge": threshold is None and high_ok is False,
    }

    # M3-5：无边际天数滚动计数
    if result.get("no_edge"):
        _prev = load_calibration()
        _since = float((_prev or {}).get("no_edge_since") or 0.0)
        if _since <= 0:
            _since = time.time()
            logger.warning(
                "[ScalpCalib] 校准进入「无盈利分桶」观察态：短线开仓已由 fail-closed 拦截"
            )
        else:
            _days = int((time.time() - _since) / 86400) + 1
            if _days >= 3:
                logger.critical(
                    "[ScalpCalib] 无盈利分桶已持续 %d 天（自 %s）：短线应人工决议——"
                    "继续观察 or 永久停用（当前所有开仓已被 fail-closed 拦截）",
                    _days, time.strftime("%Y-%m-%d", time.localtime(_since)),
                )
        result["no_edge_since"] = _since
    else:
        result["no_edge_since"] = 0.0
        logger.info("[ScalpCalib] 校准出现可盈利分桶（threshold=%s），短线观察态解除",
                    result.get("threshold"))

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


# [2026-08-22 M0-3] 校准判定"无盈利分桶"时的封禁门槛（等效禁止开仓）
CALIBRATION_BLOCKED_THRESHOLD = 999


def effective_threshold(confirm: int, kind: str = "trend") -> int:
    """校准后的生效门槛 = max(静态 CONFIRM, 校准建议门槛)。

    [2026-08-22 M0-3 fail-closed] 校准输出"无解"（threshold=None，即没有任何
    分数段的单调化胜率达到盈亏平衡线）时，trend(LANE) 必须关断短线开仓，
    而不是回退到静态 CONFIRM 继续刷单。返回 CALIBRATION_BLOCKED_THRESHOLD(999)
    等效禁止开仓；仅当管理员显式设置 SCALP_CALIBRATED_THRESHOLD>0 时允许人工覆盖。

    [2026-08-22 PROFIT-2 盈利实验] 分类放行：ranging_mr（震荡均值回归）历史胜率
    44.8% > 盈亏平衡 37.5%（TP2%/SL1.2%），是短线里唯一有胜率证据的子类——
    即便全桶校准仍为 None，也允许 MR 按基础门槛放行（配合 EV 闸门按 MR 独立的
    0.85 实现率折扣 + 冷启动豁免做最后把关 + 峰值追踪改善盈亏比）。
    学习错误由 EV 门与后续 daily EV governor 收紧，而非入门禁。
    """
    thr = confirm
    try:
        static_thr = float(os.getenv("SCALP_CALIBRATED_THRESHOLD", "0") or 0)
        if static_thr > 0:
            return max(int(confirm), int(static_thr))
    except (TypeError, ValueError):
        pass
    try:
        calib = load_calibration()
        if not calib:
            return thr  # 校准数据缺/未启用：按静态门槛（冷启动保守放行，观察期）
        t = calib.get("threshold")
        if t is None or not isinstance(t, (int, float)) or float(t) <= 0:
            if str(kind).lower() == "ranging_mr":
                # MR：唯一有胜率证据的短线子类（44.8% vs 保本 37.5%），允许实验放行
                logger.info(
                    "[ScalpCalib] 校准无盈利分桶，但 ranging_mr 历史胜率≥保本线："
                    "按基础门槛放行（EV 闸门 + 峰值追踪兜底） (PROFIT-2)"
                )
                return thr
            # [2026-08-23 短线赚钱改造] 无盈利分桶不再 999 全拦趋势打法：
            # ①旧校准口径建立在旧 TP/SL 参数（TP≈2.5% 摸不到）之上，与 8/23 新
            #   参数（TP≤1.5%/SL≤1.15%/45min 超时）不对应，用旧尺子把新参数锁死
            #   只会让新参数永远得不到样本验证；
            # ②模拟盘的本职是积累学习数据，全拦=零数据=永远无法校准；
            # ③回退静态 CONFIRM 门槛放行，由 EV 闸门/仓位乘数/风控硬顶兜底。
            # 回滚：SCALP_CALIB_NOEDGE_BLOCK=1 恢复旧 fail-closed 行为。
            if os.getenv("SCALP_CALIB_NOEDGE_BLOCK", "0") in ("1", "true", "yes", "on"):
                logger.warning(
                    "[ScalpCalib] 校准无盈利分桶(threshold=None)，trend/lane 开仓按 "
                    "fail-closed 拦截 (SCALP_CALIB_NOEDGE_BLOCK=1)"
                )
                return CALIBRATION_BLOCKED_THRESHOLD
            logger.warning(
                "[ScalpCalib] 校准无盈利分桶(threshold=None)，trend/lane 回退静态门槛 "
                "%s 放行观察（新 TP/SL 参数需要新样本；EV 闸门/风控兜底）——"
                "SCALP_CALIB_NOEDGE_BLOCK=1 可回滚全拦",
                thr,
            )
            return thr
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
