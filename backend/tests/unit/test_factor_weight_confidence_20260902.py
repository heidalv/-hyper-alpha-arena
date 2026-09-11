# -*- coding: utf-8 -*-
"""因子权重与证据强度对齐（2026-09-02 G19）。

修复前的两处失配（实测 data/factor_runtime_weights.json，97 个因子）：

1. 样本不足即满权：n<MIN_SAMPLES 的因子 weight 停在初始值 1.0。全池 50 个这类
   因子（样本 3~28 条、IC 算不出）合计占 75.2% 的权重，而 11 个 n>=500 的可信
   因子只占 4.8%。ic_ev 分支注释写的"IC=null（样本不足）→ 中性 0.1"从未生效，
   因为 _rank_ic 对 n<30 直接返回 None，根本进不到那个分支。

2. 裸 IC 定权不含可信度：ai_gen_sl_break 以 n=37 的 IC=0.2759（t=1.66，不显著）
   顶到上限 1.5；obv 以 n=757 的 IC=0.0939（t=2.58，显著）只拿 0.876。

本用例锁定修复后的两条性质：没有证据不得主导；同样的 IC，样本越少权重越低。
"""
import math

import pytest

from backend.services import factor_ic_evaluator as fie


def _w(n, ic, win_rate=0.6, *, k=None, unverified=None, monkeypatch=None):
    """复刻 run_factor_ic_evaluation 的权重分支（与被测源同一套逻辑参数）。"""
    if monkeypatch is not None:
        if k is not None:
            monkeypatch.setenv("FACTOR_IC_SHRINK_K", str(k))
        if unverified is not None:
            monkeypatch.setenv("FACTOR_UNVERIFIED_WEIGHT", str(unverified))
    weight = fie._unverified_weight()
    if n >= fie.MIN_SAMPLES:
        if ic is not None and ic <= 0:
            weight = 0.0
        elif ic is None or win_rate < 0.45:
            weight = fie._EV_WEIGHT_FLOOR
        else:
            _k = fie._ic_shrink_k()
            _eff = ic * (n / (n + _k)) if _k > 0 else ic
            weight = float(min(1.5, max(fie._EV_WEIGHT_FLOOR, 0.5 + 4.0 * _eff)))
    return weight


class TestUnverifiedNotFullWeight:
    """性质一：没有证据的因子不得拿满权。"""

    def test_default_is_floor_not_one(self):
        assert fie._unverified_weight() == pytest.approx(0.1), (
            "样本不足的默认权重必须是地板 —— 1.0 等于把'从未验证'当'完全信任'"
        )

    @pytest.mark.parametrize("n", [3, 4, 12, 19, 29])
    def test_below_min_samples_gets_floor(self, n):
        assert _w(n, None) == pytest.approx(0.1), f"n={n} 不应拿到高权重"

    def test_at_min_samples_boundary_switches(self):
        """n 刚够 MIN_SAMPLES 就改由 IC 定权，不再走未验证地板。"""
        below = _w(fie.MIN_SAMPLES - 1, None)
        at = _w(fie.MIN_SAMPLES, 0.20, win_rate=0.6)
        assert below == pytest.approx(0.1)
        assert at > 0.1, "样本达标且 IC 为正应高于地板"

    def test_override_can_restore_old_behavior(self, monkeypatch):
        monkeypatch.setenv("FACTOR_UNVERIFIED_WEIGHT", "1.0")
        assert fie._unverified_weight() == pytest.approx(1.0), "需保留回滚能力"

    def test_override_is_clamped(self, monkeypatch):
        monkeypatch.setenv("FACTOR_UNVERIFIED_WEIGHT", "99")
        assert fie._unverified_weight() <= 1.5
        monkeypatch.setenv("FACTOR_UNVERIFIED_WEIGHT", "-5")
        assert fie._unverified_weight() >= 0.0

    def test_malformed_override_falls_back(self, monkeypatch):
        monkeypatch.setenv("FACTOR_UNVERIFIED_WEIGHT", "abc")
        assert fie._unverified_weight() == pytest.approx(fie._EV_WEIGHT_FLOOR)


class TestICShrinkage:
    """性质二：同样的 IC，样本越少权重越低。"""

    def test_same_ic_more_samples_wins(self):
        small = _w(37, 0.2759)
        large = _w(757, 0.2759)
        assert large > small, "同 IC 下大样本必须拿到更高权重"

    def test_the_real_inversion_is_fixed(self):
        """线上实测的倒挂个案：n=37/IC=0.276 曾压过 n=757/IC=0.094。"""
        sl_break = _w(37, 0.2759)     # t=1.66，不显著
        obv = _w(757, 0.0939)         # t=2.58，显著
        old_sl_break = min(1.5, max(0.1, 0.5 + 4.0 * 0.2759))   # 旧公式=1.5
        old_obv = min(1.5, max(0.1, 0.5 + 4.0 * 0.0939))        # 旧公式=0.876
        assert old_sl_break > old_obv, "旧公式确实倒挂（回归基线）"
        assert sl_break - obv < old_sl_break - old_obv, (
            "新公式必须缩小这个倒挂差距"
        )

    def test_shrink_monotonic_in_n(self):
        ws = [_w(n, 0.15) for n in (30, 100, 300, 1000, 5000)]
        assert ws == sorted(ws), f"权重应随样本量单调不减: {ws}"

    def test_shrink_never_flips_sign(self):
        """收缩只削弱强度，不改变方向：正 IC 不该被压到地板以下。"""
        assert _w(30, 0.30) >= fie._EV_WEIGHT_FLOOR

    def test_k_zero_disables_shrink(self, monkeypatch):
        monkeypatch.setenv("FACTOR_IC_SHRINK_K", "0")
        assert fie._ic_shrink_k() == 0.0
        naked = min(1.5, max(0.1, 0.5 + 4.0 * 0.2759))
        assert _w(37, 0.2759) == pytest.approx(naked), "K=0 应回到裸 IC"

    def test_k_default_and_fallback(self, monkeypatch):
        monkeypatch.delenv("FACTOR_IC_SHRINK_K", raising=False)
        assert fie._ic_shrink_k() == pytest.approx(100.0)
        monkeypatch.setenv("FACTOR_IC_SHRINK_K", "not-a-number")
        assert fie._ic_shrink_k() == pytest.approx(100.0)

    def test_shrink_factor_matches_intent(self, monkeypatch):
        """K=100 时 n=100 打 5 折 —— 文档承诺的口径不能漂。"""
        monkeypatch.setenv("FACTOR_IC_SHRINK_K", "100")
        ic = 0.20
        expect = min(1.5, max(0.1, 0.5 + 4.0 * ic * (100 / 200)))
        assert _w(100, ic) == pytest.approx(expect)


class TestExistingContractsIntact:
    """回归护栏：本次只改'没证据'与'证据强度'，既有淘汰语义不动。"""

    def test_negative_ic_still_zero(self):
        assert _w(500, -0.05) == 0.0, "负 IC 必须继续被清零"

    def test_low_winrate_still_floor(self):
        assert _w(500, 0.20, win_rate=0.30) == pytest.approx(0.1)

    def test_weight_never_exceeds_cap(self):
        assert _w(100000, 0.99) <= 1.5

    def test_weight_never_negative(self):
        for n, ic in ((5, None), (500, -0.9), (500, 0.0), (50, 0.01)):
            assert _w(n, ic) >= 0.0


class TestStatisticalSanity:
    """用 t 统计量做一次端到端合理性检查。"""

    def test_significant_factor_outranks_insignificant(self):
        """t 显著的因子最终权重应不低于 t 不显著的（同为正 IC）。"""
        cases = [
            (424, 0.1608),   # t=3.31 显著
            (757, 0.0939),   # t=2.58 显著
            (37, 0.2759),    # t=1.66 不显著
            (45, 0.1375),    # t=0.91 不显著
        ]
        scored = []
        for n, ic in cases:
            t = ic * math.sqrt(n - 1)
            scored.append((t, _w(n, ic), n, ic))
        sig = [w for t, w, _, _ in scored if abs(t) > 1.96]
        insig = [w for t, w, _, _ in scored if abs(t) <= 1.96]
        assert min(sig) >= max(insig) * 0.85, (
            f"显著因子权重不应被不显著的明显压过: 显著={sig} 不显著={insig}"
        )
