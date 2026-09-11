# -*- coding: utf-8 -*-
"""Asterdex 钱包 → broker-create-api-key 派生 apiKey/apiSecret（临时脚本，跑完即删）。

流程（官方 demo/aster-api-key-registration.md）：
  1) get-nonce
  2) personal_sign "You are signing into Astherus {nonce}"
  3) ae/login → token
  4) broker-create-api-key → apiKey/apiSecret
"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

import requests
from eth_account import Account
from eth_account.messages import encode_defunct

ADDR = "0x4d99f814a609A85d6a02F132B0B85694003a530e"
PRIV = "0xfbe6d3a5ed566efdbf78b691e047065d78580da349baf9e947542818059e04d0"
BASE = "https://www.asterdex.com"
CHAIN_ID = 56  # BSC（官方 demo 默认）


def post(path: str, payload: dict, headers: dict = None) -> dict:
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    proxies = [None, {"http": "http://127.0.0.1:1080", "https": "http://127.0.0.1:1080"}]
    last = None
    for p in proxies:
        try:
            r = requests.post(BASE + path, json=payload, headers=h, timeout=20, proxies=p)
            return r.json()
        except Exception as e:
            last = e
    raise last


# 1) nonce（登录用）
r1 = post("/bapi/futures/v1/public/future/web3/get-nonce",
          {"sourceAddr": ADDR, "type": "LOGIN"})
print("1 nonce(LOGIN) resp:", {k: r1.get(k) for k in ("code", "success")}, "nonce=", (r1.get("data") or {}).get("nonce"))
nonce = (r1.get("data") or {}).get("nonce")
if not nonce:
    print("ABORT: no nonce", r1)
    sys.exit(1)

# 2) sign personal message
msg = f"You are signing into Astherus {nonce}"
signable = encode_defunct(text=msg)
sig = Account.sign_message(signable, private_key=PRIV).signature.hex()
print("2 signed:", "0x" + sig[:16] + "...")

# 3) login
r3 = post("/bapi/futures/v1/public/future/web3/ae/login",
          {"signature": "0x" + sig, "sourceAddr": ADDR, "chainId": CHAIN_ID},
          headers={"clientType": "broker"})
print("3 login:", {k: r3.get(k) for k in ("code", "success", "message")})
token = (r3.get("data") or {}).get("token")
if not token:
    print("ABORT: login failed", str(r3)[:300])
    sys.exit(1)
print("   uid:", (r3.get("data") or {}).get("uid"), "token:", token[:8] + "...")

# 3.5) 创建 key 用独立 nonce + 签名
r_n2 = post("/bapi/futures/v1/public/future/web3/get-nonce",
            {"sourceAddr": ADDR, "type": "CREATE_API_KEY"})
nonce2 = (r_n2.get("data") or {}).get("nonce")
if not nonce2:
    print("ABORT: no nonce2", r_n2)
    sys.exit(1)
msg2 = f"You are signing into Astherus {nonce2}"
sig2 = Account.sign_message(encode_defunct(text=msg2), private_key=PRIV).signature.hex()

# 4) create api key
r4 = post("/bapi/futures/v1/public/future/web3/broker-create-api-key",
          {"desc": "alpha-arena-live",
           "ip": "",
           "network": str(CHAIN_ID),
           "signature": "0x" + sig2,
           "sourceAddr": ADDR,
           "type": "CREATE_API_KEY",
           "sourceCode": "broker"})
print("4 create:", {k: r4.get(k) for k in ("code", "success", "message")})
d4 = r4.get("data") or {}
api_key, api_secret = d4.get("apiKey"), d4.get("apiSecret")
if not api_key or not api_secret:
    print("ABORT: create failed", str(r4)[:300])
    sys.exit(1)
print("   apiKey:", api_key[:8] + "..." + api_key[-4:])
print("   apiSecret:", api_secret[:4] + "..." + api_secret[-4:])

# 5) 加密入库（绑定账户 188，与 binance 凭证同 owner）
from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal
from backend.database.models import ExchangeCredential
from backend.utils.encryption import encrypt_private_key

with system_identity(), SessionLocal() as db:
    existing = db.query(ExchangeCredential).filter(
        ExchangeCredential.exchange == "asterdex",
        ExchangeCredential.user_id == 326,
    ).first()
    if existing:
        existing.api_key_encrypted = encrypt_private_key(api_key)
        existing.api_secret_encrypted = encrypt_private_key(api_secret)
        existing.account_id = 188
        existing.testnet = False
        existing.enabled = True
        existing.label = "Asterdex 实盘(钱包派生)"
        db.commit()
        print("5 已更新现有 asterdex 凭证 id=", existing.id)
    else:
        cred = ExchangeCredential(
            account_id=188,
            user_id=326,
            exchange="asterdex",
            label="Asterdex 实盘(钱包派生)",
            api_key_encrypted=encrypt_private_key(api_key),
            api_secret_encrypted=encrypt_private_key(api_secret),
            passphrase_encrypted="",
            testnet=False,
            enabled=True,
            tenant_id=326,
        )
        db.add(cred)
        db.commit()
        print("5 已插入 asterdex 凭证 id=", cred.id)
print("DONE")
