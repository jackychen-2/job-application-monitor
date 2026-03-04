"""Encrypt/decrypt OAuth tokens for storage."""

from __future__ import annotations

from cryptography.fernet import Fernet


def _build_fernet(key: str) -> Fernet:
    if not key:
        raise ValueError("TOKEN_ENCRYPTION_KEY is required")
    return Fernet(key.encode("utf-8"))


def encrypt_token(raw_token: str, key: str) -> str:
    fernet = _build_fernet(key)
    return fernet.encrypt(raw_token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_token: str, key: str) -> str:
    fernet = _build_fernet(key)
    return fernet.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
