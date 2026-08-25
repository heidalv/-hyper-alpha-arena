#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性补丁: 注册每小时 regime 状态刷新任务(2026-08-25)。"""
import shutil

PATH = "backend/services/evolution_scheduler.py"
BAK = PATH + ".bak_20260825"
shutil.copy(PATH, BAK)
print("backup ->", BAK)

src = open(PATH, encoding="utf-8").read()
old = '''        task_scheduler.add_interval_task(
            task_func=_settle_scalp_signals,
            interval_seconds=300,
            task_id="scalp_signal_settle",
        )
        logger.info("[EvoScheduler] 已注册短线信号结算任务(5min)")'''
new = '''        task_scheduler.add_interval_task(
            task_func=_settle_scalp_signals,
            interval_seconds=300,
            task_id="scalp_signal_settle",
        )
        logger.info("[EvoScheduler] 已注册短线信号结算任务(5min)")

        # v3(2026-08-25): 币种级滚动 regime 状态刷新(推理特征,1h 一次;
        # 基于 settle_ts 无前视;元模型 predict_win_prob 读该状态文件)
        def _refresh_scalp_regime():
            try:
                from backend.services.scalp_meta_trainer import refresh_regime_state
                refresh_regime_state()
            except Exception as _e:
                logger.debug(f"[EvoScheduler] scalp regime 刷新跳过: {_e}")

        task_scheduler.add_interval_task(
            task_func=_refresh_scalp_regime,
            interval_seconds=3600,
            task_id="scalp_regime_refresh_hourly",
        )
        logger.info("[EvoScheduler] 已注册短线regime状态刷新任务(1h)")'''
assert src.count(old) == 1, f"count={src.count(old)}"
open(PATH, "w", encoding="utf-8").write(src.replace(old, new))
print("patched OK")
