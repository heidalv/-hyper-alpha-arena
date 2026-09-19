# -*- coding: utf-8 -*-
"""轮122：AI 选币名单打通 + ZEC 根除（2026-09-19）。

## 用户两条

> 「你刚才跟我说的 AI 选币候选和真正的 AI 选币里的有出入，根本对不上，看来根本就没有打通同送名单。」
> 「再有 zec 问题必须根除」

## 现场（`reports/_probe122.txt`）

| 事实 | 值 |
|---|---|
| 看板真源（23:52 最新一轮） | **ICP approve 0.60 / SYN approve 0.62**；**ZEC = watch 0.45**；WLFI = watch 0.40 |
| `get_ai_mid_candidates_for_session` | `['SYN','ZEC','WLFI']` ← approve 的 **ICP 不在**、watch 的 ZEC/WLFI 在 |
| 落盘 reason | `retention kept=3 evicted=[] added=[]` ← **只增不减** |
| ZEC 的策略 | `tpl_mid_reversion_a9e8e8` **status=paused**（tier=mid） |

机制：
1. `MIDLONG_AI_CANDIDATE_VERDICTS` 默认 `"approve,watch"` ⇒ 5–6 个都"合格"；
2. `max_slots=3` 且旧逻辑「在任者先占满」⇒ **置信度更高的 approve(ICP 0.60) 永远
   挤不掉 watch(ZEC 0.45/WLFI 0.40)** ⇒ 名单与看板长期背离（用户看到的"对不上"）；
3. ZEC 因为一直在池里 ⇒ 一直被扫描 ⇒ 它的 mid 策略是 **paused** ⇒ 入口解析拿到对象
   就放行、执行层要 active ⇒ `strategy_detached` ⇒ 5 次同因装配 30 分钟冷却 ⇒ 无限循环。

## 修法

* 名额分配改**按置信度取前 N**（`_rank_mid_pool` 纯函数，`manual_adopt` 24h 豁免保留）；
* 扫描入口要求策略 **status=active**（与执行层同口径），非 active 记
  `strategy_inactive` 并跳过。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()


# ══════════════════════════════════════════════════════════════════════
# ① 名单打通：按置信度分配名额
# ══════════════════════════════════════════════════════════════════════

def test_pool_ranking_admits_board_approve_over_incumbent_watch():
    from backend.services.auto_coin_selector import _rank_mid_pool

    cands = [("SYN", 0.62), ("ICP", 0.60), ("ZEC", 0.45), ("WLFI", 0.40), ("APT", 0.45)]
    kept, evicted, full, added = _rank_mid_pool(
        cands=cands, sticky_syms=["SYN", "ZEC", "WLFI"], manual_alive=False, max_slots=3)
    assert "ICP" in full, "看板 approve(0.60) 必须能进池"
    assert "ZEC" in evicted and "WLFI" in evicted, "watch 且置信度更低的在任者必须被淘汰"
    assert full == ["SYN", "ICP", "APT"], full
    assert len(full) <= 3


def test_pool_ranking_keeps_manual_adopt():
    from backend.services.auto_coin_selector import _rank_mid_pool

    kept, evicted, full, _ = _rank_mid_pool(
        cands=[], sticky_syms=["SYN", "ZEC"], manual_alive=True, max_slots=3)
    assert kept == ["SYN", "ZEC"] and evicted == [], "人工采纳 24h 内不得被清"


def test_selector_uses_the_ranker():
    src = _src("backend/services/auto_coin_selector.py")
    i = src.index("轮122 2026-09-19 修「AI 选币名单和真选币对不上」")
    seg = src[i:i + 1200]
    assert "_rank_mid_pool(" in seg
    assert "kept[: _max_slots]" not in seg and "kept[:_max_slots]" not in seg, "旧的在任者先占满必须删掉"


# ══════════════════════════════════════════════════════════════════════
# ② ZEC 根除：入口要求 active 策略
# ══════════════════════════════════════════════════════════════════════

def test_sweep_requires_active_strategy():
    src = _src("backend/services/mlto/brain.py")
    i = src.index("轮122 2026-09-19 ZEC 根除")
    seg = src[i:i + 1000]
    assert "_st_status" in seg and '"active"' in seg
    assert "strategy_inactive" in seg, "非 active 必须单独计数（可查）"


def test_paused_strategy_is_skipped(monkeypatch):
    """行为：paused 策略的候选不进执行层（ZEC 就是 paused）。"""
    from backend.services.mlto import brain as B

    calls = []

    class _Dto:
        accepted = 1
        recommend_open = 1
        direction = "long"
        tranche_stage = 0
        llm_conviction = 45

    class _Store:
        @staticmethod
        def get(sid, sym, tier):
            return _Dto() if sym == "ZEC" else None

    class _Strat:
        def __init__(self, st):
            self.status = st

    monkeypatch.setattr(B, "thesis_is_fresh", lambda dto: True)
    monkeypatch.setattr(B, "maybe_open", lambda **k: calls.append(k["symbol"]) or True)
    monkeypatch.setitem(sys.modules, "backend.services.mlto.thesis_store", _Store)
    monkeypatch.setattr(
        "backend.services.full_auto.midlong_position_manager.has_open_position_of_nature",
        lambda db, acct, sym, tier: False)

    class _Host:
        @staticmethod
        def resolve_independent_strategy(db, session, sym, tier):
            return _Strat("paused")

    class _Session:
        session_id = "fa_test"
        paper_account_id = 14

    B.run_midlong_open_sweep(host=_Host(), session=_Session(), symbols=["ZEC"], tier="mid")
    assert calls == [], "paused 策略必须被入口拦掉（否则执行层会 strategy_detached 循环）"
