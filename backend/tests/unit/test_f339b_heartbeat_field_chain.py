# -*- coding: utf-8 -*-
"""[F339 2026-09-22] 心跳字段链的回归：`runner.status()` → 心跳白名单 → `_WORKER_KEYS`。

# 为什么需要这个测试（一天内踩了三次的同一个坑）

字段从「引擎里算出来」到「能被读到」要穿过**三道显式白名单**：

    ① `runner.status()` 的字典          —— 例：`lane_pause_counts`
    ② `scripts/mm_lane_worker.py::_snapshot()` 的白名单
    ③ `worker_status.py::_WORKER_KEYS` 的白名单（API 侧合并）

**任何一道漏了，字段就静默变成 `None`** —— 不报错、不警告，
只是"改了但看不出来"。本仓库的记录：

    F285  `states`          —— 漏了 ① / ② ⇒ 看板持仓滞后
    F288  `avg_width_bp` 等 —— 只在真正 tick 的进程里有值
    F327  `param_authority` —— 漏了 ② ⇒ 心跳里是空（注释里已写明警告）
    F339  `lane_pause_*`    —— **又漏了 ②**（本次，尽管 F327 已警告过）

⇒ 只靠注释防不住。本测试把三道白名单**逐字段对齐**，让漏项在 CI 里就红。

# 判据的取舍

不能简单断言「`runner.status()` 的所有键都必须在心跳里」—— `recent_ticks` /
`fill_notes` 是**故意**不进心跳的（会让心跳文件膨胀到 MB 级，
而它们是审计环，按需从 `/shadow` 读）。所以这里用**显式清单**：
每个"必须能被心跳读到"的键列出它应该在哪些白名单里。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
WORKER_SRC = (REPO / "scripts" / "mm_lane_worker.py").read_text(encoding="utf-8")

# 字段 → 它必须出现的白名单（"heartbeat" = mm_lane_worker._snapshot 的元组）
REQUIRED = {
    # F339：车道闸的可见性
    "lane_pause_counts": {"heartbeat", "worker_keys"},
    "lane_pause_last": {"heartbeat", "worker_keys"},
    "day_pnl_usd": {"heartbeat", "worker_keys"},
    "day_pnl_limit_usd": {"heartbeat", "worker_keys"},
    # F327：谁在权威
    "param_authority": {"heartbeat"},
    # F288：报价行为读数
    "avg_width_bp": {"heartbeat", "worker_keys"},
    "avg_sigma_all": {"heartbeat", "worker_keys"},
    # F285：每币运行态
    "states": {"heartbeat", "worker_keys"},
}

# 故意**不**进心跳的键（列出了原因，防止有人"顺手补齐"把心跳撑爆）
INTENTIONALLY_ABSENT = {
    "recent_ticks": "审计环（60 条 tick 输入），按需从 /shadow 读",
    "fill_notes": "审计环（最近 60 笔成交上下文），同上",
    "spread_buckets": "逐币原始计数，看板不用",
}


def _snapshot_tuple_fields() -> set:
    """从 `_snapshot` 里取出那个字段白名单元组的所有字符串常量。"""
    tree = ast.parse(WORKER_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_snapshot":
            out = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.For):
                    it = sub.iter
                    if isinstance(it, (ast.Tuple, ast.List)):
                        for el in it.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                out.add(el.value)
            return out
    raise AssertionError("找不到 `_snapshot` 函数 ⇒ 代码结构变了，本测试需要同步更新")


def _worker_keys() -> set:
    from backend.services.market_maker import worker_status as WS
    return set(WS._WORKER_KEYS)


def _runner_status_keys() -> set:
    """静态取 `runner.status()` 返回字典的字面键（不实例化 lane，代价低且稳）。"""
    src = (REPO / "backend" / "services" / "market_maker" / "runner.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "status":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    keys = set()
                    for k in sub.value.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            keys.add(k.value)
                    if keys:
                        return keys
    raise AssertionError("找不到 `status()` 的返回字典 ⇒ 结构变了，需同步更新")


# ── 1. 三道白名单逐字段对齐 ──────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("field,needles", sorted(REQUIRED.items()))
def test_field_reaches_every_required_whitelist(field, needles):
    hb = _snapshot_tuple_fields()
    wk = _worker_keys()
    for n in needles:
        if n == "heartbeat":
            assert field in hb, (
                f"`{field}` 不在 `mm_lane_worker._snapshot()` 的心跳白名单里 "
                f"⇒ 心跳里会是 None（F327/F339 的同一个坑）")
        elif n == "worker_keys":
            assert field in wk, (
                f"`{field}` 不在 `worker_status._WORKER_KEYS` 里 "
                f"⇒ worker 写了也传不到 API/看板")


@pytest.mark.unit
def test_required_fields_exist_in_runner_status():
    """① 层：这些键必须真的由 `runner.status()` 产出（否则前两道白名单是空的）。"""
    keys = _runner_status_keys()
    for f in REQUIRED:
        assert f in keys, f"`{f}` 不在 `runner.status()` 的返回字典里"


@pytest.mark.unit
def test_intentionally_absent_fields_are_documented_not_dropped():
    """反向钉子：故意不进心跳的键必须**有登记**。

    防的是下一个人看到"心跳缺字段"就顺手补进去（`recent_ticks` 会让
    心跳文件从 ~3KB 涨到 MB 级，且它们本来就是按需读的审计环）。
    """
    hb = _snapshot_tuple_fields()
    for f, why in INTENTIONALLY_ABSENT.items():
        assert f not in hb, (
            f"`{f}` 被加进心跳白名单了 —— 若确实需要，请更新本测试的 "
            f"INTENTIONALLY_ABSENT（原登记理由：{why}）")


# ── 2. 现场证据：本次缺陷的直接形态 ──────────────────────────────────────
@pytest.mark.unit
def test_snapshot_iterates_a_literal_tuple_not_starred_status():
    """心跳必须是**显式白名单**（有意设计），不能改成 `**st` 整份透传。

    为什么保留这个"缺陷形状"：白名单是**故意的** —— 整份透传会让
    `recent_ticks` / `fill_notes` 进心跳，文件从 3KB 涨到 MB 级，
    而心跳是**每个 tick 都重写**的文件（本会话实测 out.log 出现过 100MB+）。
    所以这里的正确做法是"白名单 + 测试对齐"，不是"取消白名单"。
    """
    assert "out[k] = st.get(k)" in WORKER_SRC, (
        "心跳不再是逐字段白名单取值 ⇒ 若改成了 `out.update(st)`，"
        "请先确认 recent_ticks/fill_notes 不会把心跳文件撑爆")
