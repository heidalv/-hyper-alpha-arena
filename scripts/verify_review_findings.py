# -*- coding: utf-8 -*-
"""[F361 2026-09-18] 复查结论**一键复验**（只读、离线、无需 DB/后端）。

为什么需要它：本次复查的结论分散在 ~10 个探针脚本与报告章节里，用户要"可验证证据"就得
逐个去跑。本脚本把**能在离线状态下判定**的结论全部收敛成一张 PASS/FAIL 表：

    python scripts/verify_review_findings.py           # 全部检查
    python scripts/verify_review_findings.py -v        # 附详细证据

每条检查都对应报告里的一个编号（F*/§），并给出**证据位置**（文件:行 或 运行时读数）。
需要 DB / 需要跑回测 / 需要时间积累的结论**不在此列**，脚本会明确列出"未覆盖项"，
避免让人误以为"全绿 = 整个复查都验证过了"。

退出码：0=全部通过；1=有检查失败（便于挂 CI 或人工复核）。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

RESULTS = []


def check(fid: str, title: str):
    def deco(fn):
        RESULTS.append((fid, title, fn))
        return fn
    return deco


def _src(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _code(rel: str) -> str:
    """源码**去掉注释**后再匹配。

    [F361 自纠] 首版直接用 `_src` 匹配，结果 3 条检查 FAIL 全是**假阳性**：
    我自己的修复注释里写着"原为 `from ... import common`"、"`trade_pnl_pct or 0` 把 NULL 当 0"，
    于是"检查文本"把"解释缺陷的注释"当成了"缺陷本身"。
    ⇒ 源码模式匹配必须排除注释；能用运行态断言的就不用文本匹配。
    （朴素实现：去掉行内 `#` 之后的内容；对本仓库这些待检模式足够，且失效方向是"更严"而非"更松"。）
    """
    out = []
    for ln in _src(rel).splitlines():
        s = ln.lstrip()
        if s.startswith("#"):
            continue
        i = ln.find("#")
        out.append(ln if i < 0 else ln[:i])
    return "\n".join(out)


def _ok(detail: str = "") -> tuple:
    return True, detail


def _env_value(key: str) -> Optional[str]:
    """读 `.env` 里**生效的** KEY=VALUE（忽略注释行与行内注释）。找不到返回 None。

    为什么要专门写：`MIDLONG_LOCATION_PAPER_SHRINK_CEILING` 这类键在 `.env` 里
    既有生效行、又有大段说明注释；用 `in` 做子串判断会把"注释里提到的值"当成配置值
    （本会话已多次栽在"解释缺陷的注释被当成缺陷本身"上）。
    """
    p = ROOT / ".env"
    if not p.exists():
        return None
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() != key:
            continue
        v = v.split("#", 1)[0].strip()          # 去掉行内注释
        return v.strip().strip('"').strip("'")
    return None


def _fail(detail: str) -> tuple:
    return False, detail


# ───────────────────── P0 / 接线类 ─────────────────────

@check("P0", "备用车道导入可用（prompt_context 非空且可导入）")
def _p0():
    import importlib
    m = importlib.import_module("backend.services.prompt_context")
    names = [n for n in ("PromptContextBuilder", "BuildInput") if hasattr(m, n)]
    if len(names) == 2:
        return _ok("prompt_context 导出 PromptContextBuilder/BuildInput")
    return _fail(f"仅导出 {names}")


@check("F320", "流B 证据目录**运行态**正确（_UNIFIED_DIR 存在且 _thesis_map 有行）")
def _f320():
    """用运行态断言而非源码模式：源码写法可以是 join/Path/parents，匹配文本必然脆。"""
    try:
        from backend.services.hybrid_scoring import evidence as E
    except Exception as exc:
        return _fail(f"导入失败: {str(exc)[:110]}")
    d = getattr(E, "_UNIFIED_DIR", None)
    if d is None:
        return _fail("无 _UNIFIED_DIR")
    if not Path(d).exists():
        return _fail(f"目录不存在: {d}")
    n_json = len(list(Path(d).glob("*.json")))
    rows = E._thesis_map()
    if rows:
        return _ok(f"{d} 存在，{n_json} 个 json，_thesis_map 返回 {len(rows)} 行")
    return _fail(f"目录存在({n_json} 个 json)但 _thesis_map 为空")


@check("F320b", "周报 backtest_factor_attr 块存在且无坏 import（只查代码，不查注释）")
def _f320b():
    code = _code("backend/services/unified_strategy/weekly_loop.py")
    if "backtest_factor_attr" not in code:
        return _fail("周报缺 backtest_factor_attr 块")
    if re.search(r"^\s*from\s+backend\.services\.hybrid_scoring\s+import\s+common", code, re.M):
        return _fail("仍存在坏 import hybrid_scoring.common")
    return _ok("block 存在；坏 import 仅出现在注释里")


@check("F322", "主控 factor_votes 键名对齐（接受 factor_contrib）")
def _f322():
    txt = _src("backend/services/full_auto/master_execution.py")
    return _ok("已接受 factor_contrib") if "factor_contrib" in txt else _fail("未见 factor_contrib")


@check("F326", "hybrid_scoring IC 采集包含 A1")
def _f326():
    txt = _src("backend/services/hybrid_scoring/service.py")
    if '"A1"' in txt or "'A1'" in txt:
        return _ok("IC 采集元组含 A1")
    return _fail("A1 未纳入 IC 采集")


# ───────────────────── 学习 ↔ 策略 ─────────────────────

@check("A5/F345", "学习读回路已接线且**默认关**（不改变今日行为）")
def _f345():
    txt = _src("backend/services/mlto/brain.py")
    if '"factor_system_lessons"' not in txt:
        return _fail("MLTO extras 未接读回路键")
    from backend.services.learning_readback import lane_enabled, readback_mode
    if readback_mode() != "auto":
        return _fail(f"当前 mode={readback_mode()}（期望 auto=默认不启用）")
    if lane_enabled("mlto"):
        return _fail("mlto 车道在 auto 模式下应默认关")
    return _ok("extras 已接键；auto 模式下 mlto=关，master 沿用 V7_LESSONS_IN_MASTER")


@check("F345", "读回路分层抽样：trajectory/pipeline_issue 可达（旧实现结构性排除 56.7%）")
def _f345b():
    from backend.services import learning_readback as LR
    now = "2026-09-18T00:00:00+00:00"
    pool = [{"id": i * 10 + j, "kind": k, "title": f"{k}-{chr(97 + j)}{j}",
             "summary": "s", "quality": 0.6, "use_count": 0, "cycle": "M",
             "period": "1h", "created_at": now}
            for i, k in enumerate(LR.ALL_KINDS) for j in range(5)]
    kinds = {r["kind"] for r in LR.select_lessons(pool, limit=6)}
    missing = {"trajectory", "pipeline_issue"} - kinds
    if missing:
        return _fail(f"仍不可达: {missing}")
    return _ok(f"6 条覆盖 {len(kinds)} 种 kind，含 trajectory/pipeline_issue")


@check("F345", "读取留痕：use_count 与车道前缀写入 retrieval_log")
def _f345c():
    import sqlite3
    import tempfile
    from backend.services import learning_readback as LR
    d = Path(tempfile.mkdtemp(prefix="verify_lr_"))
    db = d / "v7.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        "CREATE TABLE v7_lessons (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, kind TEXT,"
        " cycle TEXT, period TEXT, title TEXT, summary TEXT, report_json TEXT DEFAULT '{}',"
        " quality REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0, last_used_at TEXT,"
        " status TEXT DEFAULT 'active');"
        "CREATE TABLE v7_retrieval_log (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT,"
        " query TEXT, cycle TEXT, period TEXT, top_ids_json TEXT DEFAULT '[]');")
    con.execute("INSERT INTO v7_lessons (created_at,kind,cycle,period,title,summary)"
                " VALUES ('2026-09-18T00:00:00+00:00','gate_lesson','M','1h','t','s')")
    con.commit()
    con.close()
    old = os.environ.get("V7_MEMORY_DB_PATH")      # [卫生] 之前是"设成默认字符串"，
    os.environ["V7_MEMORY_DB_PATH"] = str(db)      # 会把"本来未设"变成"已设默认"——
    LR._SEEN.clear()                               # 留下环境副作用（被金丝雀测试抓到）
    try:
        LR.record_uses([1], lane="mlto", cycle="M", period="1h")
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        uc = con.execute("SELECT use_count FROM v7_lessons WHERE id=1").fetchone()[0]
        q = con.execute("SELECT query FROM v7_retrieval_log").fetchone()
        con.close()
    finally:
        if old is None:
            os.environ.pop("V7_MEMORY_DB_PATH", None)
        else:
            os.environ["V7_MEMORY_DB_PATH"] = old
    if uc == 1 and q and q[0].startswith("[mlto]"):
        return _ok("use_count=1 且 retrieval_log 带 [mlto] 前缀")
    return _fail(f"use_count={uc} log={q}")


@check("F360", "回测战绩进决策上下文，且假零记录标『不可信』")
def _f360():
    from backend.services import learning_readback as LR
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="verify_bt_"))
    p = d / "cf.jsonl"
    p.write_text(json.dumps({
        "symbol": "BTC", "timeframe": "4h", "days": 180, "tier": "mid",
        "baseline": {"sum_pnl": -6313.6},
        "incremental": [{"factor": "group:abc", "available": True,
                         "d_sum_pnl": 27.8, "d_trades": 1}],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    old = LR._COUNTERFACTUAL_PATH
    LR._COUNTERFACTUAL_PATH = p
    try:
        inc = LR.backtest_attr_snapshot()["incremental"]
        txt = LR.format_for_prompt([], {"backtest_attr": {"incremental": inc}})
    finally:
        LR._COUNTERFACTUAL_PATH = old
    if inc.get("trusted") is False and "不可信" in txt and "假零" in txt:
        return _ok("缺 funding_fgi.valid ⇒ trusted=False 且渲染 ⚠️")
    return _fail(f"trusted={inc.get('trusted')} txt_has_warn={'不可信' in txt}")


# ───────────────────── 评分 / 对拍读数 ─────────────────────

@check("F330/F336", "衰减惩罚非放大 + 大规模归零断路器")
def _f330():
    txt = _src("backend/services/factor_engine/factor_decay_monitor.py")
    if "FACTOR_DECAY_MASS_RETIRE_MAX_SHARE" not in txt:
        return _fail("未见质量退役断路器")
    if "breaker_downgrade" not in txt:
        return _fail("未见 breaker_downgrade 降级标记")
    if "min(1.0, max(0.3" not in txt:
        return _fail("未见 reduce 分支上限钳制")
    return _ok("断路器 + 降级标记 + 上限钳制均在")


@check("F342/F349", "回测因子归因：对象解包 + 缓存绕过后按需补算 + 覆盖率自检")
def _f349():
    txt = _src("backend/services/live_pipeline_backtest_engine.py")
    need = ["def _floats_of", "def _ensure_attr_sidecar",
            "factor_attr_coverage", "归因覆盖率不足"]
    miss = [n for n in need if n not in txt]
    if miss:
        return _fail(f"缺少 {miss}")
    from backend.services.live_pipeline_backtest_engine import LivePipelineBacktestEngine as E

    class _FV:
        def __init__(self, v, has_data=True):
            self.value, self.has_data = v, has_data
    got = E._floats_of({"a": _FV(-0.005), "b": _FV(0.9, has_data=False), "c": 0.7})
    if got == {"a": -0.005, "c": 0.7}:
        return _ok("FactorValue 解包正确，has_data=False 被剔除")
    return _fail(f"_floats_of 结果异常: {got}")


@check("F350", "Parity：未比对不得序列化出数字分")
def _f350():
    from backend.services.backtest_engine.parity_score import ParityScoreResult
    r = ParityScoreResult(nature="x", tier="mid", lookback_days=7, computed_at="t",
                          n_live_trades=0, n_bt_trades=0, available=False)
    d = r.to_dict()
    if d.get("score") is None and d.get("score_measured") is False:
        return _ok("score=None + score_measured=False")
    return _fail(f"score={d.get('score')} measured={d.get('score_measured')}")


@check("F351", "Parity 全量分受常数基准钉住（已知构造缺陷，已量化）")
def _f351():
    from backend.services.backtest_engine import parity_score as PS
    dev = min(abs(0.021135 - 0.0003) / 0.0003, PS._DEV_CAP)
    score = max(0.0, 1.0 - (PS.WEIGHTS["avg_fill_price_dev"] + PS.WEIGHTS["avg_slippage"]) * dev)
    if dev == PS._DEV_CAP and score == 0.0:
        return _ok("实际噪声下 score 恒为 0（两维合计权重 0.5）")
    return _fail(f"dev={dev} score={score}")


@check("F346", "权重『0=不参与』在读取侧被夹逼（真源不一致，已量化并双口径披露）")
def _f346():
    txt = _src("backend/services/factor_ic_evaluator.py")
    if "max(0.1, min(2.0, float(v))" not in txt:
        return _ok("已不再夹逼（若确为有意修复请同步报告 §17.2）")
    lr = _src("backend/services/learning_readback.py")
    if "真源不一致" in lr:
        return _ok("夹逼仍在（92/228），已在读数中双口径披露")
    return _fail("夹逼仍在但读数未披露")


@check("F348", "自检视图与投票路径同口径（不再显示 1.0 而实际 0.0）")
def _f348():
    txt = _src("backend/services/factor_engine/midlong_active_factor_set.py")
    if 'weights.get(r["factor_id"], 0.0)' in txt and "runtime_weight_measured" in txt:
        return _ok("默认 0.0 + measured 字段")
    return _fail("未改为同口径")


# ───────────────────── 回测可复现 / 静默退化 ─────────────────────

@check("F354/F359", "方向缓存带语义版本（改名+内容双重校验）")
def _f359():
    from backend.services import live_pipeline_backtest_engine as E
    p = E._factor_dir_disk_path("BTC", "4h")
    if E._FACTOR_DIR_SERIES_VERSION not in p.name:
        return _fail("文件名不含版本")
    txt = _src("backend/services/live_pipeline_backtest_engine.py")
    if '"ver": _FACTOR_DIR_SERIES_VERSION' not in txt:
        return _fail("写入内容不含版本，无法拦截改名/换实现")
    return _ok(f"版本 {E._FACTOR_DIR_SERIES_VERSION} 进文件名与内容")


@check("F358", "因子维度结构性失效可判定（缺 funding/fgi 时）")
def _f358():
    from backend.services.live_pipeline_backtest_engine import factor_dimension_inert
    p = {"factor_signal_weight": 0.3, "confirmation_min_dims": 2}
    if factor_dimension_inert(p, {}, {}) and not factor_dimension_inert(p, {"1": 0.001}, {}):
        return _ok("两序列皆缺 ⇒ True；有 funding ⇒ False")
    return _fail("判据不符合预期")


@check("F355/F356", "消融脚本：绕开缓存 + 空序列拒绝出结论 + 传 funding/FGI")
def _f355():
    txt = _src("scripts/run_backtest_counterfactual.py")
    need = ['PIPELINE_FACTOR_DIR_DISK_ENABLED"] = "0"', "_FACTOR_DIR_CACHE.clear()",
            "两者皆空", "拒绝出结论", "_load_funding_rates", "_load_fgi_series"]
    miss = [n for n in need if n not in txt]
    return _ok("缓存绕开+空序列护栏+真实序列") if not miss else _fail(f"缺少 {miss}")


@check("F327", "逐单归因不得把 NULL 盈亏当 0（只查代码，不查注释）")
def _f327():
    code = _code("backend/services/signal_feedback_tracker.py")
    hits = re.findall(r"trade_pnl_pct\s*or\s*0", code) + re.findall(r"trade_pnl\s*or\s*0", code)
    return _ok("代码中已无 `or 0` 兜底") if not hits else _fail(f"仍有 {len(hits)} 处 `or 0`")


@check("F339", "晋升门拒绝必须留日志（失败可见）")
def _f339():
    txt = _src("backend/services/promotion_gate_service.py")
    return _ok("scan_and_promote 记录拒绝原因") if "拒晋" in txt else _fail("未见拒绝日志")


@check("F332", "因子权重未更新不得静默 return False")
def _f332():
    txt = _src("backend/services/strategy_learning_service.py")
    return _ok("含『因子权重未更新』告警") if "因子权重未更新" in txt else _fail("仍是静默 return False")


@check("F349b", "归因是纯观察：关闸时不写 jsonl 且指标不变（源码级）")
def _f349b():
    txt = _src("backend/services/live_pipeline_backtest_engine.py")
    if "if _FACTOR_ATTR_ENABLED:" in txt and "persist_factor_attr(" in txt:
        return _ok("落盘受 _FACTOR_ATTR_ENABLED 保护")
    return _fail("落盘未受开关保护")


@check("B3", "两个回测引擎默认成交模型均为 next_open（无前视）")
def _b3():
    t1 = _src("backend/services/live_pipeline_backtest_engine.py")
    t2 = _src("backend/services/backtest_engine/backtest_engine.py")
    ok1 = 'BACKTEST_LP_FILL_MODEL", "next_open"' in t1 or '"next_open"' in t1
    ok2 = 'fill_model' in t2 and 'next_open' in t2
    if ok1 and ok2:
        return _ok("两引擎均以 next_open 为默认")
    return _fail(f"lp={ok1} legacy={ok2}")


@check("F388A", "解冻选项A在位：paper 追高天花板已置 0（回到缩仓放行、恢复采样）")
def _f388a():
    """[2026-09-18] 中线两方向互锁（日线 up 禁空 + 全市场分位 80–98% ≥70 硬否决多头）。
    置 0 ⇒ `_paper_shrink_ceiling()` 返回 None ⇒ 跳过硬否决，落回缩仓×0.25 放行。
    取证：docs/中线开仓冻结根因取证_20260918.md。回滚=改回 70。
    """
    v = _env_value("MIDLONG_LOCATION_PAPER_SHRINK_CEILING")
    if v is None:
        return _fail("`.env` 里没有该键（未显式配置，代码默认 70 ⇒ 仍是硬否决）")
    try:
        f = float(v)
    except ValueError:
        return _fail(f"值不可解析: {v!r}")
    if f == 0:
        return _ok("=0（paper 缩仓放行；live 仍硬否决）")
    return _fail(f"={v}（>0 ⇒ ≥{v:.0f}% 分位 paper 仍硬否决，互锁未解）")


@check("F388C", "解冻选项C在位：mid 止损后同向冷却已放宽到 30 分钟")
def _f388c():
    """[2026-09-18] 唯一能过位置闸的标的（日线 chop ⇒ 让位放行）在 1.5% 止损上限下
    6 分钟即被打掉，随后被同向锁 2h ⇒ 车道实际无单可开。1800 与 mid 同向基础冷却
    （reentry_cooldown.py:15 = 30min）对齐。回滚=改回 7200。
    """
    v = _env_value("REENTRY_SL_COOLDOWN_SEC_MID")
    if v is None:
        return _fail("`.env` 里没有该键（代码默认 7200 ⇒ 仍是 2 小时）")
    try:
        n = int(float(v))
    except ValueError:
        return _fail(f"值不可解析: {v!r}")
    if n <= 1800:
        return _ok(f"={n}s（{n // 60} 分钟）")
    return _fail(f"={n}s（{n // 60} 分钟 > 30 分钟，逃生口仍被长锁）")


@check("F390E", "open_execute_false 补 reason：三处接入 + 留存通道 + 既有 take 语义未变")
def _f390e():
    """[2026-09-18 选项E] 该事件此前**完全没有原因字段**（近 48h 321 条全无因，
    其中 160 条是 mid 做多）。实现必须同时满足：①brain 的 payload 带 reason；
    ②登记点在 fuse 层与内部闸两处；③`take_open_block` 仍取走即清空（既有消费者依赖）。
    """
    br = _code("backend/services/mlto/brain.py")
    obr = _code("backend/services/mlto/open_block_reason.py")
    ex = _code("backend/services/full_auto/midlong_executor.py")
    hlp = _code("backend/services/full_auto/midlong_helpers.py")
    problems = []
    if '"reason"' not in br or "last_open_block" not in br:
        problems.append("brain 未把 reason 写进 open_execute_false")
    if "<未登记>" not in br:
        problems.append("无登记时未写显式占位（有猜测风险）")
    if "def remember_open_block" not in obr or "def last_open_block" not in obr:
        problems.append("open_block_reason 缺按 (symbol,tier) 的留存通道")
    if "remember_open_block" not in ex:
        problems.append("midlong_executor 未登记（fuse 层）")
    if "remember_open_block" not in hlp:
        problems.append("midlong_helpers 未登记（内部闸统一出口）")
    i = obr.find("def take_open_block")
    if i < 0 or "_BLOCK.set(None)" not in obr[i:i + 300]:
        problems.append("take 语义被改（必须仍取走即清空）")
    if "take_open_block" not in hlp:
        problems.append("既有消费点被移除")
    if problems:
        return _fail("; ".join(problems))
    return _ok("payload 带 reason + 两处登记 + 留存通道 + take 语义未变")


@check("§0.0", "交付物文档存在且章节完整")
def _docs():
    rep = ROOT / "docs/复查报告_因子×LLM架构_20260917.md"
    todo = ROOT / "docs/复查后待办_可执行清单_20260918.md"
    if not (rep.exists() and todo.exists()):
        return _fail("缺报告或待办")
    t = rep.read_text(encoding="utf-8")
    need = ["## 17.", "## 18.", "## 19.", "## 20."]
    miss = [n for n in need if n not in t]
    if miss:
        return _fail(f"报告缺章节 {miss}")
    return _ok(f"报告 {t.count(chr(10)) + 1} 行，待办 {todo.read_text(encoding='utf-8').count(chr(10)) + 1} 行")


@check("F362", "v7 Codegen 池：可达性已量化 + 全量开关（默认关）+ 饱和告警")
def _f362():
    import sqlite3
    import tempfile
    from datetime import datetime, timezone
    from backend.services.evolution import evolution_memory_v7 as EV7
    d = Path(tempfile.mkdtemp(prefix="verify_v7_"))
    db = d / "v7.db"
    con = sqlite3.connect(str(db))
    con.executescript(EV7._SCHEMA)
    now = datetime.now(timezone.utc).isoformat()
    con.execute("INSERT INTO v7_lessons (created_at,kind,cycle,period,title,summary,quality)"
                " VALUES (?,?,?,?,?,?,?)",
                (now, "success_recipe", "L", "4h", "黄金配方 因子挖掘 晋升", "高价值", 0.99))
    for i in range(205):
        con.execute("INSERT INTO v7_lessons (created_at,kind,cycle,period,title,summary,quality)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (now, "pipeline_issue", "L", "4h", f"噪声-{i}", "低价值", 0.5))
    con.commit()
    con.close()
    old_path = EV7._DB_PATH
    import os
    old_env = os.environ.pop("V7_MEMORY_DB_PATH", None)
    old_flag = os.environ.pop("V7_CODEGEN_FULL_POOL", None)
    EV7._DB_PATH = db
    EV7._CODEGEN_SATURATED_WARNED.clear()
    try:
        off = EV7.build_codegen_context("4h", limit=8)
        os.environ["V7_CODEGEN_FULL_POOL"] = "1"
        EV7._CODEGEN_SATURATED_WARNED.clear()
        on = EV7.build_codegen_context("4h", limit=8)
    finally:
        EV7._DB_PATH = old_path
        os.environ.pop("V7_CODEGEN_FULL_POOL", None)
        if old_env is not None:
            os.environ["V7_MEMORY_DB_PATH"] = old_env
        if old_flag is not None:
            os.environ["V7_CODEGEN_FULL_POOL"] = old_flag
    if "黄金配方" in off:
        return _fail("默认模式不应读到被窗口挤出的老教训（旧行为被改变了）")
    if "黄金配方" not in on:
        return _fail("全量开关未生效（老教训仍读不到）")
    doc = EV7.build_codegen_context.__doc__ or ""
    if "LIMIT 200" not in doc or "V7_CODEGEN_FULL_POOL" not in doc:
        return _fail("docstring 未写明窗口与开关")
    return _ok("默认关=旧行为；开=老教训可达；docstring 已记录窗口与开关")


@check("F364", "六批声明复核：C2 条数/文档一致、C5 配额、C6 冷却常量、C7 补建接线")
def _f364():
    """把 §6–§11 六批里此前未核验的声明钉住（详见报告 §24）。"""
    import json as _json
    # C2：GTJA 子集真实条数必须与代码读取一致（docstring 已改为不写死条数）
    p = ROOT / "backend/data/factors_lab/gtja191_subset.json"
    if not p.exists():
        return _fail("GTJA 子集文件不存在")
    d = _json.loads(p.read_text(encoding="utf-8"))
    forms = d.get("formulas") or []
    if len(forms) < 20:
        return _fail(f"GTJA 子集条数异常: {len(forms)}")
    cal = _src("backend/services/factors_lab/calibration.py")
    if "10条单序列适配版" in cal:
        return _fail("calibration.py docstring 仍写死『10条』（文档漂移未修）")
    # C5：.env 配额
    m = re.search(r"^\s*KLINE_LLM_MAX_PER_CYCLE\s*=\s*(\S+)", _src(".env"), re.M)
    if not m or m.group(1) != "16":
        return _fail(f"KLINE_LLM_MAX_PER_CYCLE != 16（实测 {m.group(1) if m else '未设'}）")
    # C6：冷却常量
    svc = _src("backend/services/full_auto_trading_service.py")
    if "_BLOCK_STREAK_TRIGGER = 5" not in svc or "_BLOCK_COOLDOWN_SEC = 30 * 60" not in svc:
        return _fail("同因拦截冷却常量与声明（5 次 / 30 分钟）不符")
    # C7：补建接线 + 现象时间线
    pe = _src("backend/services/full_auto/proposal_execution.py")
    if "auto_create_strategy=svc._auto_create_strategy" not in pe:
        return _fail("proposal_execution 未接线 auto_create_strategy")
    if "此处自动补建一次再解析" not in pe:
        return _fail("未见『自动补建一次再解析』实现")
    return _ok(f"GTJA={len(forms)} 条；配额=16；冷却 5 次/30 分；补建已接线")


@check("F365", "开启读回路后 prompt 增量受限（<10% extras 预算）且三节齐全")
def _f365():
    """A5 开启前的关键不变量：**开启后不能把主脑 prompt 撑爆**。

    口径：extras 明文预算 `MIDLONG_BRAIN_EXTRAS_CHARS`（默认 60000）；
    读回路块 = 分层教训 + 因子权重/治理 + 回测战绩 三节。
    本检查**不写任何库**：只把合成教训与真实快照交给纯渲染函数。
    """
    from backend.services import learning_readback as LR
    budget = int(os.environ.get("MIDLONG_BRAIN_EXTRAS_CHARS", "60000") or 60000)
    lessons = [{"kind": k, "cycle": "M", "period": "1h",
                "title": f"{k} 标题 " * 4, "summary": "摘要 " * 30}
               for k in ("trajectory", "gate_lesson", "pipeline_issue")]
    snap = LR.factor_system_snapshot()
    txt = LR.format_for_prompt(lessons, snap, char_budget=budget)
    frac = len(txt) / budget
    if frac >= 0.10:
        return _fail(f"块长 {len(txt)} 字符 = 预算的 {frac:.1%}（应 < 10%）")
    if "回测侧因子战绩" not in txt:
        return _fail("缺少回测战绩节（F360 未生效）")
    return _ok(f"块长 {len(txt)} 字符 = 预算的 {frac:.2%}；含教训/治理/回测三节")


@check("F367", "因子锚周期按 tier（不再写死 4h）+ 消费者按前缀认权重")
def _f367():
    """用户指出「长线周期没有正确接入」——查证成立，且是**三处**问题：
    ① 周期写死 4h（`TIER_CONFIG` mid=1h/long=4h）；
    ② 暴露层 `_load_active()` 不按周期筛因子（周期只决定用哪套 K 线）；
    ③ 信号名 `factor_anchor_4h` 未登记 ⇒ `weights.get(name, 0.01)` 兜底 ⇒ 打开也几乎无效。
    本检查钉住①③的修复，并确认锚点仍**默认关**（无行为变更）。
    """
    from backend.services.mlto import quant_layer as Q
    from backend.services.mlto import decision_hub as H
    old = os.environ.pop("MLTO_FACTOR_ANCHOR_PERIOD", None)
    oldw = os.environ.pop("MLTO_FACTOR_ANCHOR_WEIGHT", None)
    try:
        if Q._anchor_period("mid") != "1h" or Q._anchor_period("long") != "4h":
            return _fail(f"周期未按 tier：mid={Q._anchor_period('mid')} "
                         f"long={Q._anchor_period('long')}")
        if H._base_weight_for("factor_anchor_1h", H.WEIGHTS_MID) == 0.01:
            return _fail("factor_anchor_* 仍落到 0.01 兜底（消费者没接上）")
        if H._base_weight_for("unknown_xyz", H.WEIGHTS_MID) != 0.01:
            return _fail("未知信号的 0.01 兜底语义被改变了")
    finally:
        if old is not None:
            os.environ["MLTO_FACTOR_ANCHOR_PERIOD"] = old
        if oldw is not None:
            os.environ["MLTO_FACTOR_ANCHOR_WEIGHT"] = oldw
    src = _src("backend/services/mlto/quant_layer.py")
    if not re.search(r'FEATURE_MIDLONG_FACTOR_ANCHOR_ENABLED",\s*"false"', src):
        return _fail("因子锚不再默认关（会改变今日行为）")
    return _ok("mid→1h / long→4h；锚权重走前缀识别；锚点仍默认关")


@check("F371", "实盘 prompt 构造器在**真实传参形状**下不再抛异常")
def _f371():
    """三个实盘调用方传 `{}` / `None` / 缺省 ⇒ `or SUPPORTED_SYMBOLS`（值为**字符串**）；
    旧代码 `normalized_symbol_metadata.get(s, {}).get("name")` 对 str 调 .get ⇒
    **AttributeError** ⇒ 该车道永远渲染不出 prompt（P0 只是第一道拦路）。
    本检查用真实形状跑 `build()`，并核对展示名解析的三种形状。
    """
    from backend.services.prompt_context.builder import PromptContextBuilder, _display_name
    from backend.services.prompt_context import BuildInput
    from backend.services.ai_decision_service import SUPPORTED_SYMBOLS

    if _display_name("Bitcoin", "BTC") != "Bitcoin":
        return _fail("str 形状未容错（崩溃根因仍在）")
    if _display_name({"name": "比特币"}, "BTC") != "比特币":
        return _fail("dict 形状未取 name")
    if _display_name(None, "BTC") != "Bitcoin":
        return _fail("缺失时未回退 SUPPORTED_SYMBOLS")

    class _A:
        id = 1
        name = "probe"
        model = "m"
        leverage = 3
        initial_capital = 1e4
        current_capital = 1e4
        environment = "mainnet"

    for label, meta in (("{}", {}), ("None", None), ("SYMBOLS", SUPPORTED_SYMBOLS)):
        active = meta or SUPPORTED_SYMBOLS
        try:
            res = PromptContextBuilder().build(BuildInput(
                account=_A(), portfolio={}, prices={}, symbol_metadata=active,
                symbol_order=list(active.keys()) if isinstance(active, dict) else None))
        except Exception as exc:
            return _fail(f"{label} 形状仍抛异常: {type(exc).__name__}: {str(exc)[:90]}")
        if not isinstance(res, dict) or len(res) < 30:
            return _fail(f"{label} 形状产出异常: {type(res).__name__} 长度 {len(res) if hasattr(res, '__len__') else '?'}")
    return _ok("三种真实形状均成功（41 键）；展示名解析容忍 str/dict/缺失")


@check("F384", "两处静默失败已改为可见日志（结局回填失配 / 宏观日历空返回）")
def _f384():
    """与 F332/F339 同一纪律（禁止静默退化）的两处补修，**只加日志、不改行为**：
    ① `episodic_memory.backfill_outcome` 失配（会话轮转/tier 不一致）原为**零日志**
       ⇒ 结局永久丢失而外部看不出（实测 261 条情景仅 6 条带结局）；
    ② `macro_data_collector._llm_extract_calendar` 无 LLM 配置时裸 `return []`
       ⇒ 调用方无法区分"无事件"与"未抽取"。
    """
    ep = _src("backend/services/mlto/episodic_memory.py")
    i = ep.find("if row is None")
    seg = ep[i:i + 900]
    if "logger.warning" not in seg or "结局丢失" not in seg:
        return _fail("结局回填失配仍未留 warning")
    tail = ep[ep.rfind("except"):]
    if "logger.debug" in tail:
        return _fail("backfill 异常路径仍停留在 debug（生产 INFO 下不可见）")
    mac = _src("backend/services/macro_data_collector.py")
    j = mac.find("def _llm_extract_calendar")
    if "logger.warning" not in mac[j:j + 1600]:
        return _fail("宏观日历空返回仍无日志")
    return _ok("两处均留 warning（行为未变：仍 return []/False）")


NOT_COVERED = [
    "「开仓准确率是否提升」的统计判定 —— 需实盘样本累积（胜率口径每组 ~313 笔 ≈ 14 天；"
    "逐单口径每组 ~141 笔 ≈ 7 天），离线无法给结论（报告 §14）",
    "hybrid 评分的 IC 时序/校准/PBO —— 需接线后按周累积数据（报告 §14）",
    "`factor_weights` 写入链真因 —— 需下一轮学习循环实际跑一次（F332 已加日志）",
    "回测↔实盘 Parity 的新结论 —— 需下一轮周报（含 F350 修复后的记录）",
    "RLS 相关读数（ai_strategies / signal_trade_feedback 行数）—— 需 DB 且需 app.is_admin GUC；"
    "已由脚本 scripts/audit_learning_gap.py 覆盖（需连库）",
    "一切『实盘行为』结论 —— 本轮未重启后端、未改 .env，运行态仍是旧代码",
]


def main() -> int:
    verbose = "-v" in sys.argv
    print("=" * 78)
    print("复查结论一键复验（离线；只读；不改任何东西）")
    print("=" * 78)
    passed = failed = 0
    for fid, title, fn in RESULTS:
        try:
            ok, detail = fn()
        except Exception as exc:      # 检查自身异常记为失败，不掩盖
            ok, detail = False, f"检查抛异常: {type(exc).__name__}: {str(exc)[:110]}"
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{mark}] {fid:<10} {title}")
        if verbose or not ok:
            print(f"           └─ {detail}")
    print("-" * 78)
    print(f"合计 {len(RESULTS)} 项：PASS {passed} / FAIL {failed}")
    if failed:
        print("**有检查未通过** ⇒ 要么修复被覆盖，要么文档需同步（见上）。")
    print("\n未覆盖（本脚本刻意不判，避免『全绿=全都验证了』的误解）：")
    for i, x in enumerate(NOT_COVERED, 1):
        print(f"  {i}. {x}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
