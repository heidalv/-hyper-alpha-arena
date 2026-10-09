# -*- coding: utf-8 -*-
r"""[h894] 高频趋势概率模型训练驱动。

用法:.venv\Scripts\python.exe scripts\h894_train_trend_prob.py [小时数=24] [标签阈值bp=3]
产出:data/hft_trend_prob/model.json —— worker 每拍读取(trend_prob.load_model)
     控制台打印样本外校准报告(Brier/AUC/可靠性/基础率/门槛)。
"""
import io
import sys
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from backend.services.evolution.hft_trend_prob_train import (  # noqa: E402
    load_training_rows, save_model, train_model,
)

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
THR = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
# 当前高频宇宙里最活跃的主流币(与 h892 对齐,可随宇宙调整)
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AAVEUSDT", "BCHUSDT"]

print(f"== 装载训练数据:{HOURS}h × {len(SYMS)} 币 ==", flush=True)
rows = load_training_rows(SYMS, hours=HOURS, step_s=2.0, horizon_s=30.0)
print(f"  样本:{ {k: len(v['fwd']) for k, v in rows.items()} }", flush=True)
if not rows:
    print("⚠ 无数据,退出", flush=True)
    sys.exit(1)

print(f"== 训练(标签阈值 ±{THR}bp / 30s)==", flush=True)
model = train_model(rows, label_thr_bp=THR, margin=0.08, horizon_s=30.0)
p = save_model(model)

m = model["metrics"]
print(f"✓ 模型已写 {p}", flush=True)
print(f"  样本:train {m['n_train']} / test(OOS) {m['n_test']}", flush=True)
print(f"  基础率:up {m['base_up']:.1%} / dn {m['base_dn']:.1%}  "
      f"⇒ 门槛 p_min up {model['gate']['p_min_up']:.2f} / dn {model['gate']['p_min_dn']:.2f}",
      flush=True)
for side in ("up", "dn"):
    s = m[side]
    print(f"  [{side}] OOS Brier={s['brier']} AUC={s['auc']}", flush=True)
    for r in s["reliability"]:
        print(f"      预测 {r['pred']:.3f} → 实际 {r['actual']:.3f} (n={r['n']})",
              flush=True)
