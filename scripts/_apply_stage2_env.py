"""轮101 阶段2：把三条**值分离**写进 .env（二进制安全，保留 CRLF）。"""
import io
import sys

PATH = '.env'

# (定位用的锚点行, 要插入/替换的内容块)
BLOCK_MIDLONG_IV = (
    'MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC=14400\r\n',
    'MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC=14400\r\n'
    '# [轮101 阶段2·值分离] 复查节奏按车道独立（轮100 已拆键，此处给值）。\r\n'
    '#   中线 14400(4h)：中线设计持仓 12-48h，4h 复查 = 生命的 1/3~1/12，合理。\r\n'
    '#   长线 14400(4h)：**值保留**，但语义已变 —— 轮99 起趋势车道的 tighten/reduce 被禁止，\r\n'
    '#     该节奏现在只决定「滚仓决策」频率；thesis 规则失效退出不受它节流。\r\n'
    '#     实测长线中位持仓 13.54h、平均 26.87h：若改成 24h，平均只剩 1.1 次滚仓决策（会饿死滚仓）。\r\n'
    '#     （原先建议的 24h 是"裁量复查"口径；等阶段3 拆出独立的 thesis 复查节奏后再按日线走。）\r\n'
    'MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_MID=14400\r\n'
    'MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_LONG=14400\r\n'
)

BLOCK_ATR_MULT = (
    'MIDLONG_ATR_SL_MULT=1.5\r\n',
    '# [轮101 阶段2·值分离] 入场止损的 ATR 倍数按车道独立。\r\n'
    '#   中线 1.5：12-48h 的初始止损，币圈常用 >=1.5x。\r\n'
    '#   长线 3.0：与 Chandelier 同口径（高峰 - 3xATR20(日线)），让趋势仓有呼吸空间。\r\n'
    '#   仅作用于**非 E1** 的长线建仓链（E1 的止损来自 Chandelier 结构位）。\r\n'
    '#   抬升仍受 MIDLONG_ATR_FLOOR_MAX_LIFT(2.0x) 封顶，不会无限放宽。\r\n'
    'MIDLONG_ATR_SL_MULT=1.5\r\n'
    'MIDLONG_ATR_SL_MULT_MID=1.5\r\n'
    'MIDLONG_ATR_SL_MULT_LONG=3.0\r\n'
)

BLOCK_MID_MAXHOLD = (
    'TIER_MID_MAX_HOLD_SEC=604800\r\n',
    '# [轮101 阶段2·值分离] 中线恢复自己的时间尺度。\r\n'
    '#   原 604800(7天) 与长线 TIER_LONG_MAX_HOLD_SEC 完全相同 —— 合并设计的症状之一，\r\n'
    '#   等于把中线拉成了长线。settings 默认值本就是 172800(48h)。\r\n'
    '#   实测影响面：近 60 天 213 笔中线仓只有 2 笔(0.9%)超过 48h，P90=18.8h，\r\n'
    '#   改后不会强平任何在册仓位（当时在册的 3 笔均 <2.1h）。\r\n'
    'TIER_MID_MAX_HOLD_SEC=172800\r\n'
)


def main(apply: bool) -> int:
    with io.open(PATH, 'r', encoding='utf-8', errors='surrogateescape', newline='') as f:
        data = f.read()

    for anchor, replacement in (BLOCK_MIDLONG_IV, BLOCK_ATR_MULT, BLOCK_MID_MAXHOLD):
        if anchor not in data:
            print(f'  [!!] 锚点未找到（可能已改过）: {anchor.strip()}')
            return 2
        if data.count(anchor) != 1:
            print(f'  [!!] 锚点不唯一({data.count(anchor)}): {anchor.strip()}')
            return 2
        data = data.replace(anchor, replacement, 1)
        print(f'  [OK] 已替换: {anchor.strip()}')

    if not apply:
        print('  [DRY-RUN] 加 --apply 才写入')
        return 0
    with io.open(PATH, 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
        f.write(data)
    print('  [OK] 已写入 .env')
    return 0


if __name__ == '__main__':
    sys.exit(main('--apply' in sys.argv))
