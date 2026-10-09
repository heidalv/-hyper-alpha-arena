# -*- coding: utf-8 -*-
"""[F382 2026-09-18] 「相似情景检索」链路三处缺陷：**不筛结局 / 产出饥饿且静默 / 归属粗糙**。

## 事实链（全部可复现）

**运行态实测**（`_similar_episodes_feed("BTC","mid",…)`）：

```
ranging/low波动 neutral → 未开仓；ranging/low波动 short → 未开仓；ranging/low波动 neutral → 未开仓；
ranging/low波动 neutral → 未开仓；ranging/low波动 short → 未开仓
```
n=5，**全部 `opened=false`、`outcome_pct=null`**；135 字符里 5 条几乎完全相同。
而主脑饲料自己的说明写着：*"相似市场指纹的历史情景**及其结局**。做对了参考，做错了别再摔。"*
——**承诺了结局，交付的是 5 个「未开仓」**。

**缺陷 1：检索不筛「有结局」**
`episodic_memory.retrieve_similar():234` docstring 写"检索与当前指纹相似的历史情景（**带结局**）"，
但查询（`:241-250`）只有 `symbol`/`tier` 过滤 + `order_by(created_at).limit(200)`
——**没有 `opened==1`、没有 `outcome_pct is not null`**。
（同模块 `consolidate_daily():333` **有**这两个过滤 ⇒ 不是不会写，是漏了。）

**缺陷 2：产出侧严重饥饿 + 失配静默**
全表 BTC/mid 情景 **260** 条，其中**只有 6 条**带结局（**2.3%**）；
最新 200 条（检索池）里有结局的也仅 **6 条（3.0%）** ⇒ 无论怎么排序，top-5 几乎必然是"未开仓"。
唯一写入方 `backfill_outcome()` 只有**一个调用点**（`paper_trading_engine.py:2136`），
它取 **该 paper 账户最新的 `FullAutoSession.session_id`**（`:2137-2143`）去精确匹配
`MltoEpisode.session_id`，并要求 `tier` 字符串一致 ⇒ **会话轮转 / tier 标签不一致时匹配不到**，
而失配路径是 `return False`（**静默**，无日志）⇒ **结局永久丢失且无人知道**。

**缺陷 3：归属粗糙**
`backfill_outcome` 的匹配是"该 (session,symbol,tier) 下 `outcome_ts IS NULL` 的**最新一条**"
⇒ 回填到的可能**不是**开出这笔仓的那条决策情景 ⇒ "相似情景 → 结局"的因果链本身不可靠。

## 结论

这是本报告主题（"写了/接了，但没真正生效；失败了没人知道"）在**情景记忆**这一环的又一实例：
机制在、检索在、渲染在，但**它检索的历史里几乎没有"结局"**，而缺结局这件事**完全静默**。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto import episodic_memory as EM  # noqa: E402
from backend.services.mlto import brain as B  # noqa: E402


def test_retrieve_similar_does_not_filter_for_outcomes():
    """缺陷 1：检索未按"有结局"过滤（修好时本用例失败）。"""
    src = inspect.getsource(EM.retrieve_similar)
    q = src[src.find("db.query(MltoEpisode)"):src.find("now = _utcnow()")]
    assert "MltoEpisode.symbol" in q and "MltoEpisode.tier" in q
    for kw in ("opened", "outcome_pct", "outcome_ts"):
        assert kw not in q, f"查询已加入 {kw} 过滤 ⇒ 缺陷 1 可能已修，请更新报告 §37"


def test_consolidate_does_filter_so_it_is_an_omission_not_incapacity():
    """反证：同模块的 consolidate_daily **确实**会筛结局 ⇒ 属漏写而非不会写。"""
    src = inspect.getsource(EM.consolidate_daily)
    assert "MltoEpisode.opened == 1" in src
    assert "outcome_pct.isnot(None)" in src


def test_format_renders_uninformative_placeholder_for_non_opened():
    """缺陷 1 的可见后果：未开仓情景渲染成「未开仓」（无结局信息）。"""
    txt = EM.format_similar_for_feed([
        {"opened": False, "outcome_pct": None, "regime": "ranging",
         "vol_bucket": "low", "direction": "neutral"},
    ])
    assert "未开仓" in txt


def test_feed_promise_vs_content():
    """主脑饲料的说明承诺"及其结局"，而实际内容可能全是「未开仓」。"""
    src = inspect.getsource(B.build_feed)
    assert "历史情景及其结局" in src, "说明文案变了 ⇒ 复核 §37"


def test_backfill_failure_is_now_logged_and_single_caller():
    """缺陷 2 的**修复后**形态（F384，2026-09-18 已修，纯日志）：

    - 匹配不到那條情景（会话轮转/tier 不一致时的必然结果）⇒ 现在记 **warning** 并带上下文；
    - 异常路径由 `debug` 升为 `warning`（生产 INFO 级别下 debug 等于不可见）；
    - **只加日志，未改任何行为**（仍返回 False、仍取最新空结局情景）。
    """
    src = inspect.getsource(EM.backfill_outcome)
    seg = src[src.find("if row is None"):src.find("row.opened = 1")]
    assert "return False" in seg, "失败仍应返回 False（行为未变）"
    assert "logger.warning" in seg, (
        "匹配不到仍未记 warning ⇒ 静默退化回归（报告 §37/§39）")
    assert "结局丢失" in seg, "warning 文案应说明后果（结局丢失）"
    tail = src[src.rfind("except"):]
    assert "logger.warning" in tail, "异常路径应升为 warning"
    assert "logger.debug" not in tail, "异常路径不应仍停留在 debug"
    # 调用点仍只有一个
    # [2026-09-18 修] 原实现直接 `(ROOT/"backend").rglob("*.py")` —— 而 `backend/.venv`
    # 也在其下 ⇒ **命中 24,253 个文件、全部读取耗时 ≈100 秒**，与 pytest 的 120s 单测超时
    # 擦边（机器一忙就翻成 Timeout，表现为"随机红"）。断言本意是"**源码**里只有一个调用点"，
    # 扫虚拟环境既无意义又脆弱 ⇒ 排除非源码目录。
    _SKIP_DIRS = {".venv", "venv", "site-packages", "node_modules", "__pycache__", ".git"}
    hits = []
    for f in (ROOT / "backend").rglob("*.py"):
        if any(part in _SKIP_DIRS for part in f.parts):
            continue
        try:
            t = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "backfill_outcome(" in t and "def backfill_outcome" not in t:
            hits.append(f.relative_to(ROOT).as_posix())
    assert len(hits) == 1, f"调用点变为 {hits} ⇒ 请更新 §37"


def test_backfill_no_longer_silent_behaviorally(monkeypatch, caplog):
    """行为级：匹配不到时必须有 WARNING 落日志。"""
    import logging
    from backend.database.connection import AnalyticsSessionLocal
    try:
        with AnalyticsSessionLocal() as db:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception:
        pytest.skip("无 DB 会话")
    with caplog.at_level(logging.WARNING):
        ok = EM.backfill_outcome(session_id="nonexistent_session_for_test",
                                 symbol="ZZZTEST", tier="mid",
                                 pnl=1.0, pct=0.1, hold_hours=1.0, close_reason="test")
    assert ok is False
    assert any("结局回填" in r.message and "未匹配" in r.message for r in caplog.records), \
        "未产生『未匹配』warning ⇒ 静默退化"


def test_macro_empty_return_is_logged():
    """F384 第二处：macro 日历抽取在无 LLM 配置时由裸 `return []` 改为带 warning。"""
    src = (ROOT / "backend/services/macro_data_collector.py").read_text(
        encoding="utf-8", errors="replace")
    i = src.find("def _llm_extract_calendar")
    assert i > 0
    seg = src[i:i + 1600]
    assert "return []" in seg
    assert "logger.warning" in seg and "无事件" in seg, (
        "空返回仍未与非空区分（『无事件』vs『未抽取』）⇒ 请更新 §39")


def test_backfill_attaches_to_newest_blank_episode():
    """缺陷 3：匹配口径是"最新一条 outcome_ts 为空的" ⇒ 不保证是开仓那条决策。"""
    src = inspect.getsource(EM.backfill_outcome)
    assert "outcome_ts.is_(None)" in src
    assert "order_by(MltoEpisode.created_at.desc())" in src
    assert ".first()" in src


def test_runtime_outcome_starvation_is_material():
    """缺陷 2 的量化：BTC/mid 带结局情景占比（无 DB 时跳过）。"""
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.services.mlto.db_models import MltoEpisode
        with AnalyticsSessionLocal() as db:
            tot = db.query(MltoEpisode).filter(
                MltoEpisode.symbol == "BTC", MltoEpisode.tier == "mid").count()
            with_out = db.query(MltoEpisode).filter(
                MltoEpisode.symbol == "BTC", MltoEpisode.tier == "mid",
                MltoEpisode.opened == 1, MltoEpisode.outcome_pct.isnot(None)).count()
    except Exception:
        pytest.skip("无 DB 会话")
    if tot == 0:
        pytest.skip("无情景数据")
    share = with_out / tot
    print(f"[F382] BTC/mid 情景={tot} 带结局={with_out} 占比={share:.1%}")
    assert share < 0.5, (
        f"带结局占比升到 {share:.1%} ⇒ 产出侧可能已修复，请更新报告 §37 与待办 B20")


# ───────────── §38 载荷总账（F383）：不超预算 + 缺陷饲料占比可复跑 ─────────────

#: §32–§37 已证有缺陷的 extras 键（见报告 §38.3）
DEFECTIVE_KEYS = ("similar_episodes", "recent_pnl_14d", "backtest_wisdom",
                  "recent_same_dir_pnl_14d", "consolidated_lessons",
                  # F387（2026-09-18 追加）：`reflexion_memory` 的 `exit` 字段结构性恒空
                  # —— 丢掉"为什么平仓"，而库里 10/10 条 close_reason 都有值。
                  "reflexion_memory")


def test_extras_payload_within_budget():
    """主脑 extras 必须显著低于预算（§38.1 实测 15.3%）；超 50% 即需重新评估。"""
    import json
    import backend.services.mlto.brain as B
    sid = "probe"
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        db.execute(text("SET app.is_admin='on'"))
        r = db.execute(text("SELECT session_id FROM full_auto_sessions "
                            "ORDER BY id DESC LIMIT 1")).fetchone()
        db.close()
        if r:
            sid = str(r[0])
    except Exception:
        pytest.skip("无 DB 会话，无法构建真实 feed")
    try:
        feed = B.build_feed(symbol="BTC", tier="mid", session_id=sid, market_summary=None)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"build_feed 不可用: {str(exc)[:80]}")
    extra = feed.get("extras") or {}
    size = len(json.dumps(extra, ensure_ascii=False, default=str))
    budget = B._BRAIN_EXTRAS_CHAR_BUDGET
    print(f"[F383] extras={size} 预算={budget} 占用={size / budget:.1%} 键数={len(extra)}")
    assert size < budget, "**extras 超预算 ⇒ 正在静默截断**（见报告 §38.1）"
    assert size / budget < 0.5, (
        f"载荷占用升到 {size / budget:.1%} ⇒ 逼近截断，请复核 §38")


def test_defective_feed_share_is_documented():
    """记录事实：已证有缺陷的饲料占载荷比例（修好任一项后本用例会失败，提醒更新 §38.3）。"""
    import json
    import backend.services.mlto.brain as B
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        db.execute(text("SET app.is_admin='on'"))
        r = db.execute(text("SELECT session_id FROM full_auto_sessions "
                            "ORDER BY id DESC LIMIT 1")).fetchone()
        db.close()
        sid = str(r[0]) if r else "probe"
        feed = B.build_feed(symbol="BTC", tier="mid", session_id=sid, market_summary=None)
    except Exception:
        pytest.skip("无 DB 会话 / build_feed 不可用")
    extra = feed.get("extras") or {}
    total = len(json.dumps(extra, ensure_ascii=False, default=str))
    defect = sum(len(json.dumps(extra.get(k), ensure_ascii=False, default=str))
                 for k in DEFECTIVE_KEYS if k in extra)
    share = defect / max(total, 1)
    print(f"[F383/F387] 缺陷饲料占载荷 {defect}/{total} = {share:.1%}")
    assert share < 0.60, (
        f"缺陷饲料占比升到 {share:.1%}。历史读数：先 43.9%/42.8%（5 键口径），"
        f"加入 F387 的 reflexion_memory 后为 **53.4%(BTC)/55.1%(ETH)**（6 键口径）。"
        f"超过 60% 说明又发现了新的缺陷饲料或缺陷扩大 ⇒ 请复核报告 §38.3")


# ───────── §35 家族补一例：最大载荷项 chart_review 的 accepted=false 无口径说明 ─────────

def test_chart_review_accepted_flag_has_no_explanation():
    """`chart_review`（**载荷最大项，约 2716 字符 = 30%**）透出 `accepted` 字段，
    而 `chart_role` 只解释了 `source=none`，**没有解释 `accepted=false` 的含义**
    （它其实是"图审未过其自身共识门"，见 `_SOFT_MISS_MARKERS`）。

    低严重度：字段名尚可自解释，但内容详尽且语气肯定，LLM 可能忽略这一个布尔。
    修好（补一句口径说明）时本用例会失败 —— 请同步报告 §35 与待办 B18。
    """
    import inspect
    import backend.services.mlto.brain as B
    src = inspect.getsource(B.build_feed)
    assert '"chart_review"' in src and '"chart_role"' in src
    role = ""
    for ln in src.splitlines():
        if '"chart_role"' in ln:
            role = ln
    assert role, "未找到 chart_role 文案"
    assert "accepted" not in role, (
        "chart_role 已解释 accepted ⇒ 请更新报告 §35（该项已缓解）")
    assert "source=none" in role, "chart_role 文案变了 ⇒ 复核 §35"


def test_chart_feed_passes_accepted_through():
    """`_chart_feed` 原样透传上游文件的 `accepted`/`consensus_score`（不是自己算的门）。"""
    import inspect
    import backend.services.mlto.brain as B
    src = inspect.getsource(B._chart_feed)
    assert 'latest.get("accepted")' in src
    assert 'latest.get("consensus_score")' in src
    # 且 accepted=false 在软证据标记里（说明它不阻止开仓）
    assert "accepted=false" in "\n".join(B._SOFT_MISS_MARKERS)
