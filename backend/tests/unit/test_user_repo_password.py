# backend/tests/unit/test_user_repo_password.py
"""bcrypt password hashing via user_repo (replaces the old SHA256 dead code)."""
from backend.repositories.user_repo import _hash_password, _verify_password


def test_bcrypt_hash_and_verify():
    h = _hash_password("secret123")
    assert h != "secret123"
    assert h != "5fa72f3c8c7b8e9c3f8b8c7b8e9c3f8b8c7b8e9c3f8b8c7b8e9c3f8b8c7b8e9c"  # not a hex sha256
    assert _verify_password("secret123", h) is True


def test_bcrypt_verify_wrong_password():
    h = _hash_password("secret123")
    assert _verify_password("wrong", h) is False


def test_verify_empty_hash_returns_false():
    assert _verify_password("x", "") is False


def test_verify_none_hash_returns_false():
    assert _verify_password("x", None) is False


def test_bcrypt_hashes_are_unique():
    # bcrypt includes a random salt → two hashes of the same pw differ
    h1 = _hash_password("same")
    h2 = _hash_password("same")
    assert h1 != h2
    assert _verify_password("same", h1) is True
    assert _verify_password("same", h2) is True


def test_hash_is_bcrypt_format():
    # bcrypt hashes start with $2 (bcrypt identifier)
    h = _hash_password("x")
    assert h.startswith("$2")
