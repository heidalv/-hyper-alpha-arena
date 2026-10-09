"""
D7: Factor Decay Monitor — 因子衰减监控 + 自动淘汰

定期评估每个因子的预测能力，自动降权或淘汰衰减因子。
配合 FactorSelector 的 IC 计算和 genetic_optimizer 的权重进化使用。
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DecayStatus:
    factor_id: str
    current_ic: float           # 最近IC均值
    historical_ic: float         # 历史IC均值
    decay_rate: float            # 衰减速率 (正=改善, 负=衰减)
    half_life_days: float        # 半衰期（天）
    trend: str                   # "improving" / "stable" / "declining" / "dead"
    recommendation: str          # "keep" / "reduce" / "retire"
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FactorDecayMonitor:
    """因子衰减监控器 — 单例"""
    
    _instance = None
    
    DECAY_THRESHOLDS = {
        "retire_ic": 0.01,        # IC 低于此值自动退役
        "reduce_ic": 0.03,        # IC 低于此值降权
        "decline_rate": -0.02,    # 月衰减率低于此 = declining
        "check_interval_days": 7, # 每7天全面检查一次
    }
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    # [P0-2] 状态持久化路径：重启后恢复 penalty，避免重启清零导致衰减因子满权重复活
    # [2026-09-02] 相对路径改为基于 __file__：原写法依赖进程 cwd，从非仓库根启动
    # 时读不到已有状态（penalty 静默归零 = 衰减因子满权复活，正是 P0-2 要防的），
    # 且会在错误目录另写一份状态文件。
    STATUS_PATH = str(
        Path(__file__).resolve().parents[3] / "data" / "factor_decay_status.json")

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._ic_history: Dict[str, List[float]] = {}  # factor_id → [ic1, ic2, ...]
        self._decay_status: Dict[str, DecayStatus] = {}
        self._last_full_check: Optional[datetime] = None
        self._load_status()
        logger.info("[DecayMonitor] 因子衰减监控初始化")
    
    # 非因子键前缀。策略/品种维度的盈亏不构成因子 IC，混入后会顶替真实因子占位。
    _NON_FACTOR_PREFIXES = ("strategy_",)

    @classmethod
    def _is_factor_key(cls, key: str) -> bool:
        """判定一个 key 是否是合法因子 ID。

        写入侧（record_ic）与读取侧（_load_status）必须共用同一把尺子 ——
        [2026-09-02] 只在 record_ic 做校验时，堵住了新污染但清不掉存量：
        启动 _load_status 把历史 strategy_* 键原样读回内存，下一次
        _save_status 再原样写出，文件永远自我延续。实测重启后
        factor_decay_status.json 的 saved_at 刷新成重启时刻，内容仍是那三个
        strategy_ASTER/BTC/XPL（全 trend=dead），真实因子零覆盖。
        """
        k = str(key or "").strip()
        if not k:
            return False
        return not k.startswith(cls._NON_FACTOR_PREFIXES)

    def record_ic(self, factor_id: str, ic: float):
        """记录一次 IC 值（由因子 IC 评估器在算出真实 Rank IC 后调用）。

        factor_id 必须是**因子** ID/名，不能是策略或品种标识。
        [2026-09-02] 此前 paper_trading_engine 在平仓时按 f"strategy_{symbol}"
        记录固定占位 IC(±0.05)，导致：① 状态文件里只有 strategy_BTC 这类键，
        get_factor_weight_penalty(真实因子ID) 永远查不到、恒返回 1.0，衰减惩罚
        从未生效；② 盈亏各半时 recent≈0 < retire_ic(0.01)，三个策略键全被判
        dead/retire。单笔平仓本就算不出 IC（单点相关无定义），真实 IC 只能由
        run_factor_ic_evaluation 按因子聚合样本得出。
        """
        _fid = str(factor_id or "").strip()
        if not _fid:
            return
        if not self._is_factor_key(_fid):
            logger.warning(
                "[DecayMonitor] 拒绝非因子键 %s：record_ic 只接受因子 ID，"
                "策略/品种维度的盈亏统计不属于因子衰减监控", _fid,
            )
            return
        try:
            _ic = float(ic)
        except (TypeError, ValueError):
            return
        if _ic != _ic:  # NaN
            return
        if _fid not in self._ic_history:
            self._ic_history[_fid] = []
        self._ic_history[_fid].append(_ic)
        # 保留最近 100 次记录
        if len(self._ic_history[_fid]) > 100:
            self._ic_history[_fid] = self._ic_history[_fid][-100:]
    
    def evaluate_factor(self, factor_id: str) -> DecayStatus:
        """评估单个因子的衰减状态"""
        history = self._ic_history.get(factor_id, [])
        
        if len(history) < 20:
            return DecayStatus(
                factor_id=factor_id,
                current_ic=sum(history[-10:]) / max(len(history[-10:]), 1) if history else 0,
                historical_ic=sum(history) / len(history) if history else 0,
                decay_rate=0,
                half_life_days=999,
                trend="stable",
                recommendation="keep",
            )
        
        # 最近10次 vs 全部历史
        recent = sum(history[-10:]) / 10
        all_time = sum(history) / len(history)
        decay_rate = (recent - all_time) / max(abs(all_time), 0.001)
        
        # 半衰期估算
        if decay_rate < 0:
            half_life = -0.693 / decay_rate  # 简单的指数衰减
        else:
            half_life = 999
        
        # 判断趋势
        if recent < self.DECAY_THRESHOLDS["retire_ic"]:
            trend = "dead"
            recommendation = "retire"
        elif recent < self.DECAY_THRESHOLDS["reduce_ic"]:
            trend = "declining"
            recommendation = "reduce"
        elif decay_rate < self.DECAY_THRESHOLDS["decline_rate"]:
            trend = "declining"
            recommendation = "reduce"
        elif decay_rate > 0.02:
            trend = "improving"
            recommendation = "keep"
        else:
            trend = "stable"
            recommendation = "keep"
        
        status = DecayStatus(
            factor_id=factor_id,
            current_ic=round(recent, 4),
            historical_ic=round(all_time, 4),
            decay_rate=round(decay_rate, 4),
            half_life_days=round(half_life, 1),
            trend=trend,
            recommendation=recommendation,
        )
        
        self._decay_status[factor_id] = status
        return status
    
    def evaluate_all_factors(self) -> Dict[str, DecayStatus]:
        """评估所有因子"""
        results = {}
        for fid in self._ic_history:
            results[fid] = self.evaluate_factor(fid)
        self._last_full_check = datetime.now(timezone.utc)

        retired = [fid for fid, s in results.items() if s.recommendation == "retire"]
        reduced = [fid for fid, s in results.items() if s.recommendation == "reduce"]

        if retired:
            logger.warning(f"[DecayMonitor] {len(retired)}个因子建议退役: {retired}")
        if reduced:
            logger.info(f"[DecayMonitor] {len(reduced)}个因子建议降权: {reduced}")

        # [P0-2] 持久化：重启后 penalty 不归零（此前 _decay_status 仅内存态）
        self._save_status()
        return results

    def apply_incremental_pnl(self, contributions: Dict[str, float],
                              threshold: float = -0.002) -> Dict[str, int]:
        """[统一策略 2026-09-18] 增量PnL归因桥 —— 因子战绩接入治理。

        根因：SignalFeedbackTracker 的逐单增量归因（因子活跃期 avgPnL − 全局）能抓到
        "IC 不差但实战持续亏钱"的因子（如 evo_3d51976a92a07bea -0.44%），而 IC 衰减
        监视器抓不到 → 最差因子满权重跑实盘。本桥把持续负贡献写入衰减状态：
        inc ≤ threshold*2 → retire（权重0），threshold~2× → reduce（权重≤0.3）。
        只对已评估因子或明确负贡献的新键生效；evidence: 86432 样本归因。
        """
        from dataclasses import replace as _dc_replace

        import os as _os

        # [F336 2026-09-18 复查修复·断路器] 接线前的前置条件。
        # 现场：`data/factor_decay_status.json` 实测 128 条里 **57 条（44.5%）已归零**
        # （retire 且双确认命中），而该惩罚当前**只被已停用的短线车道消费** ⇒ 一旦把惩罚
        # 接入中长线（`mlto/quant_layer.py:18`）就会按现状一次性砍掉 44.5% 的因子池。
        # 本断路器：单轮"将归零"占比超过上限即**拒绝批量归零**（降级为 reduce/0.3）并留痕；
        # 逃生阀 `FACTOR_DECAY_ALLOW_MASS_RETIRE=1`（默认关）。
        try:
            _max_share = float(_os.getenv("FACTOR_DECAY_MASS_RETIRE_MAX_SHARE", "0.30"))
        except Exception:
            _max_share = 0.30
        _allow_mass = str(_os.getenv("FACTOR_DECAY_ALLOW_MASS_RETIRE", "0")).strip().lower() in (
            "1", "true", "yes", "on")
        try:
            _retire_ic = float(self.DECAY_THRESHOLDS.get("retire_ic", 0.01))
        except Exception:
            _retire_ic = 0.01
        # 新建档时播种的 historical_ic：**不得**低于 retire 阈值，否则"双确认"自动成立、
        # 首次触发的因子会被直接归零（这正是 57 条归零的根因，见 :221-225 原实现）。
        _seed_hist = max(_retire_ic * 2.0, 0.02)

        # —— 阶段一：先规划（不落库），统计"本次将归零"的占比 ——
        _plan = []
        for fid, inc in (contributions or {}).items():
            if not fid or inc > threshold:
                continue
            _new_rec = "retire" if inc <= threshold * 2 else "reduce"
            _st = self._decay_status.get(fid)
            _hist = _seed_hist
            if _st is not None:
                try:
                    _hist = float(getattr(_st, "historical_ic", 0.0) or 0.0)
                except Exception:
                    _hist = 0.0
            _zeroing = bool(_new_rec == "retire" and _hist < _retire_ic)
            _plan.append({"fid": fid, "rec": _new_rec, "st": _st, "zero": _zeroing})

        _pool = max(1, len(self._decay_status))
        _zero_n = sum(1 for p in _plan if p["zero"])
        _share = _zero_n / _pool
        _blocked = bool(_zero_n > 0 and _share > _max_share and not _allow_mass)
        if _blocked:
            logger.warning(
                "[DecayMonitor][断路器] 本轮将归零 %d/%d = %.1f%%（上限 %.0f%%）⇒ **拒绝批量归零**，"
                "降级为 reduce(0.3)。确需批量归零请设 FACTOR_DECAY_ALLOW_MASS_RETIRE=1",
                _zero_n, _pool, _share * 100, _max_share * 100)
            for p in _plan:
                if p["zero"]:
                    p["rec"] = "reduce"
                    p["zero"] = False
                    # [F336 修正] 仅把 recommendation 降级为 reduce **不足以**产生惩罚：
                    # `get_factor_weight_penalty` 的 reduce 分支算的是 current_ic/historical_ic，
                    # 而这类因子 historical_ic=0.0 ⇒ 比值被 max(...,0.001) 放大、再被 F330 的
                    # 1.0 上限截断 ⇒ **权重变成 1.0（完全无惩罚）**，断路器等于失效 ✗。
                    # 故打上显式标记，由 get_factor_weight_penalty 固定返回 0.3（不改写 IC 证据）。
                    p["breaker"] = True

        # —— 阶段二：应用 ——
        acted = {"retire": 0, "reduce": 0}
        for p in _plan:
            fid, new_rec, st = p["fid"], p["rec"], p["st"]
            if st is not None:
                if new_rec == "retire" and st.recommendation != "retire":
                    acted["retire"] += 1
                elif new_rec == "reduce" and st.recommendation not in ("retire", "reduce"):
                    acted["reduce"] += 1
                self._decay_status[fid] = _dc_replace(
                    st, recommendation=new_rec,
                    trend=("breaker_downgrade" if p.get("breaker")
                           else ("dead" if new_rec == "retire" else st.trend)))
            else:
                # 未入衰减评估的因子（如 evo_/ai_gen 系）按战绩直接建档。
                # [F336] `historical_ic` 播种为 _seed_hist（> retire 阈值）——原来播 0.0，
                # 会让 `get_factor_weight_penalty` 的"双确认"自动成立 ⇒ 首触即归零。
                self._decay_status[fid] = DecayStatus(
                    factor_id=fid, current_ic=0.0, historical_ic=_seed_hist,
                    decay_rate=0.0, half_life_days=0.0,
                    trend="dead" if new_rec == "retire" else "declining",
                    recommendation=new_rec)
                acted[new_rec] += 1
        if any(acted.values()):
            self._save_status()
            logger.warning(
                "[DecayMonitor][增量PnL桥] 战绩治理生效: retire=%d reduce=%d（阈值%.2f%%）%s",
                acted["retire"], acted["reduce"], threshold * 100,
                "［断路器已降级批量归零］" if _blocked else "")
        return acted

    def get_factor_weight_penalty(self, factor_id: str) -> float:
        """返回因子权重惩罚系数 (1.0=无惩罚, 0.0=完全淘汰)"""
        status = self._decay_status.get(factor_id)
        if not status:
            return 1.0

        if status.recommendation == "retire":
            # [P0-2 双确认] recent 与 historical 同时低于退役阈值才归零；
            # 防止单次误评（噪声窗口）把因子权重直接打到 0。
            if status.historical_ic < self.DECAY_THRESHOLDS["retire_ic"]:
                return 0.0
            return 0.3
        elif status.recommendation == "reduce":
            # [F336] 断路器降级标记 ⇒ 固定 0.3（不改写 IC 证据；见 apply_incremental_pnl 内注释）
            if getattr(status, "trend", "") == "breaker_downgrade":
                return 0.3
            # [F330 2026-09-18 复查修复] 原为 `return max(0.3, current_ic / max(historical_ic, 0.001))`
            # —— **缺少 1.0 上限** ⇒ 当 historical_ic 落在地板 0.001 而 current_ic 较大时，
            # 比值会 **>1**，被当作"惩罚系数"相乘 ⇒ **降权变成放大**。
            # 实测（第 5 路复查）：`ai_gen_bsq` penalty=**12.4**、`supertrend`=**1.364**
            # ⇒ 被判定"衰减需降权"的因子反被放大最多 12 倍，直接污染信号加权。
            # 修法：加上限 1.0（惩罚系数语义 = 只降不升），下限保持 0.3。
            return min(1.0, max(0.3, status.current_ic / max(status.historical_ic, 0.001)))
        else:
            return 1.0

    # ── [P0-2] 状态持久化（data/factor_decay_status.json）──

    # 状态文件格式版本。v2 起同时持久化 _ic_history（见 _load_status 说明）。
    _STATUS_VERSION = 2

    def _load_status(self) -> None:
        try:
            with open(self.STATUS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            # [2026-09-02] v2 格式：额外恢复 _ic_history。P0-2 只持久化了
            # _decay_status，而 evaluate_factor 需要 ≥20 条 IC 历史才给出非
            # "stable/keep" 的判定——历史是纯内存态，每次重启清零后要重新累积
            # 20 轮评估（按日频=20 天）才能重新生效，衰减监控实际长期空转。
            if "ic_history" in raw or "status" in raw:
                _status_raw = raw.get("status") or {}
                for fid, vals in (raw.get("ic_history") or {}).items():
                    if not self._is_factor_key(fid):
                        continue
                    try:
                        _hist = [float(v) for v in (vals or [])][-100:]
                    except (TypeError, ValueError):
                        continue
                    if _hist:
                        self._ic_history[str(fid)] = _hist
            else:
                _status_raw = raw  # v1：顶层直接是 factor_id → status

            # 存量清理：过滤非因子键，并在确有污染时立刻回写一次，
            # 否则这批键会在下一次 _save_status 被原样续命（详见 _is_factor_key）。
            _dropped = [k for k in _status_raw if not self._is_factor_key(k)]
            for fid, d in _status_raw.items():
                if not self._is_factor_key(fid):
                    continue
                self._decay_status[fid] = DecayStatus(
                    factor_id=fid,
                    current_ic=float(d.get("current_ic", 0) or 0),
                    historical_ic=float(d.get("historical_ic", 0) or 0),
                    decay_rate=float(d.get("decay_rate", 0) or 0),
                    half_life_days=float(d.get("half_life_days", 999) or 999),
                    trend=str(d.get("trend", "stable")),
                    recommendation=str(d.get("recommendation", "keep")),
                )
            logger.info(
                "[DecayMonitor] 恢复 %d 个因子衰减状态、%d 个因子 IC 历史",
                len(self._decay_status), len(self._ic_history),
            )
            if _dropped:
                logger.warning(
                    "[DecayMonitor] 已清理 %d 个历史非因子键并回写状态文件: %s",
                    len(_dropped), _dropped[:10],
                )
                self._save_status()
        except FileNotFoundError:
            pass
        except Exception as e:
            # 加载失败 = penalty 归零 = 衰减因子满权复活（P0-2 要防的正是这个），
            # 不能只记 debug。
            logger.warning("[DecayMonitor] 状态加载失败，衰减惩罚将从零累积: %s", e)

    def _save_status(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.STATUS_PATH) or ".", exist_ok=True)
            payload = {
                "_meta": {
                    "version": self._STATUS_VERSION,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                },
                "status": {
                    fid: {
                        "current_ic": s.current_ic,
                        "historical_ic": s.historical_ic,
                        "decay_rate": s.decay_rate,
                        "half_life_days": s.half_life_days,
                        "trend": s.trend,
                        "recommendation": s.recommendation,
                    }
                    for fid, s in self._decay_status.items()
                },
                "ic_history": {
                    fid: [round(float(v), 6) for v in hist[-100:]]
                    for fid, hist in self._ic_history.items() if hist
                },
            }
            with open(self.STATUS_PATH, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("[DecayMonitor] 状态保存失败: %s", e)


# 全局单例
decay_monitor = FactorDecayMonitor()
