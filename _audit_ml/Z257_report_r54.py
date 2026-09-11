# -*- coding: utf-8 -*-
"""[§88] 写入第 54 轮：执行 P29-C（long SL 上限 3% + 风险预算 0.75%）。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
text = P.read_text(encoding="utf-8")
if "## 88. 第 54 轮" in text:
    print("已存在 §88，跳过")
    raise SystemExit(0)

BLOCK = """## 88. 第 54 轮：执行 **P29-C**（long 层 SL 上限 + 风险预算对齐）（2026-09-11 17:2x）

用户拍板：**A+B 同时** —— long 的 SL 距离从 6.52% 拉到 2.5–3%，风险预算从 1.25% 降到 0.75%。

### 88.1 落地方式（为什么不是"只改一条乘数"）

找到后发现 SL 之所以有 6.5%：`mlto/midlong_trade_design.apply_structure_atr_floor()`
有一条**刻意的 ATR 下限**——"止损至少覆盖 `1dATR × MIDLONG_ATR_SL_MULT(1.5)`，
避免长线被日噪音波扫"。所以：
* 只把某一处的乘数改小，覆盖不全（long 仓来自 `tpl_long_swing_*` / `trend_e1:*` /
  `auto_*` 多条路径，SL 6.0–7.6% 不等）；
* 把全局 `MIDLONG_ATR_SL_MULT` 调小会**同时**影响 mid 层（mid SL 4.11%，不在本次决策范围）。

⇒ 采用**下单收口点统一夹子**（一处覆盖所有来源），并保留 ATR 下限不动：

| 改动 | 位置 | 说明 |
|---|---|---|
| `PaperTradingEngine.sl_max_pct_for_tier()` | `paper_trading_engine.py` | 读 `MIDLONG_SL_MAX_PCT_<TIER>` → `MIDLONG_SL_MAX_PCT` → **0（关闭）** |
| `PaperTradingEngine.clamp_sl_price()` | 同上 | 只夹"过远"一侧（long: `SL ≥ entry×(1−cap)`；short 对称），过近不动、失败原样返回 |
| 下单点接线 | `PaperOrder(...)` 创建处 | 夹住后回写 `order.sl_price` 并打 INFO（含旧值/新值/原因） |
| 配置 | `.env` + `env_registry` | `MIDLONG_SL_MAX_PCT_LONG=0.03`；`PC_RISK_PER_TRADE_PCT_LONG` **0.0125 → 0.0075** |

**生效值实测**：`PC_RISK_PER_TRADE_PCT_LONG=0.0075`｜`MIDLONG_SL_MAX_PCT_LONG=0.03`｜
`sl_max_pct_for_tier("long")=0.03`（探针脚本 + 契约测试双重核验）。
备份：`.env.bak_p29_20260911_172123`；回滚 = 两个键改回（`MIDLONG_SL_MAX_PCT_LONG=0` 即关）。

### 88.2 行为影响（预期）

* 新的 long 仓：SL 距离 **≤3%**（原来 6.0–7.6%）⇒ 触损时亏损约为原来的 40–45%；
* 风险预算 1.25%→0.75% ⇒ 名义仓位按比例下降（约 ×0.6），单笔亏损再降一档；
* 合并粗算：long 单笔亏损 −$19.22 → **−$5~−$7 量级**（≈ mid 的 1.5–2×，而非 6.3×）；
* 代价（必须记账）：SL 拉近 ⇒ **被噪音扫损的概率上升**，long 的胜率会下降、
  但单笔亏损收窄；净效果要用观察期数据判定（现有 3 笔 long 仓开于本次改动之前，
  不受影响；新仓才适用）。

### 88.3 契约与护栏

* `test_long_sl_cap_p29_20260911.py`（**7 例**）：过远拉近 / 过近不动 / 空头对称 /
  上限 0=关闭 / 层键优先于全局键 / **部署生效值 == 决策值（0.03 与 0.0075）** /
  **接线护栏**（`PaperOrder` 创建点必须调用 `clamp_sl_price` 且回写 `order.sl_price`）；
* 新键已登记 `env_registry`（死键登记册交叉验证 ✅ 18/18 一致）；
* 后端已重启加载（pid 1880，`/api/health` 200）。

### 88.4 观察项（下一轮起）

1. 新开的 long 仓 SL 距离是否 ≤3%（`paper_positions` 直接可验）；
2. 是否出现被拉近的 INFO 日志（`[Paper] SL 距离超上限被拉近`）——出现即证明夹子在生产路径生效；
3. long 层 `sl` 通道离场占比（原 0/34）与单笔亏损中位数变化；
4. 与 §87 的 MAE 遥测一起看："SL 拉近后是否真的在 MAE 量级触发"。

"""

idx = text.index("## 23. 验收标准")
shutil.copy2(P, P.with_name(P.name + f".pre_88_{time.strftime('%Y%m%d_%H%M%S')}"))
text = text[:idx] + BLOCK + text[idx:]

lines = text.splitlines()
row = ("| 72 | 🟠 中 | **long 层 SL 距离无上限**：ATR 下限（`apply_structure_atr_floor`：SL ≥ 1dATR×1.5）"
       "把 long 的 SL 撑到 **6.0–7.6%**，而实际 MAE 只有 −1.41% ⇒ 止损结构性不触发；"
       "叠加风险预算 1.25%（mid 的 1.67×）⇒ 单笔亏损 −$19.22（mid 的 6.3×） | "
       "§87–§88；`_audit_ml/Z254_long_sl_chain_audit.py`、`test_long_sl_cap_p29_20260911.py` | "
       "✅ **已修 P29-C**：下单收口点新增按层 SL 上限（`MIDLONG_SL_MAX_PCT_LONG=0.03`）+ "
       "`PC_RISK_PER_TRADE_PCT_LONG` 1.25%→0.75%；7 例契约（含部署生效值断言与接线护栏） |")
anchor = next((i for i, l in enumerate(lines) if l.startswith("| 71 |")), None)
assert anchor is not None, "未找到 #71 行"
lines.insert(anchor + 1, row)
q = next((i for i, l in enumerate(lines) if l.startswith("| **P29** |")), None)
if q is not None:
    lines[q] = ("| **P29** | ~~long 层大亏的动作选择~~ ✅ **已执行（选 C：A+B 同时）2026-09-11**（§88）："
                "下单收口点加按层 SL 上限 `MIDLONG_SL_MAX_PCT_LONG=0.03`（0=关闭）+ "
                "`PC_RISK_PER_TRADE_PCT_LONG` 0.0125→0.0075 | 生效值已核验；7 例契约含"
                "部署值断言与接线护栏；观察项见 §88.4 | #71/#72 |")
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("已写入 §88 + 清单 #72 + 更新 P29 行")
