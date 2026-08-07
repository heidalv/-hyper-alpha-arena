"""GTX 1070 老卡温度/功耗/显存巡检（v6 10.1 老卡可靠性预案）。

2016 Pascal 卡无硬件温度保护上的软件侧保障，长训/挖掘前必须先巡检，
运行中按固定间隔监控：温度逼近 TjMax、功耗接近 TDP、显存不足都要告警，
并建议"先降负载再继续"。

用法:
    .venv\\Scripts\\python.exe backend/scripts/gpu_guard.py --once          # 单次巡检（默认）
    .venv\\Scripts\\python.exe backend/scripts/gpu_guard.py --watch        # 持续监控（Ctrl+C 退出）
    .venv\\Scripts\\python.exe backend/scripts/gpu_guard.py --watch --interval 60

输出: 单次巡检打印一行状态；--watch 周期打印；任何阈值超限在 stdout 标红提示
（供接入运维脚本 / 计划任务；不在此处杀进程，只预警）。

阈值（GTX 1070 实测口径）:
  - 温度: TjMax=94°C，预警 83°C（85%）
  - 功耗: TDP 151W，预警 135W（90%）
  - 显存: 总 8GB（桌面占用 ~1.6GB），可用 < 512MB 视为不足
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from typing import Dict, List, Optional

TEMP_WARN_C = 83          # GTX 1070 TjMax 94°C 的 ~85%
POWER_WARN_RATIO = 0.90   # TDP 90%
VRAM_MIN_MB = 512         # 可用显存下限（长训 batch 最小余量）

_QUERY = (
    "name,driver_version,memory.total,memory.used,memory.free,"
    "temperature.gpu,power.draw,power.limit"
)


def query_gpu() -> Optional[Dict[str, float]]:
    """nvidia-smi 单次查询 → dict；无 GPU/查询失败返回 None。"""
    try:
        out = subprocess.run(
            [
                "nvidia-smi", "--query-gpu=" + _QUERY,
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    parts = [p.strip() for p in out.stdout.splitlines()[0].split(",")]
    if len(parts) < 8:
        return None
    try:
        return {
            "name": parts[0],
            "driver": parts[1],
            "mem_total_mb": float(parts[2]),
            "mem_used_mb": float(parts[3]),
            "mem_free_mb": float(parts[4]),
            "temp_c": float(parts[5]),
            "power_w": float(parts[6]),
            "power_limit_w": float(parts[7]),
        }
    except ValueError:
        return None


def check(g: Dict[str, float], temp_warn: float = TEMP_WARN_C,
          power_ratio: float = POWER_WARN_RATIO,
          vram_min_mb: float = VRAM_MIN_MB) -> List[str]:
    """阈值判定，返回告警列表（空 = 健康）。"""
    alerts: List[str] = []
    if g["temp_c"] >= temp_warn:
        alerts.append(f"温度 {g['temp_c']:.0f}C >= {temp_warn:.0f}C（TjMax 94C 的 85%），建议暂停长训冷却")
    pl = g["power_limit_w"]
    if pl > 0 and g["power_w"] >= pl * power_ratio:
        alerts.append(f"功耗 {g['power_w']:.0f}W >= {pl * power_ratio:.0f}W（TDP {pl:.0f}W 的 90%）")
    if g["mem_free_mb"] < vram_min_mb:
        alerts.append(f"可用显存 {g['mem_free_mb']:.0f}MB < {vram_min_mb:.0f}MB，长训可能 OOM")
    return alerts


def _fmt(g: Dict[str, float]) -> str:
    return (
        f"{g['name']} | driver {g['driver']} | "
        f"temp {g['temp_c']:.0f}C | power {g['power_w']:.0f}/{g['power_limit_w']:.0f}W | "
        f"vram free {g['mem_free_mb']:.0f}/{g['mem_total_mb']:.0f}MB"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="GTX 1070 老卡温度/功耗/显存巡检")
    ap.add_argument("--once", action="store_true", help="单次巡检（默认）")
    ap.add_argument("--watch", action="store_true", help="持续监控模式")
    ap.add_argument("--interval", type=float, default=30.0, help="watch 模式间隔秒（默认 30）")
    args = ap.parse_args()

    if not args.watch:
        g = query_gpu()
        if g is None:
            print("[gpu_guard] FATAL: nvidia-smi 不可用或无 NVIDIA GPU", file=sys.stderr)
            sys.exit(2)
        print("[gpu_guard]", _fmt(g))
        alerts = check(g)
        for a in alerts:
            print(f"[gpu_guard] ALERT: {a}")
        sys.exit(0 if not alerts else 1)

    # watch 模式
    try:
        while True:
            g = query_gpu()
            if g is None:
                print("[gpu_guard] WARN: nvidia-smi 查询失败（驱动异常？）", file=sys.stderr)
            else:
                alerts = check(g)
                tag = "OK" if not alerts else "ALERT"
                print(f"[gpu_guard][{time.strftime('%H:%M:%S')}][{tag}] {_fmt(g)}"
                      + (f" | {'; '.join(alerts)}" if alerts else ""))
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        print("[gpu_guard] 巡检结束")


if __name__ == "__main__":
    main()
