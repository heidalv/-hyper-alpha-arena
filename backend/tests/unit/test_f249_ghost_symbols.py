# -*- coding: utf-8 -*-
"""[F249 2026-09-20] "幽灵标的"根因回归测试。

## 现象

用户报告：`TAO`/`ZEC` **总是**出现在车道宇宙相关的地方，即使已把它们从
`lane_registry.meta.symbols` 移除、也停掉了采集。清一次、回来一次。

## 根因（四处叠加，缺一不可）

1. **`DEFAULT_SYMBOLS` 兜底**：原值 `["ZEC","ASTER","SOL","DOGE","TAO"]` 是
   `meta.symbols` 为空时的回退 ⇒ 任何"注册表未就绪"的时刻（首次建表、竞态、
   手工清空）都会让车道去挂这两个**已停采**的僵尸币。
2. **`load_states()` 无条件恢复**：它把 `lane_runtime_state` 里的**每一行**装回
   `self.states`，不看该 symbol 是否还在宇宙 ⇒ 移出宇宙的币在重启后被"复活"。
3. **`save_states()` 只增不删**（UPSERT，无 DELETE）⇒ 那两行永久驻留。
   ∴ 1+2+3 构成**自我维持循环**：恢复 → 报价 → 写回 → 下次重启再恢复。
4. **基准脚本用"合并"而非"重建"**：`rb = dict(旧块)` 保留已移出宇宙的键，
   于是 `replay_baseline.vol_baseline_bp` 里长期留着 TAO/ZEC。

## 本文件锁什么

这些是**行为契约**，不是经济性判断：宇宙是会被改的，所以
「任何宇宙相关的持久化都必须能被"收缩"」必须锁死。

测试写法：按**可执行语句**定位，不用"从子串截 N 字符"的窗口断言
（F326 的教训：窗口起点落在注释里 ⇒ 假失败）。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as R  # noqa: E402


# ── 根因 1：兜底宇宙必须为空（fail-closed） ──────────────────────

def test_default_symbols_is_empty():
    """`DEFAULT_SYMBOLS` 必须为空 —— 有兜底名单就会让幽灵币回来。"""
    assert list(R.DEFAULT_SYMBOLS) == [], (
        "DEFAULT_SYMBOLS 必须为 []：任何非空兜底都会在 meta.symbols 为空时"
        "让车道去挂一组过时的币（曾含已停采的 ZEC/TAO）"
    )


def test_default_symbols_has_no_ghost_symbols():
    """即使将来有人加回兜底，也不许出现已停采的 ZEC/TAO。"""
    ghosts = {"ZEC", "TAO"} & {str(s).upper() for s in R.DEFAULT_SYMBOLS}
    assert not ghosts, f"兜底宇宙不得含已停采标的: {sorted(ghosts)}"


def test_get_runner_fails_closed_on_empty_universe():
    """空宇宙 ⇒ 返回 None（不建 runner），而不是回退到默认宇宙。"""
    src = inspect.getsource(R.get_runner)
    lines = [ln.strip() for ln in src.splitlines()]
    # 必须存在「symbols 为空 ⇒ return None」的可执行分支
    has_empty_guard = any(
        "if not _symbols" in ln and ln.endswith(":")
        for ln in lines
    )
    assert has_empty_guard, "get_runner 缺少 'if not _symbols:' 的 fail-closed 分支"
    # 且 return None 必须紧跟其后（3 行内）
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("if not _symbols"))
    assert any("return None" in ln for ln in lines[idx: idx + 5]), \
        "空宇宙分支必须 return None"
    # 不许再把 DEFAULT_SYMBOLS 当兜底拼进 _symbols
    assert not any("or list(DEFAULT_SYMBOLS)" in ln for ln in lines), \
        "不得回退到 DEFAULT_SYMBOLS（幽灵来源）"


# ── 根因 2：load_states 必须按宇宙过滤 ──────────────────────────

def test_load_states_filters_non_universe_symbols():
    src = inspect.getsource(R.ShadowRunner.load_states)
    assert "_universe" in src, "load_states 必须取宇宙集合用于过滤"
    assert "skipped" in src, "load_states 必须记录被跳过的幽灵标的（可观测）"
    lines = [ln.strip() for ln in src.splitlines()]
    guard = [ln for ln in lines if "_universe" in ln and "not in" in ln]
    assert guard, "缺少「不在宇宙 ⇒ 跳过」的判定"


def test_load_states_keeps_orphan_positions():
    """**关键安全性质**：不在宇宙但**仍有仓位**的币必须保留，否则无法平仓。"""
    src = inspect.getsource(R.ShadowRunner.load_states)
    assert "st.qty" in src or "qty" in src, "过滤条件必须考虑仓位"
    # 过滤条件里必须有"无仓位"这一半
    assert "1e-12" in src or "abs(" in src, \
        "过滤必须只在『无仓位』时生效（有仓位必须保留以便平仓）"


# ── 根因 3：save_states 不得复活已被剔除的标的 ──────────────────

def test_save_states_only_persists_known_states():
    """save_states 写的是 `self.states`；配合 load_states 的过滤即可收敛。

    这里锁定：它**不**从 DB 读回、也**不**重建 symbols —— 即不会主动引入新标的。
    """
    src = inspect.getsource(R.ShadowRunner.save_states)
    assert "self.states.values()" in src
    assert "SELECT" not in src.upper().replace("SELECTED", ""), \
        "save_states 不应从库里读回标的（那会重新引入幽灵）"


# ── 根因 4：基准块必须"重建"而非"合并" ─────────────────────────

def _code_lines(src: str) -> list:
    """只保留**可执行代码行**，剔除注释。

    ⚠️ 必须这么做：第一版测试直接在全文里断言 `dict(meta.get("replay_baseline"))`
    不存在，结果**匹配到了解释该 bug 的注释文本** ⇒ 假失败。
    这正是 F326 文件里记过的同一类错误（窗口断言落在注释里）。
    """
    out = []
    for raw in src.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        # 去掉行尾注释（不处理字符串里的 # —— 本用途足够）
        if "  #" in s:
            s = s.split("  #", 1)[0].rstrip()
        out.append(s)
    return out


@pytest.mark.parametrize("script", [
    "scripts/mm_anchor_vol_baseline.py",
    "scripts/mm_anchor_vol_baseline_ticks.py",
])
def test_vol_baseline_scripts_rebuild_not_merge(script: str):
    p = ROOT / script
    if not p.is_file():
        pytest.skip(f"{script} 不存在")
    code = "\n".join(_code_lines(p.read_text(encoding="utf-8")))
    # 不许把整个旧块复制过来（那会保留已移出宇宙的键）
    assert 'dict(meta.get("replay_baseline") or {})' not in code, (
        f"{script} 不得 `dict(旧块)` —— 必须整体重建 replay_baseline，"
        "否则已移出宇宙的币（TAO/ZEC）会永久留在基准表里"
    )
    assert 'meta["replay_baseline"] = {' in code, \
        f"{script} 应显式重建 replay_baseline 块"


def test_tick_anchor_final_starts_empty():
    """tick 锚定脚本的 `final` 必须从空字典开始（不得 dict(old)）。"""
    p = ROOT / "scripts/mm_anchor_vol_baseline_ticks.py"
    if not p.is_file():
        pytest.skip("脚本不存在")
    code = "\n".join(_code_lines(p.read_text(encoding="utf-8")))
    assert "final = dict(old)" not in code, \
        "`final` 若从 old 复制会把已移出宇宙的币带回来（幽灵标的）"
