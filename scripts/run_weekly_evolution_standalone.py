# -*- coding: utf-8 -*-
"""独立进程运行 weekly_evolution（M0-E1i）。

背景：DSH web 对 8000 端口后端有健康监管（响应慢即重启）。NSGA-II 进化
（预计算 ~1.5h + 8 模板 GA）与交易循环同进程时，/api/health 延迟升高触发
监管重启，整轮进化反复随进程死亡。本脚本在独立进程运行进化：
- 不占任何端口 → 不受 DSH 监管；
- 冠军落库/晋升/evolution_events/gates 下发（runtime_tuning.json 文件通道）
  全部持久化 → 交易后端 60s 内生效；
- 唯一损失：进程内 Orchestrator 参数热推送（本就随重启丢失，非持久通道）。

[2026-09-11 可观测性] 新增心跳文件 data/standalone_weekly_heartbeat.json：
- 启动/每阶段/完成/异常都写（时间戳+phase+进度）；
- 交易后端 /api/compute/evolution/status 读该文件返回 standalone 段，
  使「主脑进化未知」变为可观测（计划任务下次 09-13 01:00 生效）。

用法: backend\.venv\Scripts\python.exe scripts\run_weekly_evolution_standalone.py
"""
import json
import sys
import time
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HEARTBEAT = ROOT / "data" / "standalone_weekly_heartbeat.json"


def _beat(phase: str, extra: dict | None = None) -> None:
    try:
        HEARTBEAT.parent.mkdir(exist_ok=True)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "epoch": time.time(),
            "phase": phase,
            "pid": __import__("os").getpid(),
            **(extra or {}),
        }
        HEARTBEAT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("standalone-weekly")

if __name__ == "__main__":
    logger.info("[StandaloneWeekly] 启动独立进程 weekly_evolution")
    _beat("start")
    from backend.services.evolution_scheduler import EvolutionScheduler
    sched = EvolutionScheduler()
    try:
        sched.weekly_evolution()
        logger.info("[StandaloneWeekly] weekly_evolution 完成")
        _beat("done")
    except Exception as e:
        logger.error("[StandaloneWeekly] weekly_evolution 异常: %s", e, exc_info=True)
        _beat("error", {"error": str(e)[:400], "trace": traceback.format_exc()[-2000:]})
        sys.exit(1)
