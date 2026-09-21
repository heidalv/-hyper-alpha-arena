# -*- coding: utf-8 -*-
"""[轮153 2026-09-21] 链上辅助读数不再给 LLM 喂**假 0**，`active_addresses` 按真实语义改名。

## 溯源结论（reports/_轮153_模板族归档与aux字段溯源_20260921.md 第五节）
唯一写入者 `kline_enrichment_service.record_aux_snapshots`（`unified_data_pool.py:580` 调用）。
9 列里只有 `fear_greed / btc_dominance / tvl` 恒满（免费全局源）；其余 6 列结构性 NULL：
`exchange_net_flow` 缺 Coinglass key、`whale_tx_*` 真实源是付费 API、
`social_score/news_sentiment/discussion_volume` 的采集器 2026-08-17 被删。
`active_addresses` 是**唯一"有值但错"**的列：只有 BTC 有值，且值为 blockchain.info 的
`n_tx` = 全比特币网络**日交易笔数**，被挂在"活跃地址"名下。

## 本轮改法（用户批准方案 A）
`context_pack._emit_aux_readings`：
  · 值为 NULL ⇒ **不写这个键**（依 2026-07-10 原则「取不到就不填，让下游明确无此项数据」）；
  · 值非空 ⇒ 写 `btc_network_tx_count`（真实语义），不再写 `active_addresses`；
  · 回滚开关 `CTX_AUX_LEGACY_ACTIVE_ADDRESSES=true` 恢复旧行为（含 0 填充）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _clear_market_cache():
    from backend.services.analysis import context_pack as cp

    cp._MARKET_LAYER_CACHE.clear()
    yield
    cp._MARKET_LAYER_CACHE.clear()


def _emit(aux_row, legacy=None, monkeypatch=None):
    from backend.services.analysis import context_pack as cp

    if legacy is not None:
        monkeypatch.setenv("CTX_AUX_LEGACY_ACTIVE_ADDRESSES", legacy)
    d = {}
    cp._emit_aux_readings(d, aux_row)
    return d


def test_null_active_addresses_writes_no_key(monkeypatch):
    """其他 34 个币的真实情形：NULL ⇒ 键必须缺席（旧的 0 是假数据）。"""
    d = _emit({"fear_greed": 71, "btc_dominance": 55.2, "active_addresses": None,
               "timestamp_ms": 1789930000000}, monkeypatch=monkeypatch)
    assert "active_addresses" not in d, "NULL 被填成了 0（LLM 会读作『链上毫无活动』）"
    assert "btc_network_tx_count" not in d
    assert d["fear_greed"] == 71 and d["btc_dominance"] == 55.2, "既有字段不得被本轮改动影响"


def test_btc_value_renamed_to_true_semantics(monkeypatch):
    """BTC 的真实情形：有值 ⇒ 按真实语义写 btc_network_tx_count，且不再冒名 active_addresses。"""
    d = _emit({"fear_greed": 71, "btc_dominance": 55.2, "active_addresses": 331204.0,
               "timestamp_ms": 1789930000000}, monkeypatch=monkeypatch)
    assert d.get("btc_network_tx_count") == 331204.0
    assert "active_addresses" not in d, "错标名不得继续出现（值其实是全网日交易笔数）"


def test_onchain_macro_alias_kept(monkeypatch):
    """量化简报读 `md.onchain_macro.fear_greed` 这条路径不能被本轮改动打断。"""
    d = _emit({"fear_greed": 63, "btc_dominance": 54.1, "active_addresses": None,
               "timestamp_ms": 1789930000000}, monkeypatch=monkeypatch)
    assert (d.get("onchain_macro") or {}).get("fear_greed") == 63
    assert (d.get("onchain_macro") or {}).get("ts_ms") == 1789930000000


def test_rollback_switch_restores_legacy(monkeypatch):
    """回滚开关必须真的能恢复旧行为（否则出问题没法对照排查）。

    ⚠️ 旧行为**不是**"填 0"：`_r(v, n)` 的第二个参数是**精度**，且 `v is None → None`，
    所以旧代码给每个币写的是 `active_addresses: None`（有键无值）。
    本测试钉住的正是这个真实语义 —— 我在轮153 报告初稿里误写成"按 0 填充"，已更正。
    """
    d = _emit({"fear_greed": 71, "btc_dominance": 55.2, "active_addresses": None,
               "timestamp_ms": 1}, legacy="true", monkeypatch=monkeypatch)
    assert "active_addresses" in d, "回滚开关未生效：旧行为会给每个币写这个键"
    assert d["active_addresses"] is None, "旧行为的缺失值是 None（不是 0）"
    assert "btc_network_tx_count" not in d


def test_deep_context_summary_uses_true_name():
    """第二个面向 LLM 的展示点（`agent_deep_context.build_onchain_summary`）也要用真实语义名。"""
    from backend.services.agent_deep_context import _aux_display_name

    assert _aux_display_name("active_addresses") == "btc_network_tx_count"
    for k in ("fear_greed", "btc_dominance", "tvl", "whale_tx_count"):
        assert _aux_display_name(k) == k, f"{k} 不该被改名"


# ── [轮153d] 方案 A 全链：采集端不输出缺失项 + 展示端不写 btc_dominance 的 0 ──

def test_stored_zero_dominance_is_treated_as_missing(monkeypatch):
    """存量行里 btc_dominance=0.0（近 3 天 9,926 行唯一去重值）不得进 LLM market 层。"""
    d = _emit({"fear_greed": 71, "btc_dominance": 0.0, "active_addresses": None,
               "timestamp_ms": 1}, monkeypatch=monkeypatch)
    assert "btc_dominance" not in d, "0.0 是采集器旧默认值造成的假读数（真值 ~55%）"
    assert (d.get("onchain_macro") or {}).get("btc_dominance") is None


def test_real_dominance_still_passes(monkeypatch):
    d = _emit({"fear_greed": 71, "btc_dominance": 54.83, "active_addresses": None,
               "timestamp_ms": 1}, monkeypatch=monkeypatch)
    assert d.get("btc_dominance") == 54.83


def test_dominance_rollback_switch(monkeypatch):
    d = _emit({"fear_greed": 71, "btc_dominance": 0.0, "active_addresses": None,
               "timestamp_ms": 1}, legacy="true", monkeypatch=monkeypatch)
    # legacy 开关只回滚 active_addresses；btc_dominance 另有 CTX_AUX_LEGACY_ZERO_READINGS
    monkeypatch.setenv("CTX_AUX_LEGACY_ZERO_READINGS", "true")
    d = _emit({"fear_greed": 71, "btc_dominance": 0.0, "active_addresses": None,
               "timestamp_ms": 1}, monkeypatch=monkeypatch)
    assert d.get("btc_dominance") == 0.0, "回滚开关未生效"


def test_collector_omits_missing_macro_fields(monkeypatch):
    """采集端：源取不到就不输出该键（旧行为是 fear_greed→50 / btc_dominance→0.0）。"""
    from backend.services import onchain_data_collector as odc

    monkeypatch.delenv("AUX_MACRO_OMIT_MISSING", raising=False)
    coll = odc.OnchainDataCollector()
    monkeypatch.setattr(coll, "_collect_macro", lambda: {"fear_greed": None, "btc_dominance": None})
    monkeypatch.setattr(coll, "_collect_tvl", lambda s: 0.0)
    monkeypatch.setattr(coll, "_collect_mempool", lambda: {})
    monkeypatch.setattr(coll, "_collect_blockchain_info", lambda: {})
    monkeypatch.setattr(coll, "_collect_etherscan", lambda: {})
    monkeypatch.setattr(coll, "_collect_coinglass", lambda: {})
    row = coll.collect_all(["BTC"])["BTC"]
    assert "fear_greed" not in row, "源失败时不得写入 50 中性值"
    assert "btc_dominance" not in row, "源失败时不得写入 0.0"
    assert "tvl" not in row, "tvl 为 0（取不到）时不得输出"
    assert "mempool_size" not in row, "mempool 源失败时不得输出 0"


def test_collector_keeps_real_macro_values(monkeypatch):
    from backend.services import onchain_data_collector as odc

    coll = odc.OnchainDataCollector()
    monkeypatch.setattr(coll, "_collect_macro", lambda: {"fear_greed": 71.0, "btc_dominance": 54.8})
    monkeypatch.setattr(coll, "_collect_tvl", lambda s: 1.2e9)
    monkeypatch.setattr(coll, "_collect_mempool",
                        lambda: {"mempool_size": 0, "fee_rate": 3.0, "congestion": 0.0})
    monkeypatch.setattr(coll, "_collect_blockchain_info", lambda: {})
    monkeypatch.setattr(coll, "_collect_etherscan", lambda: {})
    monkeypatch.setattr(coll, "_collect_coinglass", lambda: {})
    row = coll.collect_all(["BTC"])["BTC"]
    assert row["fear_greed"] == 71.0 and row["btc_dominance"] == 54.8 and row["tvl"] == 1.2e9
    # 空 mempool 的 0 是**真实读数**（键存在即保留），与"源失败"区别对待
    assert row["mempool_size"] == 0 and row["mempool_fee_rate"] == 3.0


def test_collector_rollback_switch_restores_defaults(monkeypatch):
    from backend.services import onchain_data_collector as odc

    monkeypatch.setenv("AUX_MACRO_OMIT_MISSING", "false")
    assert odc.macro_omit_missing() is False
    coll = odc.OnchainDataCollector()
    with __import__("unittest").mock.patch("requests.get",
                                          side_effect=Exception("down")):
        assert coll._collect_fear_greed() == 50.0
        assert coll._collect_btc_dominance() == 0.0


def test_live_market_layer_has_no_fake_zero(monkeypatch):
    """端到端（真库）：market 层不得再出现 active_addresses=0 这种假读数。"""
    from backend.services.analysis import context_pack as cp

    pack = cp.build("midlong_thesis", symbols=["BTC", "ETH"])
    syms = (pack.layers.get("market") or {}).get("symbols", {})
    for sym in ("BTC", "ETH"):
        row = syms.get(sym) or {}
        if not row:
            continue
        if "active_addresses" in row:
            assert row["active_addresses"] not in (0, 0.0), (
                f"{sym} 的 active_addresses 仍是 0 —— 假数据没清掉"
            )
        # BTC 有真值时必须是新语义名
        if row.get("btc_network_tx_count") is not None:
            assert float(row["btc_network_tx_count"]) > 0
