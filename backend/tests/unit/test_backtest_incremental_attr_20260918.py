# -*- coding: utf-8 -*-
"""[F354–F357 2026-09-18] 回测**增量（反事实）归因**的前提与陷阱。

这一轮把我自己的三次错误钉成了测试，因为每一个都会让"增量归因"得出**看起来合理但错**的数：

- **F355 假零（我犯的第 11 个错）**：首版消融脚本没传 funding/FGI ⇒ `flow_dir=sent_dir=0`
  ⇒ `replay_confirmation` 要求 ≥2 个非零同向维度，永远 HOLD ⇒ 方向退化为 `mid_bias`
  ⇒ 因子方向**在构造上无法影响决策** ⇒ 剔除任何因子结果都逐位相同，像"所有因子增量为 0"。
  本文件用 `replay_confirmation` 直接钉住这条依赖，并断言脚本会**拒绝**在空序列下出结论。
- **F356 我的第 13 次自我更正**：我一度据此宣布"因子通道功能性死亡"。加上真实 funding/FGI 后
  逐 bar 探针显示因子方向在 **188~1085 个 bar** 上改变返回值 ⇒ 通道是通的，**错的是调用方**：
  `parity_score._run_backtest_side` 与 `strategy_evolver.py:515`（以及我的两个脚本）
  都没传，静默关掉了整个因子维度。
- **F354**：因子方向缓存键只有 `(sym, tf, ts0, n)` + `gov/full` 模式标签，**不含因子集合内容**
  ⇒ 缓存命中时"剔除因子重跑"会复用基线方向序列 ⇒ 又一重假零来源。
- **F357**：`replay_finalize` 在**因子方向之前**就决定了"是否开仓"（`if action != "enter": return None`）
  ⇒ 因子最多只能影响**方向**，永远不能影响**是否进场**。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.live_pipeline_backtest_engine import (  # noqa: E402
    replay_confirmation,
    replay_rule_decision,
)


def _load_script(name: str):
    """按路径加载 scripts/ 下的脚本（不执行 main）。"""
    p = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_scr_{name}", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ───────────── ① F355：确认机制对"非因子维度"的硬依赖 ─────────────

def test_confirmation_needs_two_nonzero_dims():
    """空 funding/FGI ⇒ flow=sent=0 ⇒ 只剩 tech 一个非零维度 ⇒ 永远 HOLD。

    `confirmation_min_dims` 默认 2（`strategy_params_registry.DEFAULT_PIPELINE_PARAMS`）。
    这就是"没传 funding/FGI 时因子无论怎么变都不影响结果"的机制根因。
    """
    assert replay_confirmation(1, 0, 0, 2) == ("HOLD", 0, "none")
    assert replay_confirmation(-1, 0, 0, 2) == ("HOLD", 0, "none")
    # 补上第二个同向维度后确认才可能通过
    assert replay_confirmation(1, 1, 0, 2) == ("BUY", 1, "normal")
    assert replay_confirmation(-1, -1, 0, 2) == ("SELL", -1, "normal")
    # 两个维度冲突 → 仍 HOLD
    assert replay_confirmation(1, -1, 0, 2) == ("HOLD", 0, "none")


def test_rule_decision_ignores_confirm_dir_when_hold():
    """确认为 HOLD 时，方向**完全**由 mid_bias/mid_conf 决定 —— 与因子无关。"""
    assert replay_rule_decision("HOLD", 0, "bullish", 0.5) == "buy"
    assert replay_rule_decision("HOLD", 0, "bearish", 0.5) == "sell"
    assert replay_rule_decision("HOLD", 0, "bullish", 0.1) == "hold"
    # 确认通过时用确认结果，confirm_dir 才起作用
    assert replay_rule_decision("BUY", 1, "bearish", 0.5) == "buy"
    assert replay_rule_decision("SELL", -1, "bullish", 0.5) == "sell"


def test_f357_entry_gate_precedes_factor_computation():
    """F357：源码顺序证明"是否开仓"先于因子计算 —— 因子不可能影响开仓与否。"""
    src = (ROOT / "backend" / "services"
           / "live_pipeline_backtest_engine.py").read_text(encoding="utf-8")
    body = src.split("def _pipeline_signal")[1].split("def _compute_factor_direction")[0]
    i_gate = body.index('if action != "enter"')
    i_factor = body.index("factor_dir = self._compute_factor_direction")
    assert i_gate < i_factor, "开仓闸必须在因子计算之前（现状如此，改动需重估 F357）"


# ───────────── ② F354：方向缓存的键不含因子集合 ─────────────

def test_f354_dir_cache_key_has_no_factor_set_component(monkeypatch):
    """缓存键 = (sym, tf, ts0, n) + 模式标签；换因子集合**不会**换缓存。

    ⇒ ① 缓存命中时"剔除因子重跑"会复用基线序列（假零）；
      ② 受治理集变化后（晋升/退役）方向序列最长 7 天不刷新（磁盘 TTL）。
    本用例把键的构成钉住：一旦有人把因子集合纳入键，这里会失败并提醒同步文档/脚本。
    """
    from backend.services import live_pipeline_backtest_engine as E
    monkeypatch.setenv("FACTOR_LIVE_ALLOWLIST_ONLY", "true")
    assert E._factor_dir_mode_tag() == "gov"
    monkeypatch.setenv("FACTOR_LIVE_ALLOWLIST_ONLY", "false")
    assert E._factor_dir_mode_tag() == "full"
    # 同一 (sym, tf) 在不同 allowlist 内容下路径相同 —— 函数根本不接受集合参数
    monkeypatch.setenv("FACTOR_LIVE_ALLOWLIST_ONLY", "true")
    p1 = E._factor_dir_disk_path("BTC", "4h")
    p2 = E._factor_dir_disk_path("BTC", "4h")
    assert p1 == p2 and "_gov" in p1.name
    # F359 之后名字里多了**语义版本**，但仍然没有"因子集合"这一维：
    # 候选因子 id 一个都不出现在文件名里。
    assert E._FACTOR_DIR_SERIES_VERSION in p1.name
    for fid in ("evo_180ee6eedd1fe9cc", "5a0c7a226b5446a4", "s5m_fa81777d"):
        assert fid not in p1.name
    assert E._FACTOR_DIR_DISK_TTL >= 24 * 3600, "磁盘 TTL 是『受治理集变化后方向过期』的上界"


def test_counterfactual_script_bypasses_cache_and_guards_empty_series():
    """脚本必须①关磁盘缓存+清内存缓存 ②空 funding/FGI 时拒绝出结论（F354/F355）。"""
    src = (ROOT / "scripts" / "run_backtest_counterfactual.py").read_text(encoding="utf-8")
    assert 'PIPELINE_FACTOR_DIR_DISK_ENABLED"] = "0"' in src
    assert "_FACTOR_DIR_CACHE.clear()" in src
    assert "两者皆空" in src and "拒绝出结论" in src
    assert "_load_funding_rates" in src and "_load_fgi_series" in src


# ───────────── ③ 增量表的算法口径 ─────────────

def test_incremental_table_defines_increment_as_base_minus_without():
    mod = _load_script("run_backtest_counterfactual")
    base = {"n_trades": 10, "sum_pnl": 100.0, "total_return": 0.1, "win_rate": 0.5}
    runs = {
        "good": {"n_trades": 10, "sum_pnl": 60.0, "total_return": 0.06, "win_rate": 0.4},
        "bad": {"n_trades": 11, "sum_pnl": 140.0, "total_return": 0.14, "win_rate": 0.6},
        "flat": {"n_trades": 10, "sum_pnl": 100.0, "total_return": 0.1, "win_rate": 0.5},
    }
    rows = {r["factor"]: r for r in mod.incremental_table(base, runs)}
    assert rows["good"]["d_sum_pnl"] == pytest.approx(40.0), "正增量=该因子有正贡献"
    assert rows["bad"]["d_sum_pnl"] == pytest.approx(-40.0), "负增量=剔掉它反而更好"
    assert rows["flat"]["d_sum_pnl"] == 0.0
    assert rows["bad"]["d_trades"] == -1


def test_incremental_table_sorted_desc_and_tolerates_failure():
    mod = _load_script("run_backtest_counterfactual")
    base = {"n_trades": 5, "sum_pnl": 50.0, "total_return": 0.05, "win_rate": 0.5}
    runs = {
        "a": {"n_trades": 5, "sum_pnl": 30.0, "total_return": 0.03, "win_rate": 0.5},
        "b": {"n_trades": 5, "sum_pnl": 60.0, "total_return": 0.06, "win_rate": 0.5},
        "c": None,
    }
    rows = mod.incremental_table(base, runs)
    assert [r["factor"] for r in rows if r.get("available")] == ["a", "b"]
    assert any(r["factor"] == "c" and r["available"] is False for r in rows)


def test_alias_grouping_collapses_two_names_of_same_factor():
    """F357 附注：同一底层因子有两个名字 ⇒ 只剔一个会低估；脚本按尾段分组补跑。"""
    src = (ROOT / "scripts" / "run_backtest_counterfactual.py").read_text(encoding="utf-8")
    assert "evo_s5m_" in src and "dup_groups" in src and "_group_key" in src


# ───────────── ④ F358：把"结构性失效"从静默变成可判定 + 可告警 ─────────────

def test_factor_dimension_inert_predicate():
    """判据：非因子维度上限 = tech(1) + funding(有?) + fgi(有?) < min_dims ⇒ 因子无效。"""
    from backend.services.live_pipeline_backtest_engine import factor_dimension_inert

    p2 = {"factor_signal_weight": 0.3, "confirmation_min_dims": 2}
    assert factor_dimension_inert(p2, {}, {}) is True, "两条序列都缺 ⇒ 因子结构性失效"
    assert factor_dimension_inert(p2, {"1": 0.001}, {}) is False, "有 funding ⇒ 可凑 2 维"
    assert factor_dimension_inert(p2, {}, {"1": 50.0}) is False, "有 fgi ⇒ 可凑 2 维"
    # 因子通道本来就关着时不算"失效"（是刻意的）
    assert factor_dimension_inert({"factor_signal_weight": 0.0,
                                   "confirmation_min_dims": 2}, {}, {}) is False
    # min_dims=3 时只有 funding 仍不够（sent 由 fgi 决定，缺失即恒 0）
    p3 = {"factor_signal_weight": 0.3, "confirmation_min_dims": 3}
    assert factor_dimension_inert(p3, {"1": 0.001}, {}) is True
    assert factor_dimension_inert(p3, {"1": 0.001}, {"1": 50.0}) is False


def test_factor_inert_warning_is_logged_once(caplog):
    import logging
    from backend.services import live_pipeline_backtest_engine as E
    E._FACTOR_INERT_WARNED.clear()
    with caplog.at_level(logging.WARNING):
        E._warn_factor_inert_once("BTC", "4h", "mid")
        E._warn_factor_inert_once("BTC", "4h", "mid")   # 同键第二次不再刷屏
        E._warn_factor_inert_once("ETH", "4h", "mid")
    msgs = [r.message for r in caplog.records if "结构性失效" in r.message]
    assert len(msgs) == 2, msgs


def test_run_marks_dimensions_and_warns_when_inert():
    """源码级：run() 必须把维度明细带出结果，并在结构性失效时告警（F358）。"""
    src = (ROOT / "backend" / "services"
           / "live_pipeline_backtest_engine.py").read_text(encoding="utf-8")
    assert "result.data_dims_used = dict(data_dims_used)" in src
    assert "factor_dimension_inert(p, funding_rates, fgi_map)" in src
    assert "_warn_factor_inert_once(symbol, timeframe, tier)" in src


def test_backtest_result_exposes_data_dims():
    from backend.services.backtest_evolution_engine import BacktestResult
    r = BacktestResult(run_id="t")
    assert r.data_dims_used == {}
    r.data_dims_used = {"factor_signal": True, "funding": False}
    assert r.data_dims_used["factor_signal"] is True


# ───────────── ⑤ F359：方向缓存必须有**语义版本**，否则改了实现还在吃旧序列 ─────────────

def test_f359_cache_path_carries_semantic_version(monkeypatch):
    """版本进入文件名 ⇒ 语义变更后旧缓存天然不可命中（不会静默复用）。"""
    from backend.services import live_pipeline_backtest_engine as E
    monkeypatch.setenv("FACTOR_LIVE_ALLOWLIST_ONLY", "true")
    p = E._factor_dir_disk_path("BTC", "4h")
    assert E._FACTOR_DIR_SERIES_VERSION in p.name, p.name
    assert p.name == f"BTC_4h_gov_{E._FACTOR_DIR_SERIES_VERSION}.json"


def test_f359_old_cache_file_is_ignored(tmp_path, monkeypatch):
    """**内容**校验：旧文件（无 ver 字段）即便文件名匹配也必须被拒（返回 None）。"""
    from backend.services import live_pipeline_backtest_engine as E
    monkeypatch.setattr(E, "_FACTOR_DIR_DISK_DIR", tmp_path, raising=False)
    monkeypatch.setattr(E, "_FACTOR_DIR_DISK_ENABLED", True, raising=False)
    monkeypatch.setenv("FACTOR_LIVE_ALLOWLIST_ONLY", "true")
    p = E._factor_dir_disk_path("BTC", "4h")
    p.write_text(json.dumps({"tss": [1, 2], "series": [0, 1], "updated": 0}),
                 encoding="utf-8")
    assert E._factor_dir_disk_load("BTC", "4h") is None, "无 ver 的旧缓存必须作废"
    # 写入正确版本后即可命中
    p.write_text(json.dumps({"ver": E._FACTOR_DIR_SERIES_VERSION,
                             "tss": [1, 2], "series": [0, 1], "updated": 0}),
                 encoding="utf-8")
    got = E._factor_dir_disk_load("BTC", "4h")
    assert got == ([1, 2], [0, 1])


def test_f359_version_mismatch_is_logged_once(tmp_path, monkeypatch, caplog):
    import logging
    from backend.services import live_pipeline_backtest_engine as E
    monkeypatch.setattr(E, "_FACTOR_DIR_DISK_DIR", tmp_path, raising=False)
    monkeypatch.setattr(E, "_FACTOR_DIR_DISK_ENABLED", True, raising=False)
    E._DIR_CACHE_VER_WARNED.clear()
    p = E._factor_dir_disk_path("ETH", "4h")
    p.write_text(json.dumps({"ver": "v1-old", "tss": [1], "series": [0], "updated": 0}),
                 encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert E._factor_dir_disk_load("ETH", "4h") is None
        assert E._factor_dir_disk_load("ETH", "4h") is None
    msgs = [r.message for r in caplog.records if "语义版本不匹配" in r.message]
    assert len(msgs) == 1, "同一文件只告警一次"


def test_f359_version_constant_is_documented_as_bump_required():
    """版本常量旁必须写明"改语义就 +1"，否则下一个人会把缓存当永久正确。"""
    src = (ROOT / "backend" / "services"
           / "live_pipeline_backtest_engine.py").read_text(encoding="utf-8")
    i = src.index("_FACTOR_DIR_SERIES_VERSION = ")
    ctx = src[max(0, i - 2000):i]
    assert "语义版本" in ctx and "必须 +1" in ctx


# ───────── F386（2026-09-18）回测→学习 这条链路的两个缺口 ─────────
# 动机：目标第 3 项要求"构建与学习系统关联的回测系统"。我原以为关联已通，
# 逐跳取证后发现：**写入侧在生产默认关、消费侧（主脑）不带时间**。
# 下面两条把"当下就是这样"钉住 —— 修好后会失败，提醒同步报告 §41 与待办。

def test_f386_production_engine_writes_sidecar_only_when_flag_on():
    """生产引擎写边车受 `BACKTEST_FACTOR_ATTR` 门控，**默认关**，且是**导入时**求值。

    证据链（只读、不连库）：
    - 常量默认值 `"0"` ⇒ 未配置时不写；
    - `.env` 里**没有**这个键 ⇒ 当前生产就是"不写"；
    - 唯一打开它的是**手工脚本** `scripts/run_backtest_factor_attr.py` 的
      `os.environ.setdefault("BACKTEST_FACTOR_ATTR", "1")` ⇒ 边车里的记录只可能来自手工运行。
    """
    from backend.services import live_pipeline_backtest_engine as E
    assert E._FACTOR_ATTR_ENABLED is False, (
        "测试进程内未设该 env ⇒ 必须为 False（若为 True，说明有人把它默认成开，需复核 §41）")
    src = (ROOT / "backend" / "services"
           / "live_pipeline_backtest_engine.py").read_text(encoding="utf-8")
    i = src.index("_FACTOR_ATTR_ENABLED = ")
    line = src[i:i + 120].split("\n")[0]
    assert '"0"' in line, f"默认值必须显式写 0：{line}"
    assert "os.getenv" in line, "必须是 env 门控（不得写死开）"
    # 手工脚本是当前唯一的开启方
    script = (ROOT / "scripts" / "run_backtest_factor_attr.py").read_text(encoding="utf-8")
    assert 'os.environ.setdefault("BACKTEST_FACTOR_ATTR", "1")' in script
    # .env 未设置该键（否则"生产不写"的结论要改）
    env_txt = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
    assert not [ln for ln in env_txt.splitlines()
                if ln.strip().startswith("BACKTEST_FACTOR_ATTR=")], (
        ".env 已设置 BACKTEST_FACTOR_ATTR ⇒ 生产会写边车，请更新报告 §41")


def test_f386_brain_render_omits_sidecar_timestamp():
    """主脑侧渲染**没有**回测记录的时间；而周环侧**有** ⇒ 同一数据两条路径可判定性不一致。

    上游一直有 `ts`（`persist_factor_attr` 写入 `"ts": time.time()`），
    两处消费：`weekly_loop._backtest_factor_attr_block`（**透传 `ts`**）、
    `learning_readback.format_for_prompt`（**不渲染任何时间**）。
    属"透传缺失"族（同 F379/§35）；修好（补一行时间/年龄）时本用例失败。
    """
    lr = (ROOT / "backend" / "services" / "learning_readback.py").read_text(encoding="utf-8")
    # 定位 ba 渲染段
    i = lr.index("### 🔬 回测侧因子战绩")
    seg = lr[i:i + 1800]
    for bad in ('cov.get("ts")', "cov.get('ts')", 'inc.get("ts")', "inc.get('ts')",
                "跑于", "小时前", "age_h"):
        assert bad not in seg, f"主脑渲染已带上时间（{bad}）⇒ 请更新报告 §41 与待办 B21"
    # 但上游确实写了 ts，且周环确实透传了 —— 证明这是"没渲染"而不是"没有"
    eng = (ROOT / "backend" / "services"
           / "live_pipeline_backtest_engine.py").read_text(encoding="utf-8")
    assert '"ts": _time.time()' in eng, "persist_factor_attr 应写入 ts"
    wl = (ROOT / "backend" / "services" / "unified_strategy"
          / "weekly_loop.py").read_text(encoding="utf-8")
    assert '"ts": last.get("ts")' in wl, "周环侧应透传 ts（两条路径不一致是本条的要点）"
    # 且 learning_readback 的 coverage 块确实没有时间字段
    j = lr.index("def backtest_attr_snapshot")
    snap_seg = lr[j:j + 2600]
    assert '"ts"' not in snap_seg, "coverage/incremental 快照不应含 ts（钉住缺口）"
