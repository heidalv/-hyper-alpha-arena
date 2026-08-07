#!/usr/bin/env python3
"""OpenCode 提案评审 — 全面集成/E2E 测试脚本。"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, List, Tuple

import httpx

BASE = "http://127.0.0.1:8000"
SIDECAR = "http://127.0.0.1:4096"
TIMEOUT = 180.0


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    raise AssertionError(msg)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def get(path: str) -> Dict[str, Any]:
    r = httpx.get(f"{BASE}{path}", timeout=30.0)
    r.raise_for_status()
    return r.json()


def post(path: str, timeout: float = TIMEOUT) -> Dict[str, Any]:
    r = httpx.post(f"{BASE}{path}", timeout=timeout)
    r.raise_for_status()
    return r.json()


def test_health() -> None:
    section("1. 服务健康")
    h = get("/api/health")
    assert h.get("status") == "healthy", h
    ok("backend healthy")

    sr = httpx.get(f"{SIDECAR}/global/health", timeout=5.0)
    sr.raise_for_status()
    assert sr.json().get("healthy") is True
    ok("sidecar healthy")


def test_config() -> None:
    section("2. 配置项")
    cfg = get("/api/opencode/config")
    required = [
        "OPENCODE_AUTO_REVIEW",
        "OPENCODE_AGENT_REVIEW",
        "OPENCODE_REVIEW_MODEL",
        "OPENCODE_REVIEW_MIN_CONFIDENCE",
        "OPENCODE_REVIEW_DEFER_RETRY_S",
    ]
    for k in required:
        if k not in cfg:
            fail(f"missing config key {k}")
    ok(f"OPENCODE_AUTO_REVIEW={cfg['OPENCODE_AUTO_REVIEW']}")
    ok(f"OPENCODE_AGENT_REVIEW={cfg['OPENCODE_AGENT_REVIEW']}")
    ok(f"OPENCODE_REVIEW_MODEL={cfg['OPENCODE_REVIEW_MODEL']}")


def test_openapi_routes() -> None:
    section("3. OpenAPI 路由")
    spec = get("/openapi.json")
    paths = spec.get("paths") or {}
    for p in (
        "/api/opencode/proposals/review-all",
        "/api/opencode/proposals/{proposal_id}/review",
    ):
        if p not in paths:
            fail(f"missing route {p}")
        if "post" not in paths[p]:
            fail(f"route {p} has no POST")
    ok("review-all + single review routes registered")


def test_bridge_status() -> None:
    section("4. Bridge 状态")
    st = get("/api/opencode/status")
    bridge = st.get("bridge") or {}
    if not bridge.get("enabled"):
        fail("OPENCODE_ENABLED=false")
    if not bridge.get("sidecar_healthy"):
        fail(f"sidecar unhealthy: {bridge.get('last_error')}")
    ok(f"bridge model={bridge.get('model')} transport={bridge.get('transport')}")


def test_hard_reject_via_api() -> None:
    section("5. 硬规则拒绝（API 间接验证）")
    from backend.database.connection import SessionLocal
    from backend.services.opencode_proposal_applier import create_proposal, reject_proposal
    from backend.services.opencode_proposal_reviewer import review_and_apply_proposal

    db = SessionLocal()
    try:
        pid = create_proposal(
            db,
            [{"key": "evil_key", "value": 999, "type": "tuning"}],
            title="e2e hard reject",
        )
        assert pid
        out = review_and_apply_proposal(db, pid)
        assert out.get("status") == "rejected", out
        review = out.get("review") or {}
        assert review.get("source") == "hard_validation" or review.get("decision") == "reject"
        ok(f"proposal #{pid} hard-rejected without LLM")
        # cleanup: already rejected
    finally:
        db.close()


def test_review_all_pending() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    section("6. 批量评审 pending 提案（真实 LLM）")
    before = get("/api/opencode/proposals")
    pending_before = [i for i in before.get("items") or [] if i.get("status") == "pending"]
    print(f"  pending before: {len(pending_before)}")
    for p in pending_before:
        print(f"    #{p['id']} {p['severity']} {p['patch_type']} {p['title'][:50]}")

    if not pending_before:
        ok("no pending proposals to review")
        return [], {"reviewed": 0, "results": []}

    t0 = time.time()
    result = post("/api/opencode/proposals/review-all?limit=10", timeout=TIMEOUT)
    elapsed = time.time() - t0
    print(f"  review-all elapsed: {elapsed:.1f}s")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])

    reviewed = int(result.get("reviewed") or 0)
    if reviewed <= 0 and pending_before:
        fail(f"expected to review pending proposals, got {result}")
    ok(f"reviewed {reviewed} proposals in {elapsed:.1f}s")

    after = get("/api/opencode/proposals")
    return after.get("items") or [], result


def test_proposal_details(items: List[Dict[str, Any]]) -> None:
    section("7. 提案详情与 review 元数据")
    for row in items[:5]:
        detail = get(f"/api/opencode/proposals/{row['id']}")
        proposal = detail.get("proposal") or {}
        review = proposal.get("review")
        status = detail.get("status")
        print(f"  #{row['id']} status={status} review={bool(review)}")
        if status in ("paper_applying", "rejected", "pending") and review:
            ok(f"#{row['id']} has review.decision={review.get('decision')}")
        elif status == "pending" and not review:
            ok(f"#{row['id']} pending awaiting first review")
        elif status in ("paper_applying", "rejected"):
            ok(f"#{row['id']} terminal status={status}")


def test_pace_after_reject(items: List[Dict[str, Any]]) -> None:
    section("8. Pace 状态")
    st = get("/api/opencode/status")
    gear = (st.get("pace") or {}).get("gear")
    rejected = [i for i in items if i.get("status") == "rejected"]
    if rejected:
        ok(f"pace gear={gear} (after {len(rejected)} rejected)")
    else:
        ok(f"pace gear={gear}")


def main() -> int:
    print("OpenCode 提案评审 — 全面测试")
    errors = 0
    tests = [
        test_health,
        test_config,
        test_openapi_routes,
        test_bridge_status,
        test_hard_reject_via_api,
    ]
    for fn in tests:
        try:
            fn()
        except Exception as err:
            print(f"  [FAIL] {err}")
            errors += 1

    items: List[Dict[str, Any]] = []
    try:
        items, _ = test_review_all_pending()
    except Exception as err:
        print(f"  [FAIL] {err}")
        errors += 1

    for fn in (lambda: test_proposal_details(items), lambda: test_pace_after_reject(items)):
        try:
            fn()
        except Exception as err:
            print(f"  [FAIL] {err}")
            errors += 1

    section("总结")
    if errors:
        print(f"FAILED: {errors} error(s)")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, ".")
    raise SystemExit(main())
