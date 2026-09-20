# -*- coding: utf-8 -*-
"""[轮130 2026-09-20] 牛熊对抗辩论接线：闸门 / 成本闸 / 有界生效 / 真落库 / 接线不许被悄悄摘掉。

## 背景（用户架构）
「五分析师 → 牛熊研究员对抗辩论 → 风控官（有否决权）→ 交易员」
实测（轮129）：`mlto/debate_layer.py` 完整存在，但**唯一调用者是生产 0 调用点的 orchestrator**
（旧 MLTO 主脑 09-05 下线）⇒ `alpha_analytics.mlto_debate_log` **0 行**。本轮接到活主脑。

本文件钉住四件事：
  1. **灰区才跑**（`should_debate`：mid 0.40~0.70 / long 0.45~0.75）+ 冷却 + 小时上限；
  2. 裁决**有界生效**（reject×0.6 / reduce×0.85，下限 10），且 `MIDLONG_DEBATE_APPLY=false` 时完全不改；
  3. 真的写进 `mlto_debate_log`（bull/bear 两行）——"接线了"必须能在库里看到；
  4. `brain.py` 里**必须仍有**这段接线（防止下一轮有人重构时把它悄悄摘掉，重演"设计了没做"）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto import brain_debate as BD  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """每例清空冷却/计数，避免相互污染。"""
    monkeypatch.setattr(BD, "_LAST_BY_SYMBOL", {})
    monkeypatch.setattr(BD, "_HOURLY", [])
    monkeypatch.setattr(BD, "_STATS", {k: 0 for k in BD._STATS})
    for k in ("MIDLONG_DEBATE_ENABLED", "MIDLONG_DEBATE_LLM", "MIDLONG_DEBATE_APPLY",
              "MIDLONG_DEBATE_HOURLY_CAP", "MIDLONG_DEBATE_COOLDOWN_S", "MIDLONG_DEBATE_MAX_ROUNDS"):
        monkeypatch.delenv(k, raising=False)
    yield


def _run(**kw):
    args = dict(symbol="BTC", tier="mid", conviction=55.0, direction="long",
                pack=None, extras={}, regime="up", thesis_id="t-test", session_id="s-test")
    args.update(kw)
    return BD.run_debate_for_thesis(**args)


# ───────────────── 1. 闸门 ─────────────────

def test_gray_zone_required(monkeypatch):
    monkeypatch.setenv("MIDLONG_DEBATE_LLM", "false")
    assert _run(conviction=20.0) is None, "低 conviction 不该辩论（区间外）"
    assert _run(conviction=90.0) is None, "高 conviction 不该辩论（区间外）"
    assert _run(conviction=55.0) is not None, "灰区必须辩论"


def test_disabled_switch_blocks_everything(monkeypatch):
    monkeypatch.setenv("MIDLONG_DEBATE_ENABLED", "false")
    assert _run(conviction=55.0) is None
    assert BD.enabled() is False


def test_cooldown_blocks_second_run_same_symbol(monkeypatch):
    monkeypatch.setenv("MIDLONG_DEBATE_LLM", "false")
    monkeypatch.setenv("MIDLONG_DEBATE_COOLDOWN_S", "600")
    assert _run(conviction=55.0) is not None
    assert _run(conviction=56.0) is None, "同标的冷却期内不得重复辩论（控成本）"
    assert BD._STATS["skipped_cooldown"] >= 1


def test_hourly_cap_blocks_across_symbols(monkeypatch):
    monkeypatch.setenv("MIDLONG_DEBATE_LLM", "false")
    monkeypatch.setenv("MIDLONG_DEBATE_HOURLY_CAP", "1")
    assert _run(symbol="BTC", conviction=55.0) is not None
    assert _run(symbol="ETH", conviction=55.0) is None, "全局小时上限必须拦住别的标的"
    assert BD._STATS["skipped_cap"] >= 1


# ───────────────── 2. 有界生效 ─────────────────

def test_conviction_effect_is_bounded_and_switchable(monkeypatch):
    monkeypatch.setenv("MIDLONG_DEBATE_APPLY", "true")
    c, note = BD.apply_conviction_effect(50.0, {"verdict": "reject"})
    assert c == 30.0 and "reject" in note
    c2, _ = BD.apply_conviction_effect(12.0, {"verdict": "reject"})
    assert c2 == 10.0, "下限 10：辩论不得把 conviction 打到 0（避免事实上的永久静默）"
    c3, _ = BD.apply_conviction_effect(50.0, {"verdict": "reduce"})
    assert c3 == pytest.approx(42.5)
    c4, _ = BD.apply_conviction_effect(50.0, {"verdict": "proceed"})
    assert c4 == 50.0
    monkeypatch.setenv("MIDLONG_DEBATE_APPLY", "false")
    c5, note5 = BD.apply_conviction_effect(50.0, {"verdict": "reject"})
    assert c5 == 50.0 and note5 == "off", "APPLY=false 时必须只记录、不改数值"


# ───────────────── 3. 真的落库 ─────────────────

def test_debate_persists_two_rows_to_mlto_debate_log(monkeypatch):
    monkeypatch.setenv("MIDLONG_DEBATE_LLM", "false")
    from sqlalchemy import text

    from backend.database.connection import analytics_engine

    with analytics_engine.connect() as c:
        before = c.execute(text("select count(*) from mlto_debate_log")).scalar()
    res = _run(conviction=55.0)
    assert res is not None and res["verdict"] in ("proceed", "reduce", "reject")
    assert res["persist_rows"] == 2, "bull/bear 两行都必须落库"
    with analytics_engine.connect() as c:
        after = c.execute(text("select count(*) from mlto_debate_log")).scalar()
        latest = c.execute(text(
            "select side, content_json from mlto_debate_log order by id desc limit 2")).fetchall()
    assert after == before + 2, f"mlto_debate_log 行数未增加：{before}→{after}"
    sides = {r[0] for r in latest}
    assert sides == {"bull", "bear"}, f"落库的 side 不对：{sides}"
    assert "verdict" in (latest[0][1] or ""), "content_json 必须含裁决（可复盘）"


# ───────────────── 4. 接线不许被悄悄摘掉 ─────────────────

def test_brain_still_calls_the_debate_wiring():
    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name:
                calls.add(name)
    assert "run_debate_for_thesis" in calls, \
        "brain.py 里没有调用 run_debate_for_thesis —— 辩论接线被摘掉了（重演'设计了没做'）"
    assert "apply_conviction_effect" in calls, "brain.py 未消费辩论裁决"
    # 顺序：辩论必须在 accepted/recommend_open 定型之前（否则改了也没用）
    i_deb = src.find("run_debate_for_thesis(")
    i_acc = src.find("dto.accepted = new_accepted")
    assert 0 < i_deb < i_acc, "辩论接线位置必须在 accepted 定型之前"


def test_status_reports_gates_and_counters():
    st = BD.status()
    for k in ("enabled", "llm", "apply", "cooldown_s", "hourly_cap", "stats", "producer"):
        assert k in st, f"status() 缺 {k}"
    assert st["producer"].endswith("brain_debate.py")


def test_evidence_builder_uses_analyst_signals():
    """证据必须来自真实上下文层，**含六分析师信号**（否则辩论只是空谈）。"""
    class _Pack:
        layers = {
            "market": {"symbols": {"BTC": {"ret_24h_pct": 1.5, "ema_trend_1h": "bullish",
                                           "atr14_1h_pct": 2.0, "pos24_pct": 70, "rsi14_1h": 61,
                                           "regime": "up"}}},
            "analysts": {"symbols": {"BTC": {"flow": {"score": 0.1, "conf": 1.0},
                                             "technical": {"score": -0.4, "conf": 1.0}}},
                         "global": {"macro": {"score": -0.08, "conf": 0.35}}},
        }

    ev, ctx, by_hz = BD._evidence_from_pack(_Pack(), "BTC")
    joined = " ".join(ev)
    assert "六分析师信号" in joined, "辩论证据里必须带六分析师数值信号"
    assert "flow=0.1" in joined
    assert ctx.get("composite_score") == pytest.approx(-0.4)
    # [轮130 周期化] 三个周期都必须拿到证据（空手辩论 = 又一次"装饰"）
    for k in ("intraday", "swing", "trend"):
        assert by_hz.get(k), f"周期 {k} 没有证据可辩"


def test_each_horizon_gets_evidence_quota(monkeypatch):
    """证据必须**按周期配额**分配：日内条目再多也不能把长期趋势挤掉。

    实测（11:26 那次）：全局 `[:12]` 截断后，牛方在长期趋势上抱怨"缺乏距EMA200/宏观证据"
    （证据其实采到了）⇒ 长期那一档等于空手辩论。
    """
    class _Pack:
        layers = {
            "market": {"symbols": {"BTC": {"ret_24h_pct": -1.0, "ema_trend_1h": "bearish",
                                           "atr14_1h_pct": 0.4, "pos24_pct": 12.0, "rsi14_1h": 36.0,
                                           "regime": "up", "ema_trend_4h": "bullish",
                                           "ret_7d_pct": 5.0, "ret_30d_pct": 2.7, "rsi14_1d": 61.0,
                                           "rv30_annual_pct": 37.0, "above_ema200_1d": True,
                                           "dist_ema200_pct": 12.7, "atr14_1d_pct": 2.6}}},
            "flows": {"funding_8h_pct": {"BTC": {"binance": 0.01}},
                      "position_structure": {"BTC": {"oi_usd": 1.0, "global_ls": 0.93}},
                      "liquidations_24h": {"BTC": {"long_liq_usd": 1.0, "short_liq_usd": 2.0}},
                      "events": [{"sev": 2, "title": f"事件{i}"} for i in range(8)]},
            "analysts": {"symbols": {"BTC": {"flow": {"score": 0.1, "conf": 1.0},
                                             "technical": {"score": -0.39, "conf": 1.0}}},
                         "global": {"macro": {"score": -0.08, "conf": 0.35}}},
        }

    ev, ctx, by_hz = BD._evidence_from_pack(_Pack(), "BTC")
    assert by_hz["trend"], "长期趋势必须有证据（距EMA200/宏观）"
    assert by_hz["swing"], "中期必须有证据（4h结构/收益/因子）"


# ───────────────── 5. 周期维度（用户指令：牛熊分析必须明确周期） ─────────────────

def test_three_horizons_declared_with_scope():
    keys = [h[0] for h in BD.HORIZONS]
    assert keys == ["intraday", "swing", "trend"]
    assert BD.HORIZON_CN == {"intraday": "日内", "swing": "中期", "trend": "长期趋势"}
    for _, cn, scope in BD.HORIZONS:
        assert cn and scope, "每个周期必须写清中文名与证据范围"


def test_primary_horizon_per_tier():
    assert BD.primary_horizon("mid") == "intraday", "中线车道以日内为主周期"
    assert BD.primary_horizon("long") == "trend", "长线车道以长期趋势为主周期"


def test_llm_prompt_carries_horizon_contract(monkeypatch):
    """发给模型的 prompt 必须显式要求三个周期（否则模型只会给一个不分周期的结论）。"""
    instr = BD._horizon_instruction()
    for cn in ("日内", "中期", "长期趋势"):
        assert cn in instr, f"周期契约里缺 {cn}"
    assert "horizons" in instr and "confidence" in instr


def test_parse_horizons_handles_missing_gracefully():
    """模型不给 horizons ⇒ 三档标 unknown（不猜、也不崩）。"""
    hz = BD._parse_horizons('{"argument":"x","confidence":0.6}')
    for k in ("intraday", "swing", "trend"):
        assert hz[k]["stance"] == "unknown"
    hz2 = BD._parse_horizons(
        '{"argument":"x","horizons":{"intraday":{"stance":"short","confidence":0.8,'
        '"argument":"资金费拥挤"},"trend":{"stance":"long","confidence":0.7,"argument":"距EMA200正"}}}')
    assert hz2["intraday"]["stance"] == "short" and hz2["intraday"]["confidence"] == 0.8
    assert hz2["trend"]["stance"] == "long"
    assert hz2["swing"]["stance"] == "unknown", "未给出的周期必须显式 unknown"


def test_horizon_verdict_thresholds():
    assert BD._horizon_verdict(0.3, 0.9) == "proceed"
    assert BD._horizon_verdict(0.0, 0.9) == "reduce"
    assert BD._horizon_verdict(-0.5, 0.9) == "reject"
    assert BD._horizon_verdict(0.5, 0.2) == "reject", "风险共识过低时任何周期都该 reject"


def test_apply_effect_uses_primary_horizon_not_average(monkeypatch):
    """「日内 reject、长期 proceed」时：中线车道必须按日内生效，不能被平均掉。"""
    monkeypatch.setenv("MIDLONG_DEBATE_APPLY", "true")
    res = {"primary_horizon": "intraday", "primary_verdict": "reject", "verdict": "proceed",
           "horizon_verdicts": {"intraday": "reject", "swing": "proceed", "trend": "proceed"}}
    c, note = BD.apply_conviction_effect(50.0, res)
    assert c == 30.0 and "日内" in note and "reject" in note
    res2 = {"primary_horizon": "trend", "primary_verdict": "proceed", "verdict": "reject",
            "horizon_verdicts": {"intraday": "reject", "swing": "reject", "trend": "proceed"}}
    c2, note2 = BD.apply_conviction_effect(50.0, res2)
    assert c2 == 50.0, "长线车道按长期趋势裁决：长期 proceed 就不该被日内的 reject 拉低"
    assert "长期趋势" in note2


def test_single_sided_answer_yields_insufficient_not_a_verdict(monkeypatch):
    """一侧没按周期作答且文本抽不出立场 ⇒ 该周期 `insufficient`，**不产生裁决、不生效**。

    实测（11:25 那次）：熊方把三周期写进 argument 却没给结构化 horizons，
    若把它当 0.5 参与比较，就会得到「牛方信心 − 0.5」这种半盲裁决。
    """
    assert BD._horizon_verdict(0.0, 0.9) == "reduce"          # 两侧都有立场时才成立
    # [轮136] 必须显式开启生效，否则默认是"只记录"（APPLY=false）——那样测不到 insufficient 分支
    monkeypatch.setenv("MIDLONG_DEBATE_APPLY", "true")
    c, note = BD.apply_conviction_effect(50.0, {"primary_horizon": "intraday",
                                                "primary_verdict": "insufficient"})
    assert c == 50.0 and "不生效" in note


def test_derive_horizon_stance_from_argument_text():
    """兜底抽取：从总论点里按周期分句判定立场（实测熊方文本正是这种形态）。"""
    txt = ("三重周期均不支持做多：日内层面1h EMA趋势已转空且RSI仅36.5；"
           "中期4h虽EMA多头但regime=ranging；长期趋势regime=up但出现背离")
    d1 = BD._derive_horizon_stance(txt, "日内")
    d2 = BD._derive_horizon_stance(txt, "长期趋势")
    assert d1 and d1["stance"] == "short" and d1["derived"] is True
    assert d2 and d2["stance"] == "short"
    assert BD._derive_horizon_stance("与周期无关的一句话", "日内") is None, "抽不出必须返回 None（不猜）"
