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
_PROBE_QUOTA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "fusion_pwin_probe_quota.json")


def _explore_quota_read() -> Dict[str, int]:
    """今日各账户已用探索配额 {account_id_str: used}（文件按日期重置）。

    [2026-08-29 跨账户污染修复] 原计数无账户维度——paper 会话（日均 30+ 笔
    scalp）把全局 5 笔/天配额吃光，实盘账户（零成交）被同步锁死。改为
    按账户独立计数；旧格式 {"date","used":int} 迁移到 paper 账户(14)。
    """
    import json as _json
    import time as _time
    try:
        _today = _time.strftime("%Y-%m-%d")
        if os.path.exists(_EXPLORE_QUOTA_PATH):
            _d = _json.load(open(_EXPLORE_QUOTA_PATH, "r", encoding="utf-8"))
            if isinstance(_d, dict) and _d.get("date") == _today:
                _u = _d.get("used")
                if isinstance(_u, dict):
                    return {str(k): int(v) for k, v in _u.items() if isinstance(v, (int, float))}
                # 旧全局格式：归到 paper 主账户
                if isinstance(_u, (int, float)):
                    return {"14": int(_u)}
    except Exception:
        pass
    return {}


def _explore_quota_used(account_id=None) -> int:
    _key = str(account_id) if account_id else "14"
    return int(_explore_quota_read().get(_key, 0) or 0)


def _explore_quota_bump(account_id=None) -> None:
    import json as _json
    import time as _time
    try:
        _today = _time.strftime("%Y-%m-%d")
        _used_map = _explore_quota_read()
        _key = str(account_id) if account_id else "14"
        _used_map[_key] = int(_used_map.get(_key, 0) or 0) + 1
        _json.dump({"date": _today, "used": _used_map},
                   open(_EXPLORE_QUOTA_PATH, "w", encoding="utf-8"))
    except Exception:
        pass


def explore_quota_bump(account_id=None) -> None:
    """成交后扣减探索配额（公开入口，scalp_loop 真实成交时按账户调用）。"""
    _explore_quota_bump(account_id)


# ── [2026-08-29 v2] 保底流量探针配额：分档地板之下仍保证每日最小交易流 ──
def _probe_quota_read() -> Dict[str, int]:
    import json as _json
    import time as _time
    try:
        _today = _time.strftime("%Y-%m-%d")
        if os.path.exists(_PROBE_QUOTA_PATH):
            _d = _json.load(open(_PROBE_QUOTA_PATH, "r", encoding="utf-8"))
            if isinstance(_d, dict) and _d.get("date") == _today:
                _u = _d.get("used")
                if isinstance(_u, dict):
                    return {str(k): int(v) for k, v in _u.items() if isinstance(v, (int, float))}
    except Exception:
        pass
    return {}


def _probe_quota_used(account_id=None) -> int:
    return int(_probe_quota_read().get(str(account_id) if account_id else "14", 0) or 0)


def _probe_quota_bump(account_id=None) -> None:
    import json as _json
    import time as _time
    try:
        _today = _time.strftime("%Y-%m-%d")
        _used_map = _probe_quota_read()
        _key = str(account_id) if account_id else "14"
        _used_map[_key] = int(_used_map.get(_key, 0) or 0) + 1
        _json.dump({"date": _today, "used": _used_map},
                   open(_PROBE_QUOTA_PATH, "w", encoding="utf-8"))
    except Exception:
        pass


def probe_quota_bump(account_id=None) -> None:
    """成交后扣减探针配额（公开入口，scalp_loop 真实成交时按账户调用）。"""
    _probe_quota_bump(account_id)


def _probe_quota_cap() -> int:
    """每日保底流量探针配额（正常探针与影子探针共享同一预算）。"""
    try:
        return int(float(os.getenv("FUSION_PROBE_DAILY_QUOTA", "3") or 3))
    except (TypeError, ValueError):
        return 3


def _pwin_tiers() -> list:
    """解析 FUSION_PWIN_TIERS="0.60:0.75,0.55:0.50,0.50:0.25"。

    返回按阈值降序的 [(threshold, size_mult), ...]；解析失败返回空表（退回硬地板模式）。
    """
    raw = (os.getenv("FUSION_PWIN_TIERS", "0.60:0.75,0.55:0.50,0.50:0.25") or "").strip()
    tiers = []
    try:
        for part in raw.split(","):
            th, sz = part.split(":")
            tiers.append((float(th), float(sz)))
        tiers.sort(key=lambda t: -t[0])
    except Exception:
        return []
    return tiers


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
    mode: str = "paper",
    account_id: Optional[int] = None,
    is_mr: bool = False,
) -> FusionDecision:
    """短线入场仲裁（v2 矩阵，pwin 主轴）。

    [2026-08-28 实盘零成交修复] mode/account_id：实盘引导期
    （live_gate_policy.live_bootstrap_active）内，pwin 地板改用
    FUSION_SCALP_PWIN_MIN_LIVE（绝对下限，不叠加 RR 安全系数），让实盘在自身
    零样本时不被「模拟盘标定的旧地板」锁死；引导期满自动回落到严格地板。
    paper 行为完全不变。
    """
    # 0. 每次决策前重载 .env(门槛调整免重启生效)
    _maybe_reload_env()
    # 1. LLM 硬否决（清算簇/黑天鹅/流动性告警）→ 因子不可覆盖
    if llm_veto:
        return FusionDecision("hold", 0.0, "llm", "llm_hard_veto", {"veto": str(llm_veto)[:120]})

    # 2. 来源信用熔断：该来源滚动期望<0 → shadow（只记账不下单）。
    # [2026-08-31 根治] 死锁出口：shadow 来源被停摆后不再产生新样本，而
    # 退出条件要求 n>=40 且净期望≥0 → 永无解除。给 shadow 来源极小额
    # "影子探针"（高分信号 + pwin 达探针线 + 共享每日探针配额），重新积累
    # 归因证据；若继续亏损，归因仍为负 → 保持停摆（风控不被削弱）。
    if credit <= 0.0:
        try:
            _sp_min_score = _f("FUSION_SHADOW_PROBE_MIN_SCORE", 50.0)
            _sp_min_pwin = _f("FUSION_SHADOW_PROBE_MIN_PWIN", _f("FUSION_PROBE_MIN_PWIN", 0.40))
        except (TypeError, ValueError):
            _sp_min_score, _sp_min_pwin = 50.0, 0.40
        _probe_short_ok = str(os.getenv("FUSION_PROBE_SHORT_ENABLED", "false")).strip().lower() in (
            "1", "true", "yes", "on",
        )
        _is_short_dir = (str(direction or "").lower() in ("short", "sell"))
        if (
            not is_mr
            and (_probe_short_ok or not _is_short_dir)
            and pwin is not None
            and factor_score >= _sp_min_score
            and pwin >= _sp_min_pwin
            and _probe_quota_used(account_id) < _probe_quota_cap()
        ):
            return FusionDecision(
                "trade", 0.125, "rule", "shadow_probe_quota",
                {"credit": credit, "pwin": pwin, "factor_score": factor_score,
                 "quota_used": _probe_quota_used(account_id),
                 "quota": _probe_quota_cap(),
                 "note": "shadow来源影子探针(最小仓), 成交后由 scalp_loop 扣配额"},
            )
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
        _used = _explore_quota_used(account_id)
        if _used < _quota:
            try:
                _ex_thr = float(os.getenv("SCALP_FACTOR_EXECUTE_THRESHOLD", "35") or 35)
            except (TypeError, ValueError):
                _ex_thr = 35.0
            _rr_ok = True
            if tp_pct and sl_pct and sl_pct > 0:
                # [2026-08-29 MR分档] MR 结构自带贴边小止损（RR≈保本口径不同），
                # 按 SCALP_MR_MIN_RR 独立档；其余走 FUSION_RR_FLOOR（已提至 1.3）。
                _rr_floor_now = (
                    _f("SCALP_MR_MIN_RR", 1.0) if is_mr else _f("FUSION_RR_FLOOR", RR_FLOOR)
                )
                _rr_ok = (float(tp_pct) / float(sl_pct)) >= _rr_floor_now
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
            # 配额有余但分数/RR 未达探索门槛：诚实标注真实拦截原因
            return FusionDecision(
                "hold", 0.0, "rule", "pwin_model_unusable_explore_bar",
                {
                    "pwin": pwin, "factor_score": factor_score, "ex_thr": _ex_thr,
                    "rr_ok": _rr_ok, "quota_used": _used, "quota": _quota,
                },
            )
        return FusionDecision("hold", 0.0, "rule", "pwin_model_unusable_quota",
                              {"pwin": pwin, "quota_used": _used, "quota": _quota})

    # 5. pwin 门槛 —— [2026-08-29 v2] 分档放行（替代硬地板，防"零成交死锁"）。
    #    实证：过分数门的信号 pwin 分布集中 0.4-0.5 带，硬地板 0.60 ≈ 永久零成交
    #    （用户实测判断）；重训模型（剔噪后 AUC 0.621）top30% 净 +0.112%——
    #    0.50-0.60 带在新模型下非负 EV。分档让流量存活、资本向最优带集中：
    #      ≥0.60 → 0.75x | [0.55,0.60) → 0.50x | [0.50,0.55) → 0.25x | <地板 → hold
    #    硬地板 = max(最低档阈值, rr感知保本地板, 桶验证衰减覆盖, 实盘加严)。
    #    地板之下还有保底探针（pwin≥FUSION_PROBE_MIN_PWIN 且当日配额有余 →
    #    0.125x 最小仓）——系统永不全眠，学习样本不断流。
    #    FUSION_PWIN_TIERED=false 回退 v1 硬地板模式。
    _tiered = (os.getenv("FUSION_PWIN_TIERED", "true") or "true").strip().lower() in (
        "1", "true", "yes", "on")
    _tiers = _pwin_tiers() if _tiered else []
    # 基础地板：分档模式=最低档阈值（旧 0.55 base 不再盖过档位，否则无 tp/sl
    # 上下文的调用会退回硬地板语义）；非分档=旧 base。桶验证衰减覆盖优先。
    if _tiers:
        _floor = (_pwin_floor_override if _pwin_floor_override is not None
                  else _tiers[-1][0])
    else:
        _floor = effective_pwin_floor()
    _floor_mode = os.getenv("FUSION_PWIN_FLOOR_MODE", "rr_aware").strip().lower()
    _mode_ds = (mode or "paper").strip().lower()
    _live_tight = _mode_ds == "live"
    _live_boot = False
    if _live_tight:
        try:
            from backend.services.full_auto.live_gate_policy import (
                live_bootstrap_active,
                live_pwin_extra,
                live_scalp_pwin_floor,
            )
            _live_boot = bool(live_bootstrap_active(account_id))
            _floor_mode = "live_tight"
        except Exception as _live_boot_err:
            logger.debug("[FusionArbiter] 实盘地板策略读取失败(沿用默认): %s", _live_boot_err)
    if _floor_mode not in ("legacy",) and tp_pct and sl_pct and sl_pct > 0:
        try:
            _rr_now = float(tp_pct) / float(sl_pct)
            _be = 1.0 / (1.0 + _rr_now) if _rr_now > 0 else 1.0
            _safety = float(os.getenv("FUSION_PWIN_SAFETY_MULT", "1.05") or 1.05)
            _abs_min = float(os.getenv("FUSION_PWIN_ABSOLUTE_MIN", "0.45") or 0.45)
            # rr_aware: 地板 = max(保本×安全系数, 绝对下限)——MR(RR0.75)地板
            # 自动上浮到 ~0.60，低于保本线的信号不放行（历史实测净亏）。
            _floor = max(_be * _safety, _abs_min)
        except (TypeError, ValueError):
            pass
    # 分档模式的硬地板 = max(最低档阈值, 上面的 rr/桶/实盘地板)
    if _tiers:
        _floor = max(_floor, _tiers[-1][0])
    if _live_tight:
        try:
            from backend.services.full_auto.live_gate_policy import (
                live_pwin_extra,
                live_scalp_pwin_floor,
            )
            if _live_boot:
                _floor = max(float(_floor), float(live_scalp_pwin_floor()))
            _floor = min(0.95, float(_floor) + float(live_pwin_extra()))
        except Exception as _live_extra_err:
            logger.debug("[FusionArbiter] 实盘 pwin 加严失败(沿用当前地板): %s", _live_extra_err)

    # [2026-08-31 空头加严] 空头结构失血（14 天 -114、14 天中 12 天亏）：
    # 空头 pwin 地板加 premium（默认 +0.05，FUSION_SHORT_PWIN_EXTRA=0 关闭）。
    if d in ("short", "sell"):
        try:
            _short_extra = _f("FUSION_SHORT_PWIN_EXTRA", 0.05)
        except (TypeError, ValueError):
            _short_extra = 0.05
        _floor = min(0.95, float(_floor) + _short_extra)

    if pwin < _floor:
        # ── 保底流量探针：地板之下但 pwin 达探针线且当日配额有余 → 最小仓放行 ──
        # 仅限非 MR（MR 地板是保本口径，探它=负 EV）；fail 时不放行。
        # [2026-08-31 根治] ① 探针线默认 0.45→0.40：模型实际输出 ~0.40-0.41，
        # 0.45 永远够不到 → 系统全眠、无样本、模型不更新（收紧死螺旋）。
        # ② 补 factor_score 门槛：探针只给高分信号，不给低分垃圾流量。
        if not is_mr:
            try:
                _probe_min = _f("FUSION_PROBE_MIN_PWIN", 0.40)
                _probe_min_score = _f("FUSION_PROBE_MIN_SCORE", 45.0)
                _probe_cap = _probe_quota_cap()
            except (TypeError, ValueError):
                _probe_min, _probe_min_score, _probe_cap = 0.40, 45.0, 3
            _probe_short_ok2 = str(os.getenv("FUSION_PROBE_SHORT_ENABLED", "false")).strip().lower() in (
                "1", "true", "yes", "on",
            )
            if (
                (_probe_short_ok2 or d not in ("short", "sell"))
                and pwin >= _probe_min
                and factor_score >= _probe_min_score
                and _probe_quota_used(account_id) < _probe_cap
            ):
                return FusionDecision(
                    "trade", 0.125, "rule", "pwin_probe_quota",
                    {"pwin": pwin, "floor": _floor, "probe_min": _probe_min,
                     "probe_min_score": _probe_min_score,
                     "quota_used": _probe_quota_used(account_id), "quota": _probe_cap,
                     "note": "保底流量探针(最小仓), 成交后由 scalp_loop 扣配额"},
                )
        return FusionDecision("hold", 0.0, "rule", "pwin_below_min",
                              {"pwin": pwin, "floor": _floor, "floor_mode": _floor_mode,
                               "tiered": bool(_tiers)})

    # 分档仓位：命中最高满足档的 size_mult（与 thesis 冲突仓取较小值）
    if _tiers:
        _tier_mult = _tiers[-1][1]  # 兜底=最低档仓位
        _tier_hit = None
        for _th, _sz in _tiers:
            if pwin >= _th:
                _tier_mult, _tier_hit = _sz, _th
                break
        if src == "hybrid":  # thesis 冲突放行路径保持保守上限
            size = min(size, _tier_mult)
        else:
            size = _tier_mult

    # 6. RR 下限：TP/SL < 1.2 的结构必亏（历史 RR=0.9/0.32 类），LLM 特批除外
    # [2026-08-29 MR分档] FUSION_RR_FLOOR 提至 1.3 后，MR 信号（贴区间边缘的
    # 小止损结构，RR≈1.0，由 SCALP_MR_MIN_RR 在结构层保障 + pwin 地板按保本
    # 口径自动上浮到 ~0.60）走独立档，不被 1.3 一刀切灭活。
    if tp_pct and sl_pct and sl_pct > 0:
        rr = float(tp_pct) / float(sl_pct)
        _rr_floor = (
            _f("SCALP_MR_MIN_RR", 1.0) if is_mr else _f("FUSION_RR_FLOOR", RR_FLOOR)
        )
        # [2026-08-31 浮点容差] 结构止盈止损恰好等于地板（如 sl=0.9% tp=sl×1.3）
        # 时，1.17/0.9=1.2999999999999998 < 1.3 会被浮点误差冤杀。与
        # scalp_execution_gate._ensure_min_rr 同款 1e-9 容差。
        if rr + 1e-9 < _rr_floor:
            return FusionDecision("hold", 0.0, "rule", "rr_below_floor",
                                  {"rr": round(rr, 3), "tp_pct": tp_pct, "sl_pct": sl_pct,
                                   "pwin": round(float(pwin), 4), "mr": bool(is_mr)})

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
