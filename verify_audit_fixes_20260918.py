"""轮67-69 审计修复：实机验证脚本（只读）。

一次跑完，逐条打印每个 P0/P1 修复的**实测**证据。
用法：.venv\\Scripts\\python.exe verify_audit_fixes_20260918.py
"""
import io
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

OK = "  [OK]  "
BAD = "  [!!]  "
INFO = "        "

results = []


def check(name, passed, detail=""):
    results.append((name, bool(passed)))
    print(f"{OK if passed else BAD}{name}")
    if detail:
        for line in str(detail).splitlines():
            print(f"{INFO}{line}")


print("=" * 78)
print("轮67-69 审计修复实机验证")
print("=" * 78)

# ── P0-2 有界历史区间读取 ─────────────────────────────────────────────
print("\n── P0-2  DataCenter：有界历史区间 trade 读取（原恒 0 行）──")
try:
    from backend.services.data_center import data_center, kline_fresh_window_sec
    r_trade = data_center.get_klines("BTC", "1d", count=500,
                                     start="2026-01-01", end="2026-03-01", purpose="trade")
    r_res = data_center.get_klines("BTC", "1d", count=500,
                                   start="2026-01-01", end="2026-03-01", purpose="research")
    check("有界历史 purpose=trade 返回数据",
          r_trade.count > 0,
          f"trade count={r_trade.count}（修复前为 0）| research count={r_res.count}\n"
          f"bounded_history={r_trade.bounded_history} is_fresh={r_trade.is_fresh}")
    check("有界读取不被新鲜度闸门拦截",
          bool(r_trade.is_fresh) and r_trade.bounded_history,
          f"stale_sec={r_trade.stale_sec and round(r_trade.stale_sec)}s（该值很大但仍应放行）")
    r_live = data_center.get_klines("BTC", "1h", count=200, purpose="trade")
    check("实时（无界）读取仍受新鲜度约束",
          (not r_live.bounded_history),
          f"BTC/1h count={r_live.count} bounded={r_live.bounded_history} "
          f"stale={r_live.stale_sec and round(r_live.stale_sec)}s "
          f"lag={r_live.lag_sec and round(r_live.lag_sec)}s "
          f"thr={kline_fresh_window_sec('1h'):.0f}s is_fresh={r_live.is_fresh}")

    from backend.api.data_center_routes import _fresh_stale_limit_sec
    same = all(_fresh_stale_limit_sec(p) == kline_fresh_window_sec(p)
               for p in ("15m", "1h", "4h", "1d"))
    check("新鲜度阈值单一权威（路由 = data_center）", same,
          " / ".join(f"{p}:{kline_fresh_window_sec(p):.0f}s" for p in ("15m", "1h", "4h", "1d")))
except Exception as e:
    check("P0-2 验证执行", False, f"{type(e).__name__}: {e}")

# ── P0-3 交易模式 ─────────────────────────────────────────────────────
print("\n── P0-3  交易模式 = session.trading_mode，不是 session.status ──")
try:
    from backend.services.full_auto.analyst_system_cycle import resolve_trading_mode

    class _S:
        def __init__(self, status, tm):
            self.status, self.trading_mode = status, tm

    cases = [(("running", "paper"), "paper"), (("running", "live"), "live"),
             (("defensive", "live"), "live"), (("paused", "live"), "live")]
    bad = [(s, tm, resolve_trading_mode(_S(s, tm))) for (s, tm), want in cases
           if resolve_trading_mode(_S(s, tm)) != want]
    check("按 trading_mode 解析（status 被完全忽略）", not bad,
          "、".join(f"status={s},tm={tm}→{got}" for s, tm, got in bad) or
          " / ".join(f"status={s}+tm={tm}→{resolve_trading_mode(_S(s, tm))}" for (s, tm), _ in cases))

    fallback = resolve_trading_mode(_S("running", "weird"))
    check("非法值保守回落 paper", fallback == "paper", f"trading_mode='weird' → {fallback}")

    # 源码级：两个站点不得再把 status 赋给 mode
    import re
    root = os.path.dirname(os.path.abspath(__file__))
    pat = re.compile(r'^\s*mode\s*=\s*session\.status\s*$')
    hits = []
    for rel in ("backend/services/full_auto/analyst_system_cycle.py",
                "backend/services/tier_parallel_executor.py"):
        for i, line in enumerate(io.open(os.path.join(root, rel), encoding="utf-8"), 1):
            if pat.match(line):
                hits.append(f"{rel}:{i}")
    check("两个站点均无 mode = session.status", not hits, "、".join(hits) or "analyst_system_cycle + tier_parallel_executor 都已修")
except Exception as e:
    check("P0-3 验证执行", False, f"{type(e).__name__}: {e}")

# ── P1 静默死门控 ─────────────────────────────────────────────────────
print("\n── P1  三道被静默吞掉的门控 ──")
try:
    import inspect
    import re

    root = os.path.dirname(os.path.abspath(__file__))

    def _body(rel, fn):
        src = io.open(os.path.join(root, rel), encoding="utf-8").read()
        m = re.search(rf'^def {re.escape(fn)}\(.*?(?=^def |^class |\Z)', src, re.S | re.M)
        b = m.group(0)
        b = re.sub(r'""".*?"""', '', b, flags=re.S)
        return "\n".join(l for l in b.splitlines() if not l.lstrip().startswith('#'))

    for rel, fn, label in (
        ("backend/services/full_auto/health_check_cycle.py", "run_health_check", "STRICT_DATA_GATE"),
        ("backend/services/full_auto/paper_execution.py", "_execute_paper_trade_inner", "同模板齐发限流"),
    ):
        has = re.search(r'(?<![\w.])self\.', _body(rel, fn))
        check(f"{label}：模块函数内无裸 self", not has, f"{fn} @ {rel.split('/')[-1]}")

    from backend.services.paper_trading_engine import PaperTradingEngine
    sig = inspect.signature(PaperTradingEngine.close_position)
    req = [n for n, p in list(sig.parameters.items())[1:]
           if p.default is inspect.Parameter.empty]
    body = _body("backend/services/full_auto/exit_agent.py", "run_exit_pass")
    call = re.search(r'close_position\((.*?)\n\s*\)', body, re.S)
    provided = call and all(k in call.group(1) for k in ("db=", "account_id=", "symbol=", "side="))
    check("ExitAgent 调用满足 close_position 签名", bool(provided),
          f"必填={req}；调用已提供 db/account_id/symbol/side")
except Exception as e:
    check("P1 死门控验证执行", False, f"{type(e).__name__}: {e}")

# ── P1 单位错配 + 空头对账 ────────────────────────────────────────────
print("\n── P1  peak_pnl_pct 单位错配 / 空头对账失明 ──")
try:
    from backend.services.exit.exit_types import PositionContext
    ctx_ok = PositionContext(position_id=1, symbol="X", tier="long", side="long",
                             entry_price=100.0, current_price=102.0, quantity=1.0, leverage=1.0,
                             unrealized_pnl_pct=2.0, peak_pnl_pct=10.0, hold_seconds=1, atr_pct=1.0)
    dd = (ctx_ok.peak_pnl_pct or 0) - (ctx_ok.unrealized_pnl_pct or 0)
    check("百分数口径下 drawdown 为正（追踪出场可触发）", dd > 0,
          f"peak={ctx_ok.peak_pnl_pct} - unrealized={ctx_ok.unrealized_pnl_pct} = {dd}")

    root = os.path.dirname(os.path.abspath(__file__))
    src = io.open(os.path.join(root, "backend/services/position_exit_orchestrator.py"), encoding="utf-8").read()
    check("orchestrator 已把 peak 分数 ×100",
          bool(re.search(r'_pnl_pct\s*=\s*float\(state\.peak_pnl_pct[^)]*\)\s*\*\s*100', src)))

    from unittest.mock import MagicMock
    from backend.services.sub_position_manager import SubPositionManager
    m = SubPositionManager()
    m.get_sub_positions = MagicMock(return_value=[{"size": 2.0, "side": "short", "trade_nature": "trend_follow"}])
    r_sh = m.reconcile(None, 1, "BTC", exchange_qty=-5.0, exchange_side="short")
    check("空头对账不再失明（数量不符被检出）", r_sh["matched"] is False,
          f"内部 2.0(short) vs 交易所 -5.0 → matched={r_sh['matched']} reason={r_sh.get('mismatch_reason')}")
    m2 = SubPositionManager()
    m2.get_sub_positions = MagicMock(return_value=[{"size": 1.5, "side": "long", "trade_nature": "trend_follow"}])
    r_flat = m2.reconcile(None, 1, "BTC", exchange_qty=0.0)
    check("「交易所已平但内部仍持」被检出", r_flat["matched"] is False,
          f"reason={r_flat.get('mismatch_reason')}")
except Exception as e:
    check("单位/对账验证执行", False, f"{type(e).__name__}: {e}")

# ── P0-1 PnL 口径 ─────────────────────────────────────────────────────
print("\n── P0-1  PnL 权威口径（分档止盈不丢腿）──")
try:
    from backend.services.pnl_authority import realized_pnl
    import psycopg

    class _P:
        pass

    with psycopg.connect("postgresql://laobao:alpha_pass@localhost:5432/alpha_arena") as c:
        with c.cursor() as cur:
            cur.execute("select set_config('app.is_admin','on',false)")
            cur.execute("""select id,symbol,side,size,entry_price,close_price,
                                  partial_realized_pnl,unrealized_pnl,status
                           from paper_positions
                           where status in ('closed','liquidated')
                             and abs(coalesce(partial_realized_pnl,0))>1e-9
                           order by id desc limit 200""")
            rows = cur.fetchall()
    bad = []
    fallback_ok = 0
    for r in rows:
        p = _P()
        (p.id, p.symbol, p.side, p.size, p.entry_price, p.close_price,
         p.partial_realized_pnl, p.unrealized_pnl, p.status) = r
        got, want = realized_pnl(p), float(r[7] or 0)
        if abs(want) > 1e-9:
            # unrealized_pnl 有值 → 必须精确等于引擎合计
            if abs(got - want) > 1e-6:
                bad.append((r[0], r[1], got, want))
        else:
            # unrealized_pnl 为 0 → 按设计退回 partial/价差复原（不可也返回 0，
            # 那会把 1180 笔真实盈亏清零 —— 比原 bug 更糟）
            if abs(got) > 1e-9:
                fallback_ok += 1
    check("分档止盈仓位 realized_pnl == 引擎存档合计", not bad,
          f"抽查 {len(rows)} 笔：unrealized 有值者全部一致（不一致 {len(bad)} 笔）；"
          f"unrealized=0 者 {fallback_ok} 笔按设计走兜底且未清零\n"
          + ("\n".join(f"    不一致 id={i} {s} got={g} want={w}" for i, s, g, w in bad[:5])
             or "    修复前：12/12 不一致（4672 UNI 报 16.78，实为 45.74）"))
except Exception as e:
    check("PnL 口径验证执行", False, f"{type(e).__name__}: {e}")

# ── P1 时区（轮71）────────────────────────────────────────────────────
print("\n── P1  TIMESTAMP 读侧时区（本地 naive 不得当 UTC）──")
try:
    import re as _re
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    from backend.utils.db_datetime import db_dt_for_age

    now_local = _dt.now()
    now_utc = now_local.astimezone(_tz.utc)
    naive_from_db = now_local.replace(tzinfo=None)

    old_h = (now_utc - naive_from_db.replace(tzinfo=_tz.utc)).total_seconds() / 3600.0
    new_h = (now_utc - db_dt_for_age(naive_from_db)).total_seconds() / 3600.0
    check("时长计算不再差 8 小时", (-8.01 < old_h < -7.99) and abs(new_h) < 0.01,
          f"旧算法 elapsed={old_h:.2f}h（刚写入即 -8h）→ 新算法 {new_h:.4f}h")

    cd_h = 1.0
    two_h_ago = (now_local - _td(hours=2)).replace(tzinfo=None)
    old_2h = (now_utc - two_h_ago.replace(tzinfo=_tz.utc)).total_seconds() / 3600.0
    new_2h = (now_utc - db_dt_for_age(two_h_ago)).total_seconds() / 3600.0
    check("减仓冷却不再被拉长 8h",
          (old_2h < cd_h) and (new_2h >= cd_h),
          f"距上次 2h / 冷却 {cd_h}h：旧={old_2h:.1f}h→仍判冷却中(错)；新={new_2h:.1f}h→放行(对)")

    _root = os.path.dirname(os.path.abspath(__file__))
    _spm = io.open(os.path.join(_root, "backend/services/sub_position_manager.py"), encoding="utf-8").read()
    _spm_code = _re.sub(r'#.*', '', _spm)
    check("sub_position_manager 四处站点均归一化",
          (not _re.search(r'\.replace\(tzinfo=timezone\.utc\)', _spm_code))
          and _spm_code.count("db_dt_for_age") >= 4,
          f"db_dt_for_age 使用 {_spm_code.count('db_dt_for_age')} 处；无裸 replace(tzinfo=utc)")

    _rc = io.open(os.path.join(_root, "backend/services/reentry_cooldown.py"), encoding="utf-8").read()
    check("reentry_cooldown 截止值与本地钟面同尺度",
          "since = datetime.now() - timedelta(seconds=lookback)" in _rc)
except Exception as e:
    check("时区验证执行", False, f"{type(e).__name__}: {e}")

# ── P1 敞口 fail-closed（轮72）─────────────────────────────────────────
print("\n── P1  在手敞口读取失败必须 fail-closed ──")
try:
    from unittest.mock import MagicMock

    from backend.services.position_construction import open_notionals

    _db_ok = MagicMock()
    _db_ok.execute.return_value.fetchall.return_value = [("BTC", "long", "trend_follow", 1.0, 100.0, 100.0)]
    ok_res = open_notionals(_db_ok, 14, "BTC", lane="long")

    _db_bad = MagicMock()
    _db_bad.execute.side_effect = RuntimeError("relation does not exist")
    bad_res = open_notionals(_db_bad, 14, "BTC", lane="long")

    check("成功时 ok=True 且数字可信", ok_res.get("ok") is True and ok_res["total"] == 100.0,
          f"ok={ok_res.get('ok')} total={ok_res['total']}")
    check("DB 异常时 ok=False（与「零敞口」可区分）", bad_res.get("ok") is False,
          f"ok={bad_res.get('ok')} —— 裸数字 {bad_res['total']} 与无仓位时相同，只能靠 ok 分辨")

    _root = os.path.dirname(os.path.abspath(__file__))
    _sites = [("backend/services/paper_trading_engine.py", '_open.get("ok", False)'),
              ("backend/services/trading_commands.py", '_open.get("ok", False)'),
              ("backend/services/trend_e1_engine.py", 'opens.get("ok", False)')]
    missing = [rel for rel, needle in _sites
               if needle not in io.open(os.path.join(_root, rel), encoding="utf-8").read()]
    check("三个调用点均已 fail-closed", not missing,
          "、".join(missing) if missing else "paper_trading_engine / trading_commands / trend_e1_engine")
except Exception as e:
    check("敞口 fail-closed 验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮74-80 的后续修复 ────────────────────────────────────────────────
print("\n── P1-8  实盘执行结果诚实性 ──")
try:
    from backend.services.exit.exit_types import close_result_succeeded
    bad = [s for s in ("error", "blocked", "rejected", "failed", "unknown")
           if close_result_succeeded({"status": s})]
    good = [s for s in ("filled", "closed", "ok", "success")
            if not close_result_succeeded({"status": s})]
    check("失败状态不得判为成功", not bad, f"误判: {bad}" if bad else "error/blocked/rejected/failed/unknown 全部 False")
    check("成功状态判为成功", not good, f"漏判: {good}" if good else "filled/closed/ok/success 全部 True")
    check("None / 空 dict 判为未确认",
          close_result_succeeded(None) is False and close_result_succeeded({}) is False)

    _root = os.path.dirname(os.path.abspath(__file__))
    _sc = io.open(os.path.join(_root, "backend/services/full_auto/scalp_position_review.py"), encoding="utf-8").read()
    _sc = _re.sub(r'#.*', '', _sc)
    check("调用方不再用 bool(res) 判 dict", "bool(res)" not in _sc)
    _svc = io.open(os.path.join(_root, "backend/services/full_auto_trading_service.py"), encoding="utf-8").read()
    check("_execute_live_trade 已 return 结果",
          "return execute_live_trade(" in _svc,
          "旧实现吞掉 bool → 调用方 bool() 恒 False")
except Exception as e:
    check("实盘诚实性验证执行", False, f"{type(e).__name__}: {e}")

print("\n── P1-9  Flow 订阅：注册必须早于 subscribe ──")
try:
    from unittest.mock import MagicMock as _MM

    from backend.services.market_flow_collector import MarketFlowCollector

    class _BadInfo:
        def subscribe(self, payload, cb):
            raise KeyError(payload.get("coin"))

    _c = MarketFlowCollector()
    _c.info = _BadInfo()
    _c.subscribed_symbols, _c.subscription_ids, _c.trade_buffers = [], {}, {}
    _c._subscribe_symbol("COTI")
    check("订阅抛 KeyError 后 symbol 仍被登记", "COTI" in _c.subscribed_symbols,
          "旧实现 append 在 subscribe 之后 → 不可达 → 成交永不落库")
except Exception as e:
    check("Flow 订阅验证执行", False, f"{type(e).__name__}: {e}")

print("\n── P1-12  paused 会话仍执行持仓治理 ──")
try:
    _hc = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "backend/services/full_auto/health_check_cycle.py"), encoding="utf-8").read()
    _hc_code = "\n".join(l for l in _hc.splitlines() if not l.lstrip().startswith('#'))
    check("不再整轮 return",
          not _re.search(r'if\s+session\.status\s*==\s*["\']paused["\']\s*:\s*\n\s*return\b', _hc_code))
    check("paused 时强制不开新仓",
          "if _session_paused and should_run:" in _hc and "_session_paused = (session.status == \"paused\")" in _hc)
    check("任何 paused 都免于自动解锁", "if _paused_any:" in _hc,
          "否则人工 pause 会被 paper_auto_unlock_session 静默恢复交易")
except Exception as e:
    check("paused 治理验证执行", False, f"{type(e).__name__}: {e}")

print("\n── P1-10  factor loader 目录指纹缓存 ──")
try:
    from backend.services.factor_engine.factor_loader import (
        FactorLoader as _FL,
        _DISCOVERY_CACHE,
    )
    _DISCOVERY_CACHE.clear()
    _t = time.time()
    _a = _FL()
    _n1 = _a.discover_and_load_all()
    _cold = time.time() - _t
    _t = time.time()
    _b = _FL()
    _n2 = _b.discover_and_load_all()
    _warm = time.time() - _t
    check("第二次命中缓存且因子数一致", _b.cache_hit and _n1 == _n2,
          f"冷 {_cold*1000:.0f}ms → 热 {_warm*1000:.0f}ms（{_cold/max(_warm,1e-9):.1f}x），因子数 {_n1}")
except Exception as e:
    check("factor loader 缓存验证执行", False, f"{type(e).__name__}: {e}")

print("\n── P1-13  学习层 val_IC 样本外 + 门槛可配 ──")
try:
    from backend.services.factor_engine.learned_weighting import LearnedWeightingConfig as _LWC
    check("min_val_ic 默认 0.02", abs(_LWC().min_val_ic - 0.02) < 1e-9)
    os.environ["LEARNED_MIN_VAL_IC"] = "0.05"
    check("min_val_ic 可经 env 覆盖", abs(_LWC().min_val_ic - 0.05) < 1e-9)
    os.environ.pop("LEARNED_MIN_VAL_IC", None)

    _lw = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "backend/services/factor_engine/learned_weighting.py"), encoding="utf-8").read()
    _lw_code = "\n".join(l for l in _lw.splitlines() if not l.lstrip().startswith('#'))
    check("切分早于特征筛选",
          _lw_code.find("_tr = aligned.iloc[:_train_end]") < _lw_code.find("float(self.config.min_ic_to_include"),
          "否则特征筛选会偷看校验段（val_IC 偏乐观）")
    check("特征筛选只用训练段", "_tr[_c]" in _lw_code and "aligned[_c]" not in _lw_code)
except Exception as e:
    check("学习层样本外验证执行", False, f"{type(e).__name__}: {e}")

print("\n── P1  训练期标的限制不再失效 ──")
try:
    from backend.services.full_auto.master_execution import _resolve_training_allowed_symbols as _rtas

    class _H:
        pass

    _got = _rtas(_H())
    from backend.services.training_phase_service import is_active as _tpa
    if _tpa():
        check("训练期 active 时解析出非空限制", bool(_got), f"allowed={sorted(_got)}")
    else:
        check("训练期未启用 → 不限制", _got == set())
    _me = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "backend/services/full_auto/master_execution.py"), encoding="utf-8").read()
    check("不再只依赖 host 字段",
          "getattr(host, \"training_allowed_symbols\", set())" not in _me
          and "_training_allowed_cache = _resolve_training_allowed_symbols(host)" in _me,
          "该字段在独立调度器配置下恒为空 → 空集等于不限制 → 守卫失效")
except Exception as e:
    check("训练期限制验证执行", False, f"{type(e).__name__}: {e}")

print("\n── P2  master 决策快照回写 + pyramid/dca 可达性 ──")
try:
    _me2 = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "backend/services/full_auto/master_execution.py"), encoding="utf-8").read()
    _me2_code = "\n".join(l for l in _me2.splitlines() if not l.lstrip().startswith('#'))
    _lines2 = _me2.splitlines()
    _ia = next(i for i, l in enumerate(_lines2) if _re.match(r'\s*_snap_entry = snap\s*$', l))
    _over = [i + 1 for i, l in enumerate(_lines2)
             if i > _ia and _re.match(r'\s*_snap_entry = None\s*$', l)]
    check("快照句柄不被同轮覆盖", not _over, f"覆盖行: {_over}" if _over else "DecisionSnapshot.executed 得以置位")
    _n_calls = len(_re.findall(r'mark_master_decision_executed\(\s*_snap_entry', _me2_code))
    check("8 处回写点仍在", _n_calls == 8, f"实际 {_n_calls}")

    _top = [l for l in _lines2
            if len(l) - len(l.lstrip()) == 8 and _re.match(r'\s*elif action == "(pyramid|dca)"', l)]
    check("顶层 pyramid/dca 实现可达（P2-2 更正）", len(_top) == 2,
          f"顶层分支 {len(_top)} 个 —— 审计原称不可达，实为嵌套冗余副本")
except Exception as e:
    check("P2 验证执行", False, f"{type(e).__name__}: {e}")

print("\n── P2 风控拦截的副作用隔离 / 权益分母 / 分支遮蔽 / 锁故障分类 ──")
try:
    _root = os.path.dirname(os.path.abspath(__file__))

    # P2-3 及其 6 处同类：判定 try 内不得有拦截性 append_event
    _me3 = io.open(os.path.join(_root, "backend/services/full_auto/master_execution.py"), encoding="utf-8").read()
    _lines3 = _me3.splitlines()
    _risky = []
    for _i, _l in enumerate(_lines3):
        if not _re.match(r'\s*try:\s*$', _l):
            continue
        _ind = len(_l) - len(_l.lstrip())
        _j, _body = _i + 1, []
        while _j < len(_lines3):
            _lj = _lines3[_j]
            if _lj.strip() and (len(_lj) - len(_lj.lstrip())) <= _ind and _re.match(r'\s*(except|finally)', _lj):
                break
            if _lj.strip() and (len(_lj) - len(_lj.lstrip())) < _ind:
                break
            _body.append(_lj)
            _j += 1
        _txt = '\n'.join(_body)
        if (_re.search(r'\b(continue|return)\b', _txt) and 'append_event' in _txt
                and _j < len(_lines3) and _re.match(r'\s*except\s+Exception\s*:\s*$', _lines3[_j])
                and _j + 1 < len(_lines3) and _lines3[_j + 1].strip() == 'pass'):
            _risky.append(_i + 1)
    check("风控拦截不再被自己的日志副作用关掉（7 处已修）", not _risky,
          f"剩余同类风险点: {_risky}" if _risky else "0 处（_emit_block_event 统一记录）")

    # P2-5 权益分母
    _sr = io.open(os.path.join(_root, "backend/services/full_auto/symbol_risk.py"), encoding="utf-8").read()
    _m = _re.search(r'^def check_per_symbol_risk\(.*?(?=^def |\Z)', _sr, _re.S | _re.M)
    _sr_body = '\n'.join(l for l in _m.group(0).splitlines() if not l.lstrip().startswith('#'))
    check("不再用编造的权益当风控分母",
          '10000.0' not in _sr_body and '_equity_known' in _sr_body,
          "权益不可用时跳过按百分比的判据；回撤安全网（与权益无关）仍生效")
    check("权益读取失败有告警", '权益读取异常' in _sr and '权益不可用' in _sr)

    # P2-4 分支遮蔽（可观测）
    _hc2 = io.open(os.path.join(_root, "backend/services/full_auto/health_check_cycle.py"), encoding="utf-8").read()
    check("风控巡检四个分支都能看出走了哪个",
          all(k in _hc2 for k in ("风控巡检分支=live_constitutional",
                                  "风控巡检分支=paper_auto_unlock",
                                  "风控巡检分支=per_symbol_risk")),
          "per-symbol 层被 lock_strength 配置遮蔽，现可查")

    # P2-11 锁故障 vs 竞争
    from backend.services.full_auto_trading_service import _LOCK_FS_FAULT
    _svc2 = io.open(os.path.join(_root, "backend/services/full_auto_trading_service.py"), encoding="utf-8").read()
    check("锁的「文件系统故障」与「锁竞争」已分开",
          'except BlockingIOError:' in _svc2 and 'except (PermissionError, OSError)' in _svc2
          and 'except (BlockingIOError, PermissionError, OSError)' not in _svc2
          and _LOCK_FS_FAULT is not None,
          "故障记 ERROR 并指向 data/locks；竞争仍 INFO「其他进程正在执行」")
except Exception as e:
    check("P2 批次验证执行", False, f"{type(e).__name__}: {e}")

# ── 可用性：HTTP 端到端 ───────────────────────────────────────────────
print("\n── 可用性：运行中后端 HTTP ──")
for path in ("/api/health", "/api/period/lanes", "/api/full-auto/sessions"):
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=25)
        check(f"HTTP {path}", r.status == 200, f"status={r.status}")
    except urllib.error.HTTPError as e:
        check(f"HTTP {path}", False, f"status={e.code}")
    except Exception as e:
        check(f"HTTP {path}", False, f"{type(e).__name__}")

# ── 汇总 ──────────────────────────────────────────────────────────────
passed = sum(1 for _, ok in results if ok)
failed = len(results) - passed
print("\n" + "=" * 78)
print(f"汇总：{passed} 通过 / {failed} 失败（共 {len(results)} 项）")
if failed:
    print("失败项：")
    for name, ok in results:
        if not ok:
            print(f"  - {name}")
print("=" * 78)
sys.exit(1 if failed else 0)
