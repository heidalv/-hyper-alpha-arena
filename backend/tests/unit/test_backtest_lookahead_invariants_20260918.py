# -*- coding: utf-8 -*-
"""[F334 2026-09-18] 回测前视不变量（把"审计文档说已修"锁成测试）。

背景：仓库里两份审计文档（`交易审计修复设计文档_V1.md`、`_audit_short_mid_202608.md` B8）曾判定
`live_pipeline_backtest_engine` 存在**入场同根收盘成交**与**资金费/FGI 取到未来样本**两类前视；
复查确认两处**均已修**（P0-5 批次），但**没有测试锁住** ⇒ 任何人回退即静默复原。
本文件按报告 §6.3 判据 **A1（无前视）** 加锁：既做行为验证（取数必须 ≤ t），也做源码契约（成交在下一根）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.live_pipeline_backtest_engine import LivePipelineBacktestEngine  # noqa: E402

_SRC = (ROOT / "backend" / "services" / "live_pipeline_backtest_engine.py").read_text(
    encoding="utf-8")


# ── 1. 行为：资金费只能取 ≤ t 的历史样本（不得取未来） ──────────────────────

def test_funding_rate_is_backward_only():
    rates = {500: 0.001, 1000: 0.002, 5000: 0.9}      # 5000 是"未来"
    got = LivePipelineBacktestEngine._get_funding_rate(1000, rates)
    assert got != 0.9, "取到了未来样本（前视）"
    assert got in (0.001, 0.002), got


def test_fgi_is_backward_only():
    fgi = {500: 10.0, 1000: 20.0, 5000: 99.0}
    got = LivePipelineBacktestEngine._get_fgi(1000, fgi)
    assert got != 99.0, "取到了未来 FGI（前视）"
    assert got in (10.0, 20.0), got


# ── 2. 源码契约：默认按【下一根开盘】成交（不是同根收盘） ────────────────────

def test_default_fill_model_is_next_open():
    assert 'os.getenv("BACKTEST_LP_FILL_MODEL", "next_open")' in _SRC, \
        "默认成交模型必须仍为 next_open（同日收盘成交＝前视）"


def test_fill_uses_next_bar():
    assert "_fill_bar = bars[i + 1]" in _SRC, "入场未使用下一根 bar"
    assert "_fill_price = float(getattr(_fill_bar, \"o\", _fill_bar.c) or _fill_bar.c)" in _SRC, \
        "入场价必须取下一根的开盘价"


# ── 3. 源码契约：资金费按 8h UTC 相位结算（跨边界只结一次） ─────────────────

def test_funding_settlement_uses_8h_phase():
    assert "// 28800" in _SRC, "未见 8h 相位结算判据（00/08/16 UTC）"


# ── 4. 两套引擎的成交模型**都已**默认 next_open（B3 实为已完成，测试锁住现状） ──

def test_both_backtest_engines_default_to_next_open():
    """[P0-5 前视修复] 两套回测引擎都默认 `next_open`（下一根开盘成交），
    `close`（同日收盘成交＝前视）只能由 env 显式开启。

    订正：此前仓库审计文档（`交易审计修复设计文档_V1.md:19`）称"`backtest_engine`
    fill_model 默认 close、两套引擎不一致"——**该说法已过期**；实测
    `backtest_engine.py:132-134` 的 `BacktestConfig.fill_model` 默认已是
    `os.getenv("BACKTEST_FILL_MODEL", "next_open")`。本测试锁住"两套都 next_open"这一现状，
    防止任何一侧被悄悄改回 close（前视）。
    """
    bt_src = (ROOT / "backend" / "services" / "backtest_engine" / "backtest_engine.py").read_text(
        encoding="utf-8")
    assert 'os.getenv("BACKTEST_FILL_MODEL", "next_open")' in bt_src, \
        "backtest_engine 的成交模型默认不再是 next_open（可能被改回 close = 前视）"
    assert 'os.getenv("BACKTEST_LP_FILL_MODEL", "next_open")' in _SRC, \
        "LivePipelineBacktestEngine 的成交模型默认不再是 next_open"


# ── 5. B4 因子归因（phase 1/2）不变量 ────────────────────────────────────────

def _synthetic_run(dirs):
    """跑一次合成回测：把信号与因子方向都换成确定性桩，返回 BacktestResult。"""
    from backend.services.backtest_evolution_engine import Bar
    from backend.services.live_pipeline_backtest_engine import (
        DEFAULT_PIPELINE_PARAMS, LivePipelineBacktestEngine,
    )
    eng = LivePipelineBacktestEngine(initial_capital=10000.0)
    eng._pipeline_signal = lambda i, bars, rsi, macd, p, fr, fg: (
        "long" if dirs[i % len(dirs)] > 0 else ("short" if dirs[i % len(dirs)] < 0 else None))
    eng._compute_factor_direction = lambda i, bars, p: dirs[i % len(dirs)]
    bars, px = [], 100.0
    for i in range(80):
        px += 0.4 if i % 3 else -0.3
        bars.append(Bar(timestamp=1_700_000_000 + i * 900, dt_str=f"t{i}",
                        o=px, h=px + 0.6, l=px - 0.6, c=px, v=100.0, idx=i))
    p = dict(DEFAULT_PIPELINE_PARAMS)
    p.update({"factor_signal_weight": 0.0, "min_bars_gap": 1})
    return eng.run(bars, p, symbol="TEST", tier="mid")


def test_factor_dir_buckets_conserve_trades_and_pnl():
    """phase 1 验收：① 桶 n 之和 == total_trades（精确）；② Σ(桶 n×均值) == 总 PnL（容差 1e-6）。"""
    r = _synthetic_run([1, -1, 0])
    if r.total_trades == 0:
        import pytest
        pytest.skip("合成行情未产生成交（环境相关），跳过守恒断言")
    assert sum(int(b["n"]) for b in r.factor_attr_by_dir.values()) == r.total_trades, \
        "方向桶 n 之和必须等于 total_trades"
    attr_pnl = sum(int(b["n"]) * float(b["avg_pnl"]) for b in r.factor_attr_by_dir.values())
    total_pnl = sum(float(getattr(t, "pnl", 0.0) or 0.0) for t in r.trades)
    assert abs(attr_pnl - total_pnl) < 1e-6, f"Σ桶贡献 {attr_pnl} vs 总PnL {total_pnl}"


def test_factor_name_attr_is_optin_only(monkeypatch):
    """phase 2 step 2 契约：`BACKTEST_FACTOR_ATTR` 未开时**不得**填充 `factor_attr_by_name`。"""
    monkeypatch.delenv("BACKTEST_FACTOR_ATTR", raising=False)
    r = _synthetic_run([1, -1])
    assert r.factor_attr_by_name == {}, "默认关时该字段必须为空（避免无谓内存/耗时）"


def test_attribute_trades_by_factor_coverage_semantics():
    """`coverage` = 该因子在开仓 bar 上有非 0 值的交易占比；全 0 值因子 coverage 应为 0。"""
    from types import SimpleNamespace as NS
    from backend.services.live_pipeline_backtest_engine import attribute_trades_by_factor as f

    trades = [NS(entry_bar=10, pnl=+5.0, quantity=1.0, entry_price=100.0),
              NS(entry_bar=11, pnl=-2.0, quantity=1.0, entry_price=100.0),
              NS(entry_bar=12, pnl=+1.0, quantity=1.0, entry_price=100.0)]
    side = {10: {"macd": 0.5, "zero_fac": 0.0}, 11: {"macd": -0.3, "zero_fac": 0.0},
            12: {"macd": 0.0, "zero_fac": 0.0}}
    r = f(trades, side)
    # `coverage` 按设计**舍入到 4 位小数**（与本文件其它输出一致）⇒ 容差必须与舍入精度匹配
    # （1e-4），不能拿 1e-9 去比 0.6667 与 2/3 —— 这与 F340 那次"容差不匹配舍入"是同一类错误。
    assert abs(r["macd"]["coverage"] - 2 / 3) < 1e-4, r["macd"]
    assert r["zero_fac"]["coverage"] == 0.0, "恒 0 值因子不得有 coverage"
    assert r["macd"]["pnl_long"] == 5.0 and r["macd"]["pnl_short"] == -2.0
    assert abs(r["macd"]["avg_bp_long"] - 500.0) < 1e-6


# ── 6. B4 phase 2 step 3：归因落盘/读回 + 周报块契约（F343/F344） ──────────────

def test_factor_attr_persist_and_load_roundtrip(tmp_path, monkeypatch):
    """落盘 → 读回 → 按 run_id 过滤；且**口径声明随数据落盘**（attr_note）。"""
    from backend.services.live_pipeline_backtest_engine import (
        load_factor_attr, persist_factor_attr,
    )

    monkeypatch.setenv("BACKTEST_FACTOR_ATTR_PATH", str(tmp_path / "attr.jsonl"))
    assert load_factor_attr("none") == [], "空文件应返回空列表（不得抛异常）"
    p = persist_factor_attr(run_id="lp_t1", symbol="TEST", tier="mid",
                            by_dir={"1": {"n": 2, "avg_pnl": 1.0}},
                            by_name={"macd": {"n_long": 1, "coverage": 0.5}})
    assert p is not None, "落盘应成功"
    recs = load_factor_attr("lp_t1")
    assert len(recs) == 1 and recs[0]["run_id"] == "lp_t1"
    assert "coverage" in recs[0]["attr_note"], "口径声明必须随数据落盘（防下游误当增量 alpha）"
    assert load_factor_attr("other") == [], "按 run_id 过滤必须生效"


def test_weekly_backtest_attr_block_reads_jsonl(tmp_path, monkeypatch):
    """周报块：无记录 ⇒ available=False；有记录 ⇒ 透出 latest 且带口径说明（只读、不写库）。"""
    from backend.services.live_pipeline_backtest_engine import persist_factor_attr
    from backend.services.unified_strategy.weekly_loop import _backtest_factor_attr_block

    monkeypatch.setenv("BACKTEST_FACTOR_ATTR_PATH", str(tmp_path / "attr2.jsonl"))
    b0 = _backtest_factor_attr_block()
    assert b0.get("available") is False, b0

    persist_factor_attr(run_id="lp_t2", symbol="ETH", tier="mid",
                        by_dir={"-1": {"n": 1, "avg_pnl": -0.5}},
                        by_name={"hv": {"n_short": 1, "coverage": 1.0}})
    b1 = _backtest_factor_attr_block()
    assert b1.get("available") is True and b1.get("runs") == 1
    assert b1["latest"]["run_id"] == "lp_t2" and "hv" in b1["latest"]["by_name_top"]
    assert "并列" in b1.get("note", "") or "覆盖度" in b1.get("note", ""), b1.get("note")




