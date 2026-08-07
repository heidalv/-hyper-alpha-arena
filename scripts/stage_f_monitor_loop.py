"""Stage F 后台常驻监控 — 每 N 分钟跑一次 stage_f_monitor，带 dry-run / live 两档开关.

设计理由 (给看代码的我自己和未来的我):
    `stage_f_monitor.py` 本身是"一次性"脚本 (跑完 exit)，适合 cron。
    Windows 上没装 cron，用这个脚本起一个 Python 后台进程反复调用即可。

    两档模式:
        dry-run (默认): 只产报告到 docs/research/stage_f_report.md，不写 rollback flag。
                        刚打开批次 4 时用这个：先让 KPI 随 P2 新行为稳下来，
                        避免开 flag 的瞬间把 24h 窗口里尚未被 P2 作用的历史止损
                        误判成"P2 害的"从而自动回滚，白忙一场。
        live:            真正的熔断模式：任意 KPI trip 就写 data/stage_f_rollback.flag，
                        下次 reload / restart 后端时 LEGACY_RISK_HARD_ROLLBACK 自动变 true。

    推荐切换节点:
        批次 4 打开后 2-4 小时，人工确认 sl_trigger_rate_7d 开始下降 + heartbeat 恢复
        → 停掉 dry-run 模式的 loop，起 live 模式的 loop。

用法:
    # dry-run (默认)
    python scripts/stage_f_monitor_loop.py

    # live (真正写熔断 flag)
    python scripts/stage_f_monitor_loop.py --live

    # 自定义间隔 / 窗口
    python scripts/stage_f_monitor_loop.py --interval-min 15 --since-hours 24 --equity 10000
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--interval-min", type=int, default=15, help="多少分钟跑一次")
    p.add_argument("--since-hours", type=int, default=24, help="stage_f_monitor 的回看窗口")
    p.add_argument("--equity", type=float, default=10000.0, help="账户本金估算")
    p.add_argument("--live", action="store_true", help="去掉 dry-run，真正触发熔断写 flag")
    p.add_argument("--db", default="data/alpha_arena.db")
    p.add_argument("--report-dir", default="docs/research")
    p.add_argument("--log", default="logs/stage_f_monitor_loop.log")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # logger
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger("stage_f_loop")

    mode = "LIVE (写 rollback flag)" if args.live else "DRY-RUN (仅报告)"
    logger.info("=" * 60)
    logger.info(f"Stage F 常驻监控启动  模式={mode}  间隔={args.interval_min}min  窗口={args.since_hours}h")
    logger.info("=" * 60)

    report_file = Path(args.report_dir) / "stage_f_report.md"

    while True:
        tick_start = datetime.now()
        cmd = [
            sys.executable,
            "scripts/stage_f_monitor.py",
            "--db", args.db,
            "--since-hours", str(args.since_hours),
            "--equity", str(args.equity),
            "--out", str(report_file),
        ]
        if not args.live:
            cmd.append("--dry-run")

        logger.info(f"[tick] 跑: {' '.join(cmd)}")
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            # 把 stdout/stderr 精华 1-2 行喂到日志
            out = (res.stdout or "").strip().splitlines()
            err = (res.stderr or "").strip().splitlines()
            for line in out[-4:]:
                logger.info(f"    [out] {line}")
            for line in err[-4:]:
                logger.warning(f"    [err] {line}")
            if res.returncode == 0:
                logger.info("[tick] OK — 无熔断")
            elif res.returncode == 2:
                logger.warning("[tick] 熔断触发！详见 report 或 rollback flag")
            else:
                logger.error(f"[tick] 异常 exit={res.returncode}")
        except Exception as e:
            logger.exception(f"[tick] 执行异常: {e}")

        elapsed = (datetime.now() - tick_start).total_seconds()
        sleep_sec = max(5, args.interval_min * 60 - elapsed)
        logger.info(f"[tick] 本轮耗时 {elapsed:.1f}s, 休眠 {sleep_sec:.0f}s 后继续...")
        try:
            time.sleep(sleep_sec)
        except KeyboardInterrupt:
            logger.info("收到 Ctrl-C, 退出循环")
            return 0


if __name__ == "__main__":
    sys.exit(main())
