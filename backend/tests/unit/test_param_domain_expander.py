"""S2-10b 单元测试：参数域扩展（Hermes 高置信模式 → GA 搜索域）。

覆盖：
- 无高置信模式 → 原域返回（安全回退）；
- increase 模式 → 上界 ×1.2；decrease 模式 → 下界 ×0.8；
- 同 key 多条模式 → 系数累乘（1.2^n），封顶 1.5 倍；
- 整数域扩展保留整数粒度；
- disabled 配置 → 原域；
- 缓存命中不重复查库；reset 后重新加载；
- evolution_scheduler._get_full_param_ranges 接入点；
- Hermes _infer_old_value_from_reason 四类文本模式 + 负例。
"""
from unittest.mock import MagicMock

import pytest

import backend.services.param_domain_expander as pde
from backend.services.hermes_proposal_wisdom_engine import ProposalWisdomEngine


@pytest.fixture(autouse=True)
def _reset_domain_cache():
    pde.reset_domain_cache()
    yield
    pde.reset_domain_cache()


@pytest.fixture
def base_ranges():
    return {
        "stop_loss_pct": (0.01, 0.08),
        "default_leverage": (5, 20),
        "weight_funding": (0.05, 0.40),
        "mid_rsi_bull": (50, 70),  # 整数域
    }


def _pattern(param_key, direction, n_patterns=1, **kw):
    return {
        "param_key": param_key,
        "direction": direction,
        "sample_count": 5,
        "avg_pnl_impact": 100.0,
        "confidence_avg": 0.6,
        **kw,
    }


class TestNoPatterns:
    def test_empty_patterns_returns_base(self, base_ranges, monkeypatch):
        monkeypatch.setattr(pde, "load_improved_patterns", lambda *a, **k: [])
        expanded, changes = pde.apply_domain_expansion(base_ranges)
        assert expanded == base_ranges
        assert changes == []


class TestIncreaseExpansion:
    def test_increase_expands_hi(self, base_ranges, monkeypatch):
        monkeypatch.setattr(
            pde, "load_improved_patterns",
            lambda *a, **k: [_pattern("weight_funding", "increase")],
        )
        expanded, changes = pde.apply_domain_expansion(base_ranges)
        assert expanded["weight_funding"] == (0.05, round(0.40 * 1.2, 6))
        assert expanded["stop_loss_pct"] == (0.01, 0.08)  # 未扩展
        assert changes[0]["param_key"] == "weight_funding"
        assert changes[0]["direction"] == "increase"


class TestDecreaseExpansion:
    def test_decrease_expands_lo(self, base_ranges, monkeypatch):
        # 用非整数域测 decrease：整数域（如 default_leverage）会被取整，
        # 该行为由 TestIntegerDomain 单独覆盖
        monkeypatch.setattr(
            pde, "load_improved_patterns",
            lambda *a, **k: [_pattern("weight_funding", "decrease")],
        )
        expanded, changes = pde.apply_domain_expansion(base_ranges)
        assert expanded["weight_funding"][0] == round(0.05 / 1.2, 6)
        assert expanded["weight_funding"][1] == 0.40  # 上界不变


class TestMultiPatternCap:
    def test_two_patterns_multiply(self, base_ranges, monkeypatch):
        # 2 条同 key increase → ×1.44（< 1.5 封顶）
        monkeypatch.setattr(
            pde, "load_improved_patterns",
            lambda *a, **k: [
                _pattern("weight_funding", "increase"),
                _pattern("weight_funding", "increase"),
            ],
        )
        expanded, changes = pde.apply_domain_expansion(base_ranges)
        assert expanded["weight_funding"][1] == round(0.40 * 1.2 ** 2, 6)

    def test_cap_at_max_ratio(self, base_ranges, monkeypatch):
        # 4 条同 key → ×2.07 理论，封顶 ×1.5
        monkeypatch.setattr(
            pde, "load_improved_patterns",
            lambda *a, **k: [_pattern("weight_funding", "increase")] * 4,
        )
        expanded, changes = pde.apply_domain_expansion(base_ranges)
        assert expanded["weight_funding"][1] == round(0.40 * 1.5, 6)

    def test_both_directions_independent(self, base_ranges, monkeypatch):
        # increase + decrease 同时存在：双向各按各自系数（非整数域）
        monkeypatch.setattr(
            pde, "load_improved_patterns",
            lambda *a, **k: [
                _pattern("stop_loss_pct", "increase"),
                _pattern("stop_loss_pct", "decrease"),
            ],
        )
        expanded, changes = pde.apply_domain_expansion(base_ranges)
        assert expanded["stop_loss_pct"][0] == round(0.01 / 1.2, 6)
        assert expanded["stop_loss_pct"][1] == round(0.08 * 1.2, 6)


class TestIntegerDomain:
    def test_integer_kept_integer(self, base_ranges, monkeypatch):
        monkeypatch.setattr(
            pde, "load_improved_patterns",
            lambda *a, **k: [_pattern("mid_rsi_bull", "increase")],
        )
        expanded, changes = pde.apply_domain_expansion(base_ranges)
        lo, hi = expanded["mid_rsi_bull"]
        assert float(lo).is_integer() and float(hi).is_integer()
        assert hi == 84  # 70 × 1.2 = 84


class TestDisabled:
    def test_disabled_returns_base(self, base_ranges, monkeypatch):
        # 先取真实 cfg 再 patch（避免 lambda 内递归调用已被替换的 _settings_cfg）
        cfg = pde._settings_cfg()
        monkeypatch.setattr(pde, "_settings_cfg", lambda: {**cfg, "enabled": False})
        expanded, changes = pde.apply_domain_expansion(base_ranges)
        assert expanded == base_ranges
        assert changes == []


class TestCache:
    def test_cache_hit_no_reload(self, base_ranges, monkeypatch):
        calls = {"n": 0}

        def fake_load(*a, **k):
            calls["n"] += 1
            return [_pattern("weight_funding", "increase")]

        monkeypatch.setattr(pde, "load_improved_patterns", fake_load)

        pde.apply_domain_expansion(base_ranges)
        pde.apply_domain_expansion(base_ranges)  # 第二次走缓存
        assert calls["n"] == 1

    def test_reset_forces_reload(self, base_ranges, monkeypatch):
        calls = {"n": 0}

        def fake_load(*a, **k):
            calls["n"] += 1
            return [_pattern("weight_funding", "increase")]

        monkeypatch.setattr(pde, "load_improved_patterns", fake_load)

        pde.apply_domain_expansion(base_ranges)
        pde.reset_domain_cache()
        pde.apply_domain_expansion(base_ranges)
        assert calls["n"] == 2

    def test_cached_replay_on_different_base(self, base_ranges, monkeypatch):
        # 缓存命中时对另一基础域正确重放
        monkeypatch.setattr(
            pde, "load_improved_patterns",
            lambda *a, **k: [_pattern("weight_funding", "increase")],
        )
        pde.apply_domain_expansion(base_ranges)
        other = {"weight_funding": (0.1, 0.5)}
        expanded, _ = pde.apply_domain_expansion(other)
        assert expanded["weight_funding"][1] == round(0.5 * 1.2, 6)


class TestSchedulerIntegration:
    def test_get_full_param_ranges_applies_expansion(self, monkeypatch):
        from backend.services import evolution_scheduler

        fake_base = {"stop_loss_pct": (0.01, 0.08), "weight_funding": (0.05, 0.40)}

        def fake_apply(base, *a, **k):
            out = dict(base)
            out["weight_funding"] = (out["weight_funding"][0],
                                     round(out["weight_funding"][1] * 1.2, 6))
            return out, [{"param_key": "weight_funding", "new": out["weight_funding"]}]

        monkeypatch.setattr(
            evolution_scheduler, "apply_domain_expansion", fake_apply, raising=False,
        )
        # apply_domain_expansion 在 _get_full_param_ranges 内部延迟 import，
        # 因此 patch 包名路径：backend.services.param_domain_expander.apply_domain_expansion
        monkeypatch.setattr(
            pde, "apply_domain_expansion", fake_apply,
        )
        monkeypatch.setattr(
            evolution_scheduler,
            "PIPELINE_PARAM_RANGES",
            fake_base,
            raising=False,
        )

        ranges = evolution_scheduler._get_full_param_ranges()
        assert ranges["weight_funding"][1] == round(0.40 * 1.2, 6)
        assert ranges["stop_loss_pct"] == (0.01, 0.08)


class TestInferOldValueFromReason:
    """Hermes 引擎 reason 旧值推断（参数域扩展的数据上游）"""

    def setup_method(self):
        self.engine = ProposalWisdomEngine()

    def test_key_equals_format(self):
        assert self.engine._infer_old_value_from_reason(
            "max_daily_trades", 14, "当前 max_daily_trades=12，+20% 上限 14.4"
        ) == 12.0

    def test_cong_x_dao_y_format(self):
        assert self.engine._infer_old_value_from_reason(
            "master_reduce_min_loss_pct", 0.18, "从 0.20 降到 0.18，降低止损门槛"
        ) == 0.20

    def test_cong_x_sheng_y_format(self):
        assert self.engine._infer_old_value_from_reason(
            "weight_funding", 0.3, "从 0.2 升到 0.3，加仓资金费率权重"
        ) == 0.2

    def test_dangqian_x_format(self):
        assert self.engine._infer_old_value_from_reason(
            "max_daily_trades", 14, "当前 12，+20% 上限 14.4 取 14"
        ) == 12.0

    def test_arrow_format(self):
        assert self.engine._infer_old_value_from_reason(
            "max_daily_trades", 10, "每日交易上限 12→10，-16.7%"
        ) == 12.0

    def test_arrow_reverse(self):
        assert self.engine._infer_old_value_from_reason(
            "max_daily_trades", 16, "从 14→16 增加，+14.3%"
        ) == 14.0

    def test_no_info_returns_none(self):
        assert self.engine._infer_old_value_from_reason(
            "some_key", 5, "无旧值描述，仅说明调整理由"
        ) is None

    def test_empty_reason(self):
        assert self.engine._infer_old_value_from_reason("k", 5, "") is None

    def test_extract_param_ops_with_reason(self):
        patches = [
            {"type": "tuning", "key": "max_daily_trades", "value": 14,
             "reason": "当前 max_daily_trades=12，+20% 上限"},
        ]
        ops = self.engine._extract_param_ops(patches)
        assert ops[0]["direction"] == "increase"
        # delta_pct 以 4 位小数展示口径输出
        assert abs(ops[0]["delta_pct"] - round((14 - 12) / 12, 4)) < 1e-9

    def test_extract_param_ops_no_old_value_unknown(self):
        patches = [
            {"type": "tuning", "key": "some_key", "value": 5, "reason": "优化调整"},
        ]
        ops = self.engine._extract_param_ops(patches)
        assert ops[0]["direction"] == ""
        assert ops[0]["delta_pct"] == 0.0
