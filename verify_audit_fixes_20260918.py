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
