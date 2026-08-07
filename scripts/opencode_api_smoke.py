"""OpenCode 智能中心 API 在线冒烟测试（需后端 8000 运行）。"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0


def get(path: str, expect: int = 200):
    req = urllib.request.Request(f"{BASE}{path}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        assert resp.status == expect, resp.status
        return json.loads(resp.read().decode())


def post(path: str, expect: int = 200):
    req = urllib.request.Request(f"{BASE}{path}", method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=30) as resp:
        assert resp.status == expect, resp.status
        return json.loads(resp.read().decode())


def patch_json(path: str, body: dict, expect: int = 200):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        method="PATCH",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        assert resp.status == expect, resp.status
        return json.loads(resp.read().decode())


def check(name: str, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  [PASS] {name}")
    except Exception as e:
        FAIL += 1
        print(f"  [FAIL] {name} — {e}")


def main():
    print("=== OpenCode API 在线冒烟 ===\n")

    def t_status():
        d = get("/api/opencode/status")
        assert "bridge" in d and "pace" in d and "shadow" in d

    def t_config():
        d = get("/api/opencode/config")
        assert "OPENCODE_ENABLED" in d and "note" in d

    def t_insights():
        d = get("/api/opencode/insights?limit=5")
        assert "items" in d and "open_major_count" in d

    def t_proposals():
        d = get("/api/opencode/proposals")
        assert "items" in d

    def t_tuning():
        d = get("/api/opencode/tuning")
        assert "master_reduce_min_loss_pct" in d

    def t_paper_pace():
        d = get("/api/opencode/paper-pace")
        assert "gear" in d

    def t_unlock():
        d = post("/api/opencode/paper-pace/unlock")
        assert "gear" in d

    def t_reports_dir():
        d = get("/api/opencode/reports/dir")
        assert "files" in d

    def t_srr():
        d = get("/api/analytics/strategy-runtime?window=24h&domain=ai")
        assert "window" in d and "win_rate" in d

    def t_policy():
        d = get("/api/opencode/policies/master_close")
        assert "content" in d and len(d["content"]) > 0

    def t_shadow():
        d = get("/api/opencode/shadow/status")
        assert "enabled" in d

    def t_traversal():
        try:
            get("/api/opencode/reports/content?file=../secrets", expect=200)
            raise AssertionError("should have failed")
        except urllib.error.HTTPError as e:
            assert e.code == 400

    def t_patch_pace():
        orig = get("/api/opencode/paper-pace")
        gear = orig["gear"]
        d = patch_json("/api/opencode/paper-pace", {"gear": gear, "manual": False})
        assert d["gear"] == gear

    for name, fn in [
        ("GET /api/opencode/status", t_status),
        ("GET /api/opencode/config", t_config),
        ("GET /api/opencode/insights", t_insights),
        ("GET /api/opencode/proposals", t_proposals),
        ("GET /api/opencode/tuning", t_tuning),
        ("GET /api/opencode/paper-pace", t_paper_pace),
        ("POST /api/opencode/paper-pace/unlock", t_unlock),
        ("GET /api/opencode/reports/dir", t_reports_dir),
        ("GET /api/analytics/strategy-runtime", t_srr),
        ("GET /api/opencode/policies/master_close", t_policy),
        ("GET /api/opencode/shadow/status", t_shadow),
        ("GET reports/content 路径穿越拒绝", t_traversal),
        ("PATCH /api/opencode/paper-pace", t_patch_pace),
    ]:
        check(name, fn)

    print(f"\n=== 结果: {PASS} PASS / {FAIL} FAIL ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
