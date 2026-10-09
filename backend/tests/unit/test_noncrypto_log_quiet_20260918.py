# -*- coding: utf-8 -*-
"""[2026-09-18 数据中心优化·D-4] 非加密 ticker 告警必须**聚合降噪**而不是每条刷屏。

## 实测规模
`logs/data-center.log` 里 `拒绝写入非加密 ticker` = **2,219 条（全文件 174,558 行的 1.27%）**，
而实际只有 **10 个标的**（bybit 代币化股票/ETF：AMZN/EWY/IWM/MSTR/QQQ/SPY/BTR/FLOCK/DIA/USO），
在 4 个交易所 × 多轮采集中被反复打印。

## 要求
1. 逐条降 `debug`（默认日志级别 INFO 下不再刷屏）；
2. **按交易所定期聚合一条 `warning`**（保留可观测性、不掉级别）；
3. `KLINE_NONCRYPTO_LOG_INTERVAL_S=0` ⇒ 每条即时 warning（可回滚到旧行为）；
4. **已下架交易对**的告警不得被改动 —— 那是防"目录自读自写复活"的安全信息。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import kline_sync_meta as M  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("KLINE_NONCRYPTO_LOG_INTERVAL_S", raising=False)
    M._NONCRYPTO_COUNT.clear()
    M._NONCRYPTO_LAST_LOG.clear()
    yield
    M._NONCRYPTO_COUNT.clear()
    M._NONCRYPTO_LAST_LOG.clear()


def test_per_symbol_line_is_debug_not_warning(caplog):
    with caplog.at_level(logging.DEBUG):
        M._log_non_crypto_ticker("bybit", "QQQ")
    warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debugs, "逐条必须仍以 debug 记录（不丢可观测性）"
    # 首次调用即到期 ⇒ 会有 1 条聚合 warning；关键是"逐条"那条不是 warning
    per_symbol_warns = [r for r in warns if "ticker: QQQ" in r.getMessage()]
    assert not per_symbol_warns, "逐条不得再打 warning"


def test_aggregate_warning_fires_on_window_boundary(caplog):
    """聚合语义：**首次只登记起点**；窗口到期后的第一次调用汇报该窗口累积量。

    （我第一版在首次调用就报警、那时只有 1 条 ⇒ 之后一小时静默、聚合失去意义，
      被本用例抓出后改为"窗口到期汇报"。）
    """
    with caplog.at_level(logging.WARNING):
        M._log_non_crypto_ticker("okx", "SPY")     # 首次：登记起点，不报警
        assert not [r for r in caplog.records if "累计拒绝" in r.getMessage()], \
            "首次调用不应报警（否则聚合量恒为 1）"
        M._log_non_crypto_ticker("okx", "SPY")
        M._log_non_crypto_ticker("okx", "IWM")
        # 模拟窗口到期
        M._NONCRYPTO_LAST_LOG["okx"] -= 3600 + 1
        M._log_non_crypto_ticker("okx", "IWM")
    agg = [r for r in caplog.records if "累计拒绝非加密 ticker" in r.getMessage()]
    assert agg, "窗口到期必须汇报聚合"
    msg = agg[-1].getMessage()
    # 口径：**触发汇报的那次调用本身也计入**本窗口 ⇒ 4 次（SPY×2 + IWM×2）
    assert "4 次" in msg, f"应汇报窗口内累积 4 次（含触发本次），实际: {msg}"
    assert "2 个标的" in msg, "应给出窗口内去重标的数"


def test_interval_throttles_aggregate(caplog, monkeypatch):
    """窗口内不重复汇报。"""
    monkeypatch.setenv("KLINE_NONCRYPTO_LOG_INTERVAL_S", "3600")
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            M._log_non_crypto_ticker("binance", "AMZN")
        n = len([r for r in caplog.records if "累计拒绝" in r.getMessage()])
    assert n == 0, "窗口未到期不应汇报（只 debug）"


def test_zero_interval_rolls_back_to_every_call(caplog, monkeypatch):
    monkeypatch.setenv("KLINE_NONCRYPTO_LOG_INTERVAL_S", "0")
    with caplog.at_level(logging.WARNING):
        M._log_non_crypto_ticker("asterdex", "FLOCK")
        M._log_non_crypto_ticker("asterdex", "FLOCK")
    assert len([r for r in caplog.records if "累计拒绝" in r.getMessage()]) == 2, (
        "=0 时应回滚为每次都汇报（旧行为）")


def test_delisted_warning_untouched():
    """安全信息（已下架交易对）不得被本次降噪波及。"""
    src = (ROOT / "backend" / "services" / "kline_sync_meta.py").read_text(encoding="utf-8")
    i = src.index("DELISTED_BY_VENUE")
    seg = src[i:i + 400]
    assert "logger.warning" in seg or "_log_non_crypto" not in seg, (
        "已下架分支仍应是 warning（防目录自读自写复活）")
    assert "logger.warning(\n                \"[KlineSyncMeta] 拒绝写入已下架交易对" in src or \
           "拒绝写入已下架交易对" in src
