# -*- coding: utf-8 -*-
"""[h666] Asterdex V3 客户端纯函数测试(签名/格式/nonce,不触网)。"""
from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.exchange.asterdex_v3_client import (  # noqa: E402
    AsterdexV3Client, _TYPED_DATA_TEMPLATE, _enc,
)
from eth_account import Account  # noqa: E402

USER = "0x63DD5aCC6b1aa0f563956C0e534DD30B6dcF7C4e"
SIGNER = "0x21cF8Ae13Bb72632562c6Fff438652Ba1a151bb0"
PK = "0x4fd0a42218f3eae43a6ce26d22544e986139a01e5b34a62db53757ffca81bae1"


def _mk() -> AsterdexV3Client:
    return AsterdexV3Client(user=USER, signer=SIGNER, private_key=PK)


def test_sign_recovers_signer():
    """EIP-712 签名必须能恢复出 signer 地址(=官方示例口径)。"""
    c = _mk()
    params = {"symbol": "ASTERUSDT", "type": "LIMIT", "side": "BUY",
              "timeInForce": "GTC", "quantity": "20", "price": "0.5",
              "nonce": str(c._nonce()), "signer": c.signer}
    sig = c._sign(params)
    assert len(sig) == 130
    td = dict(_TYPED_DATA_TEMPLATE)
    td["message"] = {"msg": urllib.parse.urlencode(params)}
    rec = Account.recover_message(_enc(full_message=td),
                                  signature=bytes.fromhex(sig))
    assert rec.lower() == SIGNER.lower()


def test_nonce_monotonic_microseconds():
    c = _mk()
    n1, n2, n3 = c._nonce(), c._nonce(), c._nonce()
    assert n2 > n1 and n3 > n2           # 重放保护:绝不重复
    assert n1 > 1_700_000_000_000_000    # 微秒精度时间戳


def test_fmt_guards_tiny_qty():
    assert _mk()._fmt(20.0) == "20"
    assert _mk()._fmt(1e-9) == "0"       # 不能出空串
    assert _mk()._fmt(0.001) == "0.001"


def test_requires_all_three_keys():
    with pytest.raises(ValueError):
        AsterdexV3Client(user="", signer=SIGNER, private_key=PK)
    with pytest.raises(ValueError):
        AsterdexV3Client(user=USER, signer="", private_key=PK)
