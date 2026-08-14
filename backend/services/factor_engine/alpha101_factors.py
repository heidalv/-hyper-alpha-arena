"""alpha101_factors — Alpha101 风格公式因子库（S4-A，中长线 4h/1d）。

把经典 Alpha101 / WorldQuant 类因子改写成**单序列、向量化、可用受限 eval 求值**的
numpy 表达式（借助 `formula_ops` 提供的 delay/delta/ts_*/decay_linear 等算子），
灌入 `custom_factor_store` 并打标 `extra={"horizon":"midlong","timeframe":...}`。

准入闭环
========
仅"登记候选"——真正能否进入中长线活跃因子集，必须经 `factor_backtest_scorer` 在
对应时间框架(4h/1d)上做样本外回测(IC/ICIR/OOS Sharpe)+ 与已 active 因子相关性去冗余，
达 A/B 级才晋升 active。跨截面 rank 类因子在单标的下退化为滚动时间序列排名。

设计取舍：中长线更看"趋势/均值回归/量价背离"，故 4h 侧重中周期动量与量价相关，
1d 侧重均值回归与长动量。所有公式含 +1e-9 兜底避免除零。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# (基础名, 公式, [时间框架], 说明)
_ALPHA_LIB: List[Tuple[str, str, List[str], str]] = [
    ("mom",           "delta(close, 20) / (delay(close, 20) + 1e-9)",              ["4h", "1d"], "20期动量"),
    ("mom_fast",      "delta(close, 6) / (delay(close, 6) + 1e-9)",                ["4h"],       "6期动量"),
    ("rev_z",         "-1 * ((close - ts_mean(close, 20)) / (ts_std(close, 20) + 1e-9))", ["4h", "1d"], "均值回归z分"),
    ("close_open",    "(close - open) / (open + 1e-9)",                            ["4h"],       "日内收益(Alpha#101变体)"),
    ("hl_pos",        "(close - low) / ((high - low) + 1e-9)",                     ["4h"],       "收盘在高低区间位置"),
    ("alpha006",      "-1 * ts_corr(open, volume, 10)",                            ["4h"],       "Alpha#6 量价背离"),
    ("alpha012",      "sign(delta(volume, 1)) * (-1 * delta(close, 1))",           ["4h"],       "Alpha#12 量增价跌反转"),
    ("corr_c_v",      "ts_corr(close, volume, 15)",                                ["4h", "1d"], "价量滚动相关"),
    ("vol_mom",       "delta(close, 5) / (ts_std(close, 20) + 1e-9)",              ["4h"],       "波动归一动量"),
    ("tsr_vol_dir",   "ts_rank(volume, 20) * sign(delta(close, 5))",               ["4h"],       "量能排名×方向"),
    ("decay_mom",     "decay_linear(delta(close, 1), 10)",                         ["4h"],       "线性衰减动量"),
    ("argmax_rev",    "-1 * (ts_argmax(close, 20) - 0.5)",                          ["4h", "1d"], "新高位置反转"),
    ("range_contract","(ts_max(high, 10) - ts_min(low, 10)) / (close + 1e-9)",     ["1d"],       "区间收缩/扩张"),
    ("gap",           "(open - delay(close, 1)) / (delay(close, 1) + 1e-9)",       ["4h"],       "跳空"),
    ("accel",         "delta(delta(close, 1), 1)",                                 ["4h"],       "价格加速度"),
    ("mom_long",      "delta(close, 40) / (delay(close, 40) + 1e-9)",              ["1d"],       "长周期动量"),
    ("rev_long",      "-1 * ((close - ts_mean(close, 40)) / (ts_std(close, 40) + 1e-9))", ["1d"], "长周期均值回归"),
    ("vp_trend",      "sign(delta(close, 10)) * ts_rank(volume, 10)",              ["4h", "1d"], "量价趋势确认"),
    # [2026-08-14 弹药扩源 v2] 参数域扩展：4h 侧重趋势/动量/突破，1d 侧重位置/长动量。
    ("mom10",         "delta(close, 10) / (delay(close, 10) + 1e-9)",             ["4h", "1d"], "10期动量"),
    ("mom30",         "delta(close, 30) / (delay(close, 30) + 1e-9)",             ["4h", "1d"], "30期动量"),
    ("mom60",         "delta(close, 60) / (delay(close, 60) + 1e-9)",             ["1d"], "60期长动量"),
    ("sma_dev20",     "(close - ts_mean(close, 20)) / (ts_mean(close, 20) + 1e-9)", ["4h", "1d"], "价格偏离20均线"),
    ("sma_dev50",     "(close - ts_mean(close, 50)) / (ts_mean(close, 50) + 1e-9)", ["4h", "1d"], "价格偏离50均线"),
    ("breakout_hi",   "(close - ts_max(high, 20)) / (ts_max(high, 20) + 1e-9)",   ["4h"], "20期新高突破"),
    ("breakout_lo",   "(close - ts_min(low, 20)) / (ts_min(low, 20) + 1e-9)",     ["4h"], "20期新低突破"),
    ("macd_like",     "delta(ts_mean(close, 12), 1) - delta(ts_mean(close, 26), 1)", ["4h", "1d"], "MACD近似(12/26)"),
    ("macd_sig",      "ts_mean(delta(ts_mean(close, 12), 1) - delta(ts_mean(close, 26), 1), 9)", ["4h"], "MACD信号线近似"),
    ("rsi_like",      "(ts_mean(np.maximum(delta(close, 1), 0), 14) - ts_mean(np.maximum(-delta(close, 1), 0), 14)) / (ts_mean(np.maximum(delta(close, 1), 0), 14) + ts_mean(np.maximum(-delta(close, 1), 0), 14) + 1e-9)", ["4h"], "RSI14近似(多空强度差比)"),
    ("range_pos",     "(close - ts_min(low, 20)) / ((ts_max(high, 20) - ts_min(low, 20)) + 1e-9)", ["4h", "1d"], "唐奇安通道位置"),
    ("atr_mom",       "delta(close, 20) / (ts_mean(ts_max(high, 14) - ts_min(low, 14), 14) + 1e-9)", ["4h"], "ATR归一动量"),
    ("vol_expand",    "ts_std(close, 10) / (ts_std(close, 40) + 1e-9)",           ["4h", "1d"], "波动扩张比"),
    ("trend_pos",     "ts_rank(close, 60)",                                        ["1d"], "60期价格位置"),
    ("mom_rank",      "ts_rank(delta(close, 20), 60)",                            ["4h"], "动量排名"),
    ("gap_rev",       "-1 * (open - delay(close, 1)) / (delay(close, 1) + 1e-9)", ["4h"], "跳空反转"),
    ("hl_corr",       "ts_corr(close, high, 20)",                                 ["4h"], "价高相关"),
    ("vol_mom10",     "delta(close, 10) / (ts_std(close, 20) + 1e-9)",            ["4h"], "短窗波动归一动量"),
]


def _admin_tenant() -> "int | None":
    """[2026-08-14 阶段2-1] 租户修复：register/list 不传 tenant_id 时读 ContextVar，
    在脚本/调度器上下文恒为 None → 登记被拒（"tenant_id required"）、候选列表恒空，
    导致中长线因子挖掘从未产出过候选。显式解析 admin tenant（与
    midlong_active_factor_set._resolve_tenant_id 同源）。"""
    try:
        from backend.services.coin_select_platform_service import resolve_admin_tenant_id
        return resolve_admin_tenant_id()
    except Exception:
        return None


def seed_alpha101(timeframes: List[str] = None, reopen: bool = False) -> Dict[str, Any]:
    """把公式库登记为中长线候选因子（幂等）。返回登记统计。

    [2026-08-14 P2-1] reopen=True 时先把 rejected 的 alpha101 因子重开为
    candidate（闸门阈值/引擎修复后批量重评，或"一键挖矿"重灌后全部重新入队）。
    """
    from backend.services.factor_engine.custom_factor_store import custom_factor_store

    _tid = _admin_tenant()
    reopened = 0
    if reopen and _tid is not None:
        reopened = custom_factor_store.reopen_rejected(tenant_id=_tid, category="alpha101")
    want = set(timeframes or ["4h", "1d"])
    registered, skipped = 0, 0
    ids: List[str] = []
    for base, formula, tfs, note in _ALPHA_LIB:
        for tf in tfs:
            if tf not in want:
                continue
            res = custom_factor_store.register(
                name=f"a101_{base}_{tf}",
                formula=formula,
                category="alpha101",
                source="alpha101_lib",
                extra={"horizon": "midlong", "timeframe": tf, "note": note},
                tenant_id=_tid,
            )
            if res.get("ok"):
                registered += 1
                ids.append(res["factor_id"])
            else:
                skipped += 1
                logger.info(f"[Alpha101] 跳过 {base}_{tf}: {res.get('reason')}")
    logger.info(f"[Alpha101] 灌库完成: 登记{registered} 跳过{skipped} 重开{reopened}")
    return {"registered": registered, "skipped": skipped, "reopened": reopened,
            "factor_ids": ids}


def validate_alpha101(limit: int = 50) -> Dict[str, Any]:
    """对已登记的 Alpha101 中长线候选逐个样本外打分+晋升（A/B→active）。"""
    from backend.services.factor_engine.custom_factor_store import custom_factor_store
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

    _tid = _admin_tenant()
    cands = [
        r for r in custom_factor_store.list_candidates(tenant_id=_tid)
        if r.get("category") == "alpha101"
        and str((r.get("extra") or {}).get("horizon") or "").lower() == "midlong"
    ][:limit]
    results = []
    for rec in cands:
        try:
            r = factor_backtest_scorer.validate_and_promote(rec["factor_id"])
            results.append({
                "factor_id": r.factor_id, "grade": r.grade,
                "admitted": r.admitted, "ic": r.ic_mean,
                "oos_sharpe": r.oos_sharpe, "reason": r.reason,
            })
        except Exception as e:
            logger.warning(f"[Alpha101] {rec.get('factor_id')} 打分异常: {e}")
    promoted = [r for r in results if r["admitted"]]
    logger.info(f"[Alpha101] 验证完成: 打分{len(results)} 晋升{len(promoted)}")
    return {"scored": len(results), "promoted": len(promoted), "results": results}


def seed_and_validate() -> Dict[str, Any]:
    """一步到位：灌库 + 样本外验证晋升（供 CLI/接口/定时任务调用）。"""
    seed = seed_alpha101()
    val = validate_alpha101()
    return {"seed": seed, "validate": val}
