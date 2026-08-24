# -*- coding: utf-8 -*-
"""14B vs 35B 严格对比：交替 3 轮测纯生成速率 + 加载成本 + 质量抽样。"""
import json
import time
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
A = "qwen3:14b"
B = "batiai/qwen3.6-35b:q3"

PROMPT = "分析BTC当前15m/1h/4h多周期趋势并给出方向判断，150字以内，用中文。"


def call(model, prompt, num_predict=128, think=False):
    payload = {
        "model": model, "prompt": prompt, "stream": False,
        "options": {"num_predict": num_predict, "temperature": 0.2},
        "think": think,
    }
    req = urllib.request.Request(
        OLLAMA + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def bench(model, rounds=3):
    rates = []
    loads = []
    first_text = ""
    for i in range(rounds):
        r = call(model, PROMPT, num_predict=128, think=False)
        ev = r.get("eval_count", 0)
        dur = r.get("eval_duration", 0) / 1e9
        rates.append(ev / dur if dur > 0 else 0)
        loads.append(r.get("load_duration", 0) / 1e9)
        if i == 0:
            first_text = r.get("response", "")
    return rates, loads, first_text


def main():
    # 先看显存占用
    with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=10) as r:
        ps = json.loads(r.read())
        for m in ps.get("models", []):
            print(f"已加载: {m['name'][:36]} {m.get('size',0)/1e9:.1f}GB")

    print("== 交替测速（3 轮，每轮 128 token）==")
    ra, la, ta = bench(A)
    rb, lb, tb = bench(B)
    # 再交替一轮（模拟两模型轮流被加载/换出）
    ra2, la2, _ = bench(A, rounds=1)
    rb2, lb2, _ = bench(B, rounds=1)

    print(f"[14B] 纯生成: {ra} → 均值 {sum(ra)/len(ra):.1f} tok/s | 加载: {la}")
    print(f"[35B] 纯生成: {rb} → 均值 {sum(rb)/len(rb):.1f} tok/s | 加载: {lb}")
    print(f"[再测] 14B={ra2[0]:.1f} tok/s (load {la2[0]:.1f}s) | 35B={rb2[0]:.1f} tok/s (load {lb2[0]:.1f}s)")

    print("\n== 质量抽样（相同 prompt，各 128 token）==")
    print("--- 14B ---")
    print(ta[:300])
    print("--- 35B ---")
    print(tb[:300])


if __name__ == "__main__":
    main()
