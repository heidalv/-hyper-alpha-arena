# -*- coding: utf-8 -*-
"""triple-barrier 标签（2026-09-02 P1.1）。

问题：旧标签是"信号后固定 1800s 的收盘价收益"，而实盘盈亏由 TP/SL 谁先被触及
决定 —— 短线持仓上限 7200s（旧 horizon 只覆盖 1/4）、中位持仓 46 分钟、止盈命中
率仅 13%。标签与执行错配时，模型把标签预测得再准也预测不了那一笔的真实输赢。

本用例锁定 triple-barrier 判定的路径语义，特别是三个最容易写错的地方：
1. 做空方向的轨道摆放（TP 在下、SL 在上）；
2. 同一根 K 线内两轨都触及时必须判 SL（保守），否则标签会系统性乐观；
3. 窗口未走完且未触轨时必须返回 None（继续等），不能提前判 timeout。
"""
import pytest

from backend.services.scalp_signal_logger import (
    _tb_max_hold_sec,
    triple_barrier_outcome,
)

# K 线为 (ts, high, low, close)，5m = 300s
T0 = 1_000_000


def _bar(i, hi, lo, cl):
    return (T0 + i * 300, hi, lo, cl)


def _run(rows, *, direction="long", entry=100.0, tp=0.01, sl=0.01,
         max_hold=1800, now=None):
    return triple_barrier_outcome(
        rows, start_ts=T0, entry=entry, direction=direction,
        tp_pct=tp, sl_pct=sl, max_hold_sec=max_hold,
        now_ts=(now if now is not None else T0 + 10_000),
    )


class TestLongBarriers:
    def test_tp_hit(self):
        out = _run([_bar(0, 100.5, 99.8, 100.2), _bar(1, 101.5, 100.4, 101.2)])
        assert out["kind"] == "tp"
        assert out["hold_sec"] == 300
        assert out["fwd_ret"] == pytest.approx(0.01)

    def test_sl_hit(self):
        out = _run([_bar(0, 100.2, 99.9, 100.0), _bar(1, 100.1, 98.5, 98.7)])
        assert out["kind"] == "sl"
        assert out["fwd_ret"] == pytest.approx(-0.01)

    def test_timeout_uses_last_close(self):
        """两轨都没碰到、窗口走完 → timeout，收益按最后一根收盘价。"""
        rows = [_bar(i, 100.3, 99.7, 100.1) for i in range(8)]
        out = _run(rows, max_hold=1800)
        assert out["kind"] == "timeout"
        assert out["hold_sec"] == 1800
        assert out["fwd_ret"] == pytest.approx(0.001, abs=1e-6)

    def test_earliest_barrier_wins(self):
        """先到的轨道生效，不能被后面的行情改写。"""
        rows = [
            _bar(0, 100.2, 99.9, 100.0),
            _bar(1, 101.5, 100.1, 101.3),   # TP
            _bar(2, 101.0, 98.0, 98.2),     # 之后跌穿 SL，但已出场
        ]
        out = _run(rows)
        assert out["kind"] == "tp"
        assert out["hold_sec"] == 300


class TestShortBarriers:
    """做空的轨道方向是最易写反的地方：TP 在下、SL 在上。"""

    def test_short_tp_is_downward(self):
        out = _run([_bar(0, 100.1, 98.9, 99.0)], direction="short")
        assert out["kind"] == "tp"
        assert out["fwd_ret"] == pytest.approx(0.01)

    def test_short_sl_is_upward(self):
        out = _run([_bar(0, 101.2, 100.0, 101.1)], direction="short")
        assert out["kind"] == "sl"
        assert out["fwd_ret"] == pytest.approx(-0.01)

    def test_short_timeout_ret_is_inverted(self):
        """空头收益方向相反：价格跌 → 正收益。"""
        rows = [_bar(i, 100.2, 99.5, 99.6) for i in range(8)]
        out = _run(rows, direction="short", max_hold=1800)
        assert out["kind"] == "timeout"
        assert out["fwd_ret"] == pytest.approx(0.004, abs=1e-6)


class TestConservativeTie:
    """同根 K 线内两轨都触及 → 判 SL。标签乐观偏移会让模型学出"敢扛"。"""

    def test_long_tie_resolves_to_sl(self):
        out = _run([_bar(0, 101.5, 98.5, 100.0)])
        assert out["kind"] == "sl", "同根两轨齐触必须判 SL（保守）"

    def test_short_tie_resolves_to_sl(self):
        out = _run([_bar(0, 101.5, 98.5, 100.0)], direction="short")
        assert out["kind"] == "sl"

    def test_tie_on_later_bar_still_sl(self):
        rows = [_bar(0, 100.2, 99.9, 100.0), _bar(1, 101.9, 98.1, 100.0)]
        out = _run(rows)
        assert out["kind"] == "sl"
        assert out["hold_sec"] == 300


class TestWaitingSemantics:
    """未到期不得提前定论 —— 否则大量样本会被错标为 timeout。"""

    def test_returns_none_when_window_open(self):
        rows = [_bar(0, 100.2, 99.9, 100.0)]
        out = _run(rows, max_hold=7200, now=T0 + 600)
        assert out is None, "窗口未走完且未触轨 → 必须返回 None 继续等"

    def test_settles_once_deadline_passed(self):
        rows = [_bar(i, 100.2, 99.9, 100.0) for i in range(4)]
        out = _run(rows, max_hold=900, now=T0 + 5000)
        assert out is not None and out["kind"] == "timeout"

    def test_bars_after_deadline_ignored(self):
        """截止之后才触及的轨道不算 —— 实盘那时已被强平。"""
        rows = [
            _bar(0, 100.2, 99.9, 100.0),
            _bar(1, 100.2, 99.9, 100.0),
            _bar(9, 105.0, 99.0, 104.0),   # 远在 deadline 之后
        ]
        out = _run(rows, max_hold=900, now=T0 + 20_000)
        assert out["kind"] == "timeout"

    def test_no_bars_in_window_returns_none(self):
        out = _run([], max_hold=900, now=T0 + 5000)
        assert out is None

    def test_bars_only_before_signal_ignored(self):
        """信号之前的 K 线不参与判定（否则等于前视）。"""
        rows = [(T0 - 600, 105.0, 95.0, 100.0), (T0 - 300, 105.0, 95.0, 100.0)]
        out = _run(rows, max_hold=900, now=T0 + 5000)
        assert out is None, "只有信号前的 K 线时不得结算"


class TestGuards:
    def test_invalid_inputs_return_none(self):
        rows = [_bar(0, 101.5, 98.5, 100.0)]
        assert _run(rows, entry=0.0) is None
        assert _run(rows, tp=0.0) is None
        assert _run(rows, sl=0.0) is None
        assert _run([], max_hold=900, now=T0) is None

    def test_max_hold_follows_tier_cap(self):
        """垂直轨须**跟随**实盘短线持仓上限，而非旧 horizon 1800s。

        [2026-09-02 P2.2 后修正] 原断言写死 == 7200。持仓上限随后被回放定标
        改为 5400s（60/90/120/180min 交叉验证在 90min 处见峰值 +48.4bp），
        这条就假报警了 —— 而代码本身是对的：_tb_max_hold_sec 动态读
        TIER_PROTECTION_PARAMS，本来就会自动跟随。
        所以这里锁"跟随"这个契约，不锁某个具体秒数：垂直轨与实际持仓上限
        脱钩才是真 bug（标签窗口 ≠ 执行窗口，等于又回到旧 horizon 的错配）。
        """
        from backend.config.settings import TIER_PROTECTION_PARAMS

        expected = int(TIER_PROTECTION_PARAMS["short"]["max_hold_sec"])
        assert _tb_max_hold_sec() == expected
        assert expected != 1800, "垂直轨不得退回旧固定 horizon"

    def test_max_hold_env_override(self, monkeypatch):
        monkeypatch.setenv("SCALP_META_TB_MAX_HOLD_SEC", "3600")
        assert _tb_max_hold_sec() == 3600
        monkeypatch.setenv("SCALP_META_TB_MAX_HOLD_SEC", "10")
        assert _tb_max_hold_sec() == 300, "须有下限保护"

    def test_asymmetric_barriers(self):
        """TP/SL 不等距时各自独立判定（实盘 TP 2.27% / SL 1.11% 就是不等距）。"""
        rows = [_bar(0, 101.5, 99.5, 101.0)]
        out = _run(rows, tp=0.03, sl=0.004)
        assert out["kind"] == "sl", "SL 更近时应先被触及"
        out2 = _run(rows, tp=0.01, sl=0.03)
        assert out2["kind"] == "tp"


class TestSchemaWiring:
    """标签列与调度接线不漂移。"""

    def test_model_has_tb_columns(self):
        from backend.database.models import ScalpSignalLog
        cols = set(ScalpSignalLog.__table__.columns.keys())
        for c in ("tb_tp_pct", "tb_sl_pct", "tb_max_hold_sec", "tb_kind",
                  "tb_hold_sec", "tb_fwd_ret", "tb_net_ret", "tb_win",
                  "tb_settled"):
            assert c in cols, f"缺少 triple-barrier 列 {c}"

    def test_old_label_columns_untouched(self):
        """旧标签列必须保留 —— 31.5 万条历史样本依赖它。"""
        from backend.database.models import ScalpSignalLog
        cols = set(ScalpSignalLog.__table__.columns.keys())
        for c in ("fwd_ret", "net_ret", "win", "horizon_sec", "settled"):
            assert c in cols, f"旧标签列 {c} 不得删除"

    def test_log_signal_accepts_tpsl(self):
        import inspect
        from backend.services.scalp_signal_logger import log_signal
        params = inspect.signature(log_signal).parameters
        assert "tp_pct" in params and "sl_pct" in params

    def test_scheduler_registers_tb_settle(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[3] / "backend" / "services"
               / "evolution_scheduler.py").read_text(encoding="utf-8")
        assert "settle_triple_barrier" in src, "tb 结算须挂上调度"


class TestSettleKlineSource:
    """[2026-09-02] 结算取 K 线必须走 research 用途：不设新鲜度门、不强制切活跃所。

    此前走 purpose="trade"：币一退出活跃池、K 线停更就被判"过期"返回空，它过去几周
    的信号全部结算成 none（实测 DOT 1382 条、KAITO 1.4 万条，共 5.7 万条标签丢失）。
    """

    def _run(self, monkeypatch, first_rows, second_rows=None):
        from unittest.mock import MagicMock
        from backend.services import scalp_signal_logger as L
        from backend.services import data_center as dc_mod

        calls = []

        def _fake_get_klines(symbol, period, count=0, exchange=None, purpose="trade",
                             closed_only=None, **kw):
            calls.append({"symbol": symbol, "period": period, "count": count,
                          "exchange": exchange, "purpose": purpose, "closed_only": closed_only})
            res = MagicMock()
            res.rows = list(first_rows) if len(calls) == 1 else list(second_rows or [])
            return res

        monkeypatch.setattr(dc_mod.data_center, "get_klines", _fake_get_klines)
        monkeypatch.setattr(L, "_settle_exchange", lambda: "binance")
        return L, calls

    def test_uses_research_purpose_with_settle_exchange(self, monkeypatch):
        rows = [{"timestamp": 1000, "high": 2, "low": 1, "close": 1.5}]
        L, calls = self._run(monkeypatch, rows)
        out = L._load_ohlc("DOT", {})
        assert out == [(1000, 2.0, 1.0, 1.5)]
        assert len(calls) == 1
        c = calls[0]
        assert c["purpose"] == "research", "历史结算不得走 trade 用途（过期即空、强制切所）"
        assert c["exchange"] == "binance", "应显式按结算所取价"
        assert c["closed_only"] is True, "仍不得使用未收盘 bar"
        assert c["symbol"] == "DOT" and c["period"] == "5m"

    def test_falls_back_to_any_exchange_when_settle_exchange_empty(self, monkeypatch):
        rows = [{"timestamp": 2000, "close": 3.0}]
        L, calls = self._run(monkeypatch, first_rows=[], second_rows=rows)
        out = L._load_klines("DOT", {})
        assert out == [(2000, 3.0)]
        assert len(calls) == 2
        assert calls[1]["exchange"] is None and calls[1]["purpose"] == "research"
