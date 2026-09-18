"""三层切分 + 周期分档单测（v6 计划 5.4.3）。"""
import os
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from backend.services.evolution.factor_evolution_loop import (
    _check_split_depth,
    _final_test_confirm,
    _load_data,
    _lookback_for_period,
    _required_coverage_days,
    _run_evolution_loop_impl,
    _split_days_for_period,
    _split_train_val_test,
)


def _make_df(n: int = 2200) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    return pd.DataFrame({
        "open": close * 0.999, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": rng.uniform(10, 20, n),
    })


def test_split_days_period_tiers():
    # [2026-08-30 挖矿升级 M1] 窗口按 DB 实际覆盖拉长：4h 验证段仅 360 根时 DSR×27 试验
    # 统计功效不足（ICIR 1.31 仍不显著）。1h 90/30/15→150/45/30，4h/1d 180/60/30→300/60/30。
    #
    # [2026-09-18 轮95 修] 5m 档在 **2026-09-07** 已由 30/10/10 改为 **26/10/10**
    # （理由写在 `factor_evolution_loop._PERIOD_SPLIT_DAYS` 的注释里：
    #  30 天窗口高于库内多数币实测覆盖 ~47d，每日 04:00 剔除后只剩 UNI/XRP，
    #  <MIN_SYMBOLS 导致整轮 depth_insufficient；回填追上 55d 目标后可用
    #  `FACTOR_EVO_*_DAYS` 再拉长）。**本测试当时没跟着改**，于是 4 条断言长期变红，
    # 把"真回归"淹在噪音里。此处按当前分档表对齐，并把「改动必须有解释」写成守卫。
    assert _split_days_for_period("5m") == (26, 10, 10)
    assert _split_days_for_period("1h") == (150, 45, 30)
    assert _split_days_for_period("4h") == (300, 60, 30)
    assert _split_days_for_period("1d") == (300, 60, 30)
    assert _split_days_for_period(None) == (300, 60, 30)  # 默认 4h
    # 每档都必须是 (train, val, test) 且三段为正/非负，防止手滑写成二元组
    for p in ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "1d"):
        td, vd, ted = _split_days_for_period(p)
        assert td > 0 and vd > 0 and ted >= 0, f"{p} 分档不合法: {(td, vd, ted)}"


def test_period_split_table_changes_are_documented():
    """分档表任何改动都必须带解释：表里每个上游注释块要说明"为什么是这个数"。

    这是本轮 4 条断言长期变红的根因 —— 表改了、原因写在代码里、测试没跟上。
    守卫做法：表定义处必须存在解释性注释（含 `>2026-` 或"对齐"等说明词）。
    """
    import inspect

    from backend.services.evolution import factor_evolution_loop as fel

    src = inspect.getsource(fel)
    anchor = src.index("_PERIOD_SPLIT_DAYS: dict")
    block = src[max(0, anchor - 900): anchor]
    assert any(k in block for k in ("对齐", "覆盖", "回填", "目标")), (
        "_PERIOD_SPLIT_DAYS 上方缺少解释性注释：改档位时请写明依据"
    )


def test_split_days_env_override():
    with patch.dict(os.environ, {"FACTOR_EVO_TRAIN_DAYS": "120", "FACTOR_EVO_VAL_DAYS": "40"}):
        assert _split_days_for_period("1h") == (120, 40, 0)
    with patch.dict(os.environ, {"FACTOR_EVO_TRAIN_DAYS": "120", "FACTOR_EVO_VAL_DAYS": "40", "FACTOR_EVO_TEST_DAYS": "20"}):
        assert _split_days_for_period("1h") == (120, 40, 20)


def test_lookback_period():
    # +50 安全缓冲（与 _lookback_for_period 实现对齐）；天数随 M1 分档表
    assert _lookback_for_period("4h") == (300 + 60 + 30) * 6 + 50    # 2390
    assert _lookback_for_period("1h") == (150 + 45 + 30) * 24 + 50   # 5450
    # 与分档表恒等（防两处各改一处）—— 5m 的绝对根数不再写死，
    # 因为它是"随回填墙浮动"的值（见 _PERIOD_SPLIT_DAYS 注释），写死会随窗口调整再度变红。
    for p in ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "1d"):
        td, vd, ted = _split_days_for_period(p)
        bpd = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48, "1h": 24,
               "2h": 12, "4h": 6, "8h": 3, "1d": 1}[p]
        assert _lookback_for_period(p) == (td + vd + ted) * bpd + 50, (
            f"{p} 的 lookback 与分档表不一致（两处各改了一处）"
        )


def test_split_train_val_test_4h():
    need = _lookback_for_period("4h")  # 2390
    dfs = {"BTC": _make_df(need + 100), "ETH": _make_df(need + 100)}
    train, val, test = _split_train_val_test(dfs, "4h")
    # 4h: 300/60/30 天 → 1800/360/180 根
    assert len(train["BTC"]) == 1800
    assert len(val["BTC"]) == 360
    assert len(test["BTC"]) == 180
    assert len(train["ETH"]) == 1800
    # 时间顺序：train < val < test
    assert train["BTC"].index.max() < val["BTC"].index.min()
    assert val["BTC"].index.max() < test["BTC"].index.min()


def test_split_train_val_test_1h():
    need = _lookback_for_period("1h")  # 5450
    dfs = {"BTC": _make_df(need + 100)}
    train, val, test = _split_train_val_test(dfs, "1h")
    # 1h: 150/45/30 天 → 3600/1080/720 根
    assert len(train["BTC"]) == 3600
    assert len(val["BTC"]) == 1080
    assert len(test["BTC"]) == 720


def test_split_skips_short_symbol():
    need = _lookback_for_period("4h")
    dfs = {"BTC": _make_df(need + 100), "ETH": _make_df(300)}  # ETH 不足
    train, val, test = _split_train_val_test(dfs, "4h")
    assert "BTC" in train
    assert "ETH" not in train and "ETH" not in val and "ETH" not in test


def test_final_test_confirm_intercepts_negative_ic():
    # 构造：AR(1) 收益（正自相关）→ 动量因子 IC 为正、反向因子 IC 为负
    rng = np.random.default_rng(1)
    n = 400
    x = np.zeros(n)
    noise = rng.normal(0, 0.01, n)
    for i in range(1, n):
        x[i] = 0.6 * x[i - 1] + noise[i]
    close = 100 * np.exp(np.cumsum(x))
    idx = pd.date_range("2026-01-01", periods=n, freq="4h")
    df = pd.DataFrame({"close": close, "volume": rng.uniform(10, 20, n)}, index=idx)
    dfs_test = {"BTC": df}

    # 正向因子：returns 均值（动量，与未来收益正相关）
    from backend.services.factor_engine.expr.parser import parse
    good_ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}
    # 反向因子：returns 均值取负号（IC 为负）
    bad_ast = {"op": "mul", "args": [{"c": -1}, {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}]}

    good_p = {"factor_id": "good", "expr": parse(good_ast), "source": "test"}
    bad_p = {"factor_id": "bad", "expr": parse(bad_ast), "source": "test"}
    eval_results = {"good": {}, "bad": {}}

    kept = _final_test_confirm([good_p, bad_p], eval_results, dfs_test)
    kept_ids = {p["factor_id"] for p in kept}
    # 正 IC 因子通过、负 IC 因子拦截
    assert "bad" not in kept_ids
    assert "good" in kept_ids
    assert kept[0].get("test_ic") is not None


def test_final_test_confirm_fail_closed_without_test():
    """测试集为空时默认 fail-closed 拦截（对齐 FACTOR_EVO_GATE_FAIL_CLOSED=1）。"""
    p = {"factor_id": "x", "expr": None, "source": "test"}
    kept = _final_test_confirm([p], {}, {})
    assert kept == []


def test_final_test_confirm_fail_open_without_test_env():
    """FACTOR_EVO_GATE_FAIL_CLOSED=0 时测试集为空 fail-open 放行。"""
    p = {"factor_id": "x", "expr": None, "source": "test"}
    with patch.dict(os.environ, {"FACTOR_EVO_GATE_FAIL_CLOSED": "0"}):
        kept = _final_test_confirm([p], {}, {})
    assert len(kept) == 1


def test_load_data_uses_period_lookback():
    """P0-1: period=5m 必须按 5m 档取数，不得回落 DEFAULT_LOOKBACK(4h)。"""
    need_5m = _lookback_for_period("5m")
    captured = {}

    class _Res:
        def to_dataframe(self):
            return _make_df(200)

    class _DC:
        def get_klines(self, sym, p, count=0, purpose=None):
            captured["count"] = count
            captured["period"] = p
            return _Res()

    fake_mod = MagicMock()
    fake_mod.data_center = _DC()
    with patch.dict("sys.modules", {"backend.services.data_center": fake_mod}):
        dfs = _load_data(symbols=["BTC"], period="5m")

    assert captured.get("period") == "5m"
    assert captured.get("count") == need_5m
    assert "BTC" in dfs


def test_required_coverage_days_5m():
    """覆盖天数必须与分档表恒等（不写死数字：5m 的 train 窗口随回填墙浮动）。"""
    td, vd, ted = _split_days_for_period("5m")
    assert _required_coverage_days("5m") == td + vd + ted
    assert _required_coverage_days("4h") == 300 + 60 + 30


def test_check_split_depth_short_fails():
    depth = _check_split_depth({"BTC": _make_df(500)}, "5m")
    assert depth["ok"] is False
    assert "BTC" in depth["short_symbols"]
    td, vd, ted = _split_days_for_period("5m")
    assert depth["need_days"] == td + vd + ted


def test_check_split_depth_ok():
    need = _lookback_for_period("5m")
    depth = _check_split_depth({"BTC": _make_df(need)}, "5m")
    assert depth["ok"] is True
    assert depth["short_symbols"] == []


def test_tag_short_horizon_prefix():
    from backend.services.evolution.factor_evolution_loop import _tag_short_horizon_factors
    tagged = _tag_short_horizon_factors(
        [{"factor_id": "abc123", "source": "gp", "expr_id": "abc123"}], "5m",
    )
    assert tagged[0]["factor_id"].startswith("s5m_")
    assert "horizon=scalp" in tagged[0]["source"]
    # 4h 不打标
    mid = _tag_short_horizon_factors(
        [{"factor_id": "abc123", "source": "gp"}], "4h",
    )
    assert mid[0]["factor_id"] == "abc123"


def test_depth_insufficient_aborts_no_silent_degrade():
    """P0-1/P0-2: 深度不足时返回 error，禁止 train=val=全窗假 OOS。"""
    short = {"BTC": _make_df(500)}  # 远小于 5m need≈14450
    with patch(
        "backend.services.evolution.factor_evolution_loop._load_data",
        return_value=short,
    ), patch(
        "backend.services.evolution.factor_evolution_loop._ensure_governance_columns",
    ), patch(
        "backend.services.evolution.factor_evolution_loop._nudge_depth_backfill",
    ):
        import time
        report = _run_evolution_loop_impl(
            symbols=["BTC"], period="5m", quick=True, t0=time.time(),
        )
    assert report.get("error") in ("depth_insufficient", "split_insufficient_data")
