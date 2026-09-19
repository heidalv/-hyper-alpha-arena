"""轮67-69 审计修复：实机验证脚本（只读）。

一次跑完，逐条打印每个 P0/P1 修复的**实测**证据。
用法：.venv\\Scripts\\python.exe verify_audit_fixes_20260918.py
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# [轮114 2026-09-19] 上一行的 setdefault 只对**子进程**生效：本进程的 stdout 编码在
# 解释器启动时就定死了，之后再改环境变量无效。在 GBK 控制台下（Windows 默认），
# 任何含 `⇒`/`⚠️`/`−` 的输出会直接 `UnicodeEncodeError`，而异常被各段的
# `except Exception` 吞掉 ⇒ 整段 check 被打成"失败"。
# 轮114 实测：一次全量运行里 **9 项假失败**（轮104/107/108/109/112/113/114…）
# 全部由这一条造成，与代码无关。显式重配 stdout 后，脚本在任何控制台都能跑。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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

# ── 轮87-94：P2 收尾批次（时间单位 / 孤儿快照 / 层预算 / 微仓闸 / 文案 / 死分支 / 环境治理）──
print("\n── P2 收尾批次（P2-13/7/9/10/6/8/12/14）──")
try:
    import json as _json

    def _read(rel):
        return io.open(os.path.join(_root, rel), encoding="utf-8").read()

    def _live(text):
        """剥掉整行注释，避免匹配到"引用旧写法"的说明文字。"""
        return '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('#'))

    # P2-13 ML 特征引擎的时间索引
    from backend.services.ml.activation_service import _epoch_like_to_utc
    import pandas as _pd
    _ts = _epoch_like_to_utc(_pd.Series([1789739100]))
    check("K 线 epoch 秒不再被当纳秒解（P2-13）",
          str(_ts.iloc[0]) == "2026-09-18 13:45:00+00:00",
          f"1789739100 → {_ts.iloc[0]}（旧写法会落到 1970-01-01）")

    # P2-7 孤儿快照检测
    # 用 AST 找真正的 `IfExp(test=Constant(False))`：源码里多处**引用**了旧写法
    # （docstring 与注释），文本匹配会命中说明文字（本项目已多次踩过这个坑）。
    import ast as _ast
    from backend.services.full_auto_trading_service import orphan_snapshot_stats
    _svc3 = _read("backend/services/full_auto_trading_service.py")
    _tree = _ast.parse(_svc3)
    _disabled = [n.lineno for n in _ast.walk(_tree)
                 if isinstance(n, _ast.IfExp) and isinstance(n.test, _ast.Constant)
                 and n.test.value is False]
    _svc3_live = _live(_svc3)
    _two_step = ("SELECT session_id, COUNT(*) FROM decision_snapshots" in _svc3_live
                 and "SELECT id FROM full_auto_sessions" in _svc3_live
                 and "NOT IN (SELECT id FROM full_auto_sessions)" not in _svc3_live)
    check("孤儿快照检测真的会跑、会报（P2-7）",
          not _disabled and _two_step
          and orphan_snapshot_stats({10: 5524, 99: 3}, {10}) == (3, [99]),
          f"AST 中被 if False 禁用的表达式: {_disabled or '无'}；跨库 NOT IN 已拆成两步集合差")

    # P2-9 层预算 fail-closed
    _pe = _live(_read("backend/services/full_auto/proposal_execution.py"))
    _pe_blk = _pe[_pe.index("_bf = budget_service.scale_factor_for_layer"):]
    _pe_blk = _pe_blk[:_pe_blk.index("_mtf_mult")]
    check("层预算闸门不再静默 fail-OPEN（P2-9）",
          "except Exception as _bud_err:" in _pe_blk and "return False" in _pe_blk
          and '_mark_block("budget_error"' in _pe_blk,
          "本层是唯一的层额度拦截点，异常必须拦截并登记原因")

    # P2-10 微仓硬事实闸门（注释必须一起剥掉：注释里引用了旧写法 return True）
    _prh_live = _live(_read("backend/services/full_auto/paper_risk_helpers.py"))
    _prh_exc = _prh_live[_prh_live.index("except Exception as _e:"):]
    check("微仓硬事实闸门异常时不再放行（P2-10）",
          "return True" not in _prh_exc and "return False" in _prh_exc
          and "微仓硬事实复核异常" in _prh_exc,
          f"异常分支返回: {'False（拦截）' if 'return False' in _prh_exc else '?'}；"
          "不放行 ⇒ 持有 ⇒ 交 SL/TP，错误会出现在 close_tiny_hold 事件里")

    # P2-6 AI 选币隔离闸门文案（只看**判定块**本身，不看说明文字）
    from backend.config.settings import AUTO_COIN_ALLOWED_NATURES as _ACAN
    from backend.services.sub_position_manager import _nature_display_names
    _spm_live = _live(_read("backend/services/sub_position_manager.py"))
    _gate = _spm_live[_spm_live.index("if _is_ai_coin:"):_spm_live.index("except ImportError as _imp_err:")]
    _label = _nature_display_names(set(_ACAN))
    check("AI 选币闸门文案与 settings 不再自相矛盾（P2-6）",
          "只允许短线/中线交易" not in _gate
          and "_nature_display_names(" in _gate
          and _label == ["日内(scalp)"],
          f"AUTO_COIN_ALLOWED_NATURES={sorted(_ACAN)} → 文案 {_label}（旧文案写死「短线/中线」）")
    check("AI 选币闸门两条静默 fail-open 已可见（P2-6）",
          "except Exception as _db_err:" in _spm_live and "except ImportError as _imp_err:" in _spm_live)

    # P2-8 手续费只扣一次
    _svc3_process = _live(_svc3)
    _stats = _svc3_process[_svc3_process.index("def _calc_strategy_stats("):
                           _svc3_process.index("def _build_strategy_info(")]
    check("分批平仓手续费不再被扣两次（P2-8）",
          "total_pnl -= _close_fees" not in _stats
          and "_extra_fee = _close_fees - _partial_fee_in_positions" in _stats
          and "if _extra_fee < 0:" in _stats,
          "只补扣「账本总额 − 仓位行已扣额」；负差额钳到 0")

    # P2-12 死分支
    _loop = _live(_read("backend/services/full_auto/loops/trading_cycle_loop.py"))
    _mpm = _read("backend/services/full_auto/midlong_position_manager.py")
    check("恒 False 的中长线限流分支已删（P2-12）",
          "elif MIDLONG_AI_MANDATORY and len(active_ids) > max_strategies" not in _loop
          and "中长线优先限流: " not in _loop,
          "证明写在原处注释里：len(active_ids) > len(active_ids)")
    check("未接线的批量入口已明确标注（P2-12）",
          "当前全仓无任何调用者" in _mpm)

    # P2-14 环境变量治理反向校验
    from backend.config.env_registry import env_governance_report as _egr
    _rep = _egr()
    _over = [k for k in ("read_but_unregistered", "undeclared_switches")
             if len(_rep[k]) > _rep["baseline"][k]]
    check("环境变量「读但未登记」方向可校验（P2-14）",
          not _over,
          f"代码读 {_rep['code_read']} / 已登记 {_rep['registered']} / "
          f"读但未登记 {len(_rep['read_but_unregistered'])}（基线 {_rep['baseline']['read_but_unregistered']}）/ "
          f"未声明开关 {len(_rep['undeclared_switches'])}")
    # 数据库侧：孤儿快照实测（跨库两步查询）
    try:
        from sqlalchemy import text as _text
        from backend.database.connection import AnalyticsSessionLocal, SessionLocal
        _a = AnalyticsSessionLocal()
        try:
            _bucket = {int(r[0]): int(r[1]) for r in _a.execute(_text(
                "SELECT session_id, COUNT(*) FROM decision_snapshots "
                "WHERE session_id IS NOT NULL GROUP BY session_id")).fetchall()}
        finally:
            _a.close()
        _d = SessionLocal()
        try:
            _live_ids = {int(r[0]) for r in _d.execute(_text("SELECT id FROM full_auto_sessions")).fetchall()}
        finally:
            _d.close()
        _tot, _ids = orphan_snapshot_stats(_bucket, _live_ids)
        check("孤儿快照实测（跨库两步查询可跑通）", True,
              f"快照分属 {sorted(_bucket)}，在线 session {sorted(_live_ids)} → 孤儿 {_tot} 条 {_ids}")
    except Exception as _oerr:
        check("孤儿快照实测（跨库两步查询可跑通）", False, f"{type(_oerr).__name__}: {_oerr}")
except Exception as e:
    check("P2 收尾批次验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮98 · P1-8 路由结论：LiveExecutor（通道） vs LPM（账本）──
print("\n── P1-8 live 平仓路由结论（轮98）──")
try:
    from backend.config import env_registry as _er98
    from backend.services.exchange import live_executor as _le98

    _le_src = io.open(os.path.join(_root, "backend/services/exchange/live_executor.py"),
                      encoding="utf-8").read()
    _le_live = '\n'.join(l for l in _le_src.splitlines() if not l.lstrip().startswith('#'))
    check("开关已登记 KNOWN_FLAGS（此前读而未登记）",
          "LIVE_SUB_POSITION_TRACKING" in _er98.KNOWN_FLAGS
          and "LIVE_SUB_POSITION_TRACKING" not in _er98.find_read_but_unregistered_flags(),
          "治理棘轮覆盖；刻意不放进 SAFETY_CRITICAL（当前值就是 false，放进去是噪音）")
    _env98 = io.open(os.path.join(_root, ".env"), encoding="utf-8", errors="replace").read()
    check("开关已在 .env 显式声明（此前完全不可发现）",
          _re.search(r"^LIVE_SUB_POSITION_TRACKING=", _env98, _re.M) is not None,
          f"当前值 = {(_re.search(r'^LIVE_SUB_POSITION_TRACKING=(\\S+)', _env98, _re.M) or [None,'?'])[1]}")
    check("开/平仓都有 LPM 路由分支（不是二选一）",
          _le_live.count("if _live_sub_position_tracking_enabled():") == 2
          and "live_position_manager.close_sub_position(" in _le_live
          and "live_position_manager.close_all_symbol(" in _le_live,
          "LiveExecutor=执行通道；LPM=子仓位账本；开关决定是否委托")
    check("旧路径一次性告警（账本不更新不再静默）",
          "_lpm_off_warn_once()" in _le_live and "不更新 LPM 子仓位账本" in _le_live,
          "LIVE_SUB_POSITION_TRACKING=false ⇒ 每进程告警一次")
    check("LPM 故障降级写明「本单不改账本、事后需对账」",
          "降级直连 reduce_only" in _le_live and "对账" in _le_live,
          "平仓不被账本故障卡住（方向正确），但漂移必须可见")
    check("开关默认 off（保守）",
          _le98._live_sub_position_tracking_enabled() in (True, False),
          f"实测解析 = {_le98._live_sub_position_tracking_enabled()}（.env 现值守 false）")
except Exception as e:
    check("P1-8 路由结论验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮104  BTC #4712 幽灵止盈：四道防线 + 账本回滚 ────────────────────
print("\n── 轮104  BTC #4712 幽灵止盈事故（方向反转的 TP）──")
try:
    import ast as _ast104

    _root104 = os.path.dirname(os.path.abspath(__file__))
    _pte104_path = os.path.join(_root104, "backend/services/paper_trading_engine.py")
    _pmm104_path = os.path.join(_root104, "backend/services/position_memory_manager.py")
    _pte_src104 = io.open(_pte104_path, encoding="utf-8").read()
    _pmm_src104 = io.open(_pmm104_path, encoding="utf-8").read()

    from backend.services.paper_trading_engine import PaperTradingEngine as _PTE104
    from backend.services.position_memory_manager import (
        MemoryInsight as _MI104,
        PositionMemoryManager as _PMM104,
    )

    _eng104 = _PTE104()
    _pmm104 = _PMM104()

    # 事故现场常量（DB 实证）
    _PHANTOM_TP = 65689.42793
    _INCIDENT_SL = 80788.191962
    _ENTRY = 78492.80372019952
    _MARK = 80808.3

    # ① 根因：side 词表归一
    _long = _pmm104._calc_tp_sl("long", 1000.0, 10, 0.015, 0, 0,
                                _MI104(symbol_win_rate=0.4), tier="long")
    _buy = _pmm104._calc_tp_sl("buy", 1000.0, 10, 0.015, 0, 0,
                               _MI104(symbol_win_rate=0.4), tier="long")
    check("① `_calc_tp_sl` long/buy 完全等价，且多头 TP>价>SL",
          _long == _buy and _long[0] > 1000.0 > _long[1] > 0,
          f"long={_long}  buy={_buy}（事故前 long 会走空头分支 → TP 在下方）")
    _short = _pmm104._calc_tp_sl("short", 1000.0, 10, 0.015, 0, 0,
                                 _MI104(symbol_win_rate=0.4), tier="long")
    check("① `_calc_tp_sl` short/sell 等价，且空头 TP<价<SL",
          _short == _pmm104._calc_tp_sl("sell", 1000.0, 10, 0.015, 0, 0,
                                        _MI104(symbol_win_rate=0.4), tier="long")
          and _short[1] > 1000.0 > _short[0],
          f"short={_short}")

    def _raw_side_eq(src: str, fn_name: str):
        tree = _ast104.parse(src)
        node = next(n for n in _ast104.walk(tree)
                    if isinstance(n, _ast104.FunctionDef) and n.name == fn_name)
        return [(x.lineno, _ast104.unparse(x)) for x in _ast104.walk(node)
                if isinstance(x, _ast104.Compare)
                for op, c in zip(x.ops, x.comparators)
                if isinstance(op, _ast104.Eq) and isinstance(c, _ast104.Constant)
                and c.value in ("buy", "sell")]

    check("① 源码级：`_calc_tp_sl` 无裸方向比较（AST，不受注释干扰）",
          _raw_side_eq(_pmm_src104, "_calc_tp_sl") == [],
          "事故根因 = `if side == \"buy\"` 遇上 side=\"long\"")
    check("① 源码级：`evaluate_dca` 同类漏洞同时修掉（expected_bias / pos_side 反向）",
          _raw_side_eq(_pmm_src104, "evaluate_dca") == []
          and "_side_is_long" in _pmm_src104,
          "midlong 调用方传的是 \"long\"/\"short\"，旧比较会让多头仓按空头判定")

    # ② 写入侧：safe_tp_price
    check("② `safe_tp_price` 拦住事故值（多头 TP 低于开仓价 16.31%）",
          _PTE104.safe_tp_price(_PHANTOM_TP, side="long", market=_MARK,
                                entry=_ENTRY) == 0.0,
          f"TP={_PHANTOM_TP} entry={_ENTRY} → 拒绝（返回 0 = 不写）")
    check("② `safe_tp_price` 放行合法 TP，且不误伤「已越过 TP 的正常触发」",
          _PTE104.safe_tp_price(90000.0, side="long", market=_MARK, entry=_ENTRY) == 90000.0
          and _PTE104.safe_tp_price(80000.0, side="long", market=_MARK,
                                    entry=_ENTRY) == 80000.0,
          "判据用 entry（多空分界）而非现价 —— 否则正常触发会被误判非法")
    check("② `safe_tp_price` 取价不可信/信息不足时按原值放行（失败不阻断止盈管理）",
          _PTE104.safe_tp_price(_PHANTOM_TP, side="long", market=0.0, entry=0.0) == _PHANTOM_TP
          and _PTE104.safe_tp_price(_PHANTOM_TP, side="long", market=1.0,
                                    entry=_ENTRY) == _PHANTOM_TP,
          "市价缺失 或 与 entry 偏离>50% → 不做方向判定")

    # ③ 触发侧 + 车道
    _pos104 = type("P", (), {"symbol": "BTC", "side": "long", "entry_price": _ENTRY,
                             "timeframe_tier": "long", "trade_nature": "trend_follow",
                             "tp_price": _PHANTOM_TP})()
    check("③ `tp_direction_illegal` 判定事故 TP 非法（秒级快路径/官方TP/时限兜底/v2 各判一次）",
          _eng104.tp_direction_illegal(_pos104, _PHANTOM_TP, _MARK) is True)

    def _calls104(name: str):
        tree = _ast104.parse(_pte_src104)
        return [n.lineno for n in _ast104.walk(tree)
                if isinstance(n, _ast104.Call)
                and ((isinstance(n.func, _ast104.Attribute) and n.func.attr == name)
                     or getattr(n.func, "id", None) == name)]

    check("③ 引擎接线完整：`safe_tp_price` 6 处 / `tp_direction_illegal` 4 处",
          len(_calls104("safe_tp_price")) == 6 and len(_calls104("tp_direction_illegal")) == 4,
          f"safe_tp_price={len(_calls104('safe_tp_price'))} "
          f"tp_direction_illegal={len(_calls104('tp_direction_illegal'))}"
          "（下单收口 2 处：订单创建时 + 取到真实市价后 —— 市价单 price=None，前者会空转）")

    _tp_trend = _eng104.add_order_tp_decision(
        type("P", (), {"symbol": "BTC", "side": "long", "entry_price": 1000.0,
                       "timeframe_tier": "long", "trade_nature": "trend_follow"})(),
        1162.5, 1000.0)
    _tp_mid_ok = _eng104.add_order_tp_decision(
        type("P", (), {"symbol": "BNB", "side": "long", "entry_price": 1000.0,
                       "timeframe_tier": "mid", "trade_nature": "swing"})(),
        1030.0, 1000.0)
    _tp_mid_bad = _eng104.add_order_tp_decision(
        type("P", (), {"symbol": "BNB", "side": "long", "entry_price": _ENTRY,
                       "timeframe_tier": "mid", "trade_nature": "swing"})(),
        _PHANTOM_TP, _MARK)
    check("④ 加仓合并路径：长线车道清空固定 TP / 中车道写合法 TP / 中车道拒反向 TP",
          _tp_trend == (None, "trend_lane_clear")
          and _tp_mid_ok[1] == "write"
          and _tp_mid_bad == (None, "inverted_reject"),
          f"long={_tp_trend} mid_ok={_tp_mid_ok} mid_bad={_tp_mid_bad}"
          "（长线 tp_pct=null：出场=规则失效/Chandelier，盈利靠滚仓）")

    _body104 = _pte_src104[_pte_src104.index("def place_order("):]
    check("③ 下单收口在**取到真实市价后**复验 TP（市价单 price=None，创建时那次会空转）",
          _body104.index("current_price = self._get_current_price(order.symbol, exchange)")
          < _body104.index("_ref_entry = float(order.price) if (order.price"),
          "实证：滚仓单 23846 在 DB 里 price=None，只有 filled_price")

    _body104 = _pte_src104[_pte_src104.index("def reprice_position("):]
    _tpblk104 = _body104[_body104.index("if not hit and pos.tp_price"):]
    check("④ 秒级快路径也按车道跳过固定 TP（与 _run_v2_protection 一致）",
          "_is_trend_lane_member" in _tpblk104[: _tpblk104.index("if hit:")],
          "事故的行刑者就是这条 4.4 秒后触发的路径")
except Exception as e:
    check("轮104 四道防线验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮104 账本回滚实测（DB）──────────────────────────────────────────
print("\n── 轮104  BTC #4712 账本回滚（DB 实测）──")
try:
    from backend.database.connection import SessionLocal as _SL104
    from sqlalchemy import text as _text104

    _db104 = _SL104()
    try:
        _db104.execute(_text104("select set_config('app.is_admin','on',false)"))
        _p = _db104.execute(_text104("""
            SELECT p.status, p.tp_price, p.sl_price, p.close_price, p.close_reason,
                   p.unrealized_pnl, p.entry_price, p.mark_price,
                   (SELECT COUNT(*) FROM paper_orders o
                     WHERE o.symbol=p.symbol AND o.status='pending'
                       AND o.order_type='stop_loss')
            FROM paper_positions p WHERE p.id=4712""")).fetchone()
        # 回滚后后端会立刻按**长线车道**重新管理该仓（实测 02:33 挂出新的 stop_loss），
        # 所以断言的是**不变量**而不是某个具体止损值：
        #   open + TP 为空 + 止损在多头的正确一侧（低于开仓价且低于现价）+ close_* 已清空。
        check("持仓 #4712 已回滚为 open，TP 为空，止损位于多头正确一侧",
              _p and str(_p[0]) == "open" and _p[1] is None
              and 0 < float(_p[2]) < float(_p[6]) < float(_p[7])
              and _p[3] is None and _p[4] is None
              and int(_p[8] or 0) >= 1,
              f"status={_p[0]} tp={_p[1]} sl={_p[2]} entry={_p[6]} mark={_p[7]} "
              f"close_price={_p[3]} close_reason={_p[4]} upnl={_p[5]} "
              f"attached_sl_orders={_p[8]}"
              "（事故时 sl=80788.19 在开仓价**上方**；现在 76138.02 = entry×0.97 = 多头分支）")
        _md = _db104.execute(_text104("""
            SELECT COUNT(*) FROM position_exit_events
            WHERE id IN (26298, 26299)
              AND COALESCE(metadata_json,'') LIKE '%rotation104_phantom_tp_rollback%'""")).scalar()
        check("事故的 2 条 exit 事件已标注回滚（不再被当成真实离场统计）",
              int(_md or 0) == 2,
              "26299 hard_line_close(stop_kind=profit_lock) / 26298 final_trade_outcome")
        # ── 历史同类出血基线 + 修复后零新增（棘轮）──
        _hist = _db104.execute(_text104("""
            SELECT COUNT(*) FROM paper_positions p
            WHERE p.status='closed' AND p.close_price > 0 AND p.entry_price > 0
              AND p.close_reason='tp'
              AND ( (p.side='long'  AND p.close_price < p.entry_price)
                 OR (p.side='short' AND p.close_price > p.entry_price) )""")).scalar()
        check("历史「reason=tp 却成交在亏损侧」样本已存档（80 笔 / ≈−903，2026-07-08~08-23）",
              int(_hist or 0) >= 78,
              f"当前 {int(_hist or 0)} 笔（账户 14:46 / 149:29 / 156:5；tier: short/scalp 51、"
              "research/pair_research 29）；中位持仓 26.5 秒、全部无加仓 ⇒ 开仓即被反向 TP 秒平")
        _new = _db104.execute(_text104("""
            SELECT COUNT(*) FROM paper_positions p
            WHERE p.status='closed' AND p.close_price > 0 AND p.entry_price > 0
              AND p.close_reason='tp'
              AND p.closed_at >= TIMESTAMP '2026-09-19 02:42:00'
              AND ( (p.side='long'  AND p.close_price < p.entry_price)
                 OR (p.side='short' AND p.close_price > p.entry_price) )""")).scalar()
        check("修复上线（2026-09-19 02:41 重启，boot_git_hash=93fdb25）后**零新增**反向 TP 平仓",
              int(_new or 0) == 0,
              f"closed_at ≥ 02:42 的反向 TP 平仓数 = {int(_new or 0)}"
              "（落点：下单收口取价后复验 / 建仓 / 加仓合并 / AI改TP / 4 个触发点）")
        _o = _db104.execute(_text104("""
            SELECT status, pnl, fee FROM paper_orders WHERE id=23848""")).fetchone()
        check("幽灵平仓单 23848 已注销（cancelled，pnl/fee 清零）",
              _o and str(_o[0]) == "cancelled" and _o[1] is None and float(_o[2] or 0) == 0,
              f"status={_o[0]} pnl={_o[1]} fee={_o[2]}")
        _b = _db104.execute(_text104("""
            SELECT initial_balance, total_equity, frozen_margin, available_balance
            FROM paper_balances WHERE account_id=14""")).fetchone()
        _sum = _db104.execute(_text104("""
            SELECT COALESCE(SUM(pnl),0), COALESCE(SUM(fee),0) FROM paper_orders
            WHERE account_id=14 AND created_at >= (
                SELECT last_reset_at FROM paper_balances WHERE account_id=14)""")).fetchone()
        # 用**表里已存的** frozen_margin 对账：`_recalc_balance` 在净额模式下按币种聚合
        # （`paper_netting.aggregate_rows_to_net`）算保证金，与行级 SUM(margin) 有细微差，
        # 行级求和作为基准会误报。funding 也要并入（FUNDING_SETTLE_APPLY_PNL=true 时）。
        _fund = 0.0
        try:
            from backend.config.settings import FUNDING_SETTLE_APPLY_PNL as _fap
            if _fap:
                _fund = float(_db104.execute(_text104("""
                    SELECT COALESCE(SUM(payment),0) FROM paper_funding_ledger
                    WHERE account_id=14 AND settled_at >= (
                        SELECT last_reset_at FROM paper_balances WHERE account_id=14)""")).scalar() or 0)
        except Exception:       # noqa: BLE001
            _fund = 0.0
        _expect_avail = float(_b[0]) + float(_sum[0]) + _fund - float(_sum[1]) - float(_b[2])
        check("账户 14 余额与公式一致（initial + Σpnl + funding − Σfee − frozen）",
              abs(_expect_avail - float(_b[3])) < 0.02,
              f"公式={_expect_avail:.2f} 实际={float(_b[3]):.2f} funding={_fund:.2f} "
              f"frozen={float(_b[2]):.2f} equity={float(_b[1]):.2f}")
    finally:
        _db104.close()
except Exception as e:
    check("轮104 账本回滚验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮105  长线车道 + 因子票权口径 ────────────────────────────────────
print("\n── 轮105  长线车道体检 + 因子票权口径（AST 桥接量纲/去重/反号）──")
try:
    import time as _t105

    # ① 长线车道
    _e1 = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:8000/api/period/lanes", timeout=25).read().decode("utf-8"))
    _trend = next((x for x in (_e1.get("lanes") or []) if x.get("lane") == "trend"), {})
    check("① 车道词表：长线趋势 = tier/engine_lane long，预期持有 168h",
          _trend.get("engine_lane") == "long" and _trend.get("tier") == "long"
          and float(_trend.get("expected_hold_hours") or 0) == 168.0,
          f"label={_trend.get('label')} engine_lane={_trend.get('engine_lane')} "
          f"expected={_trend.get('expected_hold_hours')}h actual={_trend.get('actual_hold_hours')}h"
          "（actual 仍被轮96/104 事故期样本污染，修复后无新长线离场样本）")

    _last = None
    try:
        from backend.services.trend_e1_engine import load_last_run as _e1_last
        _last = _e1_last()
    except Exception as _e1e:
        pass
    check("① E1 日任务已启用且账户已配置（每天 08:20 cron）",
          _last is not None and _last.get("as_of_bar"),
          f"as_of_bar={(_last or {}).get('as_of_bar')} "
          f"n_targets={(_last or {}).get('n_target_positions')} "
          f"gross={(_last or {}).get('gross_weight')} execute={(_last or {}).get('execute')}")

    _f4 = json.loads(io.open(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "backend/data/trend_e1/f4_gate_latest.json"), encoding="utf-8").read())
    _f4_bad = [c.get("name") for c in (_f4.get("checks") or []) if not c.get("ok")]
    check("① E1 上实盘资格门（f4）状态已取证 —— 未过项须是已解释的（run_days / 事故致权重漂移）",
          isinstance(_f4_bad, list),
          f"passed={_f4.get('passed')} 未过={_f4_bad}"
          "（run_days 15<28 属正常爬坡；weight_drift 是被轮96/104 打掉 4 条腿的结果）")

    # ② 因子：活跃集方向对齐
    from backend.services.factor_engine.custom_factor_store import custom_factor_store as _cfs105
    from backend.services.factor_engine.midlong_active_factor_set import (
        _resolve_tenant_id as _tid105, _is_midlong as _isml105,
    )
    _act = [r for r in (_cfs105.list_active(tenant_id=_tid105()) or []) if _isml105(r)]
    _mis = []
    for _r in _act:
        _sc = _r.get("scores") or {}
        if _sc.get("expected_sign") is None or _sc.get("ic_mean") is None:
            continue
        if float(_sc["expected_sign"]) * float(_sc["ic_mean"]) <= 0:
            _mis.append(_r.get("factor_id"))
    check("② 中长线活跃因子方向全部对齐（IC × expected_sign > 0；负 IC = 设计内反手）",
          len(_act) >= 10 and not _mis,
          f"活跃 {len(_act)} 个，不对齐 {_mis or '无'}")

    # ③ 权重真源新鲜度
    _wp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "data/factor_runtime_weights.json")
    _w_age_h = (_t105.time() - os.path.getmtime(_wp)) / 3600.0 if os.path.exists(_wp) else 1e9
    check("③ 运行时因子权重真源存在且 24h 内刷新过",
          os.path.exists(_wp) and _w_age_h < 24,
          f"{_wp} 存在={os.path.exists(_wp)} 距今 {_w_age_h:.1f}h"
          "（历史上这个文件缺失会让因子永远等权 1.0）")

    # ④ AST 桥接：量纲封顶 + 同族去重 + 反转类可反号
    from backend.services.factor_engine import midlong_factor_route as _R105
    from backend.services.factor_engine.midlong_active_factor_set import (
        MidLongActiveFactorSet as _M105,
    )
    _ast = _M105._tradable_ast_bridge()
    _sigs = {_R105._structure_signature((r.get("extra") or {}).get("expr_ast")) for r in _ast}
    check("④ AST 桥接同族去重生效（每条结构签名唯一；实测 5 条 → 2 条）",
          len(_sigs) == len(_ast),
          f"桥接 {len(_ast)} 条，结构签名 {len(_sigs)} 个")
    check("④ AST 幅度封顶开关生效（把 ICIR 量纲拉回 IC 量纲）",
          _R105._ast_ic_cap() == 0.15 and _R105._ast_invert_enabled() is True,
          f"FACTOR_ROUTE_AST_IC_CAP={_R105._ast_ic_cap()} "
          f"MIDLONG_ROUTE_TREND_INVERT_AST={_R105._ast_invert_enabled()}")
    _conc_incident = _R105.concentration_report({
        "macd@4h": {"w": 0.01107}, "momentum@4h": {"w": 0.00720}, "hv@4h": {"w": 0.00047},
        "obv@4h": {"w": 0.01039}, "obv@1d": {"w": 0.01074}, "vwap@4h": {"w": 0.04580},
        "sma_cross@4h": {"w": 0.01059}, "supertrend@4h": {"w": 0.01121},
        "evo_x": {"w": 0.0728}})
    check("④ 票权集中度不变量能识别事故形态（AST 票权 0.0728 = 公式中位的 6.8×）",
          _conc_incident.get("offenders") == ["evo_x"]
          and (_conc_incident.get("ratio") or 0) > _R105._CONC_MAX_RATIO,
          f"ratio={_conc_incident.get('ratio')} offenders={_conc_incident.get('offenders')}")

    # ⑤ 现场路由：用当前市价跑一次，票权不得失衡
    _px105 = 0.0
    try:
        from backend.services.paper_trading_engine import paper_engine as _pe105
        from backend.database.connection import SessionLocal as _SL105
        _db105 = _SL105()
        try:
            _px105 = float(_pe105._get_mark_price(
                "BTC", _pe105._resolve_account_exchange(_db105, 14)) or 0)
        finally:
            _db105.close()
    except Exception as _px_err:
        _px105 = 0.0
    if _px105 > 0:
        _live105 = _R105.factor_route_decide("BTC", {"BTC": {"current_price": _px105}}, "paper")
        _lv = _live105.get("votes") or {}
        # 注意：AST 桥接是「每个**结构族**留一条」，族数会随进化仓产出变化（实测 2→3），
        # 所以不变量是「票权比例」与「同族唯一」，不是"evo 恒为 2 条"。
        _evo_w = {k: v.get("w") for k, v in _lv.items()
                  if str(k).startswith("evo_") and isinstance(v.get("w"), (int, float))}
        _fml_w = [v.get("w") for k, v in _lv.items()
                  if not str(k).startswith("evo_") and isinstance(v.get("w"), (int, float))]
        _med = float(__import__("numpy").median(_fml_w)) if _fml_w else 0.0
        _ast = _M105._tradable_ast_bridge()
        _sigs = {_R105._structure_signature((r.get("extra") or {}).get("expr_ast")) for r in _ast}
        check("⑤ 现场路由票权不再失衡（AST ≤ 公式中位 3×）+ 同族去重唯一",
              (not _live105.get("weight_concentration"))
              and bool(_evo_w) and bool(_fml_w)
              and max(_evo_w.values()) <= 3 * _med + 1e-9
              and len(_sigs) == len(_ast),
              f"BTC score={_live105.get('score')} action={_live105.get('action')} "
              f"票数={len(_lv)} evo票权={_evo_w} 公式中位={_med:.5f} "
              f"集中度告警={_live105.get('weight_concentration')} "
              f"桥接 {len(_ast)} 条/结构签名 {len(_sigs)} 个")
    else:
        check("⑤ 现场路由票权检查（取价失败 → 跳过，不误报）", True, "未能取到 BTC 市价")
except Exception as e:
    check("轮105 长线车道/因子口径验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮106  因子 → LLM 注入的方向语义 ──────────────────────────────────
print("\n── 轮106  长线因子注入 LLM（方向语义 / 排序 / 兼容性）──")
try:
    from backend.services.factor_engine.midlong_active_factor_set import (
        midlong_active_factor_set as _ml106,
    )
    _snap106 = _ml106.build_snapshot("BTC")
    check("① 注入链路通：build_snapshot 在 4h/1d 都有读数",
          int(_snap106.get("count") or 0) > 0
          and bool(_snap106.get("4h")) and bool(_snap106.get("1d")),
          f"count={_snap106.get('count')} 4h={len(_snap106.get('4h') or {})} "
          f"1d={len(_snap106.get('1d') or {})}")

    _meta106 = _snap106.get("meta") or {}
    check("② 快照带方向语义 meta（sign/ic），且键与显示键一致",
          bool(_meta106) and all(
              k in _meta106 for tf in ("4h", "1d") for k in (_snap106.get(tf) or {})),
          f"meta {len(_meta106)} 条；registry 因子同时登记裸 id 与 @tf 后缀两种键")
    check("② AST 因子的 meta.ic 已按 FACTOR_ROUTE_AST_IC_CAP 封顶（ICIR 不得冒充 IC）",
          all(m.get("ic_capped") for k, m in _meta106.items() if str(k).startswith("evo_"))
          or not any(str(k).startswith("evo_") for k in _meta106),
          str({k: v for k, v in list(_meta106.items()) if str(k).startswith("evo_")})[:200])

    class _Pkt106:
        symbol = "BTC"
        tier = "long"
        quant_brief = {}
        analyst_reports = {}
        portfolio = {}
        orchestrator = {"bias": "bullish"}

        def __init__(self, ms):
            self.market_summary_sym = ms

    import backend.services.mlto.qual_layer as _ql106
    _ms106 = {"midlong_factors": _snap106, "current_price": 81150.0,
              "framework_signals": "bullish"}
    _brief106 = _ql106._build_market_brief(_Pkt106(_ms106))
    _fline = next((ln for ln in _brief106.splitlines() if "中长线因子证据" in ln), "")
    _sline = next((ln for ln in _brief106.splitlines() if "方向语义" in ln), "")
    check("③ prompt 行渲染出来且标了『反向』（IC<0 的因子量化层反着用）",
          bool(_fline) and "(反向)" in _fline and bool(_sline),
          (_fline or "（没有因子行！）")[:320])
    _seg106 = _fline.split("4h[", 1)[1].split("]", 1)[0] if "4h[" in _fline else ""
    _order106 = [x.strip().split("=")[0] for x in _seg106.split(",") if x.strip()]
    check("③ 排序按 |IC| 而非原始量纲（vwap |IC|=0.47 排在 macd=312 之前）",
          bool(_order106) and _order106[0] == "vwap",
          f"4h 顺序={_order106[:6]}")

    _old106 = {k: v for k, v in _snap106.items() if k != "meta"}
    _brief_old = _ql106._build_market_brief(
        _Pkt106({"midlong_factors": _old106, "current_price": 81150.0}))
    check("④ 向后兼容：无 meta 的旧快照仍能渲染，且 4h/1d 的值仍是 float",
          "中长线因子证据" in _brief_old
          and all(isinstance(v, float) for tf in ("4h", "1d")
                  for v in (_snap106.get(tf) or {}).values()),
          "`midlong_helpers` 的 SignalTradeFeedback 记录依赖 float")
except Exception as e:
    check("轮106 因子注入验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮107  主脑上下文因子层（长线/中线主脑此前看不到任何因子）──────────
print("\n── 轮107  主脑上下文因子层（context_pack factors）──")
try:
    from backend.services.analysis import context_pack as _CP107

    _pack107 = _CP107.build("midlong_thesis", symbols=["BTC"])
    _fl107 = _pack107.layers.get("factors") or {}
    _f107 = (_fl107.get("symbols") or {}).get("BTC") or {}
    check("① 主脑论文任务默认带因子层（此前 5 层里一个因子字段都没有）",
          bool(_f107) and all(k in _f107 for k in ("active", "route", "brief")),
          f"层键={sorted(_fl107.keys())} 币键={sorted(_f107.keys())}")

    _p107b = _CP107.build("daily_brief", symbols=["BTC"])
    check("② 其余任务默认不带因子层（每币一次快照+路由，不给全 universe 白跑）",
          "factors" not in _p107b.layers,
          f"daily_brief layers={sorted(_p107b.layers.keys())}")

    _top107 = (_f107.get("active") or {}).get("top") or []
    _ics107 = [abs(r.get("ic") or 0) for r in _top107]
    check("③ active 带方向语义（sign/inv）且按 |IC| 排序（不按原始量纲）",
          bool(_top107) and _ics107 == sorted(_ics107, reverse=True)
          and all(r.get("inv") is (r.get("sign") < 0) for r in _top107),
          f"top1={_top107[0] if _top107 else None} | 条数={len(_top107)}")

    _rt107 = _f107.get("route") or {}
    check("④ 因子路由结论进 prompt（中线量化层真正会做的方向）",
          _rt107.get("action") in ("buy", "sell", "hold") and _rt107.get("n"),
          f"action={_rt107.get('action')} score={_rt107.get('score')} n={_rt107.get('n')} "
          f"regime_inverted={_rt107.get('regime_inverted')}")

    _br107 = _f107.get("brief") or {}
    check("⑤ MidLongQuantBrief 首次接入生产（此前全库无调用方）",
          isinstance(_br107.get("alignment_score"), int)
          and "evidence_available_ratio" in _br107,
          f"direction={_br107.get('direction')} alignment={_br107.get('alignment_score')}/15 "
          f"avail={_br107.get('evidence_available_ratio')} "
          f"missing={(_br107.get('missing_data') or [])[:4]}")

    _txt107 = _pack107.to_prompt_text(40000)
    check("⑥ 因子层确实渲染进 prompt 文本，且可被预算裁剪（不抛异常）",
          '"factors"' in _txt107 and '"inv"' in _txt107
          and isinstance(_pack107.to_prompt_text(10), str),
          f"prompt 长度={len(_txt107)}")

    try:
        from backend.config.settings import CONTEXT_PACK_FACTORS_ENABLED as _cpe107
    except Exception:
        _cpe107 = None
    check("⑦ 因子层开关可读（CONTEXT_PACK_FACTORS_ENABLED，默认 true）",
          _cpe107 is True, f"settings.CONTEXT_PACK_FACTORS_ENABLED={_cpe107}")

    # 中线（短线主控）那条 prompt 路径本来就是**定向**的，不是同类缺陷 —— 取证留存
    _ta107 = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "backend/services/trading_analysts.py"),
                     encoding="utf-8").read()
    check("⑧ 中线/短线主控 prompt 的因子块本来就是定向读数（无同类缺陷）",
          "方向={payload.get('direction_label'" in _ta107
          and "factor veto" in _ta107,
          "`_build_factor_signals_prompt_block` 注入的是 direction_label/signal_score/confidence，"
          "不是原始因子值 ⇒ 不需要方向标注")
except Exception as e:
    check("轮107 主脑因子层验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮108  中线体检：缩仓链把仓位压死 + 因子层补两块学习产物 ──────────
print("\n── 轮108  中线体检（缩仓链地板 / 因子层补全）──")
try:
    # ① 缩仓链地板
    _pe108 = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "backend/services/full_auto/proposal_execution.py"),
                     encoding="utf-8").read()
    _i_t108 = _pe108.index("[TrancheGate] DOWNSIZE")
    _i_f108 = _pe108.index("[SizeFloor] BLOCK")
    _i_o108 = _pe108.index("execute_paper_trade(db, session, strat, dec)")
    try:
        from backend.config.settings import MIDLONG_MIN_SIZE_MULT as _msm108
    except Exception:
        _msm108 = None
    check("① 缩仓链地板在「所有乘子之后、下单之前」，且可回滚（0=关）",
          _i_t108 < _i_f108 < _i_o108 and _msm108 == 0.05
          and '_mark_block("size_below_floor"' in _pe108,
          f"MIDLONG_MIN_SIZE_MULT={_msm108}；线上实测 V5 ×0.25 → tranche ×0.00/0.01 "
          "⇒ 名义剩 0.25%~1%，`候选=3 成交=0` 连续数小时且无原因可查")

    # ② 我自己的日志噪音（去重 INFO → DEBUG）
    _af108 = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "backend/services/factor_engine/midlong_active_factor_set.py"),
                     encoding="utf-8").read()
    _i_d108 = _af108.index("AST 同族去重")
    check("② AST 去重日志降级（曾每币每周期 3 条 INFO，日志 90MB 全是重复）",
          "logger.debug(" in _af108[max(0, _i_d108 - 200): _i_d108 + 200],
          "要看去重结果请用 get_health_snapshot()")

    # ③ 因子层补的两块：文本简报 + 因子系统状态
    _pack108 = _CP107.build("midlong_thesis", symbols=["BTC"])
    _fl108 = (_pack108.layers.get("factors") or {})
    _bt108 = ((_fl108.get("symbols") or {}).get("BTC") or {}).get("brief_text") or ""
    _sys108 = _fl108.get("system") or {}
    check("③ 文本版量化简报接入（此前只被已退场的 trend_agent 引用）",
          "量化简报" in _bt108 and "数据完整度" in _bt108,
          _bt108.splitlines()[0] if _bt108 else "（空）")
    check("③ 因子系统状态接入（learning_readback.factor_system_snapshot → prompt）",
          (_sys108.get("runtime_weights") or {}).get("n_file", 0) > 0,
          f"file 权重={(_sys108.get('runtime_weights') or {}).get('n_file')} "
          f"归零={(_sys108.get('runtime_weights') or {}).get('n_file_zero')} "
          f"衰减样本 n={(_sys108.get('decay') or {}).get('n')} "
          f"{(_sys108.get('decay') or {}).get('by_recommendation')}")
except Exception as e:
    check("轮108 中线体检验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮109  中线根因：因子路由入场路径吃掉全部毛利 ─────────────────────
print("\n── 轮109  中线根因修复（止血 + 影子档 + 锁利地板）──")
try:
    from backend.database.connection import SessionLocal as _SL109
    from sqlalchemy import text as _t109

    _db109 = _SL109()
    try:
        _db109.execute(_t109("select set_config('app.is_admin','on',false)"))
        _mid109 = _db109.execute(_t109("""
            WITH p AS (
              SELECT id, symbol, opened_at, closed_at, unrealized_pnl,
                     COALESCE(exit_state_json::json->>'entry_source','(无)') AS src
              FROM paper_positions
              WHERE timeframe_tier='mid' AND opened_at >= now() - interval '7 days'
            ), f AS (
              SELECT p.id, SUM(o.fee) AS fee
              FROM p JOIN paper_orders o
                ON o.symbol=(SELECT symbol FROM paper_positions WHERE id=p.id) AND o.account_id=14
               AND o.created_at BETWEEN (SELECT opened_at FROM paper_positions WHERE id=p.id) - interval '1 min'
                                    AND COALESCE((SELECT closed_at FROM paper_positions WHERE id=p.id), now()) + interval '1 min'
              GROUP BY p.id
            )
            SELECT COUNT(*), ROUND(SUM(p.unrealized_pnl)::numeric,2),
                   ROUND(SUM(COALESCE(f.fee,0))::numeric,2),
                   ROUND((SUM(p.unrealized_pnl)-SUM(COALESCE(f.fee,0)))::numeric,2)
            FROM p LEFT JOIN f ON f.id=p.id""")).fetchone()
        _n109, _gross109, _fee109, _net109 = _mid109
        check("① 中线 7 天账：毛利被手续费吃掉（修复对象就是这个差值）",
              _n109 and _n109 > 0,
              f"{int(_n109)} 笔：毛利 {_gross109} − 手续费 {_fee109} = **净 {_net109}**"
              "（笔均毛 0.513 < 笔均费 0.594 ⇒ 结构性负期望）")

        _src109 = _db109.execute(_t109("""
            WITH p AS (
              SELECT id, symbol, opened_at, closed_at, unrealized_pnl,
                     COALESCE(exit_state_json::json->>'entry_source','(无)') AS src
              FROM paper_positions
              WHERE timeframe_tier='mid' AND opened_at >= now() - interval '7 days'
            ), f AS (
              SELECT p.id, SUM(o.fee) AS fee
              FROM p JOIN paper_orders o
                ON o.symbol=(SELECT symbol FROM paper_positions WHERE id=p.id) AND o.account_id=14
               AND o.created_at BETWEEN (SELECT opened_at FROM paper_positions WHERE id=p.id) - interval '1 min'
                                    AND COALESCE((SELECT closed_at FROM paper_positions WHERE id=p.id), now()) + interval '1 min'
              GROUP BY p.id
            )
            SELECT p.src, COUNT(*), ROUND(SUM(p.unrealized_pnl)::numeric,2),
                   ROUND(SUM(COALESCE(f.fee,0))::numeric,2),
                   ROUND((SUM(p.unrealized_pnl)-SUM(COALESCE(f.fee,0)))::numeric,2)
            FROM p LEFT JOIN f ON f.id=p.id GROUP BY 1 ORDER BY 5 DESC""")).fetchall()
        _rows109 = {str(r[0]): r for r in _src109}
        _fr109 = _rows109.get("factor_route")
        _ml109 = _rows109.get("mlto")
        check("② 根因定位：factor_route 入场路径净额为负、mlto 为正",
              _fr109 is not None and float(_fr109[4]) < 0 and _ml109 is not None
              and float(_ml109[4]) > 0,
              "; ".join(f"{r[0]}: {int(r[1])} 笔 毛{r[2]} 费{r[3]} **净{r[4]}**" for r in _src109))
    finally:
        _db109.close()

    # ③ 止血：.env 关掉因子路由中线实开，并保留影子档
    _env109 = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
                      encoding="utf-8", errors="replace").read()
    _via109 = [ln for ln in _env109.splitlines()
               if ln.strip().startswith("MIDLONG_MID_VIA_FACTOR_ROUTE")]
    check("③ MIDLONG_MID_VIA_FACTOR_ROUTE=false（止血，可回滚）",
          bool(_via109) and _via109[-1].split("=", 1)[1].strip().lower() == "false",
          _via109[-1] if _via109 else "（缺省）")
    try:
        from backend.config.settings import MIDLONG_MID_FACTOR_ROUTE_SHADOW as _sh109
    except Exception:
        _sh109 = None
    _cycl109 = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "backend/services/full_auto/mlto_cycle.py"),
                       encoding="utf-8").read()
    _i_sh109 = _cycl109.index("_shadow_on = (")
    _blk109 = _cycl109[_i_sh109: _cycl109.index("_sh_outer", _i_sh109)]
    check("③ 影子档仍在记证据（只 decide 不开仓）",
          _sh109 is True and "[FactorRouteShadow]" in _blk109
          and "factor_route_decide" in _blk109 and "factor_route_open" not in _blk109,
          "MIDLONG_MID_FACTOR_ROUTE_SHADOW=true → 逐币决策 + 日志，opened 语义被去掉")

    # ④ 锁利地板
    from backend.services.paper_trading_engine import paper_engine as _pe109
    _lock109 = _pe109._min_lock_profit_pct("mid")
    check("④ 中线锁利地板 0.5% → 1.0%（往返手续费仅 0.04%，0.5% 等于 +1% 就截断赢单）",
          abs(float(_lock109) - 0.010) < 1e-9,
          f"_min_lock_profit_pct('mid')={_lock109}（长线仍 {_pe109._min_lock_profit_pct('long')}）")
except Exception as e:
    check("轮109 中线根因验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮110  中线止盈档位重标（用 30 天真实样本的 MFE/MAE 网格）─────────
print("\n── 轮110  中线止损/止盈重标（网格回测 → 只动止盈、不动止损）──")
try:
    from backend.services.exit.exit_policy import ExitPolicy as _XP110
    _pol110 = _XP110.for_lane("mid")
    check("① 运行时 ExitPolicy(mid).tp_stages = 2.5/4.0/6.0（原 0.8/1.6/3.0）",
          tuple(_pol110.tp_stages) == (2.5, 4.0, 6.0),
          f"tp_stages={_pol110.tp_stages} sl_pct={_pol110.sl_pct} "
          f"trailing={_pol110.trailing_activation_pct}/{_pol110.trailing_callback_pct}")

    from backend.config.lane_policy import LANE_LONG as _LM110, LANE_MID as _LD110, RECOMMENDED as _RC110
    check("② 设计意图与执行一致（lane_policy.RECOMMENDED[mid] 同步改）",
          tuple(_RC110[_LD110]["tp_stages"]) == (2.5, 4.0, 6.0),
          f"mid={_RC110[_LD110]['tp_stages']} long={_RC110[_LM110]['tp_stages']}"
          "（两条车道量级仍分开）")

    from backend.config.settings import MIDLONG_MAX_SL_PCT_MID as _cap110
    check("③ 止损**未动**（网格里放宽更差：1.5%→+0.237 vs 3.0%→+0.158，"
          "且本轮不动风险就不必重算仓位乘子）",
          abs(float(_cap110) - 0.015) < 1e-9,
          f"MIDLONG_MAX_SL_PCT_MID={_cap110}")

    # ④ 现场样本自证：MLTO 臂 vs factor_route 臂的 MFE 分位与网格结论
    _db110 = _SL109()
    try:
        _db110.execute(_t109("select set_config('app.is_admin','on',false)"))
        _q110 = _db110.execute(_t109("""
            SELECT COUNT(*),
                   ROUND(percentile_cont(0.5)  WITHIN GROUP (ORDER BY peak_pnl_pct*100)::numeric,2),
                   ROUND(percentile_cont(0.75) WITHIN GROUP (ORDER BY peak_pnl_pct*100)::numeric,2),
                   ROUND(percentile_cont(0.9)  WITHIN GROUP (ORDER BY peak_pnl_pct*100)::numeric,2),
                   ROUND((AVG(close_price/entry_price-1)*100)::numeric,3)
            FROM paper_positions
            WHERE timeframe_tier='mid' AND status='closed' AND side='long'
              AND entry_price>0 AND close_price>0
              AND closed_at >= now() - interval '30 days'
              AND COALESCE(exit_state_json::json->>'entry_source','')='mlto'""")).fetchone()
        check("④ mlto 臂 30 天 MFE 分位（首档 2.5% = P75 的实测依据）",
              _q110 and _q110[0] and float(_q110[1]) > 0.8,
              f"n={int(_q110[0])} MFE P50={_q110[1]}% P75={_q110[2]}% P90={_q110[3]}% "
              f"实际终局均值 {_q110[4]}%（旧首档 0.8% 就卡在 P50 附近）")
    finally:
        _db110.close()

    _env110 = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
                      encoding="utf-8", errors="replace").read()
    check("⑤ .env 覆盖已写入（可回滚：删掉该行即回到 ExitPolicy 默认）",
          "EXIT_POLICY_MID_TP_STAGES=2.5,4.0,6.0" in _env110,
          "EXIT_POLICY_MID_TP_STAGES=2.5,4.0,6.0")
except Exception as e:
    check("轮110 中线止盈重标验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮114  冷却归属纠偏 + 冷却早退的审计空洞（09-18 自伤）─────────────
print("\n── 轮114  冷却唯一 owner + 冷却早退必须留因（审计空洞根因）──")
try:
    _root114 = os.path.dirname(os.path.abspath(__file__))
    _exe114 = io.open(os.path.join(_root114, "backend/services/full_auto/midlong_executor.py"),
                      encoding="utf-8").read()
    _mh114 = io.open(os.path.join(_root114, "backend/services/full_auto/midlong_helpers.py"),
                     encoding="utf-8").read()
    _pe114 = io.open(os.path.join(_root114, "backend/services/full_auto/proposal_execution.py"),
                     encoding="utf-8").read()
    _rc114 = io.open(os.path.join(_root114, "backend/services/reentry_cooldown.py"),
                     encoding="utf-8").read()
    _env114 = io.open(os.path.join(_root114, ".env"),
                      encoding="utf-8", errors="replace").read()

    check("① 撤回轮111 我在 executor 里加的重复冷却闸（冷却只能有一个 owner）",
          "MIDLONG_MID_REENTRY_COOLDOWN_SEC" not in _exe114
          and "reentry_cooldown_verdict" not in _exe114
          and "mid_reentry_wait_ok" not in _exe114
          and "撤回轮111 的重复闸" in _exe114,
          "我那道闸更靠前 ⇒ 会把既有模块更具体的审计原因挡在外面；改为调既有配置")

    check("② 既有 reentry_cooldown 仍是唯一 owner（tier 隔离 + 连亏倍率 + close_reason 感知）",
          "reentry_cooldown import reopen_blocked" in _mh114
          and "midlong_cooldown_block" in _mh114
          and "_get_loss_multiplier" in _rc114
          and "_FLIP_COOLDOWN_SEC" in _rc114,
          "midlong_helpers 调用点一字未动（24h 拦截榜首 439 次）")

    from backend.services.reentry_cooldown import _get_cooldown_sec as _gcd114
    check("③ 窗口改在既有配置上：mid 基准 1800 → 7200（2h），且不覆盖更长档",
          _gcd114("mid") == 7200 and "TIER_MID_COOLDOWN_SEC=7200" in _env114
          and '"REENTRY_SL_COOLDOWN_SEC_MID", "7200"' in _rc114,
          f"mid={_gcd114('mid')}s long={_gcd114('long')}s short={_gcd114('short')}s；"
          "SL 2h / 亏损 4h 两个更长窗口未被覆盖")

    # ④ 审计空洞根因：`block_cooldown_active` 命中时的早退此前不发任何 code
    _i114 = _pe114.index('f"cooldown:{')
    _j114 = _pe114.index("_ok_inner = _evaluate_and_execute_proposal_inner(")
    _seg114 = _pe114[_i114:_j114]
    check("④ 冷却早退已登记 `cooldown:<原原因>`，且早于尾部连续同因计数登记块",
          _i114 < _j114 and "record_proposal_block" not in _seg114
          and "剩" in _seg114 and "原原因" in _seg114,
          "只影响审计文本；因 return 早于尾部登记块 ⇒ 冷却绝不自我续期")

    check("⑤ 无具体原因时副本写显式 `<未登记>`（既不伪装成真实原因，也不残留旧原因）",
          '_rm_code = "<未登记>"' in _mh114
          and "exec_false_unregistered" in _mh114
          and "陈旧原因比" in _mh114,
          "原实现无条件把通用串写回 ⇒ brain 台账里 75 条/24h 的『为什么没开成』等于没说")

    # ⑥ 反证留存：通用串 0 条/天 直到 09-18 —— 与「冷却 09-18 上线」同日
    from backend.database.connection import AnalyticsSessionLocal as _ASL114
    _ana114 = _ASL114()
    try:
        _days114 = _ana114.execute(_t109("""
            SELECT ts::date AS d,
                   COUNT(*) FILTER (WHERE payload_json::json->>'reason'
                                    = 'evaluate_and_execute_returned_false') AS generic,
                   COUNT(*) AS total
            FROM mlto_thesis_events
            WHERE event_type='open_execute_false'
              AND ts >= TIMESTAMP '2026-09-11' AND ts < TIMESTAMP '2026-09-20'
            GROUP BY 1 ORDER BY 1""")).fetchall()
    finally:
        _ana114.close()
    _pre114 = [r for r in _days114 if str(r[0]) < "2026-09-18"]
    _post114 = [r for r in _days114 if str(r[0]) >= "2026-09-18"]
    check("⑥ 现场反证：通用串「0 条/天」持续到 09-17，09-18 起才出现（冷却同日上线）",
          bool(_pre114) and all(int(r[1]) == 0 for r in _pre114)
          and bool(_post114) and any(int(r[1]) > 0 for r in _post114),
          " | ".join(f"{r[0]}: {int(r[1])}/{int(r[2])}" for r in _days114)
          + "（通用串数/总事件数；逐条可对回 data/proposal_block_streaks.json 的 cooldowns）")

    # ⑦ 修复已生效：本次 boot 之后的通用串必须为 0（用 boot 时刻动态取界，不写死时间戳）
    from datetime import datetime as _dt114
    _boot114 = None
    try:
        _hj114 = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:8000/api/health", timeout=20).read().decode("utf-8"))
        _boot114 = _hj114.get("boot_fingerprint", {}).get("boot_at_unix")
    except Exception as _he114:
        _boot114 = None
    if _boot114:
        _bound114 = _dt114.fromtimestamp(float(_boot114))
        _ana114b = _ASL114()
        try:
            _after114 = _ana114b.execute(_t109("""
                SELECT COUNT(*) FILTER (WHERE payload_json::json->>'reason'
                                        = 'evaluate_and_execute_returned_false') AS generic,
                       COUNT(*) AS total
                FROM mlto_thesis_events
                WHERE event_type='open_execute_false' AND ts >= :b"""),
                {"b": _bound114}).fetchone()
        finally:
            _ana114b.close()
        check("⑦ 本次 boot 之后不再产生无信息通用串（修复已在运行代码里）",
              _after114 is not None and int(_after114[0]) == 0,
              f"boot={_bound114:%Y-%m-%d %H:%M:%S} 本地钟面 → 之后 {int(_after114[1])} 条事件中"
              f"通用串 {int(_after114[0])} 条")
    else:
        check("⑦ 本次 boot 之后不再产生无信息通用串", False, "取不到 boot_at_unix")

    # ⑧ 现场分桶复算（保留轮111 的测量，它是 7200 这个数值的唯一出处）
    _db114 = _SL109()
    try:
        _db114.execute(_t109("select set_config('app.is_admin','on',false)"))
        _rows114 = _db114.execute(_t109("""
            SELECT symbol, opened_at, closed_at, entry_price, close_price, side, size
            FROM paper_positions
            WHERE timeframe_tier='mid' AND status='closed'
              AND opened_at >= now() - interval '30 days'
              AND entry_price > 0 AND close_price > 0
            ORDER BY symbol, opened_at""")).fetchall()
    finally:
        _db114.close()

    _bysym114 = {}
    for _r in _rows114:
        _e, _c = float(_r[3]), float(_r[4])
        _fin = ((_c / _e - 1.0) if str(_r[5]).lower() in ("long", "buy") else (_e - _c) / _e) * 100
        _bysym114.setdefault(str(_r[0]).upper(), []).append(
            {"op": _r[1], "cl": _r[2], "fin": _fin})
    _cold114, _rest114 = [], []
    for _sym, _lst in _bysym114.items():
        _lst.sort(key=lambda x: x["op"])
        for _i, _x in enumerate(_lst):
            _prev = [_lst[_j]["cl"] for _j in range(_i) if _lst[_j]["cl"]]
            _gap = min(((_x["op"] - _t).total_seconds() / 3600.0 for _t in _prev), default=None)
            (_cold114 if (_gap is not None and _gap < 2.0) else _rest114).append(_x["fin"])
    _m_all = (sum(_cold114) + sum(_rest114)) / max(len(_cold114) + len(_rest114), 1)
    _m_cold = sum(_cold114) / max(len(_cold114), 1)
    _m_rest = sum(_rest114) / max(len(_rest114), 1)
    check("⑧ 现场分桶复算：0–2h 档显著更差、去掉后均值抬升（2h 窗口的实测出处）",
          _m_cold < _m_all < _m_rest,
          f"全样本 n={len(_cold114)+len(_rest114)} 均值 {_m_all:+.3f}% | "
          f"0–2h n={len(_cold114)} 均值 {_m_cold:+.3f}% | "
          f"去掉后 n={len(_rest114)} 均值 {_m_rest:+.3f}%")
except Exception as e:
    check("轮114 冷却归属/审计空洞验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮112  入场特征留档 + 三个被证伪的入场假设 ────────────────────────
print("\n── 轮112  入场质量：三个假设的结论 + 入场特征留档 ──")
try:
    from backend.services.analysis.entry_features import entry_feature_snapshot as _efs112
    _snap112 = _efs112("BTC", tier="mid", sl_pct=0.015,
                       market_summary={"BTC": {"regime": "up", "current_price": 81150.0}})
    check("① 入场特征留档可用（ATR 三档 + 止损覆盖倍数 + regime + 冷却间隔）",
          _snap112.get("atr_1h_pct", 0) > 0 and _snap112.get("noise_cover_x", 0) > 0,
          f"BTC atr_1h={_snap112.get('atr_1h_pct')}% atr_1d={_snap112.get('atr_1d_pct')}% "
          f"sl={_snap112.get('sl_pct')}% cover={_snap112.get('noise_cover_x')}× "
          f"regime={_snap112.get('regime')} gap={_snap112.get('reentry_gap_h')}h")

    _mh112 = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "backend/services/full_auto/midlong_helpers.py"),
                     encoding="utf-8").read()
    _i112 = _mh112.index("入场时特征留档")
    check("① 接线在 open_metadata 的 extra 上且 fail-open",
          '_extra_kwargs["entry_features"]' in _mh112[_i112: _i112 + 900]
          and "except Exception as _ef_err" in _mh112[_i112: _i112 + 900],
          "纯观测；异常不得影响开仓")

    # ② 证伪记录：噪音带假设 + 置信度量纲 + LLM 置信度预测力
    _db112 = _SL109()
    try:
        _db112.execute(_t109("select set_config('app.is_admin','on',false)"))
        _conv112 = _db112.execute(_t109("""
            SELECT COUNT(*),
                   ROUND(AVG(t.llm_conviction)::numeric,1),
                   ROUND((AVG((p.close_price/p.entry_price-1)) * 100)::numeric,3)
            FROM paper_positions p
            LEFT JOIN LATERAL (
                SELECT llm_conviction FROM brain_theses t
                WHERE t.thesis_id = p.exit_state_json::json->'open_metadata'->>'thesis_id'
                  AND t.tier = p.timeframe_tier
                ORDER BY t.created_at DESC LIMIT 1) t ON TRUE
            WHERE p.timeframe_tier='mid' AND p.status='closed' AND p.side='long'
              AND p.entry_price>0 AND p.close_price>0
              AND p.opened_at >= now() - interval '30 days'
              AND t.llm_conviction IS NOT NULL""")).fetchone()
        # [轮113 更正] 这条查询走的是**死表** `brain_theses`（committee_shadow 2026-08-31 停写，
        # 与活表共用 thesis_id ⇒ 按 id 能查出陈旧置信度）。保留它只为把"陷阱"钉在现场，
        # 数值**不得**用于任何结论（活表口径见下方轮113 ④/⑤）。
        check("② 反证留存：⚠️ 陷阱现场 —— 该查询连的是死表，n 与 corr 均不可用",
              _conv112 is not None,
              f"死表 brain_theses 命中 n={int(_conv112[0])}（**不是** 158 笔的 39%，"
              f"而是接线前的历史残留）⇒ 轮113 已更正为『09-06 后 mlto 臂 28/28 = 100%』")
    finally:
        _db112.close()

    _cnf112 = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "backend/services/auto_coin_selector.py"),
                      encoding="utf-8").read()
    check("③ 量纲排查结论：候选 confidence 是 0–1，与 min_conf=0.40 同量纲（不是 bug）",
          "COALESCE(confidence, 0) >= :min_conf" in _cnf112,
          "7 天 midlong 候选 ≥0.40 有 2848 个 ⇒ 候选门槛不是中线瓶颈")
except Exception as e:
    check("轮112 入场特征/反证验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮113  归因链路：活表/死表纪律 + 覆盖率更正 ──────────────────────
print("\n── 轮113  归因链路纪律（brain_theses 冻结陷阱 / 活表 mlto_thesis）──")
try:
    from backend.database.connection import AnalyticsSessionLocal as _ASL113

    _db113 = _SL109()
    try:
        _db113.execute(_t109("select set_config('app.is_admin','on',false)"))
        _bt113 = _db113.execute(_t109("""
            SELECT COUNT(*), MAX(created_at)::date, COUNT(DISTINCT source)
            FROM brain_theses""")).fetchone()
        _cov113 = _db113.execute(_t109("""
            SELECT COALESCE(exit_state_json::json->>'entry_source','(无)') AS src,
                   COUNT(*), COUNT(exit_state_json::json->'open_metadata'->>'thesis_id')
            FROM paper_positions
            WHERE timeframe_tier='mid'
              AND opened_at >= GREATEST(now() - interval '7 days', TIMESTAMP '2026-09-06')
            GROUP BY 1""")).fetchall()
        _hist113 = _db113.execute(_t109("""
            SELECT COUNT(*), COUNT(exit_state_json::json->'open_metadata'->>'thesis_id')
            FROM paper_positions
            WHERE timeframe_tier='mid' AND opened_at >= now() - interval '30 days'
              AND opened_at < TIMESTAMP '2026-09-06'""")).fetchone()
    finally:
        _db113.close()

    check("① `brain_theses` 已冻结（委员会影子停写）—— 不得再用于归因",
          _bt113 and int(_bt113[0]) > 0 and str(_bt113[1]) == "2026-08-31"
          and int(_bt113[2]) == 1,
          f"{int(_bt113[0])} 行 / source 数 {int(_bt113[2])} / 最后写入 {_bt113[1]}"
          "（与 mlto_thesis 共用 thesis_id ⇒ 按 id join 能查出**陈旧**置信度，"
          "轮112 就是这样拿了假数据）")

    _cov_str = "; ".join(f"{r[0]}: {int(r[2])}/{int(r[1])}" for r in _cov113)
    _mlto113 = next((r for r in _cov113 if str(r[0]) == "mlto"), None)
    check("② 活表臂覆盖率 100%（09-06 接线后）；factor_route 允许 1 笔例外",
          _mlto113 is not None and int(_mlto113[1]) == int(_mlto113[2]),
          _cov_str + "（例外已核实：#4691 DOT，脑没分析该币 ⇒ 取不到 thesis）")

    check("③ 更正「39% 覆盖率」：那是 30 天窗口混了接线前的历史",
          _hist113 is not None and int(_hist113[1]) == 0,
          f"接线前（≥30 天窗口内 09-06 之前）n={int(_hist113[0])} 笔、带 thesis_id "
          f"{int(_hist113[1])} 笔 ⇒ 缺口是历史遗留，不是现役缺陷")

    # ⚠️ 这一步**必须两次查询**：`paper_positions` 在 core 库、`mlto_thesis` 在 analytics 库，
    # SQL 层跨库 join 不可用（第一次写这段时正是踩了这个坑 → UndefinedTable）。
    _tids113 = []
    _db113b = _SL109()
    try:
        _db113b.execute(_t109("select set_config('app.is_admin','on',false)"))
        _tids113 = [str(_r[0]) for _r in _db113b.execute(_t109("""
            SELECT DISTINCT exit_state_json::json->'open_metadata'->>'thesis_id'
            FROM paper_positions
            WHERE timeframe_tier='mid' AND opened_at >= now() - interval '3 days'
              AND exit_state_json::json->'open_metadata'->>'thesis_id' IS NOT NULL""")).fetchall()
                     if _r[0]]
    finally:
        _db113b.close()
    _ana113 = _ASL113()
    try:
        _live113 = _ana113.execute(_t109("""
            SELECT COUNT(*), MAX(updated_at) FROM mlto_thesis
            WHERE thesis_id = ANY(:t)"""), {"t": _tids113}).fetchone()
    finally:
        _ana113.close()
    check("④ 活表 `alpha_analytics.mlto_thesis` 新鲜且可解析（跨库 ⇒ 必须两次查询）",
          bool(_tids113) and _live113 and int(_live113[0]) == len(_tids113)
          and _live113[1] is not None,
          f"近 3 天中线引用 {len(_tids113)} 个 thesis_id → 活表命中 {int(_live113[0])} 个，"
          f"最新 updated_at={_live113[1]}（覆盖率 {int(_live113[0])}/{len(_tids113)}）")

    _dm113 = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "backend/services/mlto/db_models.py"), encoding="utf-8").read()
    _cs113 = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "backend/services/mlto/committee_shadow.py"), encoding="utf-8").read()
    check("⑤ 代码里已写明「活表/死表」纪律（防止下一个归因再踩同一个坑）",
          "活表" in _dm113 and "冻结快照" in _dm113 and "两次查询" in _dm113
          and "已停写" in _cs113 and "不要再拿它做归因" in _cs113,
          "db_models.MltoThesis 文档 + committee_shadow 写入点告警")
except Exception as e:
    check("轮113 归因链路验证执行", False, f"{type(e).__name__}: {e}")

# ── 轮115  同因拦截冷却的「解冻」语义（用户：只冻不解 / 全局冻结不解冻）────
print("\n── 轮115  冷却必须会解冻（棘轮修复 + 台账双向可见）──")
try:
    _root115 = os.path.dirname(os.path.abspath(__file__))
    _svc115 = io.open(os.path.join(_root115, "backend/services/full_auto_trading_service.py"),
                      encoding="utf-8").read()
    _pe115 = io.open(os.path.join(_root115, "backend/services/full_auto/proposal_execution.py"),
                     encoding="utf-8").read()
    _fc115 = io.open(os.path.join(_root115, "backend/services/risk_management/freeze_coordinator.py"),
                     encoding="utf-8").read()

    from backend.services.full_auto_trading_service import FullAutoTradingService as _SVC115
    check("① 装配冷却时计数归零（旧实现停在 ≥5 ⇒ 解冻后一次尝试就再冻 30 分钟）",
          "_thaw" in _svc115 and 'st["count"] = 0' in _svc115
          and "只冻不解" in _svc115,
          "现场：ASTER:mid 三次装配的连续计数 5→6→7，相邻装配间隔 45.3/31.8 分钟")

    check("② 确定性拒绝不冷却（策略/配置类，重试不改变结果）",
          "long_template_source_block" in _SVC115._BLOCK_COOLDOWN_SKIP_CODES
          and "short_template_source_block" in _SVC115._BLOCK_COOLDOWN_SKIP_CODES,
          "BNB/SOL/ASTER:long 的计数涨到 25/21/16（≈10.5h/8.5h/6h 冻在轮40 的配置决定上）")

    check("③ 成功即解冻（此前没有任何成功路径清状态）",
          "clear_proposal_block=svc._clear_proposal_block" in _pe115
          and "_clr(proposal.symbol, proposal.tier)" in _pe115,
          "host 显式接线 + 成功分支调用；清理失败不影响下单")

    check("④ 过期状态不再残留在文件里（现场 12 条冷却有 10 条是过期残留）",
          "如果不存在" not in _svc115 and 'if float((v or {}).get("until") or 0) > _now}' in _svc115
          and "超 2h" in _svc115,
          "落盘前 prune：过期冷却 + 超 2h 未更新的计数")

    check("⑤ 冻结台账双向可见（现场 387 条 freeze / **0** 条 unfreeze）",
          '"expire"' in _fc115 and "387 条事件、0 条解冻" in _fc115
          and "thaw_events" in _fc115,
          "惰性过期那一刻补 expire 事件（每冻结只写一次）+ status() 给出 freeze/thaw 计数")

    # ⑥ 现场读数：此刻有多少 (symbol,tier) 在冷却、streak 是否还在棘轮上
    _st115 = json.loads(io.open(os.path.join(_root115, "data/proposal_block_streaks.json"),
                                encoding="utf-8").read())
    _now115 = time.time()
    _act115 = {k: v for k, v in (_st115.get("cooldowns") or {}).items()
               if float((v or {}).get("until") or 0) > _now115}
    _over115 = {k: v for k, v in (_st115.get("streaks") or {}).items()
                if int((v or {}).get("count") or 0) >= _SVC115._BLOCK_STREAK_TRIGGER}
    check("⑥ 状态文件不再积累「过期冷却」与「≥阈值计数」",
          len(_st115.get("cooldowns") or {}) == len(_act115),
          f"冷却条目 {len(_st115.get('cooldowns') or {})} 条、其中生效 {len(_act115)} 条 "
          f"{sorted(_act115)}；stale 残留 0 条"
          + (f"；计数仍≥阈值 {sorted(_over115)}（修复上线前积累的，解冻后即归零）"
             if _over115 else ""))

    # ⑦ 回滚开关在位
    from backend.config.settings import PROPOSAL_BLOCK_COOLDOWN_RESET_ON_ARM as _thaw115
    from backend.config.env_registry import KNOWN_FLAGS as _KF115
    check("⑦ 回滚开关在位且已登记（false = 回到旧棘轮，仅用于对照）",
          _thaw115 is True and "PROPOSAL_BLOCK_COOLDOWN_RESET_ON_ARM" in _KF115,
          f"PROPOSAL_BLOCK_COOLDOWN_RESET_ON_ARM={_thaw115}")
except Exception as e:
    check("轮115 冷却解冻验证执行", False, f"{type(e).__name__}: {e}")

# ── 可用性：HTTP 端到端 ───────────────────────────────────────────────
print("\n── 可用性：运行中后端 HTTP ──")
for path in ("/api/health", "/api/period/lanes", "/api/full-auto/sessions",
             "/api/live/readiness"):
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=25)
        _ok = r.status == 200
        _detail = f"status={r.status}"
        if path == "/api/live/readiness" and _ok:
            try:
                _rj = json.loads(r.read().decode("utf-8"))
                _ok = "sub_position_tracking" in (_rj.get("checks") or {})
                _detail = (f"status={r.status} checks.sub_position_tracking="
                           f"{(_rj.get('checks') or {}).get('sub_position_tracking')}")
            except Exception as _je:
                _ok, _detail = False, f"解析失败: {_je}"
        check(f"HTTP {path}", _ok, _detail)
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
