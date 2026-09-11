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
            return max(0.3, status.current_ic / max(status.historical_ic, 0.001))
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
