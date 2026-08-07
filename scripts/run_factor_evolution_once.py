"""
手动触发一轮真实的因子进化闭环执行（P1: 实际运行一轮回测，产出真实报告验证）。

这不是新写的测试脚本，而是直接调用生产环境每日3点定时任务
(backend/main.py 里 task_scheduler.add_cron_task 挂的同一个函数)——
用于在不等到凌晨3点的情况下，立即产出一轮真实的挖掘/验证/晋升/DSR-PBO/
影子期判定结果，并顺带验证新增的事件驱动回测触发器(on_factor_promoted)
是否真的被调用。

用法：
    python scripts/run_factor_evolution_once.py
"""
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    from backend.services.evolution.factor_evolution_loop import (
        run_factor_evolution_loop, run_online_weight_update,
    )
    from backend.services.backtest.trigger import get_recent_triggers

    print("=" * 60)
    print("第一步：运行因子进化闭环主流程(挖掘→样本外验证→DSR/PBO→晋升→影子期推进)")
    print("=" * 60)
    t0 = time.time()
    result = run_factor_evolution_loop()
    elapsed = time.time() - t0
    print(f"\n耗时 {elapsed:.1f}s，结果：")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    print("\n" + "=" * 60)
    print("第二步：运行在线权重更新(验证权重变化事件触发)")
    print("=" * 60)
    weight_result = run_online_weight_update()
    print(json.dumps(weight_result, ensure_ascii=False, indent=2, default=str))

    print("\n" + "=" * 60)
    print("第三步：查询事件驱动回测触发记录(验证真的被触发，而非空转)")
    print("=" * 60)
    time.sleep(3)  # 给后台job一点时间跑完
    triggers = get_recent_triggers(20)
    print(f"最近触发记录数: {len(triggers)}")
    print(json.dumps(triggers, ensure_ascii=False, indent=2, default=str))

    out = {
        "evolution_result": result,
        "weight_update_result": weight_result,
        "recent_backtest_triggers": triggers,
    }
    out_path = os.path.join("data", f"factor_evolution_manual_run_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n完整结果已写入: {out_path}")


if __name__ == "__main__":
    main()
