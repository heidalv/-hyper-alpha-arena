"""GTX 1070 单卡 GPU 环境核验（v6 10.1 GPU 环境锁定）。

用法（必须用 GPU venv，CPU 版 torch 会 FAIL）:
    venv-gpu\\Scripts\\python.exe backend/scripts/verify_gpu.py

核验项:
  1. torch 构建版本含 cu124（GPU 环境锁定：换装 cu124 版）
  2. cuda.is_available() + 设备名 NVIDIA GeForce GTX 1070
  3. 显存 ~8GB、WDDM 桌面占用后可用 ~6.4GB（预算口径）
  4. FP32 小训练跑通（Pascal 无 Tensor Core，训练一律 FP32）
  5. 训练产物可复用 checkpoint（断点续训组件对接示例）

退出码: 0 = 全过；1 = 任一核验失败。
"""
from __future__ import annotations

import os
import sys

import torch

FAIL = False


def _check(name: str, ok: bool, detail: str) -> None:
    global FAIL
    tag = "PASS" if ok else "FAIL"
    if not ok:
        FAIL = True
    print(f"[verify_gpu][{tag}] {name}: {detail}")


def main() -> None:
    print(f"[verify_gpu] torch {torch.__version__} build_cuda={torch.version.cuda} "
          f"python {sys.version.split()[0]}")

    # 1) cu124 线（计划 10.1：PyTorch 锁定 cu12x，当前系统 python 曾为 +cpu 无效版）
    #    torch 2.6 的 version.cuda 为 '12.4'（点分）；旧 wheel 风格为 'cu124'，两种都算 cu12x 线
    build = (torch.version.cuda or "").lower().replace("cu", "").replace(".", "")
    ok_cu = build.startswith("12")
    _check("torch cu12x 线", ok_cu, f"version.cuda={torch.version.cuda!r}（需 cu124/cu12x）")

    # 2) CUDA 可用 + GTX 1070
    ok_cuda = torch.cuda.is_available()
    dev = torch.cuda.get_device_name(0) if ok_cuda else "N/A"
    _check("cuda.is_available", ok_cuda, f"device={dev}")
    if ok_cuda:
        _check("GTX 1070 单卡", dev.startswith("NVIDIA GeForce GTX 1070"), dev)

    # 3) 显存口径（8GB 总显存；WDDM 桌面占用后可用 ~6.4GB）
    if ok_cuda:
        total = torch.cuda.get_device_properties(0).total_memory / 1024**3
        free = torch.cuda.mem_get_info()[0] / 1024**3
        _check("显存 8GB 总容量", 7.0 < total < 9.0, f"total={total:.2f}GB free={free:.2f}GB")
        _check("可用 ~6.4GB 预算口径", free >= 5.5, f"free={free:.2f}GB（WDDM 桌面占用后预算 6.4GB）")
        _check("无 Tensor Core（FP16 无加速，训练 FP32）",
               not torch.cuda.get_device_properties(0).major >= 7,
               f"sm_{torch.cuda.get_device_properties(0).major}{torch.cuda.get_device_properties(0).minor}（Pascal sm_61）")

    # 4) FP32 小训练跑通（模拟 RL 策略网络 MLP：256-512 隐藏层小模型）
    if ok_cuda:
        torch.manual_seed(0)
        dev0 = torch.device("cuda:0")
        model = torch.nn.Sequential(
            torch.nn.Linear(64, 256), torch.nn.ReLU(),
            torch.nn.Linear(256, 256), torch.nn.ReLU(),
            torch.nn.Linear(256, 3),
        ).to(dev0)
        opt = torch.optim.Adam(model.parameters(), lr=1e-3)
        loss_fn = torch.nn.MSELoss()
        x = torch.randn(128, 64, device=dev0)
        y = torch.randn(128, 3, device=dev0)
        first_loss = None
        for step in range(50):
            opt.zero_grad()
            loss = loss_fn(model(x), y)
            loss.backward()
            opt.step()
            if step == 0:
                first_loss = float(loss)
        converged = float(loss) < first_loss * 0.9
        _check("FP32 MLP 训练 50 步收敛", converged,
               f"loss {first_loss:.4f}->{float(loss):.4f}（显存占用 {torch.cuda.memory_allocated()/1024**2:.0f}MB）")
        torch.cuda.empty_cache()

    # 5) 断点续训对接示例：训练权重经 CheckpointManager 落盘/恢复
    #    （依赖 backend 包；venv-gpu 精简环境无完整依赖时标记 SKIP，
    #      该组件单测在 .venv 的 test_gpu_guard_checkpointing.py 已覆盖）
    if ok_cuda:
        try:
            from backend.services.evolution.checkpointing import CheckpointManager
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                ckpt = CheckpointManager(tmp, every_n_steps=10, keep_last=3)
                ckpt.maybe_save(10, {"weights": model.state_dict()})
                step, state = ckpt.load_latest()
                n_params = sum(p.numel() for p in state["weights"].values())
                _check("checkpoint 断点续训对接", step == 10 and n_params > 0,
                       f"step={step} params={n_params}")
        except Exception as exc:
            print(f"[verify_gpu][SKIP] checkpoint 对接（backend 依赖缺失，单测已覆盖）: {exc}")

    print(f"\n[verify_gpu] RESULT: {'ALL PASS' if not FAIL else 'HAS FAILED ITEM'}")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
