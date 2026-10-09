# -*- coding: utf-8 -*-
"""ExitPolicy — 每条车道**声明一份**出场策略，三重屏障收敛到一处评估（v3 方向 1，p1-trend-engine，2026-09-03）。

背景（方案第一节"止损止盈"）：paper 出场栈 9 层（_run_v2_protection → D6 → 附着 TP/SL → DSM → 统一分档），
live 只有附着 TP/SL 替换；没有时间递减 ROI，没有结构失效**价格**，trailing 是软件改 SL。对标 Hummingbot 三重屏障
（TP / SL / time_limit + trailing 激活/回撤）与 Freqtrade minimal_roi（时间递减 ROI）。

本模块做三件事（纯函数，无 DB / 无 LLM）：

  1. ExitPolicy.for_lane(lane)   车道默认 + 环境变量覆盖（EXIT_POLICY_<LANE>_<PARAM>），
                                  开仓时快照进 exit_state_json["exit_policy"]，持仓终身按声明执行。
  2. evaluate(policy, snap)       固定优先级评估一根 tick：
                                  结构失效价 → 硬 SL → 硬 TP → time_limit → 时间递减 ROI → trailing 回撤/收紧 → hold
                                  返回 ExitVerdict(action ∈ {close, tighten_sl, hold}, reason, new_sl)。
  3. 与既有栈的关系                paper_trading_engine._run_v2_protection 在硬层（max_hold/SL/TP/liq）之后、
                                  利润管理器之前调用一次 evaluate；命中 close/tighten_sl 即执行。
                                  硬 SL/TP 在引擎里仍先行判定（幂等：本模块再判一次只是收敛口径）。
                                  长线（trend）车道：结构失效价 = long_trend_v2 维护的 Chandelier（写在 pos.sl_price），
                                  所以 structural_stop="chandelier" 时本模块不重复算 Chandelier，只声明"唯一出场 = L1/Chandelier"，
                                  不设 TP、不设 time_limit、不设时间递减 ROI（趋势要让利润奔跑）。

分档 TP（1/2/3 档 + 80% 安全网）保留在 _run_unified_staged_tp；本模块只**声明**档位（tp_stages，MFE P50/P70/P85 标定，
见 exit_policy_calibration()），供分档逻辑读取，不再手填百分比。

开关：EXIT_POLICY_ENFORCE（默认 true）。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

LANES = ("short", "mid", "long", "research", "arb")


def enforce_enabled() -> bool:
    return str(os.getenv("EXIT_POLICY_ENFORCE", "true")).strip().lower() in ("1", "true", "yes", "on")


def _env(key: str) -> Optional[str]:
    v = os.getenv(key)
    return v if v is not None and str(v).strip() != "" else None


def _env_f(lane: str, param: str, default: Optional[float]) -> Optional[float]:
    """EXIT_POLICY_<LANE>_<PARAM>；值 'none'/'off' → None。"""
    raw = _env(f"EXIT_POLICY_{lane.upper()}_{param.upper()}")
    if raw is None:
        return default
    if str(raw).strip().lower() in ("none", "off", "null", "-"):
        return None
    try:
        return float(raw)
    except Exception:
        return default


def _env_roi(lane: str, default: Sequence[Tuple[int, float]]) -> Tuple[Tuple[int, float], ...]:
    """EXIT_POLICY_<LANE>_MIN_ROI = "3600:0.4,7200:0.15,10800:0" （秒:最低ROI%）；'off' → 空。"""
    raw = _env(f"EXIT_POLICY_{lane.upper()}_MIN_ROI")
    if raw is None:
        return tuple((int(s), float(r)) for s, r in default)
    if str(raw).strip().lower() in ("none", "off", "null", "-"):
        return tuple()
    out: List[Tuple[int, float]] = []
    try:
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            s, r = part.split(":")
            out.append((int(float(s)), float(r)))
    except Exception:
        return tuple((int(s), float(r)) for s, r in default)
    return tuple(sorted(out))


@dataclass
class ExitPolicy:
    """一条车道的出场声明。所有百分比都是**价格**口径（不乘杠杆），单位 %（1.0 = 1%）。"""

    lane: str
    sl_pct: Optional[float]                 # 硬止损距离（None = 由车道自行给价，如 Chandelier）
    tp_pct: Optional[float]                 # 硬止盈距离（None = 不设，让利润奔跑）
    time_limit_sec: Optional[int]           # 第三屏障：最长持仓（None = 无）
    trailing_activation_pct: Optional[float]  # 浮盈达到该值后激活 trailing（None = 不用 trailing）
    trailing_callback_pct: Optional[float]    # 激活后从峰值回撤该值 → close
    structural_stop: str                    # none | chandelier（长线，价在 pos.sl_price）| price（exit_state_json.structural_stop_price）
    min_roi: Tuple[Tuple[int, float], ...]  # 时间递减 ROI：elapsed ≥ sec 且 roi% < min → close（空 = 关闭）
    tp_stages: Tuple[float, ...]            # 分档 TP 声明（%），按各车道 MFE P50/P70/P85 标定
    safety_net_cap: float = 0.80            # 已达 80% 目标利润后的安全网（沿用现栈）
    enabled: bool = True

    @classmethod
    def for_lane(cls, lane: str) -> "ExitPolicy":
        lane = (lane or "mid").strip().lower()
        if lane not in LANES:
            lane = "mid"
        d = _DEFAULTS[lane]
        tl = _env_f(lane, "TIME_LIMIT_SEC", d["time_limit_sec"])
        stages_raw = _env(f"EXIT_POLICY_{lane.upper()}_TP_STAGES")
        stages: Tuple[float, ...] = tuple(d["tp_stages"])
        if stages_raw:
            try:
                stages = tuple(float(x) for x in stages_raw.split(",") if x.strip())
            except Exception:
                pass
        return cls(
            lane=lane,
            sl_pct=_env_f(lane, "SL_PCT", d["sl_pct"]),
            tp_pct=_env_f(lane, "TP_PCT", d["tp_pct"]),
            time_limit_sec=int(tl) if tl is not None else None,
            trailing_activation_pct=_env_f(lane, "TRAILING_ACTIVATION_PCT", d["trailing_activation_pct"]),
            trailing_callback_pct=_env_f(lane, "TRAILING_CALLBACK_PCT", d["trailing_callback_pct"]),
            structural_stop=(_env(f"EXIT_POLICY_{lane.upper()}_STRUCTURAL_STOP") or d["structural_stop"]).lower(),
            min_roi=_env_roi(lane, d["min_roi"]),
            tp_stages=stages,
            safety_net_cap=float(_env_f(lane, "SAFETY_NET_CAP", 0.80) or 0.80),
            enabled=str(_env(f"EXIT_POLICY_{lane.upper()}_ENABLED") or "true").lower() in ("1", "true", "yes", "on"),
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["min_roi"] = [list(x) for x in self.min_roi]
        d["tp_stages"] = list(self.tp_stages)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ExitPolicy":
        return cls(
            lane=str(d.get("lane") or "mid"),
            sl_pct=d.get("sl_pct"),
            tp_pct=d.get("tp_pct"),
            time_limit_sec=(int(d["time_limit_sec"]) if d.get("time_limit_sec") is not None else None),
            trailing_activation_pct=d.get("trailing_activation_pct"),
            trailing_callback_pct=d.get("trailing_callback_pct"),
            structural_stop=str(d.get("structural_stop") or "none"),
            min_roi=tuple((int(x[0]), float(x[1])) for x in (d.get("min_roi") or [])),
            tp_stages=tuple(float(x) for x in (d.get("tp_stages") or [])),
            safety_net_cap=float(d.get("safety_net_cap") or 0.80),
            enabled=bool(d.get("enabled", True)),
        )


# 车道默认（价格口径 %）。短/中线的 time_limit 与 runtime_tuning.tier_max_hold_sec 对齐（1.5h / 48h）；
# 长线 = 趋势：只留 L1/Chandelier（structural_stop=chandelier），无 TP / time_limit / 递减 ROI。
# tp_stages 初值来自各车道历史 MFE 分位的粗标定（exit_policy_calibration 可用 trade_facts 重标）。
_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "short": dict(sl_pct=0.6, tp_pct=1.2, time_limit_sec=5400, trailing_activation_pct=0.7, trailing_callback_pct=0.3,
                  structural_stop="none", min_roi=((2700, 0.30), (4200, 0.10)), tp_stages=(0.5, 0.9, 1.2)),
    # [验收轮6 2026-09-16 用户实测反馈] trail 激活 4.0%→1.0%、callback 1.5→0.5、
    # tp_stages 2/3.5/5.5→0.8/1.6/3.0、min_roi 打开（12h<0.5%走、24h<0走）、
    # time_limit 7d→48h。依据：9/14-9/16 共 23 笔平仓 peak 合计 296 美元、
    # 回撤掉 302.88（ASTER 峰值+0.21%→SL -38.66、VIRTUAL +0.54%→SL -38.45）——
    # 旧档位（TP1=2%、trail 4%）按"价格走 2%+"校准，而探针仓实际峰值只有
    # 0.2~0.5%，止盈永远摸不到、止损 -4.66% 必然吃到。新口径锁小利润 +
    # 时间递减 ROI 兜底（赚不到钱就离场，不再死扛）。
    #
    # [轮110 2026-09-19 用 30 天真实样本重标] tp_stages 0.8/1.6/3.0 → **2.5/4.0/6.0**。
    # 依据（159 笔中线已平仓，真实 MFE/MAE 网格回测，已扣 0.04% 往返费）：
    #   · 现行 SL1.5%/TP0.8% = **−0.175%/笔**（0.8% 那一列在 7×8 网格里全场最差）；
    #   · 保留的 mlto 臂上，SL1.5%/TP2.5% = **+0.237%/笔**、TP4.0% = +0.386、TP6.0% = +0.399；
    #   · mlto 臂 MFE 分位：P50=1.41%、P75=2.46%、P85=3.67%、P90=3.85%、P95=4.52%
    #     ⇒ 首档取 **P75**（2.5%）、二档≈P90（4.0%）、三档放到 P95 之外（6.0%）让尾部奔跑；
    #   · 旧 0.8% 首档的含义是"在 MFE 中位数附近止盈"——赢单被截断、亏损单仍按 −1.7% 走。
    # 注意：**放宽止损没有用**（同一个网格 SL1.5/2.0/3.0/4.0 → +0.237/+0.245/+0.158/+0.058），
    # 所以 SL 保持 1.5%，本次只动止盈档位 ⇒ 每笔风险不变，**不需要重算仓位乘子**。
    "mid": dict(sl_pct=3.0, tp_pct=None, time_limit_sec=172800, trailing_activation_pct=1.0, trailing_callback_pct=0.5,
                structural_stop="price", min_roi=((43200, 0.5), (86400, 0.0)), tp_stages=(2.5, 4.0, 6.0)),
    "long": dict(sl_pct=None, tp_pct=None, time_limit_sec=None, trailing_activation_pct=None, trailing_callback_pct=None,
                 structural_stop="chandelier", min_roi=(), tp_stages=(8.0, 15.0, 25.0)),
    "research": dict(sl_pct=2.0, tp_pct=4.0, time_limit_sec=86400, trailing_activation_pct=2.0, trailing_callback_pct=1.0,
                     structural_stop="price", min_roi=((43200, 0.5), (64800, 0.0)), tp_stages=(1.5, 2.5, 4.0)),
    "arb": dict(sl_pct=None, tp_pct=None, time_limit_sec=None, trailing_activation_pct=None, trailing_callback_pct=None,
                structural_stop="none", min_roi=(), tp_stages=()),
}


@dataclass
class ExitSnapshot:
    """评估一根 tick 需要的持仓快照（全部由调用方给，纯数据）。"""

    side: str                       # long / short
    entry: float
    current: float
    elapsed_sec: float
    peak_roi_pct: float             # 历史最高价格口径浮盈 %（含本 tick）
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    structural_stop_price: Optional[float] = None
    roi_pct: Optional[float] = None  # 不给则由 entry/current 计算

    def computed_roi_pct(self) -> float:
        if self.roi_pct is not None:
            return float(self.roi_pct)
        if not self.entry or self.entry <= 0:
            return 0.0
        r = (float(self.current) - float(self.entry)) / float(self.entry) * 100.0
        return r if self.side == "long" else -r


@dataclass
class ExitVerdict:
    action: str                     # close | tighten_sl | hold
    reason: str = ""
    new_sl: Optional[float] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_close(self) -> bool:
        return self.action == "close"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _crossed(side: str, current: float, level: float, kind: str) -> bool:
    """kind=stop：多头 current ≤ level / 空头 current ≥ level；kind=target 反之。"""
    if level is None or level <= 0:
        return False
    if kind == "stop":
        return current <= level if side == "long" else current >= level
    return current >= level if side == "long" else current <= level


def _price_from_roi(side: str, entry: float, roi_pct: float) -> float:
    return entry * (1 + roi_pct / 100.0) if side == "long" else entry * (1 - roi_pct / 100.0)


def evaluate(policy: ExitPolicy, snap: ExitSnapshot) -> ExitVerdict:
    """固定优先级评估。全部价格口径；不改任何状态。"""
    if not policy.enabled:
        return ExitVerdict("hold", "policy_disabled")
    side = "long" if str(snap.side).lower() == "long" else "short"
    cur = float(snap.current or 0)
    entry = float(snap.entry or 0)
    if cur <= 0 or entry <= 0:
        return ExitVerdict("hold", "no_price")
    roi = snap.computed_roi_pct()
    peak = max(float(snap.peak_roi_pct or 0.0), roi)

    # 1) 结构失效价（price 模式：exit_state_json.structural_stop_price；chandelier 模式：价在 sl_price，由硬 SL 层判）
    if policy.structural_stop == "price" and snap.structural_stop_price and _crossed(side, cur, float(snap.structural_stop_price), "stop"):
        return ExitVerdict("close", "structural_invalidation", detail={"level": snap.structural_stop_price, "roi_pct": roi})

    # 2) 硬 SL（引擎已先判；此处收敛口径 + 声明的 sl_pct 兜底）
    #
    # [2026-09-07] 有附着硬 SL 时，禁止再用声明 sl_pct 软平。
    # 根因：ATR floor 常把硬 SL 拉到 ~4.5%，声明仍 3% → ROI 刚到 -3% 就被
    # exit_policy:sl_pct 扫掉（VIRTUAL #4602：2.8h / -32.8U）。硬单才是真相。
    if snap.sl_price and _crossed(side, cur, float(snap.sl_price), "stop"):
        return ExitVerdict("close", "sl", detail={"level": snap.sl_price, "roi_pct": roi})
    if policy.sl_pct is not None and not snap.sl_price and roi <= -abs(float(policy.sl_pct)):
        return ExitVerdict("close", "sl_pct", detail={"sl_pct": policy.sl_pct, "roi_pct": roi})

    # 3) 硬 TP
    if snap.tp_price and _crossed(side, cur, float(snap.tp_price), "target"):
        return ExitVerdict("close", "tp", detail={"level": snap.tp_price, "roi_pct": roi})
    if policy.tp_pct is not None and roi >= abs(float(policy.tp_pct)):
        return ExitVerdict("close", "tp_pct", detail={"tp_pct": policy.tp_pct, "roi_pct": roi})

    # 4) time_limit（第三屏障）
    if policy.time_limit_sec is not None and snap.elapsed_sec >= float(policy.time_limit_sec):
        return ExitVerdict("close", "time_limit", detail={"elapsed_sec": snap.elapsed_sec, "limit_sec": policy.time_limit_sec, "roi_pct": roi})

    # 5) 时间递减 ROI（Freqtrade minimal_roi）：取已到达的最后一档
    # [P2 大轮回 2026-09-27] 弱化：仅盈利后生效（§96 反事实：min_roi_decay 的 55%
    # 是市场 beta——把时间到点但浮亏的仓砍掉等于把 beta 当 alpha）。
    # 弱化后：roi>0 且低于阶段门槛 → 关（保护已兑现利润）；roi≤0 → 不砍，
    # 浮亏仓交给 4h 反转/SL/§9.2⑤时间止损（P3 落地 8h 减半/24h 全平）接管。
    # 回滚：EXIT_POLICY_MIN_ROI_PROFIT_ONLY=false（恢复旧口径"时间到点即砍"）。
    if policy.min_roi:
        threshold = None
        for sec, min_roi in policy.min_roi:
            if snap.elapsed_sec >= sec:
                threshold = (sec, min_roi)
        if threshold is not None:
            _profit_only = (
                os.getenv("EXIT_POLICY_MIN_ROI_PROFIT_ONLY", "true") or "true"
            ).strip().lower() in ("1", "true", "yes", "on")
            _fire = roi < threshold[1] and (roi > 0 or not _profit_only)
            if _fire:
                return ExitVerdict("close", "min_roi_decay",
                                   detail={"elapsed_sec": snap.elapsed_sec, "stage_sec": threshold[0],
                                           "min_roi_pct": threshold[1], "roi_pct": roi,
                                           "profit_only": _profit_only})

    # 6) trailing：激活后从峰值回撤 ≥ callback → close；否则把 SL 收紧到 peak − callback（只朝有利方向）
    if policy.trailing_activation_pct is not None and policy.trailing_callback_pct is not None and peak >= float(policy.trailing_activation_pct):
        cb = float(policy.trailing_callback_pct)
        if peak - roi >= cb:
            return ExitVerdict("close", "trailing_callback", detail={"peak_roi_pct": peak, "roi_pct": roi, "callback_pct": cb})
        lock_roi = peak - cb
        new_sl = _price_from_roi(side, entry, lock_roi)
        cur_sl = float(snap.sl_price) if snap.sl_price else None
        better = cur_sl is None or (new_sl > cur_sl if side == "long" else new_sl < cur_sl)
        # [2026-09-10 防抖] 低激活阈值（如 mid 0.5%）下，峰值每动一点都会想改 SL，
        # 会带来高频改单/同步开销。只在改善幅度 ≥ EXIT_POLICY_TRAILING_MIN_STEP_PCT
        # （默认 0.05% 价格）时才返回 tighten_sl；回撤 ≥ callback 的平仓判定不受影响。
        try:
            _min_step_pct = float(os.getenv("EXIT_POLICY_TRAILING_MIN_STEP_PCT", "0.05") or 0.05)
        except ValueError:
            _min_step_pct = 0.05
        _step_ok = (
            cur_sl is None
            or abs(new_sl - cur_sl) / entry * 100.0 >= _min_step_pct
        )
        if better and _step_ok:
            return ExitVerdict("tighten_sl", "trailing_lock", new_sl=round(new_sl, 8),
                               detail={"peak_roi_pct": peak, "lock_roi_pct": lock_roi, "prev_sl": cur_sl})

    return ExitVerdict("hold", "ok", detail={"roi_pct": roi, "peak_roi_pct": peak})


# ────────────────────────────── 分档标定（MFE 分位） ──────────────────────────────

def calibrate_tp_stages(mfe_pcts: Sequence[float], quantiles: Sequence[float] = (0.50, 0.70, 0.85),
                        floor_pct: float = 0.2) -> Tuple[float, ...]:
    """按车道历史 MFE（价格口径 %）分位标定 1/2/3 档。N < 30 返回空（调用方沿用默认）。"""
    vals = sorted(float(x) for x in mfe_pcts if x is not None and float(x) > 0)
    if len(vals) < 30:
        return tuple()
    out: List[float] = []
    for q in quantiles:
        idx = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
        out.append(max(floor_pct, round(vals[idx], 3)))
    # 单调递增
    for i in range(1, len(out)):
        if out[i] <= out[i - 1]:
            out[i] = round(out[i - 1] * 1.25, 3)
    return tuple(out)


def describe() -> Dict[str, Any]:
    return {"enforce": enforce_enabled(), "lanes": {l: ExitPolicy.for_lane(l).to_dict() for l in LANES}}


def policy_from_exit_state(exit_state_json: Any, lane: str) -> ExitPolicy:
    """从持仓 exit_state_json 取开仓时的声明；没有则按车道当前默认。"""
    try:
        es = exit_state_json
        if isinstance(es, str):
            es = json.loads(es) if es else {}
        if isinstance(es, dict) and isinstance(es.get("exit_policy"), dict):
            return ExitPolicy.from_dict(es["exit_policy"])
    except Exception as exc:
        logger.debug("[ExitPolicy] 解析 exit_state_json.exit_policy 失败，用车道默认: %s", exc)
    return ExitPolicy.for_lane(lane)


# ── [2026-09-16 调研轮7] 存量仓策略刷新 ──────────────────────────────
#: 刷新时**保持不变**的字段：止损相关的风险边界在开仓时即已生效（价已挂到
#: pos.sl_price），事后刷新不得移动它——只刷新「利润保护」参数。
REFRESH_KEEP_FIELDS: Tuple[str, ...] = ("sl_pct", "structural_stop", "lane", "enabled")

#: 参与刷新的利润保护字段（顺序用于日志/变更明细）
REFRESH_PROTECT_FIELDS: Tuple[str, ...] = (
    "trailing_activation_pct", "trailing_callback_pct", "min_roi",
    "tp_stages", "tp_pct", "time_limit_sec", "safety_net_cap",
)


def refresh_enabled() -> bool:
    """[2026-09-16 调研轮7] 存量仓是否随车道重标定刷新利润保护参数（默认开）。

    ## 为什么必须刷新

    ExitPolicy 在**开仓时快照**进 `exit_state_json`，此后持仓终身沿用 ⇒ 车道的
    参数重标定（如 9/16 验收轮6 的 trail 1.0/0.5、min_roi、TP 0.8/1.6/3.0）对
    存量仓**永不生效**。实测（2026-09-16 10:0x，account 14）：

      * XRP 4683 浮盈 +4.42% ROI，仍用旧 trail 3.0%/1.5%（4× 杠杆 ≈ 12% ROI 才激活）
        ⇒ 价格一回头浮盈必然回吐；
      * SOL 4681 持有 20.3h、ROI -12.26%，快照 `min_roi=[]` ⇒ 无「赚不到就走」兜底；
      * 同时刻新开的 BNB 4685 已带新参数（trail 1.0/0.5 + min_roi + TP 0.8/1.6/3.0）。

    即：同一个账户里新旧策略并存，旧仓继续按**已被证伪**的档位出场——这正是
    「浮盈回撤到负、死扛单」的结构性来源之一。

    ## 语义

    只刷新利润保护字段；`sl_pct` / `structural_stop` / `enabled` 保持快照值
    （止损边界不在盘后被移动）。刷新会写 `exit_state_json.exit_policy_history`
    （最近 5 条）以便事后审计「这笔仓何时换了规则、换了什么」。

    回滚：`EXIT_POLICY_REFRESH_OPEN=false`。
    """
    try:
        return str(os.getenv("EXIT_POLICY_REFRESH_OPEN", "true")).strip().lower() in (
            "1", "true", "yes", "on",
        )
    except Exception:
        return True


def refreshed_policy(snap: ExitPolicy, lane: str) -> Tuple[ExitPolicy, Dict[str, Any]]:
    """把车道**当前**的利润保护参数套到存量仓快照上（止损字段保持快照值）。

    返回 `(合并后的策略, 变更明细)`；无变更时变更明细为 `{}`（调用方据此免写库）。
    """
    cur = ExitPolicy.for_lane(lane)
    merged = replace(snap)
    changes: Dict[str, Any] = {}
    for f in REFRESH_PROTECT_FIELDS:
        old = getattr(snap, f, None)
        new = getattr(cur, f, None)
        if old != new:
            changes[f] = {"from": _jsonable(old), "to": _jsonable(new)}
            merged = replace(merged, **{f: new})
    return merged, changes


def _jsonable(v: Any) -> Any:
    if isinstance(v, tuple):
        return [list(x) if isinstance(x, tuple) else x for x in v]
    return v
