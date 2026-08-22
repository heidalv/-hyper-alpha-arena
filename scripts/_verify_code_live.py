# -*- coding: utf-8 -*-
"""代码生效验证：运行中的后端是否已加载最新代码？

用法：python scripts/verify_code_live.py
前提：后端已启动（8000 监听）。

原理：
  后端 /api/health 现在返回 boot_fingerprint：
    - boot_git_hash : 后端进程【启动时】的 git HEAD
    - code_git_head : 磁盘当前 git HEAD
    - live_markers  : 关键改动的运行时存在性（每项 True/False）
  三者对比即可回答"改动生效了吗"。

退出码：0=全部一致且标记通过；1=存在不一致/缺失。
"""
import json
import subprocess
import sys
import urllib.request

HEALTH = "http://127.0.0.1:8000/api/health"


def git_head() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=r"D:\001Alpha\Hyper-Alpha-Arena",
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def fetch_health(retries: int = 5, wait_s: int = 4):
    """带重试的健康读取（后端启动风暴期可能偶尔无响应）。"""
    last_err = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(HEALTH, timeout=8) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            import time
            time.sleep(wait_s)
    raise last_err


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'✅' if cond else '❌'}] {label} {detail}")
    return cond


def main() -> int:
    print("=" * 66)
    print(" 代码生效验证（运行中后端 vs 磁盘代码）")
    print("=" * 66)

    try:
        data = fetch_health()
    except Exception as e:
        print(f"\n  ❌ 无法连接后端 {HEALTH}: {e}")
        print("  请先运行 启动新代码.bat 或 dev-start.bat")
        return 1

    fp = data.get("boot_fingerprint", {})
    version = data.get("version", "?")
    print(f"\n  后端版本: {version}")
    print(f"  启动 ISO: {fp.get('boot_at_iso', '?')}")

    disk_head = git_head()
    boot_head = fp.get("boot_git_hash", "unknown")
    disk_matches = fp.get("matches_disk", False)

    all_ok = True
    all_ok &= ok("后端运行代码 == 磁盘最新 HEAD",
                 boot_head == disk_head and disk_matches,
                 f"boot={boot_head}  disk={disk_head}  matches={disk_matches}")
    print("")

    print("  关键改动运行时标记:")
    markers = fp.get("live_markers", {})
    expect = {
        "ev_governor_audit": "EV Governor 每日资金分配（利润缩放）",
        "m0_11_min_hold": "M0-11 中长线复查平仓 min_hold 保护（12h/72h）",
        "m0_6_cross_layer": "M0-6 持仓管理跨层防护（防平错腿）",
        "m1_1_tenant_auto": "M1-1 自动租户填列（多租户修复）",
        "profit_peak_trail": "PROFIT-1 峰值追踪离场（保本升级）",
        "factor_cost_9bp": "FACTOR-1 因子评审成本 9bps",
    }
    for key, label in expect.items():
        val = markers.get(key)
        if val is None:
            all_ok &= ok(f"{label}", False, "（后端无此标记，可能旧版本）")
        else:
            all_ok &= ok(f"{label}", val)

    print("")
    print("=" * 66)
    if all_ok:
        print("  ✅ 结论：运行中的后端已加载全部最新代码与关键改动。")
    else:
        print("  ⚠️ 结论：存在不一致——运行中的是旧进程。")
        print("     请先停止后端（scripts\\stop-dev.ps1）再用 启动新代码.bat 重启，")
        print("     重启后重新运行本脚本直到全绿。")
    print("=" * 66)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
