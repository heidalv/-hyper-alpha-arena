# -*- coding: utf-8 -*-
"""[F254 2026-09-20] 队列优先保持（`quote_hold_tol_bp`）回归测试。

## 背景

真实交易所里"重挂报价" = 撤单 + 重新排队 ⇒ **队列位置清零**（排到当前挂量最后）。
我们此前每 15s tick 无条件执行 `state.quote_bid, state.quote_ask = dec.bid, dec.ask`，
等于**每个 tick 都把自己的队列位置推倒重来**。

## 代价是多少（H19 实测，tick 级真实数据，15s 报价节奏，12h）

    `scripts/h19_queue_fill_model.py`
      队尾（LA = 全部挂量）成交率 24–37%，mk@1s −0.22 ~ −0.62bp
      队首（LA = 0）      成交率 85–95%，mk@1s −0.12 ~ +0.27bp
      ⇒ **队首 − 队尾 = +0.42bp @1s**（4 个有效样本币）
    我们实测净边际 = **−0.60bp** ⇒ 队列位置与整个策略盈亏**同量级**。
    论文 Albers et al. 2502.18625v2 Table 1 独立给出同格内差 0.12–0.86bp，量级一致。

## 本文件锁什么

`quote_hold_tol_bp` 是**行为契约**，不是经济性判断。它必须：
  1. 存在于 `LaneRiskLimits`（否则注册表热更新会把它静默丢掉 ⇒ 死参数）；
  2. 默认 > 0（默认启用）；
  3. 语义是"新报价与当前挂单相对差小于容差时保留原价与原 `quote_ts`"；
  4. 能被显式关闭（0 或负 ⇒ 与旧行为逐字一致，可一键回退）；
  5. 且实现里**确实**用了它（不能只是定义了不读 —— 本仓库已多次出现"静默死闸"）。

## 不锁什么

**不锁 paper 回测的收益**：我们的成交模型不建队列，"保持"与"重挂"产出**完全相同**的
成交 ⇒ 本参数对纸面结果恒等于 0 收益。它是纯实盘准备。
任何声称"因为队列保持所以回测变好了"的说法都是错的，测试也不该那样写。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as R  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits  # noqa: E402


def _code_lines(path: Path) -> str:
    """剥掉注释只留可执行文本。

    为什么必须剥：`runner.py` 在实现处写了长注释，其中**故意**引用了旧写法
    `state.quote_bid, state.quote_ask = dec.bid, dec.ask` 作为反例。
    不剥注释，"断言旧写法不存在"会被自己的注释触发假失败（F326 的同类错误）。
    """
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        if "#" in ln:
            ln = ln.split("#", 1)[0]
        out.append(ln)
    return "\n".join(out)


# ── 1. 参数必须在 dataclass 里（否则热更新会丢）──────────────────

def test_field_exists_and_defaults_on():
    assert "quote_hold_tol_bp" in LaneRiskLimits.__dataclass_fields__, (
        "`quote_hold_tol_bp` 必须存在于 `LaneRiskLimits`：`_maybe_reload_meta` 按"
        " `__dataclass_fields__` 过滤注册表参数 ⇒ 不在里面就会被静默丢弃（死参数）。"
    )
    d = LaneRiskLimits()
    assert d.quote_hold_tol_bp > 0, "默认必须 > 0（默认启用队列保持）"


def test_registry_override_is_honored():
    """注册表能覆盖它（0 或负 = 关闭，可一键回退）。"""
    lim = LaneRiskLimits(quote_hold_tol_bp=0.0)
    assert lim.quote_hold_tol_bp == 0.0
    lim2 = LaneRiskLimits(quote_hold_tol_bp=-1.0)
    assert lim2.quote_hold_tol_bp == -1.0


# ── 2. 实现必须真的读它（防"静默死闸"）──────────────────────────

def test_runner_actually_reads_the_field():
    src = _code_lines(Path(R.__file__))
    assert "quote_hold_tol_bp" in src, (
        "`runner.py` 没有读取 `quote_hold_tol_bp` ⇒ 定义了但没用 = 静默死参数。"
    )


def test_old_unconditional_requote_is_gone():
    """旧的"无条件重设报价"写法必须消失。"""
    src = _code_lines(Path(R.__file__))
    assert "state.quote_bid, state.quote_ask = dec.bid, dec.ask" not in src, (
        "仍然存在无条件重挂 `state.quote_bid, state.quote_ask = dec.bid, dec.ask`。\n"
        "这等于每个 tick 把队列位置推倒重来 —— H19 实测代价 0.42bp@1s，"
        "与我们整个净边际（0.60bp）同量级。"
    )


def test_timestamp_not_refreshed_while_held():
    """保持期间 `quote_ts` 不得前进 —— 否则 `max_quote_age_sec` 会误判为新单。

    实现里应能看到"只在**未保持**时才写 `state.quote_mid, state.quote_ts`"这个条件。
    """
    src = _code_lines(Path(R.__file__))
    assert "state.quote_mid, state.quote_ts = mid, now_ts" in src
    # 该赋值必须被一个"没有保持"的条件守护
    idx = src.index("state.quote_mid, state.quote_ts = mid, now_ts")
    window = src[max(0, idx - 400):idx]
    assert "_held_bid" in window or "_held_ask" in window, (
        "`state.quote_mid, state.quote_ts = mid, now_ts` 没有被 `_held_*` 条件守护：\n"
        "保持队列位置时若刷新了 `quote_ts`，`max_quote_age_sec` 就永远触发不了，"
        "陈旧挂单保护失效（F89a 的幻影成交事故会回来）。"
    )


# ── 3. 语义：钉住"保持"与"重挂"的分界 ────────────────────────────

def test_hold_tolerance_boundary_math():
    """容差的语义边界（用纯算术表达，避免依赖 runner 内部状态）。"""
    tol = LaneRiskLimits().quote_hold_tol_bp
    old = 100.0
    # 中价微抖 0.1bp ⇒ 应判为"同价位"（保持）
    assert abs(old * (1 + 0.1 / 1e4) - old) / old * 1e4 < tol
    # 价格移动 1bp ⇒ 必须判为"移动了"（重挂）
    assert abs(old * (1 + 1.0 / 1e4) - old) / old * 1e4 > tol


def test_hold_disabled_is_bitwise_old_behavior():
    """`quote_hold_tol_bp <= 0` 时必须完全跳过该逻辑（可一键回退）。"""
    src = _code_lines(Path(R.__file__))
    assert "_hold_tol_bp > 0" in src, (
        "缺少 `_hold_tol_bp > 0` 守卫 ⇒ 设为 0 时无法真正关闭。"
    )


# ── 4. 诚实性断言：不允许把"纸面收益改善"写进注释当作依据 ─────────

def test_no_false_paper_pnl_claim():
    """实现附近的说明必须承认"对纸面回测无影响"，不得暗示它能提高回测收益。"""
    raw = Path(R.__file__).read_text(encoding="utf-8")
    idx = raw.find("quote_hold_tol_bp")
    assert idx > 0
    window = raw[max(0, idx - 6000):idx + 2000]
    assert "纸面" in window or "paper" in window.lower(), (
        "实现附近必须写明「对纸面回测无影响，是纯实盘准备」，"
        "否则后人会误以为这是提升回测收益的改动。"
    )
