#!/usr/bin/env python3
"""验证 OpenCode + DeepSeek 配置是否就绪（Sidecar 内已配置 provider，不直连外部 API）。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def main() -> int:
    print("=== OpenCode 配置校验 ===\n")
    errors = 0

    # 1. 项目 opencode.json
    cfg_path = ROOT / "opencode.json"
    if not cfg_path.is_file():
        _fail(f"缺少 {cfg_path}")
        errors += 1
    else:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        _ok(f"opencode.json 存在，default_agent={cfg.get('default_agent', '?')}")
        ds = (cfg.get("provider") or {}).get("deepseek") or {}
        api_key_ref = (ds.get("options") or {}).get("apiKey", "")
        if "{env:DEEPSEEK_API_KEY}" in str(api_key_ref):
            _ok("DeepSeek apiKey 使用 {env:DEEPSEEK_API_KEY}（由 Sidecar 环境注入）")
        else:
            _fail("DeepSeek apiKey 未使用环境变量占位符")
            errors += 1

    # 2. plan agent 文件
    plan_md = ROOT / ".opencode" / "agents" / "plan.md"
    if plan_md.is_file():
        _ok(f"plan agent: {plan_md.relative_to(ROOT)}")
    else:
        _fail(f"缺少 {plan_md}")
        errors += 1

    # 3. 系统提示词
    prompt = ROOT / "backend" / "prompts" / "opencode_analysis_system.md"
    if prompt.is_file():
        _ok(f"系统提示词: {prompt.relative_to(ROOT)}")
    else:
        _fail(f"缺少 {prompt}")
        errors += 1

    # 4. .env DEEPSEEK_API_KEY（供 Sidecar 读取，后端不直连）
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if len(key) >= 8:
        _ok(f"DEEPSEEK_API_KEY 已设置 (len={len(key)})")
    else:
        _fail("DEEPSEEK_API_KEY 未设置 — 请在 .env 填写，Sidecar 启动时会加载")
        errors += 1

    if os.getenv("OPENCODE_ENABLED", "").lower() not in ("1", "true", "yes", "on"):
        _fail("OPENCODE_ENABLED 未开启")
        errors += 1
    else:
        _ok("OPENCODE_ENABLED=true")

    # 4b. 策略运行报告数据就绪（OpenCode 分析前置条件）
    print("\n--- 交易数据就绪 ---")
    try:
        from backend.database.connection import SessionLocal, DATABASE_URL
        from backend.services.opencode_context_pack import build_context_pack

        db = SessionLocal()
        try:
            pack = build_context_pack(db, window="24h", domain="ai")
            dq = pack.get("data_quality") or {}
            closed = int(dq.get("runtime_report_total_closed") or 0)
            db_hint = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL[:48]
            if dq.get("sufficient_for_analysis") and closed >= 5:
                _ok(f"24h 已平仓 {closed} 笔，可运行 OpenCode 分析 (db={db_hint})")
            else:
                _fail(
                    f"24h 已平仓仅 {closed} 笔 (<5)，OpenCode 分析会被跳过 — "
                    f"请确认 DATABASE_URL 指向 PostgreSQL (当前 db={db_hint})"
                )
                errors += 1
        finally:
            db.close()
    except Exception as err:
        _fail(f"无法构建 ContextPack: {err}")
        errors += 1

    # 5. Sidecar 健康
    print("\n--- Sidecar 连通性 ---")
    try:
        import httpx
        url = os.getenv("OPENCODE_SERVER_URL", "http://127.0.0.1:4096").rstrip("/")
        with httpx.Client(timeout=8.0) as client:
            h = client.get(f"{url}/global/health")
            if h.status_code == 200 and (h.json() or {}).get("healthy"):
                ver = (h.json() or {}).get("version", "?")
                _ok(f"Sidecar 在线 {url} version={ver}")
            else:
                _fail(f"Sidecar 不健康: {h.status_code}")
                errors += 1

            prov = client.get(f"{url}/config/providers")
            if prov.status_code == 200:
                data = prov.json()
                deepseek = next((p for p in data.get("providers", []) if p.get("id") == "deepseek"), None)
                if deepseek and deepseek.get("key"):
                    _ok("Sidecar 内 DeepSeek provider 已加载 API Key")
                else:
                    _fail("Sidecar 内 DeepSeek 无 Key — 请用 scripts/start_opencode_sidecar.ps1 重启 Sidecar")
                    errors += 1
            else:
                _fail(f"无法读取 providers: {prov.status_code}")
                errors += 1

            # 6. 短会话冒烟（走 OpenCode 内部 DeepSeek）
            if errors == 0:
                print("\n--- 模型冒烟（OpenCode → DeepSeek）---")
                model_slug = os.getenv("OPENCODE_SMALL_MODEL", "deepseek/deepseek-v4-flash")
                provider, model_id = model_slug.split("/", 1) if "/" in model_slug else ("deepseek", model_slug)
                sess = client.post(
                    f"{url}/session",
                    json={"agent": "plan", "model": {"providerID": provider, "id": model_id}, "title": "verify"},
                )
                if sess.status_code >= 400:
                    _fail(f"创建 session 失败: {sess.text[:300]}")
                    errors += 1
                else:
                    sid = (sess.json() or {}).get("id")
                    msg = client.post(
                        f"{url}/session/{sid}/message",
                        json={
                            "agent": "plan",
                            "model": {"providerID": provider, "modelID": model_id},
                            "parts": [{"type": "text", "text": "Reply JSON only: {\"severity\":\"info\",\"findings\":[]}"}],
                        },
                        timeout=120.0,
                    )
                    if msg.status_code >= 400:
                        _fail(f"模型调用失败: {msg.text[:300]}")
                        errors += 1
                    else:
                        parts = (msg.json() or {}).get("parts") or []
                        texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
                        if texts:
                            _ok(f"模型响应正常: {texts[-1][:120]}...")
                        else:
                            _fail("模型无 text 响应")
                            errors += 1
    except Exception as err:
        _fail(f"Sidecar 不可达: {err} — 请先运行 scripts/start_opencode_sidecar.ps1")
        errors += 1

    print(f"\n=== 结果: {'通过' if errors == 0 else f'{errors} 项失败'} ===")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
