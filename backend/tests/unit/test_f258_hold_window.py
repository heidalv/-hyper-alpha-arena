# -*- coding: utf-8 -*-
"""[F258 2026-09-20] 交易时长窗口（30s ~ 5min）契约测试。

## 背景

用户给定：「交易时间进行锁定吧，既然都是把时间锁定在 **30秒到5分钟之内**，
这个是有验证过的」。这正是本模块最早的定位（「中短期高频交易 · 30s–5min」）。

改前实测（H26，8h，66 个往返）：
    持仓时长 中位 70s   p25 45s   p75 167s   **max 3612s**
    ⇒ 14% 的往返超出 5 分钟，最长 60 分钟
    出场腿 −$0.032/往返 = 入场腿盈利（+$0.011/往返）的 3 倍

## 本文件锁什么

  1. 默认值就是用户窗口：`max_one_side_seconds=300`、`min_hold_seconds=30`；
  2. `hold_window_ok` 是纯函数、边界语义明确、可关闭（回退）；
  3. **下限只管强制出口** —— 不得拦住被动成交；
  4. 下限在 `runner.plan_tick` 里**真的被读**（防"静默死闸"，本仓库踩过多次）。

## 不锁什么

不锁"窗口一定赚钱"。这条窗口的价值在于**限制长尾暴露**与**避免白付出场成本**，
不是保证正期望 —— 见 `H23-H26` 报告：出场腿亏损的机制还没完全定位。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as R  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    MAX_HOLD_SEC_DEFAULT,
    MIN_HOLD_SEC_DEFAULT,
    LaneRiskLimits,
    hold_window_ok,
)


# ── 1. 默认值必须就是用户给定的窗口 ─────────────────────────────

def test_defaults_are_the_user_window():
    lim = LaneRiskLimits()
    assert lim.max_one_side_seconds == 300.0, (
        f"上界默认应为 300s（5 分钟），实为 {lim.max_one_side_seconds}。"
        "用户给定窗口 30s–5min，且 900s 实测导致 14% 往返超窗（最长 60 分钟）。"
    )
    assert lim.min_hold_seconds == 30.0, (
        f"下限默认应为 30s，实为 {lim.min_hold_seconds}。"
    )
    assert MIN_HOLD_SEC_DEFAULT == 30.0
    assert MAX_HOLD_SEC_DEFAULT == 300.0


def test_env_can_override_upper_bound():
    """上界仍可被环境变量覆盖（保持可配置性）。"""
    src = inspect.getsource(LaneRiskLimits)
    assert "MM_MAX_ONE_SIDE_SEC" in src, "上界失去了环境变量覆盖能力"


# ── 2. hold_window_ok 的边界语义 ────────────────────────────────

def test_window_boundaries():
    t0 = 1000.0
    # 窗口内
    ok, why = hold_window_ok(opened_ts=t0, now_ts=t0 + 60.0)
    assert ok and why == ""
    # 刚好下限 ⇒ 允许（含端点）
    ok, why = hold_window_ok(opened_ts=t0, now_ts=t0 + 30.0)
    assert ok, "下限是含端点的：age == min_hold_sec 应放行"
    # 低于下限 ⇒ 拒绝，原因是 hold_too_young
    ok, why = hold_window_ok(opened_ts=t0, now_ts=t0 + 29.9)
    assert (not ok) and why == "hold_too_young"
    # 刚好上限 ⇒ 允许（上界由 max_one_side_seconds 在别处处理）
    ok, _ = hold_window_ok(opened_ts=t0, now_ts=t0 + 300.0)
    assert ok
    # 超过上限 ⇒ 拒绝，原因 hold_too_old
    ok, why = hold_window_ok(opened_ts=t0, now_ts=t0 + 300.1)
    assert (not ok) and why == "hold_too_old"


def test_zero_disables_each_side_independently():
    t0 = 1000.0
    # 关下限：刚开仓也允许
    ok, _ = hold_window_ok(opened_ts=t0, now_ts=t0 + 1.0, min_hold_sec=0.0)
    assert ok, "min_hold_sec=0 必须完全关闭下限"
    # 关上界：超很久也允许
    ok, _ = hold_window_ok(opened_ts=t0, now_ts=t0 + 99999.0, max_hold_sec=0.0)
    assert ok, "max_hold_sec=0 必须完全关闭上界"
    # 两个都关 = 完全不限（与旧行为逐字一致，可一键回退）
    ok, why = hold_window_ok(opened_ts=t0, now_ts=t0 + 99999.0,
                             min_hold_sec=0.0, max_hold_sec=0.0)
    assert ok and why == ""


def test_unknown_open_ts_fails_open():
    """无持仓/建仓时刻未知 ⇒ 放行。

    宁可放行也不要因为缺数据把库存永久卡死 —— 卡死的库存是**无限的持仓风险**，
    而放行的代价只是少拦一次强平。
    """
    for bad in (0.0, -1.0, None):
        ok, why = hold_window_ok(opened_ts=bad, now_ts=1000.0)  # type: ignore[arg-type]
        assert ok and why == "", f"opened_ts={bad!r} 应 fail-open"


# ── 3. 下限必须真的被 runner 读（防静默死闸）────────────────────

def test_runner_reads_min_hold():
    src = Path(R.__file__).read_text(encoding="utf-8")
    assert "min_hold_seconds" in src, (
        "`runner.py` 没有读取 `min_hold_seconds` ⇒ 定义了但没用 = 静默死参数。"
    )
    assert "_force_exit_allowed" in src, "缺少 `_force_exit_allowed` 包装函数"


def test_force_exit_helper_dedupes_upper_bound():
    """`_force_exit_allowed` **不得**重复拦上界。

    上界已在 ② 超时分支里实现（`> max_one_side_seconds` 才触发）。
    若这里再拦一次上界，会出现"上界到了但被本函数拒绝"的矛盾，
    库存将永远无法被超时清掉。
    """
    src = inspect.getsource(R._force_exit_allowed)
    assert "max_hold_sec=0.0" in src, (
        "`_force_exit_allowed` 必须把上界设为 0（不拦），"
        "否则与 ② 超时分支冲突，库存可能被永久卡死。"
    )


# ── 4. 下限只管强制出口，不得拦被动成交 ─────────────────────────

def _code_only(src: str) -> str:
    """剥掉注释，只留可执行文本。

    为什么必须剥：闸门段的**注释**里**故意**解释了 `hold_too_young` 的语义，
    而那段注释在源码顺序上落在 `plan_tick` 起点之后（即"成交判定段"的切片范围内）。
    不剥注释，`"hold_too_young" not in seg` 会被自己的注释触发假失败
    —— 与 F326 / F252 / F254 同一类错误，本仓库已第 4 次踩到。
    """
    out = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        if "#" in ln:
            ln = ln.split("#", 1)[0]
        out.append(ln)
    return "\n".join(out)


def test_guard_is_only_on_forced_exits():
    """被动成交路径里不得出现时长下限判断（只看**可执行语句**）。

    ## 三个必须遵守的测试写法（每一条都被实测踩到过）

    1. **不能用"从锚点取 N 字符"的窗口** —— 窗口会跨进下一段，造成假阳性。
       正确做法：按代码结构（ASCII 标识符位置）切段。
    2. **不能用中文注释做锚点** —— `runner.py` 是**混合编码**文件
       （部分行是 GBK 乱码：`# 锛堜笅鏂规姤浠锋祦绋嬶級...`），
       用 UTF-8 读进来后 `find("① 旧挂单成交判定")` **返回 -1**。
    3. **必须剥注释再断言** —— 闸门段的注释里故意写了 `hold_too_young` 的说明，
       不剥会被自己的注释触发假失败。

    结构（按 ASCII 锚点定位）：
        `def plan_tick(`                    ← 段落起点
        `_exit_ok, _exit_why = ...`         ← 强制出口段起点（下限闸门）
        `if _sl_hit and (_grace <= 0`       ← 止损分支（**应**含 `_exit_ok`）
    """
    src = Path(R.__file__).read_text(encoding="utf-8")
    i_plan = src.find("def plan_tick(")
    i_guard = src.find("_exit_ok, _exit_why = _force_exit_allowed(")
    i_sl = src.find("if _sl_hit and (_grace <= 0")
    assert i_plan > 0, "找不到 `def plan_tick(`"
    assert i_guard > i_plan, "找不到 `_exit_ok, _exit_why = _force_exit_allowed(...)`"
    assert i_sl > i_guard, "止损分支应在闸门之后"

    # 成交判定段 = plan_tick 起点 → 闸门起点，**只看可执行语句**
    seg_code = _code_only(src[i_plan:i_guard])
    assert "_exit_ok" not in seg_code, (
        "被动成交判定段（`def plan_tick(` → 下限闸门之间）的**可执行语句**里"
        "出现了 `_exit_ok` ⇒ 时长下限会拦住自然出库。那是错的："
        "对手方主动打过来是自然出库，不付成本，任何时候都该允许。"
    )
    assert "hold_too_young" not in seg_code, (
        "被动成交判定段的**可执行语句**里出现了 `hold_too_young` ⇒ 同上。"
    )
    # 反向确认锚点有效：止损分支的可执行语句里**必须**有它
    assert "_exit_ok" in _code_only(src[i_sl:i_sl + 900]), (
        "止损分支里没有 `_exit_ok` ⇒ 下限对止损失效"
    )


def test_stop_loss_respects_floor():
    """止损路径必须被下限拦住（否则下限形同虚设）。"""
    src = Path(R.__file__).read_text(encoding="utf-8")
    idx = src.find("if _sl_hit and (_grace <= 0")
    assert idx > 0
    seg = src[idx:idx + 900]
    assert "_exit_ok" in seg, (
        "止损平仓分支没有检查 `_exit_ok` ⇒ 未满 30 秒也会被强平，下限失效。"
    )
