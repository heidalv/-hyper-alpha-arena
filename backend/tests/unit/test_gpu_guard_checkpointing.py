"""老卡温度/功耗预案单测（v6 10.1：nvidia-smi 巡检 + 断点续训 checkpoint）。"""
import os

import numpy as np
import pytest

from backend.scripts.gpu_guard import VRAM_MIN_MB, TEMP_WARN_C, check, query_gpu
from backend.services.evolution.checkpointing import CheckpointManager, dump_numpy, load_numpy


# ─────────────────────────── gpu_guard 阈值判定 ───────────────────────────

def _g(**over):
    base = {
        "name": "NVIDIA GeForce GTX 1070", "driver": "572.83",
        "mem_total_mb": 8192.0, "mem_used_mb": 1621.0, "mem_free_mb": 6447.0,
        "temp_c": 50.0, "power_w": 14.0, "power_limit_w": 151.0,
    }
    base.update(over)
    return base


def test_gpu_guard_healthy_no_alerts():
    assert check(_g()) == []


def test_gpu_guard_temp_alert():
    alerts = check(_g(temp_c=85.0))
    assert any("温度" in a for a in alerts)


def test_gpu_guard_power_alert():
    # 141W >= 151*0.9=135.9W
    alerts = check(_g(power_w=141.0))
    assert any("功耗" in a for a in alerts)


def test_gpu_guard_vram_alert():
    alerts = check(_g(mem_free_mb=300.0))
    assert any("显存" in a for a in alerts)


def test_gpu_guard_multi_alerts():
    alerts = check(_g(temp_c=90.0, power_w=150.0, mem_free_mb=100.0))
    assert len(alerts) == 3


def test_gpu_guard_boundary():
    # 阈值边界：温度恰等于预警值应告警（>=）；显存严格小于下限才告警（<）
    assert check(_g(temp_c=TEMP_WARN_C)) != []
    assert check(_g(mem_free_mb=VRAM_MIN_MB)) == []  # 恰好够用不告警
    assert check(_g(mem_free_mb=VRAM_MIN_MB - 1)) != []


def test_gpu_guard_query_real_machine():
    """实机核验：本机有 GTX 1070 且 nvidia-smi 可查询（环境性，缺卡则跳过）。"""
    g = query_gpu()
    if g is None:
        pytest.skip("nvidia-smi 不可用（无 NVIDIA GPU 或驱动未就绪）")
    assert g["name"].startswith("NVIDIA GeForce GTX 1070")
    assert g["mem_total_mb"] > 7000  # 8GB 卡
    assert 0 <= g["temp_c"] < 100


# ─────────────────────────── checkpoint 断点续训 ───────────────────────────

def test_ckpt_maybe_save_rhythm(tmp_path):
    ck = CheckpointManager(str(tmp_path), every_n_steps=10, keep_last=3)
    for step in range(1, 26):
        ck.maybe_save(step, {"w": np.arange(3)})
    step, _ = ck.load_latest()
    assert step == 20  # 只落盘 10/20


def test_ckpt_roundtrip(tmp_path):
    ck = CheckpointManager(str(tmp_path), every_n_steps=1, keep_last=3)
    ck.save(5, {"weights": np.array([1.0, 2.0, 3.0]), "rng": np.random.default_rng(1)})
    step, state = ck.load_latest()
    assert step == 5
    np.testing.assert_array_equal(state["weights"], [1.0, 2.0, 3.0])


def test_ckpt_keep_last_prune(tmp_path):
    ck = CheckpointManager(str(tmp_path), every_n_steps=1, keep_last=3)
    for step in range(1, 8):
        ck.save(step, {"step_no": step})
    paths = ck._paths()
    assert len(paths) == 3  # 只保留 5/6/7
    assert os.path.basename(paths[0]) == "ckpt_step00000007.pkl"
    assert not os.path.exists(os.path.join(str(tmp_path), "ckpt_step00000001.pkl"))


def test_ckpt_latest_step_none(tmp_path):
    ck = CheckpointManager(str(tmp_path), every_n_steps=10, keep_last=3)
    assert ck.latest_step() == 0
    assert ck.load_latest() == (0, None)


def test_ckpt_corrupt_falls_back(tmp_path):
    ck = CheckpointManager(str(tmp_path), every_n_steps=1, keep_last=3)
    ck.save(3, {"v": 3})
    ck.save(5, {"v": 5})
    # 损坏最新断点
    with open(os.path.join(str(tmp_path), "ckpt_step00000005.pkl"), "wb") as f:
        f.write(b"corrupted")
    step, state = ck.load_latest()
    assert step == 3 and state["v"] == 3  # 回退到更早断点


def test_ckpt_atomic_no_tmp_leftover(tmp_path):
    ck = CheckpointManager(str(tmp_path), every_n_steps=1, keep_last=3)
    ck.save(1, {"v": 1})
    leftovers = [n for n in os.listdir(str(tmp_path)) if n.endswith(".tmp")]
    assert leftovers == []


def test_ckpt_meta_written(tmp_path):
    ck = CheckpointManager(str(tmp_path), every_n_steps=1, keep_last=3)
    ck.save(7, {"weights": np.zeros(2)})
    import json
    with open(os.path.join(str(tmp_path), "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["step"] == 7
    assert "weights" in meta["state_keys"]


def test_numpy_pack_roundtrip():
    state = {"a": np.arange(6).reshape(2, 3), "b": np.float64(1.5)}
    data = dump_numpy(state)
    restored = load_numpy(data)
    np.testing.assert_array_equal(restored["a"], state["a"])
    assert restored["b"] == 1.5


def test_ckpt_invalid_args():
    with pytest.raises(ValueError):
        CheckpointManager("x", every_n_steps=0)
    with pytest.raises(ValueError):
        CheckpointManager("x", keep_last=0)
