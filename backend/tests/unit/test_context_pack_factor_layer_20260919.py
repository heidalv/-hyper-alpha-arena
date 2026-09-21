# -*- coding: utf-8 -*-
"""轮107 主脑上下文**因子层**回归测试（2026-09-19）。

## 背景（实测断点）

现役主脑 `mlto/brain.py:1201` 用的上下文是 `context_pack.build("midlong_thesis", ...)`，
而它的 5 个层（market / flows / positions / performance / config）**一个因子字段都没有**
—— 全局 grep `midlong_factors|factor` 在 `analysis/` 下只命中 schema 字段 `key_factors`
与共识用的 `factor_overlap`。写好的因子注入只存在于 `mlto/qual_layer`
（旧 orchestrator 链路，`brain.py:1488` 明写"随旧 orchestrator 9/5 下线后成为死路径"）。

⇒ 结论：**长线/中线因子对主脑此前是完全不可见的**；轮106 修好的是死链路上的方向语义。

本测试锁死新加的 `factors` 层：默认只在 `midlong_thesis` 开启、带方向语义、按 |IC| 排序、
包含中线因子路由结论与（此前全库无生产调用方的）`MidLongQuantBrief`。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.analysis import context_pack as CP

_SYMS = ["BTC"]


@pytest.fixture(scope="module")
def pack_layers():
    pack = CP.build("midlong_thesis", symbols=_SYMS)
    return pack.layers


# ══════════════════════════════════════════════════════════════════════
# ① 层是否存在、是否只在需要的任务上开
# ══════════════════════════════════════════════════════════════════════

def test_factor_layer_present_for_midlong_thesis(pack_layers):
    fl = pack_layers.get("factors")
    assert isinstance(fl, dict) and fl.get("symbols"), "主脑论文任务必须带因子层"
    assert fl.get("role") == "证据，非指令"


def test_factor_layer_not_built_for_other_tasks():
    """其余任务默认不带（每币一次快照 + 一次路由判定，不该给全 universe 白跑）。"""
    p = CP.build("daily_brief", symbols=_SYMS)
    assert "factors" not in p.layers


def test_factor_layer_can_be_requested_explicitly():
    p = CP.build("daily_brief", symbols=_SYMS, layers=("market", "factors"))
    assert isinstance(p.layers.get("factors"), dict)


def test_factor_layer_can_be_disabled(monkeypatch):
    monkeypatch.setattr("backend.config.settings.CONTEXT_PACK_FACTORS_ENABLED", False, raising=False)
    p = CP.build("midlong_thesis", symbols=_SYMS)
    assert "factors" not in p.layers, "开关关掉后不得再建层"


# ══════════════════════════════════════════════════════════════════════
# ② 内容：方向语义 + 排序 + 路由结论 + 量化简报
# ══════════════════════════════════════════════════════════════════════

def test_active_block_carries_direction_semantics(pack_layers):
    act = (pack_layers["factors"]["symbols"]["BTC"] or {}).get("active") or {}
    top = act.get("top") or []
    assert top, act
    for row in top:
        assert row["sign"] in (1, -1)
        assert row["inv"] is (row["sign"] < 0), row
    assert "反着用" in (act.get("note") or "")


def test_active_block_sorted_by_ic_not_raw_value(pack_layers):
    top = pack_layers["factors"]["symbols"]["BTC"]["active"]["top"]
    ics = [abs(r["ic"]) for r in top]
    assert ics == sorted(ics, reverse=True), ics


def test_route_block_present_with_action_and_score(pack_layers):
    rt = (pack_layers["factors"]["symbols"]["BTC"] or {}).get("route") or {}
    assert rt.get("action") in ("buy", "sell", "hold"), rt
    assert isinstance(rt.get("score"), (int, float))
    assert isinstance(rt.get("n"), int) and rt["n"] > 0


def test_quant_brief_block_wired(pack_layers):
    """`MidLongQuantBriefBuilder` 此前全库无生产调用方 —— 这里首次接进 prompt。"""
    br = (pack_layers["factors"]["symbols"]["BTC"] or {}).get("brief") or {}
    assert "alignment_score" in br and isinstance(br["alignment_score"], int)
    assert "evidence_available_ratio" in br
    assert isinstance(br.get("missing_data"), list)


def test_layer_is_json_serializable(pack_layers):
    json.dumps(pack_layers["factors"], ensure_ascii=False, default=str)


# ══════════════════════════════════════════════════════════════════════
# ②b 轮108：另外两处"写好了没进 prompt"的学习产物
# ══════════════════════════════════════════════════════════════════════

def test_text_quant_brief_wired(pack_layers):
    """`decision_core.quant_brief.build_quant_brief` 此前只被已退场的 trend_agent 引用。"""
    txt = (pack_layers["factors"]["symbols"]["BTC"] or {}).get("brief_text") or ""
    assert "量化简报" in txt, txt[:200]
    assert "数据完整度" in txt and "决策指引" in txt


def test_factor_system_snapshot_wired(pack_layers):
    """`learning_readback.factor_system_snapshot` 的 docstring 写着"供决策 prompt 参考"，
    此前只有它自己的 CLI 在读。"""
    sysblk = pack_layers["factors"].get("system") or {}
    assert sysblk.get("role") == "证据，非指令"
    rw = sysblk.get("runtime_weights") or {}
    assert isinstance(rw.get("n_file"), int) and rw["n_file"] > 0
    decay = sysblk.get("decay") or {}
    assert isinstance(decay.get("n"), int) or decay == {}


def test_both_new_blocks_render_into_prompt():
    pack = CP.build("midlong_thesis", symbols=_SYMS)
    txt = pack.to_prompt_text(40000)
    assert "brief_text" in txt and '"system"' in txt


# ══════════════════════════════════════════════════════════════════════
# ③ 进入 prompt 文本 + 预算裁剪
# ══════════════════════════════════════════════════════════════════════

def test_factor_layer_renders_into_prompt_text():
    pack = CP.build("midlong_thesis", symbols=_SYMS)
    txt = pack.to_prompt_text(40000)
    assert '"factors"' in txt and '"inv"' in txt


def test_factor_layer_is_trimmed_under_budget():
    """预算不够时：先砍 route 明细，再只留第一个币 —— 不得直接抛异常。"""
    pack = CP.build("midlong_thesis", symbols=["BTC", "ETH"])
    txt = pack.to_prompt_text(10)          # 极小预算 → 走完全部裁剪
    assert isinstance(txt, str) and txt
    fs = pack.layers.get("factors") or {}
    # 裁剪只作用于渲染副本：原 layers 不变（幂等/可复现 hash 依赖原样）
    assert "factors" in pack.layers


# ══════════════════════════════════════════════════════════════════════
# ④ 源码级棘轮：主脑链路确实会拿到这一层
# ══════════════════════════════════════════════════════════════════════

def _src(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return open(os.path.join(root, rel), encoding="utf-8").read()


def test_build_defaults_factor_layer_for_thesis_task():
    src = _src("backend/services/analysis/context_pack.py")
    assert 'if str(task or "") == "midlong_thesis":' in src
    # [轮153d 2026-09-21] 原断言钉的是**字面量** `_default_layers + ("factors",)`；
    # 轮129 给同一个元组加了 "analysts" 层 ⇒ 字面量变了、本断言自那时起**恒红**
    # （陈旧棘轮会让"真的丢了 factors 层"这种回归淹在噪声里）。
    # 现改钉语义：该分支必须把 factors 加进默认层，允许后续继续追加层。
    import re
    m = re.search(r"_default_layers\s*=\s*_default_layers\s*\+\s*\(([^)]*)\)", src)
    assert m, "找不到『midlong_thesis 默认层扩展』那一行（结构变了要同步本棘轮）"
    layers = [s.strip().strip("\"'") for s in m.group(1).split(",") if s.strip()]
    assert "factors" in layers, f"midlong_thesis 默认层缺 factors：{layers}"


def test_brain_uses_midlong_thesis_task():
    src = _src("backend/services/mlto/brain.py")
    assert 'build("midlong_thesis"' in src, "主脑若换了任务名，因子层默认开关要跟着改"
