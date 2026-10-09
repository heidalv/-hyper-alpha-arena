"""h481：**注册表 vs 运行态**参数对照（部署后验证的标准工具）。

为什么需要（本仓库反复踩的坑）：
  `lane_registry.meta.params` 是**意图**，运行进程里生效的是**另一份**对象：
    · `_maybe_reload_meta()` 每 60s 热采用一次；
    · 但 `apply_env_param_overrides()` 让 **7 个键的 env 值优先**（进程启动时冻结）；
    · 且 `LaneRiskLimits(**{k for k in fields})` 会**静默丢弃**运行类里不存在的键
      （新参数不重启就部署 ⇒ 部署脚本打印"✓ 已部署"，实际是空转）。
  ⇒ "部署脚本说成功" 与 "进程真的用了" 是两件事。历史上 F189/F280/F298/F327
    都是这一类静默偏差。

本脚本直接读 **worker 自己写进 `logs/mm_lane_status.json` 的生效回显**
（`params` / `limits` / `param_authority`），与注册表逐键比对，输出三类：
  · **一致**：部署已生效；
  · **不一致**：给出原因（env 覆盖 / 未热采用 / 类里没有该字段）；
  · 注册表有而回显没有：**该键被静默丢弃**（最危险的一类，必须重启 worker）。

用法：python scripts/h481_runtime_param_echo.py [--key max_one_side_seconds]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATUS = ROOT / "logs" / "mm_lane_status.json"
OUT = ROOT / "research_l1" / "out" / "h481_param_echo.json"
ENV_WHITELIST = {
    "spread_mult": "MM_SPREAD_MULT", "spread_mult_reduce": "MM_SPREAD_MULT_REDUCE",
    "min_edge_frac": "MM_MIN_EDGE_FRAC", "spread_cross_margin": "MM_SPREAD_CROSS_MARGIN",
    "w_base_bp": "MM_W_BASE_BP", "min_width_bp": "MM_MIN_WIDTH_BP", "k_inv": "MM_K_INV",
}


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="", help="只关心某个键")
    a = ap.parse_args()
    st = json.loads(STATUS.read_text(encoding="utf-8"))
    age = time.time() - float(st.get("ts") or 0)
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            m = cur.fetchone()[0]
    reg = dict(m.get("params") or {})
    rt_p = dict(st.get("params") or {})
    rt_l = dict(st.get("limits") or {})
    rt = {**rt_p, **rt_l}
    print(f"状态文件年龄 {age:.0f}s  ok={st.get('ok')}  ticks={st.get('ticks')}  "
          f"equity={st.get('equity')}")
    print(f"运行态回显：params {len(rt_p)} 键 / limits {len(rt_l)} 键"
          f"  param_authority={st.get('param_authority')}")
    print("=" * 96)
    keys = sorted(set(reg) | set(rt))
    if a.key:
        keys = [k for k in keys if k == a.key]
    same = diff = missing = default_only = 0
    for k in keys:
        rv, tv = reg.get(k, "<缺>"), rt.get(k, "<缺>")
        try:
            eq = (rv != "<缺>" and tv != "<缺>" and abs(float(rv) - float(tv)) < 1e-9) \
                or rv == tv
        except Exception:  # noqa: BLE001
            eq = rv == tv
        if eq:
            same += 1
            flag = "✓ 一致"
        elif rv == "<缺>":
            # 注册表没指定、运行态显示 dataclass 默认值 ⇒ **正常**，不是缺陷
            default_only += 1
            flag = "· 注册表未指定（运行态=dataclass 默认值）"
        elif tv == "<缺>":
            missing += 1
            flag = "✗ 运行态无此键（不在 QuoteParams/LaneRiskLimits 里：可能是别的通道，"
            flag += "若是新加的字段则必须先重启 worker）"
        else:
            diff += 1
            why = ""
            if k in ENV_WHITELIST:
                why = f"（env 白名单键 {ENV_WHITELIST[k]} 优先于注册表）"
            flag = f"✗ **不一致**{why}"
        if not flag.startswith("✓") or a.key:
            print(f"{k:28s} 注册表={str(rv):>10s}  运行态={str(tv):>10s}  {flag}")
    print("=" * 96)
    print(f"一致 {same} / **真不一致 {diff}** / 运行态缺键 {missing} / "
          f"注册表未指定 {default_only}")
    print("判读：① 只看你要部署的那个键；② 真不一致 ⇒ 查 env 白名单；"
          "③ 『运行态缺键』若是**新加字段**则该次部署是空转，必须先重启 worker。")
    OUT.write_text(json.dumps(
        {"status_age_s": round(age, 1), "n_registry": len(reg), "n_runtime": len(rt),
         "same": same, "diff": diff, "missing": missing,
         "runtime_params": rt_p, "runtime_limits": rt_l,
         "param_authority": st.get("param_authority")},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
