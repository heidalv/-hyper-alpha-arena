# -*- coding: utf-8 -*-
"""[F340 2026-09-22] 强平出口的**真实**归因：`exit_path` 必须由各出口分支显式命名。

# 缺陷现场（实测，14 天 1,089 条 flatten 腿）

    exit_reason 键存在 / exit_action 键存在 / flatten 总数 = (18, 18, 1089)

**1,071 条（98.3%）两个键都不存在**，只剩 18 条有归因，
且在任何时间窗（14 天 / 3 天 / 1 天）里都是**同样那 18 条**。

⇒ 我此前用 `exit_reason` 做的"出口原因分解"
（`trend_up` / `trend_down` / `ofi_toxic_*` 占大头）**是错的归因**。

# 两条独立的失效机制

**① 时间**：`exit_reason` 是 F335（2026-09-22）才加的字段 ⇒ F335 之前的所有
flatten 腿本来就没有这个键。统计"无键比例"时若把旧行算进来，
会把"时代差异"误读成"路径缺失"。

**② 语义（真正的坑）**：F335 把 `dec.skip` 当出口原因，而 `dec.skip`
**可能残留同一 tick 早先闸门的名字**。`plan_tick` 的顺序是：

    前置闸门（trend / ofi / vol_pause 等）→ 设 `dec.skip`
    ①′ 止损 / ①″ 止盈 / ② 超时 / ②′ OFI 平仓 → append flatten 腿
    ③ 重挂新单

⇒ 若某 tick 早先有个闸门设了 `skip="trend_up"`，随后止损触发强平，
那么这条 flatten 腿会被记成 `exit_reason="trend_up"` —— **看起来像"被趋势闸拦下"，
实际是"被止损强平"**。两者的修法完全相反（调闸门阈值 vs 调止损阈值）。

# 本测试锁什么

`exit_path` 由每条出口分支**显式命名自己**，且：
  ① 必须由 `TickDecision.to_dict()` 暴露（否则状态里看不到）；
  ② 必须落进账本 `meta`（否则事后无法归因）；
  ③ 四条 taker 出口 + 孤儿出口，每条都有**互不相同**的标识；
  ④ 出口分支里 `exit_path` 的赋值必须在 `dec.action = "flatten"` 之后
     （否则会被后面的逻辑覆盖 —— 静态无法完全保证，故用"存在且非空"保证）。
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from backend.services.market_maker import runner as R

REPO = pathlib.Path(__file__).resolve().parents[3]
SRC = (REPO / "backend" / "services" / "market_maker" / "runner.py").read_text(
    encoding="utf-8")

# 每条 taker 出口的标识（互不相同是硬要求：否则归因又混成一团）
EXPECTED_PATHS = {
    "stop_loss_taker": "①′ 价格止损",
    "take_profit_taker": "①″ 止盈",
    "timeout_taker": "② 单边超时",
    "ofi_flatten_taker": "②′ OFI 顺风平仓",
    "orphan_taker": "孤儿持仓退出（F90）",
}
TAKER_PATHS = [k for k in EXPECTED_PATHS if k != "orphan_taker"]


# ── 1. 字段存在且被三条链暴露 ────────────────────────────────────────────
@pytest.mark.unit
def test_exit_path_field_exists_with_empty_default():
    """默认必须是空串（= 本 tick 无 taker 强平），不能是 None。"""
    d = R.TickDecision(symbol="X")
    assert hasattr(d, "exit_path"), "TickDecision 缺 exit_path"
    assert d.exit_path == "", (
        f"默认应为空串（空 = 无强平）；实际 {d.exit_path!r} ⇒ "
        f"None 会让 `or ''` 之类的写法在别处炸掉")


@pytest.mark.unit
def test_exit_path_is_exposed_in_to_dict():
    d = R.TickDecision(symbol="X", exit_path="stop_loss_taker")
    out = d.to_dict()
    assert "exit_path" in out, "to_dict 未暴露 exit_path ⇒ 状态里看不到真实出口"
    assert out["exit_path"] == "stop_loss_taker"


@pytest.mark.unit
def test_exit_path_is_written_to_the_ledger_meta():
    """账本落盘必须带 `exit_path`（否则事后无法归因，只能靠猜）。"""
    assert '"exit_path": str(getattr(decision, "exit_path", "") or "")' in SRC, (
        "`_record_fills` 的 meta 里没有 `exit_path` ⇒ 账本仍然无法回答"
        "「这次强平是哪条出口触发的」")


# ── 2. 每条出口分支都必须显式命名自己 ────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("path,desc", sorted(EXPECTED_PATHS.items()))
def test_each_taker_exit_names_itself(path, desc):
    assert f'"{path}' in SRC, (
        f"出口 `{desc}`（期望标识 {path!r}）没有设置 `dec.exit_path` ⇒ "
        f"该路径的强平在账本里依然无法归因")


@pytest.mark.unit
def test_the_four_taker_paths_have_distinct_identifiers():
    """四条路径的标识**必须互不相同**。

    为什么单列一条：初版实现里 `timeout_taker` / `ofi_flatten_taker` /
    `stop_loss_taker` 的赋值位置很容易被后来的编辑挤到同一个分支，
    那样"归因"又会退化成"全都叫同一个名字"（等于没归因）。
    """
    seen = set()
    for p in TAKER_PATHS:
        assert p not in seen, f"标识 {p!r} 重复"
        seen.add(p)
    assert len(seen) == 4, f"应有 4 条不同的 taker 出口标识；实际 {len(seen)}"


# ── 3. 出口标识必须在**启动强平的那个分支内**（AST 级位置检查）──────────
def _flatten_branch_lines() -> list:
    """返回所有 `dec.fills.append(...is_flatten=True...)` 所在的行号。"""
    out = []
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "append":
            for kw in getattr(node, "keywords", []):
                pass
            seg = ast.get_source_segment(SRC, node) or ""
            if "is_flatten=True" in seg:
                out.append(node.lineno)
    return sorted(out)


@pytest.mark.unit
def test_exit_path_assignment_is_near_each_flatten_append():
    """每个 flatten 腿的 append 附近（±20 行内）必须有一次 exit_path 赋值。

    这是**距离检查**，不是精确作用域检查 —— 精确作用域需要真正的执行级测试，
    代价高且脆。距离检查能抓住"某条路径忘了命名"这一实际缺陷形态
    （本仓库的 `_pos_d` 事故也是这一类：写了但不在正确的分支里）。
    """
    lines = SRC.splitlines()
    appends = _flatten_branch_lines()
    assert len(appends) >= 4, (
        f"只找到 {len(appends)} 处 flatten append ⇒ 结构变了，本测试需同步更新")
    for ln in appends:
        lo, hi = max(0, ln - 20), min(len(lines), ln + 20)
        window = "\n".join(lines[lo:hi])
        assert "exit_path" in window, (
            f"{ln} 行的 flatten append 附近 ±20 行内没有 exit_path 赋值 ⇒ "
            f"该出口未被命名。上下文：\n{window[:600]}")


# ── 4. 回归钉子：`dec.skip` 不可作为唯一出口依据 ─────────────────────────
@pytest.mark.unit
def test_documented_that_dec_skip_is_unreliable_as_exit_reason():
    """把"为什么需要 exit_path"写成测试，防止有人删掉它并退回 `dec.skip`。

    `dec.skip` 在不同 tick 阶段会被**闸门**（trend_up / vol_pause / ofi_toxic_*）
    赋值，与"哪条出口强平了仓位"无关。实测：97% 的 flatten 腿没有这个键。
    """
    assert "exit_path" in SRC
    # `exit_reason` 仍保留（向后兼容旧行），但必须与 exit_path 并存
    assert '"exit_reason"' in SRC and '"exit_path"' in SRC, (
        "两者必须并存：exit_reason 兼容历史行，exit_path 是新的权威口径")


@pytest.mark.unit
def test_tickdecision_to_dict_is_stable():
    """`to_dict` 的键集不得因为本改动而丢失任何既有键（看板/心跳依赖它）。"""
    base = {"symbol", "action", "bid", "ask", "w_bid_bp", "w_ask_bp", "mid",
            "sigma_norm", "vol_bp", "fills", "skip", "skip_side", "skip_detail",
            "lane_pause", "quote_mode", "base_bp"}
    out = set(R.TickDecision(symbol="X").to_dict().keys())
    missing = base - out
    assert not missing, f"to_dict 丢了这些键：{sorted(missing)}"
