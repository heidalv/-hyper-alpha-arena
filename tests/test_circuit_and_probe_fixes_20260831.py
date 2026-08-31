"""2026-08-31 根治回归测试：短线/中线无单死锁三处修复。

1. midlong_circuit_gate：冷却期内继续亏损不再顺延 12h；到期在记录侧自动清零。
2. decide_scalp 保底流量探针：探针线降到 0.40 + 补 factor_score 门槛(45)。
3. decide_scalp 影子探针：credit≤0 的来源给高分信号 0.125x 出口（共享日配额），
   打破"shadow 无样本→永不解除"死锁。
"""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_env_and_model(monkeypatch):
    """测试内完全控制 env 与模型状态：
    - 停用 decide_scalp 的 .env mtime 热重载（否则 .env 文件值覆盖 monkeypatch.setenv）；
    - 模型默认 usable=True（unusable 探索通道由 TestUnusableExploreLane 单独覆盖）。
    """
    from backend.services import decision_fusion_arbiter as dfa
    monkeypatch.setattr(dfa, "_maybe_reload_env", lambda: None)
    import backend.services.scalp_meta_trainer as smt
    monkeypatch.setattr(smt, "meta_model_usable", lambda: True)


# ════════════════════════════════════════════════════════════════════
# 1. midlong_circuit_gate 冷却不顺延 + 到期重置
# ════════════════════════════════════════════════════════════════════
class TestCircuitCooldownNoExtension:
    @pytest.fixture(autouse=True)
    def _fresh_state(self, monkeypatch, tmp_path):
        from backend.services.full_auto import midlong_circuit_gate as mcg
        monkeypatch.setattr(mcg, "_STATE_FILE", str(tmp_path / "circuit_state.json"))
        monkeypatch.setattr(mcg, "CONSEC_LOSSES_LIMIT", 2)
        monkeypatch.setattr(mcg, "COOLDOWN_S", 0.10)   # 100ms 冷却，便于测到期
        monkeypatch.setattr(mcg, "DAILY_LOSS_CAP", 0.0)  # 关日亏帽，单独测连亏路径
        mcg._state.clear()
        mcg._loaded = False
        return mcg

    def _fresh(self):
        from backend.services.full_auto import midlong_circuit_gate as mcg
        return mcg

    def test_losses_within_cooldown_do_not_extend(self, _fresh_state):
        mcg = _fresh_state
        # 触发熔断：2 连亏
        mcg.record_midlong_outcome(14, "BNB", -10.0)
        mcg.record_midlong_outcome(14, "BNB", -10.0)
        st = mcg._state["14:BNB"]
        first_ban = float(st["banned_until"])
        assert first_ban > time.time()
        # 冷却期内继续亏：不再顺延
        mcg.record_midlong_outcome(14, "BNB", -10.0)
        st = mcg._state["14:BNB"]
        assert float(st["banned_until"]) == pytest.approx(first_ban, abs=0.02)
        assert int(st["consec_losses"]) == 3  # 计数保留（记录事实）

    def test_win_during_cooldown_resets_counter_but_keeps_ban(self, _fresh_state):
        mcg = _fresh_state
        mcg.record_midlong_outcome(14, "BNB", -10.0)
        mcg.record_midlong_outcome(14, "BNB", -10.0)
        ban = float(mcg._state["14:BNB"]["banned_until"])
        mcg.record_midlong_outcome(14, "BNB", +5.0)  # 冷却期内盈利
        st = mcg._state["14:BNB"]
        assert int(st["consec_losses"]) == 0
        assert float(st["banned_until"]) == pytest.approx(ban, abs=0.02)

    def test_expiry_resets_in_record_side(self, _fresh_state):
        mcg = _fresh_state
        mcg.record_midlong_outcome(14, "BNB", -10.0)
        mcg.record_midlong_outcome(14, "BNB", -10.0)
        assert float(mcg._state["14:BNB"]["banned_until"]) > time.time()
        time.sleep(0.15)  # 超过 100ms 冷却
        # 到期后下一笔记录自动清零（不再依赖开仓检查）
        mcg.record_midlong_outcome(14, "BNB", -10.0)
        st = mcg._state["14:BNB"]
        assert float(st["banned_until"]) == 0.0
        # 到期清零后，这笔新亏损计为第 1 笔连亏
        assert int(st["consec_losses"]) == 1

    def test_check_allows_after_expiry(self, _fresh_state):
        mcg = _fresh_state
        mcg.record_midlong_outcome(14, "BNB", -10.0)
        mcg.record_midlong_outcome(14, "BNB", -10.0)
        ok, reason = mcg.check_midlong_entry(14, "BNB")
        assert not ok and "mid_circuit_banned" in reason
        time.sleep(0.15)
        ok2, _ = mcg.check_midlong_entry(14, "BNB")
        assert ok2 is True


# ════════════════════════════════════════════════════════════════════
# 2. 保底流量探针（pwin 地板之下）
# ════════════════════════════════════════════════════════════════════
class TestProbeLane:
    def _decide(self, *, pwin, score, credit=1.0, is_mr=False, tp=1.17, sl=0.90):
        from backend.services.decision_fusion_arbiter import decide_scalp
        return decide_scalp(
            pwin=pwin, factor_score=score, direction="long",
            credit=credit, tp_pct=tp, sl_pct=sl, mode="paper",
            account_id=14, is_mr=is_mr,
        )

    def test_probe_fires_for_mid_pwin_high_score(self):
        d = self._decide(pwin=0.40, score=46)
        assert d.action == "trade"
        assert d.reason == "pwin_probe_quota"
        assert d.size_mult == 0.125

    def test_probe_blocked_by_low_score(self):
        d = self._decide(pwin=0.40, score=40)  # <45
        assert d.action == "hold"
        assert d.reason == "pwin_below_min"

    def test_probe_blocked_below_probe_min_pwin(self):
        d = self._decide(pwin=0.35, score=50)  # <0.40
        assert d.action == "hold"

    def test_probe_blocked_when_quota_exhausted(self, monkeypatch):
        from backend.services import decision_fusion_arbiter as dfa
        monkeypatch.setattr(dfa, "_probe_quota_read", lambda: {"14": 3})
        d = self._decide(pwin=0.40, score=50)
        assert d.action == "hold"
        assert d.reason == "pwin_below_min"


# ════════════════════════════════════════════════════════════════════
# 3. 影子探针（credit≤0 的 shadow 死锁出口）
# ════════════════════════════════════════════════════════════════════
class TestShadowProbeLane:
    def _decide(self, *, pwin, score, credit=0.0, is_mr=False):
        from backend.services.decision_fusion_arbiter import decide_scalp
        return decide_scalp(
            pwin=pwin, factor_score=score, direction="long",
            credit=credit, tp_pct=1.17, sl_pct=0.90, mode="paper",
            account_id=14, is_mr=is_mr,
        )

    def test_shadow_probe_fires_high_score(self):
        d = self._decide(pwin=0.41, score=51)
        assert d.action == "trade"
        assert d.reason == "shadow_probe_quota"
        assert d.size_mult == 0.125

    def test_shadow_probe_blocked_by_low_score(self):
        d = self._decide(pwin=0.41, score=40)  # <50
        assert d.action == "standdown"
        assert d.reason == "source_credit_shadow"

    def test_shadow_standdown_when_pwin_unavailable(self):
        d = self._decide(pwin=None, score=60)
        assert d.action == "standdown"

    def test_shadow_standdown_for_mr(self):
        d = self._decide(pwin=0.45, score=55, is_mr=True)
        assert d.action == "standdown"

    def test_shadow_probe_blocked_below_pwin_line(self):
        d = self._decide(pwin=0.35, score=55)  # <0.40
        assert d.action == "standdown"

    def test_shadow_probe_shares_quota(self, monkeypatch):
        from backend.services import decision_fusion_arbiter as dfa
        monkeypatch.setattr(dfa, "_probe_quota_read", lambda: {"14": 3})
        d = self._decide(pwin=0.41, score=55)
        assert d.action == "standdown"  # 配额耗尽 → 保持停摆


# ════════════════════════════════════════════════════════════════════
# 4. 空头加严（2026-08-31：空头 14 天 -114 结构失血）
# ════════════════════════════════════════════════════════════════════
class TestShortTightening:
    def _decide(self, *, pwin, score, direction="short", credit=1.0, is_mr=False):
        from backend.services.decision_fusion_arbiter import decide_scalp
        return decide_scalp(
            pwin=pwin, factor_score=score, direction=direction,
            credit=credit, tp_pct=1.17, sl_pct=0.90, mode="paper",
            account_id=14, is_mr=is_mr,
        )

    def test_short_pwin_premium_blocks_borderline(self, monkeypatch):
        # rr 1.3 → 基础地板 0.457；空头 +0.05 → 0.507
        monkeypatch.setenv("FUSION_SHORT_PWIN_EXTRA", "0.05")
        monkeypatch.setenv("FUSION_PROBE_SHORT_ENABLED", "false")
        d = self._decide(pwin=0.50, score=70)  # 0.50 < 0.507 → hold
        assert d.action == "hold"

    def test_short_premium_disabled_restores_floor(self, monkeypatch):
        monkeypatch.setenv("FUSION_SHORT_PWIN_EXTRA", "0")
        d = self._decide(pwin=0.52, score=70)  # 0.52 ≥ 分档地板 0.50 → trade
        assert d.action == "trade"

    def test_short_excluded_from_probe_lane(self, monkeypatch):
        # pwin 低于地板、但空头默认不给探针
        monkeypatch.setenv("FUSION_PROBE_SHORT_ENABLED", "false")
        monkeypatch.setenv("FUSION_SHORT_PWIN_EXTRA", "0.05")
        d = self._decide(pwin=0.45, score=60)  # 0.45 ≥ 探针线 0.40 但空头被排除
        assert d.action == "hold"
        assert d.reason == "pwin_below_min"

    def test_short_probe_restorable(self, monkeypatch):
        monkeypatch.setenv("FUSION_PROBE_SHORT_ENABLED", "true")
        monkeypatch.setenv("FUSION_SHORT_PWIN_EXTRA", "0")
        d = self._decide(pwin=0.45, score=60)  # 空头探针恢复放行
        assert d.action == "trade"
        assert d.reason == "pwin_probe_quota"


# ════════════════════════════════════════════════════════════════════
# 5. unusable 探索通道（2026-08-31：hold 死锁 → explore_quota 恢复）
# ════════════════════════════════════════════════════════════════════
class TestUnusableExploreLane:
    def _decide(self, *, pwin, score, direction="long", credit=1.0):
        from backend.services.decision_fusion_arbiter import decide_scalp
        return decide_scalp(
            pwin=pwin, factor_score=score, direction=direction,
            credit=credit, tp_pct=1.4, sl_pct=1.0, mode="paper",
            account_id=14,
        )

    def test_unusable_explore_fires(self, monkeypatch):
        import backend.services.scalp_meta_trainer as smt
        monkeypatch.setattr(smt, "meta_model_usable", lambda: False)
        monkeypatch.setenv("FUSION_PWIN_UNUSABLE_MODE", "explore_quota")
        d = self._decide(pwin=0.33, score=46)  # score≥35, RR 1.4≥1.3
        assert d.action == "trade"
        assert d.reason == "pwin_model_unusable_explore"

    def test_unusable_hold_falls_to_pwin_floor(self, monkeypatch):
        import backend.services.scalp_meta_trainer as smt
        monkeypatch.setattr(smt, "meta_model_usable", lambda: False)
        monkeypatch.setenv("FUSION_PWIN_UNUSABLE_MODE", "hold")
        d = self._decide(pwin=0.33, score=46)
        assert d.action == "hold"
        assert d.reason == "pwin_below_min"

    def test_unusable_explore_blocked_by_low_score(self, monkeypatch):
        import backend.services.scalp_meta_trainer as smt
        monkeypatch.setattr(smt, "meta_model_usable", lambda: False)
        monkeypatch.setenv("FUSION_PWIN_UNUSABLE_MODE", "explore_quota")
        d = self._decide(pwin=0.33, score=30)  # score < 35
        assert d.action == "hold"
        assert d.reason == "pwin_model_unusable_explore_bar"
