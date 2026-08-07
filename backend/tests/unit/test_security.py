# backend/tests/unit/test_security.py
"""JWT security module tests — token roundtrip, jti, invalid token."""
import pytest

from backend.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    JWTError,
)


def test_access_token_roundtrip():
    t = create_access_token(sub="42", tenant_id=42, tier="free")
    payload = decode_token(t)
    assert payload["sub"] == "42"
    assert payload["tenant_id"] == 42
    assert payload["tier"] == "free"
    assert payload["type"] == "access"
    # role defaults to "user"
    assert payload["role"] == "user"


def test_access_token_custom_role():
    t = create_access_token(sub="1", tenant_id=0, tier="vip", role="admin")
    payload = decode_token(t)
    assert payload["role"] == "admin"
    assert payload["tier"] == "vip"


def test_refresh_token_has_jti():
    t, jti = create_refresh_token(sub="42")
    assert isinstance(jti, str) and len(jti) > 0
    payload = decode_token(t)
    assert payload["jti"] == jti
    assert payload["type"] == "refresh"
    assert payload["sub"] == "42"


def test_refresh_tokens_have_distinct_jti():
    _, jti1 = create_refresh_token(sub="1")
    _, jti2 = create_refresh_token(sub="1")
    assert jti1 != jti2


def test_decode_invalid_token_raises():
    with pytest.raises(JWTError):
        decode_token("not.a.valid.token")


def test_decode_tampered_token_raises():
    t = create_access_token(sub="1", tenant_id=1, tier="free")
    # flip a char in the payload section to break the signature
    tampered = t[:-4] + ("aaaa" if t[-4:] != "aaaa" else "bbbb")
    with pytest.raises(JWTError):
        decode_token(tampered)
