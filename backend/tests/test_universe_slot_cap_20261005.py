# -*- coding: utf-8 -*-
"""[整顿轮·T13 2026-10-05] 交易名单必须遵守**槽位上限**（集中度 = 盈利的结构前提）。

病根（实测）：
  `runner._refresh_flow_universe` 的 situation 分支把名单写成
  `sit_doc["coins"][:40]` —— 硬编码 40，**绕开** `select_trading_slots` 的
  `SLOT_CAP=4`。实测文档新鲜（0.37h）且含 **39 个币**，
  于是 `limits.universe_slots=6` 形同虚设。

后果（算术）：
  单笔风险预算 = equity × 0.5% / 止损 = $302.73 × 0.005 / 40bp = **$378.41**
  摊到 39 个标的 ⇒ **每个只剩 $9.70 名义**
  ⇒ 单腿 edge（1~14bp）× $9.70 ≈ 不到 1 美分。

锁定：
  1. `select_trading_slots` 遵守 `slot_cap`（原有契约，防回归）
  2. runner 的情境分支**不得**再用硬编码 `[:40]`
  3. 必须提供 `MM_SIT_SLOTS` 回滚开关
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "backend/services/market_maker/runner.py"


def _src() -> str:
    return RUNNER.read_text(encoding="utf-8", errors="replace")


class TestSlotCapIsRespected:
    def test_select_trading_slots_respects_cap(self):
        from backend.services.market_maker.flow_universe import select_trading_slots
        pool = [f"S{i}" for i in range(20)]
        # 全部合格（正 edge、样本足）
        gates = {s: {"oos": {"mean_y": 5.0, "n_eff": 100.0}} for s in pool}
        kept, _ = select_trading_slots(pool, gates, [], {}, 1000.0, slot_cap=6)
        assert len(kept) <= 6, f"必须遵守 slot_cap=6，实际 {len(kept)}"
        assert kept, "应有入选标的"

    def test_cap_four_default(self):
        from backend.services.market_maker.flow_universe import select_trading_slots
        pool = [f"S{i}" for i in range(20)]
        gates = {s: {"oos": {"mean_y": 5.0, "n_eff": 100.0}} for s in pool}
        kept, _ = select_trading_slots(pool, gates, [], {}, 1000.0, slot_cap=4)
        assert len(kept) <= 4


class TestRunnerSituationPathIsCapped:
    def test_no_hardcoded_40_slice(self):
        """`[:40]` 这个硬编码必须已被移除。"""
        src = _src()
        bad = re.search(r'sit_doc\.get\(\s*["\']coins["\']\s*\)\s*or\s*\{\}\s*\)\s*\]\s*\[\s*:\s*40\s*\]', src)
        assert bad is None, (
            "situation 分支仍在用硬编码 [:40] 绕开槽位上限："
            f"{bad.group(0) if bad else ''}")

    def test_uses_universe_slots(self):
        src = _src()
        # 情境分支附近必须引用 universe_slots 或 MM_SIT_SLOTS
        i = src.find("sit_fresh")
        assert i != -1
        window = src[i:i + 4000]
        assert "universe_slots" in window or "MM_SIT_SLOTS" in window, (
            "情境分支必须受槽位上限约束")

    def test_rollback_switch_exists(self):
        src = _src()
        assert "MM_SIT_SLOTS" in src, (
            "必须提供回滚开关，否则无法 A/B 与快速回退")

    def test_ranks_by_oos_edge(self):
        src = _src()
        i = src.find("sit_fresh")
        # [2026-10-09] PP 选币路径插在情境分支之前（约 +100 行），扫描窗放宽
        # 到 16000 字符；契约不变：情境分支仍必须按样本外 edge 排序取前 N。
        window = src[i:i + 16000]
        assert "oos_conditional_mean" in window, (
            "必须在合格者中按样本外 edge 排序取前 N（而不是按文档顺序截断）")


class TestRefreshRunsWithoutNameError:
    """**运行时**验证（源码文本检查抓不到 NameError）。

    首版误用 `_os`（`plan_tick` 的局部别名），导致 `_refresh_flow_universe`
    抛 `NameError: name '_os' is not defined` 并静默失败 ——
    radar_state 里只能看到 {"error": "NameError: ..."}，名单照旧 39 个。
    这里直接**真跑一次**该方法的名单计算路径，确保不再有 NameError。
    """

    def test_refresh_does_not_raise_nameerror(self, monkeypatch):
        import json
        from pathlib import Path

        from backend.services.market_maker import runner as R

        root = Path(R.__file__).resolve().parents[3]
        sit_path = root / "data" / "flow_situation_last.json"
        if not sit_path.exists():
            import pytest
            pytest.skip("无 flow_situation_last.json，无法复现情境分支")

        # 取该 lane 的 runner 实例（只读；不 tick）
        r = R.get_runner("mm_asterdex")
        if r is None:
            import pytest
            pytest.skip("lane mm_asterdex 未注册")

        monkeypatch.setenv("MM_SIT_SLOTS", "6")
        # 直接调用刷新；若仍有 NameError，_radar_state 会带 error 且名单不变
        try:
            r._refresh_flow_universe(1e9)
        except NameError as e:      # noqa: PERF203
            raise AssertionError(f"_refresh_flow_universe 抛 NameError: {e}") from e

        st = dict(getattr(r, "_radar_state", {}) or {})
        err = str(st.get("error") or "")
        assert "_os" not in err, f"仍有 _os 未定义错误: {err}"
        if st.get("symbols"):
            assert len(st["symbols"]) <= 6, (
                f"情境路径必须遵守槽位上限 6，实际 {len(st['symbols'])}")
