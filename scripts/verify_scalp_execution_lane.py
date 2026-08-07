#!/usr/bin/env python3
"""ScalpExecutionLane 全面验收脚本（Phase 0–4）。"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT)

PASS = 0
FAIL = 0
WARN = 0


def ok(name: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))


def bad(name: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def warn(name: str, detail: str = "") -> None:
    global WARN
    WARN += 1
    print(f"  [WARN] {name}" + (f" — {detail}" if detail else ""))


def check_imports() -> None:
    print("\n== 1) 模块导入 ==")
    try:
        from backend.services.scalp.scalp_advisory_cache import scalp_advisory_cache, ScalpAdvisory
        from backend.services.scalp.scalp_execution_gate import scalp_execution_gate, GateDecision
        from backend.services.scalp.scalp_flash_veto import scalp_flash_veto, VetoResult
        from backend.services.scalp.scalp_structure_scanner import scalp_structure_scanner
        from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
        from backend.services.scalp_factor_router import scalp_factor_router, ScalpSignal
        from backend.database.models import ScalpVetoAudit
        ok("scalp 模块 + ScalpVetoAudit 模型")
    except Exception as exc:
        bad("模块导入", str(exc))


def check_settings() -> None:
    print("\n== 2) 配置默认值 ==")
    from backend.config import settings as s
    checks = [
        ("SCALP_DIRECT_THRESHOLD", 45),
        ("SCALP_MASTER_HARD_BLOCK", True),
        ("SCALP_EXECUTION_LANE_ENABLED", True),
        ("SCALP_VETO_MODE", "tiered"),
        ("SCALP_VETO_TIMEOUT_S", 5),
        ("SCALP_VETO_FAIL_OPEN", True),
    ]
    for key, expected in checks:
        val = getattr(s, key, None)
        if val == expected:
            ok(key, str(val))
        else:
            bad(key, f"期望 {expected}, 实际 {val}")

    # PAPER_FAST_TRIAL 会放宽 scalp 门槛（减门策略），按运行时实际值校验关系
    confirm = int(getattr(s, "SCALP_FACTOR_CONFIRM_THRESHOLD", 35) or 35)
    execute = int(getattr(s, "SCALP_FACTOR_EXECUTE_THRESHOLD", 45) or 45)
    veto_low = int(getattr(s, "SCALP_VETO_BAND_LOW", 35) or 35)
    if confirm <= execute and veto_low <= confirm:
        ok("Scalp 门槛关系", f"confirm={confirm} execute={execute} veto_low={veto_low}")
    else:
        bad("Scalp 门槛关系", f"confirm={confirm} execute={execute} veto_low={veto_low}")


def check_gate_tiers() -> None:
    print("\n== 3) Gate 分层逻辑 ==")
    import pandas as pd
    from backend.services.scalp.scalp_execution_gate import scalp_execution_gate
    from backend.services.scalp_factor_router import ScalpSignal

    rows = [{"open": 100, "high": 101, "low": 99, "close": 100} for _ in range(50)]
    md = {
        "price": 100,
        "klines": pd.DataFrame(rows),
        "orchestrator": {"long_bias": "bullish", "short_bias": "bullish", "final_action": "enter"},
    }
    g_veto = scalp_execution_gate.evaluate(
        "BTC", ScalpSignal(action="buy", factor_score=42, direction="long", entry_price=100), md, 1
    )
    if g_veto.allowed and g_veto.tier == "veto" and g_veto.needs_veto:
        ok("score=42 → veto tier")
    else:
        bad("veto tier", f"allowed={g_veto.allowed} tier={g_veto.tier}")

    g_direct = scalp_execution_gate.evaluate(
        "BTC", ScalpSignal(action="buy", factor_score=48, direction="long", entry_price=100), md, 1
    )
    if g_direct.allowed and g_direct.tier == "direct" and not g_direct.needs_veto:
        ok("score=48 → direct tier")
    else:
        bad("direct tier", f"tier={g_direct.tier}")

    g_hold = scalp_execution_gate.evaluate(
        "BTC", ScalpSignal(action="buy", factor_score=30, direction="long", entry_price=100), md, 1
    )
    from backend.config import settings as s
    veto_low = int(getattr(s, "SCALP_VETO_BAND_LOW", 35) or 35)
    if 30 < veto_low:
        if not g_hold.allowed:
            ok(f"score=30 < veto_low={veto_low} → hold/block")
        else:
            bad("低分应拦截", f"allowed={g_hold.allowed}")
    else:
        if g_hold.allowed and g_hold.tier in ("veto", "direct"):
            ok(f"score=30 ≥ veto_low={veto_low} → 允许(veto/direct)")
        else:
            bad("放宽门槛下 score=30", f"allowed={g_hold.allowed} tier={g_hold.tier}")


def check_structure_sl() -> None:
    print("\n== 4) 结构 SL vs swing low ==")
    try:
        from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
        from backend.services.kline_data_service import kline_service
        import pandas as pd

        sym = "BTC"
        raw = kline_service.get_klines_from_db(sym, "5m", 100, exchange="hyperliquid")
        if not raw or len(raw) < 40:
            warn("K线数据不足", "跳过 SL 结构验证")
            return
        df = pd.DataFrame(raw)
        swing_low, _, _ = structure_stop_calculator.swing_levels(df)
        price = float(df["close"].iloc[-1])
        _, _, new_sl, _ = structure_stop_calculator.compute_sl_tp(
            {"klines": df, "price": price}, side="long", entry=price,
            swing_low=swing_low, swing_high=float(df["high"].max()),
        )
        if new_sl < swing_low:
            ok(f"{sym} SL 在 swing low 下方", f"sl={new_sl:.2f} swing={swing_low:.2f}")
        else:
            bad(f"{sym} SL 未在 swing low 下方", f"sl={new_sl:.2f} swing={swing_low:.2f}")
    except Exception as exc:
        bad("结构 SL", str(exc))


def check_veto_fail_open() -> None:
    print("\n== 5) Flash Veto fail-open ==")
    from backend.services.scalp.scalp_flash_veto import scalp_flash_veto, VetoResult
    from backend.config.settings import SCALP_VETO_FAIL_OPEN

    if not SCALP_VETO_FAIL_OPEN:
        warn("SCALP_VETO_FAIL_OPEN=false", "跳过 fail-open 测试")
        return

    # 单元：_fallback 必须 accept
    fb = scalp_flash_veto._fallback(time.time(), "unit_test_timeout")
    if fb.verdict == "accept" and fb.source == "fallback":
        ok("_fallback → accept")
    else:
        bad("_fallback", f"verdict={fb.verdict}")

    # 集成：无 LLM 响应（mock）必须 accept
    import backend.services.scalp.scalp_flash_veto as veto_mod
    _orig = veto_mod.call_llm_api_sync if hasattr(veto_mod, "call_llm_api_sync") else None
    try:
        from backend.services import llm_config_service
        _real = llm_config_service.call_llm_api_sync

        def _mock_no_response(*a, **k):
            return None

        llm_config_service.call_llm_api_sync = _mock_no_response
        r = scalp_flash_veto.evaluate({"symbol": "BTC", "side": "buy", "score": 40}, account_id=1)
        if r.verdict == "accept" and r.source == "fallback":
            ok("LLM 无响应 → fail-open accept")
        else:
            bad("fail-open 集成", f"verdict={r.verdict} source={r.source}")
    finally:
        if _orig is None:
            from backend.services import llm_config_service
            llm_config_service.call_llm_api_sync = _real


def check_db_table() -> None:
    print("\n== 6) scalp_veto_audit 表 ==")
    try:
        from backend.database.connection import analytics_engine
        from sqlalchemy import inspect
        insp = inspect(analytics_engine)
        if insp.has_table("scalp_veto_audit"):
            ok("scalp_veto_audit 表存在")
        else:
            bad("scalp_veto_audit 表不存在")
    except Exception as exc:
        bad("DB 检查", str(exc))


def check_wiring() -> None:
    print("\n== 7) 代码接线 ==")
    svc_path = ROOT / "backend" / "services" / "full_auto_trading_service.py"
    text = svc_path.read_text(encoding="utf-8")
    checks = [
        ("start_session OrchBG", "_ensure_orchestrator_bg_running(session_id,"),
        ("Master hard block", "SCALP_MASTER_HARD_BLOCK"),
        ("ScalpExecutionGate", "scalp_execution_gate.evaluate"),
        ("Flash Veto", "scalp_flash_veto.evaluate"),
        ("lane_decision_id", "lane_decision_id"),
        ("stop OrchBG", "_orch_bg_running = False"),
        ("StructureScanner OrchBG", "scalp_structure_scanner.scan"),
    ]
    for name, needle in checks:
        if needle in text:
            ok(name)
        else:
            bad(name, f"未找到: {needle[:50]}")


def check_api(base: str = "http://127.0.0.1:8000") -> None:
    print(f"\n== 8) 运行时 API ({base}) ==")

    def get(path: str, timeout: int = 10):
        req = urllib.request.Request(f"{base}{path}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()

    try:
        status, _ = get("/docs", 15)
        if status == 200:
            ok("后端 /docs 可访问")
        else:
            bad("/docs", f"status={status}")
    except Exception as exc:
        bad("后端健康", str(exc))
        return

    # 查找 running full-auto session
    try:
        status, body = get("/api/full-auto/sessions", 15)
        if status != 200:
            warn("sessions API", f"status={status}")
            return
        data = json.loads(body.decode("utf-8"))
        sessions = data if isinstance(data, list) else data.get("sessions") or data.get("items") or []
        running = [s for s in sessions if (s.get("status") or "").lower() in ("running", "defensive")]
        if not running:
            warn("无 running FullAuto 会话", "OrchBG/Scalp 运行时日志需手动启动会话后观察")
            return
        sid = running[0].get("session_id") or running[0].get("id")
        ok(f"发现 running 会话", sid)
        st, st_body = get(f"/api/full-auto/status/{sid}", 20)
        if st == 200:
            st_data = json.loads(st_body.decode("utf-8"))
            ms = st_data.get("last_market_summary") or {}
            has_adv = any(
                isinstance(v, dict) and v.get("scalp_advisory")
                for v in ms.values()
            )
            if has_adv:
                ok("session status 含 scalp_advisory")
            else:
                warn("scalp_advisory 尚未写入", "OrchBG 首轮评估约 10min，或会话刚启动")
        else:
            warn("session status", f"status={st}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            warn("sessions API 路径可能不同", str(exc))
        else:
            warn("sessions API", str(exc))
    except Exception as exc:
        warn("sessions API", str(exc))


def check_backend_log() -> None:
    print("\n== 9) 后端日志关键字 ==")
    log_path = ROOT / "logs" / "backend.log"
    if not log_path.exists():
        warn("backend.log 不存在")
        return
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
        tail = text[-80000:] if len(text) > 80000 else text
        if "Application startup complete" in tail or "Uvicorn running" in tail:
            ok("uvicorn 启动完成")
        else:
            warn("未找到 startup complete", "可能仍在启动")
        errors = [ln for ln in tail.splitlines() if "Scalp" in ln and "Error" in ln]
        if errors:
            bad("Scalp 相关错误", errors[-1][:120])
        else:
            ok("无 Scalp Error 日志")
        if "OrchBG" in tail:
            ok("OrchBG 日志已出现")
        else:
            warn("OrchBG 日志", "需 FullAuto 会话 running 后才出现")
    except Exception as exc:
        warn("日志读取", str(exc))


def main() -> int:
    print("=" * 60)
    print(" ScalpExecutionLane 全面验收")
    print("=" * 60)
    check_imports()
    check_settings()
    check_gate_tiers()
    check_structure_sl()
    check_veto_fail_open()
    check_db_table()
    check_wiring()
    check_api()
    check_backend_log()

    print("\n" + "=" * 60)
    print(f" 结果: PASS={PASS}  FAIL={FAIL}  WARN={WARN}")
    print("=" * 60)
    if FAIL > 0:
        print("验收未通过，请修复 FAIL 项后重试。")
        return 1
    if WARN > 0:
        print("静态验收通过；WARN 项需运行中会话/OrchBG 时间后复验。")
    else:
        print("全面验收通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
