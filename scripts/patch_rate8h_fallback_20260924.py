import pathlib
TARGETS = {
    r"backend\services\agents\anomaly_agent.py": 305,
    r"backend\services\analysis\context_pack.py": 594,
    r"backend\services\strategies\event\e5_2_funding_shock.py": 63,
}
for f, ln in TARGETS.items():
    p = pathlib.Path(f)
    lines = p.read_text(encoding="utf-8").splitlines()
    i = ln - 1
    assert lines[i].strip() == "return float(rate)", (f, lines[i])
    ind = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    # 校验形参名确实是 (ex, rate)
    head = "\n".join(lines[max(0, i-9):i])
    assert "def rate_8h" in head and "ex" in head, (f, head[-200:])
    lines[i:i+1] = [
        f"{ind}# [R29] 兜底也必须归一到 8h：否则 hyperliquid 的 1h 费率被当 8h（高估 8×）。",
        f'{ind}_hrs = {{"hyperliquid": 1.0}}.get(str(ex or "").lower(), 8.0)',
        f"{ind}return float(rate) * (8.0 / _hrs) if _hrs > 0 else float(rate)",
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"patched {f}:{ln}")
    print("   " + "\n   ".join(lines[ln-2:ln+3]))
