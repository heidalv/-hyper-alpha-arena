# -*- coding: utf-8 -*-
"""[h779 2026-10-03] 出场成本 KPI:给出场类参数专用的判据(不跟市场波动混在一起)。

背景(22:43 裁决事故):reduce_touch_after_sec 300 和 max_net_directional 0.35
被 30 分钟"每腿净"判负回滚 —— 但每腿净测的是**市场**,不是**参数**;
一条 SI 式跳空腿就能把窗口翻负,而 reduce_touch 的收益(省 12bp 吃价差)
根本不在该指标里。⇒ 出场类参数必须用**机制指标**判:

  · timeout_cross_bp  = 超时 taker 腿的平均**捕获项**(吃价差;现测 −12.2bp)
  · stop_gap_bp       = 止损 taker 腿的最深**价格项**(跳空深度;SI 那次 −360bp)
  · maker_capture_bp  = 自然成交腿的平均捕获(基线,现测 +3.6bp)
  · jump_n / jump_avg_net = 跳变速退腿的数量与平均净(看假摔率)

判据(写进输出,供桥/裁决用):
  · reduce_touch 该开:timeout_cross_bp < −6bp 且 reduce_touch_after_sec == 0
  · jump_exit 该开:stop_gap_bp < −100bp 且 jump_exit_bp == 0
  · jump_exit 假摔:jump_n ≥ 10 且 jump_avg_net < −3bp(2h)⇒ 阈值该上调/关闭
输出 data/exit_cost_last.json;每 10 分钟跑(任务 DSH_MM_EXIT_COST)。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
HOURS = float(__import__("os").environ.get("MM_EXIT_COST_HOURS", "2"))


def _dsn():
    _spec = importlib.util.spec_from_file_location(
        "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
    h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(h)
    return h.read_env_dsn()


def _lane_params() -> dict:
    try:
        sys.path.insert(0, str(ROOT))
        from backend.services import lane_registry as reg
        return ((reg.get_lane(LANE) or {}).get("meta") or {}).get("params") or {}
    except Exception:
        return {}


def main() -> int:
    import psycopg

    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(meta_json->>'exit_path','maker'), count(*),"
            " AVG(spread_bp), AVG(price_bp), MIN(price_bp), AVG(net_bp),"
            " SUM(net_bp*notional)/10000.0"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
            " AND ts > now() - (%s * interval '1 hour')"
            " GROUP BY 1 ORDER BY 7", (LANE, HOURS))
        rows = {str(r[0]): r for r in cur.fetchall()}

    def grp(prefix):
        out = {}
        for k, r in rows.items():
            if str(k).startswith(prefix) or str(k) == prefix:
                out[k] = r
        return out

    tmo = grp("timeout_hard_taker").get("timeout_hard_taker")
    stop = grp("stop_loss_taker").get("stop_loss_taker")
    maker = rows.get("") or rows.get("maker")
    jump = {k: r for k, r in rows.items() if str(k).startswith("jump")}
    jump_n = sum(int(r[1] or 0) for r in jump.values())
    jump_net = None
    if jump_n:
        jump_net = sum(float(r[6] or 0) for r in jump.values()) / jump_n

    out = {
        "ts": time.time(), "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "window_hours": HOURS,
        "maker_n": int(maker[1] or 0) if maker else 0,
        "maker_capture_bp": round(float(maker[2] or 0), 3) if maker else None,
        "timeout_n": int(tmo[1] or 0) if tmo else 0,
        "timeout_cross_bp": round(float(tmo[2] or 0), 3) if tmo else None,
        "timeout_price_bp": round(float(tmo[3] or 0), 3) if tmo else None,
        "timeout_net_bp": round(float(tmo[5] or 0), 3) if tmo else None,
        "stop_n": int(stop[1] or 0) if stop else 0,
        "stop_gap_bp": round(float(stop[4] or 0), 3) if stop else None,
        "jump_n": jump_n,
        "jump_avg_net_bp": round(jump_net, 3) if jump_net is not None else None,
    }
    p = _lane_params()
    out["reduce_touch_after_sec"] = float(p.get("reduce_touch_after_sec") or 0)
    out["jump_exit_bp"] = float(p.get("jump_exit_bp") or 0)
    # 判据
    hints = []
    if out["timeout_cross_bp"] is not None and out["timeout_cross_bp"] < -6.0 \
            and out["reduce_touch_after_sec"] == 0:
        hints.append("reduce_touch 该开(timeout 吃价差 %.1fbp)" % out["timeout_cross_bp"])
    if out["stop_gap_bp"] is not None and out["stop_gap_bp"] < -100.0 \
            and out["jump_exit_bp"] == 0:
        hints.append("jump_exit 该开(止损跳空 %.0fbp)" % out["stop_gap_bp"])
    if jump_n >= 10 and jump_net is not None and jump_net < -3.0:
        hints.append("jump_exit 假摔(n=%d 均净 %.1fbp)" % (jump_n, jump_net))
    out["hints"] = hints
    (ROOT / "data" / "exit_cost_last.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"== 出场成本 KPI(近 {HOURS}h)== ")
    print(f"  自然成交 maker: n={out['maker_n']} 捕获 {out['maker_capture_bp']}bp(基线)")
    print(f"  超时 taker: n={out['timeout_n']} 吃价差 {out['timeout_cross_bp']}bp "
          f"价格项 {out['timeout_price_bp']}bp 净 {out['timeout_net_bp']}bp")
    print(f"  止损 taker: n={out['stop_n']} 最深跳空 {out['stop_gap_bp']}bp")
    print(f"  跳变速退: n={out['jump_n']} 均净 {out['jump_avg_net_bp']}bp")
    for hh in hints:
        print(f"  ⇒ {hh}")
    print("  ✓ 已写 data/exit_cost_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
