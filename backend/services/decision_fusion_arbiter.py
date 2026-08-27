"""decision_fusion_arbiter — LLM × 因子融合仲裁器（阶段0 v2 决策矩阵）。

依据：
  - _research_llm_factor/02_详细设计_LLM因子融合_v2.md（定稿）
  - _research_llm_factor/03_可行性评估与离线验证.md
    （315k 笔回放：meta_p_win>=0.55 桶 wr 59.2% 净 +5.09；
     pwin [0.45,0.55) 桶 wr 40.7% 净 -50.11 → 边界带直接 hold，不做逐笔 LLM 确认）

设计要点：
  - 主轴 = meta 模型的 pwin（信号时点可用），factor_score 只作并列裁决，不给仓位加分
    （回放：score>=70 桶净 -332.55，全场最差 → 反证据保护）；
  - LLM 侧（thesis/veto/仲裁）以可选参数注入，未接入时 fail-open 走纯因子路径；
  - 纯函数 + 无 IO，可单测；调用方（scalp_loop / mlto_cycle / long_trend_v2）负责取数。

回滚：FUSION_MODE=factor 时调用方应跳过本模块，行为等价 8/23 现状。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# ── 配置（settings 模块化加载有循环依赖风险，此处用 env + 默认值，settings 只做透传）──
def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except ValueError:
        return default


def _s(name: str, default: str) -> str:
    return (os.getenv(name, "") or default).strip().lower()


FUSION_MODE: str = _s("FUSION_MODE", "hybrid")            # hybrid | factor | llm
PWIN_MIN: float = _f("FUSION_SCALP_PWIN_MIN", 0.55)       # 入场主阈值（回放证据）
PWIN_STRONG: float = _f("FUSION_SCALP_PWIN_STRONG", 0.60) # 最强桶
SIZE_OBSERVE: float = _f("FUSION_SCALP_SIZE_OBSERVE", 0.5)  # 观察期仓位
SIZE_STRONG: float = _f("FUSION_SCALP_SIZE_STRONG", 0.75)
SIZE_CONFLICT: float = _f("FUSION_SCALP_SIZE_CONFLICT", 0.25)
RR_FLOOR: float = _f("FUSION_RR_FLOOR", 1.2)              # TP/SL 下限
THESIS_CONF_MIN: float = _f("FUSION_THESIS_CONF_MIN", 0.6)  # thesis 反向才仲裁的置信线
PWIN_MISSING_MODE: str = _s("FUSION_PWIN_MISSING_MODE", "hold")  # hold | pass

# 运行期地板覆盖：pwin 桶验证连续失败 → 自动上提（衰减熔断）；恢复后回落。
_pwin_floor_override: Optional[float] = None


def set_pwin_floor_override(value: Optional[float]) -> None:
    global _pwin_floor_override
    _pwin_floor_override = value


_ENV_MTIME: Dict[str, float] = {}


def _maybe_reload_env() -> None:
    """.env 文件 mtime 变化时重新载入(override=True),让门槛调整免重启生效。"""
    try:
        import os as _os
        _root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
        _env_path = _os.path.join(_root, ".env")
        if not _os.path.isfile(_env_path):
            return
        _mt = _os.path.getmtime(_env_path)
        if _ENV_MTIME.get("env") == _mt:
            return
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=True)
        _ENV_MTIME["env"] = _mt
    except Exception:
        pass


_EXPLORE_QUOTA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "fusion_pwin_explore_quota.json")


def _explore_quota_used() -> int:
    """今日已用探索配额（文件按日期重置，读写容错）。"""
    import json as _json
    import time as _time
    try:
        _today = _time.strftime("%Y-%m-%d")
        if os.path.exists(_EXPLORE_QUOTA_PATH):
            _d = _json.load(open(_EXPLORE_QUOTA_PATH, "r", encoding="utf-8"))
            if isinstance(_d, dict) and _d.get("date") == _today:
                return int(_d.get("used", 0) or 0)
    except Exception:
        pass
    return 0


def _explore_quota_bump() -> None:
    import json as _json
    import time as _time
    try:
        _today = _time.strftime("%Y-%m-%d")
        _json.dump({"date": _today, "used": _explore_quota_used() + 1},
                   open(_EXPLORE_QUOTA_PATH, "w", encoding="utf-8"))
    except Exception:
        pass


def explore_quota_bump() -> None:
    """成交后扣减探索配额（公开入口，scalp_loop 真实成交时调用）。"""
    _explore_quota_bump()


def effective_pwin_floor() -> float:
    if _pwin_floor_override:
        return float(_pwin_floor_override)
    _maybe_reload_env()
    return _f("FUSION_SCALP_PWIN_MIN", PWIN_MIN)


@dataclass
class FusionDecision:
    """仲裁输出四态：hold / trade / half / arbitrate / standdown。"""
    action: str
    size_mult: float = 0.0
    source: str = "rule"          # factor | llm | hybrid | rule
    reason: str = ""
    tags: Dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.action in ("trade", "half")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "size_mult": self.size_mult,
            "source": self.source,
            "reason": self.reason,
            "tags": self.tags,
        }


def decide_scalp(
    pwin: Optional[float],
    factor_score: float = 0.0,
    direction: str = "long",
    thesis_dir: Optional[str] = None,
    thesis_conf: float = 0.0,
    llm_veto: Optional[Any] = None,
    llm_arbitration: Optional[bool] = None,   # 冲突仲裁已回：True=放行 False=否决
    credit: float = 1.0,
    tp_pct: Optional[float] = None,
    sl_pct: Optional[float] = None,
) -> FusionDecision:
    """短线入场仲裁（v2 矩阵，pwin 主轴）。"""
    # 0. 每次决策前重载 .env(门槛调整免重启生效)
    _maybe_reload_env()
    # 1. LLM 硬否决（清算簇/黑天鹅/流动性告警）→ 因子不可覆盖
    if llm_veto:
        return FusionDecision("hold", 0.0, "llm", "llm_hard_veto", {"veto": str(llm_veto)[:120]})

    # 2. 来源信用熔断：该来源滚动期望<0 → shadow（只记账不下单）
    if credit <= 0.0:
        return FusionDecision("standdown", 0.0, "rule", "source_credit_shadow", {"credit": credit})

    # 3. meta 模型不可用：fail-closed（回放：无选择性过滤的历史 = 净 -544）
    if pwin is None:
        if PWIN_MISSING_MODE == "pass":
            return FusionDecision("trade", SIZE_OBSERVE, "factor", "pwin_unavailable_pass",
                                 {"pwin": None})
        return FusionDecision("hold", 0.0, "rule", "pwin_unavailable", {"pwin": None})

    d = (direction or "").lower()

    # 4. thesis 方向冲突（conf≥阈值）→ 需 LLM 仲裁；已仲裁 → 半仓或否决
    if thesis_dir and thesis_conf >= THESIS_CONF_MIN and thesis_dir.lower() != d:
        if llm_arbitration is None:
            return FusionDecision("arbitrate", 0.0, "llm",
                                  "thesis_conflict_need_arbitration",
                                  {"pwin": pwin, "thesis_dir": thesis_dir, "thesis_conf": thesis_conf})
        if not llm_arbitration:
            return FusionDecision("hold", 0.0, "llm", "thesis_conflict_denied",
                                  {"pwin": pwin, "thesis_dir": thesis_dir})
        size = SIZE_CONFLICT
        src = "hybrid"
    else:
        size = SIZE_STRONG if pwin >= PWIN_STRONG else SIZE_OBSERVE
        src = "hybrid" if thesis_dir else "factor"

    # 4.5 [2026-08-27] 元模型不可用（自评 AUC<门槛/净利为负）时的探索配额：
    # 实证 08:34 训练的 v3 模型 usable=False（OOS AUC 0.522<0.53，验证净利
    # -0.24%）→ 其 pwin 输出是掷硬币噪声，再用 pwin 地板卡它 = 对噪声设闸、
    # 短线永久零成交、且永远攒不到新结构样本重训（死锁）。探索模式：
    # 模型不可用期间，信号通过其余闸门（分数≥执行门槛、RR≥地板）后以最小仓
    # 放行，每日配额上限（默认5笔/天，文件计数按日期重置）→ 攒样本。
    # 模型恢复 usable 后自动回到严格 pwin 地板。legacy 行为=hold。
    try:
        from backend.services.scalp_meta_trainer import meta_model_usable as _m_usable
    except Exception:
        _m_usable = None
    _mu = bool(_m_usable()) if _m_usable is not None else None
    _unusable_mode = os.getenv("FUSION_PWIN_UNUSABLE_MODE", "explore_quota").strip().lower()
    if _mu is False and _unusable_mode != "hold":
        try:
            _quota = int(os.getenv("FUSION_PWIN_EXPLORE_DAILY_QUOTA", "5") or 5)
        except (TypeError, ValueError):
            _quota = 5
        _used = _explore_quota_used()
        if _used < _quota:
            try:
                _ex_thr = float(os.getenv("SCALP_FACTOR_EXECUTE_THRESHOLD", "35") or 35)
            except (TypeError, ValueError):
                _ex_thr = 35.0
            _rr_ok = True
            if tp_pct and sl_pct and sl_pct > 0:
                _rr_ok = (float(tp_pct) / float(sl_pct)) >= _f("FUSION_RR_FLOOR", RR_FLOOR)
            if factor_score >= _ex_thr and _rr_ok:
                # [2026-08-27 修正] 决策时不扣配额——后续闸门（仓位/执行门/veto）
                # 可能拦掉，预扣导致配额被浪费（实测5/5只成交1笔）。只打标签，
                # 由 scalp_loop 在真实成交后调 explore_quota_bump() 扣减。
                return FusionDecision(
                    "trade", SIZE_OBSERVE, "factor", "pwin_model_unusable_explore",
                    {
                        "pwin": pwin, "quota_used": _used, "quota": _quota,
                        "model_usable": False, "note": "元模型不可用期探索样本",
                        "explore_quota": True,
                    },
                )
        return FusionDecision("hold", 0.0, "rule", "pwin_model_unusable_quota",
                              {"pwin": pwin, "quota_used": _used, "quota": _quota})

    # 5. pwin 主阈值（回放：<0.55 桶全部负期望，不交易、也不浪费 LLM 确认）；
    #    运行期地板覆盖 = 桶验证失败时的自动衰减熔断（effective_pwin_floor）。
    # [2026-08-27 RR感知地板] 旧 0.55 地板建立在旧 TP/SL 结构（TP≈2.5% 摸不到、
    # RR 差）的回放上；新结构 TP1.5/SL1.15 → RR≈1.30 → 保本 pwin=1/(1+RR)=0.435。
    # 实测 SOL 因子直通 pwin=0.4716 被 0.55 全拦 → 短线零成交。改为：
    #   floor = max(保本×安全系数(默认1.05), 绝对下限(默认0.45))
    # legacy 模式（FUSION_PWIN_FLOOR_MODE=legacy）恢复旧行为。
    _floor = effective_pwin_floor()
    _floor_mode = os.getenv("FUSION_PWIN_FLOOR_MODE", "rr_aware").strip().lower()
    if _floor_mode != "legacy" and tp_pct and sl_pct and sl_pct > 0:
        try:
            _rr_now = float(tp_pct) / float(sl_pct)
            _be = 1.0 / (1.0 + _rr_now) if _rr_now > 0 else 1.0
            _safety = float(os.getenv("FUSION_PWIN_SAFETY_MULT", "1.05") or 1.05)
            _abs_min = float(os.getenv("FUSION_PWIN_ABSOLUTE_MIN", "0.45") or 0.45)
            _floor_rr = max(_be * _safety, _abs_min)
            if _floor_rr < _floor:
                _floor = _floor_rr
        except (TypeError, ValueError):
            pass
    if pwin < _floor:
        return FusionDecision("hold", 0.0, "rule", "pwin_below_min",
                              {"pwin": pwin, "floor": _floor, "floor_mode": _floor_mode})

    # 6. RR 下限：TP/SL < 1.2 的结构必亏（历史 RR=0.9/0.32 类），LLM 特批除外
    if tp_pct and sl_pct and sl_pct > 0:
        rr = float(tp_pct) / float(sl_pct)
        _rr_floor = _f("FUSION_RR_FLOOR", RR_FLOOR)
        if rr < _rr_floor:
            return FusionDecision("hold", 0.0, "rule", "rr_below_floor",
                                  {"rr": round(rr, 3), "tp_pct": tp_pct, "sl_pct": sl_pct,
                                   "pwin": round(float(pwin), 4)})

    # factor_score 不参与仓位：回放 score>=70 桶 -332.55（反证据保护）
    return FusionDecision("trade", size, src, "fusion_pass",
                          {"pwin": round(float(pwin), 4), "factor_score": factor_score,
                           "size_mult": size})


def decide_mid(
    factor_ok: bool,
    direction: str,
    thesis_dir: Optional[str] = None,
    thesis_conf: float = 0.0,
    credit: float = 1.0,
) -> FusionDecision:
    """中线仲裁：FactorRoute 投票 + LLM thesis 对齐闸（冲突→skip，不冻结）。"""
    if credit <= 0.0:
        return FusionDecision("standdown", 0.0, "rule", "source_credit_shadow", {"credit": credit})
    if not factor_ok:
        return FusionDecision("hold", 0.0, "factor", "factor_route_below_threshold")
    if thesis_dir and thesis_conf >= THESIS_CONF_MIN and thesis_dir.lower() != (direction or "").lower():
        return FusionDecision("hold", 0.0, "llm", "thesis_conflict_skip",
                              {"thesis_dir": thesis_dir, "thesis_conf": thesis_conf})
    src = "hybrid" if thesis_dir else "factor"
    return FusionDecision("trade", 1.0, src, "mid_fusion_pass")


def decide_long(thesis_state: Optional[str] = None, l1_state: str = "") -> FusionDecision:
    """长线仲裁：long_trend_v2 L1 骨架 + LLM 周级 thesis 否决。"""
    if thesis_state == "standdown":
        return FusionDecision("standdown", 0.0, "llm", "long_thesis_standdown")
    if (l1_state or "").lower() == "up":
        return FusionDecision("trade", 1.0, "factor", "long_l1_up")
    return FusionDecision("hold", 0.0, "factor", "long_l1_not_up", {"l1_state": l1_state})


# [U0 2026-08-25] ExitChannelBreaker 已删除（统一计划 E20）：出场通道熔断双实现归一，
# 唯一实现 = source_attribution.record_close 内建 breaker（持久化到 data/fusion_attribution.json）。
