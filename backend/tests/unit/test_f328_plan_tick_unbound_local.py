# -*- coding: utf-8 -*-
"""[F328 2026-09-21] 生产停摆回归：`plan_tick` 不得读取"只在某分支里绑定"的名字。

# 事故（真实，2026-09-21 约 15:00，停摆 ~15 分钟）

心跳 `logs/mm_lane_status.json`：

    "ok": false,
    "reason": "cannot access local variable '_pos_d' where it is not associated with a value",
    "ticks": 231, "fills": 432

`last_tick_ts` 停在 15:0x，而心跳时间持续刷新 ⇒ **车道一个 tick 都跑不出去**，
心跳却一直在写（心跳证明"循环活着"，不证明"车道在工作"）。
⇒ **只看心跳时间会以为车道健康；`ok=false` + `reason` 才是关键字段。**

# 根因

F302（撤减仓侧出库挂单）在 `plan_tick` 里**无条件**读了 `_pos_d`：

    if bool(getattr(limits, "reduce_quote_disabled", False)) and abs(_pos_d) > 1e-12:

而 `_pos_d` 只在上面 `_side_mode == "reduce_only_when_inv"` 分支里赋值。
生产 `side_mode="both"` ⇒ 该分支从不执行 ⇒ 名字**从未绑定** ⇒ `NameError`。

触发条件恰好是 H178 A/B 的 B 臂（`reduce_quote_disabled=True`），
而 A/B 脚本退出时未能恢复该开关（`finally` 在 SIGTERM 下不执行）
⇒ **崩溃配置被留在生产里**。这是第二层教训：
**实验脚本必须保证回滚，否则实验结束的那一刻就是生产故障的开始。**

# 为什么既有 6 项 F302 测试没抓到

它们只做**源码字符串断言**（`inspect.getsource` + `in`）。
`"abs(_pos_d) > 1e-12" in src` 对**坏代码和好代码都成立**
—— 验证的是"写了什么字"，不是"能不能跑"。本测试改用 **AST + 执行**。

# 静态判据为什么只审下划线前缀名

写成"找出所有只在 `if` 体内绑定、却在 `if` 之外被读取的名字"会**误报满天飞**：
Python 的 `for` 目标、`with ... as`、推导式变量、`except ... as` 都是"有条件绑定"
但对本函数完全合法。实测这个宽口径在 `plan_tick` 上报出 **33 条**假阳性，
其中没有一条是真问题 —— 这种防线会立刻被人删掉。

而下划线前缀名（`_pos_d` / `_pos_now` / `_tp_ready` …）**绝不会**是循环变量或
推导式变量：它们是手写的临时量，一旦跨块复用就说明作者搞错了作用域。
本次事故 100% 落在这一族里 ⇒ 把判据收窄到它，准确且零噪声。
"""
from __future__ import annotations

import ast
import builtins
import inspect

import pytest

from backend.services.market_maker import runner as R

# 事件说明里会**故意**引用这些名字，所以文本检查必须先剥注释
_HISTORICAL = ("_pos_d",)


# ── 0. 工具 ─────────────────────────────────────────────────────────────
def _strip_comments(src: str) -> str:
    """剥掉 `#` 注释（只留可执行源码）。

    ⚠️ 必须剥：F327 的事故说明里**故意**引用了 `_pos_d`。
    不剥注释会让"`_pos_d` 不应再出现"被自己的说明文字触发假失败
    —— 本仓库已第 5 次踩到这一类（F326 / F252 / F254 / F258）。
    """
    out = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        if "#" in ln:
            ln = ln.split("#", 1)[0]
        out.append(ln)
    return "\n".join(out)


def _scopes(node: ast.AST) -> list:
    """返回 `[(bound_set, load_list)]`：每个"顺序作用域"的绑定与读取。

    两个作用域：
      ① 函数体（含所有嵌套块 —— 块内绑定对函数体可见）；
      ② **每个推导式**：它自己的目标变量只在自己内有效。

    ⚠️ 推导式必须**排除在函数体扫描之外**：`ast.walk` 会把 `[_q for _q in x]`
    的目标 `_q` 收进"函数体已绑定"，于是"函数体里读了 `_q`"变成假阴性
    （实测：`for _sym2 in rows:` 的 `_sym2` 就是这样被漏掉的）。
    """
    out = []

    # ① 函数体：跳过所有推导式子树
    comps = [g for g in ast.walk(node)
             if isinstance(g, (ast.ListComp, ast.SetComp, ast.DictComp,
                               ast.GeneratorExp))]
    comp_nodes = set()
    for g in comps:
        for sub in ast.walk(g):
            comp_nodes.add(id(sub))

    bound: set = set()
    loads: list = []
    for sub in ast.walk(node):
        if id(sub) in comp_nodes:
            continue
        if isinstance(sub, ast.Name):
            if isinstance(sub.ctx, (ast.Store, ast.Del)):
                bound.add(sub.id)
            else:
                loads.append((sub.id, sub.lineno))
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(sub.name)
        elif isinstance(sub, ast.arg):
            bound.add(sub.arg)
        elif isinstance(sub, ast.alias):
            bound.add((sub.asname or sub.name).split(".")[0])
        elif isinstance(sub, (ast.Global, ast.Nonlocal)):
            bound.update(sub.names)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            bound.add(sub.name)
    out.append((bound, loads))

    # ② 每个推导式自成作用域
    for g in comps:
        g_bound: set = set()
        g_loads: list = []
        for sub in ast.walk(g):
            if isinstance(sub, ast.Name):
                if isinstance(sub.ctx, (ast.Store, ast.Del)):
                    g_bound.add(sub.id)
                else:
                    g_loads.append((sub.id, sub.lineno))
        out.append((g_bound, g_loads))
    return out


def _key(name: str) -> str:
    """只审下划线前缀名 —— 循环变量/推导式变量不会是这个名字形状。"""
    return name if name.startswith("_") and name != "_" else ""


def _scan_function_src(src: str, *, module_names: set | None = None) -> list:
    """返回 `[(name, load_lineno)]`：下划线前缀名被读取、却在该作用域内无绑定。

    `module_names`：**模块级**已存在的名字（模块函数/常量）。
    必须传进来，否则 `_force_exit_allowed` 这类模块级函数会被误报成"未绑定"
    —— 实测第一版就报出了它（假阳性 ⇒ 防线会被删）。
    """
    known = set(dir(builtins))
    if module_names:
        known |= module_names
    fn = ast.parse(src).body[0]
    bad: list = []
    for bound, loads in _scopes(fn):
        for nm, lineno in loads:
            if _key(nm) and nm not in bound and nm not in known:
                bad.append((nm, lineno))
    return sorted(set(bad), key=lambda t: (t[1], t[0]))


def _scan_function(func) -> list:
    return _scan_function_src(inspect.getsource(func), module_names=set(vars(R).keys()))


# ── 1. detector 自证（先证明工具是好的，再用它下结论）──────────────────────
@pytest.mark.unit
def test_detector_catches_a_never_bound_name():
    """**自证**：下划线名字只被读取、从未绑定 ⇒ 必须报出（含行号）。

    没有这一条，下面的断言可能只是"detector 失效所以永远绿"
    —— 本仓库已有多次"防线本身是坏的"事故（F326 等）。
    """
    src = (
        "def _replica(limits, side_mode, book, sym, allow_buy, allow_sell):\n"
        "    if side_mode == 'reduce_only_when_inv':\n"
        "        _pos_rq = book.qty(sym)\n"
        "        if _pos_rq > 0:\n"
        "            allow_buy = False\n"
        "    if limits.reduce_quote_disabled and abs(_pos_d) > 1e-12:\n"
        "        allow_sell = False\n"
        "    return allow_buy, allow_sell\n"
    )
    bad = _scan_function_src(src)
    assert ("_pos_d", 6) in bad, f"detector 未报出未绑定名；实际：{bad}"


@pytest.mark.unit
def test_detector_documents_its_static_limit():
    """**把 detector 的能力边界写成测试**（诚实标注，防止误以为它全能）。

    事故那个组合之所以能在生产发生、却在单测里查不出来，是因为：

      `if _side_mode == "reduce_only_when_inv": _pos_d = ...`

    静态看，`_pos_d` **确实被绑定了** —— detector 无法知道
    `side_mode` 在生产是 `"both"`，所以**查不出**这一例。
    真正抓住它的是 F302 那个执行级测试（真跑崩溃组合，见本文件最后一节）。

    ⚠️ 这一条**故意**断言 detector 查不出来。
    如果哪天有人把 detector 加强到能查出它，这条会红 —— 那时应该**删掉这条**，
    而不是削弱 detector。留着它是为了让"我们依赖什么"有据可查。
    """
    src = (
        "def _replica(limits, side_mode, book, sym, allow_buy, allow_sell):\n"
        "    if side_mode == 'reduce_only_when_inv':\n"
        "        _pos_d = book.qty(sym)\n"
        "        if _pos_d > 0:\n"
        "            allow_buy = False\n"
        "    if limits.reduce_quote_disabled and abs(_pos_d) > 1e-12:\n"
        "        allow_sell = False\n"
        "    return allow_buy, allow_sell\n"
    )
    assert _scan_function_src(src) == [], (
        "detector 现在能查出'条件绑定'了 ⇒ 请删掉本测试"
        "（它的存在只是为了记录静态能力的边界）")


@pytest.mark.unit
def test_detector_reports_nothing_for_the_fixed_structure():
    """**反向自证**：修好后的结构（自取 `_pos_now`）必须一条都不报。

    防的是 detector 过于激进 —— 那会让它变成一条永远失败、随后被人删掉的测试。
    """
    src = (
        "def _fixed(limits, side_mode, book, sym, allow_buy, allow_sell):\n"
        "    if side_mode == 'reduce_only_when_inv':\n"
        "        _pos_rq = book.qty(sym)\n"
        "        if _pos_rq > 0:\n"
        "            allow_buy = False\n"
        "    _pos_now = book.qty(sym)\n"
        "    if limits.reduce_quote_disabled and abs(_pos_now) > 1e-12:\n"
        "        allow_sell = False\n"
        "    return allow_buy, allow_sell\n"
    )
    assert _scan_function_src(src) == [], "修好后的结构不应被误报"


@pytest.mark.unit
def test_detector_ignores_loop_and_comprehension_scopes():
    """**第三条自证**：循环变量与推导式变量不得被误报。

    这是宽口径版本在 `plan_tick` 上报出 33 条假阳性的主要来源。
    不固定这一条，detector 会在下一个版本里被"好心放宽"回噪声状态。
    """
    src = (
        "def _loops(rows, book):\n"
        "    out = []\n"
        "    for _sym2 in rows:\n"
        "        out.append(book.qty(_sym2))\n"
        "    _vals = [_q for _q in out if _q > 0]\n"
        "    return sum(_vals)\n"
    )
    assert _scan_function_src(src) == [], "循环/推导式作用域不得被误报"


# ── 2. 静态：plan_tick 的下划线临时量必须都有绑定 ─────────────────────────
@pytest.mark.unit
def test_plan_tick_has_no_unbound_underscore_names():
    """**核心回归**。

    不依赖任何运行环境、不依赖分支是否被走到 ——
    崩溃那个组合（`side_mode="both"` + `reduce_quote_disabled=True`）
    在既有单测里从未被执行过，只有静态扫描能提前发现。
    """
    bad = _scan_function(R.plan_tick)
    assert not bad, (
        "plan_tick 读取了在**本作用域内从未绑定**的下划线临时量 "
        "⇒ 该分支不成立时抛 NameError ⇒ 车道停摆。"
        + repr(bad)
        + "（格式：(名字, 读取处行号)）。这类名字只在某一个 `if` 体里赋值时，"
          "只要那个 `if` 不成立就是致命错误。")


@pytest.mark.unit
def test_pos_now_is_bound_unconditionally():
    """F302 块必须**自己**取库存（`_pos_now`），不能依赖上游分支。"""
    src = inspect.getsource(R.plan_tick)
    assert "_pos_now = local_book.qty(" in src, "F302 块必须自取库存"
    assert "abs(_pos_now) > 1e-12" in src, "F302 块的判据应基于 _pos_now"
    # 只看**可执行代码**：历史名应已消失（改名 `_pos_rq`，作用域自明）
    code = _strip_comments(src)
    for old in _HISTORICAL:
        assert old not in code, (
            f"可执行代码里仍有 `{old}` ⇒ 跨块复用又写回来了。"
            "该名字只在 `side_mode == 'reduce_only_when_inv'` 分支里绑定。")


# ── 3. 动态：真跑崩溃组合，并证明走到了那个块 ────────────────────────────
@pytest.mark.unit
def test_reduce_quote_off_actually_fires_with_side_mode_both():
    """注入库存后真跑：`side_mode="both"` + `reduce_quote_disabled=True`
    必须**撤掉减仓侧**且**不抛异常**。

    断言"多头的 ask 被撤掉"是为了证明执行**真的走到了 F302 块**：
    若被前面的闸门提前 `return`，ask 也会是 0，但那时本块并未被验证。
    所以同时检查 `dec.skip`，确保原因是本块专属的 `reduce_quote_off`。
    """
    import time

    from backend.services.market_maker.core import (
        InventoryBook,
        LaneRiskLimits,
        Position,
        QuoteParams,
    )
    from backend.services.market_maker.runner import SymbolState

    params = QuoteParams(spread_mult=0.5, w_base_bp=5.0, min_width_bp=0.3)
    assert params.side_mode != "reduce_only_when_inv", (
        "前提：side_mode != reduce_only_when_inv，否则 _pos_d 有值，测不到崩溃")
    limits = LaneRiskLimits(reduce_quote_disabled=True)

    book = InventoryBook()
    book.positions["SOLUSDT"] = Position(qty=1.0, opened_ts=time.time() - 5.0)

    kwargs = {
        "state": SymbolState(symbol="SOLUSDT"),
        "mid": 100.0, "seg_low": 99.9, "seg_high": 100.1,
        "seg_taker_sell": 1000.0, "seg_taker_buy": 1000.0,
        "now_ts": time.time(), "params": params, "limits": limits,
        "book": book,
    }
    sig = inspect.signature(R.plan_tick)
    kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}

    dec, _ctx = R.plan_tick(**kwargs)  # 崩溃时这里抛 NameError

    assert dec is not None
    # 多头（qty>0）⇒ 减仓侧是卖侧 ⇒ ask 必须被撤
    assert dec.ask == 0.0, (
        f"持多头时减仓侧(ask)应被撤掉；实际 ask={dec.ask} skip={dec.skip}")
    assert dec.skip == "reduce_quote_off", (
        f"skip 原因应为 reduce_quote_off（证明是本块生效）；实际 {dec.skip!r}")


@pytest.mark.unit
def test_reduce_quote_off_runs_with_flat_book_and_side_mode_both():
    """**这是真正崩溃的那条路径**：`reduce_quote_disabled=True` 且**空仓**。

    上一条测试注入了持仓 ⇒ 走的是 `abs(_pos_now) > 1e-12` 为真的分支。
    但生产崩溃时触发的是**空仓**那条：`and` 短路后 `_pos_now` 仍被求值，
    而旧代码在此处读的是从未绑定的 `_pos_d` ⇒ `NameError`。

    ⇒ 两条路径都必须有测试。只测"有持仓"会漏掉崩溃本身，
      只测"空仓"会漏掉撤单行为 —— 这两种漏法本仓库都发生过。
    """
    import time

    from backend.services.market_maker.core import (
        InventoryBook,
        LaneRiskLimits,
        QuoteParams,
    )
    from backend.services.market_maker.runner import SymbolState

    params = QuoteParams(spread_mult=0.5, w_base_bp=5.0, min_width_bp=0.3)
    limits = LaneRiskLimits(reduce_quote_disabled=True)
    book = InventoryBook()          # 空仓（不注入任何 Position）

    kwargs = {
        "state": SymbolState(symbol="SOLUSDT"),
        "mid": 100.0, "seg_low": 99.9, "seg_high": 100.1,
        "seg_taker_sell": 1000.0, "seg_taker_buy": 1000.0,
        "now_ts": time.time(), "params": params, "limits": limits,
        "book": book,
    }
    sig = inspect.signature(R.plan_tick)
    kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}

    dec, _ctx = R.plan_tick(**kwargs)   # 崩溃时这里抛 NameError

    assert dec is not None
    assert dec.skip != "reduce_quote_off", (
        "空仓时**不得**撤减仓侧：两侧都是加仓侧，撤掉就等于不做市了")
    assert (dec.bid > 0.0 or dec.ask > 0.0), (
        f"空仓时至少应有一侧在报价；实际 bid={dec.bid} ask={dec.ask} "
        f"skip={dec.skip}")
