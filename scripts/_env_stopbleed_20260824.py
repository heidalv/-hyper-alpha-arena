# -*- coding: utf-8 -*-
"""2026-08-24 止血参数落盘（复盘建议 1-4 的配置侧）

1. FUSION_SCALP_PWIN_MIN 0.30 -> 0.45：放行攒样本折中档。
   证据：24h pwin 桶 n=12 wr=33.3%，0.30 地板当日短线 -16.4 / 费 -12.6。
   回滚：0.55（旧结构回放地板）/ 0.30（继续全放行攒样本）。
2. SCALP_SIZE_PCT 0.90 -> 0.60：保证金放大 8.7x 后单笔 SL 亏损 -0.2 -> -1.7~-3.8，
   失血加速。0.6 约折中（用户若仍要更大保证金可回 0.90）。
3. PB_MIDLONG_MAX_SYMBOL_EXPOSURE_PCT 2.0 -> 0.6：PB 恢复启用时的单币名义上限
   （当前 PB_PAPER_SKIP=true，实际生效的是新增确定性检查 MIDLONG_SYMBOL_EXPOSURE_CAP_PCT）。
4. MIDLONG_SYMBOL_EXPOSURE_CAP_PCT=0.6（新增）：中长线单币名义/权益封顶，
   paper_execution 下单前确定性检查，只拒单不冻结。0=关闭。
5. OLLAMA_MAX_CONCURRENT=2（新增）：本地 ollama 全局并发 2 槽；
   KlineAnalyst/MasterController 等重负载调用方在代码层每调用方限 1 槽，
   保证 scalp_confirm / ai_factor_discovery 拿得到槽。
"""
import io

ENV = r"D:\001Alpha\Hyper-Alpha-Arena\.env"
CHANGES = {
    "FUSION_SCALP_PWIN_MIN": "0.45",
    "SCALP_SIZE_PCT": "0.60",
    "PB_MIDLONG_MAX_SYMBOL_EXPOSURE_PCT": "0.6",
}
ADD = {
    "MIDLONG_SYMBOL_EXPOSURE_CAP_PCT": "0.6",
    "OLLAMA_MAX_CONCURRENT": "2",
}

with io.open(ENV, "r", encoding="utf-8", errors="replace") as fh:
    lines = fh.read().splitlines()

seen = set()
out = []
for ln in lines:
    s = ln.strip()
    if s.startswith("#") or "=" not in s:
        out.append(ln)
        continue
    k = s.split("=", 1)[0].strip()
    if k in CHANGES:
        if k in seen:
            print("[dup skip]", k, ln[:60])
            continue
        seen.add(k)
        out.append(f"{k}={CHANGES[k]}")
        print(f"[set] {k}={CHANGES[k]}  (原: {s[:60]})")
    else:
        out.append(ln)

for k, v in ADD.items():
    if k not in seen and k not in [x.split("=", 1)[0].strip() for x in out if "=" in x]:
        out.append(f"{k}={v}")
        print(f"[add] {k}={v}")

with io.open(ENV, "w", encoding="utf-8", newline=chr(10)) as fh:
    fh.write(chr(10).join(out) + chr(10))
print("DONE env written")
