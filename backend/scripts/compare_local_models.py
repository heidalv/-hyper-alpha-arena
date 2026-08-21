"""本地模型对比实测（2026-08-21）：qwen2.5-14b(基线) vs qwen3-14b vs Qwen3.8-27B-Q3KXL(社区)。

同一套题：① 基础问答 ② Codegen 因子 AST 生成×3（合法率）③ 批评家病态/健康判定
④ 加载后显存 ⑤ 粗略速度。直连 Ollama（不经 DB 配置），复用 CodegenCritic 的
prompt 与解析/审计逻辑（monkeypatch _load_configs）。
"""
import json
import os
import subprocess
import sys
import time

os.environ.setdefault("LEARNING_LOOP_ENABLED", "false")
_ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)
import urllib.request

# 系统有 Privoxy 代理会劫持 localhost —— 本地 Ollama 直连，禁用代理
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_OPENER)

BASE = "http://127.0.0.1:11434"
MODELS = [
    "qwen2.5:14b-instruct-q5_K_M",
    "qwen3:14b",
    "smtek/Qwen3.8-27B:Q3_K_XL-16gb",
]


def chat(model, messages, max_tokens=800, temperature=0.6):
    t0 = time.time()
    req = urllib.request.Request(
        f"{BASE}/api/chat",
        data=json.dumps({
            "model": model, "messages": messages, "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
            "format": "json",
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.loads(r.read())
    txt = (data.get("message") or {}).get("content") or ""
    n_tok = int(data.get("eval_count") or 0)
    dt = time.time() - t0
    return txt, n_tok, dt


def vram_used():
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]
    ).decode().strip()
    return int(out.splitlines()[0])


def stop_model(model):
    req = urllib.request.Request(
        f"{BASE}/api/generate",
        data=json.dumps({"model": model, "keep_alive": 0}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass
    time.sleep(3)


def main():
    from backend.services.evolution.alpha_miner import CodegenCritic
    from dataclasses import replace
    from backend.services.llm_config_service import LLMConfig

    critic = CodegenCritic()
    base_cfg, _ = critic._load_configs()
    bad = {"factor_id": "t1", "ic_mean": 0.008, "icir": 0.05, "oos_sharpe": -0.3,
           "oos_trades": 6, "fwd_bars": 6, "kline_exchange": "binance"}
    good = {"factor_id": "t2", "ic_mean": 0.062, "icir": 0.55, "oos_sharpe": 0.71,
            "oos_trades": 210, "fwd_bars": 6, "kline_exchange": "binance"}

    results = {}
    for m in MODELS:
        print(f"\n===== {m} =====", flush=True)
        r = {"model": m}
        # ① 基础问答
        txt, tok, dt = chat(m, [
            {"role": "user", "content": "Answer in one short sentence: what is a stop-loss?"}
        ], max_tokens=60)
        r["qa_ok"] = "limit" in txt.lower() and len(txt) < 300
        print(f"  QA: ok={r['qa_ok']} {dt:.1f}s {tok}tok")
        # ② Codegen ×3（monkeypatch 配置指向当前模型）
        cfg = replace(base_cfg, model=m, name=m)
        CodegenCritic._load_configs = lambda self, _c=cfg: (_c, None)
        ok_cnt = 0
        for i in range(3):
            try:
                res = critic.generate_and_audit("生成一个动量类因子：过去 10 根 K 线的收盘价变化率。")
                ok_cnt += 1 if res.audit_passed else 0
                print(f"  Codegen#{i}: passed={res.audit_passed} {str(res.reason)[:60]}")
            except Exception as e:
                print(f"  Codegen#{i}: EXC {e}")
        r["codegen_ok"] = ok_cnt
        # ③ 批评家 ×2（病态/健康）
        import os as _os
        _os.environ["FACTOR_CRITIC_ENABLED"] = "1"
        c_ok = {}
        for tag, sc in (("bad", bad), ("good", good)):
            try:
                ok, reason = critic.critique(sc)
                c_ok[tag] = (ok, str(reason)[:60])
                print(f"  Critic[{tag}]: ok={ok} {reason[:60]}")
            except Exception as e:
                c_ok[tag] = (None, str(e)[:60])
        _os.environ["FACTOR_CRITIC_ENABLED"] = "0"
        r["critic_bad_reject"] = c_ok.get("bad", (None, ""))[0] is False
        r["critic_good_pass"] = c_ok.get("good", (None, ""))[0] is True
        # ④ 显存
        r["vram_mb"] = vram_used()
        # ⑤ 速度（用 QA 的 tok/s）
        r["tok_s"] = round(tok / dt, 1) if dt > 0 else 0
        print(f"  VRAM={r['vram_mb']}MB 速度≈{r['tok_s']}tok/s")
        results[m] = r
        stop_model(m)

    print("\n===== 汇总 =====")
    print(f"{'模型':38s} QA  Codegen  Critic(拒/过)  VRAM   tok/s")
    for m, r in results.items():
        print(f"{m:38s} {str(r['qa_ok'])[0]}   {r['codegen_ok']}/3     "
              f"{str(r['critic_bad_reject'])[0]}/{str(r['critic_good_pass'])[0]}          "
              f"{r['vram_mb']}MB  {r['tok_s']}")


if __name__ == "__main__":
    main()
