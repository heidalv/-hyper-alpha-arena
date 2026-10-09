"""h581 — ④（h527 逐币单笔规模）的**上线前预检**（只读，R85）。

对应 ③ 的 `h553_chain_preflight.py`，但多查两条 ④ 特有的东西：
  1. **体制对齐**（R69 的教训）：④ 的基线窗 = 部署前 12h（`since−12h → since`）
     必须**完全落在 ③ 的体制之内**（③ 于 09-30 09:58 部署）⇒ ④ 不能在 ③ 部署后
     12h 之内启动，否则基线里混着 ③ 之前/之后的参数，对比失真。
     ④ 排在 09-30 22:08 ⇒ 基线的起点 10:08 **晚于** ③ 的部署时刻 ✓。
  2. **dict 参数的首次生产部署**：`limits.per_symbol_size_mult` 是**首次**部署的逐币字典
     ⇒ 查它在引擎 schema 里存在、注册表当前为 `null`、目标值与回滚值形状正确。

用法：python scripts/h581_preflight_527.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import csv
import datetime as dt
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

CHAIN = "DSH_HFT_H527_CHAIN"
DEP_CHAIN = "DSH_HFT_H464_CHAIN"
WRITERS = ("h425_repair_trial.py", "h356_universe_trial.py", "h354_p2_judge.py",
           "h464_chain.py", "h527_chain.py", "h144_restart_backend.py")


def _task(name: str) -> dict:
    q = subprocess.run(["schtasks", "/query", "/tn", name, "/fo", "LIST", "/v"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    out = {}
    for line in (q.stdout or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _parse(raw: str) -> "dt.datetime | None":
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return dt.datetime.strptime(raw, fmt)
        except Exception:  # noqa: BLE001
            continue
    return None


def main() -> int:
    ok = True
    print("=" * 96)
    print("④（h527）上线前预检")
    print("=" * 96)

    print("\n[1] 链任务与文件依赖")
    ci = _task(CHAIN)
    nx = ci.get("Next Run Time", "")
    print(f"  {CHAIN}: Status={ci.get('Status')} Next={nx}")
    tr = ci.get("Task To Run", "")
    for need in ("run-quiet.vbs", "h527_chain.py"):
        good = need in tr
        ok &= good
        print(f"  {'✓' if good else '✗'} 命令行含 {need}")
    for rel in ("scripts/run-quiet.vbs", "scripts/h527_chain.py",
                "scripts/h425_repair_trial.py", "scripts/h481_runtime_param_echo.py"):
        good = (ROOT / rel).exists()
        ok &= good
        print(f"  {'✓' if good else '✗'} {rel}")

    print("\n[2] 依赖：③ 是否终局（且**有判定产物**，R83）")
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        params = dict(meta.get("params") or {})
        dep = dict(meta.get("h464_trial") or {})
        art = ROOT / "research_l1" / "out" / "h464_verdict.json"
        print(f"  h464_trial: verdict={dep.get('verdict')!r} "
              f"started_at={dep.get('started_at')} judge_at={dep.get('judge_at')}")
        print(f"  ③ 判定产物 {art.name} 存在? {art.exists()}"
              f"（R83：终局**必须**伴随产物才放行；人工回滚不写产物）")
        if str(dep.get("verdict") or "") in ("PASS", "ROLLBACK") and not art.exists():
            print("  ⇒ 当前是'终局但无产物'（R29 插曲留下）⇒ 链会**拦下等待** ✓ 这是正确行为")

        print("\n[3] ④ 的部署可行性（dict 参数首次生产部署）")
        spec = h.SPECS["h527"]
        print(f"  field={spec['field']}  目标={json.dumps(spec['to'], ensure_ascii=False)}"
              f"  回滚={json.dumps(spec['rollback_to'], ensure_ascii=False)}")
        print(f"  注册表现值 per_symbol_size_mult="
              f"{json.dumps(params.get('per_symbol_size_mult'), ensure_ascii=False)}")
        print(f"  强制转换后 = {json.dumps(h._coerce_param(spec['to']), ensure_ascii=False)} ✓")
        if "per_symbol_size_mult" not in (params or {}) and \
                params.get("per_symbol_size_mult") is None:
            print("  （当前为 null/未设置 ⇒ 引擎按'不缩放'处理，默认关闭 ✓）")
        print("  ⚠️ 逐币 dict 参数**从未在生产部署过** ⇒ 链里的 `h481` 回显是关键验证点")

    print("\n[4] 体制对齐（R69 的教训）：④ 的基线窗必须完全落在 ③ 的体制之内")
    dep_chain = _task(DEP_CHAIN)
    d3 = _parse(dep_chain.get("Next Run Time", ""))
    d4 = _parse(nx)
    if d3 and d4:
        gap_h = (d4 - d3).total_seconds() / 3600.0
        base_start = d4 - dt.timedelta(hours=12)
        good = gap_h >= 12.0
        ok &= good
        print(f"  ③ 部署（={DEP_CHAIN} 触发）= {d3:%m-%d %H:%M}；"
              f"④ 部署 = {d4:%m-%d %H:%M} ⇒ 相隔 {gap_h:.1f}h")
        print(f"  ④ 的基线窗 = {base_start:%m-%d %H:%M} → {d4:%m-%d %H:%M}")
        print(f"  {'✓' if good else '✗'} 基线起点 {'晚于' if good else '**早于**'} ③ 的部署时刻"
              f" ⇒ {'④ 只在 ③ 一个变量上与基线不同' if good else '基线混着 ③ 前后的参数 ✗'}")
    else:
        print("  ✗ 任务时刻解析失败")
        ok = False

    print("\n[5] ④ 的窗口清洁度（部署 → +12h 内会写参数的任务）")
    if d4:
        w0, w1 = d4, d4 + dt.timedelta(hours=12)
        print(f"  ④ 的窗口 = {w0:%m-%d %H:%M} → {w1:%m-%d %H:%M}")
        q = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/v"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        rows = list(csv.DictReader(io.StringIO(q.stdout or "")))
        risks = []
        for r in rows:
            name = (r.get("TaskName") or "").replace("\\", "")
            if not name.startswith("DSH_") or (r.get("Status") or "") != "Ready":
                continue
            when = _parse((r.get("Next Run Time") or "").strip())
            if not when:
                continue
            if name in (CHAIN, f"DSH_HFT_{'H527'}_JUDGE"):
                continue
            if w0 <= when <= w1 and any(w in (r.get("Task To Run") or "") for w in WRITERS):
                risks.append((when, name))
        for when, name in sorted(risks):
            print(f"  ⚠️ {when:%m-%d %H:%M}  {name}")
        if risks:
            ok = False
            print("  ⇒ 窗口内有会写参数的任务 ⇒ ④ 可能被判 INCONCLUSIVE（先停掉或改期）")
        else:
            print("  ✓ 窗口内没有其它「会写参数」的任务")
    print("=" * 96)
    print("预检结论：" + ("**可以按计划自动部署** ✓" if ok else "**有需要处理的问题** ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
