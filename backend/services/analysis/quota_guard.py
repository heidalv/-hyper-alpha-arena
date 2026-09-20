# -*- coding: utf-8 -*-
"""QuotaGuard：深度分析模型的配额与成本守护（v3 方向 2）。

事实依据（2026-09 条款）：
  - GLM Coding Plan 与 MiniMax Token Plan 都是「5 小时滚动窗 + 周配额」；
  - GLM 峰时（工作日 14:00–18:00 北京时间）消耗按 3× 计；
  - 后端无法直接读到供应商侧余量 → 我们用本地流水（llm_quota_usage）做保守预算。

预算（env 可调，默认即方案拍板值）：
  ANALYSIS_DEEP_DAILY_PER_MODEL      深度任务（日报 / 周复盘 / 择时 / 参数寻优）每模型每日 ≤ 6 次
  ANALYSIS_EVENT_DAILY_PER_MODEL     事件评估每模型每日 ≤ 20 次
  ANALYSIS_LIGHT_DAILY_PER_MODEL     轻量 / 测试调用每模型每日 ≤ 40 次
  ANALYSIS_MAX_CONTEXT_TOKENS        单次上下文 ≤ 60k tokens（超出 → 拒绝，调用方裁剪 context pack）
  ANALYSIS_MAX_OUTPUT_TOKENS         单次输出 ≤ 4k tokens
  ANALYSIS_5H_CALLS_PER_MODEL        5 小时滚动窗每模型 ≤ 30 次（供应商窗口的本地保守镜像）
  ANALYSIS_WEEKLY_CALLS_PER_MODEL    周（7×24h 滚动）每模型 ≤ 150 次
  ANALYSIS_ALLOW_GLM_PEAK            true 时允许深度任务在 GLM 峰时运行（默认 false → 降级）
  ANALYSIS_GLM_PEAK_TZ_OFFSET_H      峰时判定时区偏移（默认 +8）

决策（check → Decision）：
  allow        正常放行
  degrade      本传输本轮不可用（配额/峰时），调用方应降级为单模型或换传输
  reject       硬拒绝（上下文超限、输出超限）——这是调用方 bug，不是配额问题
超预算时 QuotaGuard 只做决策与 P2 告警，不抛异常；ModelGateway 负责按决策降级。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TASK_CLASS_OF = {
    "daily_brief": "deep",
    "weekly_review": "deep",
    "timing": "deep",
    "param_search": "deep",
    "regime_brief": "deep",
    "event_impact": "event",
    "event_eval": "event",
    # [2026-09-05 P3] 多模态趋势图审：单币 3 图 + 数据 pack，默认 8h 一轮，按事件类计
    # （不占 deep 类配额，也不受 GLM 峰时深度任务限制——图审本身要求全时段可跑）
    "trend_chart_review": "event",
    # [2026-09-05] 中长线论题主脑：与图审同级 event，避免默认 light 被挤成单票假共识
    "midlong_thesis": "event",
    # [轮130 2026-09-20] 牛熊对抗辩论（brain_debate.py）：单次上下文小（提案+证据链），
    # 但一次辩论会连发牛/熊/风险角色多次调用 ⇒ 按 light 计，靠小时上限而不是配额控量。
    "mlto_debate": "light",
    "gateway_test": "light",
    "adhoc": "light",
    # 新闻标注：单条标题，上下文极小，但条数多（去重后约 21 条/天）。
    "news_annotate": "light",
}


def task_class(task: str) -> str:
    return TASK_CLASS_OF.get(task, "light")


# [2026-09-04] 本地推理传输：不走供应商 Key，没有 5h 窗 / 周配额 / 峰时倍率，
# 唯一成本是本机算力。原实现对所有传输一视同仁（record 的理由是"供应商侧同样扣减"，
# 这对本地模型不成立），结果是云端配额一耗尽，免费的本地兜底票也会被一起拦掉 ——
# 恰恰在最需要兜底的时候失效。这里只豁免"次数"类配额；上下文 / 输出的 token 上限
# 仍然生效，那是防止塞爆模型窗口的保护，与配额无关。
def local_transports() -> set:
    raw = os.getenv("ANALYSIS_LOCAL_TRANSPORTS", "ollama,ollama2")
    return {x.strip() for x in raw.split(",") if x.strip()}


def is_local_transport(transport: str) -> bool:
    return (transport or "").strip().lower() in local_transports()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_map(name: str) -> Dict[str, int]:
    """解析 `transport:值,transport:值` 形式的按传输覆盖，非法项跳过并告警。

    [2026-09-04] 各家套餐差着数量级，共用一个上限只能按最弱的那家定：
    GLM Coding Plan Max 每 5h 约 2400 次、MiniMax Token Plan 实测 8 次仅耗 4%，
    而全局上限是 30 —— 买来的额度只用得到约 1%，模型却频繁被本地判 degrade。
    """
    out: Dict[str, int] = {}
    for item in (os.getenv(name) or "").split(","):
        item = item.strip()
        if not item:
            continue
        tr, _, val = item.partition(":")
        try:
            out[tr.strip().lower()] = int(val)
        except Exception:
            logger.warning("[QuotaGuard] %s 配置项无法解析，已忽略: %r", name, item)
    return out


def estimate_tokens(text: str) -> int:
    """粗估 token：ASCII 约 4 字符/token，CJK 约 1.2 字符/token。只用于预算，不用于计费。"""
    if not text:
        return 0
    ascii_n = sum(1 for ch in text if ord(ch) < 128)
    other_n = len(text) - ascii_n
    return int(ascii_n / 4.0 + other_n / 1.2) + 1


def is_glm_peak(when: Optional[datetime] = None, tz_offset_h: Optional[int] = None) -> bool:
    """GLM Coding Plan 峰时：工作日 14:00–18:00（北京时间，可用 ANALYSIS_GLM_PEAK_TZ_OFFSET_H 调）。"""
    off = tz_offset_h if tz_offset_h is not None else _env_int("ANALYSIS_GLM_PEAK_TZ_OFFSET_H", 8)
    tz = timezone(timedelta(hours=off))
    dt = (when or datetime.now(timezone.utc)).astimezone(tz)
    return dt.weekday() < 5 and 14 <= dt.hour < 18


def next_off_peak(when: Optional[datetime] = None, tz_offset_h: Optional[int] = None) -> datetime:
    """给定时刻若处于 GLM 峰时，返回本次峰时结束（18:00）的时刻；否则原样返回。"""
    off = tz_offset_h if tz_offset_h is not None else _env_int("ANALYSIS_GLM_PEAK_TZ_OFFSET_H", 8)
    tz = timezone(timedelta(hours=off))
    dt = (when or datetime.now(timezone.utc)).astimezone(tz)
    if is_glm_peak(dt, off):
        return dt.replace(hour=18, minute=0, second=0, microsecond=0)
    return dt


@dataclass
class Decision:
    action: str  # allow / degrade / reject
    reason: str = ""
    remaining_today: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.action == "allow"


@dataclass
class Budget:
    deep_daily: int
    event_daily: int
    light_daily: int
    max_context_tokens: int
    max_output_tokens: int
    calls_5h: int
    calls_weekly: int
    allow_glm_peak: bool
    # 按传输覆盖（供应商侧的硬约束，各家套餐差异极大 → 不能共用一个数）
    calls_5h_map: Dict[str, int] = field(default_factory=dict)
    calls_weekly_map: Dict[str, int] = field(default_factory=dict)
    daily_map: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Budget":
        return cls(
            deep_daily=_env_int("ANALYSIS_DEEP_DAILY_PER_MODEL", 6),
            event_daily=_env_int("ANALYSIS_EVENT_DAILY_PER_MODEL", 20),
            light_daily=_env_int("ANALYSIS_LIGHT_DAILY_PER_MODEL", 40),
            max_context_tokens=_env_int("ANALYSIS_MAX_CONTEXT_TOKENS", 60000),
            max_output_tokens=_env_int("ANALYSIS_MAX_OUTPUT_TOKENS", 4000),
            calls_5h=_env_int("ANALYSIS_5H_CALLS_PER_MODEL", 30),
            calls_weekly=_env_int("ANALYSIS_WEEKLY_CALLS_PER_MODEL", 150),
            allow_glm_peak=_env_bool("ANALYSIS_ALLOW_GLM_PEAK", False),
            calls_5h_map=_env_map("ANALYSIS_5H_CALLS_MAP"),
            calls_weekly_map=_env_map("ANALYSIS_WEEKLY_CALLS_MAP"),
            daily_map=_env_map("ANALYSIS_DAILY_CALLS_MAP"),
        )

    def daily_for(self, cls_: str) -> int:
        return {"deep": self.deep_daily, "event": self.event_daily}.get(cls_, self.light_daily)

    # 下面三个按传输取值：map 里没配的传输回落到全局默认，行为与改动前一致。
    def calls_5h_for(self, transport: str) -> int:
        return self.calls_5h_map.get((transport or "").lower(), self.calls_5h)

    def calls_weekly_for(self, transport: str) -> int:
        return self.calls_weekly_map.get((transport or "").lower(), self.calls_weekly)

    def daily_for_transport(self, transport: str, cls_: str) -> int:
        """按传输的当日上限。map 配的是「该传输每日总调用数」，与分类上限取较小者。

        分类上限（deep/event/light）是我们自己的**成本与节奏**控制，按传输的
        上限是**供应商套餐**上限，两者都要满足，故取 min。
        """
        base = self.daily_for(cls_)
        override = self.daily_map.get((transport or "").lower())
        return min(base, override) if override else base


class QuotaGuard:
    """进程内计数 + DB 流水（重启后从 llm_quota_usage 回读近 7 天，计数不丢）。"""

    def __init__(self, budget: Optional[Budget] = None):
        self.budget = budget or Budget.from_env()
        self._lock = threading.Lock()
        # (transport, task_class) → [ts_ms, ...] 近 7 天调用时间戳（成功与失败都计——供应商也这么算）
        self._calls: Dict[Tuple[str, str], List[int]] = {}
        self._loaded = False

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            from sqlalchemy import text
            from backend.services.analysis import ledgers

            ledgers.ensure_schema()
            db = ledgers._db()
            try:
                rows = db.execute(
                    text("SELECT transport, task_class, ts_ms FROM llm_quota_usage WHERE ts_ms >= :since"),
                    {"since": int(time.time() * 1000) - 7 * 86400 * 1000},
                ).all()
            finally:
                db.close()
            with self._lock:
                for tr, cls_, ts in rows:
                    # 本地传输的流水只作观测用途，不能回灌进配额窗口 ——
                    # 否则 record 里的豁免会被这里的回读悄悄抵消。
                    if is_local_transport(tr):
                        continue
                    self._calls.setdefault((tr, cls_ or "light"), []).append(int(ts))
        except Exception as exc:
            logger.warning("[QuotaGuard] 回读用量失败（从零开始计数）: %s", exc)

    def _prune(self, key: Tuple[str, str], now: int) -> List[int]:
        lst = self._calls.setdefault(key, [])
        cutoff = now - 7 * 86400 * 1000
        if lst and lst[0] < cutoff:
            self._calls[key] = lst = [t for t in lst if t >= cutoff]
        return lst

    @staticmethod
    def _day_start_ms(now_ms: int) -> int:
        off = _env_int("ANALYSIS_GLM_PEAK_TZ_OFFSET_H", 8)
        tz = timezone(timedelta(hours=off))
        dt = datetime.fromtimestamp(now_ms / 1000, tz)
        return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

    def _counts(self, transport: str, cls_: str, now: int) -> Dict[str, int]:
        with self._lock:
            lst = self._prune((transport, cls_), now)
            day0 = self._day_start_ms(now)
            today = sum(1 for t in lst if t >= day0)
            last5h = sum(1 for t in lst if t >= now - 5 * 3600 * 1000)
            # 5h / 周窗口按传输合计（供应商按 Key 计，不分任务类）
            all_tr = [t for (tr, _c), ts in self._calls.items() if tr == transport for t in ts]
            tr_5h = sum(1 for t in all_tr if t >= now - 5 * 3600 * 1000)
            tr_week = len(all_tr)
        return {"today": today, "class_5h": last5h, "transport_5h": tr_5h, "transport_week": tr_week}

    # ------------------------------------------------------------------ API
    def check(
        self,
        transport: str,
        task: str,
        *,
        est_context_tokens: int,
        max_output_tokens: Optional[int] = None,
        when: Optional[datetime] = None,
    ) -> Decision:
        """调用前预检。transport ∈ {minimax, glm_opencode, deepseek, ollama}。

        本地传输（ollama）只做 token 上限检查，不受次数配额约束 —— 见 is_local_transport。
        """
        self._load()
        b = self.budget
        cls_ = task_class(task)
        if est_context_tokens > b.max_context_tokens:
            return Decision("reject", f"context {est_context_tokens} tokens > 上限 {b.max_context_tokens}")
        if max_output_tokens and max_output_tokens > b.max_output_tokens:
            return Decision("reject", f"max_output_tokens {max_output_tokens} > 上限 {b.max_output_tokens}")
        if is_local_transport(transport):
            return Decision("allow", "", remaining_today=-1)  # -1 = 本地不限量
        # [2026-09-05] 峰时约束按 glm 前缀匹配：glm_opencode_alt（第三票）走同一
        # Coding Plan 套餐，峰时倍率同样适用，不能因传输名不同而绕过。
        if (transport or "").startswith("glm") and cls_ == "deep" and not b.allow_glm_peak and is_glm_peak(when):
            return Decision("degrade", "GLM 峰时（工作日 14:00–18:00 消耗 3×），深度任务改到非峰时")
        now = int(time.time() * 1000)
        c = self._counts(transport, cls_, now)
        daily = b.daily_for_transport(transport, cls_)
        lim_5h = b.calls_5h_for(transport)
        lim_week = b.calls_weekly_for(transport)
        if c["today"] >= daily:
            return Decision("degrade", f"{transport}/{cls_} 今日已用 {c['today']}/{daily}", remaining_today=0)
        if c["transport_5h"] >= lim_5h:
            return Decision("degrade", f"{transport} 5 小时窗已用 {c['transport_5h']}/{lim_5h}", remaining_today=daily - c["today"])
        if c["transport_week"] >= lim_week:
            return Decision("degrade", f"{transport} 周配额已用 {c['transport_week']}/{lim_week}", remaining_today=daily - c["today"])
        return Decision("allow", "", remaining_today=daily - c["today"] - 1)

    def record(
        self,
        transport: str,
        task: str,
        *,
        model: Optional[str],
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        ok: bool,
        run_id: Optional[str] = None,
        cost_usd: Optional[float] = None,
    ) -> None:
        """调用后记账（成功与失败都计入配额——供应商侧同样扣减）。

        本地传输例外：不占配额窗口，但仍写 llm_quota_usage 流水，
        这样耗时 / token / 成功率依旧可观测、可与云端模型横向对比。
        """
        self._load()
        now = int(time.time() * 1000)
        cls_ = task_class(task)
        if not is_local_transport(transport):
            with self._lock:
                self._prune((transport, cls_), now).append(now)
        self._persist(
            ts_ms=now, transport=transport, model=model, task=task, task_class=cls_,
            input_tokens=input_tokens, output_tokens=output_tokens, latency_ms=latency_ms,
            ok=ok, cost_usd=cost_usd, run_id=run_id,
        )

    # [2026-09-04] 落库单独成方法，不再内联在 record 里。
    # 原因不是美观：单测要屏蔽落库只能 patch 一个**存在的**方法名。此前测试写的是
    # `monkeypatch.setattr(QuotaGuard, "_persist", ..., raising=False)`，而当时并无
    # _persist，raising=False 让这个无效 patch 静默通过 —— 于是每跑一次单元测试，
    # 就往**生产**的 llm_quota_usage 写 65 条 latency_ms=1 的假流水（ollama 60 +
    # deepseek 5），直接顶掉 DeepSeek 的 5h 窗与 light 日配额，把真实调用误拦掉。
    def _persist(
        self, *, ts_ms: int, transport: str, model: Optional[str], task: str, task_class: str,
        input_tokens: int, output_tokens: int, latency_ms: int, ok: bool,
        cost_usd: Optional[float] = None, run_id: Optional[str] = None,
    ) -> None:
        try:
            from sqlalchemy import text
            from backend.services.analysis import ledgers

            ledgers.ensure_schema()
            db = ledgers._db()
            try:
                db.execute(
                    text(
                        "INSERT INTO llm_quota_usage (ts_ms, transport, model, task, task_class, input_tokens, output_tokens, "
                        "latency_ms, ok, cost_usd, run_id) VALUES (:ts, :tr, :model, :task, :cls, :it, :ot, :lat, :ok, :cost, :run)"
                    ),
                    {
                        "ts": ts_ms, "tr": transport, "model": model, "task": task, "cls": task_class,
                        "it": int(input_tokens or 0), "ot": int(output_tokens or 0), "lat": int(latency_ms or 0),
                        "ok": bool(ok), "cost": cost_usd, "run": run_id,
                    },
                )
                db.commit()
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[QuotaGuard] 用量落库失败: %s", exc)

    def snapshot(self) -> Dict[str, Any]:
        """看板 / API：各传输今日与窗口用量、预算、峰时状态。"""
        self._load()
        now = int(time.time() * 1000)
        b = self.budget
        out: Dict[str, Any] = {
            "budget": {
                "deep_daily": b.deep_daily, "event_daily": b.event_daily, "light_daily": b.light_daily,
                "max_context_tokens": b.max_context_tokens, "max_output_tokens": b.max_output_tokens,
                "calls_5h": b.calls_5h, "calls_weekly": b.calls_weekly, "allow_glm_peak": b.allow_glm_peak,
            },
            "glm_peak_now": is_glm_peak(),
            "next_off_peak": next_off_peak().isoformat(),
            "transports": {},
        }
        # [2026-09-04] 本地传输一并纳入看板。它们不受次数配额约束，但流水照写
        # （见 record 注释：耗时 / token / 成功率要能与云端横向对比），此前硬编码
        # 只列三家云端 → 本地模型跑了多少、成功率如何在看板上完全不可见。
        for tr in ("minimax", "glm_opencode", "deepseek", *sorted(local_transports())):
            per_class = {}
            for cls_ in ("deep", "event", "light"):
                c = self._counts(tr, cls_, now)
                per_class[cls_] = {"today": c["today"], "daily_budget": b.daily_for_transport(tr, cls_)}
            c_any = self._counts(tr, "deep", now)
            _local = is_local_transport(tr)
            out["transports"][tr] = {
                "classes": per_class,
                "calls_5h": c_any["transport_5h"],
                # 本地传输不受次数配额约束，上限报 -1（与 Decision.remaining_today 同约定）。
                # 若照报全局默认，看板上会显示成 "0/30" —— 看着像有额度限制，实则不受限。
                "calls_5h_budget": -1 if _local else b.calls_5h_for(tr),
                "calls_week": c_any["transport_week"],
                "calls_week_budget": -1 if _local else b.calls_weekly_for(tr),
                "local": _local,
            }
        return out


_guard: Optional[QuotaGuard] = None
_guard_lock = threading.Lock()


def get_quota_guard() -> QuotaGuard:
    global _guard
    if _guard is None:
        with _guard_lock:
            if _guard is None:
                _guard = QuotaGuard()
    return _guard
