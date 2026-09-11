"""
因子 IC 有效性闭环（M7）

闭环：信号 →（开仓时 signal_trade_feedback 落因子快照）→ 成交 →
     （平仓时 update_trade_pnl 回填盈亏）→ 本模块评估 →
     factor_performance_logs 留痕 + 运行时权重文件 → composite 信号自动降权

评估口径：
  - 把因子原始值经 FactorSignalGenerator 的方向映射转为 [-1,+1]（修复
    record_entry_signals 用「值的正负」当方向的错误，如 RSI 恒为正）
  - long_equiv_pnl = pnl（做多）/ -pnl（做空）→ 多头等效收益
  - 方向胜率 = 因子方向与多头等效收益同号的比例（|方向|<0.2 的中性样本剔除）
  - IC = Pearson corr(因子方向, 多头等效收益)

降权规则（有 min_samples 个样本才生效）：
  胜率 < 40% → 权重 0.25；< 45% → 0.5；> 60% → 1.2（温和升权）；其余 1.0
"""

import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# [2026-09-02] 改为基于 __file__ 定位仓库根。原写法 os.path.join("data", ...) 是
# 相对路径，读写都依赖进程 cwd：从非仓库根启动（服务化/计划任务/子进程）时权重
# 文件会静默读不到 → load_runtime_factor_weights 返回空 → 全体因子退化等权，
# 且因为写入端用同一相对路径，还会在错误目录下生成第二份 data/ 分裂状态。
RUNTIME_WEIGHTS_FILE = str(
    Path(__file__).resolve().parents[2] / "data" / "factor_runtime_weights.json")
_weights_cache: dict = {"ts": 0.0, "data": {}}

MIN_SAMPLES = 30  # [P0-1 2026-07-30] 从8提高到30，避免小样本IC=1.0假象
# [2026-08-29 P2.2] ic_ev 模式的权重地板：负/零 IC 因子不再保持满权
_EV_WEIGHT_FLOOR = 0.1
NEUTRAL_DIRECTION_EPS = 0.2


def _unverified_weight() -> float:
    """样本不足（n < MIN_SAMPLES）因子的权重。

    [2026-09-02 G19] 原先这类因子保持初始 weight=1.0。实测 97 个因子里有 50 个
    样本 <30（IC 根本算不出，stats.ic 为 null），它们合计占全池权重的 75.2%，
    而 11 个样本 >=500 的可信因子只占 4.8% —— "从未验证"等价于"完全信任"，
    合成分数实际由一批没有证据的因子主导。改为地板权重：仍参与合成、仍继续
    积累样本（样本来自因子计算与 signal_trade_feedback，与权重无关），但在攒够
    证据前不主导决策。设 1.0 可回到旧行为。
    """
    try:
        return max(0.0, min(1.5, float(os.getenv("FACTOR_UNVERIFIED_WEIGHT", "0.1"))))
    except (TypeError, ValueError):
        return _EV_WEIGHT_FLOOR


def _ic_shrink_k() -> float:
    """IC 置信收缩强度 K：权重用 ic×n/(n+K) 而非裸 ic。K=0 关闭收缩。

    [2026-09-02 G19] 裸 IC 不含可信度信息：实测 ai_gen_sl_break 以 n=37 的
    IC=0.2759（t=1.66，不显著）顶到权重上限 1.5，而 obv 以 n=757 的 IC=0.0939
    （t=2.58，显著）只拿 0.876 —— 权重与证据强度完全倒挂。K=100 时样本 100 打
    5 折、400 打 8 折、900 打 9 折，与"标准误 ∝ 1/√n"的直觉一致，让证据不足的
    高 IC 回归均值。
    """
    try:
        return max(0.0, float(os.getenv("FACTOR_IC_SHRINK_K", "100")))
    except (TypeError, ValueError):
        return 100.0


def load_runtime_factor_weights() -> Dict[str, float]:
    """读取 IC 闭环产出的因子权重（60s 缓存）。

    返回空 dict 时，所有消费者都会退化为等权 1.0 —— 即"IC 学习闭环的产出被
    整体忽略"。这是一条静默 fail-open 路径，因此文件缺失/损坏/长期不更新
    都必须显式告警（见下方 _warn_weights_unavailable），否则权重体系失效
    时系统照常满权出信号、外部完全看不出来。
    """
    now = time.time()
    if now - _weights_cache["ts"] < 60:
        return _weights_cache["data"]
    data: Dict[str, float] = {}
    _reason = ""
    try:
        if os.path.exists(RUNTIME_WEIGHTS_FILE):
            with open(RUNTIME_WEIGHTS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            for k, v in (raw.get("weights") or {}).items():
                try:
                    # 边界保护：权重只允许 [0.1, 2.0]
                    data[str(k)] = max(0.1, min(2.0, float(v)))
                except (TypeError, ValueError):
                    continue
            if not data:
                _reason = "文件存在但 weights 段为空"
        else:
            _reason = f"文件不存在: {RUNTIME_WEIGHTS_FILE}"
    except Exception as err:
        _reason = f"读取失败: {err}"
        logger.warning(f"[FactorIC] 运行时权重读取失败: {err}")
    if _reason:
        _warn_weights_unavailable(_reason)
    # [2026-09-07 ReCAP regime-gate] 按当前市场 regime 门控组合权重：
    # 当前 regime 有成熟策略桶时，桶权重 65% + 全局 35%；否则用全局。
    # fail-open：任何异常用全局权重。REGIME_GATE_ENABLED=0 回滚。
    if data:
        try:
            from backend.services.regime_policy_library import gate_weights
            data = gate_weights(_current_market_regime(), data)
        except Exception:
            pass
    _weights_cache["ts"] = now
    _weights_cache["data"] = data
    return data


def _current_market_regime() -> str:
    """当前市场 regime（用 BTC 摘要分类；失败回 unknown）。"""
    try:
        from backend.services.decision_core.regime_agent import classify_regime
        from backend.services.analysis.context_pack import _klines
        kl = _klines("BTC", "1h", 30)
        if not kl or len(kl) < 25:
            return "unknown"
        closes = [float(k.get("close") or 0) for k in kl if k.get("close")]
        if len(closes) < 25:
            return "unknown"
        chg_1h = (closes[-1] / closes[-2] - 1) * 100 if closes[-2] else 0.0
        chg_24h = (closes[-1] / closes[-25] - 1) * 100 if closes[-25] else 0.0
        r = classify_regime({"price_change_1h_pct": chg_1h, "price_change_24h_pct": chg_24h})
        return str(getattr(r, "regime", "unknown") or "unknown")
    except Exception:
        return "unknown"


# 权重不可用告警的节流状态（避免每 60s 刷屏，但必须周期性提醒）
_unavailable_warn_ts: float = 0.0
_UNAVAILABLE_WARN_INTERVAL = 600.0


def _warn_weights_unavailable(reason: str) -> None:
    """权重不可用时的显式告警（10 分钟节流）。

    不在这里"降级到地板值"：地板值对下游的加权合成没有实际效果——
    midlong_factor_route 算的是 Σ(w·vote)/Σw，把所有因子统一压到 0.1 与统一
    给 1.0 归一化后完全等价。真正有意义的是让这条静默路径变得可见，并由
    scalp_chain_health 的 factor_runtime_weights_age_min 做新鲜度兜底。
    """
    global _unavailable_warn_ts
    now = time.time()
    if now - _unavailable_warn_ts < _UNAVAILABLE_WARN_INTERVAL:
        return
    _unavailable_warn_ts = now
    logger.error(
        "[FactorIC] 运行时因子权重不可用（%s）→ 全体因子退化为等权 1.0，"
        "IC 学习闭环产出被忽略。请检查 run_factor_ic_evaluation 是否在跑、"
        "以及 data/factor_runtime_weights.json 是否可读", reason,
    )


def _resolvable_factor_names() -> set:
    """代码库里可解析的因子名集合（因子源文件 stem，含归档/隔离目录）。

    仅用于识别"历史 28 字符截断残留"。返回空集表示无法判定，调用方必须
    退化为不过滤——宁可留噪声，也不能误杀有效因子的学习权重。
    """
    names: set = set()
    try:
        import glob
        base = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "factor_engine", "factors")
        for fp in glob.glob(os.path.join(base, "**", "*.py"), recursive=True):
            stem = os.path.basename(fp)[:-3]
            if stem and not stem.startswith("__"):
                names.add(stem)
    except Exception as err:  # pragma: no cover - 扫描失败即退化为不过滤
        logger.warning("[FactorIC] 因子名集合扫描失败，跳过截断名过滤: %s", err)
        return set()
    return names


# 历史 signal_type 截断长度：旧代码按 VARCHAR(28) 截 f"factor:{name}"，
# 减去 7 字符前缀 → 因子名恰好被切到 21 字符（迁移 0008 已扩到 100 并于
# 2026-08-29 修复写入侧，但 lookback 窗口内的历史行仍在持续产出死权重）。
_LEGACY_TRUNCATED_NAME_LEN = 28 - len("factor:")


def _is_legacy_truncated_name(name: str, known: set) -> bool:
    """判定因子名是否为 28 字符截断残留。

    判据取"长度恰为 21 且不对应任何因子源文件"这一交集，避免误伤真实存在
    的 21 字符因子名。这类名字的权重永远不会被消费——所有消费者都用全名
    走 runtime_weights.get(name)，截断名必然 miss。
    """
    return bool(known) and len(name) == _LEGACY_TRUNCATED_NAME_LEN and name not in known


def _recover_truncated_name(name: str, known: set) -> Optional[str]:
    """截断名 → 唯一前缀匹配的全名；多义或无匹配返回 None。

    [2026-09-02] 实测 14 天窗口 45621 行样本中 8242 行（18%）挂在 43 个截断名
    下被整体丢弃。其中 35 个名（6668 行、14.6%）在代码库里恰有**唯一**一个因子
    以其为前缀（如 ai_gen_extreme_revers → ai_gen_extreme_reversal），可无歧义
    恢复；5 个多义（如 ai_gen_short_timeout_ 对应 5 个因子）与 3 个已删因子不恢
    复，宁可少样本也不能把 A 的盈亏记到 B 头上。

    这批样本对 n<30 的 50 个因子意义最大：它们正是因样本不足拿 0.1 地板权重，
    多几百行就可能跨过 MIN_SAMPLES 拿到真实 IC。
    """
    if not _is_legacy_truncated_name(name, known):
        return None
    cands = [k for k in known if k.startswith(name)]
    return cands[0] if len(cands) == 1 else None


def _map_factor_direction(factor_name: str, raw_value: float) -> float:
    """用信号生成器的方向映射把因子原始值转为 [-1,+1] 方向。"""
    from backend.services.factor_engine.factor_signal_generator import (
        _default_direction,
    )

    gen = _get_signal_generator()
    mapper = gen._direction_mappers.get(factor_name)
    if mapper is None:
        # rsi_14 → rsi 这类带参数后缀的名字，按前缀匹配
        base = factor_name.split("_")[0]
        for key, fn in gen._direction_mappers.items():
            if factor_name.startswith(key) or base == key:
                mapper = fn
                break
    if mapper is None:
        mapper = _default_direction
    try:
        return max(-1.0, min(1.0, float(mapper(raw_value))))
    except (TypeError, ValueError, OverflowError):
        return 0.0


_signal_gen_singleton = None


def _get_signal_generator():
    global _signal_gen_singleton
    if _signal_gen_singleton is None:
        from backend.services.factor_engine.factor_signal_generator import (
            FactorSignalGenerator,
        )
        _signal_gen_singleton = FactorSignalGenerator()
    return _signal_gen_singleton


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    """Pearson IC（保留用于向后兼容，新逻辑用 _rank_ic）"""
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    return max(-1.0, min(1.0, cov / (vx * vy)))


def _rank_ic(xs: List[float], ys: List[float]) -> Optional[float]:
    """Rank IC（Spearman）— 对异常值更鲁棒，加密重尾分布下更可靠。

    [P0-1 2026-07-30] 替代 _pearson 作为主IC计算方法。
    文献依据：Alphalens/qlib标准用Rank IC；加密收益重尾分布(1909.04903)。
    """
    n = len(xs)
    if n < 30:  # 与MIN_SAMPLES对齐
        return None
    # 手动实现Spearman（避免scipy依赖）
    def _rank(values):
        sorted_vals = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(sorted_vals):
            j = i
            while j + 1 < len(sorted_vals) and values[sorted_vals[j + 1]] == values[sorted_vals[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[sorted_vals[k]] = avg_rank
            i = j + 1
        return ranks

    rx = _rank(xs)
    ry = _rank(ys)
    return _pearson(rx, ry)


def run_factor_ic_evaluation(db, lookback_days: int = 30) -> Dict[str, dict]:
    """
    评估各因子近 lookback_days 天的方向胜率与 IC：
      1. 写 factor_performance_logs（AnalyticsBase 留痕）
      2. 产出运行时权重文件 data/factor_runtime_weights.json

    [P0-1 2026-07-30] 修复IC计算bug：改为取全部历史样本（不只取最近一批），
    改用Rank IC（Spearman）替代Pearson，min_samples从8提高到30。

    Returns: {factor_name: {n, win_rate, ic, weight}}
    """
    from backend.database.models import SignalTradeFeedback
    from backend.database.connection import release_idle_txn

    # [P0-1] 取全部已配对样本（不只取lookback_days天的）
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = (
        db.query(SignalTradeFeedback)
        .filter(
            SignalTradeFeedback.signal_type.like("factor:%"),
            SignalTradeFeedback.trade_pnl.isnot(None),
            SignalTradeFeedback.created_at >= cutoff.replace(tzinfo=None),
        )
        .all()
    )
    # [2026-09-07] 快照后立即结束只读事务：下方 glob 扫因子名 + Rank IC 可达
    # 数分钟，旧实现整段 idle-in-transaction → LeakGuard 点名本函数。
    snaps = [
        (
            str(r.signal_type or ""),
            float(r.signal_value or 0),
            float(r.trade_pnl or 0),
            (r.trade_side or "").lower(),
            getattr(r, "created_at", None),
        )
        for r in rows
    ]
    release_idle_txn(db, where="factor_ic_eval.pre_compute")
    if not snaps:
        logger.info("[FactorIC] 无已配对的因子-盈亏样本，跳过本轮评估")
        return {}

    # 按因子聚合 (方向, 多头等效收益, 日期)
    samples: Dict[str, List[Tuple[float, float, object]]] = {}
    _known = _resolvable_factor_names()
    _truncated: Dict[str, int] = {}
    # 截断名 → 全名（或 None）的缓存：43 个截断名只解析 43 次，而非 4.5 万行
    # 每行都对 1287 个已知名做 startswith。
    _recover_cache: Dict[str, Optional[str]] = {}
    _recovered: Dict[str, int] = {}
    for signal_type, signal_value, pnl, side, created in snaps:
        factor_name = signal_type[len("factor:"):] if signal_type.startswith("factor:") else signal_type
        if not factor_name:
            continue
        # [2026-09-02] 处理 28 字符截断残留。写入侧已于 08-29 修好，但 lookback
        # 窗口（默认 30-45 天）内的历史行每轮都会把截断名重新写进权重文件——
        # 实测 data/factor_runtime_weights.json 有 49 个这类孤儿 key（占 ai_gen*
        # 键的 43%），它们既查不到又挤占权重表可读性，且让 slimming 审计误判。
        # 能唯一还原的记回全名（见 _recover_truncated_name），其余剔除。
        if _is_legacy_truncated_name(factor_name, _known):
            if factor_name not in _recover_cache:
                _recover_cache[factor_name] = _recover_truncated_name(factor_name, _known)
            _full = _recover_cache[factor_name]
            if _full is None:
                _truncated[factor_name] = _truncated.get(factor_name, 0) + 1
                continue
            _recovered[factor_name] = _recovered.get(factor_name, 0) + 1
            factor_name = _full
        direction = _map_factor_direction(factor_name, float(signal_value or 0))
        if abs(direction) < NEUTRAL_DIRECTION_EPS:
            continue  # 中性信号不参与方向评估
        long_equiv = pnl if side in ("long", "buy") else -pnl
        samples.setdefault(factor_name, []).append((direction, long_equiv, created))

    if _recovered:
        logger.info(
            "[FactorIC] 唯一前缀还原 %d 个历史截断因子名（%d 行样本归回全名）",
            len(_recovered), sum(_recovered.values()),
        )
    if _truncated:
        logger.info(
            "[FactorIC] 跳过 %d 个无法还原的截断因子名（%d 行样本，多义或因子已删）: %s",
            len(_truncated), sum(_truncated.values()),
            ", ".join(sorted(_truncated)[:6]),
        )

    results: Dict[str, dict] = {}
    weights: Dict[str, float] = {}
    # [2026-09-07 AlphaForge] 先按日聚合各因子 IC 时序，供动态组合回归用。
    daily_ic: Dict[str, List[Tuple[str, float]]] = {}
    for name, pairs in samples.items():
        n = len(pairs)
        if n < 3:
            continue
        wins = sum(1 for d, p, _c in pairs if (d > 0 and p > 0) or (d < 0 and p < 0))
        win_rate = wins / n
        ic = _rank_ic([d for d, _, _c in pairs], [p for _, p, _c in pairs])
        # 日度 IC 时序（按 created 日期分组）
        by_day: Dict[str, List[Tuple[float, float]]] = {}
        for d, p, c in pairs:
            day = str(c)[:10] if c else "unknown"
            if day != "unknown":
                by_day.setdefault(day, []).append((d, p))
        dic = []
        for day in sorted(by_day):
            dp = by_day[day]
            if len(dp) >= 3:
                d_ic = _rank_ic([x for x, _ in dp], [y for _, y in dp])
                if d_ic is not None:
                    dic.append((day, d_ic))
        if dic:
            daily_ic[name] = dic

        # [2026-09-02 G19] 默认值由 1.0 改为未验证地板：下面 ic_ev 分支的注释
        # 本意是"IC=null（样本不足）→ 中性 0.1"，但 n<MIN_SAMPLES 时压根进不了
        # 该分支（_rank_ic 对 n<30 直接返回 None），于是样本不足的因子一路保持
        # 满权 1.0。详见 _unverified_weight()。
        weight = _unverified_weight()
        if n >= MIN_SAMPLES:
            # [2026-08-29 全面修复 P2.2] 权重映射从"胜率分档"改为"符号一致 × IC"：
            # 旧口径只看 win_rate 分档（<40%→0.25/<45%→0.5/>60%→1.2），不看 IC
            # 符号——实测 ai_gen_dust_squeeze IC=-0.2963 仍拿权重 1.0（胜率恰在
            # 45-60% 带即满权）。新口径（FACTOR_IC_WEIGHT_MODE=ic_ev，默认）：
            #   - IC<=0（方向与收益无正相关/反向）或方向一致率<45% → 地板 0.1；
            #   - 否则 w = clip(0.5 + 4×IC, 0.1, 1.5)，IC=0.05→0.7 / 0.1→0.9 /
            #     0.25→1.5（ICIR 优秀的反手因子此前因胜率<45%被砍半，现按
            #     方向一致率+IC 正常给权）。
            # 旧口径可用 FACTOR_IC_WEIGHT_MODE=winrate 回滚。
            _ic_mode = (os.getenv("FACTOR_IC_WEIGHT_MODE", "ic_ev") or "ic_ev").strip().lower()
            if _ic_mode == "winrate":
                if win_rate < 0.40:
                    weight = 0.25
                elif win_rate < 0.45:
                    weight = 0.5
                elif win_rate > 0.60:
                    weight = 1.2
            else:
                # [2026-08-31 学习层审计 L4] 负 IC 因子（方向与收益负相关/零相关）
                # 不再占 0.1 地板权重——实测 ai_gen_dust_squeeze IC=-0.3353 仍拿
                # 0.1 参与合成，污染信号。改为：IC<=0 → 权重 0（不参与）；
                # IC=null（样本不足）→ 中性 0.1；win_rate<0.45 但 IC>0 → 地板 0.1。
                if ic is not None and ic <= 0:
                    weight = 0.0
                elif ic is None or win_rate < 0.45:
                    weight = _EV_WEIGHT_FLOOR
                else:
                    # [2026-09-02 G19] 裸 IC → 按样本量收缩的 IC，见 _ic_shrink_k()
                    _k = _ic_shrink_k()
                    _ic_eff = ic * (n / (n + _k)) if _k > 0 else ic
                    weight = float(min(1.5, max(_EV_WEIGHT_FLOOR,
                                                0.5 + 4.0 * _ic_eff)))
        weights[name] = weight
        results[name] = {
            "n": n,
            "win_rate": round(win_rate, 4),
            "ic": round(ic, 4) if ic is not None else None,
            "weight": weight,
        }
        # [2026-09-02] 把真实 Rank IC 喂给因子衰减监控。此前该监控唯一的数据源是
        # paper_trading_engine 平仓时写的 f"strategy_{symbol}" + ±0.05 占位常数，
        # 对真实因子零覆盖（状态文件里只有 3 个 strategy_* 键），
        # get_factor_weight_penalty(因子ID) 永远 miss、衰减惩罚从未生效。
        if ic is not None:
            try:
                from backend.services.factor_engine.factor_decay_monitor import (
                    decay_monitor,
                )
                decay_monitor.record_ic(name, float(ic))
            except Exception as err:
                logger.debug("[FactorIC] 衰减监控记录失败 %s: %s", name, err)

    if not results:
        logger.info("[FactorIC] 有效样本不足，跳过权重更新")
        return {}

    # ── [2026-09-07 AlphaForge 动态组合] 滚动窗线性回归预测当日权重 ──
    # 规则式 ic_ev 是「历史平均 IC → 固定映射」，权重对 regime 切换反应慢。
    # AlphaForge(AAAI'25)：每日用近 N 天 IC 时序重排因子，拟合其近期表现
    # 预测当日权重。这里用指数衰减加权的近期 IC 动量做预测性调整：
    #   pred_ic = Σ w_t·IC_t（近期权重大）→ 在 ic_ev 基础上乘动量调整系数。
    # 默认叠加在 ic_ev 之上（FACTOR_IC_DYNAMIC_WEIGHT=1），=0 回滚纯规则。
    if (os.getenv("FACTOR_IC_DYNAMIC_WEIGHT", "1") or "1").strip().lower() not in ("0", "false", "off"):
        try:
            import math as _math
            _half_life = float(os.getenv("FACTOR_IC_DYNAMIC_HALFLIFE_D", "7") or 7)
            _lam = _math.log(2) / max(1.0, _half_life)
            for name, dic in daily_ic.items():
                if name not in results or len(dic) < 5:
                    continue
                # 指数衰减加权的近期 IC 均值（预测下一期表现）
                m = len(dic)
                ws = [_math.exp(-_lam * (m - 1 - i)) for i in range(m)]
                sw = sum(ws)
                pred_ic = sum(w * v for w, (_, v) in zip(ws, dic)) / sw if sw else 0.0
                base_ic = results[name].get("ic")
                if base_ic is None or base_ic <= 0:
                    continue
                # 动量调整：近期 pred_ic 强于长期 base_ic → 上调；弱 → 下调。
                # 系数限制在 [0.7, 1.3]，避免单日噪声主导权重。
                ratio = pred_ic / (abs(base_ic) + 1e-9)
                adj = min(1.3, max(0.7, 0.5 + 0.5 * ratio))
                old_w = weights[name]
                weights[name] = float(min(1.5, max(0.0, old_w * adj)))
                results[name]["pred_ic"] = round(pred_ic, 4)
                results[name]["dyn_adj"] = round(adj, 3)
        except Exception as _dyn_err:
            logger.debug("[FactorIC] 动态权重调整跳过: %s", _dyn_err)

    # ── 留痕 factor_performance_logs（AnalyticsBase）──
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.database.models import FactorPerformanceLog

        ana = AnalyticsSessionLocal()
        try:
            for name, stat in results.items():
                ana.add(FactorPerformanceLog(
                    factor_name=name[:50],
                    factor_category="composite_v3",
                    ic_value=stat["ic"],
                    decay_rate=None,
                    current_weight=stat["weight"],
                    market_regime=None,
                    symbol=None,
                    timeframe="15m",
                ))
            ana.commit()
        finally:
            ana.close()
    except Exception as err:
        logger.warning(f"[FactorIC] factor_performance_logs 写入失败: {err}")

    # ── 产出运行时权重 ──
    try:
        os.makedirs(os.path.dirname(RUNTIME_WEIGHTS_FILE), exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": lookback_days,
            "weights": weights,
            "stats": results,
        }
        with open(RUNTIME_WEIGHTS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        _weights_cache["ts"] = 0.0  # 失效缓存，下次读取拿新权重
        # [2026-09-07 ReCAP] 把本轮权重按当前 regime 存进策略库（EMA 融合），
        # 供 regime-gate 门控组合。score 用本轮因子平均正 IC 作为表现分。
        try:
            from backend.services.regime_policy_library import record_regime_weights
            _ics = [s.get("ic") for s in results.values() if s.get("ic") is not None and s.get("ic") > 0]
            _score = (sum(_ics) / len(_ics)) if _ics else 0.5
            record_regime_weights(_current_market_regime(), weights, score=max(0.2, min(1.0, _score * 10)))
        except Exception:
            pass
    except Exception as err:
        logger.warning(f"[FactorIC] 运行时权重写入失败: {err}")

    downweighted = [k for k, v in weights.items() if v < 1.0]
    logger.info(
        f"[FactorIC] 评估完成: {len(results)} 个因子, "
        f"降权 {len(downweighted)} 个 {downweighted[:8]}"
    )
    return results


# nature → 代表性时间框架（用于 factor_performance_logs 标签与分流展示）
_NATURE_TF = {"scalp": "15m", "swing": "4h", "trend_follow": "1d", "position": "1d"}


def run_factor_ic_evaluation_segmented(db, lookback_days: int = 45) -> Dict[str, dict]:
    """按成交性质(scalp/swing/trend)分流评估因子 IC（S4-C）。

    把 `factor:%` 反馈行按其 trade_id 对应持仓的 `trade_nature` 分桶，分别计算方向
    胜率与 IC，写入 factor_performance_logs（timeframe 用 nature 代表周期），并返回
    分段统计供中长线健康视图消费。不改动全局运行时权重文件（避免与主评估冲突）。

    Returns: {nature: {factor_name: {n, win_rate, ic}}}
    """
    from backend.database.models import SignalTradeFeedback, PaperPosition
    from backend.database.connection import release_idle_txn

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = (
        db.query(SignalTradeFeedback)
        .filter(
            SignalTradeFeedback.signal_type.like("factor:%"),
            SignalTradeFeedback.trade_pnl.isnot(None),
            SignalTradeFeedback.trade_id.isnot(None),
            SignalTradeFeedback.created_at >= cutoff.replace(tzinfo=None),
        )
        .all()
    )
    snaps = [
        (
            str(r.signal_type or ""),
            float(r.signal_value or 0),
            float(r.trade_pnl or 0),
            (r.trade_side or "").lower(),
            int(r.trade_id) if r.trade_id is not None else None,
        )
        for r in rows
    ]
    release_idle_txn(db, where="factor_ic_seg.pre_nature")
    if not snaps:
        return {}

    # trade_id → trade_nature 映射（一次查全，避免 N+1）
    trade_ids = list({tid for *_, tid in snaps if tid is not None})
    nature_by_tid: Dict[int, str] = {}
    for i in range(0, len(trade_ids), 500):
        chunk = trade_ids[i:i + 500]
        try:
            for pid, nat in (
                db.query(PaperPosition.id, PaperPosition.trade_nature)
                .filter(PaperPosition.id.in_(chunk))
                .all()
            ):
                nature_by_tid[int(pid)] = (nat or "scalp").lower()
        except Exception as e:
            logger.debug(f"[FactorIC-Seg] 持仓性质查询跳过: {e}")
    release_idle_txn(db, where="factor_ic_seg.pre_compute")

    # (nature, factor) → [(direction, long_equiv_pnl)]
    seg: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
    _known = _resolvable_factor_names()
    _recover_cache: Dict[str, Optional[str]] = {}
    for signal_type, signal_value, pnl, side, trade_id in snaps:
        factor_name = signal_type[len("factor:"):] if signal_type.startswith("factor:") else signal_type
        if not factor_name:
            continue
        # 与主评估同口径：截断名能唯一还原则归回全名，否则剔除
        if _is_legacy_truncated_name(factor_name, _known):
            if factor_name not in _recover_cache:
                _recover_cache[factor_name] = _recover_truncated_name(factor_name, _known)
            factor_name = _recover_cache[factor_name]
            if factor_name is None:
                continue
        nat = nature_by_tid.get(int(trade_id), "scalp") if trade_id is not None else "scalp"
        bucket = "trend_follow" if nat in ("trend_follow", "position") else (
            "swing" if nat == "swing" else "scalp"
        )
        direction = _map_factor_direction(factor_name, float(signal_value or 0))
        if abs(direction) < NEUTRAL_DIRECTION_EPS:
            continue
        long_equiv = pnl if side in ("long", "buy") else -pnl
        seg.setdefault(bucket, {}).setdefault(factor_name, []).append((direction, long_equiv))

    out: Dict[str, dict] = {}
    logs: List[Tuple[str, str, Optional[float]]] = []  # (factor, timeframe, ic)
    for bucket, factors in seg.items():
        out[bucket] = {}
        tf = _NATURE_TF.get(bucket, "15m")
        for name, pairs in factors.items():
            n = len(pairs)
            if n < 3:
                continue
            wins = sum(1 for d, p in pairs if (d > 0 and p > 0) or (d < 0 and p < 0))
            ic = _rank_ic([d for d, _ in pairs], [p for _, p in pairs])
            out[bucket][name] = {
                "n": n,
                "win_rate": round(wins / n, 4),
                "ic": round(ic, 4) if ic is not None else None,
            }
            logs.append((name, tf, ic))

    # 留痕 factor_performance_logs（按 nature 代表周期打 timeframe 标签）
    if logs:
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.database.models import FactorPerformanceLog
            ana = AnalyticsSessionLocal()
            try:
                for name, tf, ic in logs:
                    ana.add(FactorPerformanceLog(
                        factor_name=name[:50],
                        factor_category="segmented_ic",
                        ic_value=ic,
                        decay_rate=None,
                        current_weight=None,
                        market_regime=None,
                        symbol=None,
                        timeframe=tf,
                    ))
                ana.commit()
            finally:
                ana.close()
        except Exception as err:
            logger.warning(f"[FactorIC-Seg] 分段留痕失败: {err}")

    logger.info(
        "[FactorIC-Seg] 分流评估: "
        + ", ".join(f"{k}={len(v)}因子" for k, v in out.items())
    )
    return out
