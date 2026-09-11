# -*- coding: utf-8 -*-
"""短线参数离线回放器（2026-09-02 P1.4）。

定位：把"改一个参数值多少 bp"变成可计算的问题，让后续每一步改动先回放再上线。
教训来源：2026-09-01 一次性放宽扫描间隔/冷却/门槛/每 tick 开仓数，日成交量从 3
笔跳到 105 笔、胜率 42%→25%，事后才发现是配置漂移。

本用例锁定回放器最容易出错、也最容易骗人的地方：
1. 筛选语义（门槛是"收紧子集"，不能凭空造信号）；
2. TP/SL 覆盖优先级（绝对值 > 倍数 > 信号原值）；
3. 出场判定必须复用线上 TB 引擎（口径不得分裂）；
4. verdict 不得被几十条样本的小分段翻转。
"""
import pytest

from backend.services.replay.scalp_param_replayer import (
    ReplayParams,
    ReplayResult,
    ScalpParamReplayer,
    _Sample,
    compare,
    robustness_check,
)

T0 = 1_000_000


def _bar(i, hi, lo, cl):
    return (T0 + i * 300, hi, lo, cl)


def _mk(**kw):
    d = dict(symbol="BTC", signal_ts=T0, direction="long", entry=100.0,
             tp_pct=0.01, sl_pct=0.01, pwin=0.6, score=50.0)
    d.update(kw)
    return _Sample(**d)


def _replayer(samples, ohlc=None, cost=0.0008):
    rp = ScalpParamReplayer(days=30, cost=cost)
    rp._samples = list(samples)
    rp._ohlc = ohlc if ohlc is not None else {
        "BTC": [_bar(i, 100.2, 99.8, 100.0) for i in range(40)]
    }
    rp._loaded = True
    return rp


class TestFiltering:
    """筛选是"取子集"，绝不能放大样本 —— 放宽门槛会产生历史上不存在的信号。"""

    def test_direction_filter(self):
        rp = _replayer([_mk(direction="long"), _mk(direction="short")])
        assert rp.replay(ReplayParams(direction="long")).n + \
               rp.replay(ReplayParams(direction="short")).n == \
               rp.replay(ReplayParams()).n

    def test_pwin_filter_is_subset(self):
        rp = _replayer([_mk(pwin=p) for p in (0.4, 0.5, 0.6, 0.7)])
        all_n = rp.replay(ReplayParams()).n
        sub_n = rp.replay(ReplayParams(min_pwin=0.55)).n
        assert sub_n <= all_n
        assert sub_n == 2

    def test_missing_pwin_excluded_by_threshold(self):
        """pwin 缺失的样本不能被当成"通过门槛"。"""
        rp = _replayer([_mk(pwin=None), _mk(pwin=0.9)])
        assert rp.replay(ReplayParams(min_pwin=0.55)).n == 1

    def test_score_band(self):
        rp = _replayer([_mk(score=s) for s in (20, 40, 60, 80)])
        assert rp.replay(ReplayParams(min_score=35, max_score=70)).n == 2

    def test_symbol_filters(self):
        rp = _replayer(
            [_mk(symbol="BTC"), _mk(symbol="ETH"), _mk(symbol="SOL")],
            ohlc={s: [_bar(i, 100.2, 99.8, 100.0) for i in range(40)]
                  for s in ("BTC", "ETH", "SOL")},
        )
        assert rp.replay(ReplayParams(symbols=("BTC", "ETH"))).n == 2
        assert rp.replay(ReplayParams(exclude_symbols=("SOL",))).n == 2

    def test_time_window(self):
        rp = _replayer([_mk(signal_ts=T0), _mk(signal_ts=T0 + 5000)])
        assert rp.replay(ReplayParams(until_ts=T0 + 1)).n == 1
        assert rp.replay(ReplayParams(since_ts=T0 + 1)).n == 1

    def test_missing_kline_counted_as_skipped(self):
        rp = _replayer([_mk(symbol="NOKLINE")], ohlc={})
        r = rp.replay(ReplayParams())
        assert r.n == 0 and r.skipped == 1


class TestTpSlOverride:
    def test_absolute_beats_multiplier(self):
        assert ScalpParamReplayer._resolve_dist(0.01, 0.02, 5.0) == 0.02

    def test_multiplier_scales_original(self):
        assert ScalpParamReplayer._resolve_dist(0.01, None, 2.0) == pytest.approx(0.02)

    def test_falls_back_to_signal_value(self):
        assert ScalpParamReplayer._resolve_dist(0.013, None, None) == 0.013

    def test_zero_override_ignored(self):
        """0 视为"未指定"，否则会把轨道压成 0 造出假止盈。"""
        assert ScalpParamReplayer._resolve_dist(0.01, 0.0, None) == 0.01
        assert ScalpParamReplayer._resolve_dist(0.01, None, 0.0) == 0.01

    def test_tighter_tp_raises_hit_rate(self):
        """近 TP 更容易命中 —— 这是 2.2 寻优的基本机制，写反就全错。"""
        rows = [_bar(0, 100.4, 99.9, 100.3), _bar(1, 101.2, 100.2, 101.0)]
        rp = _replayer([_mk()], ohlc={"BTC": rows})
        near = rp.replay(ReplayParams(tp_pct=0.003, sl_pct=0.02))
        far = rp.replay(ReplayParams(tp_pct=0.05, sl_pct=0.02))
        assert near.tp_rate > far.tp_rate


class TestCostAccounting:
    def test_cost_is_deducted(self):
        """净收益必须扣往返成本，否则回放会系统性乐观。"""
        rows = [_bar(0, 101.5, 99.9, 101.2)]
        r_free = _replayer([_mk()], ohlc={"BTC": rows}, cost=0.0).replay(
            ReplayParams(tp_pct=0.01, sl_pct=0.05))
        r_cost = _replayer([_mk()], ohlc={"BTC": rows}, cost=0.0008).replay(
            ReplayParams(tp_pct=0.01, sl_pct=0.05))
        assert r_free.net_bp - r_cost.net_bp == pytest.approx(8.0, abs=0.01)

    def test_default_cost_reads_env(self, monkeypatch):
        monkeypatch.setenv("SCALP_META_COST", "0.0015")
        rp = ScalpParamReplayer()
        assert rp.cost == pytest.approx(0.0015)


class TestEngineReuse:
    def test_uses_production_tb_engine(self):
        """出场判定必须复用线上 TB 引擎，否则"回放赚钱、上线亏钱"。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[3] / "backend" / "services"
               / "replay" / "scalp_param_replayer.py").read_text(encoding="utf-8")
        assert "from backend.services.scalp_signal_logger import triple_barrier_outcome" in src
        assert "triple_barrier_outcome(" in src

    def test_sl_wins_ties_same_as_production(self):
        """同根两轨齐触判 SL —— 与线上标签口径一致。"""
        rp = _replayer([_mk()], ohlc={"BTC": [_bar(0, 102.0, 98.0, 100.0)]})
        r = rp.replay(ReplayParams(tp_pct=0.01, sl_pct=0.01))
        assert r.sl_rate == 100.0


class TestResultShape:
    def test_rates_sum_to_100(self):
        rows = [_bar(i, 100.2, 99.8, 100.0) for i in range(40)]
        rp = _replayer([_mk(), _mk(entry=100.0)], ohlc={"BTC": rows})
        r = rp.replay(ReplayParams(max_hold_sec=1800))
        assert r.tp_rate + r.sl_rate + r.timeout_rate == pytest.approx(100.0, abs=0.1)

    def test_total_bp_scales_with_n(self):
        rows = [_bar(0, 101.5, 99.9, 101.2)]
        rp = _replayer([_mk() for _ in range(4)], ohlc={"BTC": rows})
        r = rp.replay(ReplayParams(tp_pct=0.01, sl_pct=0.05))
        assert r.total_bp == pytest.approx(r.net_bp * r.n, abs=0.1)

    def test_empty_result_is_safe(self):
        rp = _replayer([])
        r = rp.replay(ReplayParams())
        assert r.n == 0 and r.net_bp == 0.0
        assert "n=" in r.summary()

    def test_compare_reports_delta(self):
        a = ReplayResult(name="base", n=10, net_bp=10.0)
        b = ReplayResult(name="alt", n=10, net_bp=25.0)
        out = compare([a, b])
        assert "+15.00bp" in out


class TestRobustnessVerdict:
    """verdict 不得被小样本分段翻转 —— 实测四段样本 93/2292/44/113。"""

    # K 线须覆盖「最后一条样本 + max_hold」，否则末尾分段全部取不到价而被
    # 计为 skipped —— 那测的就不是判定逻辑了。
    _GAP = 2500          # 段间隔（秒）
    _BARS = 160          # 160 × 300s = 48000s，足够覆盖 3×_GAP + max_hold

    @classmethod
    def _spread(cls, n_per_fold, *, direction="long"):
        """造出跨 4 个时段的样本（时间跨度控制在 K 线窗口内）。"""
        samples = []
        for f in range(4):
            for i in range(n_per_fold[f]):
                samples.append(_mk(
                    signal_ts=T0 + f * cls._GAP + i,
                    direction=direction,
                ))
        return samples

    @classmethod
    def _rows(cls, hi, lo, cl):
        return {"BTC": [_bar(i, hi, lo, cl) for i in range(cls._BARS)]}

    def test_thin_when_few_valid_folds(self, monkeypatch):
        monkeypatch.setenv("REPLAY_MIN_FOLD_N", "50")
        rp = _replayer(self._spread([12, 12, 12, 12]),
                       ohlc=self._rows(100.2, 99.8, 100.0))
        out = robustness_check(rp, ReplayParams(), folds=4)
        assert out["verdict"] == "thin"
        assert out["valid_folds"] < 2

    def test_early_return_has_same_shape(self, monkeypatch):
        """样本过少的 early return 也须返回同构字典。"""
        rp = _replayer(self._spread([2, 2, 2, 2]))
        out = robustness_check(rp, ReplayParams(), folds=4)
        for k in ("verdict", "folds", "valid_folds", "min_fold_n",
                  "net_bp_range", "net_bp_mean", "weighted_net_bp", "total_n"):
            assert k in out, f"early return 缺字段 {k}"

    def test_weighted_net_bp_respects_sample_size(self, monkeypatch):
        """加权净收益是"到底赚不赚"的答案，不能等权。"""
        monkeypatch.setenv("REPLAY_MIN_FOLD_N", "10")
        rp = _replayer(self._spread([200, 200, 200, 200]),
                       ohlc=self._rows(101.5, 99.9, 101.2))
        out = robustness_check(rp, ReplayParams(tp_pct=0.01, sl_pct=0.05),
                               folds=4)
        assert out["total_n"] > 0
        assert out["weighted_net_bp"] > 0
        assert out["valid_folds"] >= 2

    def test_verdict_negative_when_all_valid_folds_lose(self, monkeypatch):
        monkeypatch.setenv("REPLAY_MIN_FOLD_N", "10")
        rp = _replayer(self._spread([100, 100, 100, 100]),
                       ohlc=self._rows(100.1, 98.0, 98.2))
        out = robustness_check(rp, ReplayParams(tp_pct=0.05, sl_pct=0.01),
                               folds=4)
        assert out["verdict"] == "negative"

    def test_verdict_robust_when_all_valid_folds_win(self, monkeypatch):
        monkeypatch.setenv("REPLAY_MIN_FOLD_N", "10")
        rp = _replayer(self._spread([100, 100, 100, 100]),
                       ohlc=self._rows(101.5, 99.9, 101.2))
        out = robustness_check(rp, ReplayParams(tp_pct=0.01, sl_pct=0.05),
                               folds=4)
        assert out["verdict"] == "robust"

    def test_min_fold_n_configurable(self, monkeypatch):
        monkeypatch.setenv("REPLAY_MIN_FOLD_N", "1000")
        rp = _replayer(self._spread([100, 100, 100, 100]),
                       ohlc=self._rows(101.5, 99.9, 101.2))
        out = robustness_check(rp, ReplayParams(tp_pct=0.01, sl_pct=0.05),
                               folds=4)
        assert out["min_fold_n"] == 1000
        assert out["verdict"] == "thin", "门槛抬到样本量之上时应判 thin"


class TestGrid:
    def test_grid_covers_axis(self):
        rows = [_bar(i, 100.6, 99.5, 100.4) for i in range(40)]
        rp = _replayer([_mk()], ohlc={"BTC": rows})
        out = rp.grid(ReplayParams(name="g"), tp_pct=[0.003, 0.006, 0.01])
        assert len(out) == 3
        assert all("tp_pct=" in r.name for r in out)

    def test_grid_cartesian(self):
        rows = [_bar(i, 100.6, 99.5, 100.4) for i in range(40)]
        rp = _replayer([_mk()], ohlc={"BTC": rows})
        out = rp.grid(ReplayParams(name="g"),
                      tp_pct=[0.005, 0.01], sl_pct=[0.005, 0.01])
        assert len(out) == 4


class TestDefaultMaxHoldSingleSource:
    """[2026-09-02 审查修正] 回放器默认 max_hold 必须与标签结算/线上执行同源。

    审查发现回放器写死 7200，而线上 short 档 max_hold_sec 已调 5400、TB 标签结算
    也已动态跟随——三处口径分裂时，"baseline" 回放评估的根本不是线上策略。
    """

    def test_default_follows_labeler(self, monkeypatch):
        from backend.services.scalp_signal_logger import _tb_max_hold_sec
        assert ScalpParamReplayer._default_max_hold_sec() == _tb_max_hold_sec()

    def test_env_override_propagates(self, monkeypatch):
        monkeypatch.setenv("SCALP_META_TB_MAX_HOLD_SEC", "3600")
        assert ScalpParamReplayer._default_max_hold_sec() == 3600

    def test_default_hold_changes_timeout_outcome(self, monkeypatch):
        """默认 max_hold 真正参与出场判定：把默认改小到 1 根 K 线，
        一条本会 TP 的信号应变成 timeout（而不是仍按 7200 走到 TP）。"""
        monkeypatch.setenv("SCALP_META_TB_MAX_HOLD_SEC", "300")
        rows = [_bar(0, 100.2, 99.9, 100.1), _bar(1, 100.3, 99.9, 100.2),
                _bar(2, 101.5, 99.9, 101.2), _bar(3, 101.6, 100.0, 101.3)]
        rp = _replayer([_mk(tp_pct=0.01, sl_pct=0.05)], ohlc={"BTC": rows})
        res = rp.replay(ReplayParams(name="short-hold"))
        assert res.n == 1
        assert res.timeout_rate == 100.0, res.summary()
        # 对照：不设环境覆盖（默认跟随线上 5400/7200）时同一信号在第 3 根 K 线 TP
        monkeypatch.delenv("SCALP_META_TB_MAX_HOLD_SEC", raising=False)
        res2 = rp.replay(ReplayParams(name="default-hold"))
        assert res2.n == 1 and res2.tp_rate == 100.0, res2.summary()
